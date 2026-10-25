#!/usr/bin/env bash
# ============================================================================
# satom-builder-setup.sh -- SATOM Builder guided installer.
#
# Built the same way as SATOM's installers/satom-setup.sh (native path only;
# see deploy/VENDORED.md), renamed, sharing nothing with SATOM.
#
#   Usage:  sudo bash satom-builder-setup.sh                     (interactive)
#           sudo bash satom-builder-setup.sh --check             (checks only)
#           sudo bash satom-builder-setup.sh --answers answers.env --yes
#           sudo bash satom-builder-setup.sh --bundle satom-builder-offline-<v>-<os>.tar.gz
#           sudo bash satom-builder-setup.sh --uninstall [--purge]
#
# FLOW
#   1. Operating system + requirements (CPU, RAM, disk, ports, Internet).
#   2. Install source: ONLINE (the install-satom-builder.sh next to this script)
#      or OFFLINE (an extracted bundle around this script, or a bundle tarball
#      given with --bundle / found next to this script; its .sha256 is checked).
#   3. Runs install-satom-builder.sh, which asks EVERYTHING first (or reads the
#      same --answers file) and then installs. Its preflight lists every
#      blocker at once.
#   4. Summary saved to /root/satom-builder-setup-summary.txt (0600).
#
# NON-INTERACTIVE MODE (--yes): every question takes its value from a SETUP_*
# variable (environment or --answers file); a missing one stops the install
# and names the variable. See install-satom-builder.sh --help for the list.
# ============================================================================
set -Eeuo pipefail

# The release this script installs: the same one it is published with.
SETUP_VERSION="0.1.3"
LOG="/var/log/satom-builder-setup.log"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE_NAME="install-satom-builder.sh"

c_b=$'\033[1m'; c_g=$'\033[32m'; c_y=$'\033[33m'; c_r=$'\033[31m'; c_d=$'\033[2m'; c_0=$'\033[0m'
[ -t 1 ] || { c_b=; c_g=; c_y=; c_r=; c_d=; c_0=; }
log_raw() { printf '%s %s\n' "$(date '+%F %T')" "$*" >>"$LOG" 2>/dev/null || true; }
say()  { echo; echo "${c_b}==> $*${c_0}"; log_raw "==> $*"; }
ok()   { echo "    ${c_g}OK${c_0} $*"; log_raw "OK $*"; }
warn() { echo "    ${c_y}!${c_0}  $*"; log_raw "WARN $*"; }
info() { echo "    ${c_d}.${c_0}  $*"; log_raw "INFO $*"; }
die()  { echo; echo "${c_r}ERROR:${c_0} $*" >&2; log_raw "ERROR $*"; exit 1; }

CURRENT_STEP="start"
on_error() {
    local rc=$?
    echo
    echo "${c_r}The setup stopped at step: ${CURRENT_STEP} (exit code ${rc}).${c_0}" >&2
    echo "  Full log: ${LOG} (and /var/log/satom-builder-install.log)" >&2
    echo "  You can run this script again: it resumes without undoing what is done." >&2
    log_raw "FAILED at step '${CURRENT_STEP}' rc=${rc}"
    exit "$rc"
}
trap on_error ERR

usage() {
    cat <<'EOF'
satom-builder-setup.sh -- SATOM Builder guided installer

Options:
  --check              System checks only; installs nothing.
  --yes                No questions: SETUP_* variables / defaults (a missing one is named).
  --answers FILE       KEY=VALUE file with the answers (passed to the installer too).
  --bundle FILE        Offline bundle (satom-builder-offline-<ver>-<os>.tar.gz). Not
                       needed when run from inside an extracted bundle.
  --dry-run            Ask (or read) every answer, print the plan, change nothing.
  --force              Continue even if this distribution is not on the supported list.
  --uninstall          Remove the services, units, vhost and root-side copies (data kept).
  --purge              With --uninstall: ALSO delete data, keys and the account. Irreversible.
  -h, --help           This help.

Setup variables (with --yes), besides the installer's (install-satom-builder.sh --help):
  SETUP_SOURCE=online|offline   (default: offline inside a bundle or when a matching
                                 bundle tarball sits next to this script)
  SETUP_BUNDLE=FILE             same as --bundle
EOF
}

