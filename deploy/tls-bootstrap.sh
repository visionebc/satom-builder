#!/usr/bin/env sh
#
# tls-bootstrap.sh -- every SATOM Builder install serves HTTPS from its first boot.
#
# Vendored from SATOM deploy/tls-bootstrap.sh (see deploy/VENDORED.md).
#
# The installer issues an internal CA, signs a node certificate whose SAN covers
# EVERY name the console is reached by (plus the IP), and puts nginx in front of
# gunicorn. The certificate is self-signed by a per-node CA, which a browser
# warns about -- the intended day-zero state: the node is usable, and the
# operator replaces the certificate afterwards (`import-cert` below). An
# imported certificate is marked "source":"imported" and is never reissued over.
#
# ONE AUTHOR FOR THE VHOST
# ------------------------
# The proxy settings below are not stylistic. `Host $http_host` (never $host)
# keeps the application's CSRF origin check working behind a non-standard port
# -- $host drops the port and every POST fails with a message about a stale
# session. `X-Forwarded-Proto https` tells the application it is reached over
# TLS even though this hop speaks plain HTTP to gunicorn.
# `client_max_body_size` must stay >= every upload the app accepts (20 MB
# assets) and >= the update-package ceiling (200 MB), or a valid upload dies
# with an nginx 413 the application never sees and therefore cannot explain.
# tests/test_tls_by_default.py holds this file to all of it.
#
# The PKI lives in /etc/satom-builder/pki, root-only, OUTSIDE the app tree: the
# Builder has no certificate manager, so the service account never needs the
# private key (SATOM keeps it in its app tree because its console manages it).
#
# USAGE
#   tls-bootstrap.sh ensure-pki    [--pki DIR] [--names "a b"] [--ip ADDR]
#   tls-bootstrap.sh write-vhost   --out FILE [--pki DIR] [--names ...] [--ip ADDR]
#                                  [--port N] [--upstream HOST:PORT]
#                                  [--acme-webroot DIR] [--default-server]
#   tls-bootstrap.sh import-cert   --cert F --key F [--chain F] [--pki DIR]
#
# Every subcommand is idempotent and safe to re-run on an installed node.
set -eu

CMD="${1:-}"; [ -n "$CMD" ] || { echo "tls-bootstrap: no subcommand" >&2; exit 2; }
shift

PKI="${SATOM_BUILDER_PKI:-/etc/satom-builder/pki}"
NAMES="${SATOM_BUILDER_SERVED_NAMES:-}"
NODE_IP="${SATOM_BUILDER_NODE_IP:-}"
PORT="${SATOM_BUILDER_WEB_PORT:-443}"
UPSTREAM="${SATOM_BUILDER_UPSTREAM:-127.0.0.1:8100}"
ACME_WEBROOT="${SATOM_BUILDER_ACME_WEBROOT:-/var/www/satom-builder-acme}"
OUT=""
DEFAULT_SERVER=""
IN_CERT=""; IN_KEY=""; IN_CHAIN=""

while [ $# -gt 0 ]; do
    case "$1" in
        --pki)           PKI="$2"; shift 2 ;;
        --names)         NAMES="$2"; shift 2 ;;
        --ip)            NODE_IP="$2"; shift 2 ;;
        --port)          PORT="$2"; shift 2 ;;
        --upstream)      UPSTREAM="$2"; shift 2 ;;
        --acme-webroot)  ACME_WEBROOT="$2"; shift 2 ;;
        --out)           OUT="$2"; shift 2 ;;
        # Claim the default server on both listeners. Without an explicit owner
        # nginx awards it to the FIRST server block for that port in parse
        # order (alphabetical between files). Opt-in: on a host that already
        # serves another site that claims it, nginx refuses the whole
        # configuration with "duplicate default server" -- the installer then
        # rewrites the vhost without it.
        --default-server) DEFAULT_SERVER=" default_server"; shift ;;
        --cert)          IN_CERT="$2"; shift 2 ;;
        --key)           IN_KEY="$2"; shift 2 ;;
        --chain)         IN_CHAIN="$2"; shift 2 ;;
        *) echo "tls-bootstrap: unknown option $1" >&2; exit 2 ;;
    esac
done

# The served names default to the node's own hostname: a FALLBACK, never the
# first source -- a SAN that does not cover the name the operator types gives a
# browser warning on a certificate that was just reported as issued.
[ -n "$NAMES" ] || NAMES="$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo satom-builder)"
CN="$(printf '%s' "$NAMES" | awk '{print $1}')"

