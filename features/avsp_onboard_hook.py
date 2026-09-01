"""Feature: avsp-onboard (rootfs half).

The actual companion daemon (avsp_client_ppc), its kernel-patch helper,
and the watchdog that keeps it running all live in a reserved blank
region of flash (mtd1 @ 0x900000, see FLASH_LAYOUT.md) and get written
there in a *separate* step after this firmware is flashed and booted --
see release/avsp_onboard/deploy_bundle.py. That two-step split is
deliberate: the reserved region sits outside what a normal firmware
update touches, which is what lets the companion daemon survive future
firmware updates without needing to be baked into every .fl.

This feature only adds the one rootfs-side hook that makes the whole
thing self-starting on every boot:

  /etc/auto_run gains one line -- 5s after main_app starts, check for and
  run /mnt/jffs/startup.sh if present. Everything that actually happens
  at boot lives in that script, on the writable JFFS2 partition, not in
  the firmware -- so future behavior changes (a new target port, a new
  companion binary) never need another firmware flash, just a new
  startup.sh pushed via deploy_bundle.py's printed instructions.

Safe to select even if you don't run deploy_bundle.py right away: with
no /mnt/jffs/startup.sh present yet, the hook is a silent no-op.
"""

HOOK = "\n(sleep 5; if [ -e /mnt/jffs/startup.sh ]; then sh /mnt/jffs/startup.sh & fi) &\n"


def describe():
    return ("AVSP companion daemon support: adds the boot-time hook that runs "
            "/mnt/jffs/startup.sh if present. Pair with "
            "avsp_onboard/deploy_bundle.py after flashing to actually install "
            "the companion daemon onto the reserved flash region.")


def apply(tree_dir, log=print):
    auto_run_path = f'{tree_dir}/etc/auto_run'
    content = open(auto_run_path).read()
    if 'startup.sh' in content:
        log("[avsp_onboard_hook] auto_run already has the startup.sh hook -- leaving as-is")
        return
    log("[avsp_onboard_hook] adding startup.sh boot hook to auto_run...")
    with open(auto_run_path, 'a') as f:
        f.write(HOOK)
