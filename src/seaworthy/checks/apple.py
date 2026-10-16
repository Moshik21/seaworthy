"""Apple platform checks: the notarization credential and agreement, the Developer ID identity, and whether
notarization is configured at all. All read-only: they list history, list keychain items and read environment variable
*names*; they never submit, sign, or print a secret value."""
import base64
import calendar
import json
import os
import re
import time

from . import Result, cannot_check, fail, ok, register, unknown, warn
from ..context import NotRun
from ..util import first_line

# ---- notarytool history: one read-only call that answers three different questions --------------------------------

_AGREEMENT = "required agreement is missing or has expired"


def _history_argv(ctx, p):
    """The history command and the env names it needs, or (None, reason) when a variable is not set."""
    if p["keychain_profile"]:
        return ["xcrun", "notarytool", "history", "--keychain-profile", p["keychain_profile"], "--output-format", "json"], []
    missing = [n for n in (p["apple_id_env"], p["team_id_env"], p["password_env"]) if not os.environ.get(n)]
    if missing:
        return None, "no keychain_profile was given and %s %s not set in this environment" % (
            ", ".join(missing), "is" if len(missing) == 1 else "are")
    # The password is passed as an @env: reference so its value never appears in an argument list.
    return ["xcrun", "notarytool", "history", "--apple-id", os.environ[p["apple_id_env"]],
            "--team-id", os.environ[p["team_id_env"]], "--password", "@env:" + p["password_env"],
            "--output-format", "json"], [p["password_env"]]


def _classify(r):
    """Turn a finished notarytool history call into (kind, evidence). kind is one of: ok, no-tool, timeout,
    no-profile, bad-credential, wrong-team, agreement, other."""
    if r.missing:
        return "no-tool", "xcrun is not installed (notarization is checked on a Mac with the Xcode command line tools)"
    if r.timed_out:
        return "timeout", "notarytool did not answer in time"
    text = r.text.lower()
    if "no keychain password item found" in text:
        return "no-profile", "the keychain profile does not exist on this machine"
    if _AGREEMENT in text:
        return "agreement", "Apple refused: a required agreement is missing or has expired"
    if "invalid or inaccessible developer team" in text:
        return "wrong-team", "Apple refused: the team is invalid or not accessible with this credential"
    if "invalid credentials" in text or "http status code: 401" in text:
        return "bad-credential", "Apple refused the Apple ID or app-specific password"
    if r.rc == 0:
        try:
            doc = json.loads(r.out)
        except ValueError:
            return "other", "notarytool printed something that is not JSON: %s" % first_line(r.out)
        if isinstance(doc, dict) and isinstance(doc.get("history"), list):
            return "ok", "%d" % len(doc["history"])
        return "other", "notarytool's JSON has no 'history' list"
    return "other", "notarytool failed (exit %s): %s" % (r.rc, first_line(r.err or r.out) or "no message")


_HISTORY_PARAMS = {
    "keychain_profile": ("str", "", "notarytool keychain profile name; when empty the Apple ID variables below are used instead"),
    "apple_id_env": ("str", "APPLE_ID", "name of the environment variable holding the Apple ID"),
    "team_id_env": ("str", "APPLE_TEAM_ID", "name of the environment variable holding the team ID"),
    "password_env": ("str", "APPLE_APP_SPECIFIC_PASSWORD", "name of the variable holding the app-specific password (passed to notarytool as @env:NAME, never as a value)"),
    "timeout_s": ("int", 25, "seconds to wait for Apple"),
}


def _history(ctx, p):
    argv, env = _history_argv(ctx, p)
    if argv is None:
        return None, env
    return ctx.run(argv, net=True, timeout=p["timeout_s"], env=env), None


