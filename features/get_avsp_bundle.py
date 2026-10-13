"""Feature: get_avsp_bundle -- one-command on-device onboarding helper.

Normally, installing the companion daemon after flashing means typing three
commands from a root shell:

    cd /tmp
    tftp -g -r install.sh -l install.sh <your-pc-ip> 6969
    sh install.sh

This feature bakes a small helper into the firmware so it's just:

    sh /usr/sbin/get_avsp_bundle

It reads the TFTP server from /mnt/jffs/get_avsp_bundle.conf (an editable,
on-device ini you can change without rebuilding firmware) if present, otherwise
from the baked /etc/get_avsp_bundle.conf (filled in at build time with whatever
TFTP host you entered). If no tftp_host is set anywhere, it just asks.

IMPORTANT about /mnt/jffs: flashing a .fl only writes the rootfs+kernel, never
the writable /mnt/jffs partition -- so we bake the default into the rootfs
(/etc/get_avsp_bundle.conf) and the helper SEEDS /mnt/jffs/get_avsp_bundle.conf
from it on first run. From then on the on-device copy wins, so you can
re-point the TFTP server right on the box (and a future Settings-page option
can write that same file).

This is the MANUAL trigger (you run it yourself). A fully-automatic one-shot
"fetch+install on first boot" is a separate, opt-in build flag that can be
layered on top once this is proven.
"""

import json
import os

S_IFREG = 0o100000

SCRIPT = r'''#!/bin/sh
# get_avsp_bundle -- fetch and run the avsp-onboard install.sh from a TFTP
# server. Baked in by build_kvm_firmware.py's "get_avsp_bundle" feature so the
# first on-device install is one command instead of three. Run from a root
# shell (modem console):   sh /usr/sbin/get_avsp_bundle
CONF_JFFS=/mnt/jffs/get_avsp_bundle.conf
CONF_ETC=/etc/get_avsp_bundle.conf

# First run: seed the editable on-device copy from the baked default, so the
# TFTP server can be changed later without reflashing firmware.
if [ ! -f "$CONF_JFFS" ] && [ -f "$CONF_ETC" ]; then
    mkdir -p /mnt/jffs 2>/dev/null
    if cp "$CONF_ETC" "$CONF_JFFS" 2>/dev/null; then
        echo "[get_avsp_bundle] seeded $CONF_JFFS (edit it to change the TFTP server)"
    fi
fi

CONF="$CONF_JFFS"
[ -f "$CONF" ] || CONF="$CONF_ETC"

# Read one "key = value" from the ini. PURE SHELL only -- this device's busybox
# (v1.13.3) has no head/tail/awk, and 'tr -d [:space:]' is NOT portable across
# busybox builds (some treat [:space:] as the literal chars [ : s p a c e ]).
# So trim with parameter expansion. Config format is "key = value" with spaces.
getconf() {
    [ -f "$CONF" ] || return 0
    while IFS= read -r line; do
        case "$line" in \#*) continue ;; esac
        case "$line" in *=*) ;; *) continue ;; esac
        k=${line%%=*}
        v=${line#*=}
        while [ "${k# }" != "$k" ]; do k=${k# }; done
        while [ "${k% }" != "$k" ]; do k=${k% }; done
        while [ "${v# }" != "$v" ]; do v=${v# }; done
        while [ "${v% }" != "$v" ]; do v=${v% }; done
        if [ "$k" = "$1" ]; then
            echo "$v"
            return 0
        fi
    done < "$CONF"
}

HOST=`getconf tftp_host`
PORT=`getconf tftp_port`
FILE=`getconf file`
[ -n "$PORT" ] || PORT=6969
[ -n "$FILE" ] || FILE=install.sh

if [ -z "$HOST" ]; then
    echo "No TFTP server set in $CONF."
    printf "TFTP server IP (your PC, where install.sh is served): "
    read HOST
fi
if [ -z "$HOST" ]; then
    echo "[get_avsp_bundle] no TFTP server given -- aborting."
    exit 1
fi

echo "[get_avsp_bundle] fetching $FILE from $HOST:$PORT ..."
cd /tmp || exit 1
rm -f /tmp/install.sh
tftp -g -r "$FILE" -l /tmp/install.sh "$HOST" "$PORT"
if [ ! -s /tmp/install.sh ]; then
    echo "[get_avsp_bundle] FAILED: /tmp/install.sh is empty."
    echo "[get_avsp_bundle] is the TFTP server up on $HOST:$PORT and serving '$FILE'?"
    exit 1
fi
echo "[get_avsp_bundle] got install.sh (`wc -c < /tmp/install.sh` bytes). Running it..."
sh /tmp/install.sh
'''

