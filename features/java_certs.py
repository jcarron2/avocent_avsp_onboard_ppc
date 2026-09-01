"""Feature: fix the Java Web Start KVM client (expired certs + dead ciphers).

Stock 3.7.2.8 ships with three separate things that make the Java client
unusable on any current JRE:

1. All 12 Java Web Start jars are signed with a certificate that expired
   2019-02-17 -- any current JRE hard-blocks launch.
2. The admin HTTPS server's certificate expired in 2025 and has no
   subjectAltName -- current browsers/JREs reject it outright.
3. The AVSP/video port's *separate* certificate (global_appliance_cert)
   is MD5-signed and expired since 2010 -- Java rejects MD5-signed certs
   unconditionally, below the level a "trust everything" TrustManager can
   override. Separately, the real client's hardcoded TLS cipher list is
   7/8 dead names on Java 8+ (RC4/DES/3DES/NULL/export-grade); only
   TLS_RSA_WITH_AES_128_CBC_SHA (the 8th) still works, and even that's
   gone on Java 11+ -- so Java 8 is the only viable target JRE regardless
   of what this feature fixes.

This feature generates fresh, long-dated self-signed certs (~20yr
validity -- these were never meant to be "trusted" by a CA chain, just
non-expired, matching this device's own original trust model) and a
fresh code-signing keystore, EVERY TIME IT RUNS -- it does not reuse or
embed any of the original project's private keys, so this stays safe to
hand out as a portable tool. Only the HTTPS admin cert's SAN needs to
know the target IP; the AVSP video cert has no IP-specific fields at all.

Requires `openssl` and a JDK 8+ (`keytool`, `jarsigner`, `jar`) on PATH.

Least-tested feature in this tool: the underlying recipe (fresh
self-signed certs + jar re-sign + Stingray.class cipher patch) is
proven -- the original project validated an equivalent build against
real hardware with a real Java 8 client. This exact reimplementation,
generating fresh throwaway keys instead of reusing the original
project's saved ones, has only been smoke-tested locally (jarsigner
-verify passes, classfile self-checks pass) -- verify against your own
target before relying on it. See release/README.md.
"""
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # release/ root, for patch_cipher_list
import patch_cipher_list  # noqa: E402

VALIDITY_DAYS = 7300  # ~20 years

HTTPS_SAN_CNF = """[req]
distinguished_name = dn
x509_extensions = v3_req
prompt = no

[dn]
C = US
O = Avocent Corporation
CN = Avocent DSR2030 Appliance

[v3_req]
basicConstraints = CA:TRUE
keyUsage = critical, digitalSignature, keyCertSign, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
IP.1 = {ip}
DNS.1 = avocent-dsr2030
DNS.2 = localhost
IP.2 = 127.0.0.1
"""

AVSP_CNF = """[req]
distinguished_name = dn
x509_extensions = v3_req
prompt = no

[dn]
C = US
ST = Alabama
L = Huntsville
O = Avocent Corp.
OU = DSView
CN = Appliance

[v3_req]
basicConstraints = CA:TRUE
keyUsage = critical, digitalSignature, keyCertSign, keyEncipherment
extendedKeyUsage = serverAuth, clientAuth
"""

JARS = [
    "avctMacOSXLib.jar", "avctWin32Lib.jar", "avctLinuxLib.jar",
    "avmSolarisLib.jar", "avctVideo.jar", "avmLinuxLib.jar",
    "avmMacOSXLib.jar", "jpcscso.jar", "jpcscdll.jar",
    "avctSolarisLib.jar", "avctVM.jar", "avmWin32Lib.jar",
]

STINGRAY_ENTRY = "com/avocent/video/Stingray.class"


def describe():
    return ("Java Web Start client: fresh self-signed HTTPS admin cert (SAN "
            "for your chosen IP), fresh AVSP video cert, fresh code-signing "
            "key, all 12 jars re-signed, avctVideo.jar's dead TLS cipher "
            "list patched to working AES suites. Requires openssl + a JDK "
            "(keytool/jarsigner/jar) on PATH. Only Java 8 can run the "
            "resulting client -- this device can't negotiate TLS with any "
            "newer JRE regardless of this fix.")


def _run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}")
    return r


def _gen_self_signed(workdir, name, cnf_text, log):
    cnf_path = os.path.join(workdir, f"{name}.cnf")
    open(cnf_path, "w").write(cnf_text)
    key_p8 = os.path.join(workdir, f"{name}_key_p8.pem")
    key_pkcs1 = os.path.join(workdir, f"{name}_key.pem")
    cert = os.path.join(workdir, f"{name}_cert.pem")
    _run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
          "-keyout", key_p8, "-out", cert,
          "-days", str(VALIDITY_DAYS), "-config", cnf_path])
    # device expects traditional PKCS1 "BEGIN RSA PRIVATE KEY", not the
    # PKCS8 "BEGIN PRIVATE KEY" openssl req produces by default
    _run(["openssl", "rsa", "-in", key_p8, "-out", key_pkcs1])
    log(f"[java_certs] generated {name}: cert={cert} key={key_pkcs1}")
    return cert, key_pkcs1


