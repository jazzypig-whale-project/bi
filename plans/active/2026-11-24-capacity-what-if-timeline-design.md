# Capacity what-if timeline — design

Date: 2026-11-24. Status: **BLOCKED** on orchestrator feature
`orchestrator/tasks/2026-11-24-fleet-state-history` (state history journals). Design approved in
outline by the owner on 2026-11-24; final SQL depends on the shape of those journals.
Replaces: card `pca-capacity-by-hour` ("Quota headroom over time (24h rotation ceiling)").

## Intent

The owner wants a chart that predicts how many certificates the system could produce in the next
24 hours under a ladder of "what if" assumptions — if there were no cooldowns, if every phone were
topped up, if every phone were online — and wants to see how those values move over time, so that
changes on our side (fleet, retire/drying, validation, holds) or on partners' side (operator blocks,
provider deposits, cooldown waves) show up as visible steps in the lines.

This is the third attempt. Why the previous two failed (verified 2026-11-24 against prod):

1. **Stock vs flow.** The current card measures leftover quota/money at one instant. Production is a
   flow: top-ups refill money and quota windows roll during the day. Result for the last 7 days:
   the card's "balance + online" line (360–780/day) sat *below* real output (1,800–3,900/day), while
   the "Quota" line (~6,500) sat 2.5× above it. Top-ups credited ~26.2M over the same 7 days
   (`balance_ledger.source = 'PROVIDER_TOPUP'`), ≈ 2,500 certs/day at a ~1,500 average amount, which is
   about real output. Money is the real pace-setter, and it is a flow.
2. **Fake history.** Device state, agent online, device/account validation, holds and retire mode are
   stored in the app DB as current values only. The current card applies today's values to every
   past hour, so the past points do not reflect what the system looked like then.
3. **Missed placement gates** (`orchestrator/.../placement/PlacementRepository.java`
   `findPlaceableCapacity` / `lockIdleCandidates`): device `validation_status = 'VALIDATED'`
   (`DeviceValidationStatus.SQL_DEVICE_PRODUCTION_GATE`), device hold (`held_at`), mode-aware money
   (`PhoneProviderRepository.SQL_AVAILABLE_MONEY`: DEPOSIT numbers spend the provider's pooled
   `deposit_balance`, everyone subtracts open RESERVED reservations), retire/drying state
   (`number.retire_mode`), and money divided by `min_certificate_amount` 1,000 while the real average
   order amount is ~1,500–1,700.

## Goals

- An honest timeline of the what-if ladder: every past point computed from the state the system
  actually had at that time.
- Lines computed at read time from raw history, so the formula can be revised and all history
  recomputes. No totals are stored.
- A "Real output (previous 24h)" line on the same chart as the reality check.

## Non-goals

