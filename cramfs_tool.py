#!/usr/bin/env python3
"""Full big-endian/PPC-bitfield cramfs reader+writer for the Avocent DSR firmware format.
Round-trips: parse original image into an in-memory tree, optionally mutate
file contents, then re-serialize into a fresh valid cramfs image.
"""
import struct, zlib, os, sys, stat

BLKSZ = 4096
S_IFMT = 0o170000
S_IFDIR = 0o040000
S_IFREG = 0o100000
S_IFLNK = 0o120000
S_IFCHR = 0o020000
S_IFBLK = 0o060000
S_IFIFO = 0o010000
S_IFSOCK = 0o140000


class Node:
    def __init__(self, name, mode, uid, gid):
        self.name = name
        self.mode = mode
        self.uid = uid
        self.gid = gid
        self.children = None   # list[Node] for dirs
        self.data = None       # bytes for regular files
        self.target = None     # bytes for symlinks
        self.rdev = None       # raw 'size' field for char/block devices


def parse(fw_bytes, base_off):
    sb = fw_bytes[base_off:]
    magic = struct.unpack_from('>I', sb, 0)[0]
    assert magic == 0x28cd3d45, hex(magic)
    fs_size = struct.unpack_from('>I', sb, 4)[0]

    def read_inode(off):
        w0, w1, w2 = struct.unpack_from('>III', sb, off)
        mode = (w0 >> 16) & 0xFFFF
        uid = w0 & 0xFFFF
        size = (w1 >> 8) & 0xFFFFFF
        gid = w1 & 0xFF
        namelen = ((w2 >> 26) & 0x3F) * 4
        offset = (w2 & 0x3FFFFFF) * 4
        return mode, uid, gid, size, namelen, offset

    def read_file_data(size, off):
        if size == 0:
            return b''
        nblocks = (size + BLKSZ - 1) // BLKSZ
        ptrs = struct.unpack_from('>%dI' % nblocks, sb, off)
        blk_start = off + nblocks * 4
        out = bytearray()
        prev_end = blk_start
        for p_end in ptrs:
            comp = sb[prev_end:p_end]
            out += zlib.decompress(comp) if comp else b''
            prev_end = p_end
        return bytes(out[:size])

    def build(inode_off, name):
        mode, uid, gid, size, namelen, offset = read_inode(inode_off)
        node = Node(name, mode, uid, gid)
        ftype = mode & S_IFMT
        if ftype == S_IFDIR:
            node.children = []
            pos = offset
            end = offset + size
            while pos < end:
                c_mode, c_uid, c_gid, c_size, c_namelen, c_offset = read_inode(pos)
                cname = sb[pos + 12:pos + 12 + c_namelen].split(b'\x00')[0].decode('latin1')
                child = build(pos, cname)
                node.children.append(child)
                pos += 12 + c_namelen
        elif ftype == S_IFREG:
            node.data = read_file_data(size, offset)
        elif ftype == S_IFLNK:
            node.target = read_file_data(size, offset)
        elif ftype in (S_IFCHR, S_IFBLK, S_IFIFO, S_IFSOCK):
            node.rdev = size
        else:
            raise ValueError("unknown type %o" % mode)
        return node

    root = build(64, '')
    return root, fs_size


def find(root, path_parts):
    node = root
    for part in path_parts:
        node = next(c for c in node.children if c.name == part)
    return node


def _compress_blocks(data):
    """Return (blockptr_table_bytes, compressed_data_bytes)."""
    if len(data) == 0:
        return b'', b''
    nblocks = (len(data) + BLKSZ - 1) // BLKSZ
    comp_blocks = []
    for i in range(nblocks):
        chunk = data[i * BLKSZ:(i + 1) * BLKSZ]
        comp_blocks.append(zlib.compress(chunk, 9))
    ptrs = []
    cum = 0
    for cb in comp_blocks:
        cum += len(cb)
        ptrs.append(cum)  # will be rebased to absolute offset later
    return comp_blocks, ptrs


