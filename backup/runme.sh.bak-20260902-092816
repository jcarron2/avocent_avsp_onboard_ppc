#!/bin/bash
# runme.sh -- interactive menu for the DSR2030 firmware customizer.
# This is the release-ppcbin variant: avsp_client_ppc and the on-device
# flash utils ship as PRECOMPILED PowerPC binaries only -- no source, no
# cross-compiler, no mbedTLS libs in this directory at all. Everything
# that doesn't touch those binaries (firmware feature builds, bundle
# deploy, undo) works identically to the full release/ package. If you
# want to rebuild avsp_client_ppc from source, use the full release/
# package instead (or the parent project's replacement/client_poc/).
# No cryptic flags to remember: pick a firmware file, pick features,
# answer a few prompts, review the exact command(s) about to run, confirm,
# done. See README.md for what each feature actually does under the hood.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# Reference checksum for this project's own known-good stock 3.7.2.8 image
# (not a vendor-published hash -- just what this project has on file from
# the copy everything here was built and tested against). Purely
# informational: the real safety gate is avocent_fl_tool.py verify's own
# structural/checksum check, which works on ANY valid stock .fl, not just
# this specific one.
KNOWN_STOCK_MD5="E6A1739BE9941F441877057D231C5F9E"
KNOWN_STOCK_NAME="dsr_x030_3.7.2.8.fl"
KNOWN_STOCK_SIZE="8888568"

# ---------------------------------------------------------------- state ----
FW_PATH=""
JDK_BIN=""

# -------------------------------------------------------------- helpers ----
pause() { read -r -p "Press Enter to continue... " _; }

confirm() {
    # confirm "question" -> 0 (yes) or 1 (no). Default no.
    local ans
    read -r -p "$1 [y/N] " ans
    [[ "$ans" =~ ^[Yy] ]]
}

have() { command -v "$1" >/dev/null 2>&1; }

find_jdk_bin() {
    # Prefer PATH; fall back to the parent project's portable JDK if this
    # happens to be run from inside that checkout.
    if have keytool && have jarsigner && have jar; then
        JDK_BIN=""   # empty means "just use PATH"
        return 0
    fi
    for candidate in "$HERE/../tools/jdk17/bin" "$HERE/../tools/jdk21/bin"; do
        if [ -x "$candidate/keytool" ] && [ -x "$candidate/jarsigner" ] && [ -x "$candidate/jar" ]; then
            JDK_BIN="$candidate"
            return 0
        fi
    done
    return 1
}

# ---------------------------------------------------------- dep checking ----
check_deps() {
    echo "=================================================================="
    echo " Dependency check"
    echo "=================================================================="
    local ok=1

    if have python3; then
        local pyver
        pyver="$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
        echo "  [OK]   python3 ($pyver)"
    else
        echo "  [MISS] python3 -- required for everything. Install it and re-run."
        ok=0
    fi

    if have openssl; then
        echo "  [OK]   openssl ($(openssl version))"
    else
        echo "  [MISS] openssl -- needed for the java-certs feature only."
    fi

    if find_jdk_bin; then
        if [ -n "$JDK_BIN" ]; then
            echo "  [OK]   keytool/jarsigner/jar (using $JDK_BIN)"
        else
            echo "  [OK]   keytool/jarsigner/jar (on PATH)"
        fi
    else
        echo "  [MISS] keytool/jarsigner/jar -- needed for the java-certs feature only."
        echo "         Install a JDK 8+, or point this script at one when asked."
    fi

    echo "  [--]   PowerPC cross-compiler / mbedTLS -- not needed by this release."
    echo "         release-ppcbin ships avsp_client_ppc and the flash utils as"
    echo "         precompiled binaries only, no source or toolchain included."
    echo "         Want to rebuild from source instead? Use the full release/"
    echo "         package (or replacement/client_poc/ in the parent project)."

    for f in avocent_fl_tool.py cramfs_tool.py patch_cipher_list.py build_kvm_firmware.py \
             features/modem_console.py features/avsp_onboard_hook.py features/java_certs.py \
             avsp_onboard/deploy_bundle.py avsp_onboard/avsp_client_ppc \
             avsp_onboard/mini_tftpd.py avsp_onboard/install.sh.template; do
        if [ -e "$HERE/$f" ]; then
            echo "  [OK]   $f"
        else
            echo "  [MISS] $f -- this release directory looks incomplete!"
            ok=0
        fi
    done

    echo "=================================================================="
    if [ "$ok" -eq 0 ]; then
        echo "One or more REQUIRED things are missing -- fix those above before continuing."
        exit 1
    fi
    echo "Ready."
    echo
}

