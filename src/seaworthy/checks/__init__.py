"""The check registry. A check is a function `fn(ctx, params) -> Result` plus a spec saying what its
parameters are, so `seaworthy explain` and profile validation come from one source."""

STATUSES = ("pass", "warn", "fail", "unknown", "skip", "not-run", "error")


class Result(object):
    def __init__(self, status, evidence, details=None, data=None, next=None):
        assert status in STATUSES, status
        self.status, self.evidence = status, evidence
        self.details = list(details or [])
        self.data = data or {}
        self.next = next              # what would change this result: shown by the report under a non-pass check


def ok(evidence, details=None, next=None, **data):
    return Result("pass", evidence, details, data, next)


def warn(evidence, details=None, next=None, **data):
    return Result("warn", evidence, details, data, next)


def fail(evidence, details=None, next=None, **data):
    return Result("fail", evidence, details, data, next)


def unknown(evidence, details=None, next=None, **data):
    return Result("unknown", evidence, details, data, next)


class CheckSpec(object):
    def __init__(self, name, fn, summary, params, cost, network, doc, not_at=(), na=None):
        self.name, self.fn, self.summary, self.params = name, fn, summary, params
        self.cost, self.network, self.doc = cost, network, doc
        self.not_at, self.na = tuple(not_at), na      # stages where the check does not apply, and what it says there


REGISTRY = {}

# Names reserved for platform checks that are not built yet. A profile may list them (disabled) so its shape does not
# change when they arrive; an enabled one is reported "not run". Empty since the platform checks shipped.
PLANNED = {}


def cannot_check(reason, details=None, next=None, **data):
    """The check cannot be answered on this machine (a tool or host is missing). Always `unknown`, never a pass."""
    return Result("unknown", "cannot check here: " + reason, details, data, next)


def register(name, summary, params, cost="free", network=False, not_at=(), na=None):
    """params: {name: (type, default, description)}; default REQUIRED means the profile must give it.
    not_at: stage names where the check does not apply; there `na(ctx, params)` (when given) says what it can, and
    otherwise the check is skipped with the reason. A profile can override the list with the check's own `stages`."""
    def deco(fn):
        REGISTRY[name] = CheckSpec(name, fn, summary, params, cost, network, (fn.__doc__ or "").strip(), not_at, na)
        return fn
    return deco


REQUIRED = object()


def load_all():
    from . import apple, external, git, github, hosts, release, tools, windows, workflow  # noqa: F401
    return REGISTRY
