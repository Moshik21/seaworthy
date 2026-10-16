"""The command line: one entry point, fixed verbs, argv arrays only.

    seaworthy run PROFILE --sha SHA [--repo DIR] [--dry-run --fixtures FILE] [--json] ...
    seaworthy validate PROFILE        seaworthy checks | explain CHECK
    seaworthy gate PROFILE --sha SHA  seaworthy receipt show|verify
    seaworthy ledger add|show|summary seaworthy image record|show
    seaworthy demo
"""
import argparse
import json
import os
import re
import sys
import time

from . import EXIT_BAD_SPEC, EXIT_ERROR, EXIT_NO_GO, EXIT_PASS, IMAGES_SCHEMA, VERSION, ledger as ledger_mod, receipt as receipt_mod
from .checks import PLANNED, REGISTRY, REQUIRED, load_all
from .context import Context, Fixtures
from .engine import run as run_engine
from .profile import load as load_profile
from .report import to_json, to_text, use_colour
from .util import SpecError, atomic_write, default_state_root, load_json, now_iso, parse_iso, redact


def _sets(items):
    out = {}
    for item in items or []:
        if "=" not in item:
            raise SpecError("--set wants name=value, got '%s'" % item)
        k, v = item.split("=", 1)
        out[k.strip()] = v
    return out


def _state_dir(args, profile):
    root = args.state or os.environ.get("SEAWORTHY_STATE") or default_state_root()
    # --state names the profile's own directory; the default root holds one directory per profile.
    return root if args.state else os.path.join(root, profile.name)


def _profile(args, sha=""):
    repo = os.path.abspath(getattr(args, "repo", None) or os.getcwd())
    return load_profile(args.profile, _sets(args.set), {"sha": sha or "", "repo": repo}), repo


def _planned(items):
    planned = []
    for item in items or []:
        try:
            os_name, minutes = item.split(":")
            planned.append({"os": os_name, "timeout_min": float(minutes)})
        except ValueError:
            raise SpecError("--planned wants os:timeout_minutes, for example windows:180")
    return planned


def _add_common(ap, repo=True):
    ap.add_argument("profile", help="path of the profile (JSON)")
    ap.add_argument("--set", action="append", metavar="NAME=VALUE", help="override a profile parameter, or a field like budget.cap=5")
    ap.add_argument("--state", metavar="DIR", help="state directory (ledger, receipts, image records); default is per user, per profile")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    if repo:
        ap.add_argument("--repo", metavar="DIR", help="the checkout being released (default: the current directory)")


def cmd_run(args):
    sha = args.sha or ""
    profile, repo = _profile(args, sha)
    if args.fixtures and not args.dry_run:
        raise SpecError("--fixtures only makes sense with --dry-run (a real run never reads recorded answers)")
    fixtures = Fixtures.load(args.fixtures) if args.fixtures else None
    state = _state_dir(args, profile)
    ctx = Context(repo, sha, dry_run=args.dry_run, fixtures=fixtures, hosts=profile.hosts, state_dir=state,
                  profile={"budget": profile.budget, "receipt": profile.receipt, "planned": _planned(args.planned)})
    waivers = {}
    for w in args.waive or []:
        if not args.by or not args.reason:
            raise SpecError("--waive needs --by NAME and --reason TEXT; waivers are recorded in the report and the receipt")
        waivers[w] = {"by": args.by, "reason": args.reason, "sha": sha}
    report = run_engine(profile, ctx, scope=args.scope, only=args.only, run_all=args.all, waivers=waivers, dry_run=args.dry_run, stage=args.stage)
    if report["verdict"] == "PASS" and not args.dry_run and not args.only and not args.no_receipt and sha:
        rc = receipt_mod.build(report, profile, args.scope, profile.sets)
        report["receipt"] = {"path": receipt_mod.write(state, rc), "hash": rc["receipt_hash"]}
    elif args.dry_run and args.receipt_out and report["verdict"] == "PASS":
        rc = receipt_mod.build(report, profile, args.scope, profile.sets)
        atomic_write(args.receipt_out, json.dumps(rc, indent=2, sort_keys=True) + "\n")
        report["receipt"] = {"path": args.receipt_out + "  (dry-run receipt: the gate refuses it)", "hash": rc["receipt_hash"]}
    if not report["receipt"]:
        report["receipt_note"] = ("no receipt: " + ("a dry run never writes one" if args.dry_run else
                                  "--only runs are partial" if args.only else "--no-receipt was given" if args.no_receipt else
                                  "the verdict is %s" % report["verdict"] if report["verdict"] != "PASS" else
                                  "pass --sha to bind one to a commit"))
    if args.out:
        atomic_write(args.out, to_json(report) + "\n")
    print(to_json(report) if args.json else to_text(report, verbose=args.verbose, colour=use_colour(sys.stdout)))
    return report["exit_code"]


