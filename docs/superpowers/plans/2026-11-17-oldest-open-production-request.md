# Oldest Open Production Request Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a scalar card showing minutes since the oldest non-final production request was created, on Pear Cider Overview → Live state.

**Architecture:** One new YAML card under `cards/`, one new dashcard plus a 3-row layout shift in the dashboard YAML. Managed by `mbc`; no Python changes.

**Tech Stack:** Metabase-as-code YAML (`mbc`), PostgreSQL native SQL.

**Spec:** `docs/superpowers/specs/2026-11-17-oldest-open-production-request-design.md`

## Global Constraints

- Database 2 (legacy orchestrator) only; no dealer twin.
- Non-final = `status NOT IN ('CLOSED', 'FAILED')`.
- `created_at` is naive UTC: compare against `now() AT TIME ZONE 'UTC'`.
- Scalar, suffix ` min`, no thresholds / colors. Value 0 when nothing is open.
- `apply` to the live instance only on the owner's explicit go. Commit/push only with owner's permission.

## Review Focus

- No open requests: `min(created_at)` is NULL — card must show 0, not blank (COALESCE in SQL).
- Requests created moments ago: floor gives 0 — acceptable, no negative values since `now()` ≥ `created_at`.
- Layout shift: every Overview dashcard at row ≥ 19 (rows 19, 20, 25, 26, 33) must move +3, or cards overlap.
- Other tabs' rows must not change.

---

### Task 1: Card + dashcard

**Files:**
- Create: `cards/pca-production-request-oldest-open.yaml`
- Modify: `dashboards/pear-cider-by-erjan-solutions-automated.yaml` (Overview dashcards)

- [ ] **Step 1: Create the card** modelled on `cards/pca-time-since-last-certificate.yaml`:
  - `key: pca-production-request-oldest-open`, `name: Oldest open production request`,
    `collection: pear-cider-automated`, `display: scalar`, `database: 2`.
  - `description:` Whole minutes since the oldest production request still in a non-final status
    (PENDING, PLACING, PRODUCING, AWAITING_INTAKE) was created; 0 when none is open. Age counts from
    created_at, not from the last status change (the table has no such timestamp). A request stuck for
    good keeps the value high until someone handles it — that is the point. Live value, no time window.
  - SQL comment header: the DB 2 / DEALER pseudo-client note used by `pca-production-queue-depth`, plus
    "production_request.created_at is naive UTC, so "now" must be naive UTC too."
  - SQL body exactly as in the spec; `visualization_settings.column_settings['["name","minutes"]'].suffix: ' min'`.
- [ ] **Step 2: Shift Overview layout** — in the dashboard YAML, for Overview-tab dashcards only, rows
  19→22, 20→23, 25→28, 26→29, 33→36.
- [ ] **Step 3: Add dashcard** `key: dc-production-request-oldest-open`, `card: pca-production-request-oldest-open`,
  `tab: overview`, `row: 19`, `col: 0`, `size_x: 12`, `size_y: 3`, empty mappings/series/inline_parameters,
  placed in the file right after `dc-capacity-now-quota-balance`.
- [ ] **Step 4: Validate** — run `make mbc-validate`; expected: no errors. Run `make mbc-test`; expected: all pass.
- [ ] **Step 5: Diff** — run `make mbc-diff`; expected drift: one card to create, dashboard dashcards changed
  (new dashcard + 5 row moves on Overview), nothing else.
- [ ] **Step 6: Commit** (owner's permission required)

```bash
git add cards/pca-production-request-oldest-open.yaml dashboards/pear-cider-by-erjan-solutions-automated.yaml docs/superpowers/specs/2026-11-17-oldest-open-production-request-design.md docs/superpowers/plans/2026-11-17-oldest-open-production-request.md
git commit --message "feat(cards): show age of oldest open production request on Pear Cider"
```

- [ ] **Step 7: Apply** (owner's go) — `./mbc apply`, then commit the updated `.state/<host>.yaml`.
