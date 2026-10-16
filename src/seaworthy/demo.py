"""`seaworthy demo`: a synthetic project, a good run, and one known-bad case per check.

Everything goes through the public command line, in subprocesses, exactly as a user would run it. The cases are
the fixture set: each builds a throwaway git project (with a local bare repository as its remote), changes one
thing, runs the profile, and states which check must change and what the exit code must be. Network answers come
from the fixtures shipped inside the package (demo_data/fixtures), so nothing leaves this machine. Exit 0 only when every case behaves as stated.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)         # the directory that holds the `seaworthy` package: src/ in a checkout, site-packages when installed
PROFILE = os.path.join(HERE, "demo_data", "profile.json")
FIX = os.path.join(HERE, "demo_data", "fixtures")

_ENV = dict(os.environ, GIT_AUTHOR_NAME="Demo", GIT_AUTHOR_EMAIL="demo@example.invalid",
            GIT_COMMITTER_NAME="Demo", GIT_COMMITTER_EMAIL="demo@example.invalid", GIT_CONFIG_NOSYSTEM="1")
_PIN = "ab" * 20      # a stand-in commit SHA for pinned actions


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", "-c", "tag.gpgsign=false"] + list(args),
                       cwd=cwd, env=_ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    if r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout.strip()


def cli(*args):
    env = dict(os.environ, SEAWORTHY_STATE=os.path.join(tempfile.gettempdir(), "seaworthy-demo-unused"),
               PYTHONPATH=os.pathsep.join(p for p in (SRC, os.environ.get("PYTHONPATH")) if p))
    r = subprocess.run([sys.executable, "-m", "seaworthy"] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=env)
    return r.returncode, r.stdout, r.stderr


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


RELEASE_YML = """name: Release
on:
  push:
    tags: ['v*']
concurrency:
  group: release
  cancel-in-progress: false
jobs:
  linux:
    runs-on: ubuntu-24.04
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@%(pin)s
  windows:
    runs-on: windows-2022
    timeout-minutes: 120
    steps:
      - uses: actions/checkout@%(pin)s
""" % {"pin": _PIN}
CI_YML = """name: CI
on:
  workflow_dispatch:
jobs:
  check:
    runs-on: ubuntu-24.04
    timeout-minutes: 15
    steps:
      - run: echo ok