def cmd_validate(args):
    profile, _ = _profile(args)
    enabled = [c for c in profile.checks if c["enabled"]]
    info = {"profile": profile.name, "profile_hash": profile.hash, "checks": len(profile.checks), "enabled": len(enabled),
            "planned_slots": [c["id"] for c in profile.checks if c["planned"]], "notes": profile.warnings}
    if args.json:
        print(json.dumps(info, indent=2))
    else:
        print("profile %s is valid: %d check(s), %d enabled, hash %s" % (profile.name, len(profile.checks), len(enabled), profile.hash[:12]))
        for c in profile.checks:
            print("  %-26s %-28s %-13s %s%s" % (c["id"], c["check"], c["cost"], "required" if c["required"] else "advisory",
                                              "" if c["enabled"] else "  (disabled)"))
        for n in profile.warnings:
            print("  note: " + n)
    return EXIT_PASS


def cmd_checks(args):
    load_all()
    if args.json:
        doc = {n: {"summary": s.summary, "cost": s.cost, "network": s.network} for n, s in sorted(REGISTRY.items())}
        doc.update({n: {"summary": t, "cost": None, "planned": True} for n, t in sorted(PLANNED.items())})
        print(json.dumps(doc, indent=2))
        return EXIT_PASS
    for name, spec in sorted(REGISTRY.items()):
        print("%-26s %-14s %s" % (name, spec.cost, spec.summary))
    for name, summary in sorted(PLANNED.items()):
        print("%-26s %-14s %s" % (name, "(planned)", summary))
    return EXIT_PASS


def cmd_explain(args):
    load_all()
    spec = REGISTRY.get(args.check)
    if spec is None:
        if args.check in PLANNED:
            print("%s is a planned platform check: %s\nIt may be listed in a profile (disabled) so the profile keeps its shape." % (args.check, PLANNED[args.check]))
            return EXIT_PASS
        raise SpecError("unknown check '%s'; `seaworthy checks` lists them" % args.check)
    print("%s  (%s%s)\n%s\n\n%s\n\nparameters:" % (spec.name, spec.cost, ", network" if spec.network else "", spec.summary, spec.doc))
    for name, (typ, default, desc) in spec.params.items():
        d = "required" if default is REQUIRED else "default %s" % json.dumps(default)
        print("  %-18s %-6s %s [%s]" % (name, typ, desc, d))
    return EXIT_PASS


def cmd_gate(args):
    sha = args.sha
    profile, _ = _profile(args, sha)
    state = _state_dir(args, profile)
    reasons = []
    now = time.time()
    rc, why = receipt_mod.check_for_dispatch(state, profile, sha, args.scope, now=now, receipt_path=args.receipt,
                                             stage=args.stage or profile.stage)
    reasons += ["receipt: " + w for w in why]
    rows = [r for r in ledger_mod.runs(state) if r.get("sha") == sha and r.get("outcome") == "failed"]
    if rows:
        last = max(rows, key=lambda r: r.get("at", ""))
        klass = last.get("failure_class") or "deterministic"
        if klass == "deterministic":
            reasons.append("retry rule: hosted run %s failed at this SHA (deterministic); re-running the same SHA cannot change the result. "
                           "Fix it, commit, and get a new PASS at a new SHA." % last.get("run_id"))
        elif rc and rc[1].get("at", "") <= last.get("at", ""):
            reasons.append("retry rule: hosted run %s failed (infrastructure) at %s; a PASS written after that time is needed" % (last.get("run_id"), last.get("at")))
    planned = _planned(args.planned)
    budget = dict(profile.budget)
    s = ledger_mod.summarize(state, budget)
    plan_known = bool(planned or budget.get("planned"))
    planned = planned or budget.get("planned", [{"os": "linux", "timeout_min": 0}])
    projected = ledger_mod.project(planned, s["rates"])
    if s["max_runs"] is not None and s["runs_used"] + len(planned) > s["max_runs"]:
        reasons.append("budget: %d hosted run(s) used + %d planned exceeds the cap of %d" % (s["runs_used"], len(planned), s["max_runs"]))
    if s["cap"] is not None and s["spent"] + projected > s["cap"]:
        reasons.append("budget: %.2f spent + %.2f worst case exceeds %.2f %s" % (s["spent"], projected, s["cap"], s["currency"]))
    decision, override = ("GO", None) if not reasons else ("REFUSED", None)
    if reasons and args.override:
        allowed = profile.receipt.get("override_allowed_by")
        if allowed and args.override not in allowed:
            raise SpecError("'%s' may not override; the profile allows: %s" % (args.override, ", ".join(allowed)))
        if not args.reason:
            raise SpecError("--override needs --reason TEXT; overrides are written to the ledger")
        override = {"kind": "override", "sha": sha, "by": args.override, "reason": args.reason, "overrode": reasons}
        ledger_mod.append(state, override)
        decision = "GO (OVERRIDDEN)"
    out = {"decision": decision, "sha": sha, "reasons": reasons, "receipt": rc[0] if rc else None,
           "budget": {"runs_used": s["runs_used"], "spent": s["spent"], "cap": s["cap"], "max_runs": s["max_runs"]}}
    try:        # one row per decision; a failed write never changes the decision, and is said, not swallowed
        ledger_mod.append(state, ledger_mod.gate_row(sha, args.scope, decision, reasons, rc, now, planned, plan_known, s, projected))
        out["ledger_row"] = "recorded"
    except OSError as exc:
        out["ledger_row"] = "not recorded: %s" % exc.strerror
        print("seaworthy: the gate decision could not be written to the ledger (%s)" % exc.strerror, file=sys.stderr)
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print("gate %s for %s" % (decision, sha[:12]))
        for r in reasons:
            print("  - " + r)
        if override:
            print("  overridden by %s: %s (recorded in the ledger)" % (args.override, args.reason))
        if not reasons:
            print("  receipt %s is valid; budget has room (%d run(s), %.2f %s so far)" % (os.path.basename(rc[0]), s["runs_used"], s["spent"], s["currency"]))
    return EXIT_PASS if decision.startswith("GO") else EXIT_NO_GO


