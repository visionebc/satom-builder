#!/usr/bin/env bash
# ============================================================================
# install-satom-builder.sh -- SATOM Builder installer (native, systemd).
#
# Built the same way as SATOM's installer (installers/install-satom.sh in the
# SATOM repo; see deploy/VENDORED.md for what was taken from it), renamed, with
# no path, account, unit or key shared with SATOM.
#
#   Supported (x86_64, systemd as PID 1):
#     ONLINE  : Debian 12/13, Ubuntu 22.04/24.04, RHEL/Rocky/Alma 9,
#               openSUSE Leap/SLES 15
#     OFFLINE : Debian 12, RHEL 9, SUSE 15 -- a bundle/ directory next to this
#               script (installers/build-offline-bundle*.sh). ONE family per
#               bundle; a bundle for another family/release is a hard error.
#
# EXECUTION ORDER (everything is asked first, then installed)
#   0. Preflight: permissions and environment; every blocker listed at once.
#   1. Questions (or --answers FILE --yes): IP, port, names, code source,
#      admin account, certificate, firewall.
#   2. OS packages (python >= 3.10, nginx, openssl, sudo, git, ...).
#   3. Service account, application tree, Python venv.
#   4. Configuration (.env), database migration, publisher key, admin user.
#   5. Privilege model: runtime sudoers, systemd units, root-owned CLI + runner.
#   6. TLS (internal CA + leaf whose SAN covers every name) and nginx.
#   7. Start, health check, summary.
#
# MODES
#   sudo bash install-satom-builder.sh                     interactive
#   sudo bash install-satom-builder.sh --answers FILE --yes   unattended
#   sudo bash install-satom-builder.sh --preflight         checks only
#   sudo bash install-satom-builder.sh --dry-run [...]     asks, prints the plan, changes nothing
#   bash install-satom-builder.sh --print-sudoers [ACCOUNT]   installer sudoers rule
#   sudo bash install-satom-builder.sh --uninstall [--purge] [--yes]
#
# Re-running it is idempotent and keeps the data (data/, .env secrets, keys).
# Updates are NOT done by re-running it: they are signed update packages
# (docs/offline-update-packages.md).
# ============================================================================
set -euo pipefail

VERSION="0.1.0"
APP_DIR="/opt/satom-builder"
ETC_DIR="/etc/satom-builder"
PKI_DIR="${ETC_DIR}/pki"
PUBLISHER_DIR="${ETC_DIR}/publisher"
PUBLISHER_KEY="${PUBLISHER_DIR}/publisher_ed25519.key"
TRUST_DIR="${ETC_DIR}/update-keys"
LOG_DIR="/var/log/satom-builder"
ACME_WEBROOT="/var/www/satom-builder-acme"
INSTALL_LOG="/var/log/satom-builder-install.log"
APP_USER="${SATOM_BUILDER_APP_USER:-satom-builder}"
BIND="127.0.0.1:8100"
SUDOERS_FILE="/etc/sudoers.d/satom-builder"
ADMIN_PW_FILE="/root/satom-builder-admin-password.txt"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_DIR="${SCRIPT_DIR}/bundle"

c_bold=$'\033[1m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_red=$'\033[31m'; c_off=$'\033[0m'
[ -t 1 ] || { c_bold=; c_grn=; c_ylw=; c_red=; c_off=; }
_log() { [ -w "$(dirname "$INSTALL_LOG")" ] && printf '%s\n' "$*" >> "$INSTALL_LOG" 2>/dev/null || true; }
say()  { echo "${c_bold}==>${c_off} $*"; _log "==> $*"; }
ok()   { echo "    ${c_grn}OK${c_off} $*"; _log "    OK $*"; }
warn() { echo "    ${c_ylw}!${c_off}  $*"; _log "    WARN $*"; }
die()  { echo "${c_red}ERROR:${c_off} $*" >&2; _log "ERROR: $*"; exit 1; }

# -- arguments ----------------------------------------------------------------
ASSUME_YES=0; DRY_RUN=0; MODE_PREFLIGHT=0; MODE_UNINSTALL=0; PURGE=0; ANSWERS_FILE=""
usage() {
    cat <<'EOF'
install-satom-builder.sh -- SATOM Builder installer

Options:
  --preflight, --check   Check permissions and requirements only; change nothing.
  --answers FILE         KEY=VALUE file with the answers (SETUP_* variables below).
  --yes                  Never prompt: every answer comes from SETUP_* (environment
                         or --answers) or its default; a missing one stops the
                         install and names its variable.
  --dry-run              Ask (or read) every answer, print the plan, change nothing.
  --print-sudoers [ACCT] Print the sudoers rule that lets ACCT run this installer.
  --uninstall [--purge]  Remove the services, units, vhost and root-side copies.
                         Data, keys and the account are kept unless --purge.
  -h, --help             This help.

Answer variables (all optional unless stated):
  SETUP_IP                IP address of this machine (default: detected)
  SETUP_PORT              HTTPS port (default 443)
  SETUP_NAMES             DNS name(s) the console is reached by (default: hostname -f)
  SETUP_SOURCE            online|offline (default: offline when bundle/ is next to
                          this script, else online)
  SETUP_APP_TARBALL       code from this app.tar.gz (online mode)
  SETUP_GIT_URL           code from this repository (online, when there is neither
                          a tarball nor a checkout around this script; REQUIRED then)
  SETUP_GIT_REF           branch or tag to clone (default main)
  SETUP_ADMIN_USER        first administrator (default admin)
  SETUP_ADMIN_PASSWORD    its password, 12..1024 characters (with --yes and empty:
                          generated and saved to /root/satom-builder-admin-password.txt, 0600)
  SETUP_RESET_ADMIN       y|n: on a re-run, set the admin password again (default n)
  SETUP_CERT              self|import (default self: internal CA)
  SETUP_CERT_FILE, SETUP_KEY_FILE, SETUP_CHAIN_FILE   with SETUP_CERT=import
  SETUP_FIREWALL          y|n: open the ports if firewalld/ufw is active (default y)
  SETUP_EXISTING          reinstall|abort when SATOM Builder is already installed
                          (default abort; a re-run keeps every piece of data)
  SETUP_CONFIRM           y|n: the final "Continue?" (default y)
EOF
}
while [ $# -gt 0 ]; do
    case "$1" in
        --preflight|--check) MODE_PREFLIGHT=1 ;;
        --yes|-y)   ASSUME_YES=1 ;;
        --dry-run)  DRY_RUN=1 ;;
        --answers)  shift; ANSWERS_FILE="${1:-}"; [ -n "$ANSWERS_FILE" ] || { echo "--answers needs a file" >&2; exit 2; } ;;
        --uninstall) MODE_UNINSTALL=1 ;;
        --purge)    PURGE=1 ;;
        --print-sudoers) shift; INSTALLER_USER="${1:-sbinstall}"; MODE_SUDOERS=1; break ;;
        -h|--help)  usage; exit 0 ;;
        *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
    shift
done

# -----------------------------------------------------------------------------
# --print-sudoers (does NOT need root). The rule for the INSTALLER ACCOUNT, so
# the systems team gets it without the repository. Installing is root work; the
# rule avoids HANDING OVER THE ROOT PASSWORD. Not to be confused with
# /etc/sudoers.d/satom-builder, the RUNTIME's two-command allowlist.
# -----------------------------------------------------------------------------
if [ "${MODE_SUDOERS:-0}" = 1 ]; then
    [[ "$INSTALLER_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || { echo "invalid account name: $INSTALLER_USER" >&2; exit 2; }
    cat <<SUDOEOF
# /etc/sudoers.d/satom-builder-installer -- permission to RUN THE INSTALLER.
# Generated by: install-satom-builder.sh --print-sudoers ${INSTALLER_USER}
#
# Requirements for this to be safe:
#   install -d -m 0755 /opt/staging
#   install -m 0755 install-satom-builder.sh /opt/staging/install-satom-builder.sh
#   chown root:root /opt/staging/install-satom-builder.sh   # the operator must NOT be able to edit it
# If the operator could write that file, the rule amounts to NOPASSWD: ALL.
#
# Remove when the installation window closes:
#   rm /etc/sudoers.d/satom-builder-installer

Cmnd_Alias SATOM_BUILDER_INSTALL = /usr/bin/bash /opt/staging/install-satom-builder.sh, \\
                                   /usr/bin/bash /opt/staging/install-satom-builder.sh --preflight, \\
                                   /usr/bin/bash /opt/staging/install-satom-builder.sh --check, \\
                                   /usr/bin/bash /opt/staging/install-satom-builder.sh --dry-run

${INSTALLER_USER} ALL=(root) NOPASSWD: SATOM_BUILDER_INSTALL
SUDOEOF
    exit 0
fi

[ "$(id -u)" -eq 0 ] || die "Run as root (or with sudo): sudo bash $0"
mkdir -p "$(dirname "$INSTALL_LOG")"; touch "$INSTALL_LOG"; chmod 600 "$INSTALL_LOG"
_log "===== install-satom-builder ${VERSION} -- $(date -u +%Y-%m-%dT%H:%M:%SZ) ====="

# -- answers file -------------------------------------------------------------
# Parsed, never sourced: an answers file is data, and only SETUP_* /
# SATOM_BUILDER_* keys are accepted. A value already in the environment wins.
load_answers() {
    local f="$1" line key val
    [ -f "$f" ] || die "--answers: '$f' does not exist"
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%$'\r'}"
        [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
        if [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?((SETUP|SATOM_BUILDER)_[A-Z0-9_]+)=(.*)$ ]]; then
            key="${BASH_REMATCH[2]}"; val="${BASH_REMATCH[4]}"
            if [[ "$val" =~ ^\"(.*)\"$ ]] || [[ "$val" =~ ^\'(.*)\'$ ]]; then val="${BASH_REMATCH[1]}"; fi
            if [ -z "${!key+x}" ]; then printf -v "$key" '%s' "$val"; export "${key?}"; fi
        else
            die "--answers: a line is not KEY=VALUE with a SETUP_* or SATOM_BUILDER_* key: '${line%%=*}'"
        fi
    done < "$f"
    ok "Answers read from ${f}"
}
[ -n "$ANSWERS_FILE" ] && load_answers "$ANSWERS_FILE"

# -- prompt input --------------------------------------------- [LOUD-READ]
# `read` returns !=0 on EOF. Under `set -euo pipefail` that kills the installer
# WITHOUT PRINTING ANYTHING when it is driven through a pipe or here-doc whose
# answer sequence is shorter than the prompt sequence. EVERY prompt goes
# through ask/ask_secret, which name the prompt that ran out. EOF with partial
# data (last line without a newline) IS a valid answer.
_ask_die() {
    die "No answer for the prompt: \"$1\"
    Input ran out (EOF). Run the installer interactively, give the answer in a
    SETUP_* variable (--answers FILE --yes), or add the missing line."
}
_mask() {  # _mask NAME VALUE -> value safe for the log
    case "$1" in *PASS*|*SECRET*|*TOKEN*|*KEY_FILE*|*GIT_URL*) printf '***' ;; *) printf '%s' "$2" ;; esac
}
ask() {                                  # ask VAR "prompt" "default" [PRESET_VAR]
    local __v="$1" __p="$2" __def="${3:-}" __pre="${4:-}" __rc=0 __ans=""
    if [ -n "$__pre" ] && [ -n "${!__pre:-}" ]; then
        __ans="${!__pre}"
        echo "    ${__p}$(_mask "$__pre" "$__ans")  (from ${__pre})"
    elif [ "$ASSUME_YES" -eq 1 ]; then
        [ -n "$__def" ] || die "--yes: no answer for \"${__p}\" -- set ${__pre:-the answer}"
        __ans="$__def"
        echo "    ${__p}${__ans}  (default)"
    else
        read -rp "    ${__p}${__def:+[${__def}] }" __ans || __rc=$?
        [ "$__rc" -eq 0 ] || [ -n "$__ans" ] || _ask_die "$__p"
        __ans="${__ans:-$__def}"
    fi
    printf -v "$__v" '%s' "$__ans"
    _log "Q: ${__p} -> $(_mask "${__pre:-$__v}" "$__ans")"
}
ask_secret() {                           # ask_secret VAR "prompt" [PRESET_VAR] -> no echo
    local __v="$1" __p="$2" __pre="${3:-}" __rc=0 __sec=""
    if [ -n "$__pre" ] && [ -n "${!__pre:-}" ]; then
        __sec="${!__pre}"; echo "    ${__p}***  (from ${__pre})"
    elif [ "$ASSUME_YES" -eq 1 ]; then
        __sec=""
    else
        read -rsp "    ${__p}" __sec || __rc=$?
        echo
        [ "$__rc" -eq 0 ] || [ -n "$__sec" ] || _ask_die "$__p"
    fi
    printf -v "$__v" '%s' "$__sec"
    _log "Q: ${__p} -> ***"
}
ask_yn() {                               # ask_yn "question" y|n [PRESET_VAR] -> rc 0 = yes
    local __yn
    ask __yn "$1 [y/n] " "$2" "${3:-}"
    # s|si accepted silently (answer files written in Spanish).
    case "$(printf '%s' "$__yn" | tr 'A-Z' 'a-z')" in y|yes|1|true|s|si) return 0 ;; *) return 1 ;; esac
}

