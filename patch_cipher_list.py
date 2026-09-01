#!/usr/bin/env python3
"""Patch the hardcoded TLS cipher suite list inside the real Avocent
Stingray.class (found via decompilation: Stingray.avspConnect() builds an
explicit String[] of 8 cipher names, 7 of which are dead/broken on any
Java 8+ build -- RC4/DES/3DES/NULL/export -- and only
TLS_RSA_WITH_AES_128_CBC_SHA actually works, confirmed via live handshake
testing against the real appliance).

This is a surgical constant-pool edit, not a recompile: Java classfiles
reference constant-pool entries only by INDEX NUMBER, never by byte offset,
so a UTF8 constant's content (and length) can be freely changed without
touching anything else in the file -- confirmed via round-trip self-test.

This script only does the classfile-level patch (reads/writes raw .class
bytes). Jar-level repackaging (regenerating a correct manifest with fresh
per-entry digests, then re-signing) is handled separately with the JDK's own
`jar`/`jarsigner` tools -- hand-rolling manifest digest regeneration would be
redundant and error-prone when the real tool already does it correctly.

Usage: patch_cipher_list.py <in.class> <out.class>
"""
import struct
import sys

OLD_CIPHERS = [
    "SSL_RSA_WITH_RC4_128_MD5",
    "SSL_RSA_WITH_RC4_128_SHA",
    "SSL_RSA_WITH_DES_CBC_SHA",
    "SSL_RSA_WITH_3DES_EDE_CBC_SHA",
    "SSL_RSA_WITH_NULL_MD5",
    "SSL_RSA_WITH_NULL_SHA",
    "SSL_RSA_EXPORT_WITH_RC4_40_MD5",
]
# TLS_RSA_WITH_AES_128_CBC_SHA (the 8th, already-good original entry) is left
# untouched. The 7 dead ones get replaced with real, working options.
REPLACEMENT_CIPHERS = [
    "TLS_RSA_WITH_AES_128_CBC_SHA",
    "TLS_RSA_WITH_AES_128_CBC_SHA256",
    "TLS_RSA_WITH_AES_256_CBC_SHA",
    "TLS_DHE_RSA_WITH_AES_128_CBC_SHA",
    "TLS_RSA_WITH_AES_128_CBC_SHA",
    "TLS_RSA_WITH_AES_128_CBC_SHA",
    "TLS_RSA_WITH_AES_128_CBC_SHA",
]

# Fixed-size constant-pool entry byte lengths, INCLUDING the 1-byte tag.
FIXED_SIZE = {
    3: 5, 4: 5,             # Integer, Float
    9: 5, 10: 5, 11: 5,     # Fieldref, Methodref, InterfaceMethodref
    12: 5,                  # NameAndType
    18: 5,                  # InvokeDynamic
    5: 9, 6: 9,             # Long, Double (occupy 2 indices, see loop below)
    7: 3, 8: 3, 16: 3,      # Class, String, MethodType
    15: 4,                  # MethodHandle
    19: 3, 20: 3,           # Module, Package (Java 9+, unlikely here)
}


def patch_classfile(data: bytes) -> bytes:
    assert data[0:4] == b'\xca\xfe\xba\xbe', "not a classfile"
    pos = 8  # magic(4) + minor(2) + major(2)
    cp_count = struct.unpack('>H', data[pos:pos+2])[0]
    pos += 2
    cp_start = pos

    entries = []
    i = 1
    while i < cp_count:
        tag = data[pos]
        if tag == 1:  # Utf8
            length = struct.unpack('>H', data[pos+1:pos+3])[0]
            text = data[pos+3:pos+3+length]
            entries.append((tag, pos, 3 + length, text))
            pos += 3 + length
            i += 1
        else:
            size = FIXED_SIZE.get(tag)
            if size is None:
                raise ValueError(f"unknown constant pool tag {tag} at offset {pos}")
            entries.append((tag, pos, size, None))
            pos += size
            i += 2 if tag in (5, 6) else 1
    cp_end = pos

    out = bytearray(data[:cp_start])
    replacements_made = 0
    for tag, start, size, text in entries:
        if tag == 1 and text is not None:
            decoded = text.decode('utf-8', errors='replace')
            if decoded in OLD_CIPHERS:
                new_str = REPLACEMENT_CIPHERS[OLD_CIPHERS.index(decoded)].encode('utf-8')
                out += bytes([1]) + struct.pack('>H', len(new_str)) + new_str
                replacements_made += 1
                continue
        out += data[start:start + size]

    out += data[cp_end:]  # rest of the classfile, byte-for-byte unchanged
    print(f"  replaced {replacements_made}/{len(OLD_CIPHERS)} cipher name constants")
    assert replacements_made == len(OLD_CIPHERS), \
        "did not find all expected cipher strings -- classfile may differ from what was decompiled"
    return bytes(out)


def main():
    in_path, out_path = sys.argv[1], sys.argv[2]
    data = open(in_path, 'rb').read()
    print(f"[*] read {in_path}: {len(data)} bytes")
    patched = patch_classfile(data)
    print(f"[*] patched: {len(patched)} bytes")
    open(out_path, 'wb').write(patched)
    print(f"[+] wrote {out_path}")


if __name__ == '__main__':
    main()
