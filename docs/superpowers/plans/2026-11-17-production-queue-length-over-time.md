# Production Queue Length Over Time Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a line chart showing how many production requests were open (non-final) at each moment, on Pear Cider → Production → Requests.

**Architecture:** One new YAML card under `cards/`, one new dashcard plus a 7-row layout shift in the dashboard YAML. Managed by `mbc`; no Python changes.

**Tech Stack:** Metabase-as-code YAML (`mbc`), PostgreSQL native SQL.

**Spec:** `docs/superpowers/specs/2026-11-17-production-queue-length-over-time-design.md`

## Global Constraints

- Database 2 (legacy orchestrator) only; no dealer twin.
- Open = `status NOT IN ('CLOSED', 'FAILED')`; exit time for CLOSED/FAILED =
  `GREATEST(max(certificate.created_at), max(GREATEST(task.succeeded_at, task.started_at)))` over its
  certificates and `purpose = 'PRODUCTION'` tasks; neither present → exit = `created_at`.
- All timestamps are naive UTC; buckets are Europe/Moscow (`mbcode/lint_timezone.py` enforces explicit `AT TIME ZONE`).
- Line, single series, no goal line, no colour rules.
- Must run well under `statement_timeout=30s` at 30 days × `1 hour` (prototype: 4.5 s).
- `apply` to the live instance only on the owner's explicit go. Commit/push only with owner's permission.

## Review Focus

- Value is a snapshot at each bucket boundary; with `1 day` the last point is today 00:00, not "now" — stated in the description.
- Requests created before window start and still open must be counted (pre-window balance term), else the line starts at ~0.
- Granularity larger than the window (e.g. `1 week`, Days 1): spine still has ≥1 point; chart must not error.
- Layout shift: every Production dashcard at row ≥ 26 must move +7, or cards overlap; other tabs untouched.
- Wrong exit semantics (range join instead of sweep) times out at hourly 30 days — keep the sweep.

---

### Task 1: Card + dashcard

**Files:**
- Create: `cards/pca-production-queue-length-over-time.yaml`
- Modify: `dashboards/pear-cider-by-erjan-solutions-automated.yaml` (Production dashcards)

- [ ] **Step 1: Create the card** modelled on `cards/pca-production-requests-by-hour.yaml` (same envelope, template tags `days` default `'7'` and `granularity` default `'1 day'`, fresh UUIDs for their `id`):
  - `key: pca-production-queue-length-over-time`, `name: Production queue length over time`,
    `collection: pear-cider-automated`, `display: line`, `database: 2`.
  - `description:` Production requests not yet finished (PENDING, PLACING, PRODUCING, AWAITING_INTAKE — not only PENDING, since requests return to PENDING after retries) at each moment. Rising means production is falling behind, falling means it is catching up. Rebuilt from timestamps: production_request has no exit time, so a finished request counts as leaving at its last certificate or production task; about 2% of finished requests have neither and are not counted. Each point is a snapshot at the start of its bucket (with 1 day, the last point is today 00:00). Buckets follow the Granularity filter, Europe/Moscow.
  - SQL: the DB 2 / DEALER pseudo-client comment header from `pca-production-queue-depth`, then:

```sql
WITH spine AS (
  SELECT generate_series(
    date_bin({{granularity}}::text::interval, (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day'), TIMESTAMP '2000-01-03'),
    (now() AT TIME ZONE 'Europe/Moscow'),
    {{granularity}}::text::interval) AS h),
-- Closed requests created more than 30 days before the window are ignored (p99 time-to-close is ~3 days).
lo AS (SELECT (min(h) AT TIME ZONE 'Europe/Moscow' AT TIME ZONE 'UTC') - INTERVAL '30 days' AS t FROM spine),
pr AS (
  SELECT pr_id, created_at, status FROM production_request, lo
  WHERE status NOT IN ('CLOSED', 'FAILED') OR created_at >= lo.t),
c AS (SELECT production_request_id AS id, max(created_at) AS at FROM certificate
      WHERE production_request_id IN (SELECT pr_id FROM pr) GROUP BY 1),
t AS (SELECT production_request_id AS id, max(GREATEST(succeeded_at, started_at)) AS at FROM task
      WHERE purpose = 'PRODUCTION' AND production_request_id IN (SELECT pr_id FROM pr) GROUP BY 1),
iv AS (
  SELECT pr.created_at AS in_at,
         CASE WHEN pr.status IN ('CLOSED', 'FAILED') THEN COALESCE(GREATEST(c.at, t.at), pr.created_at) END AS out_at
  FROM pr LEFT JOIN c ON c.id = pr.pr_id LEFT JOIN t ON t.id = pr.pr_id),
-- Sweep: +1 on entry, -1 on exit; a delta at ts lands on the next bucket boundary, so point h counts ts < h.
delta AS (
  SELECT in_at AS ts, 1 AS d FROM iv
  UNION ALL
  SELECT out_at, -1 FROM iv WHERE out_at IS NOT NULL),
bucketed AS (
  SELECT date_bin({{granularity}}::text::interval, (ts AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow'), TIMESTAMP '2000-01-03')
           + {{granularity}}::text::interval AS h,
         sum(d) AS d
  FROM delta GROUP BY 1)
SELECT s.h AS at,
       sum(COALESCE(b.d, 0)) OVER (ORDER BY s.h)
         + (SELECT COALESCE(sum(d), 0) FROM bucketed WHERE h < (SELECT min(h) FROM spine)) AS open_requests
FROM spine s
LEFT JOIN bucketed b ON b.h = s.h
ORDER BY 1
```

  - `visualization_settings`: `graph.dimensions: [at]`, `graph.metrics: [open_requests]`,
    `graph.x_axis.scale: timeseries`, `graph.y_axis.title_text: Open requests`,
    `series_settings.open_requests.color: '#509EE3'`.
- [ ] **Step 2: Shift Production layout** — Production-tab dashcards only, row +7: 26→33, 27→34 (three cards), 34→41, 35→42, 42→49, 43→50 (three cards), 46→53, 53→60, 54→61 (two cards).
- [ ] **Step 3: Add dashcard** `key: dc-production-queue-length-over-time`, `card: pca-production-queue-length-over-time`,
  `tab: production`, `row: 26`, `col: 0`, `size_x: 24`, `size_y: 7`, `visualization_settings: {}`, `series: []`,
  `inline_parameters: []`, `parameter_mappings` identical to `dc-production-requests-by-hour` (`821ec3b1` → `days`,
  `3f9a20c7` → `granularity`); place it in the file right after `dc-production-requests-by-hour`.
- [ ] **Step 4: Check the SQL live (read-only)** — run the card's SQL through `POST /api/dataset` (database 2) with
  tags substituted for (7, `1 day`), (30, `1 hour`), (1, `1 week`). Expected: each returns rows without error,
  30/`1 hour` in < 15 s, no negative values, latest point within a few percent of
  `SELECT count(*) FROM production_request WHERE status NOT IN ('CLOSED','FAILED')`.
- [ ] **Step 5: Validate** — `make mbc-validate`: no errors (including timezone lint). `make mbc-test`: all pass.
- [ ] **Step 6: Diff** — `make mbc-diff`: one card to create, dashboard dashcards changed (new dashcard + Production row moves), nothing else.
- [ ] **Step 7: Commit** (owner's permission required)

```bash
git add cards/pca-production-queue-length-over-time.yaml dashboards/pear-cider-by-erjan-solutions-automated.yaml docs/superpowers/specs/2026-11-17-production-queue-length-over-time-design.md docs/superpowers/plans/2026-11-17-production-queue-length-over-time.md
git commit --message "feat(cards): show production queue length over time on Pear Cider"
```

- [ ] **Step 8: Apply** (owner's go) — `./mbc apply`, then commit the updated `.state/<host>.yaml`.
