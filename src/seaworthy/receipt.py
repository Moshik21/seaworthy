"""The receipt: proof that a PASS happened at one SHA, under one profile, recently.

A hosted-dispatch wrapper runs `seaworthy gate`, which refuses unless a valid, fresh, real-run receipt for the SHA
exists. The receipt is tamper-evident (a content hash) and, when the profile names a key file, signed with
HMAC-SHA256. It is a guard against mistakes and stale greens, not against someone who can write the state directory
and read the key.
"""
import hashlib
import hmac
import json
import os
import time

from . import RECEIPT_SCHEMA, VERSION
from .util import SpecError, atomic_write, canonical, now_iso, parse_iso


def _key(profile):
    path = (profile.receipt or {}).get("hmac_key_file")
    if not path:
        return None
    try:
        with open(path, "rb") as fh:
            key = fh.read().strip()
    except OSError as exc:
        raise SpecError("receipt.hmac_key_file %s is not readable: %s" % (path, exc))
    if len(key) < 16:
        raise SpecError("receipt.hmac_key_file must hold at least 16 bytes")
    return key


def _body_hash(body):
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def build(report, profile, scope, sets):
    body = {
        "schema": RECEIPT_SCHEMA[0], "schema_version": RECEIPT_SCHEMA[1], "tool_version": VERSION,
        "mode": report["mode"], "verdict": report["verdict"], "warnings": report["warnings"],
        "sha": report["sha"], "profile": profile.name, "profile_hash": profile.hash,
        "checks_hash": profile.checks_hash(scope), "scope": scope or "all", "stage": report.get("stage"), "sets": sets,
        "at": report["finished"], "host": report["host"],
        "results": [{"id": r["id"], "status": r["status"], "outcome": r["outcome"]} for r in report["checks"]],
        "waivers": report["waivers"],
    }
    receipt = dict(body)
    receipt["receipt_hash"] = _body_hash(body)
    key = _key(profile)
    if key:
        receipt["hmac"] = hmac.new(key, canonical(body).encode("utf-8"), hashlib.sha256).hexdigest()
    return receipt


def receipts_dir(state_dir, sha):
    return os.path.join(state_dir, "receipts", sha)


def write(state_dir, receipt):
    stamp = receipt["at"].replace(":", "").replace("-", "")
    path = os.path.join(receipts_dir(state_dir, receipt["sha"]), "%s.json" % stamp)
    atomic_write(path, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return path


def verify_integrity(receipt, profile):
    """Returns a list of problems with the receipt itself (empty when it is intact)."""
    problems = []
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA[0]:
        return ["not a %s file" % RECEIPT_SCHEMA[0]]
    body = {k: v for k, v in receipt.items() if k not in ("receipt_hash", "hmac")}
    if receipt.get("receipt_hash") != _body_hash(body):
        problems.append("the receipt's content hash does not match its content (edited after it was written)")
    key = _key(profile)
    if key:
        want = hmac.new(key, canonical(body).encode("utf-8"), hashlib.sha256).hexdigest()
        if not receipt.get("hmac") or not hmac.compare_digest(receipt["hmac"], want):
            problems.append("the receipt's HMAC signature is missing or wrong")
    return problems


def load_all_for(state_dir, sha):
    d = receipts_dir(state_dir, sha)
    out = []
    if os.path.isdir(d):
        for name in sorted(os.listdir(d)):
            if name.endswith(".json"):
                try:
                    with open(os.path.join(d, name), "r", encoding="utf-8") as fh:
                        out.append((os.path.join(d, name), json.load(fh)))
                except (OSError, ValueError):
                    continue
    return out


def check_for_dispatch(state_dir, profile, sha, scope, now=None, receipt_path=None, stage=None):
    """The receipt half of the gate: (receipt or None, [reasons it does not qualify])."""
    now = time.time() if now is None else now
    candidates = load_all_for(state_dir, sha)
    if receipt_path:
        with open(receipt_path, "r", encoding="utf-8") as fh:
            candidates = [(receipt_path, json.load(fh))]
    if not candidates:
        return None, ["no receipt exists for %s; run `seaworthy run` for this SHA first" % sha[:12]]
    reasons = []
    max_age = (profile.receipt or {}).get("max_age_hours", 12)
    for path, r in sorted(candidates, key=lambda c: c[1].get("at", ""), reverse=True):
        why = verify_integrity(r, profile)
        if not why:
            if r.get("mode") != "real":
                why.append("it came from a %s, which never authorises spend" % r.get("mode"))
            if r.get("verdict") != "PASS":
                why.append("its verdict is %s" % r.get("verdict"))
            if r.get("sha") != sha:
                why.append("it is for a different SHA")
            if r.get("profile_hash") != profile.hash:
                why.append("the profile changed since it was written (hash differs)")
            if r.get("checks_hash") != profile.checks_hash(None if r.get("scope") in (None, "all") else r.get("scope")):
                why.append("the set of checks changed since it was written")
            if r.get("stage") != stage:
                why.append("it was made at stage '%s', not '%s'" % (r.get("stage") or "none", stage or "none"))
            if scope and r.get("scope") not in ("all", scope):
                why.append("it covers scope '%s', not '%s'" % (r.get("scope"), scope))
            try:
                age_h = (now - parse_iso(r["at"])) / 3600.0
                if age_h > max_age:
                    why.append("it is %.1f hours old; the limit is %s" % (age_h, max_age))
                if age_h < -0.1:
                    why.append("it is dated in the future")
            except (KeyError, ValueError):
                why.append("it has no valid timestamp")
        if not why:
            return (path, r), []
        reasons.append("%s: %s" % (os.path.basename(path), "; ".join(why)))
    return None, reasons[:3]
