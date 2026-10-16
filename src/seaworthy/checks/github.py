"""Money checks: the hosted-run budget, and the runner image under a label you thought was frozen.

Both read GitHub through `gh api` GET requests only. In a dry run those calls are answered from fixtures and
never made; without a fixture the check is reported 'not run'.
"""
import json
import os
import re

from . import REQUIRED, cannot_check, fail, ok, register, unknown, warn
from .. import IMAGES_SCHEMA, ledger
from ..util import SpecError, load_json, first_line

# runs-on label -> tag prefix used by actions/runner-images releases. Best effort; override with tag_prefixes.
IMAGE_TAGS = {
    "ubuntu-24.04": "ubuntu24", "ubuntu-latest": "ubuntu24", "ubuntu-22.04": "ubuntu22",
    "windows-2022": "win22", "windows-2025": "win25", "windows-latest": "win25",
    "macos-14": "macos-14", "macos-15": "macos-15", "macos-latest": "macos-15",
    "macos-14-arm64": "macos-14-arm64", "macos-15-arm64": "macos-15-arm64",
}
MOVING = re.compile(r"-latest$")


def images_path(state_dir):
    return os.path.join(state_dir, "images.json")


def read_images(state_dir):
    path = images_path(state_dir)
    if not os.path.exists(path):
        return {}
    doc = load_json(path, "last-good image record")
    if doc.get("schema") != IMAGES_SCHEMA[0]:
        raise SpecError("%s is not a %s file" % (path, IMAGES_SCHEMA[0]))
    return doc.get("labels", {})


