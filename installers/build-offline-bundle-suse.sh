#!/usr/bin/env bash
# ============================================================================
# build-offline-bundle-suse.sh -- builds the SATOM Builder OFFLINE installer
# for the SUSE family (openSUSE Leap 15.6 / SLES 15, x86_64).
#
# Run it ON an openSUSE machine or container WITH internet access, as root, e.g.:
#
#   docker run --rm -v "$PWD":/src:ro -v "$PWD/dist":/out -e BUNDLE_OUT=/out \
#       opensuse/leap:15.6 bash /src/installers/build-offline-bundle-suse.sh
#
# It produces:
#     dist/satom-builder-offline-<version>-suse15-x86_64.tar.gz (+ .sha256)
#       satom-builder-installer/
#         install-satom-builder.sh, satom-builder-setup.sh, INSTALL.md
#         bundle/
#           BUNDLE_OS     suse15-x86_64
#           rpms-suse/    FULL .rpm dependency closure + repodata
#           wheels/       Python packages for python3.11
#           app.tar.gz    application code (git archive, or APP_TARBALL)
#
# WHY A DIRECTORY OF ITS OWN AND NOT 'rpms/'
# ------------------------------------------
# Both RPM bundles are .rpm and they are NOT interchangeable: package names
# differ (python311 vs python3.11), base library versions differ, and zypper and
# dnf read repositories differently. A separate directory turns "wrong bundle"
# into an explicit installer error.
#
# WHY IT DOWNLOADS AGAINST AN EMPTY ROOT
# --------------------------------------
# zypper only downloads what THIS machine is missing. With `--root <empty dir>`
# it believes nothing is installed and fetches the FULL closure -- the
# equivalent of `dnf download --resolve --alldeps`.
# ============================================================================
set -euo pipefail

REF="${1:-HEAD}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d ' \t\r\n' < "$REPO_DIR/VERSION" 2>/dev/null || echo 0.0.0)"
OUT="${BUNDLE_OUT:-$REPO_DIR/dist}"
STAGE="$OUT/satom-builder-installer"
FAKEROOT="${FAKEROOT:-/tmp/satom-builder-suse-root}"
OS_TAG="suse15-x86_64"
# Must contain the installer's REQUIRED_PKGS for zypper (tests/test_bundles_offline.py).
# [ABI-OFFLINE] The system libraries the interpreter is built against are
# listed EXPLICITLY, so the installer can repair an old base image offline.
PKGS=(python311 python311-pip nginx openssl curl ca-certificates sudo git openssh
      libexpat1 libopenssl3 libsqlite3-0 libz1)

echo "==> SATOM Builder offline bundle v${VERSION} -- SUSE family (${OS_TAG})"
command -v zypper >/dev/null || { echo "Requires the SUSE family/zypper (use opensuse/leap:15.6)"; exit 1; }
[ "$(id -u)" -eq 0 ] || { echo "Run as root (zypper needs it)"; exit 1; }

echo "==> 0/4 Build tools (createrepo_c, python311, git, tar)"
zypper --non-interactive --gpg-auto-import-keys refresh >/dev/null
zypper --non-interactive install -y createrepo_c python311 python311-pip git-core tar gzip \
       findutils curl >/dev/null

rm -rf "$STAGE" "$FAKEROOT"
mkdir -p "$STAGE/bundle/rpms-suse" "$STAGE/bundle/wheels" "$OUT" "$FAKEROOT"
echo "$OS_TAG" > "$STAGE/bundle/BUNDLE_OS"

echo "==> 1/4 Downloading .rpms (full closure against an empty root)"
# The repos are COPIED, not parsed (zypper lr output changes between versions).
mkdir -p "$FAKEROOT/etc/zypp/repos.d"
cp /etc/zypp/repos.d/*.repo "$FAKEROOT/etc/zypp/repos.d/"
# os-release MUST come along: the .repo files use $releasever, derived from the
# ROOT's os-release; without it every package is "not found".
cp /etc/os-release "$FAKEROOT/etc/os-release"
SUSE_RELEASEVER="${SUSE_RELEASEVER:-$(. /etc/os-release; echo "$VERSION_ID")}"
echo "    releasever=${SUSE_RELEASEVER}"
zypper --non-interactive --root "$FAKEROOT" --releasever "$SUSE_RELEASEVER" \
       --no-gpg-checks refresh >/dev/null 2>&1 || true
zypper --non-interactive --root "$FAKEROOT" --releasever "$SUSE_RELEASEVER" \
       --no-gpg-checks install --download-only --no-recommends \
       --auto-agree-with-licenses "${PKGS[@]}" >/dev/null
find "$FAKEROOT" -name '*.rpm' -exec cp -n {} "$STAGE/bundle/rpms-suse/" \;
N=$(find "$STAGE/bundle/rpms-suse" -name '*.rpm' | wc -l)
echo "    $N .rpm packages"
[ "$N" -gt 60 ] || { echo "ERROR: too few rpms ($N) -- the empty root did not resolve the closure"; exit 1; }
createrepo_c -q "$STAGE/bundle/rpms-suse"

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

echo "==> 3/4 Downloading Python 3.11 wheels"
python3.11 -m pip -q download -r "$REQS" -d "$STAGE/bundle/wheels"
python3.11 -m pip -q download pip setuptools wheel -d "$STAGE/bundle/wheels"
rm -f "$REQS"
echo "    $(find "$STAGE/bundle/wheels" -type f | wc -l) wheels/sdists"

echo "==> 4/4 Installer + manual + final tarball"
cp "$REPO_DIR/installers/install-satom-builder.sh" "$STAGE/"
cp "$REPO_DIR/installers/satom-builder-setup.sh" "$STAGE/"
cp "$REPO_DIR/docs/INSTALL.md" "$STAGE/" 2>/dev/null || true
chmod 0755 "$STAGE/install-satom-builder.sh" "$STAGE/satom-builder-setup.sh"

TARBALL="$OUT/satom-builder-offline-${VERSION}-${OS_TAG}.tar.gz"
tar -C "$OUT" -czf "$TARBALL" satom-builder-installer
( cd "$(dirname "$TARBALL")" && sha256sum "$(basename "$TARBALL")" > "$(basename "$TARBALL").sha256" )
rm -rf "$STAGE" "$FAKEROOT"
echo "==> DONE: $TARBALL"
echo "    $(cat "$TARBALL.sha256")"
