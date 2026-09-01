#!/usr/bin/env python3
"""Build the avsp-onboard flash bundle (tar of on-device binaries, wrapped
in a 4-byte big-endian length header), the filled-in startup.sh, and a
one-shot install.sh for one target device -- everything needed to install
lands together in --outdir, ready to serve from a single TFTP root.

This step is deliberately NOT baked into the firmware .fl by
build_kvm_firmware.py. The bundle lives in a reserved, otherwise-blank
region of mtd1 (see FLASH_LAYOUT.md) specifically because a normal
firmware flash only ever touches the actual rootfs/kernel bytes, not the
whole physical partition -- writing the bundle separately, after the new
firmware is already running, is what lets it survive future firmware
updates. Baking it into the .fl itself would lose that property (and
isn't known to even work -- never tested).

Usage:
  deploy_bundle.py <dsr-ip> <target-port> [--ws-port 8080] [--admin-user admin]
                    [--outdir DIR] [--offset 900000]
                    [--tftp-host <your-pc-ip>] [--tftp-port 6969]

--tftp-host defaults to this machine's auto-detected outbound IP toward
<dsr-ip> -- override it if that guess is wrong (multiple NICs, NAT, etc).
It's baked into the generated install.sh, not asked for again on-device.

<target-port> is the numeric AVSP port of the DEFAULT target this KVM
should auto-connect to on boot -- not a session ticket, a stable
per-target number you only need to look up once. Find it in the DSR2030
admin web UI's target/adaptor list (https://<dsr-ip>/), or, if you have
it, standalone_companion/get_ticket.py <ip> reads the same list for you.

If this target is offline (or none was ever configured) when the daemon
starts, it's not fatal: the companion daemon still starts its web UI, you
just land on the settings/viewer page with nothing auto-connected instead
of a live session, and can pick an online target from there.

Requires the "modem console" feature (or otherwise a `startup.sh` boot
hook in /etc/auto_run) to already be present in the flashed firmware --
that's what actually calls /mnt/jffs/startup.sh on every boot. See
build_kvm_firmware.py.
"""
import argparse
import hashlib
import os
import shutil
import socket
import struct
import sys
import tarfile

HERE = os.path.dirname(os.path.abspath(__file__))

BUNDLE_FILES = [
    ("avsp_client_ppc", "avsp_client_ppc"),
    ("kmem_patch", "kmem_patch"),
    ("ensure_patched.sh", "ensure_patched.sh"),
    ("watchdog.sh", "watchdog.sh"),
    # 2026-08-19: watchdog.sh's update-from-server apply step needs this on
    # hand to write a freshly-staged bundle to flash itself -- used to be
    # absent from the default bundle.
    ("mtd_erase_write_ppc", "mtd_erase_write_ppc"),
]


def build_envelope(outdir):
    tar_path = os.path.join(outdir, "flash_bundle.tar")
    envelope_path = os.path.join(outdir, "flash_bundle_envelope.bin")

    missing = [f for f, _ in BUNDLE_FILES if not os.path.exists(os.path.join(HERE, f))]
    if missing:
        print("ERROR: missing files in avsp_onboard/:", file=sys.stderr)
        for m in missing:
            print(f"  {m}", file=sys.stderr)
        sys.exit(1)

    with tarfile.open(tar_path, "w") as tf:
        for src, arcname in BUNDLE_FILES:
            path = os.path.join(HERE, src)
            tf.add(path, arcname=arcname)
            print(f"  + {arcname} ({os.path.getsize(path)} bytes)")

    tar_bytes = open(tar_path, "rb").read()
    # 2026-08-19: "AVCB" magic + length (was a bare length) -- lets
    # mtd_erase_write_ppc's pre-write check tell blank / our-own-bundle /
    # something-else apart before ever erasing. flash_bundle_read_ppc
    # auto-detects the legacy (bare-length) format too, so this is a safe
    # drop-in even before an already-installed legacy bundle is migrated.
    header = b"AVCB" + struct.pack(">I", len(tar_bytes))
    with open(envelope_path, "wb") as f:
        f.write(header + tar_bytes)

    sha256 = hashlib.sha256(open(envelope_path, "rb").read()).hexdigest()
    sha_path = envelope_path + ".sha256"
    with open(sha_path, "w") as f:
        f.write(sha256 + "\n")
    print(f"sha256:   {sha_path} ({sha256})")

    print(f"\ntar:      {tar_path} ({len(tar_bytes)} bytes)")
    print(f"envelope: {envelope_path} ({len(tar_bytes) + 4} bytes)")
    return envelope_path


