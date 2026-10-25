#!/usr/bin/env bash
# Install (or refresh) the root-owned copy of the SATOM Builder operator CLI.
#
# Vendored from SATOM deploy/install-cli.sh (see deploy/VENDORED.md).
#
# Idempotent. Run as root. Called from:
#   * installers/install-satom-builder.sh   (fresh install / re-run)
#   * deploy/self_update_runner.py          (after every package apply)
#   * satom-builder reinstall cli           (manual)
#
# WHY A COPY AND NOT A SYMLINK: the app tree is owned by the service account.
# A launcher that exec'd code from there would let a compromised web worker
# rewrite what an operator runs under sudo. The sudo target must be a fixed,
# real, root-owned path.
set -euo pipefail

APP_DIR="${SATOM_BUILDER_APP_DIR:-/opt/satom-builder}"
DEPLOY_SRC="${SATOM_BUILDER_DEPLOY_SRC:-${APP_DIR}/deploy}"
LIB_DIR="/usr/local/lib/satom-builder-cli"
BIN="/usr/local/sbin/satom-builder"
SRC="${DEPLOY_SRC}/satom_builder_cli"
LAUNCHER="${DEPLOY_SRC}/satom-builder-cli-launcher"

if [ "$(id -u)" -ne 0 ]; then
  echo "install-cli.sh: must run as root (it writes ${BIN})." >&2
  exit 3
fi
[ -d "$SRC" ] || { echo "install-cli.sh: missing ${SRC}" >&2; exit 1; }
[ -f "$LAUNCHER" ] || { echo "install-cli.sh: missing ${LAUNCHER}" >&2; exit 1; }

# Build beside, then swap: a half-copied package would make the recovery tool
# itself unimportable.
STAGE="${LIB_DIR}.new.$$"
rm -rf "$STAGE"
mkdir -p "${STAGE}/satom_builder_cli"
cp -a "${SRC}/." "${STAGE}/satom_builder_cli/"
rm -rf "${STAGE}/satom_builder_cli/__pycache__"
chown -R root:root "$STAGE"
find "$STAGE" -type d -exec chmod 0755 {} +
find "$STAGE" -type f -exec chmod 0644 {} +

rm -rf "${LIB_DIR}.old"
[ -d "$LIB_DIR" ] && mv "$LIB_DIR" "${LIB_DIR}.old"
mv "$STAGE" "$LIB_DIR"
rm -rf "${LIB_DIR}.old"

# Launcher interpreter: a SYSTEM python >= 3.10, never the venv (the CLI must
# diagnose a node whose venv is broken) and never one inside the app tree.
# '#!/usr/bin/env python3' is not portable: openSUSE has no python3 link for
# python311 and RHEL 9's python3 is 3.9.
pick_cli_python() {
  local c v
  for c in /usr/bin/python3 /usr/bin/python3.13 /usr/bin/python3.12 \
           /usr/bin/python3.11 /usr/bin/python3.10 \
           /usr/local/bin/python3.13 /usr/local/bin/python3.12 \
           /usr/local/bin/python3.11 /usr/local/bin/python3.10; do
    [ -x "$c" ] || continue
    case "$c" in "${APP_DIR}"/*) continue ;; esac
    v="$("$c" -c 'import sys;print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" || continue
    case "$v" in
      3.1[0-9]|3.[2-9][0-9]) echo "$c"; return 0 ;;
    esac
  done
  return 1
}

CLI_PY="$(pick_cli_python)" || {
  echo "install-cli.sh: no system Python >= 3.10 available for the launcher." >&2
  echo "  The operator CLI is NOT installed. Install python3.11 (or newer)" >&2
  echo "  and retry with: bash ${APP_DIR}/deploy/install-cli.sh" >&2
  exit 1
}

head -n1 "$LAUNCHER" | grep -q '^#!' || {
  echo "install-cli.sh: ${LAUNCHER} does not start with a shebang -- not stamping it blindly" >&2
  exit 1
}

# Stamp ONLY the first line. The rest of the launcher travels untouched.
STAGEBIN="$(mktemp)"
{ echo "#!${CLI_PY} -I"; tail -n +2 "$LAUNCHER"; } > "$STAGEBIN"
install -o root -g root -m 0755 "$STAGEBIN" "$BIN"
rm -f "$STAGEBIN"

fail=0
for p in "$BIN" "$LIB_DIR"; do
  owner="$(stat -c %U "$p")"
  mode="$(stat -c %a "$p")"
  case "$owner:$mode" in
    root:755|root:0755) ;;
    *) echo "install-cli.sh: ${p} is ${owner} mode ${mode} -- expected root 755" >&2; fail=1 ;;
  esac
done
if [ -L "$BIN" ]; then
  echo "install-cli.sh: ${BIN} is a symlink -- the sudo target must be a real path" >&2
  fail=1
fi
[ "$fail" -eq 0 ] || exit 1

"$BIN" version >/dev/null 2>&1 || {
  echo "install-cli.sh: installed, but '${BIN} version' failed. Run it by hand." >&2
  exit 1
}

# 'sudo satom-builder' on openSUSE and RHEL: sudo's secure_path there has no
# /usr/local/sbin. A link in /usr/bin TO the root-owned binary, in a root-owned
# directory, adds no path the service account can write. Never replace a
# /usr/bin/satom-builder that is not ours.
ALIAS="/usr/bin/satom-builder"
if [ -L "$ALIAS" ] || [ ! -e "$ALIAS" ]; then
  ln -sfn "$BIN" "$ALIAS"
elif [ "$(readlink -f "$ALIAS")" != "$BIN" ]; then
  echo "install-cli.sh: ${ALIAS} exists and is not ours -- left alone; use ${BIN} with sudo." >&2
fi

echo "satom-builder CLI installed:"
echo "  binary : ${BIN} (root:root 0755, python ${CLI_PY})"
echo "  alias  : ${ALIAS} -> ${BIN}"
echo "  library: ${LIB_DIR} (root:root)"
