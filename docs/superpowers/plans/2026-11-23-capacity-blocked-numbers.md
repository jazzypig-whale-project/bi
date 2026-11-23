# Capacity Cards: Blocked Numbers and Archived Accounts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make "Quota headroom over time" and both "Capacity now" scalars stop counting accounts on operator-blocked numbers and archived accounts.

**Architecture:** SQL-only edits to three existing YAML cards, managed by `mbc`. A shared `number_block` CTE (same text in all three cards) maps each phone-side open-ended block to the number the account held at block time via `account_number_tenure`. By-hour zeroes blocked/archived accounts per spine hour; Capacity now drops them from `placeable`. No Python changes.

**Tech Stack:** Metabase-as-code YAML (`mbc`), PostgreSQL native SQL, database 2.

**Spec:** `docs/superpowers/specs/2026-11-23-capacity-blocked-numbers-design.md`

## Global Constraints

- Blocking reasons, exactly: `c.until IS NULL AND c.reason IN ('BLOCKED_BY_OPERATOR', 'NO_PAYMENT_METHOD_AVAILABLE')`. Other reasons unchanged.
- Number at block time: `account_number_tenure` row of the account covering `applied_at` (`valid_from <= t AND (valid_to IS NULL OR valid_to > t)`). No match (blocks older than V130 seed) → block the account only.
- Blocked number = 0 for **every** account on it, while `applied_at <= t < cleared_at` (`cleared_at IS NULL` = still blocked). Holds even if the triggering account was archived later.
- Archived account = 0 from `archived_at` onward.
- Applies to all three by-hour series (including "Quota") and both Capacity now cards.
- Column types: `account_cooldown.applied_at/cleared_at/until` = naive UTC `timestamp`; `account_number_tenure.valid_from/valid_to` and `account.archived_at` = `timestamptz`; by-hour spine `h` = naive Europe/Moscow. Every comparison converts explicitly (`mbcode/lint_timezone.py`; bare `now()` is rejected).
- Read-only against prod (role `metabase`), must stay well under `statement_timeout=30s`.
- `./mbc apply` only on the owner's explicit go. Commit/push only with the owner's permission.

## Review Focus

- A past phone swap: the block must land on the old number only; the new number's accounts must stay non-zero after the swap (tenure join, not `current_number_id`).
- Block cleared mid-window: the number must count 0 before `cleared_at` and normal after it, at the hour boundary.
- Pre-V130 block with no tenure match: the blocked account itself must still be 0 in all three series (including "Quota"), siblings unaffected.
- Series ordering Quota ≥ Quota + cooldown ≥ Quota + cooldown + balance must still hold at every hour (the new zeroing must hit all three identically).
- Timezone: a block applied at 23:30 UTC must zero the Moscow hour `02:00` next day, not `23:00`.

Each line is checked in Task 1 Step 3 with a targeted live query; the ordering line also in Task 1 Step 4.

---

### Task 1: By-hour card

**Files:**
- Modify: `cards/pca-capacity-by-hour.yaml` (SQL + `description`)

**Interfaces:**
- Produces: the `number_block` CTE text (columns `number_id uuid, applied_at timestamp, cleared_at timestamp`, all naive UTC) — Task 2 pastes it verbatim.

- [ ] **Step 1: Add CTEs** after `cooldown`:

```sql
-- Operator block on the SIM: an open-ended phone-side block on any account blocks the number that
-- account held when the block was applied (account_number_tenure), for every account on it.
-- Blocks older than the tenure journal (V130 seed) match no row: see account_block below.
number_block AS (
  SELECT t.number_id, c.applied_at, c.cleared_at
  FROM account_cooldown c
  JOIN account_number_tenure t
    ON t.account_id = c.account_id
   AND t.valid_from <= (c.applied_at AT TIME ZONE 'UTC')
   AND (t.valid_to IS NULL OR t.valid_to > (c.applied_at AT TIME ZONE 'UTC'))
  WHERE c.until IS NULL
    AND c.reason IN ('BLOCKED_BY_OPERATOR', 'NO_PAYMENT_METHOD_AVAILABLE')),
```

  plus `excluded (h, account_id)` = `GROUP BY 1, 2` union over `spine × subject` of:
  - number blocked: `number_block nb` with `nb.number_id = sub.number_id` and the hour test identical to the `cooldown` CTE's (`applied_at <= h_utc AND (cleared_at IS NULL OR cleared_at > h_utc)`, `h_utc = s.h AT TIME ZONE 'Europe/Moscow' AT TIME ZONE 'UTC'`);
  - account blocked (fallback): same reasons/`until IS NULL`/hour test on `account_cooldown` by `account_id`;
  - archived: `acc.archived_at <= (s.h AT TIME ZONE 'Europe/Moscow')` (needs `archived_at` in `subject`).

