"""Paid-job hygiene, read from the workflow files themselves. No YAML library: the few facts needed
(triggers, runner labels, timeouts, action refs, cancel-in-progress) are read by indentation."""
import glob
import os
import re

from . import REQUIRED, fail, ok, register, unknown, warn

_SHA = re.compile(r"^[0-9a-f]{40}$")


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def parse(text):
    """-> {'triggers': [...], 'jobs': {id: {...}}, 'cancel': [True...]}"""
    lines = [l.rstrip("\n") for l in text.splitlines()]
    wf = {"triggers": [], "jobs": {}, "cancel": [], "uses": []}
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        m = re.match(r"^(?:on|'on'|\"on\"):\s*(.*)$", line)
        if m:
            rest = re.sub(r"\s*#.*$", "", m.group(1)).strip()
            if rest:
                wf["triggers"] += [t.strip(" '\"") for t in rest.strip("[]").split(",") if t.strip()]
            else:
                j = i + 1
                while j < n and (not lines[j].strip() or _indent(lines[j]) > 0 or lines[j].lstrip().startswith("#")):
                    mm = re.match(r"^  ([A-Za-z_]+):", lines[j]) or re.match(r"^  - ([A-Za-z_]+)", lines[j])
                    if mm:
                        wf["triggers"].append(mm.group(1))
                    j += 1
                i = j - 1
        elif re.match(r"^jobs:\s*$", line):
            j, jid, jind = i + 1, None, None
            while j < n and (not lines[j].strip() or _indent(lines[j]) > 0 or lines[j].lstrip().startswith("#")):
                l = lines[j]
                if l.strip() and not l.lstrip().startswith("#"):
                    ind = _indent(l)
                    if jind is None:
                        jind = ind
                    if ind == jind:
                        mm = re.match(r"\s*([A-Za-z0-9_-]+):", l)
                        if mm:
                            jid = mm.group(1)
                            wf["jobs"][jid] = {"runs_on": None, "timeout": None, "reusable": False, "if": None}
                    elif jid and ind == jind + 2:
                        mm = re.match(r"\s*(runs-on|timeout-minutes|uses|if):\s*(.*?)\s*(?:#.*)?$", l)
                        if mm and mm.group(1) == "if":
                            val = mm.group(2)
                            if len(val) > 1 and val[0] == val[-1] and val[0] in "'\"":
                                val = val[1:-1]
                            wf["jobs"][jid]["if"] = val
                        elif mm:
                            key, val = mm.group(1), mm.group(2).strip("'\"")
                            if key == "runs-on":
                                wf["jobs"][jid]["runs_on"] = val.strip("[]")
                            elif key == "timeout-minutes":
                                wf["jobs"][jid]["timeout"] = val
                            else:
                                wf["jobs"][jid]["reusable"] = True
                j += 1
            i = j - 1
        i += 1
    for l in lines:
        mm = re.search(r"cancel-in-progress:\s*(\S+)", l)
        if mm:
            wf["cancel"].append(mm.group(1).strip("'\""))
        mm = re.match(r"\s*-?\s*uses:\s*([^\s#]+)", l)
        if mm and not mm.group(1).startswith(("./", "docker://")) and "@" in mm.group(1):
            wf["uses"].append(mm.group(1).strip("'\""))
    return wf


_TOKEN = re.compile(r"\s*(?:(&&|\|\||==|!=|!|\(|\)|,)|'((?:[^']|'')*)'|([A-Za-z_][A-Za-z0-9_.]*))")


def _tokens(text):
    out, pos = [], 0
    while pos < len(text):
        if not text[pos:].strip():
            break
        m = _TOKEN.match(text, pos)
        if not m:
            return None
        pos = m.end()
        out.append(("op", m.group(1)) if m.group(1) else ("str", m.group(2).replace("''", "'")) if m.group(2) is not None
                   else ("name", m.group(3)))
    return out