# ------------------------------------------------------------ firmware ----
list_fl_candidates() {
    find "$HERE" -maxdepth 1 -iname "*.fl" 2>/dev/null | sort
}

open_firmware() {
    echo "=================================================================="
    echo " Open firmware file"
    echo "=================================================================="
    local candidates=()
    while IFS= read -r line; do [ -n "$line" ] && candidates+=("$line"); done < <(list_fl_candidates)

    if [ "${#candidates[@]}" -gt 0 ]; then
        echo "Found in $HERE:"
        local i=1
        for c in "${candidates[@]}"; do
            echo "  $i) $(basename "$c")  ($(stat -c%s "$c" 2>/dev/null || echo '?') bytes)"
            i=$((i + 1))
        done
        echo "  0) enter a different path"
        read -r -p "Pick a file [0-${#candidates[@]}]: " pick
        if [[ "$pick" =~ ^[0-9]+$ ]] && [ "$pick" -ge 1 ] && [ "$pick" -le "${#candidates[@]}" ]; then
            FW_PATH="${candidates[$((pick - 1))]}"
        else
            read -r -p "Path to a stock .fl firmware image: " FW_PATH
        fi
    else
        echo "No .fl files found in $HERE."
        read -r -p "Path to a stock .fl firmware image: " FW_PATH
    fi

    if [ ! -f "$FW_PATH" ]; then
        echo "ERROR: $FW_PATH not found."
        FW_PATH=""
        return 1
    fi

    echo
    echo "--- verifying $FW_PATH ---"
    python3 "$HERE/avocent_fl_tool.py" verify "$FW_PATH"
    local rc=$?
    echo "---"

    local md5
    md5="$(md5sum "$FW_PATH" | awk '{print toupper($1)}')"
    local size
    size="$(stat -c%s "$FW_PATH")"
    echo "MD5:  $md5"
    echo "Size: $size bytes"
    if [ "$md5" = "$KNOWN_STOCK_MD5" ] && [ "$size" = "$KNOWN_STOCK_SIZE" ]; then
        echo "Matches this project's own reference copy of $KNOWN_STOCK_NAME."
    else
        echo "(Doesn't match this project's reference copy of $KNOWN_STOCK_NAME --"
        echo " that's fine if this is a different unit/version; the structural"
        echo " verify() result above is the real gate, not this comparison.)"
    fi

    if [ "$rc" -ne 0 ]; then
        echo
        echo "REFUSING to proceed: this file failed verification above."
        echo "Only a stock (or previously verify()-clean) .fl is safe to modify."
        FW_PATH=""
        return 1
    fi
    echo
    echo "[+] $FW_PATH opened and verified clean."
    pause
}

