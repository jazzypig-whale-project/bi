# Oldest open production request — design

## Intent

A plain sign on the Pear Cider dashboard that some production requests are stuck: the age of the
oldest production request that has not reached a final status. Text only — no color thresholds.

## Goals / non-goals

- Goal: one live number, readable at a glance, that grows when production stalls.
- Non-goal: listing the stuck requests, showing their status, alerting, color highlighting.

## Approaches

- **Scalar of minutes since the oldest non-final `created_at` (picked).** Mirrors the existing
  "Last certificate sent" scalar; minimal and matches "only a sign".
- Table of stuck requests — more than a sign; rejected.
- Text value with status ("42 min, PRODUCING") — scalar formatting gets awkward and status was not asked
  for; rejected.

## Design

- Card `pca-production-request-oldest-open`, name "Oldest open production request", collection
  `pear-cider-automated`, database 2 (legacy orchestrator; production exists only there, dealer demand
  arrives via the DEALER pseudo-client, so no dealer twin).
- Non-final statuses: PENDING, PLACING, PRODUCING, AWAITING_INTAKE. Final: CLOSED, FAILED.
- SQL (`created_at` is naive UTC):

  ```sql
  SELECT COALESCE(floor(EXTRACT(EPOCH FROM ((now() AT TIME ZONE 'UTC') - min(created_at))) / 60), 0)::bigint AS minutes
  FROM production_request
  WHERE status NOT IN ('CLOSED', 'FAILED')
  ```

- Display `scalar` of text: "N min" under a day, "D d H h" from a day up (SQL CASE), no thresholds. "0 min" when nothing is open.
- Description states: age is from `created_at`, not from the last status change (no such timestamp);
  a permanently stuck request keeps the value high until handled — intended.
- Placement: Production & Capacity tab, under the "Requests" heading, after the requests-by-hour chart, new row 23 col 0, size 12×3. Every Production dashcard
  at row ≥ 23 shifts down by 3.

## Verification

`make mbc-validate`, `make mbc-diff` (shows one new card + layout shift). `apply` only on the owner's go.