# -----------------------------------------------------------------------------
# DISTRO ABSTRACTION LAYER -- everything family-specific lives here
# -----------------------------------------------------------------------------
command -v systemctl >/dev/null 2>&1 || die "systemd is required (Alpine/musl is not supported)"

PKG_MGR=""
for m in apt-get dnf yum zypper; do
    command -v "$m" >/dev/null 2>&1 && { PKG_MGR="${m/apt-get/apt}"; break; }
done
[ -n "$PKG_MGR" ] || die "Unsupported package manager. Supported families: apt (Debian/Ubuntu), dnf/yum (RHEL 9), zypper (SUSE 15)"

# Package names per family (same order of concepts in all of them). The first
# entry is the Python the family needs: RHEL 9's python3 is 3.9, so python3.11;
# SUSE 15's is 3.6, so python311. python3 is never assumed.
case "$PKG_MGR" in
    apt)        REQUIRED_PKGS=(python3 python3-venv python3-pip nginx openssl curl ca-certificates sudo git openssh-client) ;;
    dnf|yum)    REQUIRED_PKGS=(python3.11 python3.11-pip nginx openssl curl ca-certificates sudo git openssh-clients) ;;
    zypper)     REQUIRED_PKGS=(python311 python311-pip nginx openssl curl ca-certificates sudo git openssh) ;;
esac

pkg_installed() {
    case "$PKG_MGR" in
        # "ii" only: `dpkg -s` also succeeds for a REMOVED package whose
        # config files remain ("rc"), or one left half-configured.
        apt) case "$(dpkg-query -W -f='${db:Status-Abbrev}' "$1" 2>/dev/null)" in ii*) return 0 ;; esac
             return 1 ;;
        *)   rpm -q "$1" >/dev/null 2>&1 ;;
    esac
}
pkg_version() {
    case "$PKG_MGR" in
        apt) dpkg-query -W -f='${Version}' "$1" 2>/dev/null || echo "?" ;;
        *)   rpm -q --qf '%{VERSION}-%{RELEASE}' "$1" 2>/dev/null || echo "?" ;;
    esac
}
pkg_install() {
    case "$PKG_MGR" in
        apt)     export DEBIAN_FRONTEND=noninteractive
                 apt-get update -qq >>"$INSTALL_LOG" 2>&1
                 apt-get install -y -qq "$@" >>"$INSTALL_LOG" 2>&1 ;;
        # --allowerasing: EL9 minimal ships curl-minimal, which conflicts with curl.
        dnf)     dnf install -y -q --allowerasing "$@" >>"$INSTALL_LOG" 2>&1 ;;
        yum)     yum install -y -q "$@" >>"$INSTALL_LOG" 2>&1 ;;
        zypper)  zypper --non-interactive --quiet install "$@" >>"$INSTALL_LOG" 2>&1 ;;
    esac
}

# detect_os: identical in installers/satom-builder-setup.sh (a test compares them).
OS_ID=""; OS_VER=""; OS_NAME=""; OS_FAMILY=""; OS_BUNDLE=""; OS_SUPPORT="no"
detect_os() {
    [ -r /etc/os-release ] || die "/etc/os-release does not exist: unrecognisable distribution"
    # In a SUBSHELL: os-release defines VERSION and NAME, which would overwrite ours.
    local id="" id_like="" ver_id="" pretty=""
    { IFS= read -r id; IFS= read -r id_like; IFS= read -r ver_id; IFS= read -r pretty; } < <(
        # shellcheck disable=SC1091
        . /etc/os-release
        printf '%s\n' "${ID:-}" "${ID_LIKE:-}" "${VERSION_ID:-}" "${PRETTY_NAME:-}")
    OS_ID="${id:-?}"; OS_VER="${ver_id:-?}"; OS_NAME="${pretty:-$OS_ID $OS_VER}"
    local like=" ${id} ${id_like} " major="${ver_id%%.*}"
    OS_SUPPORT="no"; OS_BUNDLE=""
    case "$like" in
        *" debian "*|*" ubuntu "*)
            OS_FAMILY=debian
            case "$OS_ID:$major" in
                debian:12) OS_SUPPORT=yes; OS_BUNDLE="debian12-amd64" ;;
                debian:13|ubuntu:22|ubuntu:24) OS_SUPPORT=yes ;;
            esac ;;
        *" rhel "*|*" centos "*|*" fedora "*|*" rocky "*|*" almalinux "*)
            OS_FAMILY=rhel
            case "$OS_ID:$major" in
                rhel:9|rocky:9|almalinux:9|centos:9) OS_SUPPORT=yes; OS_BUNDLE="rhel9-x86_64" ;;
            esac ;;
        *" suse "*|*" opensuse "*|*" sles "*)
            OS_FAMILY=suse
            case "$OS_ID:$major" in
                opensuse-leap:15|sles:15|sled:15) OS_SUPPORT=yes; OS_BUNDLE="suse15-x86_64" ;;
            esac ;;
    esac
}
detect_os

OFFLINE=0
case "${SETUP_SOURCE:-}" in ""|online|offline) ;; *) die "SETUP_SOURCE must be online or offline" ;; esac
if [ "${SETUP_SOURCE:-}" != online ] && [ -d "$BUNDLE_DIR" ]; then
    if [ -d "$BUNDLE_DIR/debs" ]; then
        [ "$PKG_MGR" = "apt" ] || die "The offline bundle contains .deb packages (Debian family) and this machine uses ${PKG_MGR}. Use ONLINE mode (SETUP_SOURCE=online) or the bundle for this family."
        OFFLINE=1
    elif [ -d "$BUNDLE_DIR/rpms-suse" ]; then
        # A directory of its own on purpose: both RPM bundles are .rpm and they
        # are NOT interchangeable (python311 vs python3.11, different base
        # libraries, zypper and dnf read repositories differently).
        case "$PKG_MGR" in
            zypper) OFFLINE=1 ;;
            *) die "The offline bundle contains openSUSE/SLES .rpm packages and this machine uses ${PKG_MGR}. Use ONLINE mode or the bundle for this family." ;;
        esac
    elif [ -d "$BUNDLE_DIR/rpms" ]; then
        case "$PKG_MGR" in
            dnf|yum) OFFLINE=1 ;;
            *) die "The offline bundle contains RHEL-family .rpm packages and this machine uses ${PKG_MGR}. Use ONLINE mode or the bundle for this family." ;;
        esac
    fi
    if [ "$OFFLINE" -eq 1 ]; then
        # Same family is not enough: a Debian 12 closure forced onto Ubuntu 24 or
        # Debian 13 breaks the base system. The builder records what it was built for.
        _bos="$(tr -d ' \t\r\n' < "$BUNDLE_DIR/BUNDLE_OS" 2>/dev/null || true)"
        [ -n "$_bos" ] || die "bundle/BUNDLE_OS is missing: this is not a SATOM Builder offline bundle"
        [ "$_bos" = "$OS_BUNDLE" ] || die "The offline bundle was built for ${_bos} and this machine is ${OS_NAME} (${OS_BUNDLE:-no bundle exists for it}). Use the matching bundle or ONLINE mode."
        [ -f "$BUNDLE_DIR/app.tar.gz" ] || die "bundle/app.tar.gz is missing: incomplete bundle"
    fi
