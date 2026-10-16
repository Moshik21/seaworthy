"""A build host that cannot be reached is a hosted-run bill waiting to happen."""
from . import REQUIRED, fail, ok, register, unknown
from ..util import first_line


@register("hosts.reachable", "the named build host answers, and the things the release needs are on it",
          {"host": ("str", REQUIRED, "a host name defined in the profile's 'hosts'"),
           "timeout_s": ("int", 5, "seconds to wait for each probe"),
           "tcp": ("str", "", "optional 'name:port' to test with a plain connection first"),
           "probes": ("list", [{"name": "answers", "argv": ["echo", "seaworthy-ok"], "expect": "seaworthy-ok"}],
                      "list of {name, argv, expect}: argv runs on the host and its output must contain expect")},
          cost="cheap-network", network=True)
def reachable(ctx, p):
    """Runs each probe through the host's runner (for example ssh with BatchMode). A timeout or refusal is a failure of
    this check, not an unknown: an unreachable host is exactly what it exists to catch."""
    host, lines, bad = p["host"], [], []
    ctx.host_spec(host)
    if p["tcp"]:
        res = ctx.tcp(p["tcp"], timeout=p["timeout_s"])
        lines.append("tcp %s: %s" % (p["tcp"], res))
        if res != "ok":
            return fail("%s did not accept a connection (%s)" % (p["tcp"], res), lines)
    for probe in p["probes"]:
        r = ctx.run(probe["argv"], host=host, net=True, timeout=p["timeout_s"])
        label = probe.get("name", " ".join(probe["argv"][:2]))
        if r.timed_out:
            bad.append("%s: timed out after %ss" % (label, p["timeout_s"]))
        elif r.missing:
            bad.append("%s: the runner program is missing" % label)
        elif not r.ok:
            bad.append("%s: exit %s %s" % (label, r.rc, first_line(r.err or r.out)))
        elif probe.get("expect") and probe["expect"] not in r.text:
            bad.append("%s: output lacks '%s'" % (label, probe["expect"]))
        else:
            lines.append("%s: ok" % label)
    if bad:
        return fail("; ".join(bad), lines)
    return ok("host %s answered %d probe(s)" % (host, len(p["probes"])), lines)
