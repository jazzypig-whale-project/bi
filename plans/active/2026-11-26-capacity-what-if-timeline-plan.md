# Capacity what-if timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace card `pca-capacity-by-hour` with a what-if capacity timeline whose every past point is computed from the state the fleet actually had at that hour.

**Architecture:** One native-SQL Metabase card, managed as YAML via `mbc`. Fleet state at each spine hour comes from interval joins against the orchestrator's tenure journals; quota, cooldowns and money come from the existing ledgers. The card is tested offline by running its SQL in a throwaway `postgres:16` container against a minimal copy of the app schema, with scenario rows placed relative to spine hours.

**Tech Stack:** PostgreSQL 16 SQL (Metabase native question), YAML (`mbc`), pytest + `docker` CLI (no new Python dependency).

**Spec:** `plans/active/2026-11-24-capacity-what-if-timeline-design.md` (revised 2026-11-26). Read it first. The plan does not repeat the line definitions.

## Global Constraints

- App DB is read only, via role `metabase`: statement_timeout 30s. The card at Days = 14, Granularity = `1 hour` must finish well inside that (target ≤ 10s).
- No orchestrator change and no write to `orchestrator` of any kind.
- Quota limits are hard-coded in a `limits` CTE with a source comment: `max_tasks_per_account` 6, `day_limit_account` 35000, `day_limit_number` 500000, `month_limit_number` 600000, `min_certificate_amount` 1000 (`orchestrator/src/main/resources/application.yml`, `app.quota`).
- Timestamp types, verbatim from the orchestrator migrations: `quota_ledger.ts`, `account_cooldown.applied_at/until/cleared_at` and `task.succeeded_at` are naive `timestamp` in UTC; the tenure journals, `balance_ledger.created_at` and `phone_provider_deposit_ledger.created_at` are `timestamptz`. The spine is naive Europe/Moscow, as in every Pear Cider card. Convert with `h AT TIME ZONE 'Europe/Moscow'` (to timestamptz) and `h AT TIME ZONE 'Europe/Moscow' AT TIME ZONE 'UTC'` (to naive UTC).
- Point-in-time predicate for every tenure journal: `valid_from <= :at AND (valid_to IS NULL OR valid_to > :at)`.
- History start = `min(valid_from) FROM device_state_tenure WHERE source = 'SEED'` (prod: 2026-11-26 07:45:34 UTC). It is data-driven, not a literal, so tests can move it. Spine = `date_bin(granularity, greatest(now - least(days, 14) days, history start), TIMESTAMP '2000-01-03')` to now, stepping by granularity. If the bin of the start falls before history start, the spine starts at the next bin.
- Card key `pca-capacity-what-if-by-hour`. Output columns `hour`, `series`, `capacity`, same as the old card. Series values and colors:
  - `Ceiling (quota)` `#88BF4D`
  - `+ cooldowns` `#509EE3`
  - `+ money (24h flow)` `#7172AD`
  - `+ fleet ready` `#EF8C8C`
  - `Real output (previous 24h)` `#4C5773`
- `avg_amount(h)` = mean `task.amount` of `status = 'SUCCESS'` tasks with `succeeded_at` in [h-7d, h). If there are none, it falls back to `min_certificate_amount`.
- Number with no `number_provider_tenure` row at h: DEFAULT-mode money (the ELSE branch of `PhoneProviderRepository.SQL_AVAILABLE_MONEY`).
- Commit only with the owner's explicit permission in the same message. `./mbc apply` changes the live Metabase: run it only when the owner says so.

## Review Focus

1. A journal row whose `valid_to` equals a spine hour exactly: the row is **not** in effect at that hour (exclusive end). Test in Task 2.
2. An account whose device has no `device_state_tenure` row at h: counts in lines 1–3, not in line 4. Test in Task 5.
3. A negative or zero money stock (`balance_after` < 0, deposit overdrawn by `WRITE_OFF`): the money cap is floored at 0, never negative. Test in Task 4.
4. No SUCCESS task in the 7 days before h: `avg_amount` falls back to 1000, no NULL or division error. Test in Task 2.
5. Days = 30 or Granularity = `1 day`: Days capped at 14, spine never earlier than history start. Test in Task 2.

---

### Task 1: SQL test harness

