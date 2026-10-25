#!/usr/bin/env bash
# ============================================================================
# build-offline-bundle.sh -- builds the SATOM Builder OFFLINE installer for
# Debian 12 (amd64).
#
# Run it on a Debian 12 amd64 machine or container WITH internet access (same
# release and architecture as the target), as root, e.g.:
#
#   docker run --rm -v "$PWD":/src:ro -v "$PWD/dist":/out -e BUNDLE_OUT=/out \
#       debian:12 bash /src/installers/build-offline-bundle.sh
#
# It produces:
#     dist/satom-builder-offline-<version>-debian12-amd64.tar.gz (+ .sha256)
#       satom-builder-installer/
#         install-satom-builder.sh    the same installer; it detects bundle/
#         satom-builder-setup.sh      guided installer (uses the sibling)
#         INSTALL.md                  manual for the systems team
#         bundle/
#           BUNDLE_OS                 debian12-amd64 (the installer refuses any other OS)
#           debs/                     FULL .deb dependency closure
#           debs-closure.txt          the closure as resolved
#           wheels/                   every Python package (pip download)
#           app.tar.gz                application code (git archive, or APP_TARBALL)
#
# Usage:  bash installers/build-offline-bundle.sh [git-ref]   (default: HEAD)
#         APP_TARBALL=/path/app.tar.gz bash installers/build-offline-bundle.sh
# ============================================================================
set -euo pipefail

REF="${1:-HEAD}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d ' \t\r\n' < "$REPO_DIR/VERSION" 2>/dev/null || echo 0.0.0)"
OUT="${BUNDLE_OUT:-$REPO_DIR/dist}"
STAGE="$OUT/satom-builder-installer"
OS_TAG="debian12-amd64"
# Must contain the installer's REQUIRED_PKGS for apt (tests/test_bundles_offline.py).
# sudo: the privilege model writes /etc/sudoers.d and validates it with visudo.
# openssh-client: ssh-keygen verifies release-trust signatures.
PKGS=(python3 python3-venv python3-pip nginx openssl curl ca-certificates
      sudo git openssh-client)
# Alternative providers the recursive resolver drags in through `a | b`
# dependencies. The target already runs one provider (the image's systemd);
# shipping the other and forcing it breaks the base system. The installer
# applies the matching rule on its side ([BUNDLE-DEBS]).
BUNDLE_EXCLUDE=(libelogind0 elogind libpam-elogind)

echo "==> SATOM Builder offline bundle v${VERSION} (${OS_TAG}, ref ${REF})"
command -v apt-get >/dev/null || { echo "Requires Debian/apt"; exit 1; }
[ "$(id -u)" -eq 0 ] || { echo "Run as root (apt needs it)"; exit 1; }
# In a SUBSHELL: os-release defines VERSION, which would overwrite ours.
# shellcheck disable=SC1091
( . /etc/os-release; [ "${ID:-}" = debian ] && [ "${VERSION_ID%%.*}" = 12 ] ) \
    || { echo "Run it ON Debian 12 (this is $(sed -n 's/^PRETTY_NAME=//p' /etc/os-release))"; exit 1; }
[ "$(dpkg --print-architecture)" = amd64 ] || { echo "Run it on amd64"; exit 1; }
[ -f "$REPO_DIR/requirements.txt" ] || [ -n "${APP_TARBALL:-}" ] \
    || { echo "requirements.txt is missing from ${REPO_DIR}"; exit 1; }

