# Seaworthy

A release-readiness check engine. Before you spend hosted CI minutes on a release, it runs the real checks locally,
prints one pass/fail report (text or JSON), and writes a **receipt** that your dispatch step can require. It also keeps
a **budget ledger** of the hosted runs you have already paid for, so "don't re-run a failed run just to retry" is a
mechanism and not a promise. It works on any project; it is not Kubernetes and not Docker.

**Names.** The project is Seaworthy; the command is `seaworthy` and the Python import name is `seaworthy`. On the Python
Package Index the distribution is named **`seaworthy-release`**, because the plain name `seaworthy` belongs to an unrelated
package. Nothing is published to a package index yet; the build here is the zip from `scripts/build-package`, named
`seaworthy-release-<version>.zip`.

It is built for teams that ship desktop apps (signed, notarized, several platforms) on a small hosted-minutes budget,
but nothing in the engine is specific to one project: a project is a *profile*, a JSON file of ordered checks.

- **Real checks, not proxies.** Git state, tool versions, workflow files, build-host reachability and GitHub reads run
  for real. A check that cannot be sure says `unknown`; a check that did not run says `not run`. Neither is ever a pass.
- **Platform checks for signing and publishing.** Apple notarization credential and agreement, Developer ID identity and
  expiry, Windows signing role and module visibility, repository secrets (names only) and draft release state, all read-only,
  through the vendors' own tools. A check whose tool is missing on this machine says `cannot check here`, never a pass
  ([docs/checks.md](docs/checks.md#platform-checks)).
- **Cheapest first.** Free checks, then cheap-network checks, then heavy local ones. One run lists every cheap problem;
  heavy work is skipped once a required check has failed.
- **A dry run that cannot spend.** `--dry-run` never leaves the machine: network answers come from a fixtures file or the
  check is reported `not run`. A dry run never writes a receipt into the state directory; with `--receipt-out FILE` it can
  write one to the file you name, and the gate never accepts it.
- **Standard library only.** Python 3.8 or newer, one command, no build step, no daemon, no telemetry, no model call.

## Install

There is nothing to install from a package index yet, and the tool has no dependencies. Get the source (a clone of this
repository, or the zip from `scripts/build-package`) and run it in place: `python3 bin/seaworthy` on macOS and Linux,
`bin\seaworthy.cmd` on Windows. You need Python 3.8 or newer and `git` on your PATH.

To get a `seaworthy` command on your PATH, install the checkout into a virtual environment:

```
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/seaworthy --version
```

- On Debian and Ubuntu, `python3 -m venv` fails with "ensurepip is not available" until you install the distribution's
  venv package (`sudo apt install python3-venv`, or `python3.N-venv` for a specific version). Without it, run
  `python3 bin/seaworthy` directly.
- Installing with `pip` needs Python 3.9 or newer (the build needs `setuptools>=77`). On Python 3.8, run
  `python3 bin/seaworthy` in place.
- A symlink to `bin/seaworthy` on your PATH also works, without a virtual environment.
- To uninstall, delete the checkout (or the virtual environment, or the symlink), and delete the state directory
  (`$SEAWORTHY_STATE`, else `~/.local/state/seaworthy` on Linux, `~/Library/Application Support/seaworthy` on macOS,
  `%LOCALAPPDATA%\seaworthy` on Windows), which holds the receipts and the ledger.

## Five-minute quickstart

Below, `seaworthy` means `python3 bin/seaworthy` on macOS and Linux and `bin\seaworthy.cmd` on Windows (or the command
from the virtual environment above).

1. **Watch it work on a synthetic project** (nothing leaves your machine):

   ```
   seaworthy demo
   ```

   It builds a throwaway git project, runs a good release, then breaks one thing per check and shows which check
   catches it, with the exit code. Then it walks the receipt and the dispatch gate: refuse with no receipt, allow with a
   fresh one, refuse an edited receipt, refuse a dry-run receipt, refuse a re-run of a failed SHA, allow a named override.

2. **Read a profile.** `seaworthy checks` lists the checks; `seaworthy explain git.source_frozen` shows one with its
   parameters. Which example fits you:
   - `examples/profiles/minimal.json`: three free checks (source frozen, clean checkout, hosted budget). Start here for any
     project; it passes when your clean checkout is at the fetched tip of `origin/main`. Add checks as your release needs them.
   - `examples/profiles/python-library.json`: a pure-Python library that publishes from a tag: annotated tag on the frozen
     source, clean checkout, version in `pyproject.toml` and `CHANGELOG.md`, bounded publish workflow, budget, and the project's
     own tests. [docs/ci-receipt.md](docs/ci-receipt.md) walks it through a CI job.
   - `examples/profiles/electron-desktop.json`: a commented example with every check type, for a signed desktop app on
     several platforms. Expect a NO-GO on a project that is not one; delete the checks that do not apply.
   - `examples/profiles/platform/seaworthy.json`: every Apple, Windows and GitHub release check once, with stand-in tools.

3. **Dry-run it on your project.** Copy an example, set `params`, `hosts` and `budget`, then:

   ```
   seaworthy validate my-profile.json
   seaworthy run my-profile.json --repo /path/to/checkout --sha <frozen-sha> --dry-run
   ```

   Checks that need the network are reported `not run` until you give them recorded answers (`--fixtures`,
   see [docs/fixtures.md](docs/fixtures.md)).

4. **Run it for real**, the day of the release, on the release machine:

   ```
   seaworthy run my-profile.json --repo /path/to/checkout --sha <frozen-sha> --set version=1.4.0
   ```

   A `PASS` writes a receipt into your per-user state directory. Exit codes: `0` pass, `1` engine error, `2` bad
   profile or arguments, `3` no-go, `4` pass with warnings.

   Not every check applies at every stage of a release. `--stage publish` (or `"stage": "publish"` in the profile) says
   which stage this run is for: a check that does not apply there is shown as not applicable, with whatever evidence it
   has, instead of failing for something the stage does not need. A check that applies still fails honestly, and a
   receipt records its stage, so a `publish` receipt does not open the gate for another stage.

5. **Gate the paid step.** Put one line in front of whatever starts the hosted run:

   ```
   seaworthy gate my-profile.json --sha <frozen-sha> --set version=1.4.0 --planned linux:60 --planned windows:180 && ./start-hosted-release
   ```

   `gate` exits 0 only with a fresh, intact, real-run PASS receipt for that SHA and profile, enough budget left, and
   no earlier hosted failure at that SHA that a re-run could not change. After the run, record it with
   `seaworthy ledger add` so the next gate sees it. See [docs/receipt.md](docs/receipt.md) and [docs/budget.md](docs/budget.md).

## What the output looks like

```
Seaworthy 0.1.0   profile demo   sha 6bab4f76a426   DRY RUN
started 2026-10-09T02:35:42Z on <host>   scope all

FREE CHECKS  (6 ms)
  PASS      source-frozen      HEAD = expected = last-fetched origin/main (6bab4f76)
  FAIL      clean-checkout     1 changed or untracked path(s)
                                 - ?? stray.txt
                                 would change: fix what the evidence names and re-run; a fail is never waivable
  PASS      version-and-notes  version 1.2.3 and notes agree
  PASS      workflow-hygiene   2 workflow file(s) bounded
  PASS      hosted-budget      1.56 of 5.00 USD committed, 0 of 2 runs left after the plan

CHEAP NETWORK CHECKS  (0 ms)
  UNKNOWN   tool-pins-builder       node did not answer in time
                                      would change: answer it by running where the tool or host is available; this check is not waivable in this profile
  FAIL      builder-reachable       answers: timed out after 5s
                                      would change: fix what the evidence names and re-run; a fail is never waivable
  PASS      runner-image-migration  no open announcement names a label in use

VERDICT: NO-GO  8 pass, 2 fail, 1 unknown, 1 skip
  blocking: clean-checkout, tool-pins-builder, builder-reachable
  to waive: seaworthy run PROFILE --sha SHA --waive CHECK_ID [--waive CHECK_ID ...] --by NAME --reason TEXT
    a waiver names each check, covers only this SHA (one release), is written in the report and the receipt, and applies only to an 'unknown' result of a check the profile marks waivable; a fail is never waivable
    waivable among the blocking checks now: none
  dry run: 0 command(s) left this machine, 5 answered from fixtures; a dry run never writes a receipt
  exit code 3  (0 pass, 1 error, 2 bad spec, 3 no-go, 4 pass with warnings)
```

The "never writes a receipt" in the dry-run line means no receipt reaches the state directory. With `--receipt-out FILE`
a passing dry run writes a receipt to that file, marked `dry-run`, and the gate never accepts it.

`--json` prints the same report as `seaworthy.report` 1.0 (every check with status, evidence, details, timing and
cost tier, plus a `gate` object `{value, as_of, note}` that a status board can show as a release gate).

The `--json` report is the integration point: anything that wants a verdict (a status line, a card on a dashboard) reads
`seaworthy.report` 1.0 from `seaworthy run --json` and needs nothing else from this repository.

## What a waiver is

A check that comes back `unknown` (the tool or host could not answer) blocks the release. A person who has the answer by
other means can waive that one result, and only that one:

```
seaworthy run my-profile.json --sha <frozen-sha> --waive CHECK_ID --by NAME --reason "what you checked and how"
```

A waiver is a named, release-scoped, recorded acknowledgement, never a silent pass. It names each check (`--waive` is
repeated per check), it covers the one SHA being released, and it is written in the report and in the receipt with who made it and
why. It applies only to an `unknown` result of a check the profile marks `waivable`; a `fail` is never waivable, and
the check still shows as `unknown`, counted as waived. A NO-GO report prints this syntax once and lists, for every check
that is not a pass, what would change the verdict.

A `skip` is not a pass. A check that is not applicable at the stage, or that was skipped because its credential variables
are not set in this shell (`apple.notary_credentials` does this: "not checked", nothing sent to Apple), proved nothing and
blocks nothing. The check that requires the configuration to exist is the separate `apple.notarization_configured`; keep it
enabled in any profile that notarizes. A skip that has a next step is listed in what would change the verdict.

## What it will never do

- Start, re-run, cancel or publish anything hosted. The engine refuses commands such as `gh workflow run`,
  `gh run rerun`, `gh release create`, `gh api` with a write method, and `git push`, in any check, however the profile spells them.
- Run a shell string. Commands are argv arrays; nothing goes through `sh -c` or `eval`.
- Sign, notarize, submit, create or publish anything: the platform checks only list, read and ask for versions.
- Read or print a secret. Checks receive environment variable *names*; output is scrubbed of credential patterns
  before it is stored or shown; tokens are read from files or the environment, never from argv.
- Call the network from a dry run, or let a dry run authorise spend.
- Write outside its own state directory (and the report file you ask for). It never changes the repository it inspects.
- Report a pass it did not earn: unreachable services, missing tools and unreadable usage are `unknown` or `not run`.

## Honesty

Every claim below can be checked in the file named beside it.

- **Measured, derived, unknown.** A check's status is *measured* when a command, file or service answered (`pass`,
  `fail`, `warn`), and *unknown* when it could not (`unknown`, `not run`; `src/seaworthy/report.py`, per-check
  rules in [docs/checks.md](docs/checks.md)). The verdict, the counts, the budget figures and the release `gate` object
  are *derived* from those rows (`src/seaworthy/engine.py`). Minutes in a `gate` ledger row are an upper bound from the
  plan's worst-case timeouts, labelled as such ([docs/receipt.md](docs/receipt.md#the-gate-row)). Spend is *estimated,
  not billed* ([docs/budget.md](docs/budget.md)).
- **Unknown and stale are shown, never zero or healthy.** An unreachable service, a missing tool or an unreadable
  usage endpoint is `unknown` or `cannot check here`, never a pass ([docs/checks.md](docs/checks.md#platform-checks),
  [docs/budget.md](docs/budget.md)). The report's `gate` value is `pass`, `fail` or `unknown`, never a default pass, and
  carries an `as_of` time (`src/seaworthy/engine.py`). A receipt older than `receipt.max_age_hours` (default 12) stops
  counting, and so does one for another profile or checks hash (`src/seaworthy/receipt.py`,
  [docs/receipt.md](docs/receipt.md#the-gate)). A gate row with no qualifying receipt says `receipt_unknown`, not an age of 0.
- **What it cannot know.** Whether a hosted run was started outside the wrapper (the ledger only knows what you record,
  [docs/budget.md](docs/budget.md#limits)); that the real vendor services accept a release, because the platform checks
  have only been run against stand-in tools ([examples/profiles/platform/PROOF.md](examples/profiles/platform/PROOF.md));
  that a profile is complete, since it checks only what the profile lists; or who is at the keyboard, because the receipt
  hash and HMAC catch accident, not the owner ([docs/receipt.md](docs/receipt.md#the-receipt)).
- **What it never claims.** A pass it did not earn ([docs/safety.md](docs/safety.md#exit-codes): an unknown, not-run or
  failed required check is a no-go); that a dry run authorised anything (a dry-run receipt never opens the gate,
  `src/seaworthy/receipt.py`); that a refusal "saved" money (it reports minutes not dispatched, as an upper bound, not a
  saving). It makes no model call and sends no telemetry ([docs/safety.md](docs/safety.md#privacy)).
- **Advice, not action.** Its built-in checks start, re-run, cancel and publish nothing hosted, and sign and submit
  nothing; the refusal list is a backstop, not a sandbox, so `external` checks a profile defines are your responsibility
  ([docs/safety.md](docs/safety.md#what-is-refused)). The gate decides and exits 0 (go) or 3 (refused), or 2 for a bad
  profile or arguments, and your wrapper decides what happens next ([docs/safety.md](docs/safety.md#exit-codes),
  [docs/receipt.md](docs/receipt.md#the-gate)).

## Supported platforms

| Platform | Python | Status |
| --- | --- | --- |
| Ubuntu / WSL2 | 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14 | run: demo, smoke, self-audit (the platform proof on 3.11 and 3.14) |
| macOS (Apple Silicon) | 3.13 | run on a hosted macOS 15 image (2026-10-16): smoke, demo, self-audit. Python 3.8 is not in the matrix (no Apple Silicon build). Not run on a developer's own Mac |
| Windows 11 (native; NT 10.0.26300, PowerShell 5.1, Python 3.12.10) | 3.8+ | run: install, `seaworthy demo`, the quickstart and `gate`. `python scripts\smoke` runs from a git clone, not from the zip (the zip has no `scripts/`) |
| Windows Server 2022 (hosted image) | 3.8, 3.13 | run on a hosted windows-2022 image (2026-10-16): smoke, demo, self-audit |

The platform checks are proven on Linux against stand-in `xcrun`, `security`, `spctl`, `gh` and `ssh` programs
([examples/profiles/platform/PROOF.md](examples/profiles/platform/PROOF.md)); they have **not** yet been run against Apple's,
Azure's or GitHub's real services, on a real Mac, or on a real Windows signing machine. The first real run of each is its check.

Python 3.8 is the floor: no later standard-library API is used. `scripts/smoke`, `seaworthy demo` and `scripts/self-audit`
pass on Linux on Python 3.8, 3.9, 3.10, 3.11, 3.12, 3.13 and 3.14. The table says what was *actually* run; it will be
updated as the other rows are.

## Known issues

What is open today. The [Honesty](#honesty) section says what the tool cannot know; this list is what has not been done yet.

- Not published to a package index; install from a clone or the zip from `scripts/build-package` ([Install](#install)).
- The platform checks have only been run against stand-in tools, not Apple's, Azure's or GitHub's real services, a real Mac, or a real Windows signing machine ([Supported platforms](#supported-platforms), [PROOF.md](examples/profiles/platform/PROOF.md)).
- The workflow snippet in [docs/ci-receipt.md](docs/ci-receipt.md) has not run on hosted CI; the commands in it were run locally.
- `release.version_notes` reads a JSON package file only (`package.json` shape); other formats such as `pyproject.toml` need an `external` check ([python-library.json](examples/profiles/python-library.json)).
- Hosted CI is manual-dispatch only and runs Python 3.8 and 3.13; macOS with Python 3.8 is not in the matrix, and 3.9 to 3.12 and 3.14 have been run on Linux only ([ci.yml](.github/workflows/ci.yml)).
- Installing with `pip` needs Python 3.9 or newer; on 3.8, run `python3 bin/seaworthy` in place ([Install](#install)).
- `apple.notary_agreement` cannot see a pending agreement by itself; it passes only with a dated human confirmation ([docs/checks.md](docs/checks.md#applenotary_agreement-cheap-network-network)).
- Three vendor details (notarytool's JSON output and `@env:` password reference, and the shape of GitHub's immutable-releases answer) come from documentation as read and have not been confirmed with a real call ([CHANGELOG](CHANGELOG.md)).

## Resource budgets (measured)

| What | Budget | Measured |
| --- | --- | --- |
| The free checks of a typical profile | under 10 s | 6 checks, about 15 ms (git plus file reads) |
| One whole invocation, including interpreter start | | about 60 ms |
| Memory | under 50 MB resident | about 22 MB |
| Idle cost | none: no daemon, nothing runs between invocations | |
| The gate row (one ledger line per `gate` decision) | no new process, no network | about 0.02 ms and one write of about 0.6 KB per decision; a whole `gate` call is the same within noise (about 43 ms either way) |

## Documents

- [docs/profiles.md](docs/profiles.md): the profile format, every field, scopes, waivers, platform slots.
- [docs/checks.md](docs/checks.md): every built-in check, its parameters, what it proves and what it cannot.
- [docs/receipt.md](docs/receipt.md): the receipt, the dispatch gate, the retry rule, overrides, signing.
- [docs/ci-receipt.md](docs/ci-receipt.md): running the preflight in a CI job, uploading the receipt, reading it and the exit codes.
- [docs/budget.md](docs/budget.md): the budget ledger, rates, windows, carried spend, the usage cross-check.
- [docs/fixtures.md](docs/fixtures.md): dry runs, fixture files, writing a known-bad case.
- [docs/safety.md](docs/safety.md): the safety model: what is refused, what is written, exit codes.
- [docs/troubleshooting.md](docs/troubleshooting.md): real messages and what to do about them.
- `examples/`: neutral example profiles (`minimal`, `python-library`, `electron-desktop`) and `examples/profiles/platform/` (every platform
  check once, with its stand-in-tool proof driver).
- In the source repository: `scripts/self-audit` (scan for secrets and names before a release), `scripts/release-scan`
  (the self-audit plus a scan of the whole git history; see CONTRIBUTING), `scripts/build-package`
  (build the distributable zip; refuses on any audit finding), `scripts/smoke` (the three-OS smoke), `scripts/merged-check`
  (the checks on the result of a merge, run right before merging; see CONTRIBUTING).

## Community

[CONTRIBUTING.md](CONTRIBUTING.md) (including packaging), [SUPPORT.md](SUPPORT.md), [SECURITY.md](SECURITY.md) and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Licence

MIT licensed, see [LICENSE](LICENSE). Copyright (c) 2026 Moshik21.
