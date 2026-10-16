# The receipt and the dispatch gate

## Why

"Never re-run a failed hosted run just to retry" only holds if something stops the retry. The receipt is a small file that
says: *at this commit, under this profile, every required check passed on this machine, this recently.* The gate is the
command your dispatch step runs first; it refuses without a good receipt.

## The receipt

Written by `seaworthy run` when the verdict is PASS, the run is real (not `--dry-run`), complete (no `--only`), and `--sha`
was given. Stored under `<state>/receipts/<sha>/<time>.json`. Schema `seaworthy.receipt` 1.0:

| Field | Meaning |
| --- | --- |
| `sha` | the commit it covers |
| `profile`, `profile_hash` | the profile name and the hash of the profile after `--set` overrides |
| `checks_hash` | hash of the checks that were selected to run (enabled, in scope) |
| `scope`, `sets` | the scope it covers and the `--set` overrides in force |
| `stage` | the release stage it was made for, or `null`; the gate refuses a receipt made at another stage (receipts written before this field existed read as no stage) |
| `mode` | `real` or `dry-run`; only `real` ever authorises anything |
| `verdict`, `warnings` | `PASS` and the advisory checks that warned |
| `at`, `host` | UTC time written and the machine that ran it |
| `results` | each check's status and outcome |
| `waivers` | who waived what, and why |
| `receipt_hash` | SHA-256 of the receipt's own content: edits are detected |
| `hmac` | present when the profile names `receipt.hmac_key_file`: HMAC-SHA256 over the content |

The hash catches accident and casual edits. The HMAC catches a forged receipt from someone who can write the state
directory but cannot read the key (keep the key file readable only by the release owner). Neither protects against
someone who can do both; the gate is a guard against mistakes and stale greens, not a security boundary against the owner.

## The gate

```
seaworthy gate PROFILE --sha SHA [--scope S] [--planned OS:MINUTES ...] [--receipt FILE] [--override NAME --reason TEXT]
```

Exit 0 means go; exit 3 means refused, with every reason printed. It checks:

1. **A qualifying receipt exists:** intact (hash and, if configured, HMAC), `real`, `PASS`, for exactly this SHA, with the
   same profile hash and checks hash as the profile on disk now, covering the requested scope, and younger than
   `receipt.max_age_hours` (default 12). Change the profile and old receipts stop counting.
2. **The retry rule:** if the ledger holds a failed hosted run at this SHA and its failure class is `deterministic` (the
   default for a failure), the gate refuses: re-running the same SHA cannot change the result; fix it, commit, and get a new
   PASS at a new SHA. If the class is `infrastructure` (a runner outage), the gate wants a PASS written *after* the failure.
3. **The budget:** runs used plus planned runs within `budget.max_runs`, spent plus the worst case of the planned runs
   within `budget.cap`. `--planned` replaces the profile's plan, for a retry that needs only one job.

Run `seaworthy ledger add` after every hosted run, success or failure, so the gate can see it. If your dispatch wrapper is
a script, call the gate first and stop on a non-zero exit.

## The gate row

Every decision the gate makes appends exactly one `kind: "gate"` line to the state directory's `ledger.jsonl` (the budget
ledger, [budget.md](budget.md)), so blocks and the minutes they held back are counted from a record instead of remembered.
It is written in one `write` call, like the run rows, and costs no new process and no network call. A usage error
(exit 2, for example an override by a name the profile does not allow) is not a decision and writes nothing. If the line
cannot be written the decision stands, stderr says so and `--json` carries `"ledger_row": "not recorded: ..."`.

```json
{"kind":"gate","at":"2026-10-10T19:44:12Z","sha":"<sha>","scope":"all","decision":"REFUSED",
 "reasons":["receipt: no receipt exists for abababababab; run `seaworthy run` for this SHA first"],
 "receipt":null,"receipt_unknown":"no receipt qualified; the reasons say why",
 "planned":[{"os":"linux","timeout_min":60},{"os":"windows","timeout_min":120}],
 "budget":{"runs_used":0,"max_runs":2,"runs_left":2,"spent":0.0,"cap":5.0,"cap_left":5.0,"currency":"USD","projected_worst_case":1.56},
 "minutes_not_dispatched_upper":180.0,
 "minutes_basis":"sum of planned[].timeout_min (worst-case timeouts), an upper bound",
 "schema":"seaworthy.ledger","schema_version":"1.0"}
```

| Field | Meaning |
| --- | --- |
| `decision` | `GO`, `GO (OVERRIDDEN)` or `REFUSED`, as printed. A passed gate is `GO`; a blocked one is `REFUSED`. |
| `reasons` | The refusal reasons as printed (at most 8, each cut at 300 characters); empty on `GO`. An override still writes its own `override` line with the full reasons. |
| `receipt` | `{id, at, age_s}` of the receipt that qualified: `id` is its `receipt_hash`, `age_s` is seconds from its `at` to the decision. `null` with `receipt_unknown` when none qualified. |
| `planned` | The runs the decision was about (`--planned`, else `budget.planned`), `null` when neither was given. |
| `budget` | What was left at the decision: `runs_left` (`max_runs` minus runs used) and `cap_left` (`cap` minus spent), each `null` when the profile sets no cap; `projected_worst_case` is the planned runs at timeout times rate. |
| `minutes_not_dispatched_upper` | See below. |

**Minutes not dispatched (upper bound).** For a `REFUSED` row with a plan: the sum of `planned[].timeout_min`. A timeout is a
worst case, so the figure is never more than what the refusal held back and says nothing about what a run would have
used; it is not a saving. A `GO` or `GO (OVERRIDDEN)` row holds nothing back: `0`. With no plan given, or a plan that sums
to 0, the value is `null` and `minutes_basis` says why. It uses only the plan and the profile: no receipt or run field is
needed and nothing is guessed. A repeated gate at the same SHA writes a row each time, so a reader that totals the minutes
counts each SHA once (the largest row), not once per row; a refusal that stood is one with no `override` line and no later
successful `run` row at the same SHA.

## Overrides

`--override NAME --reason TEXT` turns a refusal into a go, prints what was overridden and appends an `override` line to
the ledger with the name, reason and the refusals it covered. Name who may override in `receipt.override_allowed_by`; with
no list, anyone who gives a name can. An override is a decision by a person about money: keep it rare and read the ledger.

## Where the receipt can be enforced on a forge

The simplest enforcement is the wrapper above. Heavier options are native to GitHub: post the verdict as a commit status
from one fixed source and require that status, pinned to its expected source, on the release workflow; or use a custom
deployment protection rule on the release environment. This version does not post statuses.
