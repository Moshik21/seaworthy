"""Running a profile: order the checks, run them cheapest first, and turn the results into a verdict.

Order is by cost: free checks, then cheap-network, then local-heavy. Every check in a tier runs, so one run lists
every cheap problem at once; but once a required check in a tier has not passed, the costlier tiers are not run
(unless --all), because nothing heavy is worth running against a release that is already a no-go.
"""
import socket
import time
import traceback

from . import EXIT_BAD_SPEC, EXIT_ERROR, EXIT_NO_GO, EXIT_PASS, EXIT_WARN, REPORT_SCHEMA, VERSION
from .checks import REGISTRY, PLANNED, Result
from .context import NotRun, Refused
from .profile import COSTS
from .util import SpecError, now_iso, redact

_BLOCKING_STATUSES = ("fail", "unknown", "not-run", "error")
_LEVEL = {"pass": "OK", "skip": "OK", "warn": "WARNING", "unknown": "UNKNOWN", "not-run": "UNKNOWN", "error": "UNKNOWN"}


def _clip(text, n=300):
    text = redact(str(text)).replace("\r", "")
    return text if len(text) <= n else text[:n - 1] + "..."


def _order(checks):
    """Stable order by tier; inside a tier, a check follows the checks it requires."""
    out = []
    for tier in COSTS:
        pending = [c for c in checks if c["cost"] == tier]
        done = {c["id"] for c in out}
        while pending:
            progressed = False
            for c in list(pending):
                if all(r in done or r not in {p["id"] for p in pending} for r in c["requires"]):
                    out.append(c)
                    done.add(c["id"])
                    pending.remove(c)
                    progressed = True
            if not progressed:           # cannot happen after validation; keep the engine total anyway
                out += pending
                break
    return out


def _outcome(c, status, waived):
    if status == "pass":
        return "ok"
    if status == "skip":
        return "skipped"
    if status == "warn":
        return "warning"
    if c["id"] in waived and status == "unknown":
        return "waived"
    return "blocking" if c["required"] else "warning"


def _applies(c, stage):
    """Does the check apply at this stage? A check's own `stages` list wins; otherwise the check type's `not_at`."""
    if stage is None:
        return True
    if c["stages"] is not None:
        return stage in c["stages"]
    spec = REGISTRY.get(c["check"])
    return not (spec and stage in spec.not_at)


_WAIVE = "--waive %s --by NAME --reason TEXT"


def _would_change(c, row):
    """What would change this check's contribution to the verdict, in a sentence. A check's own `next` wins."""
    outcome, status = row["outcome"], row["status"]
    if outcome == "ok" or (outcome == "skipped" and not row.get("next")):
        return None
    if row.get("next"):
        return row["next"]
    if outcome == "waived":
        return "waived for this release only; a new SHA needs its own pass or its own waiver"
    if outcome == "warning" and not c["required"]:
        return "advisory (%s): it does not block; clear what the evidence names for a clean PASS" % (c["advisory_reason"] or "no reason written")
    if status == "warn":
        return "does not block; clear what the evidence names for a clean PASS (exit 0 instead of 4)"
    if status == "fail":
        return "fix what the evidence names and re-run; a fail is never waivable"
    if status == "unknown":
        if c["waivable"]:
            return "answer it by running where the tool or host is available, or waive it: " + (_WAIVE % c["id"])
        return "answer it by running where the tool or host is available; this check is not waivable in this profile"
    if status == "not-run":
        return "re-run with --all, or fix the cheaper check or requirement it waits on"
    return "fix the profile or tool error shown; an error is never waived"