echo "==> 0/4 Build tools"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
TOOLS=()
for t in python3-venv git ca-certificates; do dpkg -s "$t" >/dev/null 2>&1 || TOOLS+=("$t"); done
[ ${#TOOLS[@]} -eq 0 ] || apt-get install -y -qq --no-install-recommends "${TOOLS[@]}" >/dev/null

rm -rf "$STAGE"; mkdir -p "$STAGE/bundle/debs" "$STAGE/bundle/wheels" "$OUT"
echo "$OS_TAG" > "$STAGE/bundle/BUNDLE_OS"

echo "==> 1/4 Downloading .debs (full dependency closure)"
DEPS=$(apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts \
        --no-breaks --no-replaces --no-enhances "${PKGS[@]}" \
        | grep '^[a-z0-9]' | sort -u \
        | grep -vxF -f <(printf '%s\n' "${BUNDLE_EXCLUDE[@]}"))
# shellcheck disable=SC2086
(cd "$STAGE/bundle/debs" && apt-get download $DEPS >/dev/null 2>&1 || true)
printf '%s\n' $DEPS > "$STAGE/bundle/debs-closure.txt"
N=$(find "$STAGE/bundle/debs" -name '*.deb' | wc -l)
echo "    $N .deb packages"
[ "$N" -gt 50 ] || { echo "ERROR: too few debs -- something went wrong"; exit 1; }
for p in "${PKGS[@]}"; do
    ls "$STAGE/bundle/debs/${p}_"*.deb >/dev/null 2>&1 || { echo "ERROR: ${p} is not in the bundle"; exit 1; }
done

echo "==> 2/4 Code"
if [ -n "${APP_TARBALL:-}" ]; then
    cp "$APP_TARBALL" "$STAGE/bundle/app.tar.gz"
    echo "    code from APP_TARBALL=${APP_TARBALL}"
else
    git -c safe.directory="$REPO_DIR" -C "$REPO_DIR" archive --format=tar.gz -o "$STAGE/bundle/app.tar.gz" "$REF"
    echo "    code from git archive ${REF}"
fi
REQS="$(mktemp)"
tar -xzOf "$STAGE/bundle/app.tar.gz" requirements.txt > "$REQS" 2>/dev/null \
    || tar -xzOf "$STAGE/bundle/app.tar.gz" ./requirements.txt > "$REQS" \
    || { echo "ERROR: app.tar.gz has no requirements.txt"; exit 1; }

echo "==> 3/4 Downloading Python wheels (the code's requirements.txt)"
python3 -m venv /tmp/satom-builder-bundle-venv
/tmp/satom-builder-bundle-venv/bin/python -m pip -q install --upgrade pip
/tmp/satom-builder-bundle-venv/bin/python -m pip download -q -r "$REQS" -d "$STAGE/bundle/wheels"
/tmp/satom-builder-bundle-venv/bin/python -m pip download -q pip setuptools wheel -d "$STAGE/bundle/wheels"
rm -rf /tmp/satom-builder-bundle-venv "$REQS"
echo "    $(find "$STAGE/bundle/wheels" -type f | wc -l) wheels/sdists"

echo "==> 4/4 Installer + manual + final tarball"
cp "$REPO_DIR/installers/install-satom-builder.sh" "$STAGE/"
cp "$REPO_DIR/installers/satom-builder-setup.sh" "$STAGE/"
cp "$REPO_DIR/docs/INSTALL.md" "$STAGE/" 2>/dev/null || true
chmod 0755 "$STAGE/install-satom-builder.sh" "$STAGE/satom-builder-setup.sh"

TARBALL="$OUT/satom-builder-offline-${VERSION}-${OS_TAG}.tar.gz"
tar -C "$OUT" -czf "$TARBALL" satom-builder-installer
# The .sha256 holds ONLY the basename, so "sha256sum -c file.sha256" works in
# the download directory.
( cd "$(dirname "$TARBALL")" && sha256sum "$(basename "$TARBALL")" > "$(basename "$TARBALL").sha256" )
rm -rf "$STAGE"
echo "==> DONE: $TARBALL ($(du -h "$TARBALL" | cut -f1))"
echo "    $(cat "$TARBALL.sha256")"
echo "On the target:  tar xzf $(basename "$TARBALL") && cd satom-builder-installer && sudo bash satom-builder-setup.sh"
