#!/usr/bin/env python3
"""Minimal RFC 2131 DHCP server, stdlib only -- no dnsmasq/udhcpd/pip needed.

Purpose: the laptop field-kit. When you plug a virgin/relocated DSR2030 into a
laptop (or a dead field LAN with no DHCP), this hands the unit an IP so it comes
up reachable and can fetch its onboarding bundle over TFTP (see mini_tftpd.py /
get_avsp_bundle). It also advertises itself as the TFTP server (DHCP option 66),
so the appliance -- and your own tooling -- can discover where to pull from.

Scope on purpose: a single small pool, sticky-by-MAC leases held in memory, the
options a DSR2030 (and most clients) actually need. Not a general-purpose DHCP
server; no persistence, no relay (giaddr) support beyond echoing it back, no
conflict probing. One subnet, one interface.

Needs root (binds UDP port 67). Companion to mini_tftpd.py; same no-dependency,
copy-it-and-run philosophy.

Usage:
  sudo ./mini_dhcpd.py --server-ip 192.168.2.36 --range 192.168.2.100-192.168.2.120
  # optional: --netmask --router --dns --tftp --bootfile --lease --iface

REMINDER (robust-scripts style): checks its prereqs (root, port free, sane
range), is safe to Ctrl-C and re-run (leases are in-memory only), and logs every
OFFER/ACK so you can verify the unit actually got an address.
"""
import argparse
import os
import socket
import struct
import sys
import time

MAGIC = bytes([99, 130, 83, 99])  # DHCP options magic cookie


def ip2int(s):
    return struct.unpack("!I", socket.inet_aton(s))[0]


def int2ip(n):
    return socket.inet_ntoa(struct.pack("!I", n & 0xFFFFFFFF))


def mac_str(b):
    return ":".join("%02x" % x for x in b)


class Pool:
    """Sticky-by-MAC address pool over [start, end]."""
    def __init__(self, start, end):
        self.start, self.end = ip2int(start), ip2int(end)
        if self.end < self.start:
            raise ValueError("range end is below range start")
        self.by_mac = {}      # mac(bytes) -> ip(int)
        self.used = set()     # ip(int)

    def lease(self, mac):
        if mac in self.by_mac:
            return self.by_mac[mac]
        for ip in range(self.start, self.end + 1):
            if ip not in self.used:
                self.by_mac[mac] = ip
                self.used.add(ip)
                return ip
        return None           # pool exhausted


def parse_options(data):
    """Return {code: value_bytes} from the DHCP options area (after cookie)."""
    opts = {}
    i = data.find(MAGIC)
    if i < 0:
        return opts
    i += 4
    while i < len(data):
        code = data[i]
        if code == 0:                      # pad
            i += 1
            continue
        if code == 255:                    # end
            break
        if i + 1 >= len(data):
            break
        length = data[i + 1]
        opts[code] = data[i + 2:i + 2 + length]
        i += 2 + length
    return opts


def build_reply(req, xid, chaddr, yiaddr, flags, giaddr, cfg, msg_type):
    """Assemble a BOOTREPLY for OFFER(2)/ACK(5)."""
    bootfile = (cfg.get("bootfile") or "").encode()[:127]
    pkt = struct.pack(
        "!BBBB I HH IIII 16s 64s 128s",
        2, 1, 6, 0,                        # op=BOOTREPLY, htype=eth, hlen=6, hops=0
        xid,
        0, flags,                          # secs, flags (echo client's broadcast bit)
        0,                                 # ciaddr
        yiaddr,                            # yiaddr -- the address we're giving
        ip2int(cfg["tftp"]),               # siaddr -- next-server = TFTP server
        giaddr,                            # giaddr -- echoed for relays
        chaddr.ljust(16, b"\x00"),         # chaddr (16 bytes, MAC + pad)
        b"\x00" * 64,                      # sname (unused)
        bootfile.ljust(128, b"\x00"),      # file (boot filename, usually empty)
    )
    opts = bytearray(MAGIC)

    def opt(code, payload):
        opts.extend(bytes([code, len(payload)]))
        opts.extend(payload)

    opt(53, bytes([msg_type]))                         # DHCP message type
    opt(54, socket.inet_aton(cfg["server_ip"]))        # server identifier
    opt(51, struct.pack("!I", cfg["lease"]))           # lease time
    opt(1, socket.inet_aton(cfg["netmask"]))           # subnet mask
    if cfg.get("router"):
        opt(3, socket.inet_aton(cfg["router"]))        # router / default gw
    if cfg.get("dns"):
        opt(6, socket.inet_aton(cfg["dns"]))           # DNS server
    if cfg.get("tftp"):
        opt(66, cfg["tftp"].encode())                  # TFTP server name (string)
        opt(150, socket.inet_aton(cfg["tftp"]))        # TFTP server (Cisco, IP)
    if cfg.get("bootfile"):
        opt(67, cfg["bootfile"].encode())              # bootfile name
    opts.append(255)                                   # end
    return pkt + bytes(opts)


