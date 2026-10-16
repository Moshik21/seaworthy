# Changelog

All notable changes to this project are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project follows [Semantic Versioning](https://semver.org/). Schema versions (`seaworthy.profile`, `.report`,
`.receipt`, `.ledger`, `.fixtures`, `.images`) are versioned separately from the tool and are all 1.0.

## [0.1.2] - 2026-10-16

### Added
- `examples/profiles/python-library.json`: a second example profile for a tag-driven pure-Python library (annotated tag on the frozen
  source, clean checkout, version in `pyproject.toml` and `CHANGELOG.md` through `external` `git grep` checks, bounded publish workflow,
  budget, the project's own tests), and `docs/ci-receipt.md`: running the preflight in a GitHub Actions job, uploading the report and
  receipt, reading them and the exit codes, and checking the receipt in a later job. Commands tested locally; the workflow snippet has not run on hosted CI.

## [0.1.1] - 2026-10-16

Package page metadata (project URLs, classifiers, keywords); no behavior change.

## [0.1.0] - 2026-10-16

First release: the engine, the money checks, the platform checks and the dispatch gate.

### Added
- The engine: profiles (JSON with whole-line comments), ordered cost tiers (free, cheap-network, local-heavy), `requires`,
  scopes, waivers of `unknown` results, a text and JSON report, and engine exit codes 0 pass / 1 error / 2 bad spec / 3 no-go / 4 warnings.
  Any failed check is a no-go unless the profile marks it advisory with a written `advisory_reason`, which the report prints;
  `required: false` without one is a bad spec.
- Checks: `git.source_frozen`, `git.clean_checkout`, `release.version_notes`, `workflow.hygiene`, `tools.pins`,
  `hosts.reachable`, `github.runner_image` (drift and migration), `github.workflows_disabled`, `budget.hosted_spend`, and `external`.
- Platform checks, all read-only: `apple.notary_credentials`, `apple.notary_agreement`, `apple.developer_id_identity`
  (expiry and G2 issuer read from the certificate with a small built-in DER reader), `apple.notarization_configured`,
  `windows.signing_role`, `windows.signing_setup`, `github.secrets_present` (names only) and `release.draft_state`. A check whose
  tool or host is unavailable reports `cannot check here: <reason>` (`unknown`), never a pass.
- `examples/profiles/platform/`: a neutral profile with every platform check, stand-in `xcrun`/`security`/`gh`/`ssh` programs and
  `prove.py`, which runs each check for real against them (known-good, known-bad, tool-absent; 58 cases; asserts read-only calls and
  that no secret value reaches an argument list or the report). Output in `PROOF.md`.
- The receipt (content hash, optional HMAC), the dispatch gate with its retry rule and recorded overrides, the budget ledger,
  and the last-good runner image record.
- `seaworthy gate` appends one `gate` line to the state directory's `ledger.jsonl` for every decision (`GO`, `GO (OVERRIDDEN)`, `REFUSED`): the reasons, the qualifying receipt's id and age, the budget left, and `minutes_not_dispatched_upper`, the sum of the planned runs' timeouts for a refusal (an upper bound; `null` with a reason when there is no plan). Same single-write append as the run rows, no new process, no network; `docs/receipt.md#the-gate-row`. `seaworthy demo --case gate` proves a blocked and a passed decision each leave one row.
- Dry runs: nothing leaves the machine; fixtures answer network commands; no receipt is accepted from a dry run.
- `seaworthy demo`: a synthetic project with a good run, 18 known-bad cases and the receipt and gate story, through the public command line. It works from an installed wheel: the demo profile and fixtures ship as package data (`src/seaworthy/demo_data/`) and the demo runs the package itself (`python -m seaworthy`).
- `scripts/self-audit` (secrets, e-mails, home paths and addresses; `--denylist FILE`, repeatable, for names kept outside the repository; `--exempt DIR` for a project that wants a directory skipped), `scripts/build-package` (audits first; runs with no flags and says your own names were not checked), `scripts/smoke` (the three-OS smoke).
- `scripts/merged-check [--head REF] [--base origin/main] [--pr N] [--denylist FILE] [--audit-only]`: the merged-result check. Merges the head onto the (fetched) base in a throwaway scratch clone outside the repository, then runs `scripts/self-audit`, `scripts/smoke` and `seaworthy demo` on the merged tree, so two changes that each pass on their own base but fail together are caught before the merge. Prints one verdict line, exits non-zero on any failure (1 a step failed or the merge conflicts, 2 it could not run), never touches the caller's working tree or branches, and always removes its scratch clone. Part of the definition of done in CONTRIBUTING and the pull request checklist; `scripts/smoke` exercises it on a scratch repository (clean merge, conflict, pass-alone-fail-merged, cleanup).
- `scripts/release-scan`: one command for the pre-release check. Runs `scripts/self-audit` (with `--denylist FILE` when given) and scans the history of all refs for author and committer names and e-mails not given with `--allow`, and for home paths and token shapes in added lines. Prints kinds and counts only, never content; exits 1 on any finding, 2 when a scan could not run. Standard library only; `scripts/smoke` exercises it on a scratch repository with synthetic hits. See CONTRIBUTING.
- `pyproject.toml` (metadata only, no dependencies, console command `seaworthy`, version read from `seaworthy.VERSION`, SPDX `license = "MIT"`, which needs `setuptools>=77`, so Python 3.9 or newer, to build from source; `scripts/smoke` skips its wheel step on Python 3.8 and says why).
- Licence, contributing, security, support and conduct text: MIT (`LICENSE`), `CONTRIBUTING.md`, vulnerability reports through GitHub private vulnerability reporting in `SECURITY.md`, GitHub issues in `SUPPORT.md`, and Contributor Covenant 2.1 in `CODE_OF_CONDUCT.md` (conduct reports use the same private channel).
- A neutral example profile (`electron-desktop.json`), `examples/profiles/minimal.json` (three free checks, in the package zip) and the platform example profile.
- `scripts/smoke` also runs the launcher through a symlink, builds the wheel, installs it into a fresh virtual environment and runs `seaworthy demo` from there (skipped with a stated reason where the system Python cannot make a virtual environment with pip; building the wheel fetches `setuptools` unless it is already available), and checks the argument guard: a `gh-*` wrapper's `workflow run` and `api -X POST` are refused, its `pr view` is allowed.

### Security
- The argument guard holds any command named `gh-*` to the same rules as `gh`, so a wrapper around the GitHub CLI cannot dispatch a workflow or write through `gh api`. See docs/safety.md.

### Fixed
- `bin/seaworthy` resolves its own path with `os.path.realpath`, so a launcher symlinked onto the PATH finds the package instead of ending in `ModuleNotFoundError`.
- `build/` and `*.egg-info/` (left by `pip wheel`) are ignored by git and left out of the zip.

### Documentation
- Documents: profiles, checks, receipt, budget, fixtures, safety, troubleshooting.
- README: an Install section (install into a virtual environment, the Debian and Ubuntu `python3-venv` package, the Python 3.9 floor for `pip` installs, a symlinked launcher), a "which example fits you" list, and the supported-platform table with the Python versions actually run (3.8 to 3.14 on Linux).
- `docs/safety.md#privacy` says that reports and receipts record the machine's hostname.

### Verified
- `seaworthy demo`, `scripts/smoke` and `scripts/self-audit` on Linux with Python 3.8, 3.9, 3.10, 3.11, 3.12, 3.13 and 3.14.
- The first profile was dry-run against a real repository (read-only) with no network (inside a network-less namespace): a full report plus one known-bad case per check.
- Not yet run: macOS and Windows.

### Known limits
- The platform checks are proven only against stand-in tools on Linux. Not yet run against the real Apple, Azure or GitHub services, on a real Mac, or on a real
  Windows signing machine (where the PowerShell command lines are untested).
- Three details come from vendor documentation as read, not from a recording, and each should be confirmed with one real read-only call:
  notarytool's `--output-format json` and `@env:` password reference, and the shape of GitHub's immutable-releases answer.
- `apple.notary_agreement` cannot see a pending agreement on its own (see docs/checks.md); `windows.signing_setup` does not validate the
  catalog; entitlements are not linted.
- The runner-image announcement match is a text heuristic.
- `workflow.hygiene` reads workflow files by indentation, not with a YAML parser.
- Cost estimates use configured per-minute rates; they are not billing data.

### Also in 0.1.0 (omitted from its notes at release)

These were written under "Unreleased" and shipped in the 0.1.0 tree without a dated section: each is present in the `v0.1.0` tag,
so they belong to 0.1.0. The published 0.1.0 and 0.1.1 notes do not list them (0.1.1 changed only the package metadata), and the
0.1.2 notes listed them as new when 0.1.2 added none of them.

#### Added
- Stages: `--stage NAME` (or the profile's `stage`) says which stage of the release a run is for, a check can say where it applies
  (`stages` in the profile; a check type may declare stages it does not apply at), and a check that does not apply is reported
  `skip` ("not applicable at stage X") with the evidence it has. `apple.notary_credentials` is not applicable at `publish`: it
  never contacts Apple and shows the stapled ticket and Gatekeeper result of the new `artifacts` instead. Receipts record the stage
  and `gate --stage` accepts only a receipt made at that stage.
- The report lists, for each check that is not a pass, what would change the verdict (`would_change`), and prints the waiver
  syntax once (JSON `waiver_syntax`); the README says what a waiver is.
- `workflow.hygiene` takes `ref`: a job without `timeout-minutes` whose plain `if:` ref condition is false for the ref being released
  is a warning that names the condition, not a failure. A condition it cannot judge keeps failing.
- Hosts take `"shell": "cmd" | "powershell"`, so a program path with a space is quoted for the remote shell.

#### Changed
- `apple.notary_credentials`: a 401 says it asked once and what it can mean (invalid credential, pending agreement, transient)
  with the next step for each; unset credential variables are `skip` with the reason, never a failure.
- `windows.*`: "not installed" is said only when the error line names the program being run, so another tool's login-profile
  noise no longer reads as "pwsh is not installed"; a file that could not be read for another reason is `unknown`, not `fail`.
- `tools.pins`: a command that exits non-zero is `exited N: <first line>` (`unknown` on a remote host) and its output is never read
  for a version; the default version pattern no longer accepts a bare integer.
- `external`: the evidence is the last non-empty stdout line (the earlier lines, first first, are details).

#### Fixed
- `scripts/merged-check` removes read-only git files when it cleans its scratch clone, so `scripts/smoke` passes on Windows.