_BAD_CREDENTIAL_MEANINGS = [
    "Apple answered HTTP 401 once and it was not retried; it can mean one of three things:",
    "1. the Apple ID or app-specific password is invalid or revoked: create a new app-specific password at "
    "appleid.apple.com (Sign-In and Security) and update the secret or keychain profile",
    "2. an Apple developer agreement is pending or has expired: the Account Holder accepts it at "
    "developer.apple.com/account",
    "3. a transient Apple or network failure: wait, then run this check once more (repeated failed sign-ins can lock the Apple ID)",
]
_BAD_CREDENTIAL_NEXT = ("find out which of the three it is, in this order: the app-specific password, the pending agreements, "
                        "then one later re-run; do not loop on it. Nothing is needed at stage 'publish' (this check is not applicable there)")


def _last_line(text):
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1][:200] if lines else ""


def _spctl_argv(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".dmg":
        return ["spctl", "--assess", "--type", "open", "--context", "context:primary-signature", "-v", path]
    if ext == ".pkg":
        return ["spctl", "--assess", "--type", "install", "-v", path]
    return ["spctl", "--assess", "--type", "execute", "-v", path]


def _already_notarized(ctx, p):
    """Where the credential is not applicable (the artifacts were notarized earlier): say so, and show the evidence that
    does exist. Reads the stapled ticket and Gatekeeper's verdict of each named artifact; never contacts Apple."""
    head = "not applicable at stage '%s': notarization already happened, so the credential is not what this stage needs" % ctx.stage
    if not p["artifacts"]:
        return Result("skip", head, ["not checked: the existing ticket and Gatekeeper result (no artifacts were given; "
                                     "set artifacts to the .app, .dmg or .pkg paths to read them)", "Apple was not contacted"])
    if ctx.default_host != "local":
        return Result("skip", head, ["not checked: the artifacts are read from this machine's checkout, and this check's host is '%s'"
                                     % ctx.default_host, "Apple was not contacted"])
    try:
        return _read_tickets(ctx, p, head)
    except NotRun as exc:
        return Result("skip", head, ["not checked: %s" % exc, "Apple was not contacted"])


def _read_tickets(ctx, p, head):
    good, bad, unread = [], [], []
    for art in p["artifacts"]:
        path = art if os.path.isabs(art) else os.path.join(ctx.repo, art)
        st = ctx.run(["xcrun", "stapler", "validate", path], timeout=20)
        if st.missing:
            return Result("skip", head, ["not checked: xcrun is not installed here, so no ticket was read", "Apple was not contacted"])
        if st.timed_out:
            unread.append("%s: stapler did not answer in time" % art)
            continue
        gk = ctx.run(_spctl_argv(path), timeout=20)
        if gk.missing:
            unread.append("%s: spctl is not installed here, so Gatekeeper was not asked" % art)
        if not st.ok:
            bad.append("%s: no valid stapled ticket (%s)" % (art, _last_line(st.text) or "exit %s" % st.rc))
        elif not gk.missing and not gk.ok:
            bad.append("%s: ticket stapled, but Gatekeeper does not accept it (%s)" % (art, _last_line(gk.text) or "exit %s" % gk.rc))
        elif not gk.missing:
            good.append("%s: ticket stapled, Gatekeeper accepted" % art)
    if bad:
        return warn(head + "; but the existing artifacts are not clean: " + "; ".join(bad), bad + good + unread,
                    next="re-notarize and staple the named artifact(s); the credential is needed again at the stage that notarizes")
    if unread and not good:
        return Result("skip", head, ["not checked: " + u for u in unread])
    return Result("skip", head + "; existing ticket: " + "; ".join(good), unread)


@register("apple.notary_credentials", "the notarytool credential exists and Apple accepts it for the right team (read-only)",
          dict(_HISTORY_PARAMS, artifacts=("list", [], "built .app, .dmg or .pkg paths (relative to the checkout or absolute); "
                                                       "where this check is not applicable their stapled ticket and Gatekeeper result are shown instead")),
          cost="cheap-network", network=True, not_at=("publish",), na=_already_notarized)
def notary_credentials(ctx, p):
    """Runs `xcrun notarytool history` (a read-only listing of previous submissions) with the keychain profile or the
    Apple ID variables, and tells the failures apart: no such keychain profile, a team the credential cannot reach, and
    a rejected credential (HTTP 401, which can mean an invalid password, a pending agreement or a transient failure, and
    is asked once, never retried). It does not submit anything. An agreement refusal is `unknown` here (Apple
    answered, but the credential itself was not confirmed): `apple.notary_agreement` owns that failure. When the
    credential variables are not set the check is skipped with that reason, never a failure (`apple.notarization_configured`
    owns whether a set exists). No xcrun, a timeout or any answer this check does not recognise is `unknown`, never a
    pass. At stage `publish` the credential is not applicable (the artifacts were notarized earlier): the check then shows
    the stapled ticket and Gatekeeper result of the `artifacts` instead and never contacts Apple. The output is reduced to
    a count: submission names and ids are not printed."""
    r, why = _history(ctx, p)
    if r is None:
        # A missing variable is only worth saying on a machine that could notarize at all.
        probe = ctx.run(["xcrun", "--version"], timeout=5)
        if probe.missing:
            return cannot_check("xcrun is not installed (notarization is checked on a Mac with the Xcode command line tools)")
        return Result("skip", "not checked: " + why + "; nothing was sent to Apple",
                      ["apple.notarization_configured reports whether a credential set is present"],
                      next="set the credential variables (or give keychain_profile) in the shell that notarizes, then re-run")
    kind, ev = _classify(r)
    if kind == "ok":
        return ok("Apple accepted the credential; %s previous submission(s) listed" % ev, [_via(p)])
    if kind == "no-tool":
        return cannot_check(ev)
    if kind == "bad-credential":
        return fail("Apple answered HTTP 401: the credential was not accepted (invalid credential, pending agreement, or transient)",
                    _BAD_CREDENTIAL_MEANINGS + [_via(p)], next=_BAD_CREDENTIAL_NEXT)
    if kind in ("no-profile", "wrong-team"):
        return fail(ev, [_via(p)])
    if kind == "agreement":
        return unknown("Apple answered with an agreement refusal before the credential could be confirmed", [ev, "see the apple.notary_agreement check"])
    return unknown(ev, [_via(p)])


def _via(p):
    return "credential: keychain profile %s" % p["keychain_profile"] if p["keychain_profile"] else \
        "credential: Apple ID from $%s, team from $%s, password from $%s (names only)" % (p["apple_id_env"], p["team_id_env"], p["password_env"])


@register("apple.notary_agreement", "Apple has no missing or expired developer agreement for the notarization account",
          dict(_HISTORY_PARAMS, confirmed_by=("str", "", "who confirmed, in Apple's developer portal, that no agreement is pending"),
               confirmed_on=("str", "", "the date of that confirmation, YYYY-MM-DD"),
               max_age_days=("int", 30, "how old a human confirmation may be")),
          cost="cheap-network", network=True)
def notary_agreement(ctx, p):
    """Apple documents the refusal 'A required agreement is missing or has expired' for a submission, and only a person
    with the Account Holder role can accept the agreement; a retry never helps. This runs the same read-only
    `notarytool history` call and fails when Apple answers with that refusal. A history that merely works does not
    prove there is no pending agreement (it is not documented to refuse there), so on its own it is `unknown`; it
    becomes a pass only with a dated human confirmation (`confirmed_by`, `confirmed_on`) younger than `max_age_days`,
    and the evidence says so. Mark the check waivable in the profile if a person may waive the `unknown`."""
    r, why = _history(ctx, p)
    if r is None:
        probe = ctx.run(["xcrun", "--version"], timeout=5)
        if probe.missing:
            return cannot_check("xcrun is not installed (notarization is checked on a Mac with the Xcode command line tools)")
        return unknown("the agreement could not be checked: " + why)
    kind, ev = _classify(r)
    if kind == "no-tool":
        return cannot_check(ev)
    if kind == "agreement":
        return fail("a required Apple agreement is missing or has expired; the Account Holder must accept it, and a retry will not help",
                    [ev])
    if kind != "ok":
        return unknown("the agreement could not be checked: " + ev, [_via(p)])
    if not p["confirmed_on"]:
        return unknown("the credential works, but nothing proves there is no pending agreement: no dated human confirmation was given",
                       ["pass confirmed_by and confirmed_on (YYYY-MM-DD) after the Account Holder checks the developer portal"])
    try:
        when = calendar.timegm(time.strptime(p["confirmed_on"], "%Y-%m-%d"))
    except ValueError:
        return unknown("confirmed_on '%s' is not a YYYY-MM-DD date" % p["confirmed_on"])
    age = (time.time() - when) / 86400.0
    who = p["confirmed_by"] or "someone unnamed"
    if age < -1:
        return unknown("the agreement confirmation is dated in the future (%s)" % p["confirmed_on"])
    if age > p["max_age_days"]:
        return unknown("the last human agreement confirmation (%s, %s) is %d days old; the limit is %d"
                       % (who, p["confirmed_on"], age, p["max_age_days"]))
    return ok("no agreement refusal from Apple; %s confirmed no pending agreement on %s" % (who, p["confirmed_on"]),
              ["human-confirmed, %d day(s) ago" % max(age, 0)])


# ---- Developer ID identity: present, valid, unexpired, issued under G2 ---------------------------------------------

def _tlv(buf, pos):
    """One DER element at pos: (tag, content_start, content_end)."""
    tag = buf[pos]
    n = buf[pos + 1]
    pos += 2
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(buf[pos:pos + k], "big")
        pos += k
    return tag, pos, pos + n


def _children(buf, start, end):
    out, pos = [], start
    while pos < end:
        tag, cs, ce = _tlv(buf, pos)
        out.append((tag, cs, ce))
        pos = ce
    return out


def _text(buf, tag, cs, ce):
    raw = bytes(buf[cs:ce])
    if tag == 0x1E:
        return raw.decode("utf-16-be", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


_OIDS = {b"\x55\x04\x03": "CN", b"\x55\x04\x0a": "O", b"\x55\x04\x0b": "OU"}


def _name(buf, start, end):
    out = []
    for _t, s, e in _children(buf, start, end):                 # each RDN is a SET
        for _t2, s2, e2 in _children(buf, s, e):                # of SEQUENCE { OID, value }
            parts = _children(buf, s2, e2)
            if len(parts) == 2:
                key = _OIDS.get(bytes(buf[parts[0][1]:parts[0][2]]))
                if key:
                    out.append((key, _text(buf, parts[1][0], parts[1][1], parts[1][2])))
    return out


def _time(buf, tag, cs, ce):
    txt = bytes(buf[cs:ce]).decode("ascii")
    if tag == 0x17:                                              # UTCTime YYMMDDHHMMSSZ
        yy = int(txt[:2])
        txt = ("19" if yy >= 50 else "20") + txt
    return calendar.timegm(time.strptime(txt[:14], "%Y%m%d%H%M%S"))


def parse_certificate(der):
    """Issuer names, subject names and validity of an X.509 certificate, from its DER bytes, with no third-party code.
    Returns {'issuer': [(key, value)], 'subject': [...], 'not_before': epoch, 'not_after': epoch}."""
    buf = memoryview(der)
    _t, cs, ce = _tlv(buf, 0)                                    # Certificate
    _t, ts, te = _tlv(buf, cs)                                   # TBSCertificate
    kids = _children(buf, ts, te)
    if kids and kids[0][0] == 0xA0:                              # the optional [0] version
        kids = kids[1:]
    # serial, signature algorithm, issuer, validity, subject
    issuer, validity, subject = kids[2], kids[3], kids[4]
    nb, na = _children(buf, validity[1], validity[2])[:2]
    return {"issuer": _name(buf, issuer[1], issuer[2]), "subject": _name(buf, subject[1], subject[2]),
            "not_before": _time(buf, *nb), "not_after": _time(buf, *na)}


def pem_certificates(text):
    out = []
    for m in re.finditer(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", text, re.S):
        try:
            out.append(parse_certificate(base64.b64decode("".join(m.group(1).split()))))
        except (ValueError, IndexError):
            continue
    return out


def _day(epoch):
    return time.strftime("%Y-%m-%d", time.gmtime(epoch))


@register("apple.developer_id_identity", "the Developer ID identity is in the keychain, valid, not about to expire, and issued under G2",
          {"identity": ("str", "", "the identity name as the keychain prints it, for example 'Developer ID Application: Name (TEAMID)'"),
           "min_days": ("int", 30, "fail when the certificate expires within this many days"),
           "require_g2": ("bool", True, "fail when the issuing CA is not the Developer ID G2 authority"),
           "keychain": ("str", "", "keychain file to search (default: the user's search list)")},
          cost="free")
def developer_id_identity(ctx, p):
    """Lists code signing identities with `security find-identity -p codesigning` (without -v, so an expired or
    revoked one is shown and named rather than silently missing), finds the one named, then reads its certificate with
    `security find-certificate` and checks the expiry and the issuer from the certificate itself. The original
    Developer ID certificate authority expires on 2027-02-01; a certificate issued under it must be replaced by the
    Account Holder, so an issuer without the G2 marker fails. No `security` tool (any machine that is not a Mac) is
    `unknown`. Reads only; nothing is signed and no private key is touched."""
    if not p["identity"]:
        return unknown("no identity was named", ["set the identity param, for example --set mac_identity='Developer ID Application: ...'"])
    kc = [p["keychain"]] if p["keychain"] else []
    r = ctx.run(["security", "find-identity", "-p", "codesigning"] + kc, timeout=15)
    if r.missing:
        return cannot_check("the macOS 'security' tool is not installed (the keychain is read on the Mac that signs)")
    if r.timed_out or not r.ok:
        return unknown("the keychain did not list identities: %s" % (first_line(r.err or r.out) or "timed out"))
    lines = [ln.strip() for ln in r.out.splitlines() if p["identity"] in ln]
    if not lines:
        return fail("the identity '%s' is not in the keychain" % p["identity"], ["`security find-identity -p codesigning` does not list it"])
    marker = re.search(r"\(CSSMERR_[A-Z_]+\)|\(CSSMERR\w*\)", lines[0])
    if marker:
        return fail("the identity is in the keychain but not valid: %s" % marker.group(0).strip("()"), [lines[0].split('"')[0].strip() or "identity listed"])
    c = ctx.run(["security", "find-certificate", "-a", "-c", p["identity"], "-p"] + kc, timeout=15)
    if c.timed_out or not c.ok:
        return unknown("the certificate could not be read: %s" % (first_line(c.err or c.out) or "timed out"))
    certs = pem_certificates(c.out)
    if not certs:
        return unknown("the keychain returned no certificate that could be read for '%s'" % p["identity"])
    cert = max(certs, key=lambda x: x["not_after"])             # several renewals can share a name: judge the newest
    days = (cert["not_after"] - time.time()) / 86400.0
    ous = [v for k, v in cert["issuer"] if k == "OU"]
    cn = next((v for k, v in cert["issuer"] if k == "CN"), "?")
    details = ["expires %s (%d days)" % (_day(cert["not_after"]), days), "issuer: %s%s" % (cn, " (%s)" % ", ".join(ous) if ous else "")]
    bad = []
    if days < 0:
        bad.append("the certificate expired on %s" % _day(cert["not_after"]))
    elif days < p["min_days"]:
        bad.append("the certificate expires in %d days (%s), under the %d-day floor" % (days, _day(cert["not_after"]), p["min_days"]))
    if p["require_g2"] and "G2" not in ous:
        bad.append("the issuer is not the Developer ID G2 authority; the original authority expires on 2027-02-01 and the Account Holder must reissue the certificate")
    if bad:
        return fail("; ".join(bad), details)
    return ok("identity present and valid, expires %s (%d days), issuer %s" % (_day(cert["not_after"]), days, "G2" if "G2" in ous else cn), details)


# ---- is notarization configured at all -----------------------------------------------------------------------------

_SETS = {"apple-id": ["APPLE_ID", "APPLE_APP_SPECIFIC_PASSWORD", "APPLE_TEAM_ID"],
         "api-key": ["APPLE_API_KEY", "APPLE_API_KEY_ID", "APPLE_API_ISSUER"],
         "keychain-profile": ["APPLE_KEYCHAIN_PROFILE"]}
_BUILDER_CONFIGS = ["electron-builder.yml", "electron-builder.yaml", "electron-builder.json", "electron-builder.json5"]


@register("apple.notarization_configured", "exactly one complete notarization credential set is present and hardened runtime is on",
          {"sets": ("dict", _SETS, "credential sets: name -> the environment variables that must all be set (names only)"),
           "config": ("list", _BUILDER_CONFIGS, "builder config files, relative to the checkout; the first that exists is read for hardenedRuntime"),
           "hardened_runtime": ("bool", True, "also require the config to turn hardened runtime on")},
          cost="free")
def notarization_configured(ctx, p):
    """With no credential set in the environment electron-builder skips notarization, and a green build ships
    un-notarized. This reads which variables are set (never their values; a variable that is empty counts as unset)
    and requires exactly one complete set: none is a failure, two complete sets is ambiguous and a failure, a partial
    set is named. It then reads the builder config for `hardenedRuntime`: `false` fails, `true` passes, not stated
    is `unknown` (the builder's default is not proof). The scan is line-based and does not understand YAML scoping."""
    complete, partial, lines = [], [], []
    for name, names in sorted(p["sets"].items()):
        have = [n for n in names if os.environ.get(n)]
        if len(have) == len(names):
            complete.append(name)
        elif have:
            partial.append("%s is missing %s" % (name, ", ".join(n for n in names if n not in have)))
        lines.append("%s: %d of %d variable(s) set" % (name, len(have), len(names)))
    bad, unk = [], []
    if not complete:
        bad.append("no complete notarization credential set is present%s" % ("; " + "; ".join(partial) if partial else ""))
    elif len(complete) > 1:
        bad.append("more than one complete credential set is present (%s); the builder will pick one and which is not obvious" % ", ".join(complete))
    elif partial:
        lines.append("ignored partial set(s): " + "; ".join(partial))
    if p["hardened_runtime"]:
        found = None
        for rel in p["config"]:
            path = os.path.join(ctx.repo, rel)
            if os.path.isfile(path):
                found = rel
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                vals = [m.group(1) for m in re.finditer(r"""(?m)^\s*["']?hardenedRuntime["']?\s*:\s*(true|false)\b""", text)]
                if "false" in vals:
                    bad.append("hardenedRuntime is false in %s" % rel)
                elif "true" in vals:
                    lines.append("hardenedRuntime: true in %s" % rel)
                else:
                    unk.append("%s does not say hardenedRuntime" % rel)
                break
        if found is None:
            unk.append("no builder config found (%s)" % ", ".join(p["config"][:2] + (["..."] if len(p["config"]) > 2 else [])))
    if bad:
        return fail("; ".join(bad), lines)
    if unk:
        return unknown("; ".join(unk), lines)
    return ok("credential set '%s' is complete%s" % (complete[0], " and hardened runtime is on" if p["hardened_runtime"] else ""), lines)