**Files:**
- Create: `tests/sql/__init__.py` (empty)
- Create: `tests/sql/conftest.py`
- Create: `tests/sql/schema.sql`
- Create: `tests/sql/test_harness.py`
- Modify: `pytest.ini` (register marker `sql`)

**Interfaces:**
- Produces (fixtures in `tests/sql/conftest.py`):
  - `pg` (session scope): a running `postgres:16` container. Started with `docker run --rm --detach --env POSTGRES_PASSWORD=test postgres:16`, waited on with `docker exec <id> pg_isready`, and removed with `docker rm --force` at teardown. If `docker` is not on PATH, every `sql`-marked test is skipped.
  - `db(pg)` (function scope): resets `public` (`DROP SCHEMA public CASCADE; CREATE SCHEMA public`), loads `schema.sql`, and returns an object with:
    - `db.exec(sql: str) -> None`: runs through `docker exec -i <id> psql -U postgres -v ON_ERROR_STOP=1` on stdin, raises on non-zero exit.
    - `db.rows(sql: str) -> list[tuple[str, ...]]`: same, with `--csv --tuples-only`, parsed by `csv.reader`.
  - `card_sql(card_key: str, days: int, granularity: str) -> str` (plain function): loads `cards/<key>.yaml` and returns `dataset_query.stages[0].native` with `{{days}}` replaced by the integer and `{{granularity}}` by a quoted literal (`'1 hour'`), as Metabase substitutes them.
- Produces (`schema.sql`): the tables the card reads, with **only** the columns it reads, and names and types copied from the orchestrator migrations:
  - `task` (`status`, `amount`, `succeeded_at`)
  - `quota_ledger` (V2)
  - `account_cooldown` (V38 + `reason`)
  - `balance_ledger` (V52)
  - `phone_provider` (`code`, `mode`)
  - `phone_provider_deposit_ledger` (V98)
  - `account_number_tenure`, `number_provider_tenure` (V130)
  - `device_state_tenure`, `account_state_tenure` (V153, including the indexes)

  No FKs. It also defines the scenario helpers:
  - `spine_h(k int) RETURNS timestamptz`: the start of the hourly Moscow bin k hours before the current one.
  - `spine_h_utc(k int) RETURNS timestamp`: the same instant as naive UTC.