def job_runs(cond, ref):
    """Does a job's `if:` condition hold for this ref? True, False, or None when it cannot be told. Only the plain
    ref conditions a release workflow uses are understood: github.ref / github.ref_name / github.ref_type compared with
    == or !=, contains / startsWith / endsWith over them, and ! && || ( ). Anything else (needs.*, inputs.*, event
    payloads, functions) is unknown, and unknown never downgrades a finding. `ref` is a full ref (refs/tags/v1.2.3)
    or a bare tag name."""
    if not cond or not ref:
        return None
    cond = cond.strip()
    if cond.startswith("${{") and cond.endswith("}}"):
        cond = cond[3:-2]
    toks = _tokens(cond)
    if toks is None:
        return None
    full = ref if ref.startswith("refs/") else "refs/tags/" + ref
    known = {"github.ref": full, "github.ref_name": full.split("/", 2)[2], "github.ref_type": "tag" if full.startswith("refs/tags/") else "branch"}
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else (None, None)

    def eat(kind=None, val=None):
        if pos[0] >= len(toks):
            raise ValueError("unexpected end")
        t = peek()
        if (kind and t[0] != kind) or (val is not None and t[1] != val):
            raise ValueError("unexpected token")
        pos[0] += 1
        return t

    def operand():
        """-> (str value or None when unknown)"""
        t = eat()
        if t[0] == "str":
            return t[1].lower()
        if t[0] == "name" and t[1] in known:
            return known[t[1]].lower()
        if t[0] == "name":
            if peek() == ("op", "("):
                depth = 0                      # an unknown function call: swallow it, the value is unknown
                while True:
                    k = eat()
                    depth += k == ("op", "(")
                    depth -= k == ("op", ")")
                    if depth == 0:
                        return None
            return None
        raise ValueError("unexpected token")

    def tri_not(v):
        return None if v is None else (not v)

    def atom():
        t = peek()
        if t == ("op", "!"):
            eat()
            return tri_not(atom())
        if t == ("op", "("):
            eat()
            v = expr_or()
            eat("op", ")")
            return v
        if t[0] == "name" and t[1] in ("contains", "startsWith", "endsWith") and toks[pos[0] + 1:pos[0] + 2] == [("op", "(")]:
            fn = eat()[1]
            eat("op", "(")
            a = operand()
            eat("op", ",")
            b = operand()
            eat("op", ")")
            if a is None or b is None:
                return None
            return b in a if fn == "contains" else a.startswith(b) if fn == "startsWith" else a.endswith(b)
        a = operand()
        if peek() in (("op", "=="), ("op", "!=")):
            op = eat()[1]
            b = operand()
            if a is None or b is None:
                return None
            return (a == b) if op == "==" else (a != b)
        return None                            # a bare name or string used as a boolean: not understood

    def expr_and():
        v = atom()
        while peek() == ("op", "&&"):
            eat()
            w = atom()
            v = False if (v is False or w is False) else None if (v is None or w is None) else True
        return v

    def expr_or():
        v = expr_and()
        while peek() == ("op", "||"):
            eat()
            w = expr_and()
            v = True if (v is True or w is True) else None if (v is None or w is None) else False
        return v

    try:
        v = expr_or()
        if pos[0] != len(toks):
            return None
    except (ValueError, IndexError):
        return None
    return v


def _family(runs_on):
    r = (runs_on or "").lower()
    return "windows" if "windows" in r else "macos" if "macos" in r else "linux" if "ubuntu" in r else "other"


@register("workflow.hygiene", "paid jobs are bounded: explicit timeouts, no cancelling releases, pinned actions, no ordinary triggers",
          {"workflows": ("list", [".github/workflows/*.yml"], "workflow globs held to the timeout, cancel and pinning rules"),
           "ordinary_workflows": ("list", [], "globs scanned only for the ordinary-trigger rule (default: the same as workflows)"),
           "ref": ("str", "", "the ref being released (a tag like v1.2.3 or refs/tags/v1.2.3); a job whose plain `if:` ref condition is "
                              "false for it does not run, so a missing timeout there is a warning that names the condition"),
           "allow_no_timeout": ("list", [], "'file.yml:job' entries exempt from the timeout rule (say why in the profile, for example jobs gated to a path that never runs)"),
           "release_workflows": ("list", [], "file names of the release workflows (held to the strictest rules)"),
           "require_timeout": ("bool", True, "every job sets timeout-minutes (GitHub's default is 360)"),
           "max_timeout": ("dict", {}, "largest allowed timeout-minutes per runner family: linux, windows, macos"),
           "pin_actions": ("str", "warn", "'off', 'warn' or 'fail': third-party actions pinned to a full commit SHA"),
           "ordinary_triggers": ("str", "warn", "'off', 'warn' or 'fail': non-release workflows that run on push, pull_request or schedule"),
           "allow_ordinary_in": ("list", [], "file names exempt from the ordinary-trigger rule")},
          cost="free")
