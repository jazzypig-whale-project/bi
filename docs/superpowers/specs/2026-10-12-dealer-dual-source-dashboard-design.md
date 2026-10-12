# Pear Cider dashboard: legacy + dealer data sources

**Date:** 2026-10-12
**Status:** draft, awaiting owner review
**Dashboard:** `dashboards/pear-cider-by-erjan-solutions-automated.yaml`

## Goal

The orchestrator app now has a second database, `dealer`, where API clients move one by one (marker:
`dealer.api_client.v1_switched_at`). The Pear Cider (automated) dashboard must keep showing honest
business totals across the migration, show the legacy/dealer split, and track migration progress.

Finance reconciles by opening dashboard cards and reading their SQL. Card SQL is therefore part of the
deliverable: it must be readable and say what it counts, from which database, and what it excludes.

## Facts the design rests on

- Metabase sees the dealer DB as **database 3** (legacy `orchestrator` is database 2). The read-only
  `metabase` role has SELECT on it (`orchestrator/docs/deployment.md:241`, D156). Verified over MCP on
  2026-10-12: 1 client (`test`, DISABLED, not switched), 2 COMPLETED orders.
- A native query cannot read two databases. Cross-DB combination is done only by stacking cards from
  different databases as series on one dashcard (`docs/FORMAT.md:264-287`, `mbc` commit `d51b9fa`).
- **Every dealer sale is mirrored in legacy.** The dealer buys each voucher from legacy with
  `PURCHASE_VOUCHER`; legacy creates an `order_` row under the pseudo-client
  `00000000-0000-0000-0000-000000000002` (DEALER), status PAID, no buyer email, amount in whole rubles
  (`orchestrator/.../order/OrderService.java:204-225`). That order reserves a real legacy `certificate`,
  goes the API-delivery path (certificate SENT, order COMPLETED, no email) and answers with
  `VOUCHER_ISSUED` (`orchestrator/.../fulfillment/FulfillmentService.java:103-124`).
- Consequence: legacy certificate/pool data already contains all dealer volume. Legacy email data
  contains none of it; the dealer emails buyers itself via `shipment` / `shipment_attempt`.
- Switching copies only client config; no order history moves. After the switch a client's orders live
  only in `dealer_order` (plus the DEALER mirror in legacy).
- Dealer amounts are BIGINT minor units (kopecks) with a currency column; legacy amounts are whole rubles.
  Dealer timestamps are `timestamptz`; legacy timestamps are naive UTC.
- Pool, production, devices, accounts, top-ups, provider deposits exist only in legacy.

## Card plan

### 1. Certificate and pool totals: no twin, comment only

Already correct (dealer sales consume legacy certificates). A twin would double-count. Each card's SQL
gets one header comment stating that dealer sales are included through the DEALER pseudo-client.

`pca-certificate-face-value-by-day`, `pca-average-check-by-day`, `pca-certificates-sent-window`,
`pca-certificates-sent-by-hour`, `pca-certificates-sold-by-denomination`,
`pca-certificate-turnover-by-denomination`, `pca-time-since-last-certificate`,
`pca-certificate-face-value-by-day-by-provider`, `pca-produced-face-value-by-day-by-provider`,
`pca-certificates-produced-by-day`, `pca-certificates-in-pool-by-denomination`, `pca-pool-total-value`,
`pca-production-queue-depth`, `pca-production-queue-by-denomination`, `pca-production-requests-by-hour`.

The Provider parameter stays mapped to the two by-provider cards only (provider is a legacy production
attribute; the dealer has no equivalent).

### 2. Per-client and order-flow cards: exclude DEALER in legacy, add a dealer twin

| Legacy card | Change | Dealer twin | Dashcard |
|---|---|---|---|
| `pca-certificate-face-value-by-day-by-client` | exclude DEALER | `dealer_order` COMPLETED, `sum(face_amount)/100.0` by `completed_at` bucket, by `api_client.name` | stacked series |
| `pca-flow-by-hour` | exclude DEALER from the "Ordered" stream only | "Ordered" stream from `dealer_order.created_at` | extra series line |
| `pca-orders-in-progress` | exclude DEALER | `dealer_order` in non-terminal statuses | scalar pair, side by side |
| `pca-time-since-last-order-by-partner` | exclude DEALER | `max(created_at)` by `api_client.name` | table pair, side by side |

For the by-client card the stacked total equals the category-1 face value total for the same window.
That equality is the reconciliation check (see Testing).

### 3. Emails: dealer twins on `shipment` / `shipment_attempt`

