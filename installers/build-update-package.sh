#!/usr/bin/env bash
# Build an OFFLINE UPDATE PACKAGE for SATOM Builder -- application code + the
# pinned wheels a supported node may be missing.
#
# Built the same way as SATOM's installers/build-update-package.sh (see
# deploy/VENDORED.md); schema satom-builder.update-package/1, product
# satom-builder, signed with the BUILDER's own release key.
#
#     satom-builder-update-<version>.tar.gz
#       satom-builder-update-<version>/
#         manifest.json   signed document (schema, product, version, commit,
#                         python_tags, min_from_version, requirements,
#                         files{sha256,size}, wheelset, wheels_mode, ...)
#         app.tar.gz      `git archive HEAD` (or APP_TARBALL + COMMIT)
#         wheels/*.whl    cp311 manylinux_2_28 / manylinux2014 x86_64
#
# Without WHEEL_BASELINE the package carries every pinned wheel (full). With it
# (JSON {version: [name==ver]} from published packages' `wheelset`), it carries
# only the wheels not every baseline release already has, and min_from_version
# rises to the oldest baseline release (select_wheels in deploy/update_package.py).
#
# RELEASE GATE (opt-in): with RELEASE_TRUST_SIGNERS=<ssh allowed-signers file>
# (deploy/release-trust/satom-builder-release.allowed_signers), the build
# REFUSES a HEAD whose deploy/ differs from the newest commit signed by a
# release key -- `deploy/` is what root runs on every node, so a change to it
# must be attested with `satom-builder sign-release` first.
#
# The build host holds NO secret. Signing is a separate step, run where the
# private key lives:
#
#     bash installers/build-update-package.sh
#     python3 deploy/sign_update_package.py sign dist/satom-builder-update-<v>.tar.gz \
#         --key <release.key> --passphrase-file <file>
#
# An unsigned package is refused by every node, so shipping one fails closed.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$(cd "$HERE/.." && pwd)"
OUT="${OUT_DIR:-$APP/dist}"
PY="${PYTHON:-$(command -v python3 || true)}"
MIN_FROM="${MIN_FROM_VERSION:-0.1.0}"
APP_TARBALL="${APP_TARBALL:-}"
# Wheels for every supported node, not for the build host. The offline-capable
# families run CPython 3.11; manylinux_2_28 is the newest glibc baseline all of
# them satisfy (RHEL 9 glibc 2.34, Debian 12 2.36, Leap 15.6 2.38). Online-only
# nodes on 3.12/3.13 need WHEEL_PYTHON_VERSIONS="3.11 3.12 3.13".
WHEEL_PYTHON_VERSIONS="${WHEEL_PYTHON_VERSIONS:-${WHEEL_PYTHON_VERSION:-3.11}}"
WHEEL_PLATFORMS="${WHEEL_PLATFORMS:-manylinux_2_28_x86_64 manylinux2014_x86_64}"
RELEASE_TRUST_SIGNERS="${RELEASE_TRUST_SIGNERS:-}"

[ -n "$PY" ] || { echo "no python3 found" >&2; exit 1; }

