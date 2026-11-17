# Production queue length over time — design

## Intent

A time-series chart on the Pear Cider dashboard that shows how many production requests were
waiting at each moment. A rising line means production has a problem; a falling line means
the problem is being resolved. It is statistics for an observer only: no highlighting, no
thresholds, no alerting.

## Goals / non-goals

- Goal: one line, historical from day one, following the dashboard's Days / Granularity filters.
- Non-goal: alerts, colour thresholds, per-status or per-denomination breakdown, listing requests.

## Constraints

- `production_request` has no exit timestamp and no status history; the queue must be rebuilt.
- Requests return to `PENDING` after a retry (497 of 1761 PENDING had tasks on 2026-11-17), so a
  historical PENDING-only count cannot be rebuilt. The chart counts **all non-final requests**
  (PENDING, PLACING, PRODUCING, AWAITING_INTAKE) — the same definition as the
  "Oldest open production request" card.
- Read-only `metabase` role, `statement_timeout=30s`.

## Approaches

- **Rebuild from timestamps (picked).** A request is in the queue from `created_at` until its
  exit time. History available immediately; exit times are approximate for a small share.
- Sample the count into `bi-postgres` on a schedule — exact, could separate PENDING, but the
  history starts empty and it adds a job plus a new Metabase data source. Rejected.
- Cumulative requested minus produced — drifts with failures. Rejected.

## Design

- Card `pca-production-queue-length-over-time`, name "Production queue length over time",
  collection `pear-cider-automated`, database 2 (legacy orchestrator; production exists only
  there, dealer demand arrives via the DEALER pseudo-client, so no dealer twin).
- Exit time of a request in CLOSED or FAILED:
  `GREATEST(max(certificate.created_at), max(GREATEST(task.succeeded_at, task.started_at)))`
  over its certificates and PRODUCTION tasks. ~2% of finished requests (5551 CLOSED, 428 FAILED
  on 2026-11-17) have neither; they get exit = `created_at`, i.e. never counted. Non-final
  requests have no exit (open until now).
- Value at each point = number of requests open at that instant (snapshot at the bucket
  boundary, Europe/Moscow), computed as a sweep: +1 at entry, −1 at exit, running sum, plus
  the balance before the window. Only requests that are non-final or were created within 30
  days before the window start are read (p99 time-to-close ≈ 3 days).
- Template tags `days` (default 7) and `granularity` (default `1 day`), wired to the dashboard
  filters like `pca-production-requests-by-hour`. All timestamps are naive UTC, converted like
  the existing cards.
- Display `line`, timeseries x-axis, y-axis title "Open requests", single series, no goal line,
  no colour rules.
- Description states: counts all non-final requests, not only PENDING; exit time is
  approximated from the last certificate/task; ~2% without either are not counted; value is a
  snapshot at each bucket boundary.
- Placement: Production tab, "Requests" section, new row 26 col 0, size 24×7, under the
  oldest-open scalar. Every Production dashcard at row ≥ 26 shifts down by 7.

## Verified so far

Prototype on prod (read-only, through Metabase): 30 days at 1-hour buckets in 4.5 s; values
match a range-join cross-check; the latest point (1764) matches the live non-final count (1761).

## Verification

`make mbc-validate`, `make mbc-diff` (one new card + layout shift). `apply` only on the owner's go.