fi
[ "${SETUP_SOURCE:-}" = offline ] && [ "$OFFLINE" -eq 0 ] && die "SETUP_SOURCE=offline but there is no usable bundle/ next to this script (${BUNDLE_DIR})"

# [BUNDLE-DEBS] Which .debs of the bundle this machine actually installs. The
# bundle carries the FULL dependency closure resolved on the build machine, and
# the target is never the same point release. Rules:
#   - a package already installed at the bundle's version or newer is skipped;
#   - a BASE package (systemd, udev, PAM, libc and their libraries) is never
#     touched when ANY version of it is installed: the OS image owns those;
#   - everything else is installed, or upgraded when the bundle's is newer.
BUNDLE_BASE_PKGS="systemd udev libsystemd0 libudev1 libpam0g libpam-modules libpam-modules-bin libpam-runtime libc6 libc-bin libgcc-s1 libstdc++6 init-system-helpers dpkg apt"
bundle_select_debs() {
    local deb pkg ver inst
    for deb in "$BUNDLE_DIR"/debs/*.deb; do
        [ -e "$deb" ] || continue
        pkg="$(dpkg-deb -f "$deb" Package 2>/dev/null || true)"
        ver="$(dpkg-deb -f "$deb" Version 2>/dev/null || true)"
        [ -n "$pkg" ] || continue
        # Installed means status "ii": an "rc" (removed, config kept) or
        # half-configured package still reports a Version and would be skipped.
        inst="$(dpkg-query -W -f='${db:Status-Abbrev}|${Version}' "$pkg" 2>/dev/null || true)"
        case "$inst" in "ii "*) inst="${inst#*|}" ;; *) inst="" ;; esac
        if [ -n "$inst" ]; then
            if dpkg --compare-versions "$inst" ge "$ver"; then continue; fi
            case " $BUNDLE_BASE_PKGS " in *" $pkg "*) continue ;; esac
        fi
        echo "$deb"
    done
}
bundle_install_debs() {
    local list
    list="$(bundle_select_debs)"
    if [ -z "$list" ]; then
        echo "bundle debs: nothing to install" >>"$INSTALL_LOG"
        return 0
    fi
    echo "bundle debs selected:" >>"$INSTALL_LOG"; echo "$list" >>"$INSTALL_LOG"
    # apt, given the FILES, resolves and orders them itself -- including
    # Pre-Depends, which a single `dpkg -i` of the whole list does not honour
    # (alphabetical: python3-minimal before python3.11-minimal fails on a bare
    # image, found by installing offline on one). --no-download: the bundle is
    # the only source. NEVER `apt-get -f install` as a fallback: with no network
    # it "fixes" a half-installed set by REMOVING it.
    # shellcheck disable=SC2086
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-download --no-install-recommends \
        -o Dpkg::Options::=--force-confold $list >>"$INSTALL_LOG" 2>&1 && return 0
    echo "apt-get could not install the bundle's files; two dpkg passes" >>"$INSTALL_LOG"
    # shellcheck disable=SC2086
    dpkg -i $list >>"$INSTALL_LOG" 2>&1 || dpkg -i $list >>"$INSTALL_LOG" 2>&1
    dpkg --configure -a >>"$INSTALL_LOG" 2>&1
}
_suse_bundle_repo() {  # prints a temp dir holding a repos.d with ONLY the bundle
    local zr; zr="$(mktemp -d)"
    mkdir -p "$zr/repos.d" "$zr/cache"
    cat > "$zr/repos.d/satom-builder-bundle.repo" <<ZREPO
[satom-builder-bundle]
name=SATOM Builder offline bundle
baseurl=dir://$BUNDLE_DIR/rpms-suse
enabled=1
autorefresh=0
gpgcheck=0
keeppackages=0
ZREPO
    echo "$zr"
}
bundle_install() {
    if [ -d "$BUNDLE_DIR/debs" ]; then
        bundle_install_debs
    elif [ -d "$BUNDLE_DIR/rpms-suse" ]; then
        # zypper gets a repo directory OF ITS OWN (--reposd-dir) holding only
        # the bundle: no network, no system repos touched, nothing left behind.
        local zr; zr="$(_suse_bundle_repo)"
        zypper --non-interactive --no-gpg-checks \
               --reposd-dir "$zr/repos.d" --cache-dir "$zr/cache" \
               install --no-recommends --auto-agree-with-licenses \
               "${REQUIRED_PKGS[@]}" >>"$INSTALL_LOG" 2>&1 || { rm -rf "$zr"; return 1; }
        rm -rf "$zr"
    else
        # bundle/rpms is a local dnf repo (repodata generated at build time).
        "$PKG_MGR" -y --disablerepo='*' \
            --repofrompath="satom-builder-bundle,file://$BUNDLE_DIR/rpms" \
            --setopt=satom-builder-bundle.gpgcheck=0 \
            --setopt=install_weak_deps=False --allowerasing \
            install "${REQUIRED_PKGS[@]}" >>"$INSTALL_LOG" 2>&1 || return 1
        "$PKG_MGR" -y --disablerepo='*' \
            --repofrompath="satom-builder-bundle,file://$BUNDLE_DIR/rpms" \
            --setopt=satom-builder-bundle.gpgcheck=0 \
            --setopt=install_weak_deps=False \
            install policycoreutils-python-utils >>"$INSTALL_LOG" 2>&1 || true
    fi
}
# [ABI-OFFLINE] Upgrade the system libraries the interpreter was built
# against, from the bundle only. Called only once the interpreter is broken.
bundle_upgrade_abi_libs() {
    local rc=0
    if [ -d "$BUNDLE_DIR/rpms-suse" ]; then
        local zr; zr="$(_suse_bundle_repo)"
        zypper --non-interactive --no-gpg-checks \
               --reposd-dir "$zr/repos.d" --cache-dir "$zr/cache" \
               install --no-recommends --auto-agree-with-licenses --force-resolution \
               libexpat1 libopenssl3 libsqlite3-0 libz1 >>"$INSTALL_LOG" 2>&1 || rc=1
        rm -rf "$zr"
    elif [ -d "$BUNDLE_DIR/rpms" ]; then
        "$PKG_MGR" -y --disablerepo='*' \
            --repofrompath="satom-builder-bundle,file://$BUNDLE_DIR/rpms" \
            --setopt=satom-builder-bundle.gpgcheck=0 --setopt=install_weak_deps=False \
            --allowerasing upgrade expat openssl-libs sqlite-libs zlib >>"$INSTALL_LOG" 2>&1 || rc=1
    elif [ -d "$BUNDLE_DIR/debs" ]; then
        bundle_install_debs || rc=1
    fi
    return $rc
}

# Python >= 3.10, from an explicit list: python3 is NEVER assumed (RHEL 9's is
# 3.9; SUSE 15 has no python3 link for python311).
pick_python() {
    PYBIN=""
    local c
    for c in python3.13 python3.12 python3.11 python3.10 python3; do
        command -v "$c" >/dev/null 2>&1 || continue
        "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null \
            && { PYBIN="$(command -v "$c")"; return 0; }
    done
    return 1
}

echo ""
echo "+----------------------------------------------------------+"
echo "|  SATOM Builder ${VERSION} -- installer $( [ $OFFLINE -eq 1 ] && echo '(OFFLINE, local bundle)' || echo '(ONLINE)' )"
echo "+----------------------------------------------------------+"
echo ""

# -----------------------------------------------------------------------------
# UNINSTALL
# -----------------------------------------------------------------------------
uninstall() {
    say "Uninstall SATOM Builder"
    if [ "$PURGE" -eq 1 ]; then
        warn "--purge DELETES the database, the publisher key, the certificates and the service account."
        if [ "$ASSUME_YES" -eq 0 ]; then
            local c=""
            ask c "Type DELETE to confirm: " "" ""
            [ "$c" = DELETE ] || die "Cancelled"
        fi
    fi
    local u
    for u in satom-builder-updater.path satom-builder-updater.service satom-builder.service; do
        systemctl disable --now "$u" >>"$INSTALL_LOG" 2>&1 || true
    done
    for u in /etc/systemd/system/satom-builder.service /etc/systemd/system/satom-builder-updater.path \
             /etc/systemd/system/satom-builder-updater.service; do
        rm -rf "$u" "${u}.d"
    done
    systemctl daemon-reload >>"$INSTALL_LOG" 2>&1 || true
    rm -f "$SUDOERS_FILE"
    rm -f /etc/nginx/sites-enabled/satom-builder.conf /etc/nginx/sites-available/satom-builder.conf \
          /etc/nginx/conf.d/satom-builder.conf /etc/nginx/vhosts.d/satom-builder.conf
    # Put back what the install disabled, and nothing else.
    if [ -f "$ETC_DIR/.nginx-default-site-disabled" ] && [ -f /etc/nginx/sites-available/default ] \
       && [ ! -e /etc/nginx/sites-enabled/default ]; then
        ln -s ../sites-available/default /etc/nginx/sites-enabled/default
        rm -f "$ETC_DIR/.nginx-default-site-disabled"
        ok "Re-enabled the distro's default site"
    fi
    if [ -f /etc/nginx/conf.d/default.conf.disabled-by-satom-builder ] && [ ! -e /etc/nginx/conf.d/default.conf ]; then
        mv /etc/nginx/conf.d/default.conf.disabled-by-satom-builder /etc/nginx/conf.d/default.conf
        ok "Restored the packaged conf.d/default.conf"
    fi
    if command -v nginx >/dev/null 2>&1 && nginx -t >>"$INSTALL_LOG" 2>&1; then
        systemctl try-restart nginx >>"$INSTALL_LOG" 2>&1 || true
    fi
    rm -rf /usr/local/lib/satom-builder-cli /usr/local/lib/satom-builder-runner /usr/local/sbin/satom-builder
    [ -L /usr/bin/satom-builder ] && rm -f /usr/bin/satom-builder
    ok "Services, units, sudoers rule, vhost and root-side copies removed"
    if [ "$PURGE" -eq 1 ]; then
        rm -rf "$APP_DIR" "$ETC_DIR" "$LOG_DIR" "$ACME_WEBROOT" /var/lib/satom-builder-rollback \
               /var/lib/satom-builder-runner /var/lib/satom-builder-release
        if id -u "$APP_USER" >/dev/null 2>&1; then
            userdel "$APP_USER" >>"$INSTALL_LOG" 2>&1 || warn "could not delete the account ${APP_USER}"
        fi
        ok "Data, keys, certificates, logs and the service account removed (--purge)"
    else
        ok "KEPT: ${APP_DIR} (data/, .env), ${ETC_DIR} (keys, certificates), account ${APP_USER}, logs"
        echo "    A later install on this machine reuses them. Delete everything with: --uninstall --purge"
    fi
    echo "    Firewall rules opened for the console (if any) were left in place."
    exit 0
}
[ "$MODE_UNINSTALL" -eq 1 ] && uninstall

# -----------------------------------------------------------------------------
# STEP 0 -- PREFLIGHT: permissions and environment. Runs BEFORE asking anything
# and BEFORE modifying anything; collects ALL problems and reports them together.
# -----------------------------------------------------------------------------
PF_FAIL=(); PF_WARN=()
pf_bad()  { PF_FAIL+=("$1"); echo "    ${c_red}X${c_off}  $1"; _log "    FAIL $1"; }
pf_warn() { PF_WARN+=("$1"); warn "$1"; }
pf_have() { command -v "$1" >/dev/null 2>&1; }
pf_bundle_has() {   # does the OFFLINE bundle carry this package?
    [ $OFFLINE -eq 1 ] || return 1
    ls "$BUNDLE_DIR"/debs/"$1"_*.deb  >/dev/null 2>&1 && return 0
    ls "$BUNDLE_DIR"/rpms/"$1"-[0-9]*.rpm >/dev/null 2>&1 && return 0
    ls "$BUNDLE_DIR"/rpms-suse/"$1"-[0-9]*.rpm >/dev/null 2>&1 && return 0
    return 1
}
pf_writable() {
    local d="$1"
    while [ ! -d "$d" ] && [ "$d" != "/" ]; do d="$(dirname "$d")"; done
    local p="${d}/.satom-builder-probe.$$"
    if ( : > "$p" ) 2>/dev/null; then rm -f "$p"; return 0; fi
    return 1
}
pf_free_mb() {
    local d="$1"
    while [ ! -d "$d" ] && [ "$d" != "/" ]; do d="$(dirname "$d")"; done
    df -Pk "$d" 2>/dev/null | awk 'NR==2{printf "%d", $4/1024}'
}
pf_https_ok() {   # 0 reachable, 1 not, 2 no tool
    if pf_have curl; then curl -fsS -m 8 -o /dev/null "$1" 2>/dev/null; return $?; fi
    local py="" c
    for c in python3.12 python3.11 python3; do pf_have "$c" && { py="$c"; break; }; done
    if [ -z "$py" ]; then
        # A bare image has neither curl nor python yet (step 2 installs them):
        # a TCP connect to the host's :443 is still a real egress answer.
        local h="${1#https://}"; h="${h%%/*}"
        pf_have timeout || return 2
        timeout 8 bash -c ": > /dev/tcp/${h}/443" 2>/dev/null && return 0
        return 1
    fi
    "$py" -c 'import sys,urllib.request
try: urllib.request.urlopen(sys.argv[1], timeout=8)
except Exception: sys.exit(1)' "$1" 2>/dev/null
}
pf_port_owner() {   # name of the process listening on TCP port $1, or nothing
    local line="" name=""
    if pf_have ss; then
        line="$(ss -Hlntp "sport = :$1" 2>/dev/null | head -1)"
    elif pf_have netstat; then
        line="$(netstat -lntp 2>/dev/null | awk -v p=":$1\$" '$4 ~ p {print; exit}')"
    fi
    [ -n "$line" ] || return 0
    name="$(printf '%s' "$line" | grep -oE '"[^"]+"' | head -1 | tr -d '"')"
    [ -z "$name" ] && name="$(printf '%s' "$line" | awk '{print $NF}')"
    echo "$name"
}

preflight() {
    say "Step 0/7 -- Preflight: permissions and environment (nothing is modified)"
    ok "Identity: root (uid 0)"
    case "$OS_SUPPORT" in
        yes) ok "System: ${OS_NAME} (${OS_FAMILY} family) -- supported$( [ $OFFLINE -eq 1 ] && echo ', offline bundle matches' )" ;;
        *)   pf_bad "System ${OS_NAME} is not supported. Supported: Debian 12/13, Ubuntu 22.04/24.04, RHEL/Rocky/Alma 9, openSUSE Leap/SLES 15" ;;
    esac
    [ "$(uname -m)" = "x86_64" ] && ok "Architecture: x86_64" \
        || { if [ $OFFLINE -eq 1 ]; then pf_bad "Architecture $(uname -m): the offline bundles are x86_64 only"; else pf_warn "Architecture $(uname -m): only x86_64 is tested"; fi; }
    local d
    for d in /opt /etc /etc/systemd/system /var/log /usr/local/sbin /usr/local/lib; do
        if pf_writable "$d"; then ok "Write access verified on ${d}"
        else pf_bad "No write access to ${d} -- you are root but the filesystem prevents it (read-only mount, unprivileged container or SELinux)"; fi
    done
    if [ -d /run/systemd/system ]; then ok "systemd is the init (PID 1)"
    else pf_bad "systemd is not running as PID 1. Containers without systemd are not supported."; fi
    ok "Package manager: ${PKG_MGR}$( [ $OFFLINE -eq 1 ] && echo ' (OFFLINE mode, local bundle)' )"

    local miss=() b
    for b in useradd usermod getent install runuser tar gzip awk sed grep hostname df stat find; do
        pf_have "$b" || miss+=("$b")
    done
    if [ ${#miss[@]} -eq 0 ]; then ok "Base utilities present (shadow, util-linux, coreutils)"
    else pf_bad "Missing base utilities: ${miss[*]} -- install your distro's shadow/util-linux/coreutils/tar packages"; fi
    pf_have ss || pf_have netstat || pf_warn "Neither 'ss' nor 'netstat': cannot check whether the ports are free"

    # [PFSUDO] Step 5 writes /etc/sudoers.d and validates it with visudo.
    local sudo_miss=() sb
    for sb in sudo visudo; do pf_have "$sb" || sudo_miss+=("$sb"); done
    if [ ${#sudo_miss[@]} -eq 0 ]; then ok "sudo and visudo present"
    elif [ $OFFLINE -eq 0 ]; then pf_warn "Missing ${sudo_miss[*]} -- step 2 will install the 'sudo' package"
    elif pf_bundle_has sudo; then ok "Missing ${sudo_miss[*]}, but the offline bundle carries 'sudo'"
    else pf_bad "Missing ${sudo_miss[*]} and the OFFLINE bundle does not carry 'sudo'"; fi

    local pv="" c
    for c in python3.13 python3.12 python3.11 python3.10 python3; do
        pf_have "$c" || continue
        if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
            pv="$($c --version 2>&1)"; break
        fi
    done
    if [ -n "$pv" ]; then ok "Suitable Python: ${pv} (minimum 3.10)"
    elif [ $OFFLINE -eq 1 ] && ! pf_bundle_has "${REQUIRED_PKGS[0]}"; then
        pf_bad "No Python >= 3.10 and the offline bundle does not carry ${REQUIRED_PKGS[0]}"
    else pf_warn "No Python >= 3.10 yet -- step 2 installs ${REQUIRED_PKGS[0]}"; fi

    local opt_mb var_mb ram_mb
    opt_mb=$(pf_free_mb /opt); var_mb=$(pf_free_mb /var)
    if [ "${opt_mb:-0}" -lt 1500 ]; then pf_bad "Only ${opt_mb} MB free on /opt -- code + venv + update rollback points need 1.5 GB minimum (5 GB recommended)"
    elif [ "${opt_mb:-0}" -lt 5000 ]; then pf_warn "/opt has ${opt_mb} MB free; 5 GB is recommended (each update keeps a venv copy for rollback)"
    else ok "/opt: ${opt_mb} MB free"; fi
    if [ "${var_mb:-0}" -lt 1000 ]; then pf_warn "/var has only ${var_mb} MB free (rollback points and logs live there)"
    else ok "/var: ${var_mb} MB free"; fi
    ram_mb=$(awk '/^MemTotal:/{printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0)
    if [ "${ram_mb:-0}" -lt 900 ]; then pf_warn "Total RAM ${ram_mb} MB -- 1 GB minimum, 2 GB recommended"
    else ok "RAM: ${ram_mb} MB"; fi

    if systemctl is-active --quiet satom-builder.service 2>/dev/null; then
        pf_warn "SATOM Builder is already installed and RUNNING: a re-run keeps every piece of data; you will be asked (SETUP_EXISTING=reinstall|abort). Updates use signed update packages, not this installer."
    elif [ -d "${APP_DIR}/satom_builder" ] || [ -f "${APP_DIR}/.env" ]; then
        pf_warn "${APP_DIR} already holds an installation (service stopped) -- it is kept"
    else
        ok "No previous installation in ${APP_DIR}"
    fi
    local o
    o="$(pf_port_owner 80)"
    case "$o" in "") ok "Port 80 free (HTTP redirect + ACME challenge)" ;;
        *nginx*) ok "Port 80 served by nginx -- the vhost is added next to what is there" ;;
        *) pf_warn "Port 80 is used by ${o} -- nginx needs it for the redirect and the ACME challenge" ;; esac
    o="$(pf_port_owner 8100)"
    case "$o" in ""|*gunicorn*|*python*) : ;;
        *) pf_bad "Port 8100 (the application's loopback port) is used by ${o}" ;; esac

    if pf_have timedatectl; then
        if [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = "yes" ]; then
            ok "Clock synchronized by NTP"
        else
            pf_warn "The clock is NOT synchronized by NTP -- TLS and update-package checks suffer if it drifts"
        fi
    fi
    if [ $OFFLINE -eq 0 ]; then
        # `|| _rc=$?`, never a bare call: under `set -e` a probe that answers
        # "no tool" (2) would end the preflight silently.
        local _rc=0
        pf_https_ok "https://pypi.org/simple/" || _rc=$?
        case $_rc in
            0) ok "PyPI egress verified (needed for the venv)" ;;
            1) pf_bad "No access to PyPI -- the venv CANNOT be built online. Use the OFFLINE bundle or open HTTPS egress." ;;
            *) pf_warn "Neither curl nor python3: cannot check Internet egress" ;;
        esac
    else
        local nw nd
        nw="$(find "$BUNDLE_DIR/wheels" -maxdepth 1 -name '*.whl' 2>/dev/null | wc -l)"
        nd="$(find "$BUNDLE_DIR" -maxdepth 2 \( -name '*.deb' -o -name '*.rpm' \) 2>/dev/null | wc -l)"
        if [ "$nw" -gt 0 ] && [ "$nd" -gt 0 ]; then ok "Offline bundle: ${nd} OS package(s), ${nw} wheel(s), app.tar.gz"
        else pf_bad "The offline bundle is incomplete (${nd} OS packages, ${nw} wheels)"; fi
    fi
    if pf_have getenforce; then
        ok "SELinux: $(getenforce 2>/dev/null)"
    fi

    echo ""
    if [ ${#PF_FAIL[@]} -gt 0 ]; then
        echo "${c_red}${c_bold}PREFLIGHT: ${#PF_FAIL[@]} blocker(s).${c_off} Nothing has been modified."
        local f; for f in "${PF_FAIL[@]}"; do echo "  ${c_red}X${c_off}  $f"; done
        if [ ${#PF_WARN[@]} -gt 0 ]; then
            echo "  -- and ${#PF_WARN[@]} warning(s) --"; for f in "${PF_WARN[@]}"; do echo "  ${c_ylw}!${c_off}  $f"; done
        fi
        echo ""
        die "Fix the blockers and run again (or 'bash $0 --preflight' to re-check without installing)"
    fi
    if [ ${#PF_WARN[@]} -gt 0 ]; then
        echo "${c_ylw}${c_bold}PREFLIGHT OK with ${#PF_WARN[@]} warning(s).${c_off}"
    else
        echo "${c_grn}${c_bold}PREFLIGHT OK -- the machine meets all requirements.${c_off}"
    fi
    echo ""
}

if [ "$MODE_PREFLIGHT" -eq 1 ]; then
    echo "Check mode: only permissions and requirements are checked. Nothing is installed."
    preflight
    exit 0
fi
preflight

# -----------------------------------------------------------------------------
# STEP 1 -- QUESTIONS (all up front; nothing is touched until this is done)
# -----------------------------------------------------------------------------
say "Step 1/7 -- Questions"
EXISTING=0
if [ -d "${APP_DIR}/satom_builder" ] || [ -f "${APP_DIR}/.env" ]; then EXISTING=1; fi
if [ "$EXISTING" -eq 1 ]; then
    _ex=""
    while :; do
        ask _ex "SATOM Builder is already installed here. Re-run the installer over it (data is kept) or abort? [reinstall/abort] " "abort" SETUP_EXISTING
        case "$(printf '%s' "$_ex" | tr 'A-Z' 'a-z')" in
            reinstall) break ;;
            abort) die "Cancelled: the existing installation is untouched" ;;
            *) [ "$ASSUME_YES" -eq 1 ] || [ -n "${SETUP_EXISTING:-}" ] && die "SETUP_EXISTING must be reinstall or abort"; warn "Answer reinstall or abort" ;;
        esac
    done
fi

DETECTED_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1 || true)"
[ -n "$DETECTED_IP" ] || DETECTED_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
ask NODE_IP "IP address of this machine " "$DETECTED_IP" SETUP_IP
[[ "$NODE_IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || die "Invalid IP: $NODE_IP (SETUP_IP)"

WEB_PORT=""
while [ -z "$WEB_PORT" ]; do
    ask WEB_PORT "HTTPS port for the web console " "443" SETUP_PORT
    if ! [[ "$WEB_PORT" =~ ^[0-9]+$ ]] || [ "$WEB_PORT" -lt 1 ] || [ "$WEB_PORT" -gt 65535 ] \
       || [ "$WEB_PORT" = 80 ] || [ "$WEB_PORT" = 8100 ]; then
        { [ "$ASSUME_YES" -eq 1 ] || [ -n "${SETUP_PORT:-}" ]; } && die "Invalid SETUP_PORT: $WEB_PORT (1-65535, not 80 or 8100)"
        warn "Invalid port: $WEB_PORT"; WEB_PORT=""; continue
    fi
    PORT_OWNER="$(pf_port_owner "$WEB_PORT")"
    case "$PORT_OWNER" in
        ""|*nginx*) : ;;
        *) { [ "$ASSUME_YES" -eq 1 ] || [ -n "${SETUP_PORT:-}" ]; } && die "Port ${WEB_PORT} is in use by ${PORT_OWNER} (SETUP_PORT)"
           warn "Port ${WEB_PORT} is ALREADY IN USE by ${PORT_OWNER}; choose another one"; WEB_PORT="" ;;
    esac
done

# [SERVED-NAMES] Asked HERE because two things are minted from them that cannot
# be fixed later without reissuing: the vhost's server_name and the SAN list of
# the certificate. Plain `hostname` is the SHORT name; `hostname -f` is used.
DEFAULT_NAMES="$(hostname -f 2>/dev/null || true)"; [ -n "$DEFAULT_NAMES" ] || DEFAULT_NAMES="$(hostname)"
ask SERVED_NAMES "DNS name(s) this console is reached by (space-separated) " "$DEFAULT_NAMES" SETUP_NAMES
_sn_norm=""
for _sn in $(printf '%s' "$SERVED_NAMES" | tr ',;' '  ' | tr 'A-Z' 'a-z'); do
    case " $_sn_norm " in *" $_sn "*) continue ;; esac
    _sn_norm="$_sn_norm $_sn"
done
SERVED_NAMES="${_sn_norm# }"
[ -n "$SERVED_NAMES" ] || die "At least one DNS name is required (SETUP_NAMES)"
for _sn in $SERVED_NAMES; do
    [[ "$_sn" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] || die "Invalid DNS name: $_sn (SETUP_NAMES)"
done
_sn_short="$(hostname | tr 'A-Z' 'a-z')"
case " $SERVED_NAMES " in *" $_sn_short "*) : ;; *) SERVED_NAMES="$SERVED_NAMES $_sn_short" ;; esac
FIRST_NAME="${SERVED_NAMES%% *}"

# Code source (fresh install only: a re-run keeps the installed code).
CODE_SRC=""; GIT_URL=""; GIT_REF=""; SRC_TREE="$(cd "$SCRIPT_DIR/.." && pwd)"
if [ "$EXISTING" -eq 1 ] && [ -d "${APP_DIR}/satom_builder" ]; then
    CODE_SRC="keep"
elif [ "$OFFLINE" -eq 1 ]; then
    CODE_SRC="bundle"
elif [ -n "${SETUP_APP_TARBALL:-}" ]; then
    [ -f "$SETUP_APP_TARBALL" ] || die "SETUP_APP_TARBALL=${SETUP_APP_TARBALL} does not exist"
    CODE_SRC="tarball"
elif [ -f "$SRC_TREE/VERSION" ] && [ -d "$SRC_TREE/satom_builder" ] && [ -f "$SRC_TREE/requirements.txt" ]; then
    CODE_SRC="checkout"
else
    CODE_SRC="git"
    ask GIT_URL "Repository URL to install the code from (https://...) " "" SETUP_GIT_URL
    [[ "$GIT_URL" =~ ^https:// ]] || die "SETUP_GIT_URL must be an https:// URL"
    ask GIT_REF "Branch or tag " "main" SETUP_GIT_REF
fi

# Administrator.
ADMIN_USER=""; ADMIN_PASS=""; _pw2=""; ADMIN_PW_GENERATED=0; SET_ADMIN=1
if [ "$EXISTING" -eq 1 ] && [ -f "${APP_DIR}/.env" ]; then
    SET_ADMIN=0
    if ask_yn "Set the administrator password again?" n SETUP_RESET_ADMIN; then SET_ADMIN=1; fi
fi
if [ "$SET_ADMIN" -eq 1 ]; then
    ask ADMIN_USER "Administrator user name " "admin" SETUP_ADMIN_USER
    [[ "$ADMIN_USER" =~ ^[A-Za-z0-9._@-]{1,64}$ ]] || die "Invalid administrator name: $ADMIN_USER (SETUP_ADMIN_USER)"
    while :; do
        ask_secret ADMIN_PASS "Password for '${ADMIN_USER}' (12-1024 characters, Enter = generate one): " SETUP_ADMIN_PASSWORD
        if [ -z "$ADMIN_PASS" ]; then
            ADMIN_PASS="$(openssl rand -base64 30 | tr -d '/+=\n' | cut -c1-24)"
            ADMIN_PW_GENERATED=1
            break
        fi
        if [ "${#ADMIN_PASS}" -lt 12 ] || [ "${#ADMIN_PASS}" -gt 1024 ]; then
            [ -n "${SETUP_ADMIN_PASSWORD:-}" ] && die "SETUP_ADMIN_PASSWORD must be 12 to 1024 characters"
            warn "The password must be 12 to 1024 characters"; continue
        fi
        if [ -z "${SETUP_ADMIN_PASSWORD:-}" ]; then
            ask_secret _pw2 "Repeat the password: " ""
            [ "$ADMIN_PASS" = "$_pw2" ] || { warn "The passwords do not match"; continue; }
        fi
        break
    done
fi

# Certificate.
CERT_MODE=""; CERT_FILE=""; KEY_FILE=""; CHAIN_FILE=""
while :; do
    ask CERT_MODE "Certificate: internal CA (self) or import your own (import)? [self/import] " "self" SETUP_CERT
    case "$CERT_MODE" in self|import) break ;; esac
    { [ "$ASSUME_YES" -eq 1 ] || [ -n "${SETUP_CERT:-}" ]; } && die "SETUP_CERT must be self or import"
    warn "Answer self or import"
done
if [ "$CERT_MODE" = import ]; then
    ask CERT_FILE "Certificate file (PEM) " "" SETUP_CERT_FILE
    ask KEY_FILE "Private key file (PEM) " "" SETUP_KEY_FILE
    ask CHAIN_FILE "Chain file (PEM, '-' for none) " "-" SETUP_CHAIN_FILE
    [ "$CHAIN_FILE" = "-" ] && CHAIN_FILE=""
    [ -f "$CERT_FILE" ] || die "Certificate file not found: $CERT_FILE (SETUP_CERT_FILE)"
    [ -f "$KEY_FILE" ] || die "Key file not found: $KEY_FILE (SETUP_KEY_FILE)"
    [ -z "$CHAIN_FILE" ] || [ -f "$CHAIN_FILE" ] || die "Chain file not found: $CHAIN_FILE (SETUP_CHAIN_FILE)"
    c1="$(openssl x509 -in "$CERT_FILE" -noout -pubkey 2>/dev/null | openssl sha256)"
    c2="$(openssl pkey -in "$KEY_FILE" -pubout 2>/dev/null | openssl sha256)"
    [ -n "$c1" ] && [ "$c1" = "$c2" ] || die "The certificate and the key do NOT match"
    ok "Certificate and key match"
fi

FW_OPEN=0; FW_KIND=""
if systemctl is-active --quiet firewalld 2>/dev/null; then FW_KIND=firewalld
elif command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q 'Status: active'; then FW_KIND=ufw; fi
if [ -n "$FW_KIND" ]; then
    if ask_yn "${FW_KIND} is active. Open ports ${WEB_PORT}/tcp and 80/tcp?" y SETUP_FIREWALL; then FW_OPEN=1; fi
fi

echo ""
say "Summary: IP=${NODE_IP}  port=${WEB_PORT}  names=${SERVED_NAMES}  code=${CODE_SRC}  source=$( [ $OFFLINE -eq 1 ] && echo offline || echo online )  cert=${CERT_MODE}$( [ "$SET_ADMIN" -eq 1 ] && echo "  admin=${ADMIN_USER}" )"
if [ "$DRY_RUN" -eq 1 ]; then
    echo ""
    echo "DRY RUN -- this is what would be done (nothing was changed):"
    echo "  packages : ${REQUIRED_PKGS[*]} ($( [ $OFFLINE -eq 1 ] && echo 'from the bundle' || echo "from the ${PKG_MGR} mirrors" ))"
    echo "  account  : ${APP_USER} (system, nologin); tree ${APP_DIR}; venv ${APP_DIR}/venv"
    echo "  config   : ${APP_DIR}/.env (root:${APP_USER} 0640), publisher key ${PUBLISHER_KEY}"
    echo "  units    : satom-builder.service (gunicorn ${BIND}), satom-builder-updater.{path,service}"
    echo "  privilege: ${SUDOERS_FILE} (nginx -t, systemctl reload nginx), /usr/local/sbin/satom-builder"
    echo "  TLS      : ${PKI_DIR} ($( [ "$CERT_MODE" = import ] && echo 'imported certificate' || echo 'internal CA + leaf' ), SAN: ${SERVED_NAMES} ${NODE_IP})"
    echo "  console  : https://${FIRST_NAME}$( [ "$WEB_PORT" = 443 ] || echo ":${WEB_PORT}" )/"
    exit 0
fi
ask_yn "Continue with the installation?" y SETUP_CONFIRM || die "Cancelled by the user"

# -----------------------------------------------------------------------------
# STEP 2 -- PACKAGES
# -----------------------------------------------------------------------------
say "Step 2/7 -- Packages (${PKG_MGR}$( [ $OFFLINE -eq 1 ] && echo ', offline bundle' ))"
MISSING=()
for p in "${REQUIRED_PKGS[@]}"; do
    if pkg_installed "$p"; then ok "$p $(pkg_version "$p")"
    else MISSING+=("$p"); warn "$p missing -- will be installed"; fi
done
if [ ${#MISSING[@]} -gt 0 ]; then
    if [ $OFFLINE -eq 1 ]; then
        say "Installing from the offline bundle (${#MISSING[@]} package(s) + dependencies)"
        bundle_install || die "Failed installing the bundle's packages (check $INSTALL_LOG)"
    else
        say "Installing from the mirrors (${PKG_MGR})"
        pkg_install "${MISSING[@]}" || die "Package installation failed (check $INSTALL_LOG)"
    fi
    for p in "${MISSING[@]}"; do pkg_installed "$p" || die "$p is still not installed (check $INSTALL_LOG)"; done
    ok "Packages installed"
else
    ok "All required packages are already present"
fi

# -----------------------------------------------------------------------------
# STEP 3 -- PYTHON, ACCOUNT, CODE, VENV
# -----------------------------------------------------------------------------
say "Step 3/7 -- Python, service account, application tree"
pick_python || die "No Python >= 3.10 available after installing packages"
"$PYBIN" -m venv --help >/dev/null 2>&1 || die "$PYBIN lacks the venv module -- install your distro's venv package"
# The interpreter has to WORK, not just exist: an ABI mismatch with the system
# libraries only shows when a C extension is imported.
PY_C_EXTENSIONS="pyexpat ssl sqlite3 ctypes zlib _hashlib"
py_runtime_broken() {
    for m in $PY_C_EXTENSIONS; do
        "$PYBIN" -c "import $m" >/dev/null 2>&1 || { echo "$m"; return 0; }
    done
    return 1
}
BROKEN_MOD="$(py_runtime_broken || true)"
if [ -n "$BROKEN_MOD" ]; then
    warn "The interpreter $PYBIN cannot import '$BROKEN_MOD'"
    if [ "$OFFLINE" -eq 1 ]; then bundle_upgrade_abi_libs || true
    else
        case "$PKG_MGR" in
            zypper) zypper --non-interactive update libexpat1 libopenssl3 libsqlite3-0 libz1 >>"$INSTALL_LOG" 2>&1 || true ;;
            dnf)    dnf -y upgrade expat openssl-libs sqlite-libs zlib >>"$INSTALL_LOG" 2>&1 || true ;;
            yum)    yum -y update expat openssl-libs sqlite zlib >>"$INSTALL_LOG" 2>&1 || true ;;
            apt)    apt-get install -y --only-upgrade libexpat1 libssl3 libsqlite3-0 zlib1g >>"$INSTALL_LOG" 2>&1 || true ;;
        esac
    fi
    STILL="$(py_runtime_broken || true)"
    [ -z "$STILL" ] || die "The distro's Python is still broken: 'import $STILL' fails. Upgrade the system and run the installer again. Nothing else has been modified."
    ok "System libraries upgraded -- the interpreter imports its C extensions"
else
    ok "Interpreter $($PYBIN --version 2>&1) verified (imports ${PY_C_EXTENSIONS})"
fi

NOLOGIN="$(command -v nologin || echo /usr/sbin/nologin)"
[ -x "$NOLOGIN" ] || NOLOGIN=/bin/false
if ! id -u "$APP_USER" >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "$APP_DIR" --no-create-home --shell "$NOLOGIN" \
            --comment "SATOM Builder service account" "$APP_USER" \
        || die "Could not create the service account ${APP_USER}"
    ok "Service account ${APP_USER} created (system, no shell, no login)"
else
    ok "Service account ${APP_USER} already exists -- reused"
fi

deploy_code() {
    mkdir -p "$APP_DIR"
    case "$CODE_SRC" in
        keep)
            ok "Code kept as installed (version $(cat "$APP_DIR/VERSION" 2>/dev/null || echo '?')); updates use signed packages"
            return 0 ;;
        bundle)  tar -xzf "$BUNDLE_DIR/app.tar.gz" -C "$APP_DIR" || die "Could not extract bundle/app.tar.gz" ;;
        tarball) tar -xzf "$SETUP_APP_TARBALL" -C "$APP_DIR" || die "Could not extract ${SETUP_APP_TARBALL}" ;;
        checkout)
            if git -C "$SRC_TREE" rev-parse --verify -q HEAD >/dev/null 2>&1; then
                git -C "$SRC_TREE" archive --format=tar HEAD | tar -x -C "$APP_DIR" \
                    || die "git archive of ${SRC_TREE} failed"
            else
                tar -C "$SRC_TREE" --exclude=./.git --exclude=./venv --exclude=./.venv \
                    --exclude=./data --exclude=./state --exclude=./dist --exclude=./.env \
                    -cf - . | tar -x -C "$APP_DIR" || die "Could not copy ${SRC_TREE}"
            fi ;;
        git)
            local t; t="$(mktemp -d)"
            git clone -q --depth 1 --branch "$GIT_REF" "$GIT_URL" "$t/src" >>"$INSTALL_LOG" 2>&1 \
                || { rm -rf "$t"; die "git clone failed (check $INSTALL_LOG; the URL itself is not logged)"; }
            git -C "$t/src" archive --format=tar HEAD | tar -x -C "$APP_DIR" \
                || { rm -rf "$t"; die "Could not export the cloned tree"; }
            rm -rf "$t" ;;
    esac
    [ -f "$APP_DIR/VERSION" ] && [ -d "$APP_DIR/satom_builder" ] && [ -f "$APP_DIR/requirements.txt" ] \
        || die "The code in ${APP_DIR} is incomplete (VERSION, satom_builder/ and requirements.txt are required)"
    # [ADOPT-TREE] Every update package leans on git (rollback snapshot, dropped
    # files, the deployed-revision commit). The tree as extracted IS the
    # release archive; the shipped .gitignore keeps node-local paths out.
    if [ ! -d "$APP_DIR/.git" ]; then
        if git -C "$APP_DIR" init -q >>"$INSTALL_LOG" 2>&1 \
           && git -C "$APP_DIR" add -A >>"$INSTALL_LOG" 2>&1 \
           && git -C "$APP_DIR" -c user.name="SATOM Builder" -c user.email=satom-builder@localhost \
                  commit -q -m "baseline: SATOM Builder $(cat "$APP_DIR/VERSION") as installed" >>"$INSTALL_LOG" 2>&1; then
            ok "Application tree recorded as a git baseline (update packages need it)"
        else
            warn "Could not record the tree in git -- the first update package will do it"
        fi
    fi
    ok "Code installed to ${APP_DIR} (version $(cat "$APP_DIR/VERSION"), source: ${CODE_SRC})"
}
deploy_code
APP_VERSION="$(tr -d ' \t\r\n' < "$APP_DIR/VERSION")"
[ "$APP_VERSION" = "$VERSION" ] || warn "This installer is ${VERSION}; the code is ${APP_VERSION}"

cd "$APP_DIR"
if [ ! -x venv/bin/python ]; then
    "$PYBIN" -m venv venv || die "Could not create the venv with ${PYBIN}"
fi
if [ $OFFLINE -eq 1 ]; then
    venv/bin/python -m pip install -q --no-index --find-links "$BUNDLE_DIR/wheels" --upgrade pip >>"$INSTALL_LOG" 2>&1 || true
    venv/bin/python -m pip install -q --no-index --find-links "$BUNDLE_DIR/wheels" -r requirements.txt >>"$INSTALL_LOG" 2>&1 \
        || die "offline pip install failed (check $INSTALL_LOG)"
else
    venv/bin/python -m pip install -q --upgrade pip >>"$INSTALL_LOG" 2>&1 || true
    venv/bin/python -m pip install -q -r requirements.txt >>"$INSTALL_LOG" 2>&1 || die "pip install failed (check $INSTALL_LOG)"
fi
ok "Virtual environment ready ($(venv/bin/python --version 2>&1))"

# Layout. The service account owns the tree (it is what update packages
# replace) except .env (root:<account> 0640: the app only READS its secrets).
install -d -m 0750 "$APP_DIR/data" "$APP_DIR/data/update-requests" "$APP_DIR/data/update-status" \
                   "$APP_DIR/data/update-uploads" "$LOG_DIR"
install -d -m 0700 "$APP_DIR/state"
chown -R "${APP_USER}:${APP_USER}" "$APP_DIR" "$LOG_DIR"
chmod 0750 "$APP_DIR" "$APP_DIR/data"
chmod 0700 "$APP_DIR/state"
install -d -m 0755 -o root -g root "$ETC_DIR" "$TRUST_DIR"
install -d -m 0750 -o root -g "$APP_USER" "$PUBLISHER_DIR"
ok "Layout: ${APP_DIR} (${APP_USER}), data/ 0750, state/ 0700, ${ETC_DIR} root"

# -----------------------------------------------------------------------------
# STEP 4 -- CONFIGURATION, DATABASE, KEYS, ADMIN
# -----------------------------------------------------------------------------
say "Step 4/7 -- Configuration, database, publisher key, administrator"
ENVF="$APP_DIR/.env"
env_has() { [ -f "$ENVF" ] && grep -qE "^$1=." "$ENVF"; }
env_add() {  # env_add KEY VALUE -- only when missing: secrets are NEVER regenerated
    env_has "$1" && return 0
    printf '%s=%s\n' "$1" "$2" >> "$ENVF"
}
if [ ! -f "$ENVF" ]; then
    ( umask 077; printf '# Generated by install-satom-builder.sh %s -- %s\n# Keep a copy: FERNET_KEY cannot be regenerated without losing the stored secrets.\n' \
        "$VERSION" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$ENVF" )
fi
env_add SECRET_KEY "$("$PYBIN" -c 'import secrets; print(secrets.token_hex(32))')"
env_add FERNET_KEY "$(venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
    || die "Could not generate FERNET_KEY (is cryptography in requirements.txt?)"
env_add SATOM_BUILDER_DATA "$APP_DIR/data"
env_add SATOM_BUILDER_PUBLISHER_KEY "$PUBLISHER_KEY"
env_add SATOM_BUILDER_BIND "$BIND"
chown root:"$APP_USER" "$ENVF"; chmod 0640 "$ENVF"
ok ".env ready (root:${APP_USER} 0640; existing secrets kept)"

# manage runs AS THE SERVICE ACCOUNT with .env in its environment.
run_manage() {
    # shellcheck source=/dev/null
    ( set -a; . "$ENVF"; set +a
      cd "$APP_DIR" && runuser -u "$APP_USER" -- env HOME="$APP_DIR" "$APP_DIR/venv/bin/python" -m satom_builder.manage "$@" )
}
run_manage migrate >>"$INSTALL_LOG" 2>&1 \
    || die "manage migrate failed -- the database schema could not be created. Check the end of $INSTALL_LOG"
ok "Database migrated (SQLite in ${APP_DIR}/data)"

# The publisher key is created by root (its directory is root-owned) and is
# then made readable by the service account's group only.
# shellcheck source=/dev/null
PUB_OUT="$( ( set -a; . "$ENVF"; set +a; cd "$APP_DIR" && "$APP_DIR/venv/bin/python" -m satom_builder.manage keygen-publisher ) 2>>"$INSTALL_LOG" )" \
    || die "manage keygen-publisher failed (check $INSTALL_LOG)"
[ -f "$PUBLISHER_KEY" ] || die "manage keygen-publisher did not create ${PUBLISHER_KEY}"
chown root:"$APP_USER" "$PUBLISHER_KEY"; chmod 0640 "$PUBLISHER_KEY"
PUBLISHER_KEY_ID="$(printf '%s\n' "$PUB_OUT" | grep -oE '[0-9a-f]{64}' | head -1 || true)"
ok "Publisher key ${PUBLISHER_KEY} (root:${APP_USER} 0640)${PUBLISHER_KEY_ID:+, key_id ${PUBLISHER_KEY_ID}}"

if [ "$SET_ADMIN" -eq 1 ]; then
    # The password reaches the app through a 0600 file the service account
    # owns -- never argv (visible in ps) and never the log.
    PWF="$APP_DIR/state/.admin-password.$$"
    install -m 0600 -o "$APP_USER" -g "$APP_USER" /dev/null "$PWF"
    printf '%s' "$ADMIN_PASS" > "$PWF"
    if run_manage create-admin --username "$ADMIN_USER" --password-file "$PWF" >>"$INSTALL_LOG" 2>&1; then
        rm -f "$PWF"
    else
        rm -f "$PWF"; die "manage create-admin failed (check $INSTALL_LOG)"
    fi
    if [ "$ADMIN_PW_GENERATED" -eq 1 ]; then
        ( umask 077; printf '%s\n' "$ADMIN_PASS" > "$ADMIN_PW_FILE" )
        ok "Administrator '${ADMIN_USER}' created; generated password saved to ${ADMIN_PW_FILE} (0600)"
    else
        ok "Administrator '${ADMIN_USER}' created with the password you chose"
    fi
fi
unset ADMIN_PASS _pw2 SETUP_ADMIN_PASSWORD
run_manage check >>"$INSTALL_LOG" 2>&1 || die "manage check reports a configuration problem (check $INSTALL_LOG)"
ok "manage check: configuration sane"
: > "$APP_DIR/data/.installed"; chown "$APP_USER:$APP_USER" "$APP_DIR/data/.installed"

# -----------------------------------------------------------------------------
# STEP 5 -- PRIVILEGE MODEL: sudoers, units, CLI, runner, trust store
# -----------------------------------------------------------------------------
say "Step 5/7 -- sudoers, systemd units, operator CLI, update runner"
# A DELIBERATELY TINY allowlist. Package managers and unrestricted systemctl
# are NOT granted: both are root-equivalent. Everything that needs real
# privilege goes through the root update runner, which validates its input.
NGINX_BIN="$(command -v nginx || echo /usr/sbin/nginx)"
SYSTEMCTL_BIN="$(command -v systemctl || echo /usr/bin/systemctl)"
cat > "${SUDOERS_FILE}.tmp" <<SUDOERS
# Generated by install-satom-builder.sh -- the service account's runtime allowlist.
# Do NOT add package managers or generic systemctl here: that would be root.
Cmnd_Alias SATOM_BUILDER_NGINX = ${NGINX_BIN} -t, ${SYSTEMCTL_BIN} reload nginx
${APP_USER} ALL=(root) NOPASSWD: SATOM_BUILDER_NGINX
Defaults:${APP_USER} !requiretty
SUDOERS
chmod 0440 "${SUDOERS_FILE}.tmp"
visudo -cf "${SUDOERS_FILE}.tmp" >>"$INSTALL_LOG" 2>&1 \
    || { rm -f "${SUDOERS_FILE}.tmp"; die "The generated sudoers rule is invalid"; }
mv -f "${SUDOERS_FILE}.tmp" "$SUDOERS_FILE"
ok "sudoers: ${APP_USER} may only run 'nginx -t' and 'systemctl reload nginx'"

# Units: every template deploy/ ships, copied verbatim. The service account is
# pinned by a DROP-IN, never by editing a unit: every update re-copies the
# templates. The list is DERIVED (deploy/*.service minus the root runner), the
# same rule as self_update_runner.NONROOT_UNITS.
for src in "$APP_DIR"/deploy/*.service "$APP_DIR"/deploy/*.path "$APP_DIR"/deploy/*.timer; do
    [ -f "$src" ] || continue
    install -o root -g root -m 0644 "$src" "/etc/systemd/system/$(basename "$src")"
done
enforce_unit_user() {
    local src unit d
    for src in "$APP_DIR"/deploy/*.service; do
        unit="${src##*/}"
        [ "$unit" = "satom-builder-updater.service" ] && continue
        [ -f "/etc/systemd/system/$unit" ] || continue
        d="/etc/systemd/system/${unit}.d"
        install -d -m 0755 "$d"
        cat > "$d/10-app-user.conf" <<DROPIN