ASSUME_YES=0; CHECK_ONLY=0; FORCE=0; UNINSTALL=0; PURGE=0; DRY_RUN=0
ANSWERS_FILE=""; BUNDLE_FILE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --check) CHECK_ONLY=1 ;;
        --yes|-y) ASSUME_YES=1 ;;
        --answers) shift; ANSWERS_FILE="${1:-}"; [ -f "$ANSWERS_FILE" ] || die "--answers: '${ANSWERS_FILE}' does not exist" ;;
        --bundle) shift; BUNDLE_FILE="${1:-}" ;;
        --dry-run) DRY_RUN=1 ;;
        --force) FORCE=1 ;;
        --uninstall) UNINSTALL=1 ;;
        --purge) PURGE=1 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1 (see --help)" ;;
    esac
    shift
done

[ "$(id -u)" -eq 0 ] || die "Run as root: sudo bash $0"
mkdir -p "$(dirname "$LOG")"; touch "$LOG"; chmod 600 "$LOG"
log_raw "===== satom-builder-setup ${SETUP_VERSION} -- $(date -u +%FT%TZ) ====="

# The answers file is parsed (never sourced) for the two setup-level keys;
# the installer reads the same file for the rest.
answer_from_file() {  # answer_from_file KEY -> value or empty
    [ -n "$ANSWERS_FILE" ] || return 0
    sed -n "s/^[[:space:]]*\(export[[:space:]]\+\)\{0,1\}$1=//p" "$ANSWERS_FILE" | tail -1 \
        | sed "s/^\"\(.*\)\"\$/\1/; s/^'\(.*\)'\$/\1/"
}
[ -n "${SETUP_SOURCE:-}" ] || SETUP_SOURCE="$(answer_from_file SETUP_SOURCE)"
[ -n "${SETUP_BUNDLE:-}" ] || SETUP_BUNDLE="$(answer_from_file SETUP_BUNDLE)"
[ -n "$BUNDLE_FILE" ] || BUNDLE_FILE="${SETUP_BUNDLE:-}"

# ask VAR "question" "default" [PRESET_VAR] -- loud on EOF, names the variable.
ask() {
    local __var="$1" __q="$2" __def="${3:-}" __pre="${4:-}" __ans="" __rc=0
    if [ -n "$__pre" ] && [ -n "${!__pre:-}" ]; then
        __ans="${!__pre}"; info "$__q ${__ans} (from ${__pre})"
    elif [ "$ASSUME_YES" -eq 1 ]; then
        [ -n "$__def" ] || die "--yes: ${__pre:-the answer} is missing for: $__q"
        __ans="$__def"; info "$__q ${__ans} (default)"
    else
        read -rp "    ${__q}${__def:+ [${__def}]}: " __ans || __rc=$?
        [ "$__rc" -eq 0 ] || [ -n "$__ans" ] || die "No answer for \"$__q\": input ran out (EOF). Run interactively or set ${__pre:-the answer}."
        __ans="${__ans:-$__def}"
    fi
    printf -v "$__var" '%s' "$__ans"
    log_raw "Q: $__q -> $__ans"
}
ask_choice() {  # ask_choice VAR "question" "opt1|opt2" "default" [PRESET_VAR]
    local __var="$1" __q="$2" __opts="$3" __def="$4" __pre="${5:-}" __c
    while :; do
        ask __c "$__q (${__opts//|/\/})" "$__def" "$__pre"
        __c="$(printf '%s' "$__c" | tr 'A-Z' 'a-z')"
        case "|$__opts|" in *"|$__c|"*) printf -v "$__var" '%s' "$__c"; return ;; esac
        if [ "$ASSUME_YES" -eq 1 ] || { [ -n "$__pre" ] && [ -n "${!__pre:-}" ]; }; then
            die "Invalid value '$__c' for: $__q (options: $__opts)"
        fi
        warn "Valid options: ${__opts//|/, }"
    done
}
ask_yn() {  # ask_yn "question" y|n [PRESET_VAR] -> rc 0 = yes
    local __yn; ask __yn "$1 (y/n)" "$2" "${3:-}"
    case "$(printf '%s' "$__yn" | tr 'A-Z' 'a-z')" in y|yes|1|true|s|si) return 0 ;; *) return 1 ;; esac
}
have() { command -v "$1" >/dev/null 2>&1; }
installer_version() { sed -n 's/^VERSION="\([^"]*\)".*/\1/p' "$1" 2>/dev/null | head -1; }

