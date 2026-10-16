# Safety model

## What is refused

`guard_argv` runs on every command any check issues, including `external` checks and whatever the profile spells:

- `gh workflow run|enable|disable`, `gh run rerun|cancel|delete`, `gh release create|upload|edit|delete`,
  `gh pr merge`, `gh repo delete`, `gh secret set|delete`, `gh variable set|delete`
- `gh api` with a method other than GET, or with a request body (`-f`, `-F`, `--field`, `--raw-field`, `--input`)
- the same rules for any command named `gh-*`, so a wrapper around the GitHub CLI is held to the `gh` rules
- `git push`
- `curl` or `wget` with a write method or a body

A refused command makes its check `error` (engine exit 1) and is never run. The list is a backstop, not a sandbox: an
`external` check can run any other program, so review the profile like code. Keep checks read-only.

## What the platform checks read

Read-only commands only: `xcrun notarytool history`, `security find-identity` and `find-certificate -p` (a public certificate, never a key),
`az account get-access-token --query expiresOn` and `az role assignment list`, PowerShell `Get-Module`, `Get-Item` and `Get-Content`,
`gh secret list` (names) and `gh api` GETs. Credentials are handled by name: `apple.notarization_configured` and the notary checks read
which environment variables are set but never print or store a value, and notarytool is given the app-specific password as `@env:NAME`,
so the value is in no argument list. A token is never requested in full: only its expiry is read. Evidence from these checks is counts
and names, never submission lists. The proof (`examples/profiles/platform/prove.py`) asserts all of this against stand-in tools.

## What runs, and how

- argv arrays only; nothing goes through a shell. A profile cannot add a new action, only choose built-in checks and
  commands.
- Commands run with a minimal environment (PATH, HOME, temp, locale and ssh-agent socket variables) plus the names an
  `external` check lists. Git runs with `GIT_OPTIONAL_LOCKS=0` and `GIT_TERMINAL_PROMPT=0`, so it never takes the index
  lock and never prompts.
- Every command has a timeout; a check has a time limit; hosts are reached with `BatchMode` ssh so nothing waits on a prompt.
- Output is scrubbed of credential patterns (tokens, keys, bearer values, `password=...`) before it is stored or printed.

## What is written

Only the state directory (`ledger.jsonl`, `images.json`, `receipts/`), the report file given with `--out`, and the
dry-run receipt given with `--receipt-out`. `seaworthy gate` appends one `gate` line to `ledger.jsonl` for every decision it
makes (see [receipt.md](receipt.md#the-gate-row)). The state directory is per user and per profile
(`$SEAWORTHY_STATE/<profile>`, or `--state DIR` for an explicit directory). The inspected repository is never changed:
no fetch, no checkout, no index refresh.

## Identity of processes

Seaworthy never signals a process, so it has no process-identity problem. It matches nothing by name.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | PASS, nothing to warn about |
| 1 | engine error (a check crashed or was refused, or an internal error); never a pass |
| 2 | bad profile or arguments; nothing was run |
| 3 | NO-GO (a required check failed, was unknown, or did not run), or the gate refused |
| 4 | PASS with warnings (an advisory check did not pass, or a check warned) |

## Privacy

No telemetry. The only network traffic is what you configure: the `gh api` reads, `git ls-remote`, ssh to your hosts. A dry
run makes none.

**The hostname is recorded.** Every report carries the name of the machine that ran it (`host`, from the operating
system's hostname; the text report prints it as `started ... on <host>`), and so does every receipt
([receipt.md](receipt.md#the-receipt)). Both stay on your machine unless you copy them. Before you paste a report or a
receipt into an issue or a chat, replace the hostname if it is private.