def cmd_receipt(args):
    profile, _ = _profile(args)
    with open(args.file, "r", encoding="utf-8") as fh:
        rc = json.load(fh)
    problems = receipt_mod.verify_integrity(rc, profile)
    if args.json:
        print(json.dumps({"intact": not problems, "problems": problems, "receipt": rc}, indent=2))
    else:
        print("receipt %s: sha %s, verdict %s, mode %s, at %s" % (os.path.basename(args.file), (rc.get("sha") or "?")[:12], rc.get("verdict"), rc.get("mode"), rc.get("at")))
        print("  intact" if not problems else "  NOT INTACT: " + "; ".join(problems))
    return EXIT_PASS if not problems else EXIT_NO_GO


def cmd_ledger(args):
    profile, _ = _profile(args)
    state = _state_dir(args, profile)
    rates = dict(ledger_mod.DEFAULT_RATES)
    rates.update(profile.budget.get("rates", {}))
    if args.action == "add":
        for need in ("run_id", "sha", "workflow", "os_name", "outcome"):
            if getattr(args, need) is None:
                raise SpecError("ledger add needs --%s" % need.replace("_name", "").replace("_", "-"))
        if args.outcome not in ledger_mod.OUTCOMES:
            raise SpecError("--outcome is one of %s" % ", ".join(ledger_mod.OUTCOMES))
        entry = {"kind": "run", "run_id": args.run_id, "sha": args.sha, "workflow": args.workflow, "os": args.os_name,
                 "outcome": args.outcome, "started": args.started or now_iso(), "note": args.note or ""}
        if args.budget_id:
            entry["budget_id"] = args.budget_id
        if args.timeout_min is not None:
            entry["timeout_min"] = args.timeout_min
        if args.minutes is not None:
            entry["minutes"] = args.minutes
            entry["est_cost"] = ledger_mod.estimate(args.minutes, args.os_name, rates)
        if args.outcome == "failed":
            entry["failure_class"] = args.failure_class or "deterministic"
            if entry["failure_class"] not in ledger_mod.FAILURE_CLASSES:
                raise SpecError("--failure-class is one of %s" % ", ".join(ledger_mod.FAILURE_CLASSES))
            if args.failure_text:
                text = open(args.failure_text, "r", encoding="utf-8", errors="replace").read()
                for known in profile.deterministic_failures:
                    if re.search(known["pattern"], text):
                        entry["failure_match"] = known["id"]
                        break
        stored = ledger_mod.append(state, entry)
        if args.image:
            if args.outcome != "success":
                raise SpecError("--image records the last-good runner image, so it needs --outcome success")
            for item in args.image:
                label, _, version = item.partition("=")
                _record_image(state, label, version, args.run_id, args.sha)
        print(json.dumps(stored, sort_keys=True) if args.json else
              "ledger: recorded %s %s (%s)%s" % (stored["run_id"], stored["outcome"], stored["os"],
                                                 "  est %.3f" % stored["est_cost"] if "est_cost" in stored else ""))
        return EXIT_PASS
    s = ledger_mod.summarize(state, profile.budget)
    if args.json:
        print(json.dumps({k: v for k, v in s.items()}, indent=2, sort_keys=True))
        return EXIT_PASS
    print("budget window %s: %d run(s)%s, %.3f%s %s estimated" % (profile.budget.get("window", "all"), s["runs_used"],
          "" if s["max_runs"] is None else " of %d" % s["max_runs"], s["spent"], "" if s["cap"] is None else " of %.2f" % s["cap"], s["currency"]))
    if args.action == "show":
        for r in s["rows"]:
            print("  %-14s %-10s %-8s %-9s %s min %s" % (r.get("run_id"), r.get("sha", "")[:10], r.get("os"), r.get("outcome"),
                                                      r.get("minutes", "?"), r.get("failure_class", "")))
        for o in ledger_mod.overrides(state):
            print("  OVERRIDE by %s on %s: %s" % (o.get("by"), o.get("sha", "")[:10], o.get("reason")))
    return EXIT_PASS