# detect_os: identical in installers/install-satom-builder.sh (a test compares them).
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

INTERNET=0
check_system() {
    CURRENT_STEP="1 . operating system"
    say "Step 1 . Operating system and requirements"
    detect_os
    if [ "$OS_SUPPORT" = yes ]; then
        ok "System: ${OS_NAME} (${OS_FAMILY} family) -- supported$( [ -n "$OS_BUNDLE" ] && echo ", offline bundle: ${OS_BUNDLE}" || echo ', online install only' )"
    elif [ "$FORCE" -eq 1 ]; then
        warn "System: ${OS_NAME} -- NOT supported (continuing because of --force)"
    else
        die "System ${OS_NAME} is not supported. Supported: Debian 12/13, Ubuntu 22.04/24.04, RHEL/Rocky/Alma 9, openSUSE Leap/SLES 15 (--force to try anyway)"
    fi
    [ "$(uname -m)" = "x86_64" ] || warn "Architecture $(uname -m): only x86_64 is supported"
    { have systemctl && [ -d /run/systemd/system ]; } || die "systemd is required (not detected as init)"
    ok "systemd present"
    local cores mem_mb disk_mb
    cores=$(nproc 2>/dev/null || echo 1)
    mem_mb=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
    disk_mb=$(df -Pm /opt 2>/dev/null | awk 'NR==2{print $4}')
    [ "$cores" -ge 2 ] && ok "CPU: ${cores} cores" || warn "CPU: ${cores} core -- 2 or more recommended"
    if   [ "$mem_mb" -ge 1900 ]; then ok "RAM: ${mem_mb} MB"
    elif [ "$mem_mb" -ge 900 ];  then warn "RAM: ${mem_mb} MB -- 2 GB recommended"
    else die "RAM: ${mem_mb} MB -- not enough (1 GB minimum)"; fi
    if   [ "${disk_mb:-0}" -ge 5000 ]; then ok "Free disk on /opt: $((disk_mb/1024)) GB"
    elif [ "${disk_mb:-0}" -ge 1500 ]; then warn "Free disk on /opt: ${disk_mb} MB -- 5 GB recommended"
    else die "Free disk on /opt: ${disk_mb:-0} MB -- not enough (1.5 GB minimum)"; fi
    have tar || install_tar_from_bundle
    if have curl && curl -fsS -o /dev/null --connect-timeout 8 https://pypi.org/simple/ 2>/dev/null; then
        INTERNET=1; ok "Internet access (pypi.org) OK"
    elif ! have curl && have timeout && timeout 8 bash -c ': > /dev/tcp/pypi.org/443' 2>/dev/null; then
        # A bare image has no curl yet (the installer adds it): TCP :443 answers.
        INTERNET=1; ok "Internet access (pypi.org:443 reachable; curl is installed in step 2)"
    else
        warn "No Internet access (or no curl yet): only the OFFLINE install is possible"
    fi
}

