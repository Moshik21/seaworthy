"""Source frozen and clean checkout: the two cheapest and most valuable checks."""
import os

from . import REQUIRED, fail, ok, register, unknown, warn
from ..context import NotRun


def _git(ctx, *args, **kw):
    return ctx.run(["git"] + list(args), **kw)


def _resolve(ctx, rev):
    p = _git(ctx, "rev-parse", "--verify", "--quiet", rev + "^{commit}")
    return p.out.strip() if p.ok else None


def _short(sha):
    return sha[:8] if sha else "none"


@register("git.source_frozen", "HEAD, the branch tip on the remote and the expected SHA are one commit",
          {"branch": ("str", "main", "branch that carries the release"),
           "remote": ("str", "origin", "remote name"),
           "remote_mode": ("str", "tracking", "'tracking' compares the last-fetched remote ref (offline); 'live' asks the remote with ls-remote"),
           "branch_relation": ("str", "tip", "'tip': the expected SHA must be the branch tip; 'ancestor': it may be below the tip (control commits above a frozen source)"),
           "expected_sha": ("str", "${sha}", "the SHA being released (defaults to --sha)"),
           "tag": ("str", "", "release tag to verify, for example v1.2.3 (optional)"),
           "tag_required": ("bool", False, "fail when the tag does not exist yet")},
          cost="free")
def source_frozen(ctx, p):
    """Compares HEAD with the expected SHA and the remote branch tip, and, when a tag is named, checks that it is
    annotated and peels to the expected SHA. Remote truth comes from the remote-tracking ref (stale if you have
    not fetched) or, with remote_mode 'live', from `git ls-remote`. Never fetches, never writes."""
    expected = p["expected_sha"]
    if not expected:
        return unknown("no expected SHA was given", ["pass --sha <40-character SHA> to say what is being released"])
    head = _resolve(ctx, "HEAD")
    if head is None:
        return unknown("not a git checkout, or it has no commits", [ctx.repo])
    want = _resolve(ctx, expected)
    if want is None:
        return fail("expected SHA %s is not in this checkout" % expected[:12])
    lines, bad = [], []
    if head != want:
        bad.append("HEAD %s is not the expected %s" % (_short(head), _short(want)))
    lines.append("HEAD %s, expected %s" % (_short(head), _short(want)))
    branch, remote = p["branch"], p["remote"]
    tip = None
    if p["remote_mode"] == "live":
        r = _git(ctx, "ls-remote", remote, "refs/heads/" + branch, net=True, timeout=15)
        if not r.ok:
            return unknown("could not ask the remote for %s/%s" % (remote, branch), [r.text or "no output"])
        tip = (r.out.split() or [None])[0]
        src = "live remote"
    else:
        tip = _resolve(ctx, "refs/remotes/%s/%s" % (remote, branch))
        src = "last-fetched %s/%s (not refreshed)" % (remote, branch)
    if tip is None:
        bad.append("no %s for %s" % (src, branch))
    elif p["branch_relation"] == "tip":
        lines.append("%s is %s" % (src, _short(tip)))
        if tip != want:
            bad.append("%s is %s, not the expected %s" % (src, _short(tip), _short(want)))
    else:
        anc = _git(ctx, "merge-base", "--is-ancestor", want, tip)
        lines.append("%s is %s" % (src, _short(tip)))
        if anc.rc != 0:
            bad.append("expected %s is not an ancestor of %s" % (_short(want), src))
    tag = p["tag"]
    if tag:
        peeled = _resolve(ctx, "refs/tags/" + tag)
        if peeled is None:
            if p["tag_required"]:
                bad.append("tag %s does not exist" % tag)
            else:
                lines.append("tag %s not created yet" % tag)
        else:
            kind = _git(ctx, "cat-file", "-t", "refs/tags/" + tag).out.strip()
            if kind != "tag":
                bad.append("tag %s is lightweight; releases need an annotated tag" % tag)
            if peeled != want:
                bad.append("tag %s points at %s, not %s" % (tag, _short(peeled), _short(want)))
            else:
                lines.append("tag %s -> %s" % (tag, _short(peeled)))
    if bad:
        return fail("; ".join(bad), lines)
    return ok("HEAD = expected = %s (%s)" % (src.split(" (")[0], _short(want)), lines)


@register("git.clean_checkout", "the working tree has no changes and no stray files",
          {"allow_untracked": ("list", [], "glob patterns of untracked files that are allowed"),
           "max_listed": ("int", 5, "how many paths to name in the evidence")},
          cost="free")
def clean_checkout(ctx, p):
    """Runs `git status --porcelain=v1 --untracked-files=all -z` (without taking the index lock) and fails on any
    entry, plus an unfinished merge, rebase, cherry-pick or bisect."""
    import fnmatch
    r = _git(ctx, "status", "--porcelain=v1", "--untracked-files=all", "-z")
    if not r.ok:
        return unknown("git status failed", [r.text or "no output"])
    parts = r.out.split("\0")
    entries, i = [], 0
    while i < len(parts):
        e = parts[i]
        i += 1
        if len(e) < 4:
            continue
        code, path = e[:2], e[3:]
        if code[0] in "RC":
            i += 1
        if code == "??" and any(fnmatch.fnmatch(path, g) for g in p["allow_untracked"]):
            continue
        entries.append((code, path))
    problems = []
    gd = _git(ctx, "rev-parse", "--absolute-git-dir")
    if gd.ok:
        for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG", "rebase-merge", "rebase-apply"):
            if os.path.exists(os.path.join(gd.out.strip(), name)):
                problems.append("unfinished operation: " + name)
    if entries or problems:
        shown = ["%s %s" % (c.strip() or "?", pth) for c, pth in entries[:p["max_listed"]]]
        more = len(entries) - len(shown)
        ev = "%d changed or untracked path(s)" % len(entries) if entries else problems[0]
        return fail(ev, shown + (["... and %d more" % more] if more > 0 else []) + problems)
    return ok("working tree is clean")