def hygiene(ctx, p):
    """A hung macOS job at the default 360-minute timeout costs about 10 times a Linux one; a release workflow that
    cancels in progress can strand a half-published release; a tag like v1 can be moved under you. This reads the
    workflow files at the checked-out SHA and reports each problem by file and job. A job that has no timeout but whose
    plain `if:` condition on the ref (github.ref, github.ref_name, contains/startsWith/endsWith, == and !=, && || !) is
    false for the `ref` being released never runs for it: that is a warning naming the condition, not a failure.
    Anything the reader cannot judge (needs.*, inputs.*, events, no `ref` given) keeps failing."""
    def expand(globs):
        found = []
        for pat in globs:
            found += sorted(glob.glob(os.path.join(ctx.repo, pat)))
        return found

    strict = expand(p["workflows"])
    ordinary = set(expand(p["ordinary_workflows"] or p["workflows"]))
    files = sorted(set(strict) | ordinary)
    if not files:
        return unknown("no workflow files matched", p["workflows"])
    hard, soft, lines = [], [], []
    for path in files:
        name = os.path.basename(path)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            wf = parse(fh.read())
        release = name in p["release_workflows"]
        lines.append("%s: %d job(s), triggers %s%s" % (name, len(wf["jobs"]), ",".join(wf["triggers"]) or "none",
                                                       "" if path in strict else " (trigger rule only)"))
        if path in strict:
            for jid, job in sorted(wf["jobs"].items()):
                if job["reusable"] or "%s:%s" % (name, jid) in p["allow_no_timeout"]:
                    continue
                if p["require_timeout"] and job["timeout"] is None:
                    runs = job_runs(job["if"], p["ref"]) if job["if"] else None
                    if runs is False:
                        soft.append("%s job %s has no timeout-minutes (default 360), but it does not run for %s: its condition `if: %s` is false"
                                    % (name, jid, p["ref"], job["if"]))
                    elif runs is True:
                        hard.append("%s job %s has no timeout-minutes (default 360) and it runs for %s (`if: %s` is true)"
                                    % (name, jid, p["ref"], job["if"]))
                    elif job["if"]:
                        hard.append("%s job %s has no timeout-minutes (default 360); its `if: %s` could not be judged%s" % (
                            name, jid, job["if"], " for %s" % p["ref"] if p["ref"] else " (give the ref being released as `ref`)"))
                    else:
                        hard.append("%s job %s has no timeout-minutes (default 360)" % (name, jid))
                elif job["timeout"] and job["timeout"].isdigit():
                    cap = p["max_timeout"].get(_family(job["runs_on"]))
                    if cap and int(job["timeout"]) > cap:
                        hard.append("%s job %s allows %s min; the cap for %s is %s" % (name, jid, job["timeout"], _family(job["runs_on"]), cap))
            if release and any(c.lower() == "true" for c in wf["cancel"]):
                hard.append("%s is a release workflow but sets cancel-in-progress: true" % name)
            if p["pin_actions"] != "off":
                loose = sorted({u for u in wf["uses"] if not _SHA.match(u.split("@", 1)[1])})
                if loose:
                    (hard if p["pin_actions"] == "fail" else soft).append(
                        "%s: %d action ref(s) not pinned to a commit SHA (e.g. %s)" % (name, len(loose), loose[0]))
        if path in ordinary and p["ordinary_triggers"] != "off" and not release and name not in p["allow_ordinary_in"]:
            hits = [t for t in wf["triggers"] if t in ("push", "pull_request", "pull_request_target", "schedule")]
            if hits:
                (hard if p["ordinary_triggers"] == "fail" else soft).append(
                    "%s runs on %s: hosted minutes on every such event unless the workflow is disabled in settings" % (name, "/".join(hits)))
    if hard:
        return fail("%d paid-job problem(s): %s" % (len(hard), hard[0]), hard + soft + lines,
                    next="fix the job(s) named (timeout-minutes, cancel-in-progress, action pins) and re-run; a job that does not run for "
                         "this release is only excused when `ref` is given and its `if:` is a plain ref condition")
    if soft:
        return warn("%d advisory finding(s): %s" % (len(soft), soft[0]), soft + lines)
    return ok("%d workflow file(s) bounded" % len(files), lines)
