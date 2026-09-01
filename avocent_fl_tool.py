#!/usr/bin/env python3
"""
avocent_fl_tool.py -- unpack / pack / verify Avocent DSR2030 ".fl" firmware images.

Container format (reverse-engineered, see AVOCENT_DSR2030_FIRMWARE_ANALYSIS.md):
  A flat, UNSIGNED, checksum-only multi-image blob for a big-endian PowerPC target.
  Fixed-offset 32-bit big-endian header fields describe a boot region (untouched
  by this tool), a cramfs "rootfs" image, and a raw kernel image appended right
  after it. Checksums are a plain 32-bit wraparound sum of every byte in the
  region -- not CRC32, not cryptographic.

  Header fields this tool reads/writes (all big-endian u32):
    0x58  rootfs_off    (fixed -- never moves)
    0x5c  rootfs_len
    0x60  rootfs_chk    (byte-sum of fw[rootfs_off : rootfs_off+rootfs_len])
    0x64  rootfs_end    (= rootfs_off + rootfs_len, derived)
    0x68  kernel_len    (untouched -- kernel is never modified by this tool)
    0x6c  kernel_chk    (untouched)
    0x70  kernel_end    (= rootfs_end + kernel_len, derived)
  Anything after kernel_end ("tail") is preserved byte-for-byte.

  The rootfs itself is a cramfs filesystem, but a PowerPC-native variant: 32-bit
  words are big-endian AND inode bitfields (mode:16/uid:16, size:24/gid:8,
  namelen:6/offset:26) are packed MSB-first (PowerPC GCC bitfield order), not
  the LSB-first packing generic x86 cramfs tools assume. That's why stock
  cramfsck/mkcramfs silently misparse this image -- this file implements the
  correct layout from scratch.

Subcommands:
  unpack  <fw.fl> <outdir>            Extract rootfs to a real directory tree
                                       + MANIFEST.json (exact mode/uid/gid/rdev/
                                       symlink-target, since device nodes/ownership
                                       can't fully round-trip through a non-root
                                       filesystem extraction).
  pack    <orig_fw.fl> <indir> <out.fl>
                                       Rebuild a cramfs from <indir>+MANIFEST.json,
                                       splice it into a copy of <orig_fw.fl> (kernel/
                                       boot bytes copied through unchanged), fix up
                                       all header checksum/length/offset fields, and
                                       write a matching .md5 sidecar.
  verify  <fw.fl>                     Re-derive every checksum/length field from the
                                       actual bytes and report PASS/FAIL per field,
                                       re-parse the cramfs structurally, and check
                                       the .md5 sidecar if present alongside.
"""
import hashlib
import json
import os
import struct
import sys

from cramfs_tool import (S_IFBLK, S_IFCHR, S_IFDIR, S_IFIFO, S_IFLNK, S_IFMT,
                          S_IFREG, S_IFSOCK, Node, parse, serialize)

HDR_ROOTFS_OFF = 0x58
HDR_ROOTFS_LEN = 0x5c
HDR_ROOTFS_CHK = 0x60
HDR_ROOTFS_END = 0x64
HDR_KERNEL_LEN = 0x68
HDR_KERNEL_CHK = 0x6c
HDR_KERNEL_END = 0x70


def u32(buf, off):
    return struct.unpack_from('>I', buf, off)[0]


def find_rootfs_offset(fw):
    """Locate the cramfs superblock via its magic, cross-checked against the header."""
    hdr_off = u32(fw, HDR_ROOTFS_OFF)
    assert fw[hdr_off:hdr_off + 4] == b'\x28\xcd\x3d\x45', \
        "header rootfs_off doesn't point at a cramfs magic -- wrong firmware layout?"
    return hdr_off


# ---------------------------------------------------------------- unpack ----