def main():
    ap = argparse.ArgumentParser(description="Minimal stdlib DHCP server for the field-kit.")
    ap.add_argument("--server-ip", required=True, help="this laptop's static IP on the field LAN (also the DHCP server id)")
    ap.add_argument("--range", required=True, help="address pool, e.g. 192.168.2.100-192.168.2.120")
    ap.add_argument("--netmask", default="255.255.255.0")
    ap.add_argument("--router", default="", help="default gateway to hand out (blank = none)")
    ap.add_argument("--dns", default="", help="DNS server to hand out (blank = none)")
    ap.add_argument("--tftp", default="", help="TFTP server to advertise (option 66/150); blank = --server-ip")
    ap.add_argument("--bootfile", default="", help="bootfile name (option 67); usually blank for the DSR2030")
    ap.add_argument("--lease", type=int, default=3600, help="lease time seconds (default 3600)")
    ap.add_argument("--iface", default="", help="bind to one interface, e.g. eth0 (needs root; recommended on a multi-NIC laptop)")
    args = ap.parse_args()

    if os.geteuid() != 0:
        sys.exit("ERROR: DHCP needs UDP port 67 -- run with sudo.")

    try:
        start, end = args.range.split("-", 1)
        pool = Pool(start.strip(), end.strip())
    except Exception as e:
        sys.exit(f"ERROR: bad --range {args.range!r}: {e}")

    cfg = {
        "server_ip": args.server_ip,
        "netmask": args.netmask,
        "router": args.router or None,
        "dns": args.dns or None,
        "tftp": args.tftp or args.server_ip,
        "bootfile": args.bootfile or None,
        "lease": args.lease,
    }

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    if args.iface:
        try:
            s.setsockopt(socket.SOL_SOCKET, 25, (args.iface + "\0").encode())  # SO_BINDTODEVICE
        except PermissionError:
            sys.exit("ERROR: --iface bind needs root.")
        except OSError as e:
            sys.exit(f"ERROR: can't bind to iface {args.iface!r}: {e}")
    try:
        s.bind(("0.0.0.0", 67))
    except OSError as e:
        sys.exit(f"ERROR: can't bind UDP :67 ({e}). Another DHCP server (dnsmasq/"
                 "NetworkManager/systemd-networkd) is probably running -- stop it first.")

    print(f"[dhcpd] serving {args.range} mask {cfg['netmask']} "
          f"(server/id {cfg['server_ip']}, tftp {cfg['tftp']}, "
          f"lease {cfg['lease']}s{', iface ' + args.iface if args.iface else ''})",
          flush=True)
    print("[dhcpd] waiting for DISCOVER... (Ctrl-C to stop)", flush=True)

    while True:
        try:
            data, _ = s.recvfrom(2048)
        except KeyboardInterrupt:
            print("\n[dhcpd] stopped.", flush=True)
            return
        if len(data) < 240 or data[0] != 1:   # not a BOOTREQUEST
            continue
        xid = struct.unpack("!I", data[4:8])[0]
        flags = struct.unpack("!H", data[10:12])[0]
        giaddr = struct.unpack("!I", data[24:28])[0]
        chaddr = data[28:34]                   # first 6 of the 16-byte chaddr
        opts = parse_options(data)
        mtype = opts.get(53, b"\x00")[0] if 53 in opts else 0
        mac = mac_str(chaddr)

        if mtype == 1:        # DISCOVER
            ip = pool.lease(chaddr)
            if ip is None:
                print(f"[dhcpd] DISCOVER {mac}: pool exhausted, ignoring", flush=True)
                continue
            reply = build_reply(data, xid, chaddr, ip, flags, giaddr, cfg, 2)  # OFFER
            s.sendto(reply, ("255.255.255.255", 68))
            print(f"[dhcpd] DISCOVER {mac} -> OFFER {int2ip(ip)}", flush=True)
        elif mtype == 3:      # REQUEST
            # honour the requested address if it's the one we leased this MAC
            req_ip = None
            if 50 in opts and len(opts[50]) == 4:
                req_ip = struct.unpack("!I", opts[50])[0]
            ip = pool.lease(chaddr)
            if ip is None:
                continue
            if req_ip is not None and req_ip != ip:
                # client asked for an address we didn't give it -> NAK
                nak = build_reply(data, xid, chaddr, 0, flags, giaddr, cfg, 6)  # NAK
                s.sendto(nak, ("255.255.255.255", 68))
                print(f"[dhcpd] REQUEST {mac} for {int2ip(req_ip)} != {int2ip(ip)} -> NAK", flush=True)
                continue
            reply = build_reply(data, xid, chaddr, ip, flags, giaddr, cfg, 5)   # ACK
            s.sendto(reply, ("255.255.255.255", 68))
            print(f"[dhcpd] REQUEST {mac} -> ACK {int2ip(ip)}  (lease {cfg['lease']}s)", flush=True)
        elif mtype in (4, 7):  # DECLINE / RELEASE
            print(f"[dhcpd] {'DECLINE' if mtype == 4 else 'RELEASE'} from {mac}", flush=True)
        # INFORM(8) and others: ignore -- the client already has an address


if __name__ == "__main__":
    main()
