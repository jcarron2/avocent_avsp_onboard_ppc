#!/bin/sh
# Persistent startup script for our companion KVM client, stored on the
# writable /mnt/jffs partition -- invoked by a small hook line added to
# /etc/auto_run (firmware v4+), 5s after main_app starts (gives main_app
# time to mount /mnt/jffs itself before we try to use it).
#
# 2026-08-24: now launches via watchdog.sh (supervisor) instead of
# ensure_patched.sh directly, so the companion daemon auto-recovers if it
# ever exits/crashes -- and so a boot picks up the same supervised model,
# not just a one-shot launch. watchdog.sh itself calls ensure_patched.sh
# (kernel patches) per relaunch. Empty TFTP host arg -> watchdog's
# re-fetch-missing-files path is skipped (files are already extracted from
# flash below); it still supervises + applies any explicitly-confirmed
# staged update.

FLASH_DEV=/dev/mtd1
FLASH_OFF=900000
BUNDLE=/tmp/flash_bundle.tar
READER=/mnt/jffs/flash_bundle_read

DSR_IP=192.168.2.99
ADMIN_USER=admin
TICKET=1AVCT-1189185892
TARGET_PORT=5
WS_PORT=8080

if [ ! -x "$READER" ]; then
    echo "[startup] $READER missing or not executable -- aborting"
    exit 1
fi

"$READER" "$FLASH_DEV" "$FLASH_OFF" "$BUNDLE"
if [ ! -s "$BUNDLE" ]; then
    echo "[startup] failed to read bundle from flash -- aborting"
    exit 1
fi

tar -xf "$BUNDLE" -C /tmp
chmod +x /tmp/avsp_client_ppc /tmp/mtd_erase_write_ppc /tmp/kmem_patch 2>/dev/null

if [ ! -x /tmp/avsp_client_ppc ] || [ ! -x /tmp/kmem_patch ]; then
    echo "[startup] required files missing after extract -- aborting"
    exit 1
fi

echo "[startup] launching via watchdog.sh (supervised)"
sh /tmp/watchdog.sh "$DSR_IP" "$ADMIN_USER" "$TICKET" "$TARGET_PORT" "$WS_PORT" &