Legacy email cards are unchanged in logic (DEALER mirror orders carry no email), plus a header comment.

| Legacy card | Dealer twin reads | Dashcard |
|---|---|---|
| `pca-emails-sent-by-hour-by-domain` | `shipment_attempt.sent_at`, domain of `from_address` / `to_address` matching the legacy card's dimension | stacked series |
| `pca-emails-failed-by-hour-by-domain` | `shipment.status IN ('FAILED','BOUNCED')` / `shipment_attempt.category` | stacked series |
| `pca-emails-delivery-by-domain` | `shipment` status counts by recipient domain | table pair |
| `pca-emails-late-bounces-unresolved` | `late_bounce_at IS NOT NULL AND late_delivered_at IS NULL` | scalar pair |
| `pca-emails-late-bounces-by-day` | `shipment.late_bounce_at` bucket | stacked series |
| `pca-emails-late-bounce-lag` | `late_bounce_at - sent_at`, same buckets as legacy | stacked series |

Each twin mirrors the legacy card's dimension (sender vs recipient domain). The exact status and verdict
mapping is fixed while writing the SQL, against dealer code, and stated in the twin's header comment.

### 4. Off-dashboard cards: exclude DEALER only, no twins

`pca-orders-by-hour-sber`, `pca-orders-by-hour-other`, `pca-orders-aging-sber`, `pca-orders-aging-other`,
`pca-orders-in-progress-by-status-partner`, `pca-completion-time-percentiles`,
`pca-sales-distribution-by-partner`. Today the DEALER wholesale orders count as "Other"/"Unknown" there;
after the change those cards show legacy client orders only. `pca-emails-failed-top-domains` is unchanged.

### 5. Unaffected

Top-ups, devices, accounts, capacity, provider deposits: no change.

### 6. New: migration progress (Overview tab)

New heading row "Migration to dealer" with:
- `pca-dealer-clients-switched`: scalar, `count(*) FROM api_client WHERE v1_switched_at IS NOT NULL`
  (database 3).
- `pca-orders-by-day-legacy` + `pca-orders-by-day-dealer`: stacked bar of client orders per bucket;
  legacy excludes DEALER. Honors Days and Granularity.

## Conventions

- Twin file name: `cards/pca-<legacy-key>-dealer.yaml`, `database: 3`, collection `pear-cider-automated`.
- Twins return the **same column names** and use the same `{{days}}` / `{{granularity}}` template tags as
  the legacy card, so series align.
- DEALER exclusion is written literally:
  `AND o.client_id <> '00000000-0000-0000-0000-000000000002'  -- DEALER pseudo-client: dealer purchases, counted in the dealer DB`.
- Amounts: `face_amount / 100.0` for RUB. Rows in another currency are not mixed in; they form their own
  series labeled with `face_currency`.
- Time buckets: dealer `ts AT TIME ZONE 'Europe/Moscow'`; legacy keeps
  `ts AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow'`.
- Series and pair titles: "Legacy · …" / "Dealer · …" so the source is visible in the legend.
- Every touched SQL starts with a header comment: what it counts, which database, exclusions and why.

## Dashboard wiring

- Affected dashcards get `series: [pca-…-dealer]` and one `parameter_mappings` entry per parameter per
  twin (`card: pca-…-dealer`). A twin without its own mapping silently runs on its template-tag default.
- Pairs: the legacy dashcard shrinks to half width; the twin takes the other half on the same row.
- Layout of other dashcards on the same tab shifts only where a new row is inserted.

## Testing and rollout

1. `make mbc-test` and `./mbc validate` pass.
2. Each twin's SQL runs over MCP against database 3; each edited legacy SQL runs against database 2.
3. Reconciliation check, for the same window: Σ `pca-certificate-face-value-by-day` (legacy, includes
   DEALER) = Σ legacy by-client excluding DEALER + Σ dealer twin. Holds trivially today (2 test orders);
   re-run once a real client switches.
4. `./mbc export` to a scratch dir and compare with the repo, to catch dashcards added in the UI
   (`apply` replaces dashcards wholesale); adopt them with `./mbc export --overwrite` if any.
5. `./mbc diff`, reviewed with the owner; `./mbc apply` only on the owner's go-ahead.
6. Commit `cards/`, `dashboards/`, `.state/` with the owner's permission.

## Out of scope

- Moving any data between databases, any ETL, any change to the orchestrator app or its databases.
- Twins for off-dashboard cards.
- Provider breakdown of dealer sales.
- Combined single-number scalars across databases (Metabase cannot sum them; pairs are used instead).
