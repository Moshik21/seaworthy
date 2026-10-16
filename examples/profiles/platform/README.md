# Platform checks: one of each, and the proof

`seaworthy.json` lists every platform check once, on a made-up app, with neutral names. Copy it as a start, or read it next to
[docs/checks.md](../../../docs/checks.md#platform-checks).

`prove.py` is the proof: it runs each check as a **real** `seaworthy run --only CHECK` against stand-in `xcrun`, `security`, `spctl`, `gh` and
`ssh` programs (`shims/shim`, copied under those names onto an otherwise empty PATH), for a known-good case, known-bad cases and a case
with the tool missing. The certificates are made here with openssl for a made-up name and never leave the scratch directory. No real
vendor endpoint is contacted and no real credential is used. See [PROOF.md](PROOF.md) for the last run.

```
unshare -rn python3 examples/profiles/platform/prove.py --out DIR --scratch DIR     # needs python3 and openssl 3.4+; unshare is Linux
```

What each case asserts besides the check's status and exit code: the stand-in tool was only ever asked a read (a whitelist of
commands), it was asked at least once (the check really called its tool), and a secret sentinel set in the environment never appeared in
an argument list or in the report.

What this does **not** prove: that the real tools answer as the stand-ins do. The stand-in messages are the documented ones as read
(for example Apple's "A required agreement is missing or has expired"), not recordings. Replace them with recordings the first time you
may make the read-only calls. The Windows PowerShell command lines have not run on Windows.