def _record_image(state, label, version, run_id, sha):
    if not label or not version:
        raise SpecError("image wants LABEL=VERSION, for example windows-2022=20260908.1.0")
    path = os.path.join(state, "images.json")
    doc = load_json(path, "image record") if os.path.exists(path) else {"schema": IMAGES_SCHEMA[0], "schema_version": IMAGES_SCHEMA[1], "labels": {}}
    doc["labels"][label] = {"version": version, "run_id": run_id, "sha": sha, "recorded_at": now_iso()}
    atomic_write(path, json.dumps(doc, indent=2, sort_keys=True) + "\n")


def cmd_image(args):
    profile, _ = _profile(args)
    state = _state_dir(args, profile)
    if args.action in ("record", "from-log"):
        if args.action == "from-log":
            text = open(args.log, "r", encoding="utf-8", errors="replace").read()
            m = re.search(r"Image:\s*(\S+)\s*\n\s*Version:\s*(\S+)", text)
            if not m:
                raise SpecError("no 'Image: ... Version: ...' lines in %s (the 'Set up job' log section has them)" % args.log)
            label, version = m.group(1), m.group(2)
        else:
            label, _, version = args.pair.partition("=")
        _record_image(state, label, version, args.run_id or "", args.sha or "")
        print("image: recorded %s = %s as the last good" % (label, version))
        return EXIT_PASS
    path = os.path.join(state, "images.json")
    doc = load_json(path, "image record") if os.path.exists(path) else {"labels": {}}
    for label, rec in sorted(doc["labels"].items()):
        print("%-18s %-16s run %s at %s" % (label, rec["version"], rec.get("run_id") or "-", rec.get("recorded_at")))
    if not doc["labels"]:
        print("no last-good runner images recorded")
    return EXIT_PASS


def cmd_demo(args):
    from . import demo
    return demo.main(args)


