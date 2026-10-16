# Checks

`seaworthy checks` lists them; `seaworthy explain NAME` prints the parameters. Cost tier in brackets. A check never
starts a hosted job, and everything marked *network* is answered from fixtures in a dry run.

## git.source_frozen [free]

HEAD, the branch tip on the remote and the expected SHA are one commit; if a tag is named, it is annotated and peels to
that commit. Parameters: `branch` (main), `remote` (origin), `remote_mode` (`tracking` reads the last-fetched remote ref,
offline; `live` asks the remote with `git ls-remote`, *network*), `branch_relation` (`tip`, or `ancestor` when control
commits are allowed above a frozen source), `expected_sha` (defaults to `--sha`), `tag`, `tag_required`.
It never fetches and never writes. `tracking` is stale if you have not fetched; the evidence says so. Pair it with a
`live` copy in the cheap-network tier (`requires` the offline one) for the real answer. Without `--sha` it is `unknown`.

## git.clean_checkout [free]

`git status --porcelain=v1 --untracked-files=all -z`, taken without the index lock. Fails on any change, any stray file
(except `allow_untracked` globs), and any unfinished merge, rebase, cherry-pick, revert or bisect.

## release.version_notes [free]

The package file's `version` equals the release version, and a notes file (a path template with `{version}` and
`{X_Y_Z}`) exists and is not empty. Parameters: `version`, `package_json`, `notes`.

## workflow.hygiene [free]

