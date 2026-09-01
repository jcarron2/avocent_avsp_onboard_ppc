#!/bin/sh
# watchdog.sh <ip> <user> <ticket> <target_port> [ws_port]
#
# Keeps the live KVM session running: relaunches it via ensure_patched.sh
# (which reapplies the volatile kernel patches every time -- harmless if
# they're already in place, necessary if this is actually a fresh boot)
# any time the session process exits, for any reason.
#
# Does NOT survive a real appliance reboot. Nothing on this device
# persists across one (by design, this project never writes to flash) --
# if the whole appliance reboots, this script's own process goes down
# with it and the dev machine has to redeploy from scratch, same as
# every other file here.
#
# Circuit breaker: if the session dies within 10s of starting three times
# in a row, stop instead of respawning forever. A quick death almost
# always means a bad/reused ticket (login rejected immediately) or the
# WS port still being held by a stale process -- and this project has
# already seen live evidence that hammering main_app with rapid failed
# logins can itself destabilize the appliance, so a tight respawn loop
# on a doomed ticket is actively dangerous, not just useless.

# 2026-08-12: real failure mode found live -- /tmp/avsp_client_ppc,
# ensure_patched.sh, kmem_patch, and even this script's own launcher's
# redirected log all vanished from /tmp mid-run with no appliance reboot
# (uptime kept climbing the whole time, no fresh-boot console banner,
# not an OOM kill -- this kernel has the OOM killer disabled and memory
# was ~40% free when checked). Root cause not pinned down, but whatever
# it is, self-healing is cheap and makes the watchdog robust to it
# either way: verify the supporting files still exist before every
# respawn and re-fetch any that are missing.
TFTP_HOST="$6"
TFTP_PORT="${7:-6969}"

# 2026-08-19: applies a staged update-from-server build (avsp_client_ppc's
# /update-do handler only ever fetches/verifies/stages into
# /tmp/update_staging/, never touches flash or live files itself) before
# this iteration's relaunch. Only these three named files are ever copied
# over their live counterparts -- watchdog.sh is deliberately never one
# of them, even though a staged copy of it exists too (same tar format
# used everywhere, on purpose) -- overwriting this script's own file on
# disk mid-execution is genuinely undefined-ish in BusyBox ash. To update
# watchdog.sh itself, still needs the full manual redeploy.
# 2026-08-23: now gated on TWO markers, not one. /tmp/update_ready means
# "fetched + verified + staged" (set by avsp_client_ppc's /update-do). That
# alone is NOT enough to apply -- it will sit here inert through any number
# of unrelated restarts (crash, kill, ticket expiry). Only an explicit
# Apply-button click (avsp_client_ppc's /update-apply route) creates
# /tmp/update_apply_confirmed, and only then does this actually install.
# Rationale: if the daemon ever restarts for an unknown reason, that's the
# worst moment to also silently swap the binary underneath it -- you want
# to investigate a fresh crash on the known-good build, not add a variable.
apply_staged_update_if_ready() {
    [ -f /tmp/update_ready ] || return 0
    if [ ! -f /tmp/update_apply_confirmed ]; then
        echo "[watchdog] staged update present but NOT apply-confirmed -- leaving it untouched (explicit Apply required)"
        return 0
    fi
    echo "[watchdog] staged update + explicit apply-confirm -- applying..."
    cp /tmp/update_staging/extracted/avsp_client_ppc /tmp/avsp_client_ppc
    cp /tmp/update_staging/extracted/kmem_patch /tmp/kmem_patch
    cp /tmp/update_staging/extracted/ensure_patched.sh /tmp/ensure_patched.sh
    chmod +x /tmp/avsp_client_ppc /tmp/kmem_patch
    /tmp/mtd_erase_write_ppc /dev/mtd1 900000 /tmp/update_staging/envelope.bin
    rm -f /tmp/update_ready /tmp/update_apply_confirmed
    echo "[watchdog] update applied"
}

ensure_file() {
    f="$1"
    if [ ! -f "$f" ]; then
        echo "[watchdog] $f missing -- re-fetching from $TFTP_HOST:$TFTP_PORT"
        # 2026-08-14: this busybox has no `basename` binary -- $(basename
        # "$f") silently evaluated to an empty string, so the tftp fetch
        # requested filename "" and failed every time. ${f##*/} is a
        # POSIX shell parameter expansion (strip longest */ prefix),
        # built into ash itself, no external command needed.
        tftp -g -r "${f##*/}" -l "$f" "$TFTP_HOST" "$TFTP_PORT"
        chmod +x "$f" 2>/dev/null
    fi
}

IP="$1"
ADMIN_USER="$2"
TICKET="$3"
PORT="$4"
WS_PORT="${5:-8080}"

FAILCOUNT=0
while true; do
    apply_staged_update_if_ready
    if [ -n "$TFTP_HOST" ]; then
        ensure_file /tmp/avsp_client_ppc
        ensure_file /tmp/ensure_patched.sh
        ensure_file /tmp/kmem_patch
    fi
    read UPRAW _ < /proc/uptime
    START="${UPRAW%.*}"
    echo "[watchdog] starting session (uptime=${START}s)..."
    sh /tmp/ensure_patched.sh "$IP" "$ADMIN_USER" "$TICKET" "$PORT" live 0 "$WS_PORT"
    read UPRAW _ < /proc/uptime
    END="${UPRAW%.*}"
    ELAPSED=$((END - START))
    echo "[watchdog] session ended after ${ELAPSED}s"

    if [ "$ELAPSED" -lt 10 ]; then
        FAILCOUNT=$((FAILCOUNT + 1))
        echo "[watchdog] quick-fail #$FAILCOUNT (session lasted <10s -- likely a bad ticket or a port still in use, not a real crash)"
        if [ "$FAILCOUNT" -ge 3 ]; then
            echo "[watchdog] 3 quick-fails in a row -- stopping instead of hammering main_app. Needs a fresh ticket and a manual restart."
            exit 1
        fi
        sleep 5
    else
        FAILCOUNT=0
        sleep 2
    fi
done