def run(profile, ctx, scope=None, only=None, run_all=False, waivers=None, dry_run=False, stage=None):
    waivers = waivers or {}
    stage = stage or profile.stage
    ctx.stage = stage
    selected = profile.selected(scope, only)
    if only:
        missing = sorted(set(only) - {c["id"] for c in profile.checks})
        if missing:
            raise SpecError("--only names unknown check(s): %s" % ", ".join(missing))
    if not selected:
        raise SpecError("no checks selected (scope %r, only %r)" % (scope, only))
    for wid, w in waivers.items():
        match = [c for c in profile.checks if c["id"] == wid]
        if not match:
            raise SpecError("--waive names unknown check '%s'" % wid)
        if not match[0]["waivable"]:
            raise SpecError("check '%s' is not waivable in this profile (only 'unknown' results of checks marked waivable can be waived)" % wid)
        allowed = profile.waivers.get("allowed_by")
        if allowed and w["by"] not in allowed:
            raise SpecError("'%s' may not waive checks; the profile allows: %s" % (w["by"], ", ".join(allowed)))
    started = now_iso()
    t_run = time.time()
    status_of, rows, tier_ms = {}, [], {t: 0 for t in COSTS}
    gate_closed = False
    na_ids = set()
    for tier in COSTS:
        tier_checks = [c for c in _order(selected) if c["cost"] == tier]
        for c in tier_checks:
            row = {"id": c["id"], "check": c["check"], "phase": c["cost"], "required": c["required"], "scope": c["scope"],
                   "host": c["host"], "rationale": c["rationale"], "took_ms": 0, "details": [], "data": {}}
            if not c["required"]:
                row["advisory_reason"] = c["advisory_reason"]
            skip_reason = None
            if not _applies(c, stage):
                status, evidence, row = _execute(c, ctx, row, na=True)
                na_ids.add(c["id"])
                row["applies"] = False
                row["status"], row["evidence"] = status, _clip(evidence)
                row["outcome"] = _outcome(c, status, waivers)
                row["level"] = _LEVEL.get(status) or "WARNING"
                status_of[c["id"]] = status
                tier_ms[tier] += row["took_ms"]
                rows.append(row)
                continue
            if gate_closed and not run_all:
                skip_reason = "not run: a cheaper required check has not passed (use --all to run anyway)"
            else:
                for r in c["requires"]:
                    if r in selected_ids(selected) and r not in na_ids and status_of.get(r) not in ("pass", "warn"):
                        skip_reason = "not run: requires '%s' (%s)" % (r, status_of.get(r, "not selected"))
                        break
            if skip_reason:
                status, evidence = "not-run", skip_reason
            elif c["planned"] or c["check"] not in REGISTRY:
                status, evidence = "not-run", "not run: '%s' is a platform check planned for a later release" % c["check"]
            else:
                status, evidence, row = _execute(c, ctx, row)
            row["status"], row["evidence"] = status, _clip(evidence)
            row["outcome"] = _outcome(c, status, waivers)
            row["level"] = _LEVEL.get(status) or ("CRITICAL" if c["required"] else "WARNING")
            status_of[c["id"]] = status
            tier_ms[tier] += row["took_ms"]
            rows.append(row)
        if any(r["outcome"] == "blocking" and r["phase"] == tier for r in rows):
            gate_closed = True
    if not only:
        for c in profile.checks:
            if not c["enabled"] and (not scope or c["scope"] in ("any", scope)):
                rows.append({"id": c["id"], "check": c["check"], "phase": c["cost"], "required": c["required"], "scope": c["scope"],
                             "host": c["host"], "rationale": c["rationale"], "took_ms": 0, "details": [], "data": {},
                             "status": "skip", "outcome": "skipped", "level": "OK",
                             "evidence": "disabled in the profile%s" % (" (planned platform check)" if c["planned"] else "")})
    by_id = {c["id"]: c for c in profile.checks}
    for r in rows:
        r["would_change"] = _would_change(by_id[r["id"]], r)
        r.pop("next", None)
    blocking = [r["id"] for r in rows if r["outcome"] == "blocking"]
    warnings = [r["id"] for r in rows if r["outcome"] == "warning"]
    errors = [r["id"] for r in rows if r["status"] == "error"]
    if errors:
        exit_code, verdict = EXIT_ERROR, "NO-GO"
    elif blocking:
        exit_code, verdict = EXIT_NO_GO, "NO-GO"
    else:
        verdict, exit_code = "PASS", (EXIT_WARN if warnings else EXIT_PASS)
    counts = {k: 0 for k in ("pass", "warn", "fail", "unknown", "skip", "not-run", "error")}
    for r in rows:
        counts[r["status"]] += 1
    counts["waived"] = sum(1 for r in rows if r["outcome"] == "waived")
    finished = now_iso()
    gate_value = "pass" if verdict == "PASS" else ("fail" if any(r["status"] == "fail" and r["outcome"] == "blocking" for r in rows) else "unknown")
    return {
        "schema": REPORT_SCHEMA[0], "schema_version": REPORT_SCHEMA[1], "tool_version": VERSION,
        "profile": {"name": profile.name, "hash": profile.hash, "checks_hash": profile.checks_hash(scope) if not only else None},
        "sha": ctx.sha or None, "mode": "dry-run" if dry_run else "real", "scope": scope or "all", "stage": stage, "partial": bool(only),
        "started": started, "finished": finished, "elapsed_ms": int((time.time() - t_run) * 1000), "host": socket.gethostname(),
        "verdict": verdict, "exit_code": exit_code, "counts": counts, "blocking": blocking, "warnings": warnings,
        "network_calls": ctx.network_calls, "fixture_calls": ctx.fixture_calls, "tier_ms": tier_ms,
        "checks": rows, "waivers": [dict(id=k, **v) for k, v in sorted(waivers.items())],
        "waiver_syntax": _waiver_syntax(rows, verdict, by_id),
        "spec_warnings": list(profile.warnings),
        "gate": {"value": gate_value, "as_of": finished,
                 "note": "%s: %d blocking, %d warning(s)" % (profile.name, len(blocking), len(warnings))},
        "receipt": None,
    }