# A minimal RHEL/SUSE image may have no tar: the offline bundle carries it.
# (Found 2026-10-25 installing the RHEL 9 bundle on a minimal Rocky 9: the
# setup stopped at "tar is missing" with the package sitting in bundle/rpms.)
# Extract the bundle without tar: python3 -m tarfile -e <bundle>.tar.gz .
install_tar_from_bundle() {
    local p=""
    for p in "$SCRIPT_DIR"/bundle/rpms/tar-[0-9]*.rpm "$SCRIPT_DIR"/bundle/rpms-suse/tar-[0-9]*.rpm \
             "$SCRIPT_DIR"/bundle/debs/tar_*.deb; do
        [ -f "$p" ] || continue
        info "tar is missing: installing it from the offline bundle ($(basename "$p"))"
        case "$p" in
            *.rpm) rpm -U --quiet "$p" ;;
            *.deb) dpkg -i "$p" >/dev/null ;;
        esac || die "tar is missing and installing $(basename "$p") from the bundle failed"
        have tar && { ok "tar installed from the bundle"; return 0; }
    done
    die "tar is missing: install it with the system package manager, or run this script from an extracted offline bundle (extract it with: python3 -m tarfile -e <bundle>.tar.gz .)"
}

ENGINE=""; IN_BUNDLE=0; WORK=""
choose_source() {
    CURRENT_STEP="2 . install source"
    say "Step 2 . Install source"
    local sibling=""
    if [ -f "$SCRIPT_DIR/$ENGINE_NAME" ]; then
        if [ "$(installer_version "$SCRIPT_DIR/$ENGINE_NAME")" = "$SETUP_VERSION" ]; then
            sibling="$SCRIPT_DIR/$ENGINE_NAME"
            [ -f "$SCRIPT_DIR/bundle/app.tar.gz" ] && IN_BUNDLE=1
        else
            warn "The ${ENGINE_NAME} next to this script is not version ${SETUP_VERSION}: not used"
        fi
    fi
    if [ "$IN_BUNDLE" -eq 0 ] && [ -z "$BUNDLE_FILE" ] && [ -n "$OS_BUNDLE" ]; then
        local cand
        cand="$(find "$SCRIPT_DIR" -maxdepth 1 -name "satom-builder-offline-*-${OS_BUNDLE}.tar.gz" 2>/dev/null | sort -V | tail -1 || true)"
        [ -n "$cand" ] && BUNDLE_FILE="$cand"
    fi
    local def_src=online
    { [ "$IN_BUNDLE" -eq 1 ] || [ -n "$BUNDLE_FILE" ] || [ "$INTERNET" -eq 0 ]; } && def_src=offline
    [ "$IN_BUNDLE" -eq 1 ] && info "Run from an extracted offline bundle: ${SCRIPT_DIR}/bundle"
    local src
    ask_choice src "Install source?" "online|offline" "$def_src" SETUP_SOURCE
    SETUP_SOURCE="$src"; export SETUP_SOURCE

    if [ "$src" = offline ] && [ "$IN_BUNDLE" -eq 1 ]; then
        ENGINE="$sibling"
        ok "Offline bundle: ${SCRIPT_DIR}/bundle (built for $(cat "$SCRIPT_DIR/bundle/BUNDLE_OS" 2>/dev/null || echo '?'))"
    elif [ "$src" = offline ]; then
        [ -n "$OS_BUNDLE" ] || die "There is no offline bundle for ${OS_NAME}: use the online install"
        [ -n "$BUNDLE_FILE" ] || die "Offline mode: copy satom-builder-offline-<version>-${OS_BUNDLE}.tar.gz next to this script or give --bundle FILE"
        [ -f "$BUNDLE_FILE" ] || die "The bundle does not exist: $BUNDLE_FILE"
        case "$(basename "$BUNDLE_FILE")" in
            *"-${OS_BUNDLE}.tar.gz") : ;;
            *) die "$(basename "$BUNDLE_FILE") is not a bundle for ${OS_BUNDLE}: a bundle for another family or release cannot be installed here" ;;
        esac
        if [ -f "${BUNDLE_FILE}.sha256" ]; then
            ( cd "$(dirname "$BUNDLE_FILE")" && sha256sum -c "$(basename "$BUNDLE_FILE").sha256" ) >>"$LOG" 2>&1 \
                || die "The bundle's SHA-256 does NOT match: incomplete or tampered download"
            ok "Bundle verified (SHA-256)"
        else
            warn "No .sha256 next to the bundle: its integrity cannot be verified"
        fi
        WORK="/root/satom-builder-setup-${SETUP_VERSION}"
        rm -rf "$WORK"; mkdir -p "$WORK"; chmod 700 "$WORK"
        tar -xzf "$BUNDLE_FILE" -C "$WORK" || die "Could not extract $(basename "$BUNDLE_FILE")"
        ENGINE="$WORK/satom-builder-installer/$ENGINE_NAME"
        [ -f "$ENGINE" ] || die "The bundle does not contain satom-builder-installer/${ENGINE_NAME}"
        ok "Bundle extracted to $(dirname "$ENGINE")"
    else
        [ "$INTERNET" -eq 1 ] || die "Without Internet access an online install is not possible: use the offline bundle (SETUP_SOURCE=offline)"
        [ -n "$sibling" ] || die "${ENGINE_NAME} ${SETUP_VERSION} must sit next to this script (it ships with it in every release and offline bundle)"
        ENGINE="$sibling"
        ok "Online install with ${ENGINE}"
    fi
    bash -n "$ENGINE" || die "${ENGINE} is not a valid script"
}

