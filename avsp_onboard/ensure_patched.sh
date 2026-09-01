#!/bin/sh
# Safety-checked wrapper: verify this is the exact kernel build the video-
# read crash fix was derived against (see STATE.md's 2026-08-12 "RESOLVED"
# entry / README.md Known bug 5), apply both live kernel-memory patches if
# so, then exec the real client. Kept deliberately separate from the main
# client binary -- /dev/kmem write access is a much bigger privilege than
# anything the client otherwise needs, so this wrapper is the only thing
# that ever touches it, keeping that exposure auditable and contained.
#
# Reapplying twice is harmless (each patch just sets fixed values, nothing
# increments/toggles), so this always reapplies rather than trying to
# detect "already patched" -- simpler and avoids needing any byte-compare
# tooling this device's minimal BusyBox doesn't have (no cmp/diff/od).
#
# Usage: sh ensure_patched.sh <same args as avsp_client_ppc>

# NOT using `set -e` here deliberately: every small static binary built with
# this project's toolchain (including kmem_patch itself) hits a real,
# already-documented, harmless cosmetic SIGILL on clean exit (README.md
# "Known bug 1") -- under `set -e`, the shell treats that signal-death exit
# status as a command failure and aborts the whole script after the very
# first kmem_patch call. Confirmed live: exactly this happened. Each patch
# call's actual effect is already independently byte-verified (see
# STATE.md), so skipping strict error-checking here is safe.

EXPECTED_VERSION="Linux version 2.4.20_405ep (dstafford@mayberry) (gcc version 3.3.1) #2 Wed Oct 28 16:58:01 CDT 2015"
ACTUAL_VERSION="$(cat /proc/version)"

if [ "$ACTUAL_VERSION" != "$EXPECTED_VERSION" ]; then
    echo "[ensure_patched] REFUSING to patch -- kernel version mismatch."
    echo "[ensure_patched] expected: $EXPECTED_VERSION"
    echo "[ensure_patched] actual:   $ACTUAL_VERSION"
    echo "[ensure_patched] These patch addresses were derived from a live"
    echo "[ensure_patched] disassembly of the EXPECTED kernel build only --"
    echo "[ensure_patched] applying them to a different build could corrupt"
    echo "[ensure_patched] unrelated kernel code. Not proceeding."
    exit 1
fi

KP=/tmp/kmem_patch
if [ ! -x "$KP" ]; then
    echo "[ensure_patched] $KP not found or not executable -- fetch it first"
    echo "[ensure_patched]   (tftp -g -r kmem_patch -l /tmp/kmem_patch <host> 6969 && chmod +x /tmp/kmem_patch)"
    exit 1
fi

echo "[ensure_patched] kernel version matches -- applying patches..."

# Patch 1/2: zone_table[3..255] -> zone_table[0]'s real value. Prevents the
# kernel NULL-pointer Oops outright; safety net only, no longer strictly
# required by patch 2 below, but harmless to keep applying.
$KP c02423e4 c020bd48 253

# Patch 2/2: replace skb_copy_datagram_iovec's broken zone-lookup address
# computation with a direct use of the already-correct raw address -- this
# is what actually makes real video data flow, not just avoids the crash.
$KP c014e90c 7d445378 1   # mr r4,r10       (was: lwz r4,0x9c(r9)   <- the crash)
$KP c014e910 60000000 1   # nop             (was: lwz r0,0xa0(r9))
$KP c014e914 60000000 1   # nop             (was: subf r4,r4,r10)
$KP c014e918 60000000 1   # nop             (was: srawi r4,r4,0x2)
$KP c014e91c 60000000 1   # nop             (was: mullw r4,r4,r8)
$KP c014e920 7fbfe850 1   # subf r29,r31,r29 -- KEEP: unrelated loop bookkeeping, must survive verbatim
$KP c014e924 60000000 1   # nop             (was: rlwinm r4,r4,0xc,0x0,0x13)
$KP c014e928 60000000 1   # nop             (was: add r4,r4,r0)
$KP c014e92c 60000000 1   # nop             (was: subis r4,r4,0x4000)

echo "[ensure_patched] patches applied. Starting client..."

# 2026-08-23: logging to /tmp/companion.log is done INSIDE avsp_client_ppc
# itself now (it tees its own stdout+stderr to the file while preserving the
# console -- see setup_self_logging() in avsp_client.c). This busybox (1.13.3)
# has no `tee` applet at all (verified live on the primary unit), so a
# shell-side tee was never viable here; doing it in C needs no applet and
# can't hang startup. So this is back to a plain exec.
exec /tmp/avsp_client_ppc "$@"
