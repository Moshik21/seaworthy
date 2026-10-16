# Using the preflight and its receipt in CI

This page shows how to run Seaworthy as a job in front of a hosted publish job, how to read what it leaves behind, and how
to make the next job depend on it. The example is a tag-driven pure-Python library, using
[`examples/profiles/python-library.json`](../examples/profiles/python-library.json). The same shape works for any project:
change the profile, keep the job.

**What was tested.** The commands below were run locally (macOS, Python 3.14, Seaworthy 0.1.1), in bash with the GitHub
variables set by hand, against a throwaway git project with a bare local remote, in a fresh clone checked out detached on the tag (to approximate
what a hosted checkout leaves). Seaworthy itself was installed with the same `pip` command as in the workflow, from the pinned
tag on GitHub. **Commands tested locally; workflow snippet not run on hosted CI.** The three `uses:` steps (checkout,
setup-python, upload-artifact), the `needs:` ordering, and the claims that `GITHUB_SHA` is the tagged commit on a tag push and
that `fetch-depth: 0` leaves `origin/main` and the tag in place come from GitHub's documentation and were not observed. The real
runner image may differ from the local machine. In the snippet the three action pins are placeholders: the checks that guard
this repository reject a 40-character hex string in its text, so the real commits are not printed here. Look each one up
(`git ls-remote https://github.com/actions/checkout refs/tags/v4.2.2`, and the same for `actions/setup-python` `v5.6.0` and
`actions/upload-artifact` `v4.6.2`; if two lines are printed, take the one ending in `^{}`) and paste the 40-character commit
in place of each `<commit SHA of ...>`. The local run used those three real commits, and the profile's `workflow-hygiene`
check passed on that file. Treat the YAML as a starting point to try on a branch, not as a proven workflow. All output on this page is from that local run, with the
host name replaced by `<host>`, paths shortened and long blocks trimmed where marked.

## Where a CI preflight helps, and where it does not

A preflight job in CI is useful when the expensive step is a *later* job (a signing, packaging or publish job that needs a
macOS or Windows runner, a long build, a registry that never lets you replace a version). The preflight job itself runs on the
cheapest runner and takes seconds; the later job starts only if it passed.

Two limits to know before you rely on it:

- **A fresh runner has an empty state directory.** The budget ledger and the "do not re-run a failed SHA" rule
  ([budget.md](budget.md), [receipt.md](receipt.md#the-gate)) only know what is in the state directory. On a hosted runner that
  is nothing, so `budget.hosted_spend` there counts only the plan in the profile, and the retry rule cannot see earlier
  failures. For those two, run the preflight (and `seaworthy gate`) on the machine that keeps the ledger, or keep the ledger
  somewhere that persists. The checks on the repository itself (source frozen, clean tree, versions, workflows, tests) do not
  depend on state.
- **A tag push has already happened.** In the workflow below the tag push is what starts the run, so the preflight cannot stop
  the push; it stops the jobs that cost money. To check before pushing, run the same command on your own machine first (the
  profile's header comment shows it).

## The profile

[`examples/profiles/python-library.json`](../examples/profiles/python-library.json) is a different shape from
[`minimal.json`](../examples/profiles/minimal.json): it ties the release to an annotated tag, checks that the version is written
down consistently, bounds the publish workflow, and runs the project's own tests. Every check is one `seaworthy checks` lists.

| Check id | Built-in check | What it does here |
| --- | --- | --- |
| `source-frozen` | `git.source_frozen` | HEAD, the last-fetched tip of `origin/main` and `--sha` are one commit, and the tag `v<version>` exists, is annotated and points at that commit (`tag_required`). It never fetches. |
| `clean-checkout` | `git.clean_checkout` | No changed, staged or stray files and no unfinished merge or rebase. |
| `version-in-pyproject` | `external` | `git grep` finds a line `version = "<version>"` in `pyproject.toml`. The matching line is the evidence. |
| `version-in-changelog` | `external` | `git grep` finds `## [<version>]` in `CHANGELOG.md`. |
| `workflow-hygiene` | `workflow.hygiene` | Every job in `.github/workflows/*.yml` has a timeout, third-party actions are pinned to a full commit SHA (`pin_actions: fail`), and `publish.yml` is held to the release-workflow rules. |
| `hosted-budget` | `budget.hosted_spend` | The one planned hosted run (15 minutes on Linux) fits the cap and the run limit. Estimated, not billed. |
| `tests-pass` | `external` (`local-heavy`) | Runs `python3 -m unittest discover -v -s tests` and reads its exit code. It runs last, and only if every cheaper required check passed. |

There is no built-in "tests ran" check; `external` is the way to run your own test command, and its exit code is the verdict
(0 pass, 1 fail, 2 unknown, 3 skip). `release.version_notes` is not used because it reads a JSON package file (`package.json`
shape) only, which a Python project does not have, so the version and changelog checks use `git grep` through `external`.
In the pyproject pattern the dots of the version match any character. The test command takes its Python from the `python`
param (`python3`; on Windows run it with `--set python=py`). `unittest` writes to stderr, so the evidence line for that check is
its first output line, not a summary; a runner that prints a summary to stdout (for example `pytest -q`) reads better.

Validate it, then run it on the release commit:

```
seaworthy validate release/seaworthy.json
seaworthy run release/seaworthy.json --repo . --sha <frozen-sha> --set version=1.4.0
```

```
profile python-library is valid: 7 check(s), 7 enabled, hash 9e8152d939a1
  source-frozen              git.source_frozen            free          required
  clean-checkout             git.clean_checkout           free          required
  version-in-pyproject       external                     free          required
  version-in-changelog       external                     free          required
  workflow-hygiene           workflow.hygiene             free          required
  hosted-budget              budget.hosted_spend          free          required
  tests-pass                 external                     local-heavy   required
```

## The workflow

Keep the profile in the repository (here `release/seaworthy.json`). The job below checks out the full history (the branch tip
and the annotated tag must be present), installs Seaworthy from its `v0.1.1` tag on GitHub (nothing is on a package index yet; for a stricter pin use the tag's
commit, `git ls-remote https://github.com/Moshik21/seaworthy refs/tags/v0.1.1`, and take the commit it names), runs the preflight with its state and report under `$RUNNER_TEMP`, stops the job unless the exit
code is 0 or 4, and uploads the report and receipt whether it passed or not. The publish job waits for it.

```yaml
name: publish

on:
  push:
    tags: ["v*"]

permissions:
  contents: read

jobs:
  preflight:
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@<commit SHA of v4.2.2>
        with:
          fetch-depth: 0   # the branch tip and the annotated tag must be present
      - uses: actions/setup-python@<commit SHA of v5.6.0>
        with:
          python-version: "3.12"
      - name: Install Seaworthy
        run: |
          python -m venv "$RUNNER_TEMP/sw-venv"
          "$RUNNER_TEMP/sw-venv/bin/python" -m pip install "seaworthy-release @ git+https://github.com/Moshik21/seaworthy@v0.1.1"
          echo "$RUNNER_TEMP/sw-venv/bin" >> "$GITHUB_PATH"
      - name: Preflight
        run: |
          mkdir -p "$RUNNER_TEMP/sw-out"
          rc=0
          seaworthy run release/seaworthy.json --repo . --sha "$GITHUB_SHA" --set "version=${GITHUB_REF_NAME#v}" \
            --state "$RUNNER_TEMP/sw-out/state" --out "$RUNNER_TEMP/sw-out/report.json" || rc=$?
          echo "seaworthy exit code $rc"
          # 0 pass, 4 pass with warnings; 1 error, 2 bad profile, 3 no-go all stop the release
          case "$rc" in 0|4) ;; *) exit "$rc" ;; esac
      - name: Keep the report and receipt
        if: always()
        uses: actions/upload-artifact@<commit SHA of v4.6.2>
        with:
          name: seaworthy-preflight
          path: ${{ runner.temp }}/sw-out
          if-no-files-found: warn

  publish:
    needs: preflight
    runs-on: ubuntu-24.04
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@<commit SHA of v4.2.2>
      - run: echo "build and publish here"
```

Notes on the choices:

- `fetch-depth: 0` makes the checkout fetch every branch and tag, so `origin/main` and the annotated tag exist for
  `git.source_frozen`. On a tag push the checkout is a detached HEAD at the tagged commit; `GITHUB_SHA` is that commit, and
  `source-frozen` passes when it is also the tip of `main`. For a tag that sits below the tip, set `branch_relation` to
  `ancestor` in that check's params ([checks.md](checks.md#gitsource_frozen-free)).
- A project with dependencies installs them before the preflight, so `tests-pass` can run. Do it in a step before
  "Preflight", and keep anything it writes out of the checkout or into `.gitignore`: `clean-checkout` fails on a stray file.
- The profile's `workflow-hygiene` check reads this very file, so the pins, the `timeout-minutes` lines and the `publish.yml`
  name matter. With the placeholders replaced by full commit SHAs the profile passes on this YAML (run locally). That proves the file meets the profile's rules, not that GitHub runs it; with the placeholders left in, the check fails ("3 action ref(s) not pinned to a commit SHA"; run locally).
- The preflight step's own stdout is the report, so it is in the job log. The upload step has `if: always()` so a no-go keeps
  its report for whoever has to fix it.

## Exit codes, and gating the next step

`seaworthy run` returns one of these ([safety.md](safety.md#exit-codes)); a CI step fails on any non-zero code, so the step
decides what each means:

| Exit code | Meaning | The step above |
| --- | --- | --- |
| 0 | PASS | continues; a receipt is written |
| 4 | PASS with warnings (an advisory check did not pass) | continues; a receipt is written |
| 3 | NO-GO: a required check failed, was unknown, or did not run | fails the job; `publish` is skipped |
| 2 | bad profile or arguments; nothing ran | fails the job |
| 1 | engine error (a check crashed or was refused) | fails the job |
| 127 and others | the command itself was not found or died | fails the job (seen locally when `seaworthy` was not on `PATH`) |

Observed locally: exit 0 (below) and exit 3 (below), and 127 for a missing command. Exit 4 was not produced in this run; the
`case` line passes it through as written.

The passing run, as the preflight step printed it (trimmed to the report):

```
Seaworthy 0.1.1   profile python-library   sha a745eb21587b   REAL RUN
started 2026-10-16T21:05:06Z on <host>   scope all

FREE CHECKS  (156 ms)
  PASS      source-frozen         HEAD = expected = last-fetched origin/main (a745eb21)
  PASS      clean-checkout        working tree is clean
  PASS      version-in-pyproject  pyproject.toml:version = "1.4.0"
  PASS      version-in-changelog  CHANGELOG.md:## [1.4.0] - 2026-10-16
  PASS      workflow-hygiene      1 workflow file(s) bounded
  PASS      hosted-budget         0.09 of 5.00 USD committed, 1 of 2 runs left after the plan

LOCAL HEAVY CHECKS  (51 ms)
  PASS      tests-pass  test_add (test_add.AddTest.test_add) ... ok

VERDICT: PASS  7 pass
  0 command(s) left this machine; no check starts a hosted job
  receipt: $RUNNER_TEMP/sw-out/state/receipts/a745eb21587b9706af3165d46f4ac5c9089ab2a7/20261016T210507Z.json
  exit code 0  (0 pass, 1 error, 2 bad spec, 3 no-go, 4 pass with warnings)
seaworthy exit code 0
```

Now a release whose tag says 1.5.0 on a commit that still says 1.4.0 (same commit, a `v1.5.0` tag made on it):

```
Seaworthy 0.1.1   profile python-library   sha a745eb21587b   REAL RUN
started 2026-10-16T21:05:07Z on <host>   scope all

FREE CHECKS  (149 ms)
  PASS      source-frozen         HEAD = expected = last-fetched origin/main (a745eb21)
  PASS      clean-checkout        working tree is clean
  FAIL      version-in-pyproject  exit 1
                                    would change: fix what the evidence names and re-run; a fail is never waivable
  FAIL      version-in-changelog  exit 1
                                    would change: fix what the evidence names and re-run; a fail is never waivable
  PASS      workflow-hygiene      1 workflow file(s) bounded
  PASS      hosted-budget         0.09 of 5.00 USD committed, 1 of 2 runs left after the plan

LOCAL HEAVY CHECKS  (0 ms)
  NOT RUN   tests-pass  not run: a cheaper required check has not passed (use --all to run anyway)
                          would change: re-run with --all, or fix the cheaper check or requirement it waits on

VERDICT: NO-GO  4 pass, 2 fail, 1 not-run
  blocking: version-in-pyproject, version-in-changelog, tests-pass
  to waive: ... (the waiver syntax, as in the README)
  0 command(s) left this machine; no check starts a hosted job
  exit code 3  (0 pass, 1 error, 2 bad spec, 3 no-go, 4 pass with warnings)
seaworthy exit code 3
```

The job stops at exit 3, no receipt is written (only a passing real run writes one), and `report.json` is still uploaded. A
failing `external` check shows only `exit 1` as its evidence because `git grep` prints nothing when it finds no line; the
check's `rationale` (in `--verbose` output and in the JSON) says what it was for. Re-run just that check with the lines you
expect in front of you: `git grep -n "version" -- pyproject.toml`.

## Reading the report and the receipt

A passing run leaves two files in the uploaded artifact:

```
sw-out/report.json
sw-out/state/receipts/<sha>/<time>.json
```

`report.json` is the `seaworthy.report` 1.0 document that `--json` prints: the verdict, the exit code, every check with its
status and evidence, and a `gate` object. A script reads it without parsing the text (`jq` here; any JSON reader works):

```
$ jq -r '"\(.verdict) exit \(.exit_code) gate \(.gate.value)"' report.json
PASS exit 0 gate pass

$ jq -r '.checks[] | select(.status != "pass") | "\(.id): \(.status): \(.evidence)"' report.json    # the no-go run
version-in-pyproject: fail: exit 1
version-in-changelog: fail: exit 1
tests-pass: not-run: not run: a cheaper required check has not passed (use --all to run anyway)
```

The receipt is the record the gate accepts ([receipt.md](receipt.md#the-receipt)). An excerpt of the passing run's receipt
(`results` trimmed to three of seven checks, the two config hashes left out):

```json
{
  "at": "2026-10-16T21:05:07Z",
  "host": "<host>",
  "mode": "real",
  "profile": "python-library",
  "receipt_hash": "d5824d80...",
  "results": [
    {
      "id": "source-frozen",
      "outcome": "ok",
      "status": "pass"
    },
    {
      "id": "clean-checkout",
      "outcome": "ok",
      "status": "pass"
    },
    {
      "id": "tests-pass",
      "outcome": "ok",
      "status": "pass"
    }
  ],
  "schema": "seaworthy.receipt",
  "schema_version": "1.0",
  "scope": "all",
  "sets": {
    "version": "1.4.0"
  },
  "sha": "a745eb21587b...",
  "stage": null,
  "tool_version": "0.1.1",
  "verdict": "PASS",
  "waivers": [],
  "warnings": []
}
```

`sha`, `profile`, the two config hashes, `scope`, `stage` and `sets` say what the receipt covers; `mode: real` and
`verdict: PASS` are what the gate requires; `receipt_hash` lets it notice an edit. `host` is the runner's name, so look at it
before attaching a receipt to an issue ([safety.md](safety.md#privacy)).

## Making a later job depend on the receipt

`needs: preflight` already skips `publish` when the preflight job fails. To also have the later job *verify* the receipt (it
is the right sha, the profile and the checks have not changed since, and the file is intact and fresh), download the artifact
and call `seaworthy gate` with it. Put this in the `publish` job after the checkout and the same setup-python and install steps, with
`actions/download-artifact` (pinned like the others) writing the artifact to `$RUNNER_TEMP/sw-out`:

```yaml
      - name: Check the receipt
        run: |
          receipt=$(ls -1 "$RUNNER_TEMP/sw-out/state/receipts/$GITHUB_SHA/"*.json | tail -n 1)
          seaworthy gate release/seaworthy.json --sha "$GITHUB_SHA" --set "version=${GITHUB_REF_NAME#v}" \
            --receipt "$receipt" --state "$RUNNER_TEMP/sw-gate"
```

The `gate` command (tested locally against the receipt above; the download step was not run):

```
gate GO for a745eb21587b
  receipt 20261016T210507Z.json is valid; budget has room (0 run(s), 0.00 USD so far)

# receipt edited after it was written (its scope changed by hand; exit 3):
gate REFUSED for a745eb21587b
  - receipt: edited.json: the receipt's content hash does not match its content (edited after it was written)

# same receipt, profile run with --set version=1.5.0 (exit 3):
gate REFUSED for a745eb21587b
  - receipt: 20261016T210507Z.json: the profile changed since it was written (hash differs)
```

`gate` exits 0 for go and 3 for refused, with every reason printed. Two refusals seen locally: a receipt edited after it was
written (`the receipt's content hash does not match its content`), and the same receipt checked with `--set version=1.5.0`
(`the profile changed since it was written`). Each decision also writes one line to the gate's own ledger, which on a
hosted runner is thrown away with the runner.

## What this does not give you

- It is advice, not enforcement. The receipt's hash and optional HMAC catch accident and casual edits, not someone who can edit
  the workflow or the artifact; the artifact is readable by anyone who can read the repository's Actions runs
  ([receipt.md](receipt.md#the-receipt)).
- It checks only what the profile lists. A passing run says those things are right now, not that the publish will succeed
  ([checks.md](checks.md#what-no-check-proves)).
- It does not start, re-run or cancel anything hosted, and it is not told about runs it did not see: record each hosted run
  with `seaworthy ledger add` on the machine that keeps the ledger ([budget.md](budget.md#recording-runs)).