- [ ] **Step 1: Write the failing tests** in `tests/sql/test_harness.py`, marked `@pytest.mark.sql`:
  - `test_schema_loads`: `db.rows("SELECT count(*) FROM device_state_tenure")` == `[("0",)]`.
  - `test_spine_h_is_whole_moscow_hour`: `spine_h(0)` truncated to the hour in `Europe/Moscow` equals itself, and `spine_h(0) - spine_h(1)` == `1 hour`.
  - `test_card_sql_substitutes_tags`: the result of `card_sql("pca-capacity-by-hour", 3, "1 hour")` contains no `{{` and contains `'1 hour'::text::interval`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/sql -m sql -v`. Expected: FAIL (fixtures missing).
- [ ] **Step 3: Implement** `conftest.py`, `schema.sql` and the `sql` marker in `pytest.ini`.
- [ ] **Step 4: Run** `.venv/bin/pytest tests/sql -m sql -v`. Expected: 3 passed. Then run `.venv/bin/pytest`. Expected: the whole existing suite still passes.
- [ ] **Step 5: Commit** (with owner permission) `test(cards): throwaway-postgres harness for card SQL`.

### Task 2: Card skeleton — spine, base set, Ceiling, Real output

**Files:**
- Create: `cards/pca-capacity-what-if-by-hour.yaml`
- Create: `tests/sql/test_capacity_what_if.py`

**Interfaces:**
- Consumes: Task 1 fixtures.
- Produces: the card YAML, built like `cards/pca-capacity-by-hour.yaml`: same collection, `display: line`, `cache_ttl: 300`, template tags `days` (default `'7'`) and `granularity` (default `'1 day'`), visualization settings with the five series from Global Constraints. The tag ids are new UUIDs, not copied from the old card. CTE names that later tasks extend:
  - `limits`, `history`, `spine`
  - `avg_amount(h, amount)`
  - `account_at(h, account_id, device_id, number_id)` — the base set
  - `account_cap(h, account_id, number_id, cap)`
  - `number_cap(h, number_id, cap)`
  - `line1(h, number_id, capacity)`
  - `real_output(h, capacity)`

  The final `SELECT … UNION ALL … ORDER BY 1, 2` emits one row per (hour, series).
- Test helper in `test_capacity_what_if.py`: `value(rows, series: str, k: int) -> int`. It returns `capacity` for the spine hour `spine_h(k)` (match `hour` against `db.rows("SELECT spine_h(k) AT TIME ZONE 'Europe/Moscow'")`).
- Scenario builder in the same file: `seed_fleet(db, *, accounts: int = 1, …)`. It inserts a SEED history start 48h back (`device_state_tenure` row, `valid_from = spine_h(48)`), one ready device, N GOOD accounts on one DEFAULT number, and no tasks. Later tasks add keyword arguments instead of writing their own inserts.

- [ ] **Step 1: Write the failing tests:**
  - `test_ceiling_one_account`: 1 account, no tasks, days 2, hourly. `Ceiling (quota)` at k=1 == `least(6, floor(35000/1000))` = 6. This also pins Review Focus 4 (the avg fallback).
  - `test_ceiling_uses_avg_amount`: 5 SUCCESS tasks of amount 7000 in the 7 days before. Ceiling == `least(6, floor(35000/7000))` = 5.
  - `test_number_cap_from_month_window`: `quota_ledger` NUMBER rows sum to 599000 in the last 30d at amount 1000. Ceiling == 1.
  - `test_archived_account_drops_at_archive_hour`: `account_state_tenure` row with `archived_at = spine_h(10)`, opened at `spine_h(10)`. Account counts at k=11, not at k=9.
  - `test_operator_block_drops_number`: an open-ended `BLOCKED_BY_OPERATOR` cooldown on account A applied at `spine_h_utc(5)`. Account B on the same number counts 0 in Ceiling at k=4, full at k=6.
  - `test_valid_to_equal_to_hour_is_exclusive` (Review Focus 1): an account's GOOD row closes at exactly `spine_h(3)` and is followed by a BAD row. It counts at k=4, not at k=3.
  - `test_real_output_previous_24h`: 3 SUCCESS tasks at `spine_h_utc(2)` minus 10 minutes. `Real output (previous 24h)` == 3 at k=0 and k=1, 0 at k=26.
  - `test_spine_starts_at_history_start` (Review Focus 5): history start 48h back, days 30. The earliest hour returned is `spine_h(48)`, and no hour is earlier. Granularity `1 day` returns at most 3 distinct hours.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/sql/test_capacity_what_if.py -m sql -v`. Expected: FAIL (card file missing).
- [ ] **Step 3: Write the card SQL.** Rules the tests do not pin:
  - Copy the `number_block` / `excluded` operator-block logic from the old card, but take the account's number from `account_number_tenure` at h.
  - Compute `avg_amount` with the old card's checkpoint + `RANGE BETWEEN INTERVAL '7 days' PRECEDING` running-window technique, not a per-hour subquery.
  - Write the description: each line's semantics, the history start, and the two approximations (current provider mode applied to the past, no past reservations), in the design's words. Emit only `Ceiling (quota)` and `Real output (previous 24h)` for now.
- [ ] **Step 4: Run** the tests. Expected: 8 passed. Then run `./mbc validate`. Expected: OK.
- [ ] **Step 5: Commit** `feat(cards): capacity what-if timeline skeleton with ceiling and real output`.

### Task 3: Line 2 — cooldowns

**Files:** Modify `cards/pca-capacity-what-if-by-hour.yaml`, `tests/sql/test_capacity_what_if.py`.

**Interfaces:** Consumes `account_cap`. Produces CTE `line2(h, number_id, capacity)` and series `+ cooldowns`.

- [ ] **Step 1: Failing tests:**
  - `test_cooldown_cleared_mid_range`: 2 accounts; account A has an `APPLE_VELOCITY` cooldown applied at `spine_h_utc(8)` and cleared at `spine_h_utc(4)`. `+ cooldowns` == 6 at k=6 and 12 at k=2. Ceiling == 12 at both.
  - `test_cooldown_until_elapsed`: `until = spine_h_utc(5)`, not cleared. Account excluded at k=6, included at k=5.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Cooldown at h = `applied_at <= h_utc AND (until IS NULL OR until > h_utc) AND (cleared_at IS NULL OR cleared_at > h_utc)`, as the old card.
- [ ] **Step 4: Run.** Expected: all pass.
- [ ] **Step 5: Commit** `feat(cards): cooldown line on capacity what-if timeline`.

