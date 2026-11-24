# Top-up Completion Time Over Time Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a p50/p90/p99 completion-time-over-time chart to Pear Cider → Top-ups, and move the existing percentiles table onto `credited_at`.

**Architecture:** One new YAML card under `cards/`, one new dashcard in the dashboard YAML (free slot, no layout shift), and a SQL edit to `cards/pca-topup-completion-time-percentiles.yaml`. Managed by `mbc`; no Python changes.

**Tech Stack:** Metabase-as-code YAML (`mbc`), PostgreSQL native SQL.

**Spec:** `docs/superpowers/specs/2026-11-24-topup-completion-time-trend-design.md`

## Global Constraints

- Database 2 only; read-only SQL (role `metabase`).
- Source rows: `topup_payout` with `status = 'COMPLETED' AND credited_at IS NOT NULL`; duration = `credited_at - created_at` (both `timestamptz`).
- Buckets by `credited_at` in Europe/Moscow, `date_bin(..., TIMESTAMP '2000-01-03')` with a `generate_series` spine (pattern of `cards/pca-topups-created-by-hour.yaml`); `mbcode/lint_timezone.py` must pass.
- All providers combined; no max/min/avg; minutes as the unit.
- `apply` to the live instance only on the owner's explicit go. Commit/push only with owner's permission.

## Review Focus

- Empty bucket (no completions, e.g. night at `1 hour`): `completed = 0`, percentiles NULL — line gap, not 0 min.
- Single completion in a bucket: p50 = p90 = p99, no error.
- Granularity larger than the window (Days 1, `1 day`): spine has ≥1 point, no error.
- Window boundary: a top-up credited before the first spine bucket must not leak into it (filter on the first bucket, not on `now() - days`).
- Total of `completed` over the window must equal a plain count with the same bound — catches double-bucketing or timezone drift.

---

### Task 1: New card + dashcard

**Files:**
- Create: `cards/pca-topup-completion-time-over-time.yaml`
- Modify: `dashboards/pear-cider-by-erjan-solutions-automated.yaml` (Top-ups dashcards)

- [ ] **Step 1: Create the card** modelled on `cards/pca-topups-created-by-hour.yaml` (same envelope, template tags `days` default `'7'` and `granularity` default `'1 day'`, fresh UUIDs for their `id`):
  - `key: pca-topup-completion-time-over-time`, `name: Top-up completion time over time`,
    `collection: pear-cider-automated`, `display: combo`, `database: 2`.
  - `description:` Completion time (created to credited) of completed top-ups, as p50 / p90 / p99 per bucket in minutes, with the number of completions per bucket as bars. Bucketed by completion time, so a top-up that is stuck now appears only once it completes — the aging and in-progress cards show the live side. A spike drawn from a handful of completions is weak evidence; check the bars. All providers combined, one row per provider attempt. Buckets follow the Granularity filter, Europe/Moscow.
  - SQL:

```sql
-- One row per provider attempt (topup_payout). credited_at is the dedicated credit timestamp
-- (V90); bucketing on it, not created_at, keeps the newest buckets honest while a provider stalls.
-- tz-ok: created_at and credited_at are timestamptz; the bare now() compares timestamptz to timestamptz.
WITH spine AS (
  SELECT generate_series(
    date_bin({{granularity}}::text::interval, (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day'), TIMESTAMP '2000-01-03'),
    (now() AT TIME ZONE 'Europe/Moscow'),
    {{granularity}}::text::interval) AS h),
done AS (
  SELECT date_bin({{granularity}}::text::interval, (credited_at AT TIME ZONE 'Europe/Moscow'), TIMESTAMP '2000-01-03') AS h,
         EXTRACT(EPOCH FROM (credited_at - created_at)) / 60 AS minutes
  FROM topup_payout
  WHERE status = 'COMPLETED'
    AND credited_at >= ((SELECT min(h) FROM spine) AT TIME ZONE 'Europe/Moscow'))
SELECT s.h AS at,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY d.minutes)::numeric, 1) AS p50_min,
       round(percentile_cont(0.90) WITHIN GROUP (ORDER BY d.minutes)::numeric, 1) AS p90_min,
       round(percentile_cont(0.99) WITHIN GROUP (ORDER BY d.minutes)::numeric, 1) AS p99_min,
       count(d.minutes) AS completed
FROM spine s
LEFT JOIN done d ON d.h = s.h
GROUP BY 1
ORDER BY 1
```

  - `visualization_settings`: `graph.dimensions: [at]`, `graph.metrics: [p50_min, p90_min, p99_min, completed]`,
    `graph.x_axis.scale: timeseries`, `graph.y_axis.title_text: Completion time, min`,
    `column_settings` for the three `p*_min`: `suffix: ' min'`, `decimals: 1`.
    `series_settings`: `p50_min` line/left "p50" `#88BF4D`; `p90_min` line/left "p90" `#F9D45C`;
    `p99_min` line/left "p99" `#E03131`; `completed` bar/right "Completions" `#DCDFE0`.
    No `stackable.stack_type`.
