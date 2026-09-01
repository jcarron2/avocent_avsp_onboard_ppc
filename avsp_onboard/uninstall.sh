#!/bin/sh
# Run this ON THE DEVICE (same access pattern as everything else -- fired
# console workaround, or a direct connection to an already-open gateway
# session) to erase the avsp-onboard reserved flash region back to blank,
# undoing deploy_bundle.py's install. Leaves the device's flash exactly
# as it was before avsp-onboard was ever installed on it.
#
# This does NOT touch the firmware itself (rootfs/kernel) -- that's a
# separate concern, see release/README.md's "Undo" section: reverting a
# --modem-console or --java-certs firmware change just means reflashing
# the original stock .fl you built from, nothing on-device to clean up.
#
# Requires mtd_erase_only_ppc already present at /tmp/mtd_erase_only_ppc
# (tftp -g it from wherever this release's avsp_onboard/ directory is
# being served, e.g. `tftp -g -r mtd_erase_only_ppc -l /tmp/mtd_erase_only_ppc <your-pc-ip> 6969`).
#
# Does NOT remove /mnt/jffs/startup.sh or /mnt/jffs/flash_bundle_read --
# those are tiny and harmless if left behind (startup.sh just no-ops once
# flash_bundle_read finds nothing but 0xFF at the envelope offset). Remove
# them too if you want the device completely back to how it was:
#   rm -f /mnt/jffs/startup.sh /mnt/jffs/flash_bundle_read

ENVELOPE_OFFSET="${1:-900000}"
ENVELOPE_LENGTH="${2:-300000}"   # 3MB reserved envelope, see FLASH_LAYOUT.md

TOOL=/tmp/mtd_erase_only_ppc
if [ ! -x "$TOOL" ]; then
    echo "[uninstall] $TOOL missing -- fetch it first:"
    echo "[uninstall]   tftp -g -r mtd_erase_only_ppc -l /tmp/mtd_erase_only_ppc <your-pc-ip> 6969"
    echo "[uninstall]   chmod +x /tmp/mtd_erase_only_ppc"
    exit 1
fi

echo "[uninstall] killing any running avsp_client_ppc first..."
ps | grep avsp_client_ppc | grep -v grep | while read pid rest; do kill -9 "$pid"; done

echo "[uninstall] erasing reserved envelope 0x$ENVELOPE_OFFSET, length 0x$ENVELOPE_LENGTH..."
"$TOOL" /dev/mtd1 "$ENVELOPE_OFFSET" "$ENVELOPE_LENGTH"
echo "[uninstall] done -- the reserved region is blank again. avsp-onboard will"
echo "[uninstall] no-op on future boots (flash_bundle_read finds nothing to read)"
echo "[uninstall] until deploy_bundle.py is run again."