def _waiver_syntax(rows, verdict, by_id):
    """How to waive, printed once. Only 'unknown' results of checks marked waivable can be waived, one named check at a
    time, and a waiver is recorded against this SHA (one release)."""
    open_rows = [r for r in rows if r["outcome"] == "blocking"]
    if verdict == "PASS" or not open_rows:
        return None
    ids = [r["id"] for r in open_rows if r["status"] == "unknown" and by_id[r["id"]]["waivable"]]
    return {"syntax": "seaworthy run PROFILE --sha SHA --waive CHECK_ID [--waive CHECK_ID ...] --by NAME --reason TEXT",
            "waivable_now": ids,
            "rules": "a waiver names each check, covers only this SHA (one release), is written in the report and the receipt, "
                     "and applies only to an 'unknown' result of a check the profile marks waivable; a fail is never waivable"}


def selected_ids(selected):
    return {c["id"] for c in selected}


def _not_applicable(c, stage):
    """The result of a check that does not apply at this stage and has nothing of its own to say there."""
    if c["stages"] is not None:
        why = "it applies at stage(s) %s" % ", ".join(c["stages"])
    else:
        why = "its credential or input is not what this stage needs"
    return Result("skip", "not applicable at stage '%s': %s" % (stage, why))


def _execute(c, ctx, row, na=False):
    spec = REGISTRY.get(c["check"])
    ctx.default_host, ctx.cost = c["host"], c["cost"]
    ctx.begin_check(c["timeout_s"])
    t0 = time.time()
    try:
        if na:
            res = spec.na(ctx, c["params"]) if spec and spec.na else _not_applicable(c, ctx.stage)
        else:
            res = spec.fn(ctx, c["params"])
        status, evidence = res.status, res.evidence
        row["next"] = res.next
        row["details"] = [_clip(d) for d in res.details[:20]]
        row["data"] = res.data
    except NotRun as exc:
        status, evidence = "not-run", "not run: %s" % exc
    except Refused as exc:
        status, evidence = "error", str(exc)
    except SpecError as exc:
        status, evidence = "error", "misconfigured: %s" % exc
    except Exception as exc:                # a crashing check is an error to show, never a pass
        status, evidence = "error", "check crashed: %s: %s" % (type(exc).__name__, exc)
        row["details"] = [_clip(l) for l in traceback.format_exc().splitlines()[-4:]]
    finally:
        ctx.default_host, ctx.cost = "local", "free"
        ctx._deadline = None
    row["took_ms"] = int((time.time() - t0) * 1000)
    return status, evidence, row
