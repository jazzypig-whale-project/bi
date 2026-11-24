# Top-up completion time over time — design

Date: 2026-11-24. Size: small change. Status: approved by owner.

## Intent

The Top-ups tab has "Topup completion time percentiles" (`cards/pca-topup-completion-time-percentiles.yaml`),
a single table for the whole Days window. It cannot show *when* completions got slower. The owner
wants a time series to pin down the date and hour a top-up provider started slowing down.

## Goal

A new card, "Top-up completion time over time": p50 / p90 / p99 completion time per time bucket as
lines, with the number of completions per bucket as faint bars on a second axis, so a spike drawn
from 3 top-ups is visibly weak evidence.

## Non-goals

- Per-provider split. Only one provider is active now; deferred to a later change.
- Max / min / avg lines (owner picked approach 1).
- Alerting or thresholds.

## Constraints

- Read-only SQL against `orchestrator` (role `metabase`); no app-DB writes.
- Follows the dashboard's Days and Granularity filters, buckets in Europe/Moscow, `date_bin` anchored
  at `TIMESTAMP '2000-01-03'` with a `generate_series` spine — same pattern as `pca-topups-created-by-hour`.

## Approaches

1. **Percentile lines + completion-count bars** — picked by the owner.
2. Same plus a max line — rejected: one stuck outlier flattens the other lines; noisier chart.
3. Stacked duration-band bars (<1m, 1–5m, 5–30m, >30m) — rejected: no exact "p90 crossed X at
   hour Y" reading; band edges are fixed guesses.

## Design

- Source: `topup_payout` rows with `status = 'COMPLETED'` and `credited_at IS NOT NULL`
  (payout-row grain = one provider attempt, which is the right grain for provider latency).
- Duration: `credited_at - created_at` in seconds (both `timestamptz`). `credited_at` is the
  dedicated credit timestamp (V90, indexed V124) the app itself uses for its lead-time p95.
- Bucket: `date_bin(granularity, credited_at AT TIME ZONE 'Europe/Moscow', '2000-01-03')` — by
  completion time, not creation time. Bucketing by creation would make the newest buckets show only
  the already-finished (fast) top-ups and look healthy exactly while the provider stalls.
- Window filter: `credited_at >=` the first spine bucket (uses `idx_topup_payout_credited_at`).
- Output per bucket: `p50_min`, `p90_min`, `p99_min` (`percentile_cont`, in minutes, 1 decimal) and
  `completed` (count). Empty buckets: count 0, percentiles NULL (gap in the line, not a fake zero).
- Display: `combo`; percentile lines on the left axis ("Completion time, min"); `completed` as bars
  on the right axis, light grey.
- Dashcard: Top-ups tab, row 22, col 12, 12×6 — the free right half next to the percentiles table.
  Mapped to Days and Granularity.
- Description states: completion-time bucketing, its known lag (a stalling top-up appears only once
  it completes; the aging / in-progress cards cover the live side), all providers combined.

My calls (owner may strike):

- Minutes as the unit (vs. seconds) — readable for both sub-minute and multi-hour values.
- Also switch the existing percentiles table from `updated_at` to `credited_at` and correct its
  stale comment ("no dedicated completed_at column", "naive UTC"), so the two cards on the tab agree.

## Verification

Run the SQL live via Metabase for 7 days at 1 hour and 1 day: buckets complete, percentiles ordered
p50 ≤ p90 ≤ p99, total `completed` equals a plain count over the same window. Then `./mbc validate`
and `./mbc diff`. No apply / commit without the owner's OK.

## Left to the builder

Exact series colors, column names, description wording.
