"""Windows signing checks. They run PowerShell and the Azure CLI on the signing host (over the profile's host runner,
for example ssh) or on this machine, read-only: they list role assignments, read files and ask for versions. They
never sign, and never print a token. Commands avoid shell metacharacters (no pipes, no double quotes) because a remote
default shell may be cmd.exe."""
import json
import re

from . import cannot_check, fail, ok, register, unknown
from ..util import first_line, version_tuple

_NOT_INSTALLED = re.compile(r"is not recognized|command not found|no such file or directory|cannot find the file", re.I)
_NEEDS_QUOTES = re.compile(r"[\s()&^]")
_SIGNER_ROLES = ["Artifact Signing Certificate Profile Signer", "Trusted Signing Certificate Profile Signer"]


def _names_tool(text, tool):
    """True when a 'not recognized' style line names `tool`. An unrelated tool's login-profile noise (for example
    "The term 'fnm' is not recognized") on the same host must never read as 'the tool being run is missing'."""
    name = re.split(r"[\\/]", tool.strip("\"'"))[-1].lower()
    for ext in (".exe", ".cmd", ".bat"):
        if name.endswith(ext):
            name = name[:-len(ext)]
    named = re.compile(r"(?<![a-z0-9_-])%s(?![a-z0-9_-])" % re.escape(name))
    return any(_NOT_INSTALLED.search(ln) and named.search(ln.lower()) for ln in text.splitlines())


def _quoted_argv(ctx, argv):
    """Over a remote runner (ssh) the argv is joined into one string for the host's shell, so a program path with a space
    must be quoted there. `shell` on the host says which shell: cmd (the default) wants "path"; powershell wants & "path"."""
    if ctx.default_host == "local" or not _NEEDS_QUOTES.search(argv[0]):
        return argv
    quoted = '"%s"' % argv[0]
    if ctx.host_spec(ctx.default_host).get("shell", "cmd") == "powershell":
        return ["&", quoted] + argv[1:]
    return [quoted] + argv[1:]


def _remote(ctx, argv, timeout=25):
    """Run argv on the check's host. Returns (proc, None) or (None, an `unknown` result) when it cannot be answered."""
    r = ctx.run(_quoted_argv(ctx, argv), timeout=timeout)
    where = "on host '%s'" % ctx.default_host if ctx.default_host != "local" else "on this machine"
    if r.missing:
        runner = (ctx.host_spec(ctx.default_host).get("argv_prefix") or [argv[0]])[0]
        return None, cannot_check("%s is not installed (it is needed to run commands %s)" % (runner, where))
    if r.timed_out:
        return None, unknown("%s did not answer in time %s" % (argv[0], where))
    if r.rc == 255 and ctx.default_host != "local":
        return None, cannot_check("the host '%s' cannot be reached" % ctx.default_host, [first_line(r.err)])
    if r.rc != 0 and _names_tool(r.text, argv[0]):
        return None, cannot_check("%s is not installed %s" % (argv[0], where), [first_line(r.text)])
    return r, None


@register("windows.signing_role", "the signing identity can get a token and holds a certificate-profile signer role",
          {"az": ("str", "az", "the Azure CLI, a command name or a full path on the signing host"),
           "assignee": ("str", "", "object id or app id of the signing identity (not a secret)"),
           "scope": ("str", "", "resource scope of the certificate profile, for example /subscriptions/ID/resourceGroups/NAME/providers/..."),
           "roles": ("list", _SIGNER_ROLES, "role names that allow signing (the service's current name and its older one)")},
          cost="cheap-network", network=True)
def signing_role(ctx, p):
    """Two separate questions. First `az account get-access-token --query expiresOn`: can this machine get a token at
    all (it proves identity only; only the expiry is read, the token itself is never printed). Then
    `az role assignment list` for the identity at the certificate profile's scope: does it hold a signer role
    (authorization). Not signed in is a failure; a missing `az`, an unreachable host, or no permission to read role
    assignments is `unknown`, because none of those prove the identity cannot sign."""
    if not p["assignee"] or not p["scope"]:
        return unknown("no signing identity or certificate profile scope was given", ["set assignee and scope"])
    t, bad = _remote(ctx, [p["az"], "account", "get-access-token", "--query", "expiresOn", "--output", "tsv"])
    if bad:
        return bad
    if t.rc != 0:
        if re.search(r"az login|not logged in|please run", t.text, re.I):
            return fail("the Azure CLI is not signed in; no token can be issued", [first_line(t.text)])
        return unknown("the Azure CLI could not issue a token: %s" % (first_line(t.text) or "exit %s" % t.rc))
    r, bad = _remote(ctx, [p["az"], "role", "assignment", "list", "--assignee", p["assignee"], "--scope", p["scope"], "--output", "json"])
    if bad:
        return bad
    if r.rc != 0:
        if re.search(r"authorizationfailed|does not have authorization", r.text, re.I):
            return unknown("a token was issued, but this identity may not read role assignments at that scope, so the signer role is unproven",
                           [first_line(r.text)])
        return unknown("role assignments could not be listed: %s" % (first_line(r.text) or "exit %s" % r.rc))
    try:
        found = {a.get("roleDefinitionName") for a in json.loads(r.out)}
    except (ValueError, AttributeError, TypeError):
        return unknown("az printed role assignments that are not JSON")
    held = [x for x in p["roles"] if x in found]
    if held:
        return ok("token issued and the identity holds '%s' at the certificate profile scope" % held[0],
                  ["token: ok (expiry only was read)", "roles at scope: %d" % len(found)])
    return fail("token issued, but the identity holds no signer role at the certificate profile scope (%s)" % " or ".join("'%s'" % x for x in p["roles"]),
                ["token: ok (expiry only was read)", "roles at scope: %s" % (", ".join(sorted(x for x in found if x)) or "none")])


