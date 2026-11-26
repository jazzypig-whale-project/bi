# Capacity what-if timeline — design

Date: 2026-11-24, revised 2026-11-26. Status: **APPROVED, ready to plan.** The orchestrator feature
`orchestrator/tasks/2026-11-24-fleet-state-history` (state tenure journals, migration V153) is merged
and live on prod. The revision of 2026-11-26 rebases "Inputs" and "The lines" on the journals that
shipped. The owner approved it the same day.
Replaces: card `pca-capacity-by-hour` ("Quota headroom over time (24h rotation ceiling)").

## Intent

The owner wants a chart that predicts how many certificates the system could produce in the next
24 hours under a ladder of "what if" assumptions: no cooldowns, every phone topped up, every phone
online. The owner wants to see how those values move over time, so that changes on our side (fleet,
retire/drying, validation, holds) or on partners' side (operator blocks, provider deposits, cooldown
waves) show up as visible steps in the lines.

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
- Any orchestrator change. The journals are done; if a missing input turns out to matter, that is a
  new orchestrator task, not part of this work.

## Anti-goals

- Anything in bi that writes to the app DB `orchestrator`. bi only reads, via role `metabase`.
- A bottom line that lands below real output with no stated reason.
- Totals frozen at capture time.

## Constraints

- App DB read only via the read-only role `metabase` (statement_timeout 30s, lock_timeout 15s,
  CONNECTION LIMIT 20). The card SQL must finish well inside 30s for the dashboard's max range
  (14 days, hourly). The orchestrator design left this check to bi (its D13: no pre-aggregated table;
  if the card cannot meet the timeout, the remedy is designed starting in the bi repo).
- History exists only from **2026-11-26 07:45:34 UTC** (the V153 seed, `source = 'SEED'`). Points
  before that are not shown.
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
3. **State history journals in orchestrator — CHOSEN, now shipped.** The app journals the
   current-value-only columns as validity intervals (V130 tenure-journal precedent). Together with the
   already-historised ledgers, any past hour can be reconstructed. The bi side is one card.
   Trade-off: heavier read SQL, checked against the 30s timeout in this work.

## What shipped on the orchestrator side (verified 2026-11-26)

Source: `orchestrator/tasks/2026-11-24-fleet-state-history/design.md` (D1–D24),
`orchestrator/src/main/resources/db/migration/V153__state_tenure_journals.sql`, and prod reads as
`metabase`.

- **`device_state_tenure`**: `device_id`, `state`, `validation_status`, `held_at`, `agent_id`,
  `agent_status`, `agent_disabled_at`, `source`, `origin_id`, `valid_from`, `valid_to`. The agent's
  `status` / `disabled_at` are captured on the device row through `device LEFT JOIN agent` and are
  null when the device has no agent (D4). The card never joins agent history.
- **`account_state_tenure`**: `account_id`, `validation_status`, `archived_at`, `device_id`
  (NOT NULL), `source`, `origin_id`, `valid_from`, `valid_to`.
- Shape as V130: one open row per subject (`valid_to IS NULL`), no FK on the subject id, index
  `(<subject>_id, valid_from DESC)`. Point-in-time predicate:
  `valid_from <= :at AND (valid_to IS NULL OR valid_to > :at)`.
- **Writer semantics (D15):** a reconciler compares live state with the open row every 60 s. Interval
  edges are accurate to one tick; a change reverted within one tick is not recorded; while the job is
  down the last state stays open and is stretched. None of this is visible at an hourly grain.