def build_parser():
    ap = argparse.ArgumentParser(prog="seaworthy", description="Seaworthy: run real checks locally before spending hosted minutes.")
    ap.add_argument("--version", action="version", version="seaworthy " + VERSION)
    sub = ap.add_subparsers(dest="cmd", metavar="COMMAND")

    p = sub.add_parser("run", help="run a profile and print the report")
    _add_common(p)
    p.add_argument("--sha", help="the SHA being released (40 characters or a unique prefix)")
    p.add_argument("--dry-run", action="store_true", help="never leave this machine: network checks use --fixtures or are 'not run'; no receipt")
    p.add_argument("--fixtures", metavar="FILE", help="recorded answers for network commands (dry run only)")
    p.add_argument("--scope", help="only checks for this platform scope (and scope 'any')")
    p.add_argument("--stage", help="the release stage, for example publish (default: the profile's 'stage'); a check that does not "
                                   "apply at this stage is reported not applicable, and the receipt records the stage")
    p.add_argument("--only", action="append", metavar="ID", help="run only this check (repeatable); no receipt is written")
    p.add_argument("--all", action="store_true", help="run costlier tiers even after a required check has not passed")
    p.add_argument("--waive", action="append", metavar="ID", help="waive an 'unknown' result of a waivable check (needs --by and --reason)")
    p.add_argument("--by"), p.add_argument("--reason")
    p.add_argument("--planned", action="append", metavar="OS:MINUTES", help="the hosted runs you plan, replacing the profile's plan in the budget check")
    p.add_argument("--no-receipt", action="store_true")
    p.add_argument("--receipt-out", metavar="FILE", help="dry run only: also write the (non-authorising) receipt here")
    p.add_argument("--out", metavar="FILE", help="also write the JSON report here")
    p.add_argument("-v", "--verbose", action="store_true", help="show details and rationale for passing checks too")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("validate", help="check that a profile is sound (phase one) without running anything")
    _add_common(p, repo=False)
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("checks", help="list the built-in checks and the planned platform slots")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_checks)

    p = sub.add_parser("explain", help="describe one check and its parameters")
    p.add_argument("check")
    p.set_defaults(fn=cmd_explain)

    p = sub.add_parser("gate", help="the dispatch gate: exit 0 only with a fresh real PASS receipt and budget left")
    _add_common(p, repo=False)
    p.add_argument("--sha", required=True)
    p.add_argument("--scope")
    p.add_argument("--stage", help="the stage the dispatch is for (default: the profile's 'stage'); a receipt made at another stage does not count")
    p.add_argument("--receipt", metavar="FILE", help="use this receipt instead of searching the state directory")
    p.add_argument("--planned", action="append", metavar="OS:MINUTES", help="a planned hosted run's OS and timeout, for example windows:180")
    p.add_argument("--override", metavar="NAME", help="proceed despite a refusal; written to the ledger with --reason")
    p.add_argument("--reason")
    p.set_defaults(fn=cmd_gate)

    p = sub.add_parser("receipt", help="inspect a receipt file")
    _add_common(p, repo=False)
    p.add_argument("file")
    p.set_defaults(fn=cmd_receipt)

    p = sub.add_parser("ledger", help="the hosted-run budget ledger: add | show | summary")
    p.add_argument("action", choices=["add", "show", "summary"])
    _add_common(p, repo=False)
    p.add_argument("--run-id"), p.add_argument("--sha"), p.add_argument("--workflow")
    p.add_argument("--os", dest="os_name", help="linux, windows or macos (a key of budget.rates)")
    p.add_argument("--outcome", help="started, success, failed or cancelled")
    p.add_argument("--started", help="UTC time like 2026-09-09T12:00:00Z (default: now)")
    p.add_argument("--minutes", type=float, help="billed minutes, once known")
    p.add_argument("--timeout-min", type=float, dest="timeout_min", help="the job's timeout, used as its worst-case cost until it ends")
    p.add_argument("--failure-class", dest="failure_class", help="deterministic (default) or infrastructure")
    p.add_argument("--failure-text", dest="failure_text", metavar="FILE", help="failure log, matched against the profile's deterministic_failures")
    p.add_argument("--budget-id", dest="budget_id"), p.add_argument("--note")
    p.add_argument("--image", action="append", metavar="LABEL=VERSION", help="with --outcome success: record the runner image as last good")
    p.set_defaults(fn=cmd_ledger)

    p = sub.add_parser("image", help="the last-good runner image record: record | show")
    isub = p.add_subparsers(dest="action", metavar="ACTION")
    isub.required = True
    q = isub.add_parser("record", help="record LABEL=VERSION as the last good image")
    _add_common(q, repo=False)
    q.add_argument("pair", help="LABEL=VERSION, for example windows-2022=20260908.1.0")
    q.add_argument("--run-id", dest="run_id"), q.add_argument("--sha")
    q.set_defaults(fn=cmd_image)
    q = isub.add_parser("from-log", help="read the image from a saved 'Set up job' log ('Image:' and 'Version:' lines)")
    _add_common(q, repo=False)
    q.add_argument("log", metavar="FILE")
    q.add_argument("--run-id", dest="run_id"), q.add_argument("--sha")
    q.set_defaults(fn=cmd_image)
    q = isub.add_parser("show", help="print the recorded last-good images")
    _add_common(q, repo=False)
    q.set_defaults(fn=cmd_image)

    p = sub.add_parser("demo", help="build a synthetic project and walk every state: good, and one known-bad case per check")
    p.add_argument("--keep", action="store_true", help="keep the synthetic project and print where it is")
    p.add_argument("--case", action="append", help="run only this case")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_demo)
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        ap.print_help()
        return EXIT_BAD_SPEC
    try:
        return args.fn(args)
    except SpecError as exc:
        print("seaworthy: %s" % redact(str(exc)), file=sys.stderr)
        return EXIT_BAD_SPEC
    except KeyboardInterrupt:
        return EXIT_ERROR
    except BrokenPipeError:
        return EXIT_PASS
    except Exception as exc:                 # the engine itself failed: say so, never exit 0
        print("seaworthy: internal error: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        if os.environ.get("SEAWORTHY_DEBUG"):
            raise
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