# ------------------------------------------------------------- features ----
build_firmware_menu() {
    if [ -z "$FW_PATH" ]; then
        echo "Open a firmware file first (menu option 1)."
        pause
        return
    fi

    echo "=================================================================="
    echo " Build modified firmware from: $(basename "$FW_PATH")"
    echo "=================================================================="
    echo "Answer for each feature. You can pick any combination, including none."
    echo

    local -a args=()
    local -a name_tags=()

    echo "--- modem-console ---"
    echo "Interactive root shell on the physical MODEM port (/dev/ttyS1)."
    echo "Rootfs-only, no boot-region patch -- proven safe. No unauthenticated"
    echo "network access is opened by this (see README.md's telnet section)."
    echo "For network-reachable root shell access instead, see conshell"
    echo "(companion daemon settings page) -- real telnetd can't work on this"
    echo "firmware at all (/dev/ptmx missing); conshell is the working answer,"
    echo "and a ttyS1-network-bridge was considered and deprecated."
    if confirm "Enable modem-console?"; then
        args+=(--modem-console)
        name_tags+=(modemconsole)
    fi
    echo

    echo "--- avsp-onboard ---"
    echo "Adds the boot-time hook that lets the AVSP companion daemon start"
    echo "itself automatically. This only touches the firmware image -- the"
    echo "companion daemon binaries themselves are installed in a SEPARATE"
    echo "step against the running device afterward (menu option 3), not"
    echo "handled here."
    if confirm "Enable avsp-onboard hook?"; then
        args+=(--avsp-onboard)
        name_tags+=(avsponboard)
    fi
    echo

    echo "--- java-certs ---"
    echo "Fresh self-signed HTTPS admin + AVSP video certs, fresh code-signing"
    echo "key, all 12 Web Start jars re-signed, dead TLS ciphers replaced."
    echo "Only works with a Java 8 client -- see README.md for why."
    if confirm "Enable java-certs?"; then
        args+=(--java-certs)
        name_tags+=(javacerts)
        local cert_ip
        read -r -p "  IP address for the HTTPS cert's SAN (e.g. 192.168.2.99): " cert_ip
        if [ -z "$cert_ip" ]; then
            echo "  No IP given -- skipping java-certs."
            args=("${args[@]/--java-certs}")
            name_tags=("${name_tags[@]/javacerts}")
        else
            args+=(--cert-ip "$cert_ip")
        fi
        if ! find_jdk_bin; then
            echo "  No JDK found automatically."
            read -r -p "  Path to a JDK's bin/ directory (keytool/jarsigner/jar): " JDK_BIN
        fi
        if [ -n "$JDK_BIN" ]; then
            args+=(--jdk-bin "$JDK_BIN")
        fi
    fi
    echo

    if [ "${#args[@]}" -eq 0 ]; then
        echo "No features selected -- nothing to build."
        pause
        return
    fi

    local stem tag out_name
    stem="$(basename "$FW_PATH")"
    stem="${stem%.fl}"
    tag="$(IFS=-; echo "${name_tags[*]}")"
    out_name="${stem}-with-${tag}.fl"
    read -r -p "Output filename [$out_name]: " custom_name
    [ -n "$custom_name" ] && out_name="$custom_name"

    echo
    echo "=================================================================="
    echo " About to run:"
    echo "=================================================================="
    printf '  python3 %q %q -o %q' "$HERE/build_kvm_firmware.py" "$FW_PATH" "$HERE/$out_name"
    for a in "${args[@]}"; do printf ' %q' "$a"; done
    printf '\n'
    echo "=================================================================="
    if ! confirm "Run this now?"; then
        echo "Cancelled."
        pause
        return
    fi

    python3 "$HERE/build_kvm_firmware.py" "$FW_PATH" -o "$HERE/$out_name" "${args[@]}"
    local rc=$?
    echo
    if [ "$rc" -eq 0 ]; then
        echo "[+] Done: $HERE/$out_name"
        echo "    Your original ($FW_PATH) is untouched -- to undo, just reflash it."
    else
        echo "[!] Build failed (exit $rc) -- see output above."
    fi
    pause
}