- **Prod state on 2026-11-26 ~19:08 UTC:** device journal 487 `SEED` + 4,281 `SNAPSHOT` rows in ~11h
  (≈ 9k/day, inside the orchestrator's D12 estimate); account journal 1,923 `SEED` rows, no change
  yet. `metabase` reads both tables (default privileges).
- **Not journalled (orchestrator D1)**, and how this design handles each:
  - number `retire_mode` / `ready_to_retire_at` / `drain_endgame_at`: not needed. Drying SIMs count in
    every line, as placement places on them.
  - `phone_provider.mode`: current value applied to every past hour (see Inputs).
  - `phone_provider.deposit_balance`: reconstructed from `phone_provider_deposit_ledger`.
  - device `busy_backoff_until`, `active_account_id`: not used by any line.

## Design (bi side)

### Inputs (all in the app DB, read via `metabase`)

- **State at h** (interval join, point-in-time predicate above):
  - `account_state_tenure`: account `validation_status`, `archived_at`, `device_id`.
  - `device_state_tenure` on the account's `device_id` at h: `state`, `validation_status`, `held_at`,
    `agent_status`, `agent_disabled_at`.
  - `account_number_tenure`: the account's number at h (replaces `account.current_number_id`).
  - `number_provider_tenure`: the number's provider at h.
- **Ledgers:**
  - `quota_ledger` (statuses `RESERVED`, `COMMITTED`): 24h / 30d windows, as today.
  - `account_cooldown`: open cooldowns and operator blocks at h, as today.
  - `balance_ledger`: DEFAULT-mode number money stock at h via `balance_after`; top-up flow via
    `source = 'PROVIDER_TOPUP'` over (h-24h, h].
  - `phone_provider_deposit_ledger`: DEPOSIT-mode provider stock at h = signed `sum(amount)` of rows
    with `created_at <= h` (the ledger sum equals `phone_provider.deposit_balance`, orchestrator
    `PhoneProviderDepositLedgerRepository.java:18`). Deposit flow = `kind = 'DEPOSIT'` credits over
    (h-24h, h]. Kinds on main: `DEPOSIT`, `TASK`, `WRITE_OFF`.
  - `task`: real output and average amount.
- **Current value applied to the past (stated in the card description):** `phone_provider.mode`.
  Prod today: `beeline` = `DEFAULT`, `mts` = `DEPOSIT`. **(Builder's call, approved 2026-11-26.)**

### The lines (per spine hour h, computed in the card)

Semantics of every line: "certificates the fleet could produce in the 24h after h if the conditions
at h held for 24h". Amount per certificate = mean `task.amount` of SUCCESS tasks in the 7 days
before h.

Base set (all lines): accounts with an open `account_state_tenure` row at h, with a number at h
(`account_number_tenure`), `validation_status = 'GOOD'` and `archived_at` null or after h, not on an
operator-blocked number at h (open-ended `BLOCKED_BY_OPERATOR` / `NO_PAYMENT_METHOD_AVAILABLE`, same
logic as the current card's `number_block` / `excluded`).

- Per-account daily cap = `least(max_tasks_per_account, floor(day_limit_account / avg_amount))`.
- Per-number daily cap = `floor(least(day_limit_number, month_limit_number - number_money30d(h)) / avg_amount)`.
- A number's contribution = `least(per-number cap, sum of its accounts' caps)`.

1. **Ceiling (quota only)** — base set.
2. **+ cooldowns** — drop accounts with an open cooldown at h.
3. **+ money (flow)** — cap each number's contribution by money available at h plus the 24h money
   flow, divided by `avg_amount`:
   - DEFAULT-mode number: `balance_after(h) + PROVIDER_TOPUP credits(h-24h, h]`, per number.
   - DEPOSIT-mode numbers: `deposit_stock(h) + DEPOSIT credits(h-24h, h]`, pooled **per provider**.
     The provider's money is a shared cap over all its numbers' line-2 contributions, not added once
     per number.
   - Open RESERVED reservations are **not** subtracted for past hours: `quota_ledger.status` is a
     current value, so which rows were open at h cannot be reconstructed. Reservations last minutes,
     so the effect is a few certificates. **(Builder's call, approved 2026-11-26.)**
   The owner agreed on 2026-11-24 that top-ups count as a flow.
4. **+ fleet ready** — keep only accounts whose device at h (`device_state_tenure`) is not
   `OFFLINE`/`DISABLED`, has `validation_status = 'VALIDATED'`, `held_at` null, and
   `agent_status = 'ONLINE'` with `agent_disabled_at` null. A device with no open row at h counts as
   not ready.
5. **Real output (previous 24h)** — SUCCESS tasks with `succeeded_at` in (h-24h, h], plotted as a
   distinct line.

Ordering 1 ≥ 2 ≥ 3 ≥ 4 holds by construction: each step only removes accounts or adds a cap. Line 4
keeps line 3's money cap.

Retiring/drying SIMs: included in all lines, matching placement (which still places on them and
boosts them via `PlacementRepository.sqlDrainBoostTerm`). Line 3 limits them, because they receive no
top-ups. **(Builder's call.)**

### Acceptance (agreed with the owner 2026-11-24)

Line 4 (bottom) ≥ real output on every day of the first 7 full days with history: Moscow days
2026-11-27 through 2026-12-03 (history starts 2026-11-26 10:45 MSK). Each day's real output is known
at the next midnight, so the check cannot run before 2026-12-04. The gap between line 4
and real output is unused capacity (demand). If line 4 falls below real output, the device rotation
limit (one active account per device; real rotation ~2.4 accounts per device per day vs the
ceiling's assumption of all 4) moves into scope as an extra cap. Rotation would need device
`active_account_id` history, which is not journalled (orchestrator D1), so that would start with a new
orchestrator task.

Performance: the card at Days = 14 and Granularity = 1 hour finishes well inside 30s as `metabase`.
While history is shorter than 14 days, also time it against synthetic volume (see Left to the
builder).

### Card

New card on the Pear Cider (automated) dashboard via `mbc`, in the slot of `pca-capacity-by-hour`.
It can ship before acceptance: until then it shows only the hours since history started. It keeps the
Days (max 14) and Granularity filters. The spine starts at `greatest(now - days, history start)`.
Its description states each line's semantics, the history start date, and the two approximations
(current provider mode, no past reservations). Archive `pca-capacity-by-hour` once the new card has
≥ 7 days of history and has passed acceptance.

## Left to the builder

- Card SQL structure. Reuse the current card's timeline/running-window technique for quota and
  balance, and interval joins against the journals for state at h.
- Series colors consistent with the current card.
- Tests: card SQL against a fixture schema including a DEPOSIT provider, a drying SIM, a device that
  went OFFLINE mid-range, and a cooldown that cleared mid-range. The repo has no SQL test harness
  today (`tests/` covers `mbc` only), so the plan decides how to run one (for example a throwaway
  Postgres container).
- How to estimate the 14-day timing before 14 days of journal exist.