def cmd_unpack(fw_path, outdir):
    fw = open(fw_path, 'rb').read()
    rootfs_off = find_rootfs_offset(fw)
    root, fs_size = parse(fw, rootfs_off)
    print(f"rootfs @ {hex(rootfs_off)}, size {fs_size} bytes")

    manifest = {}

    def walk(node, disk_path, manifest_path):
        ftype = node.mode & S_IFMT
        manifest[manifest_path or '.'] = {
            'mode': node.mode, 'uid': node.uid, 'gid': node.gid,
        }
        if ftype == S_IFDIR:
            os.makedirs(disk_path, exist_ok=True)
            for c in node.children:
                walk(c, os.path.join(disk_path, c.name),
                     (manifest_path + '/' + c.name) if manifest_path else c.name)
        elif ftype == S_IFREG:
            with open(disk_path, 'wb') as f:
                f.write(node.data)
            os.chmod(disk_path, node.mode & 0o777)
        elif ftype == S_IFLNK:
            target = node.target.decode('latin1')
            manifest[manifest_path]['symlink_target'] = target
            try:
                os.symlink(target, disk_path)
            except FileExistsError:
                pass
        else:
            # char/block/fifo/socket: can't mknod without root -- record in
            # manifest only, `pack` reconstructs these from JSON, not from disk.
            manifest[manifest_path]['rdev'] = node.rdev
            manifest[manifest_path]['special'] = True

    os.makedirs(outdir, exist_ok=True)
    walk(root, outdir, '')
    with open(os.path.join(outdir, 'MANIFEST.json'), 'w') as f:
        json.dump(manifest, f, indent=1, sort_keys=True)

    print(f"unpacked to {outdir}/ ({len(manifest)} entries; special files "
          f"[char/block/fifo/socket] recorded in MANIFEST.json only, not created "
          f"on disk -- mknod needs root)")


# ------------------------------------------------------------------ pack ----

def _build_tree_from_disk(indir, manifest):
    # Directory structure comes from MANIFEST.json (the source of truth), NOT
    # from os.listdir(): special files (char/block/fifo/socket) can't be
    # mknod'd without root so they never exist on disk at all, and listdir()
    # would silently drop them from a repack otherwise.
    children_of = {}
    for mpath in manifest:
        if mpath == '.':
            continue
        parent = mpath.rsplit('/', 1)[0] if '/' in mpath else ''
        children_of.setdefault(parent, []).append(mpath)

    def build(manifest_path):
        info = manifest[manifest_path or '.']
        mode, uid, gid = info['mode'], info['uid'], info['gid']
        name = manifest_path.rsplit('/', 1)[-1] if manifest_path else ''
        node = Node(name, mode, uid, gid)
        ftype = mode & S_IFMT
        if ftype == S_IFDIR:
            node.children = [build(c) for c in sorted(children_of.get(manifest_path, []))]
        elif ftype == S_IFREG:
            node.data = open(os.path.join(indir, manifest_path), 'rb').read()
        elif ftype == S_IFLNK:
            node.target = info['symlink_target'].encode('latin1')
        else:
            node.rdev = info['rdev']
        return node

    return build('')


def cmd_pack(orig_fw_path, indir, out_fw_path):
    fw = bytearray(open(orig_fw_path, 'rb').read())
    rootfs_off = find_rootfs_offset(bytes(fw))

    manifest = json.load(open(os.path.join(indir, 'MANIFEST.json')))
    root = _build_tree_from_disk(indir, manifest)

    new_cramfs = serialize(root)
    print(f"rebuilt cramfs: {len(new_cramfs)} bytes")

    old_rootfs_len = u32(fw, HDR_ROOTFS_LEN)
    old_rootfs_end = u32(fw, HDR_ROOTFS_END)
    kernel_len = u32(fw, HDR_KERNEL_LEN)
    kernel_chk = u32(fw, HDR_KERNEL_CHK)
    old_kernel_end = u32(fw, HDR_KERNEL_END)
    assert old_rootfs_end == rootfs_off + old_rootfs_len
    assert old_kernel_end == old_rootfs_end + kernel_len

    kernel_bytes = bytes(fw[old_rootfs_end:old_kernel_end])
    assert (sum(kernel_bytes) & 0xffffffff) == kernel_chk, "kernel checksum sanity check failed"
    tail_bytes = bytes(fw[old_kernel_end:])

    new_rootfs_end = rootfs_off + len(new_cramfs)
    new_kernel_end = new_rootfs_end + kernel_len
    new_rootfs_chk = sum(new_cramfs) & 0xffffffff

    new_fw = bytearray(fw[0:rootfs_off]) + bytearray(new_cramfs) + \
        bytearray(kernel_bytes) + bytearray(tail_bytes)

    struct.pack_into('>I', new_fw, HDR_ROOTFS_LEN, len(new_cramfs))
    struct.pack_into('>I', new_fw, HDR_ROOTFS_CHK, new_rootfs_chk)
    struct.pack_into('>I', new_fw, HDR_ROOTFS_END, new_rootfs_end)
    struct.pack_into('>I', new_fw, HDR_KERNEL_END, new_kernel_end)

    delta = len(new_fw) - len(fw)
    print(f"new total size: {len(new_fw)} bytes ({delta:+d} vs original)")

    with open(out_fw_path, 'wb') as f:
        f.write(new_fw)
    md5 = hashlib.md5(new_fw).hexdigest().upper()
    md5_path = out_fw_path + '.md5'
    with open(md5_path, 'w') as f:
        f.write(f"{os.path.basename(out_fw_path)} {md5}")
    print(f"wrote {out_fw_path}")
    print(f"wrote {md5_path}  ({md5})")


