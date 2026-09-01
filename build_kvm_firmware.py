#!/usr/bin/env python3
"""build_kvm_firmware.py -- portable tool to take a STOCK Avocent DSR2030
".fl" firmware image and produce a modified one with an a-la-carte set of
features applied, without needing anything else from the original
reverse-engineering project.

Verifies the input firmware's own checksums first (refuses to touch
anything it can't confirm is internally consistent), then applies
whichever features you choose to a fresh unpack of the rootfs, then
repacks and re-verifies the output.

Features (see release/README.md for how each one actually works):
  modem-console   Interactive root shell on the MODEM port (/dev/ttyS1).
                   Rootfs-only, no boot-region patch -- proven safe.
  avsp-onboard     Adds the boot-time hook needed for the AVSP companion
                   daemon (a separate step, avsp_onboard/deploy_bundle.py,
                   actually installs the daemon after this firmware boots).
  java-certs       Fresh self-signed HTTPS admin + AVSP video certs, fresh
                   code-signing key, all 12 Web Start jars re-signed, dead
                   TLS ciphers in avctVideo.jar replaced with working ones.
                   Needs --cert-ip (the IP the HTTPS cert's SAN should cover).

NOT included, by design: a raw kernel `console=ttyS1` boot-region patch.
That path bricked a real test unit in the original project and the exact
second failure mechanism (beyond the checksum bug, which was found and
fixed) was never fully understood. See JTAG_FLASH_RECOVERY.md in the
parent project if you want to pursue it anyway -- deliberately excluded
here.

Usage:
  build_kvm_firmware.py <stock.fl> -o <out.fl> [feature flags...]
  build_kvm_firmware.py <stock.fl> -o <out.fl>          # interactive prompts

Examples:
  build_kvm_firmware.py stock.fl -o custom.fl --modem-console
  build_kvm_firmware.py stock.fl -o custom.fl --java-certs --cert-ip 192.168.2.99
  build_kvm_firmware.py stock.fl -o custom.fl --modem-console --avsp-onboard
"""
import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "features"))

import avocent_fl_tool  # noqa: E402
import modem_console  # noqa: E402
import avsp_onboard_hook  # noqa: E402
import java_certs  # noqa: E402


def prompt_yn(question, default=False):
    suffix = "[Y/n]" if default else "[y/N]"
    ans = input(f"{question} {suffix} ").strip().lower()
    if not ans:
        return default
    return ans.startswith("y")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stock_fl", help="path to a stock/unmodified .fl firmware image")
    ap.add_argument("-o", "--out", required=True, help="output .fl path")
    ap.add_argument("--modem-console", action="store_true",
                     help="enable modem_console feature")
    ap.add_argument("--avsp-onboard", action="store_true",
                     help="enable avsp_onboard_hook feature (rootfs boot hook only -- "
                          "run avsp_onboard/deploy_bundle.py afterward to finish install)")
    ap.add_argument("--java-certs", action="store_true",
                     help="enable java_certs feature")
    ap.add_argument("--cert-ip", default=None,
                     help="IP address for the HTTPS admin cert's SAN (required with --java-certs)")
    ap.add_argument("--jdk-bin", default=None,
                     help="directory containing keytool/jarsigner/jar, if not on PATH")
    ap.add_argument("--keep-work-dir", action="store_true",
                     help="don't delete the temporary unpack directory (for debugging)")
    args = ap.parse_args()

    if not os.path.exists(args.stock_fl):
        print(f"ERROR: {args.stock_fl} not found", file=sys.stderr)
        sys.exit(1)

    print(f"[*] Verifying {args.stock_fl} before touching anything...")
    ok = avocent_fl_tool.cmd_verify(args.stock_fl)
    if not ok:
        print("\nERROR: input firmware failed verification -- refusing to modify a "
              "firmware image whose own checksums don't check out. This tool only "
              "works from a known-good stock (or previously-verified) .fl.",
              file=sys.stderr)
        sys.exit(1)
    print("[+] Input firmware verified clean.\n")

    interactive = not (args.modem_console or args.avsp_onboard or args.java_certs)
    do_modem = args.modem_console
    do_avsp = args.avsp_onboard
    do_java = args.java_certs
    cert_ip = args.cert_ip

    if interactive:
        print("No feature flags given -- entering interactive mode.\n")
        print(f"  modem-console: {modem_console.describe()}\n")
        do_modem = prompt_yn("Enable modem-console?", default=True)
        print(f"\n  avsp-onboard: {avsp_onboard_hook.describe()}\n")
        do_avsp = prompt_yn("Enable avsp-onboard hook?", default=True)
        print(f"\n  java-certs: {java_certs.describe()}\n")
        do_java = prompt_yn("Enable java-certs?", default=True)
        if do_java:
            cert_ip = input("  IP address for the HTTPS cert's SAN (e.g. 192.168.2.99): ").strip()
        print()

    if do_java and not cert_ip:
        print("ERROR: --java-certs requires --cert-ip <ip>", file=sys.stderr)
        sys.exit(1)

    if not (do_modem or do_avsp or do_java):
        print("No features selected -- nothing to do.")
        sys.exit(0)

    work_dir = tempfile.mkdtemp(prefix="kvm_fw_build_")
    tree_dir = os.path.join(work_dir, "unpacked")
    try:
        print(f"[*] Unpacking {args.stock_fl} -> {tree_dir} ...")
        avocent_fl_tool.cmd_unpack(args.stock_fl, tree_dir)

        if do_modem:
            print("\n[*] Applying feature: modem-console")
            modem_console.apply(tree_dir)
        if do_avsp:
            print("\n[*] Applying feature: avsp-onboard (rootfs hook)")
            avsp_onboard_hook.apply(tree_dir)
        if do_java:
            print(f"\n[*] Applying feature: java-certs (SAN IP={cert_ip})")
            java_certs.apply(tree_dir, ip=cert_ip, jdk_bin=args.jdk_bin)

        print(f"\n[*] Packing -> {args.out} ...")
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        avocent_fl_tool.cmd_pack(args.stock_fl, tree_dir, args.out)

        print(f"\n[*] Verifying {args.out} ...")
        ok = avocent_fl_tool.cmd_verify(args.out)
        if not ok:
            print("\nERROR: output firmware failed self-verification -- do not flash this "
                  "file. Please report this as a bug.", file=sys.stderr)
            sys.exit(1)

        print(f"\n[+] Done: {args.out}")
        if do_avsp:
            print("\nNOTE: avsp-onboard's rootfs hook is in place, but the companion")
            print("daemon itself still needs to be installed after this firmware boots.")
            print(f"Run: python3 {os.path.join(HERE, 'avsp_onboard', 'deploy_bundle.py')} <ip> <target-port>")
    finally:
        if args.keep_work_dir:
            print(f"\n[*] --keep-work-dir set, not deleting {work_dir}")
        else:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
