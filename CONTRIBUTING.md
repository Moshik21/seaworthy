# Contributing

Seaworthy is a release-readiness check engine: it runs read-only checks against a hosted release pipeline and a gate that
refuses to dispatch a release on a stale or missing green. It is pre-1.0 hobby tooling, open-sourced under the
[MIT licence](LICENSE) by [Moshik21](https://github.com/Moshik21) for problems that came up in real releases. Contributions
are welcome.

**Scope.** Checks that read real state (a tool, a host, a workflow, a certificate) and say `unknown` or `not run` when they
cannot, plus the engine, report, receipt, budget ledger and gate around them. It does not start, re-run or publish anything
hosted, and it will not grow a daemon, a network service, telemetry or third-party dependencies. If you are unsure whether an
idea fits, ask first.

## Proposing a change

- **Open an issue first for anything non-trivial** (a new check, a new command or flag, a change to a schema or the gate, a
  refactor), and say what problem it solves. A typo, a doc fix or a small bug fix can go straight to a pull request.
- **Keep pull requests small and single-purpose.** One behaviour per change is easier to review and to revert.
- **Expected response time: best effort, no promise.** This is a hobby project; an issue or pull request may wait days or
  weeks. A quiet thread is not a rejection.
- Report a vulnerability privately, not in an issue: see [SECURITY.md](SECURITY.md). Everyone taking part follows the
  [code of conduct](CODE_OF_CONDUCT.md). By sending a change you agree it is licensed under the project's [MIT licence](LICENSE).

## Definition of done

Before you open a pull request (and again if `main` moved before the merge), all of these pass:

1. `python3 scripts/self-audit` (add `--denylist FILE` for any private list of names you keep outside the repository; it can be
   given more than once).
2. The full suite: `python3 scripts/smoke` and `python3 bin/seaworthy demo`. The behavioural demo cases, the self-audit and the
   three-OS smoke are the test set. Add a case to `src/seaworthy/demo.py` for every behaviour you add (a good case and the
   known-bad twin), through the public command line. Please do not add unit tests of internals.
3. `scripts/merged-check [--head REF] [--base origin/main] [--pr N] [--denylist FILE]` against current `origin/main`. Every
   change passes the checks above on its own base, but nothing else re-checks what the merge produces, and two changes that are
   each clean can fail together. It fetches the base, merges your head onto it in a scratch clone outside your tree (never
   touching your working tree or branches), and runs `scripts/self-audit` (with the denylist from `--denylist` or
   `$MERGED_CHECK_DENYLIST`), `scripts/smoke` and `seaworthy demo` there. It prints one line, `merged-check: ok` or
   `merged-check: FAILED <step>: <first finding>`, and exits non-zero on any failure (1: a step failed or the merge conflicts;
   2: it could not run).
4. `CHANGELOG.md` is updated, and the links in any Markdown file you touched resolve.

## The honesty rule

A check calls the real tool against the real target, or says `unknown` / `not run`. It never approximates and reports a pass,
and a result never claims more than was actually checked. The same goes for your change: say plainly in the pull request what
you ran and what you did not.

## House rules

- **Standard library only.** Python 3.8 or newer. No build step, no third-party packages, no telemetry, no model call.
- **Generic core.** Code, schemas, docs, example profiles, fixtures and defaults carry no personal, host, account or project
  names, and the self-audit exempts no directory. Tool-specific code lives outside this repository: integrate through the
  `seaworthy run --json` report (see [the README](README.md#what-the-output-looks-like)); example profiles use placeholder names.
- **Read-only.** A check never starts, re-runs or publishes anything hosted.
- **Every check has a rationale** in the profiles that use it: the incident that put it there.
- **Argv arrays, never shell strings.** Output that might hold a credential goes through `redact`.

## Before a public release

`python3 scripts/release-scan --denylist <your list> --allow <name or e-mail> [--allow ...]` is the one-command gate. It runs
`scripts/self-audit` on the tree (with the denylist) and scans the history of **every ref** for author and committer
names and e-mail addresses that match no `--allow` value (`*` and `?` match, so `*@users.noreply.github.com` allows that
domain; with no `--allow`, every identity is a finding), and for home paths and token shapes in any line a commit ever
added, even if the line is gone from the tree (merge commits included, diffed against each parent, so a line a merge
introduced may be counted once per parent; only the exit status and the kinds are authoritative). It prints kinds and
counts only, never the matching content, and exits 0 when clean, 1 on any finding, 2 when a scan could not run. The
denylist applies to the tree, not to the history. Scan the exact refs you intend to publish; run it again after any
history rewrite.

## Packaging

`pyproject.toml` holds metadata only: no dependencies, the `seaworthy` console command (`seaworthy.cli:main`), the distribution name `seaworthy-release` (plain `seaworthy` belongs to an unrelated package), and the
version, which is read from `VERSION` in `src/seaworthy/__init__.py`, the single source of truth. Change the version there only. The licence is declared as the SPDX expression `MIT` (with `LICENSE` bundled), which needs
`setuptools>=77` to build from source (Python 3.9 or newer for the build; the built wheel is pure Python and runs on 3.8).

- Distributable zip: `python3 scripts/build-package --denylist <your list>` (runs the self-audit first; without a list it still
  audits for secrets, e-mails, paths and IPs and says your names were not checked).
- Wheel, for a local check: `python3 -m pip wheel --no-deps -w <scratch dir> .`, then install it into a throwaway virtual
  environment and run `seaworthy demo`. `python3 scripts/smoke` does exactly that.

## Community files

- [SECURITY.md](SECURITY.md): how to report a vulnerability, and what is in and out of scope.
- [SUPPORT.md](SUPPORT.md): where to ask for help.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md): how we work together.
- Issue and pull request templates live in `.github/`.