# Generated by SATOM Builder (installer / update runner). Do NOT edit by hand.
# Lives in a drop-in rather than in the unit because every update re-copies the
# deploy/ templates: the drop-in survives that.
[Service]
User=${APP_USER}
Group=${APP_USER}
DROPIN
    done
}
enforce_unit_user
systemctl daemon-reload
ok "Units installed; service account pinned via drop-in 10-app-user.conf"

bash "$APP_DIR/deploy/install-cli.sh" >>"$INSTALL_LOG" 2>&1 || die "Could not install the operator CLI (check $INSTALL_LOG)"
ok "Operator CLI: /usr/local/sbin/satom-builder (root-owned copy)"
bash "$APP_DIR/deploy/install-runner.sh" >>"$INSTALL_LOG" 2>&1 || die "Could not harden the update runner (check $INSTALL_LOG)"
ok "Update runner: /usr/local/lib/satom-builder-runner (root-owned copy, drop-in 10-root-copy.conf)"
SEEDED=0
for k in "$APP_DIR"/deploy/update-keys/*.pub; do
    [ -f "$k" ] || continue
    install -o root -g root -m 0644 "$k" "$TRUST_DIR/" && SEEDED=$((SEEDED+1))
done
[ "$SEEDED" -gt 0 ] && ok "Trust store ${TRUST_DIR}: ${SEEDED} release key(s)" \
    || warn "No release key shipped: this node accepts no update package until one is added to ${TRUST_DIR}"

# -----------------------------------------------------------------------------
# STEP 6 -- TLS AND NGINX
# -----------------------------------------------------------------------------
say "Step 6/7 -- TLS certificate and nginx"
TLS="$APP_DIR/deploy/tls-bootstrap.sh"
if [ "$CERT_MODE" = import ]; then
    sh "$TLS" import-cert --pki "$PKI_DIR" --cert "$CERT_FILE" --key "$KEY_FILE" \
        ${CHAIN_FILE:+--chain "$CHAIN_FILE"} >>"$INSTALL_LOG" 2>&1 || die "Could not import the certificate (check $INSTALL_LOG)"
    ok "Certificate imported (marked source=imported: re-runs never reissue over it)"
else
    sh "$TLS" ensure-pki --pki "$PKI_DIR" --names "$SERVED_NAMES" --ip "$NODE_IP" >>"$INSTALL_LOG" 2>&1 \
        || die "Could not issue the node certificate (check $INSTALL_LOG)"
    if grep -q '"source"[[:space:]]*:[[:space:]]*"imported"' "$PKI_DIR/public/meta.json" 2>/dev/null; then
        ok "Operator certificate present (source=imported) -- left untouched"
    else
        ok "Internal CA + node certificate (SAN: ${SERVED_NAMES} ${NODE_IP})"
    fi
fi

if [ -d /etc/nginx/sites-enabled ]; then
    NGXCONF=/etc/nginx/sites-available/satom-builder.conf
elif [ -d /etc/nginx/vhosts.d ]; then
    # openSUSE/SLES: vhosts.d is included ONCE; conf.d appears twice in the stock nginx.conf.
    NGXCONF=/etc/nginx/vhosts.d/satom-builder.conf
else
    mkdir -p /etc/nginx/conf.d
    NGXCONF=/etc/nginx/conf.d/satom-builder.conf
fi
# ONE author for the vhost: deploy/tls-bootstrap.sh write-vhost.
write_builder_vhost() {
    sh "$TLS" write-vhost --out "$NGXCONF" --pki "$PKI_DIR" --names "$SERVED_NAMES" --ip "$NODE_IP" \
        --port "$WEB_PORT" --upstream "$BIND" --acme-webroot "$ACME_WEBROOT" "$@" >>"$INSTALL_LOG" 2>&1 \
        || die "Could not write the vhost (check $INSTALL_LOG)"
    if [ -d /etc/nginx/sites-enabled ]; then
        ln -sf "$NGXCONF" /etc/nginx/sites-enabled/satom-builder.conf
    fi
}
# Polite about the host: the distro's OWN welcome site is disabled only when it
# is the untouched packaged one (Debian/Ubuntu: the enabling link to an
# unmodified sites-available/default). Anything an operator wrote is left alone.
stock_debian_default() {
    local f=/etc/nginx/sites-available/default want got
    [ -L /etc/nginx/sites-enabled/default ] || return 1
    [ "$(readlink -f /etc/nginx/sites-enabled/default)" = "$f" ] || return 1
    command -v dpkg-query >/dev/null 2>&1 || return 1
    want="$(dpkg-query -W -f='${Conffiles}\n' nginx-common 2>/dev/null | awk -v f="$f" '$1==f {print $2}')"
    got="$(md5sum "$f" 2>/dev/null | awk '{print $1}')"
    [ -n "$want" ] && [ "$want" = "$got" ]
}
if stock_debian_default; then
    rm -f /etc/nginx/sites-enabled/default
    : > "$ETC_DIR/.nginx-default-site-disabled"     # --uninstall puts it back
    ok "Disabled the distro's unmodified welcome site (sites-available/default is kept)"
fi
# nginx.org packages ship conf.d/default.conf (a welcome page on :80
# default_server). Moved aside -- never deleted -- only when unmodified.
if [ -f /etc/nginx/conf.d/default.conf ] && command -v rpm >/dev/null 2>&1 \
   && rpm -qf /etc/nginx/conf.d/default.conf >/dev/null 2>&1 \
   && ! rpm -Vf /etc/nginx/conf.d/default.conf 2>/dev/null | grep -q 'conf.d/default.conf'; then
    mv -f /etc/nginx/conf.d/default.conf /etc/nginx/conf.d/default.conf.disabled-by-satom-builder
    ok "Moved the packaged conf.d/default.conf aside (default.conf.disabled-by-satom-builder)"
fi
# Written WITH default_server. If another vhost on this host already claims it,
# nginx says "duplicate default server" and ONLY THEN is it rewritten without
# it: the console then answers by its own names and IP, and the other site
# keeps the default. Any other nginx -t failure falls through to the
# `nginx -t || die` below, which reports the real cause.
write_builder_vhost --default-server
if ! nginx -t >>"$INSTALL_LOG" 2>&1; then
    if nginx -t 2>&1 | grep -qi "duplicate default server"; then
        warn "Another site on this host already claims default_server -- the console's vhost does not take it"
        write_builder_vhost
    fi
fi
if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce 2>/dev/null)" = "Enforcing" ]; then
    setsebool -P httpd_can_network_connect 1 >>"$INSTALL_LOG" 2>&1 || warn "SELinux: could not enable httpd_can_network_connect"
    if command -v semanage >/dev/null 2>&1; then
        semanage port -a -t http_port_t -p tcp "$WEB_PORT" >>"$INSTALL_LOG" 2>&1 \
            || semanage port -m -t http_port_t -p tcp "$WEB_PORT" >>"$INSTALL_LOG" 2>&1 || true
    fi
fi
if [ "$FW_OPEN" -eq 1 ]; then
    case "$FW_KIND" in
        firewalld) for _p in "$WEB_PORT" 80; do firewall-cmd --permanent --add-port="${_p}/tcp" >>"$INSTALL_LOG" 2>&1 || true; done
                   firewall-cmd --reload >>"$INSTALL_LOG" 2>&1 || true ;;
        ufw)       for _p in "$WEB_PORT" 80; do ufw allow "${_p}/tcp" >>"$INSTALL_LOG" 2>&1 || true; done ;;
    esac
    ok "${FW_KIND}: ports ${WEB_PORT}/tcp and 80/tcp opened"
fi
nginx -t >>"$INSTALL_LOG" 2>&1 || die "nginx -t failed (check $INSTALL_LOG)"
# [NGINX-START] (re)start nginx and WAIT until it really serves. A RESTART, not
# a reload: on Debian the package starts nginx from its postinst before this
# vhost exists, and on openSUSE the unit is Type=simple, so a reload right after
# a start finds $MAINPID empty and systemd takes the whole service down. A
# non-empty pid file is also what makes the app's later sudo reload safe.
systemctl enable nginx >>"$INSTALL_LOG" 2>&1 || true
systemctl restart nginx >>"$INSTALL_LOG" 2>&1 \
    || die "nginx did not start (diagnose with: systemctl status nginx)"
_nginx_ready=0
for _i in $(seq 1 30); do
    if systemctl is-active --quiet nginx && [ -s /run/nginx.pid ] \
       && (exec 3<>"/dev/tcp/127.0.0.1/${WEB_PORT}") 2>/dev/null; then
        _nginx_ready=1; break
    fi
    sleep 1
done
[ "$_nginx_ready" = 1 ] || die "nginx started but does not accept connections on ${WEB_PORT} after 30s"
ok "nginx serving HTTPS on :${WEB_PORT}"

# -----------------------------------------------------------------------------
# STEP 7 -- START, HEALTH CHECK, SUMMARY
# -----------------------------------------------------------------------------
say "Step 7/7 -- Start and health check"
systemctl enable --now satom-builder.service >>"$INSTALL_LOG" 2>&1 \
    || die "satom-builder.service did not start (systemctl status satom-builder; $INSTALL_LOG)"
# enable --now is a no-op on a running service; a re-run may have changed .env.
systemctl restart satom-builder.service >>"$INSTALL_LOG" 2>&1 || true
systemctl enable --now satom-builder-updater.path >>"$INSTALL_LOG" 2>&1 \
    || die "satom-builder-updater.path did not start: updates would queue and never run"
HEALTH=fail
for _i in $(seq 1 45); do
    if curl -skf --max-time 3 --resolve "${FIRST_NAME}:${WEB_PORT}:127.0.0.1" \
         "https://${FIRST_NAME}:${WEB_PORT}/healthz" >/dev/null 2>&1; then HEALTH=ok; break; fi
    sleep 1
done
[ "$HEALTH" = ok ] || die "The console does not answer https://${FIRST_NAME}:${WEB_PORT}/healthz within 45 s -- check: journalctl -u satom-builder -n 50"
ok "healthz 200 through nginx -- installation COMPLETE"
if runuser -u "$APP_USER" -- sudo -n "$NGINX_BIN" -t >/dev/null 2>&1; then
    ok "sudoers: ${APP_USER} runs 'nginx -t' without a password"
else
    warn "sudo does not honour ${SUDOERS_FILE} on this host (managed sudoers?). Check: sudo -l -U ${APP_USER}"
fi

PORT_SFX="$( [ "$WEB_PORT" = 443 ] || echo ":${WEB_PORT}" )"
echo ""
echo "+----------------------------------------------------------+"
echo "|  SATOM Builder ${APP_VERSION} installed"
echo "+----------------------------------------------------------+"
echo "  Console       : https://${FIRST_NAME}${PORT_SFX}/   (or https://${NODE_IP}${PORT_SFX}/)"
if [ "$SET_ADMIN" -eq 1 ]; then
    echo "  Administrator : ${ADMIN_USER}  ($( [ "$ADMIN_PW_GENERATED" -eq 1 ] && echo "password in ${ADMIN_PW_FILE}, 0600 -- delete it after the first login" || echo 'the password you chose' ))"
fi
echo "  Publisher key : ${PUBLISHER_KEY}${PUBLISHER_KEY_ID:+ (key_id ${PUBLISHER_KEY_ID})}"
echo "                  register its public half in SATOM -> Trusted publishers"
echo "  Services      : satom-builder.service, satom-builder-updater.path"
echo "  Operator CLI  : sudo satom-builder status"
echo "  Logs          : ${LOG_DIR}/  .  install log: ${INSTALL_LOG}"
echo "  Uninstall     : sudo bash $0 --uninstall   (add --purge to delete the data too)"
echo ""
echo "  If ${FIRST_NAME} does not resolve yet, create it in your DNS pointing to ${NODE_IP}."
