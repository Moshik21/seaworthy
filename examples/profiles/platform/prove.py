#!/usr/bin/env python3
"""Prove the platform checks: a known-good and a known-bad case for each, through the public `seaworthy run` command.

    unshare -rn python3 examples/profiles/platform/prove.py --out DIR --scratch DIR

Every case is a REAL run (not a dry run) of one check with `--only`, but the vendors' tools are stand-ins: shims/shim is
copied under the names xcrun, security, spctl, gh and ssh into a throwaway directory that is the only thing on PATH, and small
scenario files next to it choose what it answers. No Apple, GitHub, Azure or ssh endpoint is contacted, no real credential
is used, and the certificates are made here with openssl for a made-up name (their keys stay in --scratch). Run it under
`unshare -rn` (Linux) to make "no network" a property of the run. Needs python3 and openssl 3.4 or newer.

Besides each check's status, every case asserts that the shim only ever received read-only calls and that no secret
value (a sentinel set in the environment) reached an argument list or the report.
"""
import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BIN = os.path.join(ROOT, "bin", "seaworthy")
PROFILE = os.path.join(HERE, "seaworthy.json")
SHIM = os.path.join(HERE, "shims", "shim")
TOOLS = ("xcrun", "security", "spctl", "gh", "ssh")
SENTINEL = "sentinel-" + uuid.uuid4().hex          # stands in for every secret value; it must never be printed or stored

# What the shim may be asked. Anything else is a call the checks are not allowed to make.
ALLOWED = [r"^xcrun --version$", r"^xcrun notarytool history ", r"^xcrun stapler validate ", r"^spctl --assess ",
           r"^security find-(identity|certificate) ",
           r"^gh secret list ", r"^gh api repos/\S+/(releases\?per_page=100|immutable-releases)$",
           r"^ssh (-o \S+ )*\S+ (\S+ )*(account get-access-token|role assignment list|-Command \(Get-Module|-Command \(Get-Item|-Command Get-Content)"]
GOOD_ASSETS = ["app-mac.dmg", "app-win.exe", "app-linux.AppImage"]
NOTES = "Release 1.4.0\n\n- one change\n"
EXIT = {"pass": 0, "skip": 0, "warn": 4, "fail": 3, "unknown": 3}


def sh(*args, cwd=None):
    r = subprocess.run(list(args), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    if r.returncode != 0:
        raise SystemExit("%s failed: %s" % (" ".join(args[:3]), r.stderr.strip()))
    return r.stdout


def make_certs(d):
    """A made-up Developer ID chain: a G2 authority, an original-style authority, and leaves with different lifetimes."""
    os.makedirs(d, exist_ok=True)
    leaf_subj = "/CN=Developer ID Application: Example Corp (TEST000000)"
    cas = {"g2": "/CN=Developer ID Certification Authority/OU=G2/O=Example Authority/C=US",
           "old": "/CN=Developer ID Certification Authority/OU=Example Certification Authority/O=Example Authority/C=US"}
    for name, subj in cas.items():
        sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "%s/ca-%s.key" % (d, name),
           "-out", "%s/ca-%s.pem" % (d, name), "-subj", subj, "-days", "7300")
    sh("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", d + "/leaf.key", "-out", d + "/leaf.csr", "-subj", leaf_subj)
    out = {}
    for name, ca, window in (("good", "g2", ["-days", "1000"]), ("soon", "g2", ["-days", "10"]),
                             ("expired", "g2", ["-not_before", "20200101000000Z", "-not_after", "20210101000000Z"]),
                             ("old-issuer", "old", ["-days", "1000"])):
        pem = "%s/leaf-%s.pem" % (d, name)
        sh("openssl", "x509", "-req", "-in", d + "/leaf.csr", "-CA", "%s/ca-%s.pem" % (d, ca), "-CAkey", "%s/ca-%s.key" % (d, ca),
           "-CAcreateserial", "-out", pem, *window)
        out[name] = pem
    return out


def releases(**over):
    rel = {"id": 4242, "tag_name": "v1.4.0", "draft": True, "body": NOTES, "assets": [{"name": n, "state": "uploaded"} for n in GOOD_ASSETS]}
    rel.update(over)
    return [rel, {"id": 1, "tag_name": "v1.3.0", "draft": False, "body": "old", "assets": []}]


def today(offset_days=0):
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - offset_days * 86400))