- [ ] **Step 2: Zero excluded accounts** — in `account_headroom`, `LEFT JOIN excluded ex ON ex.h = s.h AND ex.account_id = sub.account_id`; both `tasks_possible` and `tasks_possible_nocd` become `0` when `ex.account_id IS NOT NULL` (guard first, before the cooldown case). Extend `subject` to select `acc.archived_at`; do not filter archived there.

- [ ] **Step 3: Live checks (read-only)** — run the SQL via `POST /api/dataset` (database 2, tags substituted). For each Review Focus line pick a real account/number from `account_cooldown` / `account_number_tenure` (e.g. a blocked account whose tenure later moved, a block with `cleared_at` inside the window, a block with no tenure match) and confirm with a scoped variant of the query (filter `subject` to that number's accounts) that the hours zeroed are exactly the expected ones. Record the ids used in the commit message body.

- [ ] **Step 4: Regression checks** — for (7, `1 day`), (14, `1 hour`): every (hour, series) value ≤ the old card's value (run old SQL from `git show HEAD:cards/pca-capacity-by-hour.yaml`); ordering Quota ≥ +cooldown ≥ +balance at every hour; 14/`1 hour` runs < 15 s.

- [ ] **Step 5: Update `description`** — replace "Sums remaining quota across every account with validation_status currently GOOD that is attached to a device" with the same plus: accounts on an operator-blocked number (any account on that SIM hit BLOCKED_BY_OPERATOR or NO_PAYMENT_METHOD_AVAILABLE, open-ended, until cleared) and archived accounts count as 0 in all three series, hour by hour from their real timestamps. Keep the rest.

- [ ] **Step 6: Validate** — `make mbc-validate` (no errors, timezone lint passes), `make mbc-test` (all pass).

### Task 2: Capacity now cards

**Files:**
- Modify: `cards/pca-capacity-now-quota.yaml`, `cards/pca-capacity-now-quota-balance.yaml` (SQL + `description`)

**Interfaces:**
- Consumes: `number_block` CTE from Task 1, verbatim (including its comment).

- [ ] **Step 1: Add `number_block`** as the first CTE after `limits` in both cards.
- [ ] **Step 2: Extend `placeable` WHERE** in both cards:

```sql
  AND acc.archived_at IS NULL
  AND NOT EXISTS (SELECT 1 FROM number_block nb
                  WHERE nb.number_id = acc.current_number_id
                    AND nb.cleared_at IS NULL)
```

  (Account-only fallback is already covered by the existing open-cooldown `NOT EXISTS`.)

- [ ] **Step 3: Update both `description`s** — add to the gate list: account not archived, and its number not operator-blocked (no open BLOCKED_BY_OPERATOR / NO_PAYMENT_METHOD_AVAILABLE block on any account that held it).
- [ ] **Step 4: Live checks** — both run without error; new ≤ old; `quota-balance` ≤ `quota`; each < 5 s.
- [ ] **Step 5: Validate** — `make mbc-validate`, `make mbc-test`.

### Task 3: Ship

- [ ] **Step 1: Diff** — `make mbc-diff`: exactly the three cards changed (SQL + description), nothing else.
- [ ] **Step 2: Commit** (owner's permission required)

```bash
git add cards/pca-capacity-by-hour.yaml cards/pca-capacity-now-quota.yaml cards/pca-capacity-now-quota-balance.yaml docs/superpowers/specs/2026-11-23-capacity-blocked-numbers-design.md docs/superpowers/plans/2026-11-23-capacity-blocked-numbers.md
git commit --message "feat(cards): exclude operator-blocked numbers and archived accounts from capacity"
```

- [ ] **Step 3: Apply** (owner's go) — `./mbc apply`, then commit the updated `.state/<host>.yaml` if it changed.
