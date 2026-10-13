"""Feature: enhanced-build marker on the admin web UI.

Shows, on the appliance's own admin login page, that this unit is running
a CUSTOM ("Enhanced") build rather than stock Avocent firmware -- so a
modified unit is obvious at a glance and never gets mistaken for a genuine
vendor image during support/troubleshooting.

Reliable surface, by deliberate choice: this edits the rootfs HTML page
(webpages/web/dsview/login.html) to append a free-text marker right after
the version the page already shows. It does NOT touch the binary version
field at .fl offset 0x78 -- main_app reformats that field as zero-padded
dotted numbers (03.07.02.08), i.e. it parses it as d.d.d.d, so arbitrary
text there would render garbled and it is the field most likely to be
version-gated (firmware-upgrade acceptance, JNLP). The page edit has none
of that risk and can say anything.

Result on the login page, e.g.:
    Appliance firmware version 03.07.02.08 -Enhanced build 4d4ff56

build_id is supplied by build_kvm_firmware.py (either the user's --build-id
or an auto-derived short hash of the build inputs).
"""

import os

# The page the version is shown on, and the token main_app substitutes the
# real version into at render time. We insert our marker right after it.
DEFAULT_PAGES = ["webpages/web/dsview/login.html"]
VERSION_TOKEN = "$$MIB_APP_VERSION$$"
MARKER_PREFIX = "-Enhanced build"


def describe():
    return ("Enhanced-build marker: appends '-Enhanced build <id>' after the "
            "firmware version shown on the admin login page, so a customized "
            "unit is obvious. Rootfs HTML edit only -- never touches the "
            "version-gated binary version field.")


def apply(tree_dir, build_id, pages=None, log=print):
    if not build_id:
        raise RuntimeError("enhanced_build.apply() needs a build_id")
    marker = " %s %s" % (MARKER_PREFIX, build_id)
    pages = pages or DEFAULT_PAGES
    touched = 0
    for rel in pages:
        path = os.path.join(tree_dir, rel)
        if not os.path.exists(path):
            log("[enhanced_build] %s not found -- skipping" % rel)
            continue
        # HTML here isn't guaranteed UTF-8; latin-1 round-trips any byte.
        content = open(path, encoding="latin-1").read()
        if MARKER_PREFIX in content:
            log("[enhanced_build] %s already marked -- leaving as-is" % rel)
            touched += 1
            continue
        if VERSION_TOKEN not in content:
            log("[enhanced_build] %s has no %s -- skipping" % (rel, VERSION_TOKEN))
            continue
        content = content.replace(VERSION_TOKEN, VERSION_TOKEN + marker, 1)
        with open(path, "w", encoding="latin-1") as f:
            f.write(content)
        log("[enhanced_build] marked %s: '...%s%s'" % (rel, VERSION_TOKEN, marker))
        touched += 1
    if touched == 0:
        raise RuntimeError("enhanced_build: no page was marked -- unexpected "
                           "rootfs layout (expected %s)" % ", ".join(pages))
