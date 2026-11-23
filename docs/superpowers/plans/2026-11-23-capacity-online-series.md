# Quota Headroom "+ online" Series Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fourth series "Quota + cooldown + balance + online (upper bound)" to the "Quota headroom over time (24h rotation ceiling)" card.

**Architecture:** SQL-only edit to one existing YAML card, managed by `mbc`. `subject` gains an `is_online` flag; the tasks sum is computed a second time filtered to online accounts, capped by the same number day/month limits, then by balance. The existing three series stay unchanged.

**Tech Stack:** Metabase-as-code YAML (`mbc`), PostgreSQL native SQL, database 2.

**Spec:** `docs/superpowers/specs/2026-11-23-capacity-online-series-design.md`

## Global Constraints

- Online = `d.state NOT IN ('OFFLINE', 'DISABLED') AND a.status = 'ONLINE' AND a.disabled_at IS NULL` (device of the account, its agent). Current state only, applied to every past hour.
- Series key exactly `Quota + cooldown + balance + online (upper bound)`; `series_settings` title `Quota + cooldown + balance + online`, color `#EF8C8C`.
- Existing three series values must not change.
- Read-only against prod (role `metabase`), well under `statement_timeout=30s`. No bare `now()` (`mbcode/lint_timezone.py`); the online filter uses no timestamps.
- `./mbc apply` only on the owner's explicit go. Commit/push only with the owner's permission.

## Review Focus

- Number shared by online and offline accounts: number day/month caps and balance must apply once per number to the online subset only, not per account (aggregate per `(h, number_id)` before capping, same as existing series).
- Ordering: the new series must be ≤ "Quota + cooldown + balance" at every hour (online is a subset; same caps).
- Device with `agent_id` whose agent row is disabled but device IDLE: must count as offline.
- Account without a matching device row (`device_id` dangling): must count as offline (`EXISTS`/LEFT JOIN yields false, not NULL leaking into `FILTER`).
- Days/Granularity filters still work: 14-day / `1 hour` run stays under 30 s.

---

### Task 1: Fourth series on `pca-capacity-by-hour`

**Files:**
- Modify: `cards/pca-capacity-by-hour.yaml` (description; CTEs `subject`, `account_headroom`, `account_headroom_agg`, `per_number`, `capped`; final `UNION ALL`; `series_settings`)
- Test (scratch, not committed): live-check script running the card SQL through Metabase `/api/dataset`, database 2

**Interfaces:**
- Consumes: nothing.
- Produces: series key `Quota + cooldown + balance + online (upper bound)` in column `series`.

- [ ] **Step 1: Write the live check** — a scratch script that loads the card YAML, substitutes `{{granularity}}` = `'1 hour'`, `{{days}}` = `14`, runs it via `/api/dataset` (proxy `METABASE_PROXY`, credentials from `.env`) and asserts:
  - 4 distinct `series` values, the new key among them;
  - for every `hour`: `online ≤ Quota + cooldown + balance ≤ Quota + cooldown ≤ Quota`;
  - at the latest hour the three old series equal a baseline captured from the unmodified card in the same run window (run baseline first, save to file);
  - elapsed < 30 s.

- [ ] **Step 2: Run against the unmodified card** — saves baseline; Expected: FAIL on "4 distinct series".

- [ ] **Step 3: Edit the SQL**
  - `subject`: add `EXISTS (SELECT 1 FROM device d JOIN agent a ON a.agent_id = d.agent_id WHERE d.device_id = acc.device_id AND d.state NOT IN ('OFFLINE','DISABLED') AND a.status = 'ONLINE' AND a.disabled_at IS NULL) AS is_online`.
  - `account_headroom`: carry `sub.is_online`.
  - `account_headroom_agg`: add `sum(tasks_possible) FILTER (WHERE is_online) AS tasks_from_accounts_online` (COALESCE to 0).
  - `per_number`: pass it through.
  - `capped`: `quota_capacity_online` = same `greatest(0, least(…))` as `quota_capacity` with `tasks_from_accounts_online`.
  - Final: new `UNION ALL` branch `'Quota + cooldown + balance + online (upper bound)'`, `sum(least(quota_capacity_online, balance_capacity))::bigint`.
  - `series_settings`: add the entry per Global Constraints.
  - `description`: add one sentence — the online series counts only accounts whose device is not OFFLINE/DISABLED and whose agent is ONLINE and not disabled; that state is not historised, so today's online set is applied to every past hour. Extend the ordering sentence: Quota + cooldown + balance is at least Quota + cooldown + balance + online.

- [ ] **Step 4: Run the live check** — Expected: PASS; latest-hour online ≈ 1 (live on 2026-11-23 19:00 MSK), old series equal baseline.

- [ ] **Step 5: Offline checks**

Run: `./mbc validate && .venv/bin/pytest`
Expected: validate OK, all tests pass (timezone lint included).

- [ ] **Step 6: Drift check**

Run: `METABASE_PROXY=http://localhost:2080 ./mbc diff`
Expected: exit 2 with a diff limited to card `pca-capacity-by-hour` (query, description, visualization_settings).

- [ ] **Step 7: Stop for owner** — report results; `./mbc apply` and commit (`feat(cards): add online series to quota headroom chart`, plus spec and plan files) only on the owner's explicit go.