if [ -n "$APP_TARBALL" ]; then
  COMMIT="${COMMIT:-}"
  [ -f "$APP_TARBALL" ] || { echo "APP_TARBALL $APP_TARBALL does not exist" >&2; exit 1; }
  [[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] \
    || { echo "APP_TARBALL needs COMMIT=<full sha> of the revision it was archived from" >&2; exit 1; }
  VERSION="$(tar -xzOf "$APP_TARBALL" VERSION 2>/dev/null || tar -xzOf "$APP_TARBALL" ./VERSION)"
else
  COMMIT="$(git -C "$APP" rev-parse HEAD 2>/dev/null || echo "")"
  VERSION="$(cat "$APP/VERSION")"
fi
VERSION="$(printf '%s' "$VERSION" | tr -d ' \t\n\r')"
[ -n "$VERSION" ] || { echo "VERSION is empty" >&2; exit 1; }
BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

NAME="satom-builder-update-$VERSION"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/satom-builder-updpkg-XXXXXX")"
PKG="$STAGE/$NAME"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$PKG/wheels" "$OUT"

echo "==> SATOM Builder update package $VERSION (${COMMIT:0:12})"

# ------------------------------------------------------------ release gate
if [ -n "$RELEASE_TRUST_SIGNERS" ]; then
  [ -n "$APP_TARBALL" ] && { echo "!!! RELEASE_TRUST_SIGNERS checks git history: not with APP_TARBALL" >&2; exit 1; }
  [ -f "$RELEASE_TRUST_SIGNERS" ] || { echo "!!! RELEASE_TRUST_SIGNERS $RELEASE_TRUST_SIGNERS does not exist" >&2; exit 1; }
  echo "--> release gate: deploy/ at ${COMMIT:0:12} must match a signed release"
  APP_DIR="$APP" HEAD_SHA="$COMMIT" SIGNERS="$RELEASE_TRUST_SIGNERS" "$PY" -I - <<'PYGATE'
import os, shutil, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(os.environ["APP_DIR"]) / "deploy" / "satom_builder_cli"))
import release_trust as rt
mirror_parent = Path(tempfile.mkdtemp(prefix="sb-gate-"))
os.chmod(mirror_parent, 0o700)
try:
    res = rt.resolve(os.environ["APP_DIR"], os.environ["HEAD_SHA"], os.environ["SIGNERS"],
                     mirror=mirror_parent / "mirror.git")
finally:
    shutil.rmtree(mirror_parent, ignore_errors=True)
print("    %s" % res.get("detail", res["mode"]))
sys.exit(0 if res["mode"] in ("signed", "pinned") else 1)
PYGATE
fi

# ---------------------------------------------------------------- app tree
# git archive, not a copy of the working tree: the package must contain the
# committed revision, never whatever happens to lie around on the build host.
if [ -n "$APP_TARBALL" ]; then
  echo "--> app.tar.gz from $APP_TARBALL"
  cp "$APP_TARBALL" "$PKG/app.tar.gz"
elif [ -n "$COMMIT" ]; then
  echo "--> app.tar.gz from git archive HEAD"
  git -C "$APP" archive --format=tar HEAD | gzip -9 > "$PKG/app.tar.gz"
else
  echo "!!! not a git checkout -- refusing to build from an untracked tree" >&2
  exit 1
fi
REQS="$STAGE/requirements.txt"
tar -xzOf "$PKG/app.tar.gz" requirements.txt > "$REQS" 2>/dev/null \
  || tar -xzOf "$PKG/app.tar.gz" ./requirements.txt > "$REQS" \
  || { echo "!!! app.tar.gz has no requirements.txt" >&2; exit 1; }
for must in deploy/self_update_runner.py deploy/update_package.py deploy/install-runner.sh \
            deploy/install-cli.sh deploy/satom-builder.service; do
  tar -tzf "$PKG/app.tar.gz" | grep -qxE "(\./)?${must}" \
    || { echo "!!! app.tar.gz lacks ${must}: a node could not refresh its root-side files" >&2; exit 1; }
done

# ------------------------------------------------------------------- wheels
# The FULL resolved set goes to a side directory first: it is recorded in the
# manifest (`wheelset`) whatever the package ships, because the next release
# computes its delta against it.
ALL_WHEELS="$STAGE/all-wheels"
mkdir -p "$ALL_WHEELS"
PLAT_ARGS=()
for p in $WHEEL_PLATFORMS; do PLAT_ARGS+=(--platform "$p"); done
for pyv in $WHEEL_PYTHON_VERSIONS; do
  echo "--> downloading wheels for requirements.txt (cp${pyv/./}, ${WHEEL_PLATFORMS// /, })"
  "$PY" -m pip download --quiet --only-binary=:all: \
        --python-version "$pyv" --implementation cp "${PLAT_ARGS[@]}" \
        --dest "$ALL_WHEELS" -r "$REQS" \
    || { echo "!!! pip download failed -- the package would apply with no deps" >&2; exit 1; }
done
WHEELS=$(find "$ALL_WHEELS" -name '*.whl' | wc -l)
[ "$WHEELS" -gt 0 ] || { echo "!!! no wheels downloaded" >&2; exit 1; }
echo "    $WHEELS wheel(s) resolved"

WHEEL_BASELINE="${WHEEL_BASELINE:-}"
if [ -n "$WHEEL_BASELINE" ] && [ ! -f "$WHEEL_BASELINE" ]; then
  echo "!!! WHEEL_BASELINE $WHEEL_BASELINE does not exist" >&2; exit 1