- [ ] **Step 2: Add dashcard** `key: dc-topup-completion-time-over-time`, `card: pca-topup-completion-time-over-time`,
  `tab: topups`, `row: 22`, `col: 12`, `size_x: 12`, `size_y: 6`, `visualization_settings: {}`, `series: []`,
  `inline_parameters: []`, `parameter_mappings` identical to `dc-topups-created-by-hour` (`821ec3b1` → `days`,
  `3f9a20c7` → `granularity`); place it in the file right after `dc-topup-completion-time-percentiles`.
- [ ] **Step 3: Check the SQL live (read-only)** — run it through `POST /api/dataset` (database 2) via the proxy
  `http://localhost:2080`, tags substituted for (7, `1 day`), (7, `1 hour`), (1, `1 day`). Expected for each:
  no error, well under 30 s; every row has p50 ≤ p90 ≤ p99 or all three NULL with `completed = 0`;
  `sum(completed)` equals `SELECT count(*) FROM topup_payout WHERE status = 'COMPLETED' AND credited_at >= <first bucket AT TIME ZONE 'Europe/Moscow'>`.
- [ ] **Step 4: Validate** — `make mbc-validate`: no errors (including timezone lint). `make mbc-test`: all pass.

### Task 2: Percentiles table onto `credited_at`

**Files:**
- Modify: `cards/pca-topup-completion-time-percentiles.yaml` (native SQL + description)

- [ ] **Step 1: Edit the SQL** — in `spans`: `t.updated_at - t.created_at` → `t.credited_at - t.created_at`; window filter
  `t.updated_at >= (now() AT TIME ZONE 'UTC') - ({{days}} * INTERVAL '1 day')` → `t.credited_at >= now() - ({{days}} * INTERVAL '1 day')`.
  Replace the two-line header comment with: `-- credited_at is the dedicated credit timestamp (V90); both endpoints are timestamptz.`
  plus `-- tz-ok: created_at and credited_at are timestamptz; the bare now() compares timestamptz to timestamptz.`
  Rest of the query (formatting CASE, unnest) unchanged.
- [ ] **Step 2: Edit description** — `created_at to updated_at for COMPLETED topups (updated_at is the last touch, ...)` →
  `created_at to credited_at for COMPLETED topups. Shown as d/h/m/s.`
- [ ] **Step 3: Check live** — run the old and new SQL for Days 7; expected: both return 5 rows, values close
  (V90 backfilled `credited_at = updated_at`, so only rows touched after crediting differ). Report any percentile that moves by more than 10%.
- [ ] **Step 4: Validate** — `make mbc-validate`, `make mbc-test`: pass.

### Task 3: Diff, commit, apply

- [ ] **Step 1: Diff** — `make mbc-diff`: one card to create, `pca-topup-completion-time-percentiles` updated, dashboard dashcards changed (one new dashcard only), nothing else.
- [ ] **Step 2: Commit** (owner's permission required)

```bash
git add cards/pca-topup-completion-time-over-time.yaml cards/pca-topup-completion-time-percentiles.yaml dashboards/pear-cider-by-erjan-solutions-automated.yaml docs/superpowers/specs/2026-11-24-topup-completion-time-trend-design.md docs/superpowers/plans/2026-11-24-topup-completion-time-trend.md
git commit --message "feat(cards): show top-up completion time percentiles over time on Pear Cider"
```

- [ ] **Step 3: Apply** (owner's go) — `./mbc apply`, then commit the updated `.state/<host>.yaml`.