### Task 4: Line 3 — money flow, mode-aware

**Files:** Modify the card and the test file.

**Interfaces:** Consumes `line2`. Produces:
- `number_money(h, number_id, money)` for DEFAULT numbers: `balance_after` forward-filled to h + `PROVIDER_TOPUP` `delta` over (h-24h, h].
- `provider_money(h, provider_code, money)` for DEPOSIT providers: the running signed sum of `phone_provider_deposit_ledger.amount` up to h + `kind = 'DEPOSIT'` amounts over (h-24h, h].
- `line3(h, capacity)` and the series `+ money (24h flow)`.

- [ ] **Step 1: Failing tests:**
  - `test_default_number_capped_by_balance_plus_topups`: `balance_after` 1000 at `spine_h(30)`, a top-up `delta` 2000 (`balance_after` 3000) at `spine_h(2)`, avg 1000, 1 account (cap 6). Line 3 == 1 at k=3 and 5 at k=1 (stock 3000 + 24h flow 2000).
  - `test_drying_sim_limited_by_stock`: the number has stock 1000 and no top-ups. Line 3 == 1. Ceiling == 6.
  - `test_deposit_provider_pooled`: provider `mts` with `mode = 'DEPOSIT'`, 2 numbers with 1 account each (line 2 = 12), deposit ledger `DEPOSIT` +4000 at `spine_h(30)`, `TASK` −1000 at `spine_h(20)`, avg 1000. Line 3 == 3 at k=1: pooled once, not 3 per number.
  - `test_deposit_flow_counts_last_24h_credits`: same as above plus `DEPOSIT` +2000 at `spine_h(2)`. Line 3 == 7 at k=1 (stock 5000 + flow 2000; 2 accounts, so line 2 = 12), 3 at k=3.
  - `test_negative_stock_floors_at_zero` (Review Focus 3): `balance_after = -5000`, no top-ups. Line 3 == 0, and no negative value appears in any row.
  - `test_provider_from_tenure_at_h`: the number moves from `beeline` to `mts` at `spine_h(10)` (`number_provider_tenure`). Before that the DEFAULT balance applies, after it the DEPOSIT pool.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
  - DEFAULT: per number `least(line2 contribution, greatest(0, floor(number_money / avg_amount)))`.
  - DEPOSIT: per provider `least(sum of its numbers' line-2 contributions, greatest(0, floor(provider_money / avg_amount)))`.
  - Mode comes from current `phone_provider.mode`. Provider comes from `number_provider_tenure` at h. Reuse the old card's `balance_grouped` forward-fill technique for `balance_after`.
- [ ] **Step 4: Run.** Expected: all pass.
- [ ] **Step 5: Commit** `feat(cards): mode-aware money flow line on capacity what-if timeline`.

### Task 5: Line 4 — fleet ready

**Files:** Modify the card and the test file.

**Interfaces:** Consumes `account_at` (`device_id`) and the line-3 money caps. Produces `line4(h, capacity)` and the series `+ fleet ready`. The money cap applies to line-4 contributions in the same way as in Task 4 (per number for DEFAULT, pooled per provider for DEPOSIT).

- [ ] **Step 1: Failing tests:**
  - `test_device_offline_mid_range`: the device goes `OFFLINE` from `spine_h(6)` to `spine_h(3)`. Line 4 == 0 at k=4, == line 3 at k=7 and k=2.
  - `test_device_not_validated_or_held`: two scenarios, `validation_status = 'PENDING'` and `held_at` set. Line 4 == 0 in both, line 3 unchanged.
  - `test_agent_offline_or_disabled`: `agent_status = 'OFFLINE'`, and separately `agent_disabled_at` set. Line 4 == 0.
  - `test_device_without_row_not_ready` (Review Focus 2): the account's `device_id` has no `device_state_tenure` row. Lines 1–3 count it, line 4 == 0.
  - `test_device_disabled_state`: `state = 'DISABLED'`. Line 4 == 0.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Ready at h = device row at h with `state NOT IN ('OFFLINE','DISABLED') AND validation_status = 'VALIDATED' AND held_at IS NULL AND agent_status = 'ONLINE' AND agent_disabled_at IS NULL`.
- [ ] **Step 4: Run.** Expected: all pass.
- [ ] **Step 5: Commit** `feat(cards): fleet-ready line on capacity what-if timeline`.