# id, check, what is set up, scenario files, extra env, --set args, expected status, evidence must contain
def cases(certs):
    c = []

    def add(cid, what, status, contains, scenario=None, env=None, sets=None, shims=TOOLS, cert=None, stage=None, call=None):
        sc = dict(scenario or {})
        if cert:
            sc["cert.pem"] = ("file", certs[cert])
        c.append(dict(id=cid, what=what, status=status, contains=contains, scenario=sc, env=env or {}, sets=sets or [], shims=shims,
                      stage=stage, call=call))

    # -- apple.notary_credentials
    add("notary-credentials", "credential accepted by Apple", "pass", "Apple accepted the credential; 2 previous")
    add("notary-credentials", "keychain profile does not exist", "fail", "keychain profile does not exist", {"xcrun": "no-profile"})
    add("notary-credentials", "credential reaches the wrong team", "fail", "team is invalid or not accessible", {"xcrun": "wrong-team"})
    add("notary-credentials", "Apple answers 401: says the three meanings and the next step", "fail", "invalid or revoked: create a new app-specific password",
        {"xcrun": "bad-credential"})
    add("notary-credentials", "a 401 still fails at a stage where the credential applies", "fail", "Apple answered HTTP 401", {"xcrun": "bad-credential"},
        stage="sign")
    add("notary-credentials", "stage publish: not applicable, existing ticket and Gatekeeper shown", "skip", "existing ticket: dist/Example-1.4.0.dmg: ticket stapled, Gatekeeper accepted",
        {"xcrun": "bad-credential"}, stage="publish")
    add("notary-credentials", "stage publish: the existing artifact has no ticket", "warn", "no valid stapled ticket", {"xcrun.stapler": "none"}, stage="publish")
    add("notary-credentials", "stage publish: Gatekeeper rejects the artifact", "warn", "Gatekeeper does not accept it", {"spctl.mode": "rejected"}, stage="publish")
    add("notary-credentials", "agreement refusal hides the credential", "unknown", "agreement refusal", {"xcrun": "agreement"})
    add("notary-credentials", "notarytool prints something unexpected", "unknown", "not JSON", {"xcrun": "garbage"})
    add("notary-credentials", "no xcrun on this machine", "unknown", "cannot check here: xcrun is not installed", shims=())
    env = {"APPLE_ID": "dev@example.invalid", "APPLE_TEAM_ID": "TEST000000", "APPLE_APP_SPECIFIC_PASSWORD": SENTINEL}
    add("notary-credentials", "Apple ID variables instead of a keychain profile (good)", "pass", "Apple accepted", env=env, sets=["keychain_profile="])
    add("notary-credentials", "Apple ID variables not set: skipped with the reason, not a failure", "skip", "not checked: no keychain_profile was given",
        sets=["keychain_profile="])
    # -- apple.notary_agreement
    add("notary-agreement", "no refusal and a fresh human confirmation", "pass", "confirmed no pending agreement on",
        sets=["agreement_confirmed_by=the account holder", "agreement_confirmed_on=" + today(3)])
    add("notary-agreement", "Apple says the agreement is missing or expired", "fail", "Account Holder must accept", {"xcrun": "agreement"},
        sets=["agreement_confirmed_by=the account holder", "agreement_confirmed_on=" + today(1)])
    add("notary-agreement", "history works but nobody confirmed", "unknown", "no dated human confirmation")
    add("notary-agreement", "confirmation is 90 days old", "unknown", "days old",
        sets=["agreement_confirmed_by=the account holder", "agreement_confirmed_on=" + today(90)])
    add("notary-agreement", "no xcrun on this machine", "unknown", "cannot check here", shims=())
    # -- apple.developer_id_identity
    add("developer-id-identity", "valid identity, G2 issuer, 1000 days left", "pass", "issuer G2", cert="good")
    add("developer-id-identity", "identity not in the keychain", "fail", "is not in the keychain", {"security.identity": "missing"}, cert="good")
    add("developer-id-identity", "keychain flags the identity as expired", "fail", "CSSMERR_TP_CERT_EXPIRED", {"security.identity": "expired-flagged"}, cert="good")
    add("developer-id-identity", "certificate expired in 2021", "fail", "expired on 2021-01-01", cert="expired")
    add("developer-id-identity", "certificate expires in 10 days", "fail", "under the 30-day floor", cert="soon")
    add("developer-id-identity", "issuer is the original authority, not G2", "fail", "not the Developer ID G2 authority", cert="old-issuer")
    add("developer-id-identity", "no security tool on this machine", "unknown", "cannot check here: the macOS 'security' tool", shims=())
    # -- apple.notarization_configured (environment variable names only)
    full = {"APPLE_ID": "dev@example.invalid", "APPLE_APP_SPECIFIC_PASSWORD": SENTINEL, "APPLE_TEAM_ID": "TEST000000"}
    add("notarization-configured", "one complete set and hardened runtime on", "pass", "credential set 'apple-id' is complete", env=full, shims=())
    add("notarization-configured", "no credential in the environment", "fail", "no complete notarization credential set", shims=())
    add("notarization-configured", "a set with a variable missing", "fail", "apple-id is missing APPLE_TEAM_ID",
        env={"APPLE_ID": "dev@example.invalid", "APPLE_APP_SPECIFIC_PASSWORD": SENTINEL}, shims=())
    add("notarization-configured", "two complete sets", "fail", "more than one complete credential set",
        env=dict(full, APPLE_KEYCHAIN_PROFILE="example-notary"), shims=())
    add("notarization-configured", "hardenedRuntime is false", "fail", "hardenedRuntime is false", env=full,
        scenario={"repo/electron-builder.yml": ("text", "mac:\n  hardenedRuntime: false\n")}, shims=())
    add("notarization-configured", "the config does not say", "unknown", "does not say hardenedRuntime", env=full,
        scenario={"repo/electron-builder.yml": ("text", "mac:\n  target: dmg\n")}, shims=())
    # -- windows.signing_role
    add("windows-signing-role", "token issued and a signer role assigned", "pass", "holds 'Artifact Signing Certificate Profile Signer'")
    add("windows-signing-role", "token issued but no signer role", "fail", "holds no signer role", {"ssh.roles": "none"})
    add("windows-signing-role", "not signed in to Azure", "fail", "not signed in", {"ssh.token": "not-logged-in"})
    add("windows-signing-role", "no permission to read role assignments", "unknown", "signer role is unproven", {"ssh.roles": "forbidden"})
    add("windows-signing-role", "az is not installed on the host", "unknown", "cannot check here: az is not installed", {"ssh.az": "missing"})
    add("windows-signing-role", "the host cannot be reached", "unknown", "cannot check here: the host 'signer' cannot be reached", {"ssh.mode": "down"})
    add("windows-signing-role", "no ssh on this machine", "unknown", "cannot check here: ssh is not installed", shims=())
    # -- windows.signing_setup
    ok_meta = ("text", '{"Endpoint":"https://eus.codesigning.azure.net/","CodeSigningAccountName":"example","CertificateProfileName":"example"}\n')
    ok_script = ("text", "signtool sign /tr http://timestamp.acs.microsoft.com /td sha256 /fd sha256 %1\n")
    base = {"metadata.json": ok_meta, "signing-script.txt": ok_script}
    add("windows-signing-setup", "module visible, signtool new enough, endpoint and timestamp right", "pass", "signing setup is in place (4 part(s) checked)", base)
    add("windows-signing-setup", "module not visible to PowerShell 7", "fail", "module is not visible", dict(base, **{"ssh.module": "absent"}))
    add("windows-signing-setup", "the module is installed twice", "fail", "expected exactly one copy at 0.5.8", dict(base, **{"ssh.module": "duplicate"}))
    add("windows-signing-setup", "signtool older than the floor", "fail", "older than 10.0.2261.755", dict(base, **{"ssh.signtool": "10.0.1000.5"}))
    add("windows-signing-setup", "signtool missing at the path", "fail", "signtool is not at", dict(base, **{"ssh.signtool": "missing"}))
    add("windows-signing-setup", "metadata endpoint is another region", "fail", "metadata endpoint is https://weu.codesigning.azure.net",
        dict(base, **{"metadata.json": ("text", '{"Endpoint":"https://weu.codesigning.azure.net"}\n')}))
    add("windows-signing-setup", "the signing script names no timestamp server", "fail", "does not name the timestamp server",
        dict(base, **{"signing-script.txt": ("text", "signtool sign /td sha256 /fd sha256 %1\n")}))
    add("windows-signing-setup", "the host cannot be reached", "unknown", "cannot be reached", dict(base, **{"ssh.mode": "down"}))
    spaced = "C:\\Program Files\\PowerShell\\7\\pwsh.exe"
    add("windows-signing-setup", "pwsh path with spaces over a cmd.exe ssh shell is quoted", "pass", "signing setup is in place", base,
        sets=["pwsh=" + spaced], call='"%s" -NoProfile' % spaced)
    add("windows-signing-setup", "pwsh path with spaces over a PowerShell ssh shell is called with &", "pass", "signing setup is in place", base,
        sets=["pwsh=" + spaced, "hosts.signer.shell=powershell"], call='& "%s" -NoProfile' % spaced)
    add("windows-signing-setup", "another tool's login-profile noise on a good run is ignored", "pass", "signing setup is in place",
        dict(base, **{"ssh.noise": "fnm"}))
    add("windows-signing-setup", "noise that names another tool is not 'pwsh is not installed'", "unknown", "could not ask for the module",
        dict(base, **{"ssh.noise": "fnm-only"}))
    # -- github.secrets_present
    add("secrets-present", "both named secrets exist", "pass", "all 2 named secret(s) exist")
    add("secrets-present", "one named secret is missing", "fail", "GH_TOKEN not in the secrets", {"gh.secrets": "missing"})
    add("secrets-present", "the token may not list secrets", "unknown", "could not be read", {"gh.secrets": "forbidden"})
    add("secrets-present", "no gh on this machine", "unknown", "cannot check here: gh is not installed", shims=())
    # -- release.draft_state
    def rel(name, **over):
        return {"releases.json": ("json", releases(**over)), "repo/RELEASE_NOTES_1_4_0.md": ("text", NOTES)}, name
    sc, _ = rel("good")
    add("draft-release-state", "a draft with the right assets, count and body", "pass", "draft v1.4.0 (id 4242) is in place with 3 asset(s)", sc)
    add("draft-release-state", "the draft id matches the pinned id", "pass", "is in place", sc, sets=["release_id=4242"])
    add("draft-release-state", "no release for the tag", "fail", "no release (draft or published) exists for v1.4.0",
        dict(sc, **{"releases.json": ("json", releases(tag_name="v9.9.9"))}))
    add("draft-release-state", "the release is already published", "fail", "already published", dict(sc, **{"releases.json": ("json", releases(draft=False))}))
    add("draft-release-state", "the draft id is not the pinned one", "fail", "not the pinned 7", sc, sets=["release_id=7"])
    add("draft-release-state", "an asset is missing", "fail", "missing asset(s): app-linux.AppImage",
        dict(sc, **{"releases.json": ("json", releases(assets=[{"name": n, "state": "uploaded"} for n in GOOD_ASSETS[:2]]))}))
    add("draft-release-state", "an asset is half uploaded", "fail", "not fully uploaded",
        dict(sc, **{"releases.json": ("json", releases(assets=[{"name": n, "state": "starter"} for n in GOOD_ASSETS]))}))
    add("draft-release-state", "the body differs from the notes file", "fail", "draft body is not identical",
        dict(sc, **{"releases.json": ("json", releases(body="something else"))}))
    add("draft-release-state", "immutable releases on (good)", "pass", "is in place", sc, sets=["immutable=true"])
    add("draft-release-state", "immutable releases off", "fail", "immutable releases are not enabled", dict(sc, **{"gh.immutable": "off"}), sets=["immutable=true"])
    add("draft-release-state", "immutable setting unreadable", "unknown", "immutable-releases setting could not be read", dict(sc, **{"gh.immutable": "error"}), sets=["immutable=true"])
    add("draft-release-state", "no gh on this machine", "unknown", "cannot check here: gh is not installed", sc, shims=())
    return c


