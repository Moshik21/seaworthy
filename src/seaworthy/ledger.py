"""The hosted-run budget ledger: append-only JSON lines in the state directory, one per hosted run event, override
and gate decision.

A run is recorded when it is dispatched (outcome 'started') and again when it ends; the latest line for a run id
wins. Money is estimated, not billed: minutes (rounded up) times a per-OS rate from the profile. A run that has
not ended counts at its worst case (its timeout), so a hung run cannot hide in the arithmetic.
"""
import json
import math
import os
import time

from . import LEDGER_SCHEMA
from .util import SpecError, now_iso, parse_iso

DEFAULT_RATES = {"linux": 0.006, "windows": 0.010, "macos": 0.062}   # USD per minute, standard hosted runners
OUTCOMES = ("started", "success", "failed", "cancelled")
FAILURE_CLASSES = ("deterministic", "infrastructure")


def ledger_path(state_dir):
    return os.path.join(state_dir, "ledger.jsonl")


def read(state_dir):
    path = ledger_path(state_dir)
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                raise SpecError("%s line %d is not JSON; the ledger is append-only, fix or remove that line" % (path, n))
    return rows


def append(state_dir, entry):
    entry = dict(entry)
    entry["schema"], entry["schema_version"] = LEDGER_SCHEMA
    entry.setdefault("at", now_iso())
    os.makedirs(state_dir, exist_ok=True)
    line = json.dumps(entry, sort_keys=True) + "\n"
    fd = os.open(ledger_path(state_dir), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))      # one write: concurrent appenders cannot interleave a line
    finally:
        os.close(fd)
    return entry


def runs(state_dir):
    """Latest line per run id, in the order runs first appeared."""
    order, latest = [], {}
    for row in read(state_dir):
        if row.get("kind") != "run":
            continue
        rid = row.get("run_id")
        if rid not in latest:
            order.append(rid)
        latest[rid] = row
    return [latest[r] for r in order]


def overrides(state_dir):
    return [r for r in read(state_dir) if r.get("kind") == "override"]


def estimate(minutes, os_name, rates):
    rate = rates.get(os_name)
    if rate is None:
        raise SpecError("no rate for OS '%s'; profile budget.rates has %s" % (os_name, ", ".join(sorted(rates)) or "none"))
    return round(math.ceil(minutes) * rate, 4)


def run_cost(row, rates):
    if row.get("outcome") == "started" or row.get("minutes") is None:
        if row.get("timeout_min"):
            return estimate(row["timeout_min"], row.get("os", "linux"), rates)     # worst case
        return 0.0
    return row.get("est_cost") if row.get("est_cost") is not None else estimate(row["minutes"], row.get("os", "linux"), rates)


def in_window(row, window, now):
    if window in (None, "", "all"):
        return True
    try:
        at = parse_iso(row.get("started") or row.get("at"))
    except (ValueError, TypeError):
        return True        # an undated row is counted, never silently dropped
    if window == "calendar-month":
        return time.gmtime(at)[:2] == time.gmtime(now)[:2]
    if window.startswith("since:"):
        return at >= parse_iso(window[6:] + "T00:00:00Z")
    raise SpecError("budget.window must be 'all', 'calendar-month' or 'since:YYYY-MM-DD', not '%s'" % window)


def summarize(state_dir, budget, now=None):
    """Spend and run count for the budget. Money is summed over the window (plus any carried_spend recorded before the
    ledger began); the run cap can be scoped to one release with runs_budget_id, since 'at most 2 hosted runs' is
    normally a per-release rule while the dollar budget is shared."""
    now = time.time() if now is None else now
    rates = dict(DEFAULT_RATES)
    rates.update(budget.get("rates", {}))
    rows = [r for r in runs(state_dir) if in_window(r, budget.get("window", "all"), now)]
    if budget.get("budget_id"):
        rows = [r for r in rows if r.get("budget_id") == budget["budget_id"]]
    carried = float(budget.get("carried_spend", 0) or 0)
    spent = round(sum(run_cost(r, rates) for r in rows) + carried, 4)
    counted = rows
    if budget.get("runs_budget_id"):
        counted = [r for r in rows if r.get("budget_id") == budget["runs_budget_id"]]
    return {"runs_used": len(counted), "spent": spent, "carried": carried, "rows": rows, "rates": rates,
            "cap": budget.get("cap"), "max_runs": budget.get("max_runs"), "currency": budget.get("currency", "USD")}


GATE_REASONS_MAX, GATE_REASON_CHARS = 8, 300     # a gate row stays a few KB however long the reasons are


def gate_row(sha, scope, decision, reasons, receipt, now, planned, plan_known, summary, projected):
    """The one ledger row a gate decision writes (GO, GO (OVERRIDDEN) or REFUSED).

    minutes_not_dispatched_upper is the sum of the planned runs' timeout_min (the worst case of what the refusal held
    back), only for a REFUSED decision with a plan the caller actually gave (--planned or budget.planned). A GO holds
    nothing back: 0. A plan that was not given, or sums to 0 minutes, is null with the reason in minutes_basis, never a guess.
    """
    runs_left = None if summary["max_runs"] is None else summary["max_runs"] - summary["runs_used"]
    cap_left = None if summary["cap"] is None else round(summary["cap"] - summary["spent"], 4)
    row = {"kind": "gate", "sha": sha, "scope": scope or "all", "decision": decision, "at": now_iso(now),
           "reasons": [r[:GATE_REASON_CHARS] for r in reasons[:GATE_REASONS_MAX]],
           "planned": [{"os": j.get("os", "linux"), "timeout_min": j["timeout_min"]} for j in planned] if plan_known else None,
           "budget": {"runs_used": summary["runs_used"], "max_runs": summary["max_runs"], "runs_left": runs_left,
                      "spent": summary["spent"], "cap": summary["cap"], "cap_left": cap_left,
                      "currency": summary["currency"], "projected_worst_case": projected}}
    if receipt:
        path, doc = receipt
        try:
            age_s = max(0, int(now - parse_iso(doc["at"])))
        except (KeyError, ValueError, TypeError):
            age_s = None
        row["receipt"] = {"id": doc.get("receipt_hash"), "at": doc.get("at"), "age_s": age_s}
    else:
        row["receipt"] = None
        row["receipt_unknown"] = "no receipt qualified; the reasons say why"
    if decision != "REFUSED":
        row["minutes_not_dispatched_upper"], row["minutes_basis"] = 0, "not refused: this decision held nothing back"
    elif not plan_known:
        row["minutes_not_dispatched_upper"], row["minutes_basis"] = None, "no plan: neither --planned nor budget.planned was given"
    else:
        total = round(sum(float(j["timeout_min"]) for j in planned), 3)
        if total > 0:
            row["minutes_not_dispatched_upper"] = total
            row["minutes_basis"] = "sum of planned[].timeout_min (worst-case timeouts), an upper bound"
        else:
            row["minutes_not_dispatched_upper"], row["minutes_basis"] = None, "the planned timeouts sum to 0 minutes"
    return row


def project(planned, rates):
    """Worst case for planned runs: each job's timeout times its OS rate."""
    total = 0.0
    for job in planned:
        total += estimate(job["timeout_min"], job.get("os", "linux"), rates)
    return round(total, 4)