# ---------------------------------------------------------------- verify ----

def cmd_verify(fw_path):
    fw = open(fw_path, 'rb').read()
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        ok = ok and cond

    print(f"verifying {fw_path} ({len(fw)} bytes)")

    try:
        rootfs_off = find_rootfs_offset(fw)
        check("cramfs magic found at header-declared rootfs offset", True)
    except AssertionError as e:
        check(f"cramfs magic found at header-declared rootfs offset ({e})", False)
        rootfs_off = None

    rootfs_len = u32(fw, HDR_ROOTFS_LEN)
    rootfs_chk = u32(fw, HDR_ROOTFS_CHK)
    rootfs_end = u32(fw, HDR_ROOTFS_END)
    kernel_len = u32(fw, HDR_KERNEL_LEN)
    kernel_chk = u32(fw, HDR_KERNEL_CHK)
    kernel_end = u32(fw, HDR_KERNEL_END)

    check("rootfs_end == rootfs_off + rootfs_len", rootfs_end == rootfs_off + rootfs_len)
    check("kernel_end == rootfs_end + kernel_len", kernel_end == rootfs_end + kernel_len)
    check("kernel_end + tail == file length", kernel_end <= len(fw))

    real_rootfs = fw[rootfs_off:rootfs_end]
    real_kernel = fw[rootfs_end:kernel_end]
    check("rootfs checksum (byte-sum) matches header",
          (sum(real_rootfs) & 0xffffffff) == rootfs_chk)
    check("kernel checksum (byte-sum) matches header",
          (sum(real_kernel) & 0xffffffff) == kernel_chk)

    if rootfs_off is not None:
        cramfs_size_field = u32(fw, rootfs_off + 4)
        check("cramfs internal 'size' field matches header rootfs_len",
              cramfs_size_field == rootfs_len)
        try:
            root, fs_size = parse(fw, rootfs_off)

            def count(node):
                n = 1
                if node.children:
                    n += sum(count(c) for c in node.children)
                return n
            total = count(root)
            check(f"cramfs structurally parses cleanly ({total} entries)", True)
        except Exception as e:
            check(f"cramfs structurally parses cleanly ({e})", False)

    md5_path = fw_path + '.md5'
    if os.path.exists(md5_path):
        sidecar = open(md5_path).read().strip()
        real_md5 = hashlib.md5(fw).hexdigest().upper()
        check(f".md5 sidecar matches (computed {real_md5})", real_md5 in sidecar.upper())
    else:
        print(f"  [SKIP] no .md5 sidecar found at {md5_path}")

    print("RESULT:", "ALL CHECKS PASSED" if ok else "ONE OR MORE CHECKS FAILED")
    return ok


# -------------------------------------------------------------------- CLI ---

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    args = sys.argv[2:]
    if cmd == 'unpack' and len(args) == 2:
        cmd_unpack(*args)
    elif cmd == 'pack' and len(args) == 3:
        cmd_pack(*args)
    elif cmd == 'verify' and len(args) == 1:
        sys.exit(0 if cmd_verify(args[0]) else 1)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main()
