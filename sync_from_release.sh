#!/bin/bash
# Regenerates release-ppcbin/ (the precompiled-binary-only sibling release)
# from ../release/ -- release/ is release-ppcbin's real canonical upstream.
# release-ppcbin never has its own copy of avsp_client_ppc's source or the
# build toolchain; it only ever carries whatever release/avsp_onboard/build.sh
# most recently produced there.
#
# Run automatically: release/avsp_onboard/build.sh invokes this itself on
# every successful build (see its own tail), so a normal
# replacement/client_poc/build.sh -> release/sync_avsp_onboard.sh ->
# release/avsp_onboard/build.sh chain also refreshes release-ppcbin/ with no
# extra step needed. Safe to also run by hand any time.
#
# Does NOT touch runme.sh or README.md here -- those are deliberately
# DIFFERENT from release/'s copies (no source-rebuild menu/section in this
# variant), hand-maintained, not generated.
set -euo pipefail

# --check: report drift without modifying anything, exit 1 if any found.
# Used by verify_ppcbin.sh; doesn't back up or copy in this mode.
CHECK_ONLY=0
if [ "${1:-}" = "--check" ]; then
    CHECK_ONLY=1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AVOCENT_ROOT="$(cd "$HERE/.." && pwd)"
RELEASE="$AVOCENT_ROOT/release"

# binaries + generic (non-source) support files -- byte-identical between
# release/avsp_onboard/ and release-ppcbin/avsp_onboard/ by design.
ONBOARD_FILES=(
    avsp_client_ppc
    conshell_ppc
    flash_bundle_read_ppc
    kmem_patch
    mtd_erase_only_ppc
    mtd_erase_write_ppc
    serbridge_ppc
    deploy_bundle.py
    uninstall.sh
    startup.sh
    startup.sh.template
    install.sh.template
    mini_tftpd.py
    watchdog.sh
    ensure_patched.sh
)

# top-level files identical between the two releases -- neither one ever
# modifies these, so a plain file-by-file copy is fine.
TOP_FILES=(avocent_fl_tool.py build_kvm_firmware.py cramfs_tool.py patch_cipher_list.py)
# whole dirs identical between the two releases -- compared/copied wholesale.
# NOT standalone_companion/ -- deliberately excluded, see 2026-08-31 note:
# it's a separate PC-side tool unrelated to the on-device binary bundle,
# out of scope for a minimal binary-only release.
TOP_DIRS=(features)

DEST="$HERE"
[ "$CHECK_ONLY" -eq 0 ] && mkdir -p "$DEST/avsp_onboard/backup"
CHANGED=()
UNCHANGED=0
MISSING=()

sync_file() {
    local src="$1" dst="$2"
    if [ ! -f "$src" ]; then
        MISSING+=("$(basename "$dst") (source $src missing)")
        return
    fi
    if [ -f "$dst" ] && cmp -s "$src" "$dst"; then
        UNCHANGED=$((UNCHANGED + 1))
        return
    fi
    if [ "$CHECK_ONLY" -eq 1 ]; then
        CHANGED+=("$(basename "$dst")")
        return
    fi
    if [ -f "$dst" ]; then
        cp "$dst" "$DEST/avsp_onboard/backup/$(basename "$dst").bak-$(date +%Y%m%d-%H%M%S)"
    fi
    cp -p "$src" "$dst"
    CHANGED+=("$(basename "$dst")")
}

for f in "${ONBOARD_FILES[@]}"; do
    sync_file "$RELEASE/avsp_onboard/$f" "$DEST/avsp_onboard/$f"
done
for f in "${TOP_FILES[@]}"; do
    sync_file "$RELEASE/$f" "$DEST/$f"
done

for d in "${TOP_DIRS[@]}"; do
    # exclude __pycache__/*.pyc -- non-deterministic bytecode written by
    # verify_release.sh's/verify_ppcbin.sh's own py_compile checks, not
    # real source; comparing it caused false "stale" reports (2026-08-31).
    if ! diff -rq --exclude=__pycache__ --exclude='*.pyc' "$RELEASE/$d" "$DEST/$d" >/dev/null 2>&1; then
        if [ "$CHECK_ONLY" -eq 1 ]; then
            CHANGED+=("$d/ (directory differs)")
        else
            rm -rf "$DEST/$d"
            cp -r "$RELEASE/$d" "$DEST/$d"
            find "$DEST/$d" -name "__pycache__" -exec rm -rf {} + 2>/dev/null
            CHANGED+=("$d/ (directory resynced)")
        fi
    else
        UNCHANGED=$((UNCHANGED + 1))
    fi
done

[ "$CHECK_ONLY" -eq 0 ] && chmod +x "$DEST"/avsp_onboard/*_ppc "$DEST/avsp_onboard/kmem_patch" "$DEST/avsp_onboard/uninstall.sh" 2>/dev/null || true

VERB="Updated"; [ "$CHECK_ONLY" -eq 1 ] && VERB="Stale (not modified, --check mode)"
echo "=================================================================="
echo "release-ppcbin/ sync from release/$([ "$CHECK_ONLY" -eq 1 ] && echo ' (--check)')"
echo "=================================================================="
echo "Unchanged (already in sync): $UNCHANGED"
if [ "${#CHANGED[@]}" -gt 0 ]; then
    echo "$VERB:"
    for f in "${CHANGED[@]}"; do echo "  + $f"; done
else
    echo "$VERB: none"
fi
if [ "${#MISSING[@]}" -gt 0 ]; then
    echo "WARNING -- source missing for:"
    for f in "${MISSING[@]}"; do echo "  ! $f"; done
fi
echo
if [ "${#CHANGED[@]}" -gt 0 ]; then
    if [ "$CHECK_ONLY" -eq 1 ]; then
        echo "Drift found -- run release-ppcbin/sync_from_release.sh (no --check) to fix."
        exit 1
    fi
    echo "Files changed -- release-ppcbin/ is now up to date with release/."
else
    echo "Nothing to do -- release-ppcbin/ already matches release/."
fi
