"""What a check can touch: commands, sockets and files, behind one door.

Every command a check runs goes through Context.run. That is where the three safety rules live:
  1. no argv array is ever handed to a shell;
  2. an argv that would start or spend on a hosted job is refused outright (guard_argv);
  3. in a dry run, anything that leaves this machine is answered from a fixtures file or reported
     "not run"; it is never executed.
"""
import os
import shutil
import socket
import subprocess
import time

from . import FIXTURES_SCHEMA
from .util import SpecError, load_json, redact


class NotRun(Exception):
    """The check could not run here (for example a dry run with no fixture for a network call)."""


class Refused(Exception):
    """A check tried to run a command the engine never allows."""


class Proc(object):
    def __init__(self, rc, out="", err="", missing=False, timed_out=False, fixture=False):
        self.rc, self.out, self.err = rc, out or "", err or ""
        self.missing, self.timed_out, self.fixture = missing, timed_out, fixture

    @property
    def ok(self):
        return self.rc == 0 and not self.timed_out and not self.missing

    @property
    def text(self):
        return (self.out + ("\n" + self.err if self.err else "")).strip()


# argv patterns that would start, repeat or publish a hosted job. Matched after the tool name.
_GH_DENY = [
    ("workflow", "run"), ("workflow", "enable"), ("workflow", "disable"),
    ("run", "rerun"), ("run", "cancel"), ("run", "delete"),
    ("release", "create"), ("release", "upload"), ("release", "edit"), ("release", "delete"),
    ("pr", "merge"), ("repo", "delete"), ("secret", "set"), ("secret", "delete"),
    ("variable", "set"), ("variable", "delete"),
]
_WRITE_FLAGS = ("-f", "-F", "--field", "--raw-field", "--input")


def guard_argv(argv):
    """Raise Refused when argv could start a hosted job or change anything remote."""
    if not argv or not all(isinstance(a, str) for a in argv):
        raise Refused("argv must be a non-empty list of strings")
    tool = os.path.basename(argv[0]).lower()
    if tool.endswith(".exe") or tool.endswith(".cmd"):
        tool = tool.rsplit(".", 1)[0]
    rest = [a for a in argv[1:]]
    if tool == "gh" or tool.startswith("gh-"):      # the GitHub CLI, or any wrapper named after it
        words = [a for a in rest if not a.startswith("-")]
        for first, second in _GH_DENY:
            if len(words) >= 2 and words[0] == first and words[1] == second:
                raise Refused("refused: 'gh %s %s' can start or change hosted work" % (first, second))
        if words and words[0] == "api":
            for i, a in enumerate(rest):
                if a in ("-X", "--method") and i + 1 < len(rest) and rest[i + 1].upper() != "GET":
                    raise Refused("refused: 'gh api' with a method other than GET")
                if a.upper().startswith("-X") and len(a) > 2 and a[2:].upper() != "GET":
                    raise Refused("refused: 'gh api' with a method other than GET")
                if a in _WRITE_FLAGS:
                    raise Refused("refused: 'gh api' with a request body (that makes it a write)")
    if tool == "git" and rest[:1] == ["push"]:
        raise Refused("refused: 'git push' is never part of a check")
    if tool in ("curl", "wget"):
        for i, a in enumerate(rest):
            if a in ("-X", "--request") and i + 1 < len(rest) and rest[i + 1].upper() not in ("GET", "HEAD"):
                raise Refused("refused: a %s request with a write method" % tool)
            if a in ("-d", "--data", "--data-raw", "--data-binary", "-F", "--form", "-T", "--upload-file"):
                raise Refused("refused: a %s request with a body" % tool)


class Fixtures(object):
    """Recorded answers for the commands and connections a dry run may not make."""

    def __init__(self, entries, base):
        self.entries, self.base, self.used = entries, base, set()

    @classmethod
    def load(cls, path):
        doc = load_json(path, "fixtures file")
        if not isinstance(doc, dict) or doc.get("schema") != FIXTURES_SCHEMA[0]:
            raise SpecError("%s: not a %s file" % (path, FIXTURES_SCHEMA[0]))
        entries = doc.get("entries")
        if not isinstance(entries, list):
            raise SpecError("%s: 'entries' must be a list" % path)
        for i, e in enumerate(entries):
            if not isinstance(e, dict) or e.get("kind") not in ("run", "tcp"):
                raise SpecError("%s: entry %d needs kind 'run' or 'tcp'" % (path, i))
        return cls(entries, os.path.dirname(os.path.abspath(path)))

    def _text(self, entry, key, sha):
        if key + "_file" in entry:
            try:
                with open(os.path.join(self.base, entry[key + "_file"]), "r", encoding="utf-8") as fh:
                    text = fh.read()
            except OSError as exc:
                raise SpecError("fixture file unreadable: %s" % exc)
        else:
            text = entry.get(key, "")
        return text.replace("{sha}", sha or "")      # lets one fixture answer for whichever SHA is being checked

    def run(self, host, argv, sha=""):
        for i, e in enumerate(self.entries):
            if e["kind"] != "run" or e.get("host", "local") != host:
                continue
            if "argv" in e and e["argv"] == argv or "argv_prefix" in e and argv[:len(e["argv_prefix"])] == e["argv_prefix"]:
                self.used.add(i)
                if e.get("timeout"):
                    return Proc(-1, timed_out=True, fixture=True)
                if e.get("missing"):
                    return Proc(127, missing=True, fixture=True)
                return Proc(int(e.get("rc", 0)), self._text(e, "stdout", sha), self._text(e, "stderr", sha), fixture=True)
        return None

    def tcp(self, target):
        for i, e in enumerate(self.entries):
            if e["kind"] == "tcp" and e.get("target") == target:
                self.used.add(i)
                return e.get("result", "ok")
        return None