Reads workflow files (no YAML library; indentation-based) and reports, by file and job: a job without
`timeout-minutes` (GitHub's default is 360 minutes), a timeout over a per-runner-family cap (`max_timeout`), a release
workflow with `cancel-in-progress: true`, third-party actions not pinned to a full commit SHA (`pin_actions`: off, warn,
fail) and non-release workflows that run on `push`, `pull_request`, `pull_request_target` or `schedule` (`ordinary_triggers`).
`workflows` are held to every rule; `ordinary_workflows` are scanned for triggers only; `allow_no_timeout`
(`file.yml:job`) exempts a job, and the profile should say why.

A job without a timeout that does not run for the release is a warning, not a failure: give `ref` (the tag or ref being
released, for example `v1.2.3`) and the job's plain `if:` condition is evaluated against it. It understands only what a release
workflow uses: `github.ref`, `github.ref_name` and `github.ref_type` with `==` and `!=`, `contains`, `startsWith` and `endsWith` over
them, and `!`, `&&`, `||` and parentheses. The warning names the condition. Anything else (`needs.*`, `inputs.*`, an event
payload, no `ref` given, a multi-line condition) cannot be told, and the missing timeout keeps failing.

## tools.pins [free, or cheap-network on a remote host]

Runs each tool's version command and compares the first version-shaped token with `expect` (exact or a glob such as
`24.*`) or `min`. A missing tool fails; a tool that does not answer in time is `unknown`. A command that exits non-zero is
reported as `NAME exited N: <first line>` (a `fail`; `unknown` on a remote host, where the failure may be the host's) and its
output is never searched for a version. The default pattern needs a dotted version (`1.2` or `1.2.3`); give `pattern` for a tool
that prints a bare integer. Each tool may name its own `host`.

## hosts.reachable [cheap-network, *network*]

Runs probes through a named host's runner (and optionally a plain TCP connection first). A timeout is a **fail**, not an
unknown: an unreachable build host is exactly what this exists to catch. Probes are `{name, argv, expect}`; use them to
check the things the release needs on the host (a machine name, a tool path).

## github.runner_image [cheap-network, *network*]

Pinning a runner label does not freeze the image behind it: images are rebuilt about weekly and labels are migrated. For
each `runs-on` label in the checked workflows it reads the newest image release and the open announcements from the
runner-images repository (read-only `gh api` GETs) and compares with the image recorded after your last good run
(`seaworthy image record LABEL=VERSION`, or `image from-log FILE` on a saved "Set up job" log). `mode: drift` warns on a
changed image and is `unknown` with no record; `mode: migration` fails when an open announcement about migrating or
retiring names a label in use; `both` does both. `forbid_latest` fails on `*-latest` labels. The label-to-tag table covers
common Ubuntu, Windows and macOS labels; extend it with `tag_prefixes`. The announcement match is a text heuristic: it
can miss, and it names the issue it matched so a person can judge.

## github.workflows_disabled [cheap-network, *network*]

Fails when a named workflow file is still active in the repository's Actions settings. Use it where policy says ordinary
CI stays off to keep the hosted budget for releases. A named file GitHub does not list is `unknown`.

## budget.hosted_spend [free; *network* only for the optional usage cross-check]

The budget and run cap leave room for the planned runs. See [budget.md](budget.md).

## external [free, or the tier you set]

Runs any command and reads its exit code: 0 pass, 1 fail, 2 unknown, 3 skip, anything else unknown. The last non-empty
stdout line is the evidence (a script's verdict is its last word); the lines before it, first first, become details. Use it to run your project's own release scripts and tests in
their own dry-run modes. Parameters: `argv` (array), `cwd` (relative to the checkout), `env` (names of variables to pass
through; values are never printed), `net` (true if it leaves the machine, so a dry run refuses it). Commands run with a
minimal environment. The engine still refuses hosted-dispatch argv. In a dry run a `local-heavy` command is never run: it
reads a fixture or is `not run`.

## Platform checks

Read-only checks for the signing and publishing steps that otherwise fail only after a paid run has started. None of them
signs, submits, publishes or spends; none prints a secret (see [safety.md](safety.md)). They share one rule: **a check that
cannot be answered on this machine says `cannot check here: <reason>` and is `unknown`, never a pass**, so run each scope
where its tools are (`--scope mac`, `--scope windows`) or run everything from the machine that has them all. `unknown` blocks
a required check; a profile may mark the check `waivable` where a person can decide.

Each was proven against a known-good and a known-bad case with stand-in command line tools, through the public command:
see [examples/profiles/platform/PROOF.md](../examples/profiles/platform/PROOF.md) and [fixtures.md](fixtures.md#shims-for-tools-you-cannot-call).

### apple.notary_credentials [cheap-network, *network*]

Runs `xcrun notarytool history ... --output-format json` (a read-only listing of earlier submissions) and tells the failures
apart: no such keychain profile (`No Keychain password item found`), a team the credential cannot reach or a rejected
app-specific password (`fail`), success (`pass`, evidence is a count, never submission names). Apple's HTTP 401 is asked once and
never retried in the same run (repeated bad sign-ins can lock the Apple ID); the report says it can mean an invalid or revoked
app-specific password (create a new one at appleid.apple.com), a pending or expired developer agreement (the Account Holder
accepts it at developer.apple.com/account) or a transient failure (run the check once more later), and which step comes
first. Credential source: `keychain_profile`,
or, when empty, the Apple ID, team ID and app-specific password *variables* (`apple_id_env`, `team_id_env`, `password_env`; the
password is handed to notarytool as `@env:NAME`, never as a value). An agreement refusal is `unknown` here (Apple answered
before the credential was confirmed): the next check owns it. No `xcrun` is `unknown`. Unset credential variables on a Mac are `skip`, with the reason and "nothing was sent to Apple": the check
never ran, and `apple.notarization_configured` is the one that fails when no credential set is present.

**Stage.** The credential is only what a stage that notarizes needs. At stage `publish` (`--stage publish`, or `"stage"` in the
profile) the check is not applicable: it never contacts Apple and shows `skip` with the evidence that does exist. With
`artifacts` (paths of the built `.app`, `.dmg` or `.pkg`, relative to the checkout or absolute) it runs `xcrun stapler validate`
and `spctl --assess` on each: all stapled and accepted is a `skip` that says so; an artifact with no valid ticket, or one
Gatekeeper rejects, is a `warn` naming it. With no `artifacts` it says what was not checked, as it does when the check's `host` is
not this machine (the artifacts are read from this checkout), when `xcrun` is missing, and in a dry run that has no answer for the
call: each is a `skip` that says "not checked", never a blocking `not-run`. At any other stage it applies and fails honestly. A check entry can set its own `stages` list in the profile to say where it applies.

### apple.notary_agreement [cheap-network, *network*]

Fails on Apple's refusal "A required agreement is missing or has expired" (only the Account Holder can accept it; a retry never
helps; the example profiles list it as a deterministic failure). Apple documents that refusal for a submission, so a history
that merely works does **not** prove no agreement is pending: on its own that is `unknown`. It becomes a `pass` only with a
dated human confirmation, `confirmed_by` and `confirmed_on` (YYYY-MM-DD), younger than `max_age_days` (30); the evidence says
it was human-confirmed. Mark it `waivable` if a person may accept the `unknown`. Not done: the Notary REST API's read-only
submissions list (needs ES256 signing, which the standard library lacks, so it would shell out to `openssl`).

### apple.developer_id_identity [free]

`security find-identity -p codesigning` (without `-v`, so an expired or revoked identity is named, not silently absent) must list
`identity`; then `security find-certificate -a -c IDENTITY -p` is parsed (a small DER reader, no third-party code) for the expiry
and the issuer. Fails when the identity is missing or flagged invalid, the newest matching certificate has expired or expires within
`min_days` (30), or the issuer is not the Developer ID **G2** authority (`require_g2`): the original authority expires on
2027-02-01 and a certificate under it must be reissued by the Account Holder. No `security` tool is `unknown`. It reads
certificates only; no private key is touched.

### apple.notarization_configured [free]

With no credential set in the environment electron-builder skips notarization and a green build ships un-notarized. Reads which
variables are set (names only; an empty value counts as unset) and requires **exactly one complete set** of `sets` (defaults:
Apple ID triple, API key trio, keychain profile): none or two complete sets fail, a partial set is named. Then reads the builder
config (`config`, first existing file) for `hardenedRuntime`: `false` fails, `true` passes, silent is `unknown` (the builder's
default is not proof). The config scan is line-based and does not understand YAML scoping. Entitlements are not linted.

### windows.signing_role [cheap-network, *network* on a remote host]

Two separate questions, run through the check's `host` (for example ssh): can the machine get a token at all
(`az account get-access-token --query expiresOn`: only the expiry is read, never the token), and does `assignee` hold a signer role
(`roles`: the service's current and older names) at `scope` (`az role assignment list`). Not signed in is a `fail`; no signer role
is a `fail`; no `az`, an unreachable host, or no permission to read role assignments is `unknown` (none proves the identity cannot
sign). Commands avoid pipes and double quotes so a Windows host whose ssh shell is `cmd.exe` runs them unchanged. A program path
with a space (`az`, `pwsh`) is quoted for the host's shell: `"shell": "cmd"` (the default) or `"powershell"` on the host
(`& "path"`); see [profiles.md](profiles.md#hosts). Only an error line that names the program being run reads as "not installed";
another tool's login-profile noise (`The term 'fnm' is not recognized`) does not.

### windows.signing_setup [cheap-network, *network* on a remote host]

Always: the signing `module` is visible to PowerShell 7 (`pwsh`), optionally exactly once at `module_version`. When given: SignTool
at `signtool` is at least `min_signtool` (10.0.2261.755); the `Endpoint` in `metadata_file` equals `endpoint` (a region mismatch
commonly causes a 403); `timestamp_file` (the signing script or its config) names `timestamp_url` (certificates are valid for three
days, so a missing timestamp breaks verification soon after). Parts not asked for are listed as "not checked". Catalog validation is
project-specific: run the project's own validator with `external`. Not run on Windows yet: see [the README](../README.md#supported-platforms).
`pwsh` may be a path with spaces (quoted as above). When the host's output is only another tool's error, the part is `unknown`
("could not ask ..."), not "pwsh is not installed" and not a failure.

### github.secrets_present [cheap-network, *network*]

`gh secret list` (names and update times; GitHub never returns values) must contain every name in `names`, for `repo`, an
environment (`env`) or an organization (`org`). A missing name is a `fail`; no `gh`, no permission, or a timeout is `unknown`.

### release.draft_state [cheap-network, *network*]

Reads the repository's newest 100 releases (a GET; drafts are listed to a token with push access) and finds the one for `tag`. Fails
when there is none (create the draft first: a draft lookup that 404s cost a hosted run once), when it is already published (a
published release cannot be repaired in place), when its id is not the pinned `release_id`, when a named asset is missing or not fully
uploaded, when the asset count differs, when the body differs from `notes_file`, or, with `immutable_releases`, when the
repository's immutable-releases setting is off. A tag not found among exactly 100 releases is `unknown`. The immutable-releases endpoint
and its answer shape come from GitHub's REST reference as read, not from a recording: confirm it with one real read-only call.

## What no check proves

A passing Seaworthy run says the things it checked are right *now*. It does not prove the build will succeed, that nobody
changes the world between the run and the dispatch (the receipt's age limit bounds that), or that a check you did not
write exists. Add the check; keep the rationale.
