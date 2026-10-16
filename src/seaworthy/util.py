"""Small shared helpers: JSON with line comments, hashing, redaction, paths, time."""
import calendar
import hashlib
import json
import os
import re
import sys
import time


class SpecError(Exception):
    """The profile, fixtures or a command line is wrong (engine exit code 2)."""


def now_iso(epoch=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if epoch is None else epoch))


def parse_iso(text):
    return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))


def strip_line_comments(text):
    """Blank out whole lines that start with // or #, keeping line numbers. JSON strings cannot span
    lines, so a line that starts with a comment marker can never be inside a string."""
    out = []
    for line in text.split("\n"):
        s = line.lstrip()
        out.append("" if s.startswith("//") or s.startswith("#") else line)
    return "\n".join(out)


def load_json(path, what):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise SpecError("cannot read %s %s: %s" % (what, path, exc))
    try:
        return json.loads(strip_line_comments(text.lstrip("﻿")))
    except ValueError as exc:
        raise SpecError("%s %s is not valid JSON: %s" % (what, path, exc))


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256(obj):
    data = obj if isinstance(obj, bytes) else (obj if isinstance(obj, str) else canonical(obj)).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


_REDACT = [
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)\b((?:password|passwd|secret|api[_-]?key|token|app_specific_password)[A-Za-z_]*)\b(\s*[:=]\s*)\S{6,}"),
]


def redact(text):
    """Remove anything that looks like a credential before it is stored or printed."""
    if not text:
        return text
    for pat in _REDACT:
        if pat.groups >= 2:
            text = pat.sub(lambda m: m.group(1) + m.group(2) + "[redacted]", text)
        else:
            text = pat.sub("[redacted]", text)
    return text


def first_line(text, limit=200):
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line[:limit]
    return ""


def default_state_root():
    env = os.environ.get("SEAWORTHY_STATE")
    if env:
        return env
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "seaworthy")


def atomic_write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = "%s.tmp%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def version_tuple(text):
    return tuple(int(p) for p in re.findall(r"\d+", text)[:4])