def image_key(version):
    """'20260908.1.0' -> (20260908, 1): the date and revision; the third part is the patch of the tooling."""
    m = re.match(r"(\d{8})\.(\d+)", version or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def workflow_labels(ctx, patterns):
    import glob
    labels = {}
    for pat in patterns:
        for path in sorted(glob.glob(os.path.join(ctx.repo, pat))):
            rel = os.path.relpath(path, ctx.repo).replace(os.sep, "/")
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = re.match(r"\s*runs-on:\s*(.+?)\s*(#.*)?$", line)
                    if not m:
                        continue
                    val = m.group(1).strip().strip("[]")
                    for item in val.split(","):
                        item = item.strip().strip("'\"")
                        if item and "${{" not in item and item != "self-hosted":
                            labels.setdefault(item, set()).add(rel)
    return labels


def gh_json(ctx, path):
    r = ctx.run(["gh", "api", path], net=True, timeout=20)
    if r.missing:
        return None, "cannot check here: gh is not installed"
    if r.timed_out:
        return None, "GitHub did not answer in time"
    if not r.ok:
        return None, "gh api %s failed: %s" % (path.split("?")[0], first_line(r.err or r.out) or "exit %s" % r.rc)
    try:
        return json.loads(r.out), None
    except ValueError:
        return None, "gh api %s returned something that is not JSON" % path.split("?")[0]


@register("github.runner_image", "the hosted runner image behind each label has not moved since the last good run",
          {"mode": ("str", "both", "'drift' (image version changed: warn), 'migration' (a label is being migrated or retired: fail), or 'both'"),
           "workflows": ("list", [".github/workflows/*.yml"], "workflow globs whose runs-on labels are checked"),
           "labels": ("list", [], "labels to check instead of reading them from the workflows"),
           "forbid_latest": ("bool", False, "fail when a workflow uses a moving '*-latest' label"),
           "tag_prefixes": ("dict", {}, "label -> runner-images tag prefix, for labels this tool does not know"),
           "releases_repo": ("str", "actions/runner-images", "repository whose releases and announcements are read")},
          cost="cheap-network", network=True)
def runner_image(ctx, p):
    """Pinning a runner label does not freeze the image behind it: images are rebuilt about weekly and labels are
    migrated to newer operating systems. This reads the newest image release and the open announcements for each label
    in use, and compares with the image version recorded after the last good hosted run (`seaworthy image record`).
    A changed image is a warning; a label named in an open migration or retirement announcement is a failure; no
    last-good record is 'unknown', never a pass. Read-only GitHub requests; fixtures answer them in a dry run."""
    labels = {l: {"files": []} for l in p["labels"]} if p["labels"] else {
        l: {"files": sorted(f)} for l, f in workflow_labels(ctx, p["workflows"]).items()}
    if not labels:
        return ok("no hosted runner labels in the checked workflows", ["nothing to drift"])
    prefixes = dict(IMAGE_TAGS)
    prefixes.update(p["tag_prefixes"])
    good = read_images(ctx.state_dir) if ctx.state_dir else {}
    lines, status_bits = [], []
    repo = p["releases_repo"]
    releases = None
    if p["mode"] in ("drift", "both"):
        releases, err = gh_json(ctx, "repos/%s/releases?per_page=60" % repo)
        if err:
            return unknown("cannot read image releases", [err])
    announcements = None
    if p["mode"] in ("migration", "both"):
        announcements, err = gh_json(ctx, "repos/%s/issues?labels=Announcement&state=open&per_page=50" % repo)
        if err:
            return unknown("cannot read image announcements", [err])
    worst = "pass"
    order = {"pass": 0, "warn": 1, "unknown": 2, "fail": 3}

    def bump(level, line):
        nonlocal worst
        lines.append(line)
        status_bits.append((level, line))
        if order[level] > order[worst]:
            worst = level

    for label in sorted(labels):
        if MOVING.search(label) and p["forbid_latest"]:
            bump("fail", "%s is a moving label; pin an explicit image such as windows-2022" % label)
        prefix = prefixes.get(label)
        if releases is not None:
            if not prefix:
                bump("unknown", "%s: no known image tag prefix (add it to tag_prefixes)" % label)
            else:
                newest = None
                for rel in releases:
                    m = re.match(r"%s/(\d{8}\.\d+)" % re.escape(prefix), rel.get("tag_name", ""))
                    if m and (newest is None or image_key(m.group(1)) > image_key(newest)):
                        newest = m.group(1)
                rec = good.get(label)
                if newest is None:
                    bump("unknown", "%s: no %s/* image release in the newest 60 releases" % (label, prefix))
                elif not rec:
                    bump("unknown", "%s: newest image %s; no last-good image recorded (run `seaworthy image record` after a good run)" % (label, newest))
                elif image_key(rec["version"]) is None:
                    bump("unknown", "%s: recorded version '%s' is not date-shaped" % (label, rec["version"]))
                elif image_key(rec["version"]) < image_key(newest):
                    bump("warn", "%s: image moved %s -> %s since the last good run" % (label, rec["version"], newest))
                else:
                    bump("pass", "%s: image %s is current (last good %s)" % (label, newest, rec["version"]))
        if announcements is not None:
            names = {label, label.replace("-", " ")}
            for issue in announcements:
                text = ((issue.get("title") or "") + " " + (issue.get("body") or "")[:600]).lower()
                if any(n in text for n in names) and re.search(r"migrat|deprecat|retir|remov|sunset|will (?:be )?(?:updat|chang)", text):
                    bump("fail", "%s: open announcement '%s' %s" % (label, (issue.get("title") or "")[:90], issue.get("html_url", "")))
    summary = {"pass": {"drift": "every label's image matches the last good run",
                        "migration": "no open announcement names a label in use"}.get(
                            p["mode"], "images match the last good run and no announcement names a label in use"),
               "warn": "a runner image moved since the last good run",
               "unknown": "image state could not be established",
               "fail": "a runner label is being migrated or is not pinned"}[worst]
    res = {"pass": ok, "warn": warn, "unknown": unknown, "fail": fail}[worst]
    return res(summary, lines)


def _budget(ctx):
    b = ctx.profile.get("budget") or {}
    if b.get("cap") is None and b.get("max_runs") is None:
        raise SpecError("the profile has no budget block (budget.cap and/or budget.max_runs)")
    return b


@register("budget.hosted_spend", "the hosted-run budget and run cap leave room for the planned runs",
          {"planned": ("list", [], "planned hosted runs as {name, os, timeout_min}; each counts as one run at worst case (timeout x rate)"),
           "warn_at": ("float", 0.8, "warn when spent plus projected passes this fraction of the cap"),
           "usage_api": ("str", "off", "'off', 'advisory' (cross-check against GitHub billing usage, note only) or 'required' (unreadable usage is unknown)"),
           "usage_user": ("str", "", "account whose billing usage is read when usage_api is not off")},
          cost="free")
def hosted_spend(ctx, p):
    """Sums the ledger (this tool's own record of hosted runs, estimated minutes times rate, unfinished runs at their
    timeout) over the budget window, adds the worst case of the planned runs, and compares with budget.cap and
    budget.max_runs from the profile. With usage_api on, also reads GitHub's billing usage (a GET) and uses the larger
    of the two spends; a 403 or 404 there is unknown, never zero."""
    if not ctx.state_dir:
        raise SpecError("no state directory for the ledger")
    b = _budget(ctx)
    s = ledger.summarize(ctx.state_dir, b)
    planned = p["planned"] or ctx.profile.get("planned") or b.get("planned", [])
    projected = ledger.project(planned, s["rates"])
    spent, lines = s["spent"], []
    cur = s["currency"]
    lines.append("ledger: %d run(s) counted, %.3f %s estimated%s" % (
        s["runs_used"], spent, cur, " (includes %.3f carried from before the ledger)" % s["carried"] if s["carried"] else ""))
    note_unknown = None
    if p["usage_api"] != "off":
        if not p["usage_user"]:
            raise SpecError("usage_user is required when usage_api is not off")
        doc, err = gh_json(ctx, "users/%s/settings/billing/usage" % p["usage_user"])
        if err or not isinstance(doc, dict):
            note_unknown = err or "billing usage was not an object"
            lines.append("billing usage unavailable: %s" % note_unknown)
        else:
            api = sum(float(i.get("grossAmount", i.get("netAmount", 0)) or 0) for i in doc.get("usageItems", [])
                      if i.get("product") == "actions")
            lines.append("billing usage: %.3f %s gross for actions" % (api, cur))
            spent = max(spent, round(api, 4))
    runs_after = s["runs_used"] + len(planned)
    bad = []
    if s["max_runs"] is not None and runs_after > s["max_runs"]:
        bad.append("%d run(s) used + %d planned exceeds the cap of %d" % (s["runs_used"], len(planned), s["max_runs"]))
    if s["cap"] is not None and spent + projected > s["cap"]:
        bad.append("%.2f spent + %.2f worst-case planned exceeds the %.2f %s budget" % (spent, projected, s["cap"], cur))
    lines.append("planned: %d run(s), worst case %.2f %s" % (len(planned), projected, cur))
    if bad:
        return fail("; ".join(bad), lines, spent=spent, projected=projected, runs_used=s["runs_used"])
    if note_unknown and p["usage_api"] == "required":
        return unknown("billing usage could not be read", lines)
    if s["cap"] and (spent + projected) / s["cap"] >= p["warn_at"]:
        return warn("%.2f of %.2f %s would be committed" % (spent + projected, s["cap"], cur), lines)
    left_runs = "" if s["max_runs"] is None else ", %d of %d runs left after the plan" % (s["max_runs"] - runs_after, s["max_runs"])
    return ok("%.2f of %.2f %s committed%s" % (spent + projected, s["cap"] or 0, cur, left_runs), lines,
              spent=spent, projected=projected, runs_used=s["runs_used"])


@register("github.workflows_disabled", "the workflows that must stay off (ordinary CI) are disabled in the repository settings",
          {"repo": ("str", REQUIRED, "owner/name of the repository"),
           "files": ("list", REQUIRED, "workflow file names (for example ci.yml) that must be disabled")},
          cost="cheap-network", network=True)
def workflows_disabled(ctx, p):
    """A workflow file that triggers on push or pull request costs hosted minutes on every merge unless the workflow
    is disabled in the repository's Actions settings. This reads the workflow list (a GET) and fails on any named
    file whose state is not disabled; a named file that does not exist on GitHub is unknown."""
    doc, err = gh_json(ctx, "repos/%s/actions/workflows?per_page=100" % p["repo"])
    if err or not isinstance(doc, dict):
        return unknown("cannot read the workflow list", [err or "unexpected response"])
    state = {os.path.basename(w.get("path", "")): w.get("state", "?") for w in doc.get("workflows", [])}
    lines, active, missing = [], [], []
    for f in p["files"]:
        if f not in state:
            missing.append(f)
            lines.append("%s: not known to GitHub" % f)
        else:
            lines.append("%s: %s" % (f, state[f]))
            if not state[f].startswith("disabled"):
                active.append(f)
    if active:
        return fail("%s still active and would bill on its triggers" % ", ".join(active), lines)
    if missing:
        return unknown("%s not found in the repository's workflow list" % ", ".join(missing), lines)
    return ok("%d workflow(s) are disabled as required" % len(p["files"]), lines)


@register("github.secrets_present", "the named repository secrets exist (names only, never values)",
          {"repo": ("str", REQUIRED, "owner/name of the repository"),
           "names": ("list", REQUIRED, "secret names that must exist"),
           "env": ("str", "", "an environment name, to list that environment's secrets instead of the repository's"),
           "org": ("str", "", "an organization, to list its secrets instead (organization secrets are not in the repository list)")},
          cost="cheap-network", network=True)
def secrets_present(ctx, p):
    """Runs `gh secret list` (GitHub returns names and update times, never values) and fails on any named secret that
    is not in the list. A missing `gh`, no permission to list, or a timeout is `unknown`: it does not prove the
    secret is absent. Only names are read, kept and printed."""
    argv = ["gh", "secret", "list"] + (["--org", p["org"]] if p["org"] else ["--repo", p["repo"]])
    if p["env"] and not p["org"]:
        argv += ["--env", p["env"]]
    r = ctx.run(argv, net=True, timeout=20)
    if r.missing:
        return cannot_check("gh is not installed")
    if r.timed_out:
        return unknown("GitHub did not answer in time")
    if not r.ok:
        return unknown("the secret list could not be read: %s" % (first_line(r.err or r.out) or "exit %s" % r.rc),
                       ["listing secrets needs a token with repository admin (or secrets) access"])
    have = {ln.split()[0] for ln in r.out.splitlines() if ln.strip()}
    missing = [n for n in p["names"] if n not in have]
    where = "%s%s" % (p["org"] or p["repo"], " (environment %s)" % p["env"] if p["env"] and not p["org"] else "")
    lines = ["%s: %s" % (n, "missing" if n in missing else "present") for n in p["names"]]
    if missing:
        return fail("%s not in the secrets of %s" % (", ".join(missing), where), lines)
    return ok("all %d named secret(s) exist in %s" % (len(p["names"]), where), lines)