CONF_TMPL = """# get_avsp_bundle.conf -- where the helper fetches the avsp-onboard install.sh.
# This is the on-device, EDITABLE copy (seeded from the baked /etc default on
# first run). Change tftp_host here to re-point without reflashing firmware.
tftp_host = {host}
tftp_port = {port}
file = {file}
"""

SH_REL = "usr/sbin/get_avsp_bundle"
CONF_REL = "etc/get_avsp_bundle.conf"


def preflight(host, port=6969, filename="install.sh", timeout=3.0):
    """Probe a TFTP server the way the on-device helper will: send a read
    request for <filename> and classify the reply. Returns (status, message)
    where status is one of: ok | nofile | noserver | error. Lets the build
    tooling catch 'TFTP not serving install.sh' BEFORE you flash + go to the
    device (the exact failure this feature is meant to make painless)."""
    import socket
    import struct
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        s.sendto(b"\x00\x01" + filename.encode() + b"\x00octet\x00",
                 (host, int(port)))
        data, _ = s.recvfrom(1024)
        s.close()
        op = struct.unpack(">H", data[:2])[0]
        if op == 3:  # DATA block 1 -> file exists and is being served
            return ("ok", "TFTP at %s:%s is serving '%s' -- ready." % (host, port, filename))
        if op == 5:  # ERROR
            code = struct.unpack(">H", data[2:4])[0]
            msg = data[4:].split(b"\x00")[0].decode(errors="replace")
            if code == 1:
                return ("nofile", "TFTP server IS up at %s:%s, but it is NOT serving "
                        "'%s' (%s). Generate + serve it with runme.sh option 3 before "
                        "running get_avsp_bundle on the device." % (host, port, filename, msg))
            return ("error", "TFTP server at %s:%s returned error %d: %s" % (host, port, code, msg))
        return ("error", "Unexpected TFTP reply (opcode %d) from %s:%s" % (op, host, port))
    except socket.timeout:
        return ("noserver", "No TFTP server answered at %s:%s. Start one serving '%s' "
                "(runme.sh option 3) before running get_avsp_bundle on the device."
                % (host, port, filename))
    except Exception as e:
        return ("error", "TFTP probe to %s:%s failed: %s" % (host, port, e))


def describe():
    return ("get_avsp_bundle helper: bakes /usr/sbin/get_avsp_bundle + its .conf "
            "so the on-device install is one command (sh /usr/sbin/get_avsp_bundle) "
            "that TFTP-fetches and runs install.sh. TFTP server is baked in (and "
            "editable on-device at /mnt/jffs/get_avsp_bundle.conf), or it prompts.")


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def apply(tree_dir, tftp_host="", tftp_port=6969, file="install.sh", log=print):
    _write(os.path.join(tree_dir, SH_REL), SCRIPT)
    _write(os.path.join(tree_dir, CONF_REL),
           CONF_TMPL.format(host=tftp_host or "", port=tftp_port, file=file))
    log("[get_avsp_bundle] wrote /%s and /%s (tftp_host=%r)"
        % (SH_REL, CONF_REL, tftp_host or "(unset -- will prompt)"))

    # Register the two NEW files in MANIFEST.json -- pack is manifest-driven,
    # so a file only on disk would be silently dropped. Inherit uid/gid from an
    # existing rootfs file so ownership matches this firmware's convention.
    mpath = os.path.join(tree_dir, "MANIFEST.json")
    manifest = json.load(open(mpath))
    ref = manifest.get("etc/auto_run", {"uid": 0, "gid": 0})
    uid, gid = ref["uid"], ref["gid"]
    manifest[SH_REL] = {"mode": S_IFREG | 0o755, "uid": uid, "gid": gid}
    manifest[CONF_REL] = {"mode": S_IFREG | 0o644, "uid": uid, "gid": gid}
    with open(mpath, "w") as f:
        json.dump(manifest, f, indent=1)
    log("[get_avsp_bundle] registered both files in MANIFEST (mode 0755/0644, "
        "uid=%d gid=%d)" % (uid, gid))