# ---------------------------------------------------------------------------
# ensure-pki
# ---------------------------------------------------------------------------
# Creates $PKI/internal-ca (once, 10 years) and $PKI/node (825 days), then
# publishes the leaf as $PKI/public/server.{crt,key} with a meta.json.
#
# meta.json's `source` is load-bearing:
#   issued    -> this script owns the material and may reissue it
#   imported  -> an OPERATOR installed a real certificate here; reissuing over
#                it would silently replace a trusted certificate with a
#                self-signed one at the next installer re-run.
ensure_pki() {
    umask 077
    mkdir -p "$PKI/internal-ca" "$PKI/node" "$PKI/public"
    chmod 700 "$PKI" "$PKI/internal-ca" "$PKI/node"
    chmod 755 "$PKI/public"

    if [ -f "$PKI/public/meta.json" ] \
       && grep -q '"source"[[:space:]]*:[[:space:]]*"imported"' "$PKI/public/meta.json" \
       && [ -s "$PKI/public/server.crt" ] && [ -s "$PKI/public/server.key" ]; then
        echo "tls-bootstrap: operator certificate present (source=imported) -- left untouched"
        return 0
    fi

    if [ ! -f "$PKI/internal-ca/ca.key" ]; then
        openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
            -keyout "$PKI/internal-ca/ca.key" -out "$PKI/internal-ca/ca.crt" \
            -subj "/CN=SATOM Builder Internal CA/O=$CN" >/dev/null 2>&1
        echo "tls-bootstrap: internal CA created (10 years)"
    fi
    chmod 600 "$PKI/internal-ca/ca.key"

    # An existing self-issued leaf that still covers the requested names and is
    # not close to expiry is kept: reissuing on every re-run would invalidate a
    # fingerprint an operator may have pinned, for no gain.
    if [ -s "$PKI/public/server.crt" ] \
       && openssl x509 -in "$PKI/public/server.crt" -noout -checkend 2592000 >/dev/null 2>&1 \
       && _cert_covers "$PKI/public/server.crt"; then
        echo "tls-bootstrap: node certificate present and valid -- reused"
        return 0
    fi

    san="DNS:$CN"
    for n in $NAMES; do
        [ "$n" = "$CN" ] || san="$san,DNS:$n"
    done
    [ -z "$NODE_IP" ] || san="$san,IP:$NODE_IP"

    ext="$(mktemp)"
    printf 'subjectAltName=%s\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n' "$san" > "$ext"
    openssl req -newkey rsa:2048 -sha256 -nodes \
        -keyout "$PKI/node/leaf.key" -out "$PKI/node/leaf.csr" \
        -subj "/CN=$CN" >/dev/null 2>&1
    openssl x509 -req -in "$PKI/node/leaf.csr" \
        -CA "$PKI/internal-ca/ca.crt" -CAkey "$PKI/internal-ca/ca.key" -CAcreateserial \
        -days 825 -sha256 -extfile "$ext" -out "$PKI/node/leaf.crt" >/dev/null 2>&1
    rm -f "$ext" "$PKI/node/leaf.csr"
    chmod 600 "$PKI/node/leaf.key"

    cp "$PKI/node/leaf.crt" "$PKI/public/server.crt"
    cp "$PKI/node/leaf.key" "$PKI/public/server.key"
    chmod 644 "$PKI/public/server.crt"
    chmod 600 "$PKI/public/server.key"
    cat > "$PKI/public/meta.json" <<META
{"source": "issued", "issued_by": "internal-ca", "cn": "$CN", "san": "$san", "installed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
META
    chmod 644 "$PKI/public/meta.json"
    echo "tls-bootstrap: node certificate issued (CN=$CN, SAN=$san)"
}

# True when the certificate's SAN already covers every requested name (and the
# IP). Without this an installer re-run that ADDS a served name would keep
# reporting success while the new name stays uncovered.
_cert_covers() {
    _txt="$(openssl x509 -in "$1" -noout -text 2>/dev/null || true)"
    for n in $NAMES; do
        printf '%s' "$_txt" | grep -q "DNS:$n\b" || return 1
    done
    if [ -n "$NODE_IP" ]; then
        printf '%s' "$_txt" | grep -q "IP Address:$NODE_IP\b" || return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# write-vhost
# ---------------------------------------------------------------------------
# The :80 server is NOT a leftover. ACME http-01 is always validated over plain
# :80, so the challenge location has to survive the redirect -- which is why
# the redirect is scoped to `location /` and not a server-level `return`, which
# runs before location selection and would swallow the challenge.
#
# `listen ... ssl http2` (not `http2 on;`): the distro nginx on Debian 12,
# RHEL 9 and SUSE 15 predates the `http2` directive (1.25.1).
# The IPv6 listeners are written only when the kernel has IPv6: nginx refuses to
# start on a `[::]` listener it cannot open.
write_vhost() {
    [ -n "$OUT" ] || { echo "tls-bootstrap: write-vhost needs --out" >&2; exit 2; }
    mkdir -p "$(dirname "$OUT")" "$ACME_WEBROOT/.well-known/acme-challenge"
    chmod 755 "$ACME_WEBROOT"

    # An explicit :443 in a redirect leaks into the address bar; omitted when
    # it is the default port, exactly as a browser does.
    redir_port=""
    [ "$PORT" = "443" ] || redir_port=":$PORT"

    V6_TLS=""; V6_80=""
    if [ -s /proc/net/if_inet6 ]; then
        V6_TLS="    listen [::]:$PORT ssl http2$DEFAULT_SERVER;"
        V6_80="    listen [::]:80$DEFAULT_SERVER;"
    fi

    cat > "$OUT" <<NGX
# Generated by deploy/tls-bootstrap.sh (SATOM Builder) -- do not edit by hand.
# Replace the CERTIFICATE, not this file: see \`tls-bootstrap.sh import-cert\`.
server {
    listen $PORT ssl http2$DEFAULT_SERVER;
$V6_TLS
    server_name $NAMES${NODE_IP:+ $NODE_IP};

    ssl_certificate     $PKI/public/server.crt;
    ssl_certificate_key $PKI/public/server.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SATOM_BUILDER:10m;
    server_tokens off;

    # Must stay >= every upload the application accepts (20 MB assets) and the
    # update-package ceiling (200 MB), or a valid upload dies with an nginx 413.
    client_max_body_size 200M;
    add_header Strict-Transport-Security "max-age=31536000" always;

    location / {
        proxy_pass http://$UPSTREAM;
        proxy_hide_header Strict-Transport-Security;
        # \$host DISCARDS the port; \$http_host passes the header through.
        proxy_set_header Host              \$http_host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 300s;
    }
}

server {
    listen 80$DEFAULT_SERVER;
$V6_80
    server_name $NAMES${NODE_IP:+ $NODE_IP};
    location ^~ /.well-known/acme-challenge/ {
        root $ACME_WEBROOT;
        default_type "text/plain";
        try_files \$uri =404;
    }
    location / { return 301 https://\$host$redir_port\$request_uri; }
}
NGX
    echo "tls-bootstrap: vhost written to $OUT (names: $NAMES, upstream: $UPSTREAM)"
}

# ---------------------------------------------------------------------------
# import-cert
# ---------------------------------------------------------------------------
# The operator-facing half of the promise: the install ships a working
# certificate, the operator replaces it later. Marking meta.json source=imported
# is what stops a later installer re-run from overwriting it.
import_cert() {
    [ -n "$IN_CERT" ] && [ -n "$IN_KEY" ] || {
        echo "tls-bootstrap: import-cert needs --cert and --key" >&2; exit 2; }
    openssl x509 -in "$IN_CERT" -noout >/dev/null 2>&1 || {
        echo "tls-bootstrap: --cert is not a certificate" >&2; exit 1; }
    # Refuse a mismatched pair HERE: nginx accepts the reload and then fails on
    # the first handshake, which reads as a network fault.
    c="$(openssl x509 -in "$IN_CERT" -noout -pubkey 2>/dev/null | openssl sha256)"
    k="$(openssl pkey -in "$IN_KEY" -pubout 2>/dev/null | openssl sha256)"
    [ -n "$c" ] && [ "$c" = "$k" ] || {
        echo "tls-bootstrap: certificate and key do not match" >&2; exit 1; }

    umask 077
    mkdir -p "$PKI/public"
    chmod 700 "$PKI"
    chmod 755 "$PKI/public"
    if [ -n "$IN_CHAIN" ]; then
        cat "$IN_CERT" "$IN_CHAIN" > "$PKI/public/server.crt"
    else
        cp "$IN_CERT" "$PKI/public/server.crt"
    fi
    cp "$IN_KEY" "$PKI/public/server.key"
    chmod 644 "$PKI/public/server.crt"
    chmod 600 "$PKI/public/server.key"
    cn="$(openssl x509 -in "$PKI/public/server.crt" -noout -subject 2>/dev/null | sed 's/.*CN *= *//; s/,.*//')"
    cat > "$PKI/public/meta.json" <<META
{"source": "imported", "cn": "$cn", "installed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
META
    chmod 644 "$PKI/public/meta.json"
    echo "tls-bootstrap: certificate imported (CN=$cn) -- reissue will not overwrite it"
}

case "$CMD" in
    ensure-pki)  ensure_pki ;;
    write-vhost) write_vhost ;;
    import-cert) import_cert ;;
    *) echo "tls-bootstrap: unknown subcommand '$CMD'" >&2; exit 2 ;;
esac