### Task 6: Ordering invariant and timing

**Files:**
- Modify: `tests/sql/test_capacity_what_if.py`
- Create: `tests/sql/volume.sql`
- Create: `tests/sql/test_capacity_what_if_perf.py`
- Modify: `pytest.ini` (marker `sql_perf`)

**Interfaces:** `volume.sql` generates 14 days of prod-like volume relative to `spine_h(0)`:
- 500 devices with 10k `device_state_tenure` rows/day
- 2,000 accounts on 1,000 numbers across `beeline` (DEFAULT) and `mts` (DEPOSIT)
- 4,500 SUCCESS tasks/day, each with matching `quota_ledger` ACCOUNT and NUMBER rows and a `balance_ledger` or `phone_provider_deposit_ledger` row
- 200 cooldowns/day

- [ ] **Step 1: Failing tests:**
  - `test_lines_are_ordered` (in the main file): on the `volume.sql` data at days 3, every hour has Ceiling ≥ `+ cooldowns` ≥ `+ money (24h flow)` ≥ `+ fleet ready`.
  - `test_card_14_days_hourly_under_10s` (`@pytest.mark.sql_perf`): on `volume.sql`, run the card with days 14 and `1 hour` under `\timing`. Elapsed < 10,000 ms.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/sql -m "sql or sql_perf" -v`. Expected: the ordering test passes (it holds by construction). The perf test either passes or shows the real time.
- [ ] **Step 3: If over 10s,** read `EXPLAIN (ANALYZE, BUFFERS)` and fix the slowest node, keeping every test green. A pre-aggregated table is out of scope: if the SQL cannot get under 10s, stop and report to the owner.
- [ ] **Step 4: Run** the full SQL suite. Expected: all pass.
- [ ] **Step 5: Commit** `test(cards): ordering and 14-day timing checks for capacity what-if`.

### Task 7: Put the card on the dashboard

**Files:**
- Modify: `dashboards/pear-cider-by-erjan-solutions-automated.yaml` — the dashcard `dc-capacity-by-hour` (row 42). Point `card:` at `pca-capacity-what-if-by-hour`, keep its size and both `parameter_mappings`. Change the heading `dc-heading-production-capacity` text from `Quota headroom` to `Capacity what-if`.
- `cards/pca-capacity-by-hour.yaml` stays as it is (not archived) until acceptance passes (Task 8).

- [ ] **Step 1: Run** `./mbc validate` and `make mbc-test`. Expected: OK, all pass.
- [ ] **Step 2: Run** `./mbc diff`. Expected: one new card, one changed dashcard and one changed heading, nothing else.
- [ ] **Step 3: Ask the owner** for permission to commit, push and `./mbc apply`. Then apply and commit `.state/<host>.yaml` together with the YAML: `feat(dashboard): capacity what-if timeline replaces quota headroom`.
- [ ] **Step 4: Prod timing check:** open the card on the live dashboard with Days 14, Granularity `1 hour`. Read its `running_time` (Metabase MCP or the query log). Expected: well under 30s. Record the value in `plans/plan.md`.
- [ ] **Step 5: Spot check:** pick one device that went OFFLINE today (`device_state_tenure`, `state = 'OFFLINE'`, `source = 'SNAPSHOT'`). Confirm that line 4 dips in the hours it was offline, by about its accounts' share.

### Task 8: Acceptance (on or after 2026-12-04)

**Files:**
- Modify: `cards/pca-capacity-by-hour.yaml` (`archived: true`)
- Modify: `plans/plan.md`
- Move: the design and this plan to `plans/archive/`.

- [ ] **Step 1:** As `metabase`, for every Moscow day from 2026-11-27 through 2026-12-03, compare line 4 at 00:00 with Real output at the next 00:00. Expected: line 4 ≥ real output on every day. Record the table in `plans/plan.md`.
- [ ] **Step 2: If any day fails,** stop and report to the owner: the device rotation cap moves into scope (design § Acceptance), and it needs an orchestrator task for `active_account_id` history.
- [ ] **Step 3: If all pass:** archive the old card, run `./mbc diff`, then (with owner permission) `./mbc apply`, move the docs to `plans/archive/`, tick the `plan.md` entry, and commit `chore(cards): archive quota headroom card after what-if acceptance`.
