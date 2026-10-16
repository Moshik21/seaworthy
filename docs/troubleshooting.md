# Troubleshooting

Messages are shown as the tool prints them.

**`seaworthy: profile ... is not valid:` followed by a list.** The profile has structural problems; every one is listed.
Typical: `unknown check type 'x'` (`seaworthy checks` shows the names), `parameter 'y' is required`, `cannot require 'z' (...): a
check may only require same-cost or cheaper checks`.

**`${name} is not a known parameter`.** A `${...}` refers to something not in `params` (or `sha`, `repo`). Add it to `params` or pass `--set name=value`.

**`no expected SHA was given` (unknown).** `git.source_frozen` needs `--sha`. The release is *a commit you chose*; the check
does not guess it.

**`last-fetched origin/main (not refreshed) is X, not the expected Y`.** The remote-tracking ref is the last one your clone
fetched. If you froze `Y` on the remote, run `git fetch` yourself first (Seaworthy never fetches), or add the `live` variant.

**`no last-fetched origin/main ... for main`.** The clone has no `refs/remotes/origin/main`: a fresh `git init`, or the
remote is named differently. Set `remote`/`branch`, or fetch.

**`... is not installed (want X)` on a tool pin.** The tool is not on this machine's PATH. Run Seaworthy on the release
machine, or put the tool's check on the right `host`.

**`dry run: 'gh api ...' would leave this machine and no fixture answers it` (not run).** Expected in a dry run. Add a fixture
(see [fixtures.md](fixtures.md)) or run for real.

**`host X answered ...` never appears; `timed out after 5s`.** The host did not answer within `timeout_s`. For ssh, run the
same command by hand with `-v`; the usual causes are a stopped machine, a changed address, a firewall, or a key not in the agent.

**`image state could not be established` with `no last-good image recorded`.** Record one after a good hosted run:
`seaworthy image record PROFILE windows-2022=20260908.1.0`, or `image from-log PROFILE saved-setup-log.txt`.

**`billing usage unavailable: ... (HTTP 403)`.** The token cannot read the billing endpoint. With `usage_api: advisory` it is
a note and the ledger decides; with `required` it makes the budget check `unknown`. Never treated as zero.

**`hosted run N failed at this SHA (deterministic)`.** The gate will not let the same SHA be dispatched again. Read the
failure, fix it, commit, run `seaworthy run` at the new SHA, and gate that. If the failure was a runner outage, record it
with `--failure-class infrastructure` and run again.

**`the receipt's content hash does not match its content`.** The file was edited after it was written, or truncated. Run again.

**`the profile changed since it was written (hash differs)`.** Any edit to the profile (or a different `--set`) invalidates earlier receipts. Run again.

**A check shows `error` with `check crashed`.** A bug in a check or a surprising environment. The last lines of the traceback
are in the check's details; open an issue with the report JSON (it is scrubbed of credential patterns, but read it first). An
`internal error` on the command line is the engine itself: `SEAWORTHY_DEBUG=1` prints the full traceback.

**Windows: `python` not found.** Use the Microsoft Store Python or `py -3`; edit `bin\seaworthy.cmd` to call `py -3` if needed.

**`cannot check here: ... is not installed` (unknown).** A platform check needs a tool this machine does not have (`xcrun` and `security`
on a Mac, `gh`, `ssh`, `az` or PowerShell on the signing host). Run the check where the tool is (`--scope mac`, `--scope windows`), or
from the machine that has them all. It is never treated as a pass.

**`cannot check here: the host 'X' cannot be reached` (unknown).** ssh exited 255: the host is down, the key is not loaded, or the
alias is wrong. `hosts.reachable` says which; run the command from the profile by hand with `ssh -v`.

**`Apple answered HTTP 401` (fail, `apple.notary_credentials`).** One call was made and it is not retried. It means the app-specific
password is invalid or revoked (make a new one at appleid.apple.com and update the secret or keychain profile), or a developer agreement
is pending (the Account Holder accepts it at developer.apple.com/account), or a transient failure (run once more later). Work through
them in that order; repeated failed sign-ins can lock the Apple ID. At stage `publish` this check is not applicable.

**`not applicable at stage 'X'` (skip).** The check does not apply at the stage you gave with `--stage` (or the profile's `stage`).
That is not a pass: the evidence it shows is what was readable instead, and a `warn` there names an artifact that is not clean.

**`the credential works, but nothing proves there is no pending agreement` (unknown).** A working `notarytool history` is not proof.
Have the Account Holder check Apple's developer portal and pass `confirmed_by` and `confirmed_on` (YYYY-MM-DD), or waive the result
with a recorded reason if the profile marks it waivable.

**`the issuer is not the Developer ID G2 authority`.** The certificate was issued under the original authority, which expires on
2027-02-01. The Account Holder must reissue it; then run again.

**`the secret list could not be read` / `the immutable-releases setting could not be read` (unknown).** The `gh` token lacks access
(listing secrets and reading that setting need admin on the repository). Fix the token's access; do not treat the result as a pass.
