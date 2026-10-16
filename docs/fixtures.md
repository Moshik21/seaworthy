# Dry runs and fixtures

`seaworthy run ... --dry-run` is the run you do any time, anywhere. It guarantees that nothing leaves the machine:

- A command a check marks as *network* (or that runs on a named host, or is a local-heavy command) is never executed.
  It is answered from a fixtures file given with `--fixtures`, or the check is reported `not run` and says why.
- Local read-only commands (`git status`, `node --version`) do run, unless a fixture answers them first, which lets you
  rehearse "the wrong Node" on a machine that has the right one.
- A dry run never writes a receipt that the gate accepts (`--receipt-out` writes one marked `dry-run`; the gate refuses it).
- The report header and footer say `DRY RUN` and how many commands left the machine (always 0) and how many fixtures answered.

For the strongest proof, run under a network-less namespace (`unshare -rn seaworthy run ...` on Linux): then a stray
network call is impossible, not just refused.

## Fixture file

```json
{"schema": "seaworthy.fixtures", "schema_version": "1.0", "entries": [
  {"kind": "run", "argv": ["node", "--version"], "rc": 0, "stdout": "v24.9.0\n"},
  {"kind": "run", "host": "builder", "argv": ["echo", "seaworthy-ok"], "timeout": true},
  {"kind": "run", "argv": ["gh", "api", "repos/octo/app/actions/workflows?per_page=100"], "rc": 0, "stdout_file": "workflows.json"},
  {"kind": "run", "argv_prefix": ["git", "ls-remote", "origin"], "rc": 0, "stdout": "{sha}\trefs/heads/main\n"},
  {"kind": "tcp", "target": "builder.example.invalid:22", "result": "timeout"}
]}
```

- `kind: run` matches a command by `argv` (exact) or `argv_prefix`, on `host` (default `local`). Fields: `rc`, `stdout`,
  `stderr` (or `stdout_file` / `stderr_file`, relative to the fixtures file), `timeout: true`, `missing: true` (command not installed).
- `kind: tcp` answers a TCP connection test with `ok`, `timeout`, `refused` or `error`.
- `{sha}` inside `stdout` / `stderr` is replaced with the SHA under test, so one fixture can answer for any commit.
- The first matching entry wins. Fixtures take precedence over real local commands.

Record fixtures from real answers where you can (save the output of the read-only command once, on a day you are allowed
to make it). A fixture is a claim about the world; the report says "answered from fixtures" so nobody mistakes it for a live
check.

## A known-bad case

A known-bad case is a fixture or a change to a throwaway checkout that makes exactly one check fail, plus the statement of
which check and which exit code. `seaworthy demo` is a set of those over a synthetic project (`src/seaworthy/demo.py`);
copy its pattern for your profile: make a scratch clone, break one thing, run with `--dry-run --fixtures`, assert the
status and exit code. Use `--all` so costlier tiers still run after the broken check and only the intended check differs.

## Shims for tools you cannot call

A dry run answers from recorded text. To prove a check against the real command line *program* (its argument handling, its
exit code, what it does when it is missing), put a stand-in program on PATH instead and do a **real** run of that one check
(`--only ID`): the stand-in is the only tool the run can reach, so nothing real is contacted. `examples/profiles/platform/prove.py`
does this for every platform check: `shims/shim` is copied under the names `xcrun`, `security`, `gh` and `ssh`, scenario files
beside it choose the answer, it logs every call and exits 99 on any call it was not written to answer. The driver asserts the status
and exit code of a known-good and a known-bad case, that the tool was never asked anything but a read, and that a secret sentinel in
the environment never reached an argument list or the report. A case with an empty PATH shows how the check degrades when the tool is
missing. Run it under `unshare -rn` for a network-less proof.
