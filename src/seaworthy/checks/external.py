"""Plug in any command. The contract is the same one node-problem-detector and pre-commit use."""
from . import REQUIRED, Result, register
from ..util import first_line

_CODES = {0: "pass", 1: "fail", 2: "unknown", 3: "skip"}


@register("external", "run any command and read its exit code",
          {"argv": ("list", REQUIRED, "the command as an argv array (never a shell string)"),
           "cwd": ("str", "", "directory to run in (default: the checkout)"),
           "env": ("list", [], "names of environment variables to pass through (values are never printed)"),
           "net": ("bool", False, "true when the command leaves this machine; a dry run then refuses it without a fixture")},
          cost="free")
def external(ctx, p):
    """Contract: exit 0 pass, 1 fail, 2 unknown, 3 skip; any other code is unknown. The last non-empty line on
    stdout is the evidence (a script's verdict is its last word); the lines before it, first first, become details. Use it to run the project's own release scripts in their own
    dry-run modes, so the check is the real thing and not an imitation. The engine still refuses hosted-dispatch argv."""
    r = ctx.run(p["argv"], net=p["net"], cwd=p["cwd"] or None, env=p["env"])
    if r.missing:
        return Result("fail", "%s is not installed" % p["argv"][0])
    if r.timed_out:
        return Result("unknown", "%s did not finish in time" % p["argv"][0])
    status = _CODES.get(r.rc, "unknown")
    lines = [ln for ln in r.out.splitlines() if ln.strip()]
    evidence = lines[-1].strip() if lines else first_line(r.err) or "exit %s" % r.rc
    if r.rc not in _CODES:
        evidence = "exit %s: %s" % (r.rc, evidence)
    return Result(status, evidence, lines[:-1][:5] + ([first_line(r.err)] if r.err and r.rc else []))