# --------------------------------------------------------- avsp bundle ----
avsp_bundle_menu() {
    echo "=================================================================="
    echo " Build the avsp-onboard companion daemon bundle (local files only)"
    echo "=================================================================="
    echo "This builds flash_bundle_envelope.bin + a filled-in startup.sh on"
    echo "THIS machine -- it does NOT touch any device. Actually installing"
    echo "them onto a running KVM is a separate, manual, human-directed step"
    echo "(this project keeps live device access deliberately manual -- see"
    echo "README.md's \"Deploying to a live device\" section for the exact"
    echo "commands to run yourself once you're ready)."
    echo
    echo "Requires the firmware you flash to this device to already have the"
    echo "avsp-onboard feature built in (menu option 2)."
    echo

    local ip target_port ws_port admin_user offset tftp_port outdir
    read -r -p "Device IP: " ip
    read -r -p "Default KVM target port to auto-connect to on boot [1] (see admin UI's target list): " target_port
    target_port="${target_port:-1}"
    read -r -p "WebSocket port [8080]: " ws_port
    ws_port="${ws_port:-8080}"
    read -r -p "TFTP port to serve/fetch on [6969]: " tftp_port
    tftp_port="${tftp_port:-6969}"
    # 2026-09-01: no password prompt here on purpose -- the daemon never
    # takes real admin credentials from this tool at all, it always reads
    # them from /mnt/jffs/kvm_creds on the device itself. The echo lines
    # below are the live explanation (not just a source comment) so
    # "real login is set on-device" isn't a dangling reference to nothing
    # in someone's actual terminal session -- this used to say "see above"
    # while nothing above it had actually explained anything.
    echo "Note: this does NOT set the appliance's real login password. The"
    echo "daemon always reads real admin credentials from /mnt/jffs/kvm_creds"
    echo "on the device itself -- set them from the companion daemon's own"
    echo "settings page (\"KVM appliance login\" section) once it's running;"
    echo "falls back to the factory admin/admin default until then. The"
    echo "value below is just a fallback AVSP-session username, not a real"
    echo "login step -- that's why there's no password prompt."
    read -r -p "Admin username [admin]: " admin_user
    admin_user="${admin_user:-admin}"
    read -r -p "Reserved flash offset (hex) [900000]: " offset
    offset="${offset:-900000}"

    if [ -z "$ip" ] || [ -z "$target_port" ]; then
        echo "IP and target port are both required."
        pause
        return
    fi

    outdir="$HERE/avsp_onboard/deploy_out"

    echo
    echo "=================================================================="
    echo " About to run:"
    echo "=================================================================="
    printf '  python3 %q %q %q --ws-port %q --admin-user %q --offset %q --outdir %q --tftp-port %q\n' \
        "$HERE/avsp_onboard/deploy_bundle.py" "$ip" "$target_port" "$ws_port" "$admin_user" "$offset" "$outdir" "$tftp_port"
    echo "=================================================================="
    if ! confirm "Run this now?"; then
        echo "Cancelled."
        pause
        return
    fi

    python3 "$HERE/avsp_onboard/deploy_bundle.py" "$ip" "$target_port" \
        --ws-port "$ws_port" --admin-user "$admin_user" --offset "$offset" \
        --outdir "$outdir" --tftp-port "$tftp_port"
    if [ $? -ne 0 ]; then
        pause
        return
    fi

    echo
    if confirm "Start a TFTP server now, serving $outdir on port $tftp_port?"; then
        echo
        echo "Serving -- on the device's root shell, run:"
        echo "  cd /tmp"
        echo "  tftp -g -r install.sh -l install.sh <your-pc-ip> $tftp_port"
        echo "  sh install.sh"
        echo
        echo "Ctrl+C here once install.sh finishes on the device."
        echo
        python3 "$HERE/avsp_onboard/mini_tftpd.py" "$outdir" "$tftp_port"
    fi
    pause
}

# ----------------------------------------------------------- main menu ----
main_menu() {
    while true; do
        echo "=================================================================="
        echo " DSR2030 firmware customizer (release-ppcbin -- binaries only)"
        [ -n "$FW_PATH" ] && echo " Current firmware: $(basename "$FW_PATH")"
        echo "=================================================================="
        echo "  1) Open firmware file"
        echo "  2) Build modified firmware (features -> new .fl)"
        echo "  3) Build avsp-onboard companion bundle (local files only)"
        echo "  4) Re-run dependency check"
        echo "  0) Exit"
        read -r -p "> " choice
        case "$choice" in
            1) open_firmware ;;
            2) build_firmware_menu ;;
            3) avsp_bundle_menu ;;
            4) check_deps; pause ;;
            0) exit 0 ;;
            *) echo "Unknown choice." ;;
        esac
        echo
    done
}

check_deps
main_menu
