"""Tool pins: the versions the release was proven with."""
import fnmatch
import re

from . import REQUIRED, fail, ok, register, unknown
from ..util import first_line, version_tuple

_DEFAULT_PATTERN = r"(\d+(?:\.\d+)+)"


@register("tools.pins", "each named tool reports exactly the pinned version",
          {"tools": ("list", REQUIRED, "list of {name, argv, expect | min, pattern?, host?}: argv prints the version; "
                                       "expect is the exact version or a glob like 24.*; min is a floor like 7.4")},
          cost="free")
def pins(ctx, p):
    """Runs each tool's version command (on this machine, or on the named host) and compares the first version-shaped
    token with the pin. A tool that is not installed fails; one that cannot be asked in time is unknown. A command that
    exits non-zero is reported as "exited N: first line" (fail here, unknown on a remote host, where the failure may be
    the host's) and its output is never read for a version. The default pattern needs a dotted version (1.2 or 1.2.3);
    give `pattern` for a tool that prints a bare integer."""
    bad, unk, lines = [], [], []
    for t in p["tools"]:
        name, argv = t["name"], t["argv"]
        r = ctx.run(argv, host=t.get("host"), timeout=t.get("timeout_s", 8))
        want = t.get("expect") or (">=" + t["min"] if t.get("min") else "?")
        if r.missing:
            bad.append("%s is not installed (want %s)" % (name, want))
            continue
        if r.timed_out:
            unk.append("%s did not answer in time" % name)
            continue
        if r.rc != 0:
            # A failed command's output is an error message, never a version: do not look for one in it.
            msg = "%s exited %s: %s" % (name, r.rc, first_line(r.text) or "no output")
            if (t.get("host") or ctx.default_host) != "local":
                unk.append(msg)
            else:
                bad.append(msg)
            continue
        m = re.search(t.get("pattern", _DEFAULT_PATTERN), r.text)
        if not m:
            unk.append("%s printed no version: %s" % (name, first_line(r.text) or "(empty)"))
            continue
        got = m.group(1) if m.groups() else m.group(0)
        if t.get("expect"):
            good = fnmatch.fnmatchcase(got, t["expect"])
        else:
            good = version_tuple(got) >= version_tuple(t["min"])
        lines.append("%s %s (want %s)%s" % (name, got, want, "" if good else "  MISMATCH"))
        if not good:
            bad.append("%s is %s, pinned %s" % (name, got, want))
    if bad:
        return fail("; ".join(bad), lines + unk)
    if unk:
        return unknown("; ".join(unk), lines)
    return ok("%d tool(s) match their pins" % len(lines), lines)