"""


def make_project(base):
    origin, work, state = (os.path.join(base, n) for n in ("origin.git", "work", "state"))
    os.makedirs(origin)
    git(origin, "init", "--bare", "-q")
    git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    os.makedirs(work)
    git(work, "init", "-q")
    git(work, "checkout", "-q", "-b", "main")
    write(os.path.join(work, "package.json"), json.dumps({"name": "demo-app", "version": "1.2.3"}, indent=2) + "\n")
    write(os.path.join(work, "docs", "RELEASE_NOTES_1_2_3.md"), "# 1.2.3\n\nSummary, new, fixed.\n")
    write(os.path.join(work, ".github", "workflows", "release.yml"), RELEASE_YML)
    write(os.path.join(work, ".github", "workflows", "ci.yml"), CI_YML)
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "release candidate")
    git(work, "remote", "add", "origin", origin)
    git(work, "push", "-q", "-u", "origin", "main")
    git(work, "tag", "-a", "v1.2.3", "-m", "1.2.3")
    ctx = {"work": work, "state": state, "sha": git(work, "rev-parse", "HEAD")}
    for pair in ("ubuntu-24.04=20260901.1.0", "windows-2022=20260908.1.0"):
        rc, _, err = cli("image", "record", PROFILE, "--state", state, pair)
        if rc != 0:
            raise RuntimeError("could not record the demo image %s (exit %s): %s" % (pair, rc, err.strip()[:300]))
    return ctx


# --- mutations: each changes one thing in the project and returns the SHA to release -------------------------------
def m_none(p):
    return p["sha"]


def m_dirty(p):
    write(os.path.join(p["work"], "scratch.txt"), "left over\n")
    return p["sha"]


def _commit(p, rel, text, push=True):
    write(os.path.join(p["work"], rel), text)
    git(p["work"], "add", "-A")
    git(p["work"], "commit", "-q", "-m", "change " + rel)
    git(p["work"], "tag", "-f", "-a", "v1.2.3", "-m", "1.2.3")      # the tag follows the new candidate
    if push:
        git(p["work"], "push", "-q", "origin", "main")
    return git(p["work"], "rev-parse", "HEAD")


def m_moved(p):
    _commit(p, "later.txt", "a commit after the frozen SHA\n")
    return p["sha"]                 # the SHA being released is no longer HEAD


def m_unpushed(p):
    return _commit(p, "local-only.txt", "not pushed\n", push=False)


def m_no_timeout(p):
    return _commit(p, ".github/workflows/release.yml", RELEASE_YML.replace("    timeout-minutes: 120\n", ""))


def m_alpha_job(p):
    alpha = ("  alpha:\n    if: contains(github.ref_name, '-alpha.')\n    runs-on: ubuntu-24.04\n    steps:\n"
             "      - uses: actions/checkout@%s\n" % _PIN)
    return _commit(p, ".github/workflows/release.yml", RELEASE_YML + alpha)


def m_unpinned(p):
    return _commit(p, ".github/workflows/release.yml", RELEASE_YML.replace("actions/checkout@" + _PIN, "actions/checkout@v4"))


def m_cancel(p):
    return _commit(p, ".github/workflows/release.yml", RELEASE_YML.replace("cancel-in-progress: false", "cancel-in-progress: true"))


def m_no_notes(p):
    os.remove(os.path.join(p["work"], "docs", "RELEASE_NOTES_1_2_3.md"))
    git(p["work"], "add", "-A")
    git(p["work"], "commit", "-q", "-m", "drop the notes")
    git(p["work"], "tag", "-f", "-a", "v1.2.3", "-m", "1.2.3")
    git(p["work"], "push", "-q", "origin", "main")
    return git(p["work"], "rev-parse", "HEAD")


def m_two_runs(p):
    for rid, os_name in (("run-1", "linux"), ("run-2", "windows")):
        cli("ledger", "add", PROFILE, "--state", p["state"], "--run-id", rid, "--sha", p["sha"], "--workflow", "release",
            "--os", os_name, "--outcome", "success", "--minutes", "12")
    return p["sha"]


def m_lightweight_tag(p):
    git(p["work"], "tag", "-d", "v1.2.3")
    git(p["work"], "tag", "v1.2.3")
    return p["sha"]


# name, what changes, mutation, fixtures, extra args, expected {check id: status}, expected exit code
CASES = [
    ("good", "nothing is wrong", m_none, "good", [], {}, 0),
    ("dirty-tree", "an untracked file is left in the checkout", m_dirty, "good", [], {"clean-checkout": "fail"}, 3),
    ("head-moved", "a commit lands after the SHA was frozen", m_moved, "good", [], {"source-frozen": "fail"}, 3),
    ("unpushed-tip", "HEAD is the SHA but the remote branch is behind", m_unpushed, "good", [], {"source-frozen": "fail"}, 3),
    ("lightweight-tag", "the release tag is not annotated", m_lightweight_tag, "good", [], {"source-frozen": "fail"}, 3),
    ("version-mismatch", "the release is 1.2.4 but the package says 1.2.3", m_none, "good", ["--set", "version=1.2.4"], {"version-and-notes": "fail"}, 3),
    ("notes-missing", "the release notes file is gone", m_no_notes, "good", [], {"version-and-notes": "fail"}, 3),
    ("no-timeout", "a paid job has no timeout-minutes", m_no_timeout, "good", [], {"workflow-hygiene": "fail"}, 3),
    ("alpha-job-skipped", "a job with no timeout runs only for alpha tags, not this one", m_alpha_job, "good", [], {"workflow-hygiene": "warn"}, 4),
    ("cancel-release", "the release workflow cancels in progress", m_cancel, "good", [], {"workflow-hygiene": "fail"}, 3),
    ("unpinned-actions", "an action is pinned to a moving tag", m_unpinned, "good", [], {"workflow-hygiene": "warn"}, 4),
    ("tool-pin", "the build host has the wrong node", m_none, "bad-node-pin", [], {"tool-pins-builder": "fail"}, 3),
    ("host-down", "the build host does not answer", m_none, "bad-host", [], {"builder-reachable": "fail", "tool-pins-builder": "unknown"}, 3),
    ("image-drift", "the runner image moved since the last good run", m_none, "image-drift", [], {"runner-image-drift": "warn"}, 4),
    ("image-migration", "the runner label is under a retirement notice", m_none, "image-migration", [], {"runner-image-migration": "fail"}, 3),
    ("ci-still-on", "ordinary CI is still enabled", m_none, "ci-active", [], {"ordinary-ci-disabled": "fail"}, 3),
    ("budget-spent", "two hosted runs are already spent", m_two_runs, "good", [], {"hosted-budget": "fail"}, 3),
    ("usage-unreadable", "billing usage cannot be read", m_none, "usage-403", ["--set", "usage_api=required"], {"hosted-budget": "unknown"}, 3),
    ("waived-unknown", "the same, waived by a named person", m_none, "usage-403",
     ["--set", "usage_api=required", "--waive", "hosted-budget", "--by", "release-owner", "--reason", "billing token lacks scope"], {"hosted-budget": "unknown"}, 0),
    ("no-fixtures", "a dry run with no recorded answers for the network", m_none, "empty", [], {"builder-reachable": "not-run", "runner-image-migration": "not-run"}, 3),
]


def run_case(case, keep, verbose):
    name, what, mutate, fixtures, extra, expect, want_exit = case
    base = tempfile.mkdtemp(prefix="seaworthy-demo-%s-" % name)
    try:
        proj = make_project(base)
        sha = mutate(proj)
        rc, out, err = cli("run", PROFILE, "--repo", proj["work"], "--state", proj["state"], "--sha", sha, "--dry-run",
                           "--fixtures", os.path.join(FIX, fixtures + ".json"), "--json", *extra)
        try:
            report = json.loads(out)
        except ValueError:
            return False, "no JSON report (exit %s): %s" % (rc, err.strip()[:200]), ""
        got = {r["id"]: r["status"] for r in report["checks"]}
        problems = []
        if rc != want_exit:
            problems.append("exit %s, wanted %s" % (rc, want_exit))
        for cid, status in expect.items():
            if got.get(cid) != status:
                problems.append("%s is %s, wanted %s" % (cid, got.get(cid), status))
        flipped = [cid for cid, s in got.items() if s not in ("pass", "skip") and cid not in expect]
        if name != "no-fixtures":
            flipped = [c for c in flipped if got[c] != "not-run" or c in expect]
        else:
            flipped = []
        if flipped and not expect.keys() >= set(flipped):
            problems.append("also changed: " + ", ".join("%s=%s" % (c, got[c]) for c in flipped))
        text = ""
        if verbose:
            rc2, text, _ = cli("run", PROFILE, "--repo", proj["work"], "--state", proj["state"], "--sha", sha, "--dry-run",
                               "--fixtures", os.path.join(FIX, fixtures + ".json"), *extra)
        summary = "%s -> %s (exit %s)" % (what, ", ".join("%s %s" % (k, v) for k, v in expect.items()) or "all pass", rc)
        return not problems, summary if not problems else "; ".join(problems), text
    finally:
        if keep:
            print("  kept: " + base)
        else:
            shutil.rmtree(base, ignore_errors=True)


def gate_story(keep, verbose):
    """The receipt and the dispatch gate, end to end, on one throwaway project."""
    steps, base = [], tempfile.mkdtemp(prefix="seaworthy-demo-gate-")
    try:
        p = make_project(base)
        common = ["--state", p["state"]]
        gate = lambda *x: cli("gate", PROFILE, "--sha", p["sha"], "--scope", "local", *(common + list(x)))
        run_local = lambda *x: cli("run", PROFILE, "--repo", p["work"], "--sha", p["sha"], "--scope", "local", "--json", *(common + list(x)))

        def step(name, got, want, note=""):
            steps.append((name, got == want, "%s%s" % (note, "" if got == want else " (exit %s, wanted %s)" % (got, want))))

        def gate_rows():
            path = os.path.join(p["state"], "ledger.jsonl")
            rows = [json.loads(ln) for ln in open(path)] if os.path.exists(path) else []
            return [r for r in rows if r.get("kind") == "gate"]

        def row_step(name, ok, note):
            steps.append((name, ok, note))

        step("gate refuses with no receipt", gate()[0], 3, "no receipt exists yet")
        rows = gate_rows()
        row_step("a refusal writes one gate row with an upper bound of the planned minutes",
                 len(rows) == 1 and rows[0]["decision"] == "REFUSED" and rows[0]["minutes_not_dispatched_upper"] == 180
                 and rows[0]["receipt"] is None and rows[0]["receipt_unknown"],
                 "blocked, not dispatched (upper bound) 180 min = 60 + 120 planned timeouts; no receipt: said, not guessed")
        rc, out, _ = run_local()
        report = json.loads(out)
        step("a real run on this machine passes and writes a receipt", rc, 0, "%d command(s) left the machine" % report["network_calls"])
        step("gate now says go", gate()[0], 0, "fresh receipt, same SHA, budget has room")
        rows = gate_rows()
        row_step("a go writes one more gate row with the receipt's id and age",
                 len(rows) == 2 and rows[1]["decision"] == "GO" and rows[1]["minutes_not_dispatched_upper"] == 0
                 and len(rows[1]["receipt"]["id"] or "") == 64 and isinstance(rows[1]["receipt"]["age_s"], int)
                 and rows[1]["budget"]["runs_left"] == 2,
                 "passed; nothing held back (0 min); budget left recorded")
        receipt = report["receipt"]["path"]
        doc = json.load(open(receipt))
        doc["warnings"] = ["edited"]
        tampered = os.path.join(base, "tampered.json")
        json.dump(doc, open(tampered, "w"))
        step("gate refuses an edited receipt", gate("--receipt", tampered)[0], 3, "content hash no longer matches")
        dry = os.path.join(base, "dry.json")
        rc, _, _ = cli("run", PROFILE, "--repo", p["work"], "--sha", p["sha"], "--dry-run", "--fixtures", os.path.join(FIX, "good.json"),
                       "--receipt-out", dry, "--json", *common)
        step("a dry run passes and can write a receipt", rc, 0)
        step("gate refuses a dry-run receipt", cli("gate", PROFILE, "--sha", p["sha"], "--receipt", dry, *common)[0], 3, "a dry run never authorises spend")
        cli("ledger", "add", PROFILE, *(common + ["--run-id", "hosted-1", "--sha", p["sha"], "--workflow", "release", "--os", "windows",
                                                   "--outcome", "failed", "--minutes", "5"]))
        step("gate refuses to re-run a failed SHA", gate()[0], 3, "a deterministic hosted failure needs a new SHA and a new PASS")
        step("an override goes through and is recorded", gate("--override", "release-owner", "--reason", "reviewed the failure")[0], 0)
        base2 = tempfile.mkdtemp(prefix="seaworthy-demo-gate2-")
        try:
            q = make_project(base2)
            c2 = ["--state", q["state"]]
            cli("run", PROFILE, "--repo", q["work"], "--sha", q["sha"], "--scope", "local", *c2)
            time.sleep(1.2)
            cli("ledger", "add", PROFILE, *(c2 + ["--run-id", "hosted-9", "--sha", q["sha"], "--workflow", "release", "--os", "linux",
                                                  "--outcome", "failed", "--failure-class", "infrastructure", "--minutes", "3"]))
            g2 = lambda: cli("gate", PROFILE, "--sha", q["sha"], "--scope", "local", "--planned", "linux:30", *c2)[0]
            step("an infrastructure failure needs a fresh PASS first", g2(), 3, "the old PASS predates the failure")
            time.sleep(1.2)
            cli("run", PROFILE, "--repo", q["work"], "--sha", q["sha"], "--scope", "local", "--planned", "linux:30", *c2)
            step("then a retry at the same SHA is allowed", g2(), 0, "new PASS written after the failure")
        finally:
            if not keep:
                shutil.rmtree(base2, ignore_errors=True)
        return steps
    finally:
        if keep:
            print("  kept: " + base)
        else:
            shutil.rmtree(base, ignore_errors=True)


def main(args):
    if shutil.which("git") is None:
        print("seaworthy demo needs git on PATH", file=sys.stderr)
        return 2
    missing = [p for p in (PROFILE, FIX) if not os.path.exists(p)]
    if missing:
        print("seaworthy demo: the demo's own data is missing from this install: %s. Reinstall seaworthy-release, or run the demo "
              "from a source checkout." % ", ".join(missing), file=sys.stderr)
        return 2
    wanted = set(args.case or [])
    bad = 0
    print("Seaworthy demo: each case builds a throwaway project, changes one thing, and checks the report.\n"
          "Nothing leaves this machine; network answers come from the fixtures shipped with the package.\n")
    cases = [c for c in CASES if not wanted or c[0] in wanted]
    for case in cases:
        ok, note, text = run_case(case, args.keep, args.verbose)
        bad += 0 if ok else 1
        print("  %-5s %-17s %s" % ("ok" if ok else "FAIL", case[0], note))
        if args.verbose and text:
            print("\n".join("        " + ln for ln in text.splitlines()) + "\n")
    if not wanted or "gate" in wanted:
        print("\nThe dispatch gate (receipt, retry rule, override):")
        for name, ok, note in gate_story(args.keep, args.verbose):
            bad += 0 if ok else 1
            print("  %-5s %s%s" % ("ok" if ok else "FAIL", name, "  - " + note if note else ""))
    print("\n%s" % ("every case behaved as stated" if not bad else "%d case(s) did not behave as stated" % bad))
    return 0 if not bad else 1