def run_case(n, case, scratch, certs):
    d = os.path.join(scratch, "case-%02d" % n)
    shutil.rmtree(d, ignore_errors=True)
    bindir, repo, state = os.path.join(d, "bin"), os.path.join(d, "repo"), os.path.join(d, "state")
    os.makedirs(bindir)
    os.makedirs(repo)
    with open(os.path.join(repo, "electron-builder.yml"), "w") as fh:
        fh.write("mac:\n  hardenedRuntime: true\n")
    for tool in case["shims"]:
        dest = os.path.join(bindir, tool)
        shutil.copy(SHIM, dest)
        os.chmod(dest, os.stat(dest).st_mode | stat.S_IXUSR)
    for name, value in case["scenario"].items():
        if isinstance(value, str):                                   # shorthand: "tool.scenario" -> file with the word
            path, text = os.path.join(bindir, name if "." in name else name + ".mode"), value + "\n"
        elif value[0] == "file":
            path, text = os.path.join(bindir, "cert.pem"), open(value[1]).read()
        else:
            path = os.path.join(bindir, name) if not name.startswith("repo/") else os.path.join(d, name)
            text = json.dumps(value[1]) if value[0] == "json" else value[1]
        with open(path, "w") as fh:
            fh.write(text)
    # PATH is the shim directory plus the system directories the shim itself needs (cat is not used, but sh is); a case with
    # no shims gets an empty directory only, so the tool is truly absent whatever this machine has.
    path_env = bindir + os.pathsep + "/usr/bin" + os.pathsep + "/bin" if case["shims"] else bindir
    env = {"PATH": path_env, "HOME": d, "LC_ALL": "C", "SEAWORTHY_STATE": state}
    env.update(case["env"])
    argv = [sys.executable, BIN, "run", PROFILE, "--repo", repo, "--only", case["id"], "--json"]
    if case.get("stage"):
        argv += ["--stage", case["stage"]]
    for s in case["sets"]:
        argv += ["--set", s]
    r = subprocess.run(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    try:
        rep = json.loads(r.stdout)
    except ValueError:
        return dict(case, got="(no JSON)", rc=r.returncode, evidence=r.stderr.strip()[:200], problems=["no JSON report: " + r.stderr.strip()[:200]])
    row = rep["checks"][0]
    problems = []
    if row["status"] != case["status"]:
        problems.append("status %s, wanted %s" % (row["status"], case["status"]))
    if r.returncode != EXIT[case["status"]]:
        problems.append("exit %s, wanted %s" % (r.returncode, EXIT[case["status"]]))
    if case["contains"] not in row["evidence"] + " " + " ".join(row["details"]):
        problems.append("evidence lacks %r: %s" % (case["contains"], row["evidence"]))
    if SENTINEL in r.stdout or SENTINEL in r.stderr:
        problems.append("a secret value reached the report")
    log = os.path.join(bindir, "calls.log")
    calls = open(log).read().splitlines() if os.path.exists(log) else []
    if case["shims"] and not calls:
        problems.append("the check never called its tool")
    if case.get("call") and not any(case["call"] in line for line in calls):
        problems.append("no call carried %r" % case["call"])
    for line in calls:
        if not any(re.search(p, line) for p in ALLOWED):
            problems.append("call outside the read-only list: " + line)
        if SENTINEL in line:
            problems.append("a secret value reached an argument list: " + line.split()[0])
    return dict(case, got=row["status"], rc=r.returncode, evidence=row["evidence"], problems=problems, calls=len(calls))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--scratch", required=True)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.scratch, exist_ok=True)
    certs = make_certs(os.path.join(args.scratch, "certs"))
    results = [run_case(i, c, args.scratch, certs) for i, c in enumerate(cases(certs))]
    bad = [r for r in results if r["problems"]]
    lines = ["# Platform checks: proof against stand-in tools", "",
             "Every case is a real `seaworthy run --only CHECK` (not a dry run) with fake `xcrun`, `security`, `spctl`, `gh` and `ssh` on PATH "
             "(`shims/shim`); no vendor endpoint was contacted. Certificates are made-up. A case with no tool on PATH shows how the "
             "check degrades. Each case also asserts that the tool only received read-only calls and that a secret sentinel set in the "
             "environment never reached an argument list or the report.", "",
             "| check | case | status | exit | evidence |", "| --- | --- | --- | --- | --- |"]
    for r in results:
        flag = "  **UNEXPECTED: %s**" % "; ".join(r["problems"]) if r["problems"] else ""
        lines.append("| %s | %s | %s | %d | %s%s |" % (r["id"], r["what"], r["got"], r["rc"], r["evidence"].replace("|", "/"), flag))
    per = {}
    for r in results:
        per.setdefault(r["id"], set()).add(r["got"])
    lines += ["", "Cases: %d, unexpected: %d. Every check was seen to %s." % (
        len(results), len(bad), "pass and to fail" if all({"pass", "fail"} <= s for s in per.values()) else "NOT both pass and fail: " + ", ".join(k for k, s in per.items() if not {"pass", "fail"} <= s)),
        "Read-only calls received by the tools across all cases: %d." % sum(r.get("calls", 0) for r in results)]
    with open(os.path.join(args.out, "PLATFORM-PROOF.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    both = all({"pass", "fail"} <= s for s in per.values())
    return 1 if bad or not both else 0


if __name__ == "__main__":
    sys.exit(main())