def serialize(root, volname=b'Compressed'):
    """Two-pass layout + emit. Returns full cramfs image bytes (uncompressed CRC filled in)."""
    HEADER = 64  # magic+size+flags+future(16) + sig(16) + fsid(16) + name(16)

    # Pass 1+2 combined via recursive emission using a mutable position counter,
    # writing inode headers into a preallocated bytearray we grow as we go.
    buf = bytearray(HEADER + 12)  # superblock + root inode placeholder
    pos = [len(buf)]

    def alloc(n):
        start = pos[0]
        buf.extend(b'\x00' * n)
        pos[0] += n
        return start

    def write_inode(at, mode, uid, gid, size, namelen_bytes, offset_words):
        w0 = ((mode & 0xFFFF) << 16) | (uid & 0xFFFF)
        w1 = ((size & 0xFFFFFF) << 8) | (gid & 0xFF)
        w2 = (((namelen_bytes // 4) & 0x3F) << 26) | (offset_words & 0x3FFFFFF)
        struct.pack_into('>III', buf, at, w0, w1, w2)

    def emit_file_data(data):
        if len(data) == 0:
            return alloc(0)
        nblocks = (len(data) + BLKSZ - 1) // BLKSZ
        comp_blocks = [zlib.compress(data[i * BLKSZ:(i + 1) * BLKSZ], 9) for i in range(nblocks)]
        table_start = alloc(nblocks * 4)
        data_start = pos[0]
        cum_abs = data_start
        ptrs = []
        for cb in comp_blocks:
            alloc(len(cb))
            buf[pos[0] - len(cb):pos[0]] = cb
            cum_abs = pos[0]
            ptrs.append(cum_abs)
        struct.pack_into('>%dI' % nblocks, buf, table_start, *ptrs)
        pad = (-pos[0]) % 4
        if pad:
            alloc(pad)
        return table_start

    def emit_dir_entries(node):
        # reserve contiguous inode+name slots for all children first
        slots = []
        for child in node.children:
            namelen_padded = ((len(child.name) + 3) // 4) * 4
            if namelen_padded == 0:
                namelen_padded = 4  # cramfs still needs a slot even for zero-length? avoid 0
            slot_at = alloc(12 + namelen_padded)
            slots.append((slot_at, namelen_padded))
        # now fill in each child's own data, then backpatch its inode header
        for child, (slot_at, namelen_padded) in zip(node.children, slots):
            name_bytes = child.name.encode('latin1')
            buf[slot_at + 12:slot_at + 12 + len(name_bytes)] = name_bytes
            ftype = child.mode & S_IFMT
            if ftype == S_IFDIR:
                data_off = emit_dir(child)
                size = child._dirsize
            elif ftype == S_IFREG:
                data_off = emit_file_data(child.data)
                size = len(child.data)
            elif ftype == S_IFLNK:
                data_off = emit_file_data(child.target)
                size = len(child.target)
            else:
                data_off = 0
                size = child.rdev
            write_inode(slot_at, child.mode, child.uid, child.gid, size,
                        namelen_padded, data_off // 4)

    def emit_dir(node):
        entries_start = pos[0]
        dirsize = sum(12 + max(((len(c.name) + 3) // 4) * 4, 4) for c in node.children)
        node._dirsize = dirsize
        emit_dir_entries(node)
        return entries_start

    root_off = emit_dir(root)
    write_inode(HEADER, root.mode, root.uid, root.gid, root._dirsize, 0, root_off // 4)

    fs_size = len(buf)
    # superblock
    struct.pack_into('>I', buf, 0, 0x28cd3d45)
    struct.pack_into('>I', buf, 4, fs_size)
    struct.pack_into('>I', buf, 8, 0)   # flags
    struct.pack_into('>I', buf, 12, 0)  # future
    buf[16:32] = b'Compressed ROMFS'.ljust(16, b'\x00')[:16]
    crc = zlib.crc32(bytes(buf[HEADER:])) & 0xffffffff
    struct.pack_into('>IIII', buf, 32, crc, 0, (fs_size + BLKSZ - 1) // BLKSZ, 0)  # crc,edition,blocks,files (files unused by kernel reader)
    buf[48:64] = volname.ljust(16, b'\x00')[:16]

    return bytes(buf)


if __name__ == '__main__':
    # self-test: round trip
    fw = open(sys.argv[1], 'rb').read()
    base = int(sys.argv[2], 0)
    root, fs_size = parse(fw, base)
    print("parsed fs_size", hex(fs_size))
    new_image = serialize(root)
    print("new image size", hex(len(new_image)))
    root2, fs_size2 = parse(new_image, 0)

    def walk_compare(a, b, path=''):
        assert a.mode == b.mode, (path, oct(a.mode), oct(b.mode))
        assert a.uid == b.uid and a.gid == b.gid, path
        ftype = a.mode & S_IFMT
        if ftype == S_IFDIR:
            an = sorted(c.name for c in a.children)
            bn = sorted(c.name for c in b.children)
            assert an == bn, (path, set(an) ^ set(bn))
            bm = {c.name: c for c in b.children}
            for c in a.children:
                walk_compare(c, bm[c.name], path + '/' + c.name)
        elif ftype == S_IFREG:
            assert a.data == b.data, (path, len(a.data), len(b.data))
        elif ftype == S_IFLNK:
            assert a.target == b.target, path
        else:
            assert a.rdev == b.rdev, path

    walk_compare(root, root2)
    print("ROUND TRIP OK: all", "files/dirs/symlinks/devices match byte-for-byte")
