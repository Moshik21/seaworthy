"""The report, as plain text (colour only on a terminal, never required) and as JSON."""
import json
import os
import sys

_LABEL = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "unknown": "UNKNOWN", "skip": "SKIP", "not-run": "NOT RUN", "error": "ERROR"}
_COLOUR = {"PASS": "32", "WARN": "33", "FAIL": "31", "UNKNOWN": "35", "SKIP": "36", "NOT RUN": "35", "ERROR": "31;1", "WAIVED": "33"}
_TIERS = (("free", "FREE CHECKS"), ("cheap-network", "CHEAP NETWORK CHECKS"), ("local-heavy", "LOCAL HEAVY CHECKS"))


def use_colour(stream):
    return stream.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"


def to_json(report):
    return json.dumps(report, indent=2, sort_keys=True)


def to_text(report, verbose=False, colour=False):
    def paint(text, label):
        return "\033[%sm%s\033[0m" % (_COLOUR[label], text) if colour and label in _COLOUR else text

    out = []
    mode = "DRY RUN" if report["mode"] == "dry-run" else "REAL RUN"
    out.append("Seaworthy %s   profile %s   sha %s   %s" % (report["tool_version"], report["profile"]["name"],
                                                         (report["sha"] or "(none given)")[:12], mode))
    out.append("started %s on %s   scope %s%s%s" % (report["started"], report["host"], report["scope"],
                                                    "   stage %s" % report["stage"] if report.get("stage") else "",
                                                    "   PARTIAL (--only): no receipt" if report["partial"] else ""))
    for tier, title in _TIERS:
        rows = [r for r in report["checks"] if r["phase"] == tier]
        if not rows:
            continue
        out.append("")
        out.append("%s  (%d ms)" % (title, report["tier_ms"][tier]))
        width = max(len(r["id"]) for r in rows)
        for r in rows:
            label = "WAIVED" if r["outcome"] == "waived" else _LABEL[r["status"]]
            if r["outcome"] == "warning" and r["status"] in ("fail", "unknown", "not-run"):
                label = "WARN"          # an advisory check that did not pass is a warning, with its written reason below
            adv = "" if r["required"] else "  [advisory]"
            out.append("  %s  %-*s  %s%s" % (paint("%-8s" % label, label), width, r["id"], r["evidence"], adv))
            if r["outcome"] == "warning" and r.get("advisory_reason") and r["status"] != "warn":
                out.append("  %s  %-*s    advisory because: %s (check status: %s)" % (" " * 8, width, "", r["advisory_reason"], _LABEL[r["status"]]))
            if r["status"] != "pass" or verbose:
                for d in r["details"]:
                    out.append("  %s  %-*s    - %s" % (" " * 8, width, "", d))
            if r.get("would_change"):
                out.append("  %s  %-*s    would change: %s" % (" " * 8, width, "", r["would_change"]))
            if verbose and r.get("rationale"):
                out.append("  %s  %-*s    why: %s" % (" " * 8, width, "", r["rationale"]))
    out.append("")
    c = report["counts"]
    parts = ["%d %s" % (c[k], k) for k in ("pass", "warn", "fail", "unknown", "not-run", "skip", "error") if c[k]]
    if c["waived"]:
        parts.append("%d waived" % c["waived"])
    headline = "%s  %s" % (report["verdict"], ", ".join(parts))
    out.append("VERDICT: " + paint(headline, "PASS" if report["verdict"] == "PASS" else "FAIL"))
    if report["blocking"]:
        out.append("  blocking: " + ", ".join(report["blocking"]))
    if report["warnings"]:
        out.append("  warnings: " + ", ".join(report["warnings"]))
    for w in report["waivers"]:
        out.append("  waived %s by %s: %s" % (w["id"], w["by"], w["reason"]))
    ws = report.get("waiver_syntax")
    if ws:
        out.append("  to waive: " + ws["syntax"])
        out.append("    " + ws["rules"])
        out.append("    waivable among the blocking checks now: " + (", ".join(ws["waivable_now"]) or "none"))
    if report["mode"] == "dry-run":
        out.append("  dry run: %d command(s) left this machine, %d answered from fixtures; a dry run never writes a receipt"
                   % (report["network_calls"], report["fixture_calls"]))
    else:
        out.append("  %d command(s) left this machine; no check starts a hosted job" % report["network_calls"])
    if report.get("receipt"):
        out.append("  receipt: %s" % report["receipt"]["path"])
    elif report.get("receipt_note") and report["mode"] != "dry-run" and report["verdict"] == "PASS":
        out.append("  " + report["receipt_note"])
    for w in report.get("spec_warnings", []):
        out.append("  profile note: " + w)
    out.append("  exit code %d  (0 pass, 1 error, 2 bad spec, 3 no-go, 4 pass with warnings)" % report["exit_code"])
    return "\n".join(out)