# -- main ---------------------------------------------------------------------
echo "${c_b}SATOM Builder ${SETUP_VERSION} -- guided installer${c_0}   (log: ${LOG})"
ENGINE_ARGS=()
[ "$ASSUME_YES" -eq 1 ] && ENGINE_ARGS+=(--yes)
[ -n "$ANSWERS_FILE" ] && ENGINE_ARGS+=(--answers "$(cd "$(dirname "$ANSWERS_FILE")" && pwd)/$(basename "$ANSWERS_FILE")")
[ "$DRY_RUN" -eq 1 ] && ENGINE_ARGS+=(--dry-run)

if [ "$UNINSTALL" -eq 1 ]; then
    CURRENT_STEP="uninstall"
    [ -f "$SCRIPT_DIR/$ENGINE_NAME" ] || die "${ENGINE_NAME} must sit next to this script"
    UARGS=(--uninstall); [ "$PURGE" -eq 1 ] && UARGS+=(--purge); [ "$ASSUME_YES" -eq 1 ] && UARGS+=(--yes)
    exec bash "$SCRIPT_DIR/$ENGINE_NAME" "${UARGS[@]}"
fi

check_system
if [ "$CHECK_ONLY" -eq 1 ]; then
    if [ -f "$SCRIPT_DIR/$ENGINE_NAME" ]; then
        say "Installer preflight (nothing is modified)"
        bash "$SCRIPT_DIR/$ENGINE_NAME" --preflight || true
    fi
    say "Check only (--check): nothing has been installed."
    exit 0
fi
choose_source

CURRENT_STEP="3 . install-satom-builder.sh"
say "Step 3 . Running ${ENGINE_NAME} (log: /var/log/satom-builder-install.log)"
set +e
bash "$ENGINE" "${ENGINE_ARGS[@]}"
rc=$?
set -e
[ "$rc" -eq 0 ] || { CURRENT_STEP="3 . install-satom-builder.sh (rc=$rc)"; false; }
[ "$DRY_RUN" -eq 1 ] && { say "Dry run finished: nothing was changed."; exit 0; }

CURRENT_STEP="4 . summary"
SUMMARY="/root/satom-builder-setup-summary.txt"
{
    echo "SATOM Builder $(cat /opt/satom-builder/VERSION 2>/dev/null || echo "$SETUP_VERSION") installed -- $(date '+%F %T')"
    echo "  Source ......... ${SETUP_SOURCE}"
    echo "  Status ......... sudo satom-builder status"
    echo "  Install log .... /var/log/satom-builder-install.log"
    echo "  Setup log ...... ${LOG}"
    echo "  Uninstall ...... sudo bash $(basename "$0") --uninstall   (--purge deletes the data too)"
} | tee "$SUMMARY"
chmod 600 "$SUMMARY"
[ -n "$WORK" ] && info "The extracted bundle stays in ${WORK}; delete it when you no longer need it."
echo
echo "${c_g}${c_b}Installation finished.${c_0} Summary saved to ${SUMMARY}"
log_raw "END OK"
