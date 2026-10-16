"""Loading, overriding, validating and hashing a profile. Phase one of a run: is the spec itself sound?"""
import copy
import json
import re

from . import PROFILE_SCHEMA
from .checks import PLANNED, REGISTRY, REQUIRED, load_all
from .util import SpecError, load_json, sha256

COSTS = ("free", "cheap-network", "local-heavy")
DEFAULT_TIMEOUT = {"free": 10, "cheap-network": 30, "local-heavy": 900}
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_VAR = re.compile(r"\$\{([A-Za-z0-9_]+)\}")
_TYPES = {"str": str, "int": int, "float": (int, float), "bool": bool, "list": list, "dict": dict}


def subst(value, variables):
    if isinstance(value, str):
        def rep(m):
            if m.group(1) not in variables:
                raise SpecError("${%s} is not a known parameter (profile params: %s)" % (m.group(1), ", ".join(sorted(variables)) or "none"))
            return str(variables[m.group(1)])
        return _VAR.sub(rep, value)
    if isinstance(value, list):
        return [subst(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: subst(v, variables) for k, v in value.items()}
    return value


def _coerce(name, typ, value):
    if isinstance(value, str) and typ in ("int", "float", "bool", "list", "dict"):
        try:
            value = {"bool": lambda v: {"true": True, "false": False}[v.lower()],
                     "int": int, "float": float, "list": json.loads, "dict": json.loads}[typ](value)
        except (KeyError, ValueError):
            raise SpecError("parameter '%s' should be %s, got '%s'" % (name, typ, value))
    ok = isinstance(value, _TYPES[typ]) and not (typ in ("int", "float") and isinstance(value, bool))
    if not ok:
        raise SpecError("parameter '%s' should be %s, got %s" % (name, typ, type(value).__name__))
    return value


def apply_sets(doc, sets):
    """--set name=value edits params; --set a.b=value edits a nested profile field (budget.cap=5)."""
    for key, raw in sets.items():
        try:
            val = json.loads(raw)
        except ValueError:
            val = raw
        if "." not in key:
            doc.setdefault("params", {})[key] = raw if not isinstance(val, (dict, list)) else val
            continue
        node = doc
        parts = key.split(".")
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                raise SpecError("--set %s: '%s' is not an object in the profile" % (key, part))
            node = node[part]
        node[parts[-1]] = val


class Profile(object):
    def __init__(self, path, doc, sets, builtins):
        self.path, self.sets = path, dict(sets)
        self.doc = doc
        self.name = doc["name"]
        self.hash = sha256(doc)
        self.variables = dict(doc.get("params", {}))
        self.variables.update(builtins)
        self.hosts = subst(doc.get("hosts", {}), self.variables)
        self.budget = subst(doc.get("budget", {}), self.variables)
        self.receipt = subst(doc.get("receipt", {}), self.variables)
        self.deterministic_failures = doc.get("deterministic_failures", [])
        self.stage = doc.get("stage") or None
        self.waivers = doc.get("waivers", {})
        self.warnings = []
        self.checks = []

    def selected(self, scope=None, only=None):
        out = []
        for c in self.checks:
            if not c["enabled"]:
                continue
            if scope and c["scope"] not in ("any", scope):
                continue
            if only and c["id"] not in only:
                continue
            out.append(c)
        return out

    def checks_hash(self, scope=None):
        return sha256([c["raw"] for c in self.selected(scope)])


def load(path, sets=None, builtins=None):
    load_all()
    doc = load_json(path, "profile")
    sets = sets or {}
    if not isinstance(doc, dict):
        raise SpecError("%s: a profile is a JSON object" % path)
    if doc.get("schema") != PROFILE_SCHEMA[0]:
        raise SpecError("%s: 'schema' must be \"%s\"" % (path, PROFILE_SCHEMA[0]))
    if str(doc.get("schema_version", "")).split(".")[0] != PROFILE_SCHEMA[1].split(".")[0]:
        raise SpecError("%s: schema_version %s is not supported (this tool reads %s.x)" % (path, doc.get("schema_version"), PROFILE_SCHEMA[1].split(".")[0]))
    if not isinstance(doc.get("name"), str) or not _ID.match(doc["name"]):
        raise SpecError("%s: 'name' must be a short lowercase identifier" % path)
    apply_sets(doc, sets)
    prof = Profile(path, doc, sets, builtins or {})
    _validate(prof, doc)
    return prof


def _validate(prof, doc):
    errors = []
    for hname, h in prof.hosts.items():
        if not isinstance(h, dict) or not isinstance(h.get("argv_prefix", []), list) or not all(isinstance(a, str) for a in h.get("argv_prefix", [])):
            errors.append("host '%s': argv_prefix must be a list of strings" % hname)
        elif h.get("shell", "cmd") not in ("cmd", "powershell"):
            errors.append("host '%s': shell must be \"cmd\" or \"powershell\" (the host's remote shell)" % hname)
    b = prof.budget
    if b:
        for k in ("cap", "carried_spend"):
            if k in b and (isinstance(b[k], bool) or not isinstance(b[k], (int, float))):
                errors.append("budget.%s must be a number" % k)
        if "max_runs" in b and (isinstance(b["max_runs"], bool) or not isinstance(b["max_runs"], int)):
            errors.append("budget.max_runs must be an integer")
        for os_name, rate in b.get("rates", {}).items():
            if not isinstance(rate, (int, float)):
                errors.append("budget.rates.%s must be a number" % os_name)
    if prof.stage is not None and (not isinstance(prof.stage, str) or not _ID.match(prof.stage)):
        errors.append("'stage' must be a short lowercase identifier such as publish")
    checks = doc.get("checks")
    if not isinstance(checks, list) or not checks:
        raise SpecError("%s: 'checks' must be a non-empty list" % prof.path)
    ids = {}
    for i, raw in enumerate(checks):
        where = "checks[%d]" % i
        if not isinstance(raw, dict):
            errors.append("%s: not an object" % where)
            continue
        cid = raw.get("id")
        if not isinstance(cid, str) or not _ID.match(cid):
            errors.append("%s: 'id' must be a short lowercase identifier" % where)
            continue
        where = "check '%s'" % cid
        if cid in ids:
            errors.append("%s: duplicate id" % where)
        ids[cid] = raw
        kind = raw.get("check")
        spec = REGISTRY.get(kind)
        planned = kind in PLANNED
        if spec is None and not planned:
            errors.append("%s: unknown check type '%s' (see `seaworthy checks`)" % (where, kind))
            continue
        cost = raw.get("cost", spec.cost if spec else "free")
        if cost not in COSTS:
            errors.append("%s: cost must be one of %s" % (where, ", ".join(COSTS)))
            cost = "free"
        host = raw.get("host", "local")
        if host != "local" and host not in prof.hosts:
            errors.append("%s: host '%s' is not defined in 'hosts'" % (where, host))
        stages = raw.get("stages")
        if stages is not None and (not isinstance(stages, list) or not all(isinstance(x, str) for x in stages)):
            errors.append("%s: 'stages' must be a list of stage names" % where)
            stages = None
        enabled = raw.get("enabled", True)
        if planned and enabled:
            prof.warnings.append("%s: '%s' is a planned platform check; it will be reported 'not run'" % (where, kind))
        if not raw.get("rationale"):
            prof.warnings.append("%s: no 'rationale' (name the incident that put this check here)" % where)
        if raw.get("required", True) is False and not (isinstance(raw.get("advisory_reason"), str) and raw["advisory_reason"].strip()):
            errors.append("%s: 'required': false needs a written 'advisory_reason' (why this check may fail without stopping the release)" % where)
        params = {}
        try:
            if spec:
                params = _resolve_params(prof, spec, raw.get("params", {}), where)
                named = params.get("host")
                if isinstance(named, str) and "host" in spec.params and named != "local" and named not in prof.hosts:
                    errors.append("%s: host '%s' is not defined in 'hosts'" % (where, named))
            elif raw.get("params"):
                params = subst(raw["params"], prof.variables)
        except SpecError as exc:
            errors.append(str(exc))
        prof.checks.append({
            "id": cid, "check": kind, "params": params, "host": host, "cost": cost, "enabled": bool(enabled),
            "required": bool(raw.get("required", True)), "waivable": bool(raw.get("waivable", False)),
            "scope": raw.get("scope", "any"), "stages": stages, "requires": list(raw.get("requires", [])),
            "timeout_s": raw.get("timeout_s", DEFAULT_TIMEOUT[cost]), "rationale": raw.get("rationale", ""),
            "advisory_reason": raw.get("advisory_reason", "") if not raw.get("required", True) else "",
            "planned": planned, "raw": copy.deepcopy(raw)})
    tier = {c["id"]: COSTS.index(c["cost"]) for c in prof.checks}
    for c in prof.checks:
        for r in c["requires"]:
            if r not in tier:
                errors.append("check '%s' requires unknown check '%s'" % (c["id"], r))
            elif tier[r] > tier[c["id"]]:
                errors.append("check '%s' (%s) cannot require '%s' (%s): a check may only require same-cost or cheaper checks"
                              % (c["id"], c["cost"], r, COSTS[tier[r]]))
    graph = {c["id"]: [r for r in c["requires"] if r in tier] for c in prof.checks}
    state = {}

    def visit(n, trail):
        if state.get(n) == 1:
            errors.append("requires cycle: %s" % " -> ".join(trail + [n]))
            return
        if state.get(n) == 2:
            return
        state[n] = 1
        for m in graph[n]:
            visit(m, trail + [n])
        state[n] = 2
    for n in graph:
        visit(n, [])
    if errors:
        raise SpecError("profile %s is not valid:\n  - %s" % (prof.path, "\n  - ".join(errors)))


def _resolve_params(prof, spec, given, where):
    if not isinstance(given, dict):
        raise SpecError("%s: 'params' must be an object" % where)
    unknown = sorted(set(given) - set(spec.params))
    if unknown:
        raise SpecError("%s: unknown parameter(s) %s for %s (it takes: %s)" % (where, ", ".join(unknown), spec.name, ", ".join(sorted(spec.params))))
    out = {}
    for name, (typ, default, _desc) in spec.params.items():
        if name in given:
            value = subst(given[name], prof.variables)
        elif default is REQUIRED:
            raise SpecError("%s: parameter '%s' is required for %s" % (where, name, spec.name))
        else:
            value = subst(copy.deepcopy(default), prof.variables)
        out[name] = _coerce("%s.%s" % (where, name), typ, value)
    return out