def _ps(ctx, p, script):
    return _remote(ctx, [p["pwsh"], "-NoProfile", "-NonInteractive", "-Command", script])


@register("windows.signing_setup", "the signing module is visible to PowerShell 7 and the signing tool, endpoint and timestamp are set up",
          {"pwsh": ("str", "pwsh", "PowerShell 7 on the signing host (the module must be visible to this one, not Windows PowerShell)"),
           "module": ("str", "TrustedSigning", "the signing module that must be visible"),
           "module_version": ("str", "", "when given, the module must be installed exactly once at exactly this version"),
           "signtool": ("str", "", "full path of signtool.exe on the host; when given its version is checked"),
           "min_signtool": ("str", "10.0.2261.755", "oldest SignTool that supports the signing service"),
           "metadata_file": ("str", "", "path of the signing metadata JSON on the host; when given its Endpoint is checked"),
           "endpoint": ("str", "", "the endpoint the metadata must name, for example https://eus.codesigning.azure.net"),
           "timestamp_file": ("str", "", "a file on the host (the signing script or its config) that must name the timestamp server"),
           "timestamp_url": ("str", "http://timestamp.acs.microsoft.com", "the timestamp server that file must contain")},
          cost="cheap-network", network=True)
def signing_setup(ctx, p):
    """Always checks that the signing module is visible to PowerShell 7 (a module installed for Windows PowerShell can
    be invisible to pwsh, and the failure only shows at signing time). Each further part runs only when its parameter
    is given and is listed in the evidence either way: the SignTool version floor, the metadata Endpoint against the
    expected one (a region mismatch commonly causes a 403), and the timestamp server named in the signing script
    (certificates are valid for three days, so a missing timestamp breaks verification soon after). A missing tool or
    unreachable host is `unknown`. Catalog validation is project-specific: run the project's own validator with `external`."""
    bad, unk, lines = [], [], []
    r, why = _ps(ctx, p, "(Get-Module -ListAvailable -Name '%s').Version -join ','" % p["module"])
    if why:
        return why
    seen = first_line(r.out)
    if r.rc != 0:
        unk.append("could not ask for the module: %s" % (first_line(r.text) or "exit %s" % r.rc))
    elif not seen:
        bad.append("the %s module is not visible to %s" % (p["module"], p["pwsh"]))
    elif p["module_version"] and seen.split(",") != [p["module_version"]]:
        bad.append("%s is installed as %s, expected exactly one copy at %s" % (p["module"], seen, p["module_version"]))
    else:
        lines.append("%s module %s is visible to %s" % (p["module"], seen, p["pwsh"]))
    if p["signtool"]:
        r, why = _ps(ctx, p, "(Get-Item -LiteralPath '%s').VersionInfo.FileVersion" % p["signtool"])
        if why:
            return why
        m = re.search(r"\d+(?:\.\d+){1,3}", r.out) if r.rc == 0 else None
        if r.rc != 0 and re.search(r"cannot find path|does not exist", r.text, re.I):
            bad.append("signtool is not at %s" % p["signtool"])
        elif not m:
            unk.append("could not read the signtool version")
        elif version_tuple(m.group(0)) < version_tuple(p["min_signtool"]):
            bad.append("signtool %s is older than %s" % (m.group(0), p["min_signtool"]))
        else:
            lines.append("signtool %s (needs %s or newer)" % (m.group(0), p["min_signtool"]))
    else:
        lines.append("signtool version not checked (no signtool path given)")
    if p["metadata_file"]:
        r, why = _ps(ctx, p, "Get-Content -Raw -LiteralPath '%s'" % p["metadata_file"])
        if why:
            return why
        if r.rc != 0 and re.search(r"cannot find path|does not exist", r.text, re.I):
            bad.append("the signing metadata file %s does not exist" % p["metadata_file"])
        else:
            try:
                have = str(json.loads(r.out.lstrip("﻿")).get("Endpoint", ""))
            except (ValueError, AttributeError):
                have = None
            if have is None:
                unk.append("the signing metadata file is not JSON")
            elif not p["endpoint"]:
                unk.append("metadata names endpoint %s but no expected endpoint was given" % have)
            elif have.rstrip("/").lower() != p["endpoint"].rstrip("/").lower():
                bad.append("the metadata endpoint is %s, expected %s (a region mismatch commonly causes a 403)" % (have, p["endpoint"]))
            else:
                lines.append("metadata endpoint %s" % have)
    else:
        lines.append("metadata endpoint not checked (no metadata file given)")
    if p["timestamp_file"]:
        r, why = _ps(ctx, p, "Get-Content -Raw -LiteralPath '%s'" % p["timestamp_file"])
        if why:
            return why
        if r.rc != 0 and re.search(r"cannot find path|does not exist", r.text, re.I):
            bad.append("the timestamp file %s does not exist" % p["timestamp_file"])
        elif r.rc != 0:
            unk.append("could not read %s: %s" % (p["timestamp_file"], first_line(r.text) or "exit %s" % r.rc))
        elif p["timestamp_url"].lower() not in r.out.lower():
            bad.append("%s does not name the timestamp server %s" % (p["timestamp_file"], p["timestamp_url"]))
        else:
            lines.append("timestamp server %s is named" % p["timestamp_url"])
    else:
        lines.append("timestamp not checked (no timestamp file given)")
    if bad:
        return fail("; ".join(bad), lines + unk)
    if unk:
        return unknown("; ".join(unk), lines)
    return ok("signing setup is in place (%d part(s) checked)" % sum(1 for ln in lines if "not checked" not in ln), lines)
