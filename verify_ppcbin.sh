#!/bin/bash
# Verifies release-ppcbin/ (the binary-only sibling release) is in good
# shape: in sync with release/ (its real canonical upstream), all expected
# files present, genuinely binary-only (no leftover source/toolchain), and
# every script syntax-clean. There's no "real build" check here like
# release/verify_release.sh has -- this release has no source to build.
# Exit 0 = all good, exit 1 = at least one real problem (see [FAIL] lines).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

FAILS=0
pass() { echo "  [PASS] $1"; }
fail() { echo "  [FAIL] $1"; FAILS=$((FAILS + 1)); }

echo "=================================================================="
echo " release-ppcbin/ verification (binary-only release)"
echo "=================================================================="

# ---- 1. sync vs release/ ----
echo
echo "--- sync vs release/ (canonical upstream for this variant) ---"
if [ -d "$HERE/../release" ]; then
    if ./sync_from_release.sh --check > /tmp/verify_ppcbin_sync.$$ 2>&1; then
        pass "release-ppcbin/ matches release/"
    else
        fail "release-ppcbin/ is stale -- run release-ppcbin/sync_from_release.sh"
        sed 's/^/         /' /tmp/verify_ppcbin_sync.$$
    fi
    rm -f /tmp/verify_ppcbin_sync.$$
else
    echo "  [SKIP] not working inside the full project tree -- nothing to compare against"
fi

# ---- 2. required files present ----
echo
echo "--- required files ---"
for f in avsp_onboard/avsp_client_ppc avsp_onboard/conshell_ppc avsp_onboard/kmem_patch \
         avsp_onboard/flash_bundle_read_ppc avsp_onboard/mtd_erase_only_ppc avsp_onboard/mtd_erase_write_ppc \
         avsp_onboard/serbridge_ppc avsp_onboard/deploy_bundle.py avsp_onboard/uninstall.sh \
         avsp_onboard/startup.sh.template avsp_onboard/watchdog.sh avsp_onboard/ensure_patched.sh \
         runme.sh README.md build_kvm_firmware.py avocent_fl_tool.py; do
    if [ -e "$HERE/$f" ]; then pass "$f present"; else fail "$f MISSING"; fi
done

# ---- 3. genuinely binary-only -- no source or toolchain should ever land here ----
echo
echo "--- binary-only invariant ---"
LEFTOVER_SRC="$(find "$HERE/avsp_onboard" -maxdepth 1 \( -name "*.c" -o -name "*.h" \) 2>/dev/null)"
if [ -z "$LEFTOVER_SRC" ]; then
    pass "no .c/.h source files under avsp_onboard/ (binary-only, as intended)"
else
    fail "source files present -- this release is supposed to be binary-only: $LEFTOVER_SRC"
fi
if [ -e "$HERE/avsp_onboard/build.sh" ]; then
    fail "avsp_onboard/build.sh present -- there's no source here to build, this shouldn't exist"
else
    pass "no build.sh (correct -- nothing to build in this release)"
fi
if [ -d "$HERE/tools" ]; then
    fail "tools/ (vendored toolchain) present -- shouldn't be, this release ships binaries only"
else
    pass "no vendored toolchain under tools/ (correct for binary-only release)"
fi
if [ -d "$HERE/standalone_companion" ]; then
    fail "standalone_companion/ present -- deliberately excluded from this release (separate PC-side tool, out of scope)"
else
    pass "no standalone_companion/ (correctly excluded)"
fi

# ---- 4. script syntax ----
echo
echo "--- script syntax ---"
while IFS= read -r f; do
    if bash -n "$f" 2>/tmp/verify_ppcbin_syn.$$; then
        pass "$f"
    else
        fail "$f -- $(cat /tmp/verify_ppcbin_syn.$$ | head -1)"
    fi
    rm -f /tmp/verify_ppcbin_syn.$$
done < <(find "$HERE" -name "*.sh" -not -path "*/backup/*")

while IFS= read -r f; do
    if python3 -m py_compile "$f" 2>/tmp/verify_ppcbin_pyc.$$; then
        pass "$f"
    else
        fail "$f -- $(cat /tmp/verify_ppcbin_pyc.$$ | tail -1)"
    fi
    rm -f /tmp/verify_ppcbin_pyc.$$
done < <(find "$HERE" -name "*.py" -not -path "*/__pycache__/*")

# ---- summary ----
echo
echo "=================================================================="
if [ "$FAILS" -eq 0 ]; then
    echo "ALL CHECKS PASSED"
else
    echo "$FAILS CHECK(S) FAILED -- see [FAIL] lines above"
fi
echo "=================================================================="
exit "$([ "$FAILS" -eq 0 ] && echo 0 || echo 1)"