def guess_local_ip(remote_ip):
    """Best-effort local outbound IP toward remote_ip, for defaulting
    --tftp-host -- no packets actually sent (UDP connect() just picks a
    route/source address), so this is safe even if remote_ip is offline."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((remote_ip, 1))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def render_install_sh(outdir, tftp_host, tftp_port, offset_hex):
    tmpl = open(os.path.join(HERE, "install.sh.template")).read()
    rendered = (tmpl
                .replace("{{TFTP_HOST}}", tftp_host)
                .replace("{{TFTP_PORT}}", str(tftp_port))
                .replace("{{OFFSET}}", offset_hex))
    out_path = os.path.join(outdir, "install.sh")
    with open(out_path, "w", newline="\n") as f:
        f.write(rendered)
    os.chmod(out_path, 0o755)
    print(f"wrote {out_path}")
    return out_path


def render_startup_sh(outdir, ip, target_port, ws_port, admin_user, offset_hex):
    tmpl = open(os.path.join(HERE, "startup.sh.template")).read()
    rendered = (tmpl
                .replace("{{BUNDLE_OFFSET}}", offset_hex)
                .replace("{{DSR_IP}}", ip)
                .replace("{{ADMIN_USER}}", admin_user)
                .replace("{{TARGET_PORT}}", str(target_port))
                .replace("{{WS_PORT}}", str(ws_port)))
    out_path = os.path.join(outdir, "startup.sh")
    with open(out_path, "w", newline="\n") as f:
        f.write(rendered)
    print(f"wrote {out_path}")
    return out_path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ip", help="the DSR2030's admin IP")
    ap.add_argument("target_port", type=int, help="numeric AVSP port of the default target to auto-connect to on boot (admin UI's target list)")
    ap.add_argument("--ws-port", type=int, default=8080)
    ap.add_argument("--admin-user", default="admin",
                     help="fallback AVSP-session username only -- NOT the appliance login. "
                          "Real admin credentials are read from /mnt/jffs/kvm_creds on the "
                          "device (set via the daemon's settings page), never from here -- "
                          "that's also why there's no --admin-pass option")
    ap.add_argument("--offset", default="900000", help="hex offset into mtd1 for the reserved envelope (default 900000, see FLASH_LAYOUT.md)")
    ap.add_argument("--outdir", default=os.path.join(HERE, "deploy_out"))
    ap.add_argument("--tftp-host", default=None,
                     help="this machine's IP as reachable from the device (default: auto-detected outbound IP toward --ip)")
    ap.add_argument("--tftp-port", type=int, default=6969)
    args = ap.parse_args()

    tftp_host = args.tftp_host or guess_local_ip(args.ip)
    if not tftp_host:
        print("ERROR: couldn't auto-detect this machine's IP toward "
              f"{args.ip} -- pass --tftp-host explicitly.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.outdir, exist_ok=True)
    envelope_path = build_envelope(args.outdir)
    startup_path = render_startup_sh(args.outdir, args.ip, args.target_port,
                                      args.ws_port, args.admin_user, args.offset)

    # Copy the two flash-tool binaries into outdir too, so install.sh's
    # single TFTP root has everything it needs -- these otherwise only
    # live alongside this script, in a different directory than the
    # per-device outputs above.
    for f in ("flash_bundle_read_ppc", "mtd_erase_write_ppc"):
        shutil.copy(os.path.join(HERE, f), os.path.join(args.outdir, f))

    install_path = render_install_sh(args.outdir, tftp_host, args.tftp_port, args.offset)

    print("\n" + "=" * 72)
    print(f"Bundle built in {args.outdir} -- serve that directory with a TFTP")
    print(f"server on {tftp_host}:{args.tftp_port} (e.g. `python3 -m tftpy.TftpServer")
    print(f"{args.tftp_port} {args.outdir}` or any standard tftpd), then from a root")
    print("shell on the device (fired console workaround, or the SETUP port")
    print("gateway), after the new firmware has booted:")
    print("=" * 72)
    print(f"""
cd /tmp
tftp -g -r install.sh -l install.sh {tftp_host} {args.tftp_port}
sh install.sh
""")
    print("That fetches everything else itself, writes the bundle to flash,")
    print("and starts the daemon immediately (no reboot needed to test).")
    print(f"install.sh has {tftp_host}:{args.tftp_port} and offset {args.offset} baked in")
    print("-- regenerate (rerun this script) if any of those need to change.")
    print("\nNOTE: mtd_erase_write_ppc refuses to overwrite anything that isn't")
    print("already blank or one of our own bundles (checks for an \"AVCB\" magic")
    print("first). Writing to a region that already holds a pre-2026-08-19")
    print("(unprefixed) bundle will be refused -- erase it first with")
    print("mtd_erase_only_ppc, or pass --force to mtd_erase_write_ppc once,")
    print("knowingly, to migrate it.")
    print("\nOnce confirmed working, reboot the appliance once to prove the")
    print("full cold-boot chain (auto_run -> startup.sh -> watchdog.sh) works")
    print("on its own, not just install.sh's own immediate test run.")


if __name__ == "__main__":
    main()
