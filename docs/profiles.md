# Profiles

A profile is one JSON file describing a release process: the checks, in what order, on which hosts, against which
budget. It is the only thing you write to use Seaworthy on a project. Keep it in the project's repository, next to its
release docs, so it changes when the release process does. Schema: `seaworthy.profile` 1.0.

JSON has no comments, so a line whose first non-blank characters are `//` or `#` is ignored (whole lines only, line
numbers in error messages stay right). Everything else is plain JSON.

`seaworthy validate PROFILE` is phase one of every run: it parses the file, resolves parameters, and checks every rule
below. It exits 2 with every problem listed. Nothing is executed.

## Top level

| Field | Required | Meaning |
| --- | --- | --- |
| `schema`, `schema_version` | yes | `"seaworthy.profile"` and `"1.0"`. A different major version is refused. |
| `name` | yes | short lowercase identifier; names the profile's state directory and appears in reports and receipts |
| `description` | no | one sentence |
| `params` | no | values the checks refer to as `${name}`; override with `--set name=value` |
| `hosts` | no | named runners (below) |
| `budget` | no | the hosted-run budget (see [budget.md](budget.md)); required by `budget.hosted_spend` and `gate` |
| `receipt` | no | `max_age_hours` (default 12), `hmac_key_file`, `override_allowed_by` (see [receipt.md](receipt.md)) |
| `deterministic_failures` | no | `{id, pattern, why}` entries: failures a re-run cannot fix |
| `waivers` | no | `{"allowed_by": [names]}`: who may waive an `unknown` result; empty or absent means anyone who names themselves |
| `stage` | no | the release stage this profile is run for, a short lowercase word such as `publish`; `--stage` overrides it (see below) |
| `checks` | yes | the ordered list of checks |

Built-in parameters: `${sha}` (from `--sha`, empty if not given) and `${repo}` (the checkout path). `--set a.b=value`
changes a nested field instead of a param, for example `--set budget.cap=5`. The profile hash in the receipt is taken
after `--set` overrides, so a receipt cannot be reused under a different configuration.

## Hosts

```json
"hosts": {"builder": {"os": "windows", "argv_prefix": ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "builder-alias"]}}
```

A check with `"host": "builder"` has every command it runs prefixed with `argv_prefix`. The prefix is an argv array, never
a shell string. For a Windows host `"shell"` (`"cmd"`, the default, or `"powershell"`) names the shell ssh hands the command to, so
a program path with a space is quoted for it (`"C:\Program Files\...\pwsh.exe"` for cmd, `& "..."` for PowerShell). Use key-based ssh with `BatchMode=yes` so a missing key fails at once instead of waiting at a prompt.
Anything that runs on a named host counts as leaving the machine, so a dry run answers it from fixtures. The Windows
platform checks run PowerShell on such a host over ssh; nothing is installed on it.

## Checks

| Field | Default | Meaning |
| --- | --- | --- |
| `id` | required | short lowercase identifier, unique in the profile |
| `check` | required | a built-in check name (`seaworthy checks`), `external`, or a reserved platform slot |
| `params` | `{}` | the check's parameters (`seaworthy explain CHECK`); unknown names are an error |
| `requires` | `[]` | ids that must have passed (or warned) first; may only name same-cost or cheaper checks |
| `host` | `local` | where the check's commands run |
| `cost` | the check's own | `free`, `cheap-network` or `local-heavy`: the tier it runs in |
| `required` | `true` | a required check that does not pass blocks the release. `false` makes it advisory and **needs an `advisory_reason`**: a written sentence on why this check may fail without stopping the release; `validate` refuses an advisory check without one |
| `waivable` | `false` | an `unknown` result may be waived by a named person |
| `enabled` | `true` | `false` lists the check in the report as skipped without running it |
| `scope` | `any` | a platform or area tag; `--scope X` runs checks whose scope is `any` or `X` |
| `stages` | the check type's own | the stage names where this check applies; elsewhere it is reported `skip`, "not applicable at stage X". Without it a check type may declare stages it does not apply at (only `apple.notary_credentials`, at `publish`) |
| `timeout_s` | 10 / 30 / 900 by cost | the check's own time limit; a check that runs out of time is `unknown` |
| `rationale` | none | the incident that put the check here. Strongly recommended; `validate` notes its absence |