- Forecasting trends forward in time ("predict" = the next 24h at that hour's state).
- Any bi-side snapshot service or separate snapshot database. Considered and dropped, see Approaches.
- Alerting. Touching the "Capacity now" scalars.
- The orchestrator work itself. That lives in `orchestrator/tasks/2026-11-24-fleet-state-history`.

## Anti-goals

- Anything in bi that writes to the app DB `orchestrator`. bi only reads, via role `metabase`.
- A bottom line that lands below real output with no stated reason.
- Totals frozen at capture time.

## Constraints

- App DB read only via the read-only role `metabase` (statement_timeout 30s, lock_timeout 15s,
  CONNECTION LIMIT 20). The card SQL must finish well inside 30s for the dashboard's max range
  (14 days, hourly).
- History exists only from the day the orchestrator journals ship (plus whatever their seed gives).
  Points before that are not shown.
- Quota limits live in app config (`orchestrator/src/main/resources/application.yml`, `app.quota`),
  not in the DB: `maxTasksPerAccount` 6, `dayLimitAccount` 35,000, `dayLimitNumber` 500,000,
  `monthLimitNumber` 600,000, `minCertificateAmount` 1,000. They are hard-coded in the card with a
  source comment, as today.

## Approaches considered

1. **Hourly raw per-account snapshot in a bi-side `capacity` DB** (a job in the bi compose copying
   app state hourly). Chosen first, then dropped by the owner: it is a workaround. It samples state
   hourly, duplicates app logic in a bi script, and adds a service, a DB and secrets to the bi stack.
2. **App writes an hourly snapshot table into its own DB.** Simple reads, but puts a BI-only table and
   a retention job into the prod DB, and the data is still hourly samples. Not chosen.
3. **State history journals in orchestrator — CHOSEN.** The app journals every change of the
   current-value-only columns as validity intervals (V130 tenure-journal precedent). Together with the
   already-historised ledgers, any past hour can be reconstructed exactly. The bi side is one card.
   Trade-off: heavier read SQL. If it cannot meet the 30s timeout, a pre-aggregation may be needed;
   that is decided in the orchestrator design, since that is where the read shape is settled.

## Design (bi side)

### Inputs (all in the app DB, read via `metabase`)

- Already there: `quota_ledger` (windows, statuses `RESERVED`,`COMMITTED`), `account_cooldown`,
  `balance_ledger` (money stock via `balance_after`; top-up flow via `source = 'PROVIDER_TOPUP'`),
  `account_number_tenure`, `number_provider_tenure`, `task` (real output and average amount).
- From the blocking feature: history of device `state` / `validation_status` / `held_at`, agent
  `status` / `disabled_at`, account `validation_status` / `archived_at` / `device_id`, number
  `retire_mode`, phone_provider `mode` / `deposit_balance`.

### The lines (per spine hour h, computed in the card)

Semantics of every line: "certificates the fleet could produce in the 24h after h if the conditions
at h held for 24h". Amount per certificate = mean `task.amount` of SUCCESS tasks in the 7 days
before h.

Base set (all lines): accounts attached to a device at h, with a current number at h, account
`GOOD` and not archived at h, not on an operator-blocked number at h (open-ended
`BLOCKED_BY_OPERATOR` / `NO_PAYMENT_METHOD_AVAILABLE`, same logic as the current card's
`number_block` / `excluded`).

- Per-account daily cap = `least(max_tasks_per_account, floor(day_limit_account / avg_amount))`.
- Per-number daily cap = `floor(least(day_limit_number, month_limit_number - number_money30d(h)) / avg_amount)`.
- A number's contribution = `least(per-number cap, sum of its accounts' caps)`.

1. **Ceiling (quota only)** — base set.
2. **+ cooldowns** — drop accounts with an open cooldown at h.
3. **+ money (flow)** — cap each number by
   `floor((money_available(h) + topups_credited(h-24h, h)) / avg_amount)`. Money available is
   mode-aware: for DEPOSIT numbers the provider's pooled deposit (capped per provider, not per number),
   minus open RESERVED reservations at h. Owner agreed 2026-11-24 that top-ups count as a flow.
4. **+ fleet ready** — keep only accounts whose device at h is not `OFFLINE`/`DISABLED`, is
   `VALIDATED`, not held, and whose agent at h is `ONLINE` and not disabled.
5. **Real output (previous 24h)** — SUCCESS tasks with `succeeded_at` in (h-24h, h], plotted as a
   distinct line.

Ordering 1 ≥ 2 ≥ 3 ≥ 4 holds by construction (each step only removes accounts or adds a cap).

Retiring/drying SIMs: included in all lines, matching placement (which still places on them and
boosts them via `PlacementRepository.sqlDrainBoostTerm`). Line 3 limits them, because they receive no
top-ups. **(Builder's call.)**

### Acceptance (agreed with the owner 2026-11-24)

Line 4 (bottom) ≥ real output on every day of the first 7 days with history. The gap between them is
unused capacity (demand). If line 4 falls below real output, the device rotation limit (one active
account per device; real rotation ~2.4 accounts per device per day vs the ceiling's assumption of
all 4) moves into scope as an extra cap.

### Card

New card on the Pear Cider (automated) dashboard via `mbc`, in the slot of `pca-capacity-by-hour`.
Keeps the Days (max 14) and Granularity filters. Its description states each line's semantics and the
date history starts. Archive `pca-capacity-by-hour` once the new card has ≥ 7 days of history and has
passed acceptance.

## Left to the builder

- Card SQL structure. Reuse the current card's timeline/running-window technique for quota and
  balance, and interval joins against the journals for state at h.
- Series colors consistent with the current card.
- Tests: card SQL against a fixture schema including a DEPOSIT provider, a drying SIM, a device that
  went OFFLINE mid-range, and a cooldown that cleared mid-range.

## Unblocks when

`orchestrator/tasks/2026-11-24-fleet-state-history` is merged and deployed to prod, and the journal
tables are readable by `metabase`. Then revisit this design's "Inputs" against the journals' actual
shape before planning.
