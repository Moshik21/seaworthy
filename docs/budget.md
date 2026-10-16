# The budget ledger

Hosted CI minutes are money. The ledger is this tool's own record of the hosted runs you have started, kept in the
state directory as `ledger.jsonl`: one JSON line per event, append-only, written in one `write` call so concurrent
writers cannot interleave a line.

## Recording runs

```
seaworthy ledger add PROFILE --run-id 34370049456 --sha SHA --workflow release --os windows \
    --outcome started --timeout-min 180 --budget-id v1.4.0
seaworthy ledger add PROFILE --run-id 34370049456 --sha SHA --workflow release --os windows \
    --outcome success --minutes 41 --budget-id v1.4.0 --image windows-2022=20260908.1.0
```

A run is written when it starts and again when it ends; the **latest line per run id wins**. A run that has not ended is
counted at its worst case (`--timeout-min` times its rate), so a hung run cannot hide. Outcomes: `started`, `success`,
`failed`, `cancelled`. For a failure, `--failure-class` is `deterministic` (the default; the same SHA fails again) or
`infrastructure` (the runner failed). `--failure-text FILE` matches a saved log against the profile's
`deterministic_failures` and records which known class it was. `--image` on a success records the runner image as the
last good one.

Every `seaworthy gate` decision also appends a `gate` line to this ledger (decision, receipt id and age, budget left, and
the minutes not dispatched as an upper bound): see [receipt.md](receipt.md#the-gate-row). `runs()` and the budget totals
read only `run` lines, so gate lines never change a budget.

`ledger show` lists the runs and overrides in the window; `ledger summary` prints the totals; `--json` for machines.

## The numbers

Cost is **estimated, not billed**: minutes rounded up times a per-OS rate from `budget.rates` (defaults: Linux 0.006,
Windows 0.010, macOS 0.062 USD per minute, the standard hosted rates when this was written; check your plan, and a larger
runner costs more). Re-runs are billed in full.

| `budget` field | Meaning |
| --- | --- |
| `currency`, `cap` | label and total allowed over the window |
| `max_runs` | most hosted runs allowed |
| `window` | `all`, `calendar-month` or `since:YYYY-MM-DD`; runs outside it are ignored |
| `budget_id` | count only runs recorded with this id (for both runs and money) |
| `runs_budget_id` | count only these runs against `max_runs` while money still sums the whole window (a per-release run cap with a shared dollar budget) |
| `carried_spend` | money already spent before the ledger began (from a release log); added to the total |
| `rates` | USD per minute by OS name |
| `planned` | the runs you intend: `{name, os, timeout_min}`; each counts as one run at timeout times rate |

`budget.hosted_spend` fails if used runs plus planned runs exceed `max_runs`, or spent plus the planned worst case exceeds
`cap`; it warns at `warn_at` (default 0.8) of the cap. Set `usage_api` to `advisory` or `required` and `usage_user` to also
read the billing usage endpoint (a GET) and take the larger of the two spends; a 403 or 404 there is `unknown`
(blocking only under `required`), never zero.

## Limits

The ledger only knows what you tell it. If a run is started outside the wrapper, record it, or the budget is wrong in
your favour. The cross-check with the usage endpoint exists to catch that, where the token allows it.
