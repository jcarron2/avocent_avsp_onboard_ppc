"""Feature: interactive shell on the MODEM port (/dev/ttyS1).

Deliberately does NOT do a raw kernel `console=ttyS1` boot-region patch.
That was tried (v6-v9 in the original project) and is still, as of
2026-08-18, an unresolved and proven-dangerous change -- it bricked one
of the two real test units (silent, no auto-recovery) and the exact
second failure mechanism (beyond the boot-region checksum, which was
found and fixed) was never fully identified. See STATE.md and
JTAG_FLASH_RECOVERY.md in the parent project for the full history if you
want to pursue that path anyway -- it is intentionally out of scope for
this tool.

What this DOES do -- the rootfs-only half of that same work, proven
live/working with zero boot-region involvement (v10 in the original
project, confirmed by a real user typing commands and getting real
output over the MODEM port):

1. /etc/auto_run: comment out `/sbin/avogetty_respawn &`. This is the
   supervisor that hands the MODEM port off to `avogetty`/`pppd` for
   dial-up. Confirmed via the port's own live rx-byte counter (0) that
   nothing has ever actually dialed in on a real deployment, so this
   loses no real functionality.
2. /etc/inittab: add `ttyS1::respawn:/bin/sh` -- BusyBox's own init
   natively supports naming a specific tty in inittab's first field to
   open it, make it the controlling terminal, and respawn a shell on it,
   the same mechanism already used for the local console's
   `::askfirst:-/bin/sh` line. No extra binary needed (this firmware has
   no getty/agetty/mgetty at all).

Net effect: plug a serial cable into the MODEM port and you get a real
root shell -- no kernel boot text (that needs the excluded boot-region
patch), but genuine interactive two-way access, which was the actual
practical goal.
"""

AUTO_RUN_MARKER = '/sbin/avogetty_respawn &'
INITTAB_LINE = "ttyS1::respawn:/bin/sh\n"


def describe():
    return ("Modem-port shell (ttyS1): disables avogetty/pppd dial-up handling "
            "and adds a real login shell on the MODEM port's serial line. "
            "Rootfs-only -- no boot-region patch, no bricking risk.")


def apply(tree_dir, log=print):
    auto_run_path = f'{tree_dir}/etc/auto_run'
    content = open(auto_run_path).read()
    if AUTO_RUN_MARKER not in content:
        raise RuntimeError(f"'{AUTO_RUN_MARKER}' not found in {auto_run_path} -- "
                            "unexpected rootfs layout, refusing to guess")
    if '# ' + AUTO_RUN_MARKER in content:
        log("[modem_console] avogetty_respawn already disabled -- leaving as-is")
    else:
        log("[modem_console] disabling avogetty_respawn (frees ttyS1 from dial-up handling)...")
        content = content.replace(
            AUTO_RUN_MARKER,
            '# ' + AUTO_RUN_MARKER + '  # disabled by release tool: ttyS1 used as a login shell instead'
        )
        with open(auto_run_path, 'w') as f:
            f.write(content)

    inittab_path = f'{tree_dir}/etc/inittab'
    inittab = open(inittab_path).read()
    if 'ttyS1' in inittab:
        log("[modem_console] inittab already has a ttyS1 entry -- leaving as-is")
    else:
        log("[modem_console] adding interactive shell on ttyS1 to inittab...")
        inittab = inittab.rstrip('\n') + '\n' + INITTAB_LINE
        with open(inittab_path, 'w') as f:
            f.write(inittab)