def _strip_signature_and_repackage(jar_path, out_path, workdir, jar_tool="jar", replace_entry=None, replace_bytes=None):
    """Extract, drop any existing signature (META-INF/*.SF/*.RSA/*.DSA) and
    per-entry manifest digests (jarsigner regenerates these correctly at
    sign time), optionally swap one entry's bytes, repackage as a plain
    (unsigned) jar with `jar cfm` preserving the manifest's non-digest
    global attributes."""
    extract_dir = os.path.join(workdir, "extract_" + os.path.basename(jar_path))
    os.makedirs(extract_dir, exist_ok=True)
    with zipfile.ZipFile(jar_path) as zf:
        zf.extractall(extract_dir)

    meta_inf = os.path.join(extract_dir, "META-INF")
    manifest_path = os.path.join(meta_inf, "MANIFEST.MF")
    global_attrs = ["Manifest-Version: 1.0\n"]
    if os.path.exists(manifest_path):
        # keep only the first (global) section -- entry-specific sections
        # are separated by a blank line and start with "Name: "
        text = open(manifest_path, encoding="utf-8", errors="replace").read()
        first_section = text.split("\n\n", 1)[0]
        kept = [ln + "\n" for ln in first_section.splitlines()
                if not ln.startswith("Name:") and ":" in ln]
        if kept:
            global_attrs = kept
    for fn in os.listdir(meta_inf) if os.path.isdir(meta_inf) else []:
        if fn != "MANIFEST.MF":
            os.remove(os.path.join(meta_inf, fn))  # old .SF/.RSA/.DSA

    if replace_entry:
        target = os.path.join(extract_dir, *replace_entry.split("/"))
        with open(target, "wb") as f:
            f.write(replace_bytes)

    stripped_manifest = os.path.join(workdir, "MANIFEST_" + os.path.basename(jar_path) + ".mf")
    with open(stripped_manifest, "w", encoding="utf-8") as f:
        f.writelines(global_attrs)
        if not global_attrs[-1].endswith("\n"):
            f.write("\n")

    if os.path.exists(out_path):
        os.remove(out_path)
    _run([jar_tool, "cfm", out_path, stripped_manifest, "-C", extract_dir, "."])


def apply(tree_dir, ip, admin_user="admin", log=print, jdk_bin=None):
    """ip: the target device's admin IP, baked into the HTTPS cert's SAN."""
    def tool(name):
        return os.path.join(jdk_bin, name) if jdk_bin else name

    workdir = tempfile.mkdtemp(prefix="java_certs_")
    try:
        log(f"[java_certs] working dir: {workdir}")

        https_cert, https_key = _gen_self_signed(workdir, "https", HTTPS_SAN_CNF.format(ip=ip), log)
        avsp_cert, avsp_key = _gen_self_signed(workdir, "avsp", AVSP_CNF, log)

        webpages_linux = f"{tree_dir}/webpages/LINUX"
        os.makedirs(f"{webpages_linux}/certs", exist_ok=True)
        server_pem = open(https_cert).read() + open(https_key).read()
        open(f"{webpages_linux}/server.pem", "w").write(server_pem)
        shutil.copy(https_cert, f"{webpages_linux}/certs/cacert.pem")
        shutil.copy(https_key, f"{webpages_linux}/certs/cakey.pem")
        log(f"[java_certs] wrote HTTPS admin cert (SAN IP={ip}) to webpages/LINUX/{{server.pem,certs/}}")

        shutil.copy(avsp_cert, f"{tree_dir}/global_appliance_cert.txt")
        shutil.copy(avsp_key, f"{tree_dir}/global_appliance_key.txt")
        log("[java_certs] wrote AVSP video cert to global_appliance_{cert,key}.txt")

        keystore = os.path.join(workdir, "codesigning.p12")
        _run([tool("keytool"), "-genkeypair", "-alias", "avocent", "-keyalg", "RSA",
              "-keysize", "2048", "-validity", str(VALIDITY_DAYS),
              "-keystore", keystore, "-storetype", "PKCS12",
              "-storepass", "changeit", "-keypass", "changeit",
              "-dname", "CN=Avocent, OU=DSView, O=Avocent Corp, C=US"])
        log("[java_certs] generated fresh code-signing keystore")

        jar_tar_path = f"{tree_dir}/jar.tar"
        jars_dir = os.path.join(workdir, "jars")
        os.makedirs(jars_dir, exist_ok=True)
        with tarfile.open(jar_tar_path) as tf:
            tf.extractall(jars_dir)

        for jar_name in JARS:
            jar_path = os.path.join(jars_dir, "webstart", jar_name)
            unsigned_path = os.path.join(workdir, "unsigned_" + jar_name)

            if jar_name == "avctVideo.jar":
                with zipfile.ZipFile(jar_path) as zf:
                    classfile = zf.read(STINGRAY_ENTRY)
                patched = patch_cipher_list.patch_classfile(classfile)
                _strip_signature_and_repackage(
                    jar_path, unsigned_path, workdir, jar_tool=tool("jar"),
                    replace_entry=STINGRAY_ENTRY, replace_bytes=patched)
                log(f"[java_certs] {jar_name}: cipher list patched + repackaged")
            else:
                _strip_signature_and_repackage(jar_path, unsigned_path, workdir, jar_tool=tool("jar"))

            _run([tool("jarsigner"), "-keystore", keystore, "-storetype", "PKCS12",
                  "-storepass", "changeit", "-sigalg", "SHA256withRSA",
                  "-digestalg", "SHA-256", "-signedjar", jar_path,
                  unsigned_path, "avocent"])
            verify = _run([tool("jarsigner"), "-verify", jar_path])
            if "jar verified" not in verify.stdout and "jar verified" not in verify.stderr:
                raise RuntimeError(f"{jar_name}: jarsigner -verify did not report 'jar verified'")
            log(f"[java_certs] {jar_name}: re-signed and verified")

        if os.path.exists(jar_tar_path):
            os.remove(jar_tar_path)
        with tarfile.open(jar_tar_path, "w") as tf:
            tf.add(os.path.join(jars_dir, "webstart"), arcname="webstart")
        log(f"[java_certs] rebuilt {jar_tar_path}")

    finally:
        shutil.rmtree(workdir, ignore_errors=True)