_BASE_ENV = ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "TMPDIR", "LANG",
             "LC_ALL", "COMSPEC", "PATHEXT", "SSH_AUTH_SOCK", "XDG_CONFIG_HOME", "APPDATA", "LOCALAPPDATA")


class Context(object):
    def __init__(self, repo, sha, dry_run=False, fixtures=None, hosts=None, state_dir=None, profile=None):
        self.repo, self.sha, self.dry_run, self.fixtures = repo, sha, dry_run, fixtures
        self.hosts = hosts or {}
        self.state_dir = state_dir
        self.profile = profile or {}
        self.network_calls = 0        # commands or connections that left this machine
        self.fixture_calls = 0
        self._deadline = None
        self.default_timeout = 10
        self.cost = "free"            # the running check's cost tier, set by the engine
        self.default_host = "local"   # set per check by the engine from the check's own `host`
        self.stage = None             # the release stage being checked (for example publish), set by the engine

    def begin_check(self, timeout_s):
        self._deadline = time.time() + timeout_s

    def _timeout(self, wanted):
        wanted = wanted or self.default_timeout
        if self._deadline is None:
            return wanted
        left = self._deadline - time.time()
        if left <= 0:
            raise NotRun("the check's own time limit was used up")
        return min(wanted, left)

    def host_spec(self, name):
        if name in (None, "", "local"):
            return {"argv_prefix": []}
        if name not in self.hosts:
            raise SpecError("host '%s' is not defined in the profile's hosts" % name)
        return self.hosts[name]

    def run(self, argv, host=None, net=False, timeout=None, cwd=None, env=None):
        """Run argv (a list) on `host`. `net` marks anything that leaves this machine."""
        guard_argv(argv)
        host = host or self.default_host
        spec = self.host_spec(host)
        remote = host != "local"
        net = net or remote
        if self.fixtures is not None and self.dry_run:
            hit = self.fixtures.run(host, argv, self.sha)
            if hit is not None:
                self.fixture_calls += 1
                return hit
        if net and self.dry_run:
            raise NotRun("dry run: '%s' would leave this machine%s and no fixture answers it"
                         % (" ".join(argv[:3]), " (host %s)" % host if remote else ""))
        if self.dry_run and self.cost == "local-heavy":
            raise NotRun("dry run: '%s' is a local-heavy command (minutes of CPU); a dry run only reads a fixture for it"
                         % " ".join(argv[:3]))
        full = list(spec.get("argv_prefix", [])) + list(argv)
        if cwd and not os.path.isabs(cwd):
            cwd = os.path.join(self.repo, cwd)
        if net:
            self.network_calls += 1
        run_env = {k: os.environ[k] for k in _BASE_ENV if k in os.environ}
        run_env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
        for name in (env or []):
            if name in os.environ:
                run_env[name] = os.environ[name]
        # Resolve the program the way a shell would (PATH and, on Windows, PATHEXT) so shims like pnpm.cmd are found.
        exe = shutil.which(full[0], path=run_env.get("PATH"))
        if exe:
            full = [exe] + full[1:]
        try:
            proc = subprocess.run(full, cwd=cwd or self.repo, env=run_env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=self._timeout(timeout))
        except FileNotFoundError:
            return Proc(127, missing=True)
        except subprocess.TimeoutExpired:
            return Proc(-1, timed_out=True)
        except OSError as exc:
            return Proc(126, err=str(exc))
        return Proc(proc.returncode, redact(proc.stdout.decode("utf-8", "replace")),
                    redact(proc.stderr.decode("utf-8", "replace")))

    def tcp(self, target, timeout=5):
        """Open a TCP connection to 'host:port'. Returns 'ok', 'timeout', 'refused' or 'error'."""
        if self.fixtures is not None and self.dry_run:
            hit = self.fixtures.tcp(target)
            if hit is not None:
                self.fixture_calls += 1
                return hit
        if self.dry_run:
            raise NotRun("dry run: a connection to %s would leave this machine and no fixture answers it" % target)
        host, _, port = target.rpartition(":")
        self.network_calls += 1
        try:
            with socket.create_connection((host, int(port)), timeout=self._timeout(timeout)):
                return "ok"
        except socket.timeout:
            return "timeout"
        except ConnectionRefusedError:
            return "refused"
        except (OSError, ValueError):
            return "error"
