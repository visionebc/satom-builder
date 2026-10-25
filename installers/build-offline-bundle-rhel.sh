#!/usr/bin/env bash
# ============================================================================
# build-offline-bundle-rhel.sh -- builds the SATOM Builder OFFLINE installer
# for the RHEL family (RHEL / Rocky / AlmaLinux 9, x86_64).
#
# Run it ON a RHEL-9 machine or container WITH internet access, as root, e.g.:
#
#   docker run --rm -v "$PWD":/src:ro -v "$PWD/dist":/out -e BUNDLE_OUT=/out \
#       rockylinux:9 bash /src/installers/build-offline-bundle-rhel.sh
#
# It produces:
#     dist/satom-builder-offline-<version>-rhel9-x86_64.tar.gz (+ .sha256)
#       satom-builder-installer/
#         install-satom-builder.sh, satom-builder-setup.sh, INSTALL.md
#         bundle/
#           BUNDLE_OS     rhel9-x86_64
#           rpms/         FULL .rpm dependency closure + repodata
#           wheels/       Python packages for python3.11 (pip download)
#           app.tar.gz    application code (git archive, or APP_TARBALL)
# ============================================================================
set -euo pipefail

REF="${1:-HEAD}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(tr -d ' \t\r\n' < "$REPO_DIR/VERSION" 2>/dev/null || echo 0.0.0)"
OUT="${BUNDLE_OUT:-$REPO_DIR/dist}"
STAGE="$OUT/satom-builder-installer"
OS_TAG="rhel9-x86_64"
# Must contain the installer's REQUIRED_PKGS for dnf (tests/test_bundles_offline.py),
# plus best-effort SELinux tooling.
# python3.11: the system python3 on EL9 is 3.9 and the code requires >= 3.10.
# [ABI-OFFLINE] The system libraries the interpreter is built against are
# listed EXPLICITLY, so the installer can repair an old base image offline.
PKGS=(python3.11 python3.11-pip nginx openssl curl ca-certificates sudo git
      openssh-clients policycoreutils-python-utils
      expat openssl-libs sqlite-libs zlib)

echo "==> SATOM Builder offline bundle v${VERSION} -- RHEL family (${OS_TAG})"
command -v dnf >/dev/null || { echo "Requires the RHEL family/dnf (use rockylinux:9)"; exit 1; }
[ "$(id -u)" -eq 0 ] || { echo "Run as root (dnf needs it)"; exit 1; }

echo "==> 0/4 Build tools (dnf-plugins-core, createrepo_c, python3.11, git, tar)"
dnf -y -q install dnf-plugins-core createrepo_c python3.11 python3.11-pip git-core tar gzip findutils >/dev/null

rm -rf "$STAGE"; mkdir -p "$STAGE/bundle/rpms" "$STAGE/bundle/wheels" "$OUT"
echo "$OS_TAG" > "$STAGE/bundle/BUNDLE_OS"

echo "==> 1/4 Downloading .rpms (full dependency closure)"
dnf download -q --resolve --alldeps --destdir "$STAGE/bundle/rpms" "${PKGS[@]}"
N=$(find "$STAGE/bundle/rpms" -name '*.rpm' | wc -l)
echo "    $N .rpm packages"
[ "$N" -gt 40 ] || { echo "ERROR: too few rpms -- something went wrong"; exit 1; }
# Repo metadata: on the target dnf uses bundle/rpms as a local repo
# (--repofrompath) and resolves ONLY what is needed, without network.
createrepo_c -q "$STAGE/bundle/rpms"

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
rm -rf "$STAGE"
echo "==> DONE: $TARBALL"
echo "    $(cat "$TARBALL.sha256")"