Every check carries a `rationale` on purpose. A check list nobody can justify becomes a ritual; each entry should name
the failure it exists to prevent, and be removed when that failure can no longer happen.

## Order, tiers and stopping

Checks run in cost tiers: all `free` checks, then all `cheap-network` checks, then all `local-heavy` checks. Inside a
tier they run in profile order, after the checks they `require`. Every check in a tier runs, so one run lists every
problem of that tier. After a tier, if any required check did not pass, the later tiers are reported `not run` (unless
`--all`). A check whose `requires` did not pass is `not run`.

## Results

Each check ends as one of: `pass`, `warn`, `fail`, `unknown`, `skip`, `not-run`, `error`.

- A **required** check that is `fail`, `unknown`, `not-run` or `error` makes the verdict **NO-GO** (exit 3; any `error` makes it exit 1).
- Every failed check is a NO-GO **unless** the profile marks that check advisory with a written `advisory_reason`. Then it reports as a warning (label WARN, with the reason printed under it in the report and kept in the JSON as `advisory_reason`) and the verdict can be PASS with exit 4. A check that itself returns `warn` is also a warning. Advisory is for signals, not for things that gate money or signing: if a failure would waste a paid run or break signing, the check is required.
- `unknown` on a `waivable` check can be waived: `seaworthy run ... --waive ID --by NAME --reason TEXT`. The waiver is
  printed in the report and kept in the receipt, and covers the one SHA of that run. A `fail` is never waivable: fix it.
- Every non-pass check in the report, and every `skip` that has a next step, carries `would_change` (text, and in the JSON row): what would change the verdict. A
  NO-GO also prints the waiver syntax once (JSON: `waiver_syntax`, with `waivable_now` naming the checks it applies to).

## Stages

A release has stages (build, sign, publish, ...) and a check that is right at one can be wrong at another: a notarization
credential is what the signing stage needs, and says nothing about artifacts that are already notarized. Give the run its stage
with `--stage NAME` or the profile's `stage`; the report prints it and the receipt records it. A check that does not apply is
`skip` with "not applicable at stage NAME" and any evidence it has, and never blocks. `seaworthy gate --stage NAME` (default:
the profile's stage) accepts only a receipt made at that stage. With no stage given nothing changes: every check applies.

## Platform checks

The platform checks (`apple.notary_credentials`, `apple.notary_agreement`, `apple.developer_id_identity`,
`apple.notarization_configured`, `windows.signing_role`, `windows.signing_setup`, `github.secrets_present`, `release.draft_state`)
are ordinary checks; see [checks.md](checks.md#platform-checks). Three profile habits go with them:

- **Scope them.** Tag Apple checks `"scope": "mac"` and Windows checks `"scope": "windows"` with `"host"` naming the signing
  machine, then run `--scope mac` on the Mac. A check whose tool is not on the machine you are on is `unknown` ("cannot check here"),
  which blocks a required check: that is the point, but it means an unscoped run only passes where every tool is reachable.
- **Pass names as params, not values.** Identity names, team and account ids are not secrets and can be `${params}`; secret *values*
  are never params. Credential checks take environment variable *names* (and notarytool is given `@env:NAME`).
- **Order by when the state exists.** `release.draft_state` is only true after the release script has staged the draft, so run it
  at that point (for example with its own scope), not before.

A planned slot (a reserved check name that is not built) is still reported `not run` and blocks if enabled and required; none is
reserved today.

## Scopes

Blocking is scoped per platform, so a Windows signing failure need not block the Linux part. Tag platform-specific
checks with a scope (`mac`, `windows`), then run `--scope mac` on the Mac. The receipt records the scope it covers, and
`gate --scope mac` accepts a receipt for `mac` or `all`.

## Writing a new profile

Start from `examples/profiles/minimal.json` and add the checks your release needs, or from
`examples/profiles/electron-desktop.json` and delete what your release does not do. Add a `rationale` to each check
from your own release log: every hosted run you regretted is a check. Prefer calling your project's own release scripts in
their own dry-run modes (an `external` check) over re-implementing them: a check that imitates the release is the
thing this tool exists not to be.
