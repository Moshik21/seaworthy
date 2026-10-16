# Security policy

Seaworthy is pre-1.0 hobby tooling, maintained on a best-effort basis by [Moshik21](https://github.com/Moshik21).

## Reporting a vulnerability

Report it privately through GitHub's private vulnerability reporting: open the repository's **Security** tab, choose
**Report a vulnerability**, and describe the problem. Do not open a public issue, pull request or discussion for it.

Please include the Seaworthy version (`seaworthy --version`), your operating system and Python version, the smallest
profile or command that shows the problem, and what you expected instead. Leave real tokens, keys and private data out of the
report; a made-up value shows the problem just as well.

This is a hobby project, so no response time is guaranteed. Reports are read and answered on a best-effort basis, and a fix
ships when it is ready.

## Supported versions

Only the latest release is supported. A fix lands in the next release; older releases are not patched.

## In scope

- A way to make Seaworthy start, re-run or change something hosted (bypassing the refused-command list), or run a shell string.
- A way to make it print or store a credential.
- A dry run that makes a network call, or a dry-run or edited receipt that the gate accepts.
- A write outside the state directory, the requested report file and the requested dry-run receipt.
- A path traversal through fixture file names or profile paths that reads or writes outside the intended files.

## Out of scope

- Anyone who can already write the state directory and read the receipt key can forge a receipt: the gate guards against mistakes
  and stale greens, not against the release owner.
- A profile you wrote that runs a harmful program through an `external` check: review profiles like code.
- The accuracy of estimated costs (they are configured rates, not billing).

## Handling secrets

Seaworthy reads tokens only from the environment variables an `external` check names and from the receipt key file, never from
argv. Reports are scrubbed of credential patterns but you should still treat a report as internal.