fi

# --------------------------------------------------------------- manifest
echo "--> wheel selection + manifest.json"
MIN_FROM="$MIN_FROM" PKG_DIR="$PKG" APP_DIR="$APP" WHEEL_PYS="$WHEEL_PYTHON_VERSIONS" \
VERSION="$VERSION" COMMIT="$COMMIT" BUILT_AT="$BUILT_AT" REQS="$REQS" \
ALL_WHEELS="$ALL_WHEELS" WHEEL_BASELINE="$WHEEL_BASELINE" \
"$PY" -I - <<'PYEOF'
import json, os, re, shutil, sys
from pathlib import Path

app = Path(os.environ["APP_DIR"])
pkg = Path(os.environ["PKG_DIR"])
sys.path.insert(0, str(app / "deploy"))
import update_package as up

all_dir = Path(os.environ["ALL_WHEELS"])
all_names = sorted(w.name for w in all_dir.glob("*.whl"))
baseline = {}
if os.environ.get("WHEEL_BASELINE"):
    baseline = json.loads(Path(os.environ["WHEEL_BASELINE"]).read_text())
    if not isinstance(baseline, dict):
        sys.exit("!!! WHEEL_BASELINE must be a JSON object {version: [name==ver]}")
sel = up.select_wheels(all_names, baseline, os.environ.get("MIN_FROM", ""),
                       os.environ["VERSION"])
for n in sel["keep"]:
    shutil.copy2(all_dir / n, pkg / "wheels" / n)
min_from = sel["min_from_version"]
if sel["mode"] == "delta":
    print("    delta against %s: shipping %d of %d wheel(s); min_from_version %s"
          % (", ".join(sel["baseline"]), len(sel["keep"]), len(all_names), min_from))
else:
    print("    full: shipping all %d wheel(s) (no baseline with a recorded wheelset)"
          % len(all_names))

files = {}
for p in sorted(pkg.rglob("*")):
    if p.is_file():
        rel = str(p.relative_to(pkg))
        files[rel] = {"sha256": up.sha256_file(p), "size": p.stat().st_size}

reqs = []
for line in Path(os.environ["REQS"]).read_text().splitlines():
    line = line.split("#", 1)[0].strip()
    if line:
        reqs.append(line)

# All pure-python wheels apply on any CPython; one compiled wheel pins the
# package to the interpreters it was RESOLVED for (judged on the FULL set).
pure = all(re.search(r"-(?:py2\.)?py3-none-any\.whl$", n) for n in all_names)
tags = ["*"] if pure else ["cp" + v.replace(".", "") for v in os.environ["WHEEL_PYS"].split()]

manifest = up.build_manifest(
    version=os.environ["VERSION"], commit=os.environ["COMMIT"],
    built_at=os.environ["BUILT_AT"], python_tags=tags, requirements=reqs,
    files=files, min_from_version=min_from,
    notes=(("Offline update package: application code + the pinned wheels a node on "
            "%s or newer may be missing. " % min_from) if sel["mode"] == "delta" else
           "Offline update package: application code + pinned wheels. ")
          + "Apply from the Updates page or with 'satom-builder update apply'.",
    wheelset=[up.wheel_key(n) for n in all_names],
    wheels_mode=sel["mode"], wheels_baseline=sel["baseline"])
(pkg / "manifest.json").write_bytes(up.dump_manifest(manifest))
print("    version %s, %d file(s), python tags %s"
      % (manifest["version"], len(files), ",".join(tags)))
PYEOF

# ------------------------------------------------------------------ pack
TARBALL="$OUT/$NAME.tar.gz"
rm -f "$TARBALL" "$TARBALL.sha256"
tar -C "$STAGE" -czf "$TARBALL" "$NAME"
( cd "$OUT" && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256" )

echo
echo "==> $TARBALL ($(du -h "$TARBALL" | cut -f1))"
echo "    $(cut -d' ' -f1 < "$TARBALL.sha256")"
echo
echo "UNSIGNED. Every node refuses an unsigned package. Sign it where the key lives:"
echo "    python3 deploy/sign_update_package.py sign $TARBALL --key <release.key> --passphrase-file <file>"
echo "(signing rewrites the tarball and refreshes its .sha256)"
