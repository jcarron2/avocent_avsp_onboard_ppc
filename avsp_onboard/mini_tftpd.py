#!/usr/bin/env python3
"""Minimal RFC 1350 TFTP server, unprivileged port, stdlib only -- no
tftpy or a system tftpd needed. Serves (and accepts) files from a given
directory; the client (BusyBox tftp on the DSR2030) is told the port
explicitly, so binding the privileged port 69 is never needed.

Usage: mini_tftpd.py <directory> [port]   (port default 6969)

Vendored from the parent project's tools/dvc15_c/mini_tftpd.py, adapted
to take the served directory and port as arguments instead of a
hardcoded ./tftproot -- runme.sh's avsp_bundle_menu uses this to serve
deploy_bundle.py's --outdir directly. WRQ (upload) support kept for
symmetry even though this release's own install.sh flow only ever does
RRQ (the device fetching files, never pushing any back).
"""
import argparse
import os
import socket
import struct
import sys
import threading

ROOT = "."
LISTEN_PORT = 6969
BLOCK_SIZE = 512

OP_RRQ, OP_WRQ, OP_DATA, OP_ACK, OP_ERROR = 1, 2, 3, 4, 5


def serve_rrq(filename, client_addr):
    path = os.path.join(ROOT, os.path.basename(filename))
    print(f"[tftpd] RRQ for {filename!r} from {client_addr} -> {path}", flush=True)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception as e:
        print(f"[tftpd] error opening {path}: {e}", flush=True)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(struct.pack("!HH", OP_ERROR, 1) + b"File not found\x00", client_addr)
        s.close()
        return

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 0))
    s.settimeout(5)

    block_num = 1
    offset = 0
    total = len(data)
    while True:
        chunk = data[offset:offset + BLOCK_SIZE]
        packet = struct.pack("!HH", OP_DATA, block_num & 0xFFFF) + chunk
        sent_ok = False
        for attempt in range(5):
            s.sendto(packet, client_addr)
            try:
                ack, addr = s.recvfrom(1024)
            except socket.timeout:
                continue
            if len(ack) >= 4:
                opcode, acked_block = struct.unpack("!HH", ack[:4])
                if opcode == OP_ACK and acked_block == (block_num & 0xFFFF):
                    sent_ok = True
                    break
        if not sent_ok:
            print(f"[tftpd] transfer to {client_addr} failed (no ACK for block {block_num})", flush=True)
            s.close()
            return
        offset += len(chunk)
        if len(chunk) < BLOCK_SIZE:
            print(f"[tftpd] transfer of {filename!r} to {client_addr} complete ({total} bytes)", flush=True)
            break
        block_num += 1
    s.close()


def serve_wrq(filename, client_addr):
    path = os.path.join(ROOT, os.path.basename(filename))
    print(f"[tftpd] WRQ for {filename!r} from {client_addr} -> {path}", flush=True)

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 0))
    s.settimeout(5)

    # ACK block 0 to tell the client to start sending block 1.
    s.sendto(struct.pack("!HH", OP_ACK, 0), client_addr)

    received = bytearray()
    expected_block = 1
    while True:
        try:
            data, addr = s.recvfrom(2048)
        except socket.timeout:
            print(f"[tftpd] WRQ for {filename!r} timed out waiting for block {expected_block}", flush=True)
            s.close()
            return
        if len(data) < 4:
            continue
        opcode, block_num = struct.unpack("!HH", data[:4])
        if opcode != OP_DATA:
            continue
        chunk = data[4:]
        if block_num == (expected_block & 0xFFFF):
            received.extend(chunk)
            s.sendto(struct.pack("!HH", OP_ACK, block_num), client_addr)
            if len(chunk) < BLOCK_SIZE:
                with open(path, "wb") as f:
                    f.write(received)
                print(f"[tftpd] WRQ of {filename!r} from {client_addr} complete ({len(received)} bytes) -> {path}", flush=True)
                s.close()
                return
            expected_block += 1
        else:
            # duplicate/out-of-order block -- re-ack the last good one
            s.sendto(struct.pack("!HH", OP_ACK, (expected_block - 1) & 0xFFFF), client_addr)


def main():
    global ROOT, LISTEN_PORT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("directory", help="directory to serve files from (and accept uploads into)")
    ap.add_argument("port", type=int, nargs="?", default=6969)
    args = ap.parse_args()

    ROOT = os.path.abspath(args.directory)
    LISTEN_PORT = args.port
    if not os.path.isdir(ROOT):
        print(f"ERROR: {ROOT} is not a directory", file=sys.stderr)
        sys.exit(1)

    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("0.0.0.0", LISTEN_PORT))
    print(f"[tftpd] listening on 0.0.0.0:{LISTEN_PORT}, serving {ROOT}", flush=True)
    print("[tftpd] Ctrl+C to stop", flush=True)
    try:
        while True:
            data, addr = srv.recvfrom(2048)
            if len(data) < 4:
                continue
            opcode = struct.unpack("!H", data[:2])[0]
            if opcode == OP_RRQ:
                parts = data[2:].split(b"\x00")
                filename = parts[0].decode(errors="replace")
                threading.Thread(target=serve_rrq, args=(filename, addr), daemon=True).start()
            elif opcode == OP_WRQ:
                parts = data[2:].split(b"\x00")
                filename = parts[0].decode(errors="replace")
                threading.Thread(target=serve_wrq, args=(filename, addr), daemon=True).start()
            else:
                print(f"[tftpd] ignoring opcode {opcode} from {addr}", flush=True)
    except KeyboardInterrupt:
        print("\n[tftpd] stopped.")


if __name__ == "__main__":
    main()
