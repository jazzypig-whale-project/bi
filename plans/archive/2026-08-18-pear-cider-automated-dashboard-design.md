# Pear Cider by Erjan.Solutions (automated) — dashboard design

**Date:** 2026-08-18
**Status:** approved, not yet implemented
**Target:** Metabase v0.60.7 OSS, database id `2` (live orchestrator PostgreSQL)

## 1. Goal

Build a new dashboard, `Pear Cider by Erjan.Solutions (automated)`, that carries every
metric of the existing `Pear Cider by Erjan.Solutions ` dashboard plus eighteen new
operational metrics, grouped into four tabs, written entirely in English.

The existing dashboard and its nine Russian-named cards are **not modified**. The new
dashboard re-authors those nine metrics as new English cards; the old dashboard keeps
working exactly as it does today.

## 2. Constraints discovered during design

These are properties of the environment the design had to be shaped around, not
decisions:

- **Read-only role with timeouts.** Metabase connects as the `metabase` role created by
  `orchestrator/docker/db/create-metabase-user.sh`: `SELECT`-only on `public`,
  `statement_timeout = 30s`, `lock_timeout = 15s`, `CONNECTION LIMIT 20`. Every query in
  this design must complete inside 30 seconds.
- **Quota limits are application config, not data.** `maxTasksPerAccount = 6`,
  `dayLimitAccount = 35000`, `dayLimitNumber = 500000`, `monthLimitNumber = 600000`,
  `bufferAccount = 5000`, `minCertificateAmount = 1000` live in `application.yml` under
  `app.quota`. They are not readable from the database.
- **Money is integer whole roubles.** No minor units anywhere; a 1000 ₽ certificate is
  stored as `1000`. Amounts are constrained to multiples of 500.
- **Missing terminal timestamps.** `order_` stamps `completed_at` only on the
  `COMPLETED` transition (`OrderRepository.java:31`). Orders ending `REJECTED`,
  `EXPIRED` or `FAILED_MANUAL` carry no end timestamp at all. `production_request` has
  no terminal timestamp of any kind.
- **No fleet-state history.** `device.state`, agent availability, `validation_status`,
  `held_at` and `busy_backoff_until` are current values only. Nothing historises them.
- **Reconstructible history.** `quota_ledger.ts`, `account_cooldown`
  (`applied_at`/`until`/`cleared_at`), `balance_ledger.balance_after` and
  `topup_payout.updated_at` do allow point-in-time reconstruction.
- **Mixed timestamp types.** `order_`, `certificate`, `production_request`, `task` and
  `quota_ledger` use naive `timestamp`. `topup_payout` and `balance_ledger` use
  `timestamptz`.

## 3. Decisions

| # | Decision | Rationale |
| --- | --- | --- |
| D1 | New English cards; existing Russian cards untouched | Zero blast radius on the live dashboard |
| D2 | Every new card is native SQL inside the MBQL-5 envelope | Percentiles, duration buckets, hour spines and the capacity formula are not expressible in MBQL; SQL also diffs readably, unlike field-id references |
| D3 | One dashboard parameter `days` (Number, default 7) | A single window control for the whole dashboard |
| D4 | Capacity is reported twice: exact scalars for "now", upper-bound trend for history | Fleet readiness is not historised, so a historical line can only ignore it |
| D5 | Created/succeeded/failed charts bucket by cohort on `created_at` | Failed orders and requests have no terminal timestamp; a cohort view needs none and invents nothing |
| D6 | Aging reconstruction excludes `REJECTED`/`EXPIRED`/`FAILED_MANUAL` orders | Their interval cannot be closed; including them would show them open forever |
| D7 | "By partner" means Sber versus one combined *Other partners* bucket | Sber is the primary partner; a nine-way split would make every chart unreadable |
| D8 | Sber and Other partners get **separate** cards, not extra series | The merged order chart already carries three outcome series; six would be unreadable |
| D9 | Four tabs, not one long page | Metabase runs every card on a visible tab at once; tabs keep the four heavy reconstructions off the KPI row |
| D10 | Orders in progress **includes** `AWAITING_PAYMENT` in the total, with a status breakdown card beside it | Requested explicitly; the breakdown keeps customer-side wait distinguishable |
| D11 | Top-ups in progress mirrors `TopUpInFlightService` exactly | The dashboard must not disagree with the orchestrator about what money is in flight |
| D12 | New collection `Pear Cider (automated)` | Clean separation from the Russian card set; archivable as one unit |

## 4. Architecture

### 4.1 Files

```
collections/pear-cider-automated.yaml
cards/<30 new card files>.yaml
dashboards/pear-cider-by-erjan-solutions-automated.yaml
```

No existing file is edited.

### 4.2 Card shape

Native SQL is carried in a native stage inside the MBQL-5 envelope:

```yaml
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      SELECT ...
    template-tags:
      days:
        id: <uuid>
        name: days
        display-name: Days
        type: number
        default: "7"
```

`mbcode/validate.py:117` asserts only `lib/type: mbql/query` plus a `stages` list, so a
native stage validates unchanged.

### 4.3 The `days` parameter

The dashboard declares one parameter, `days`, of type number, default `7`, mapped to the
`{{days}}` template tag of every time-windowed card. The window expression is uniformly:

```sql
>= now() - ({{days}} * INTERVAL '1 day')
```

### 4.4 Translation of the existing nine metrics

| Existing card | New card |
| --- | --- |
| Сумма номиналов сертификатов по дням | Certificate face value by day |
| Количество отправленных сертификатов | Certificates sent by hour |
| Оборот сертификатов по номиналам | Certificate turnover by denomination |
| Количество проданных сертификатов по номиналам | Certificates sold by denomination |
| Распределение продаж по партнерам | Sales distribution by partner |
| Средний чек по дням | Average check by day |
| Доступные сертификаты в пуле **+** Certificates in pool | Certificates in pool by denomination (merged) |
| Очередь генерации сертификатов | Production queue by denomination |
| Сумма пула сертификатов | Pool total value |

Two merges reduce the count: the pool-by-denomination metric existed twice (a table and
an unused pie), and the by-day "certificates sent" card is superseded by its hourly form,
whose window the `days` parameter controls.

## 5. Layout

Four tabs, 24-column grid, `width: fixed`. Every dashcard satisfies `col + size_x <= 24`.

### Tab 1 — Overview

```
+--------------------------------------------------------------+
| Certificate face value by day        bar+trendline    24 x 8  |
+--------------------------------------------------------------+
| PROD QUEUE  | CERTS SENT  | LAST CERT   | ORDERS IN PROGRESS  |
|    6 x 3    |    6 x 3    |    6 x 3    |       6 x 3         |
+-------------+-------------+-------------+---------------------+
| TOPUPS WIP  | TOPUPS WIP  | CAPACITY    | CAPACITY            |
| count 6 x 3 | RUB   6 x 3 | quota 6 x 3 | +balance    6 x 3   |
+-------------+-------------+-------------+---------------------+
| Time since last order, by partner            table   24 x 5   |
+--------------------------------------------------------------+
| Flow by hour: requested / produced / sent / ordered  24 x 7   |
+--------------------------------------------------------------+
```

`Certificate face value by day` is the first dashcard of the dashboard, as required.

### Tab 2 — Orders

```
+---------------------------------+----------------------------+
| Orders by hour - Sber      12x7 | Orders by hour - Other 12x7|
+---------------------------------+----------------------------+
| Orders in progress now,    12x6 | Completion time        12x6|
| by status and partner           | percentiles Sber/Other     |
+---------------------------------+----------------------------+
| Orders aging - Sber        12x7 | Orders aging - Other   12x7|
+---------------------------------+----------------------------+
| Certificates sent by hour                            24 x 6   |
+---------------------------------+----------------------------+
| Sales distribution by      12x7 | Average check by day   12x7|
| partner (all clients)           |                            |
+---------------------------------+----------------------------+
| Certificates sold by       12x7 | Certificate turnover   12x7|
| denomination                    | by denomination            |
+---------------------------------+----------------------------+
```

### Tab 3 — Production & Capacity

```
+--------------------------------------------------------------+
| Production requests by hour, by outcome     stacked  24 x 7   |
+------------------------+------------------------+-------------+
| Certificates in pool   | Production queue       | Pool total  |
| by denomination  9 x 7 | by denomination  9 x 7 | value 6 x 7 |
+------------------------+------------------------+-------------+
| Capacity by hour: quota+cooldown vs +balance    line 24 x 7   |
+--------------------------------------------------------------+
```

### Tab 4 — Top-ups

```
+--------------------------------------------------------------+
| Top-ups created by hour: count + total RUB  dual-axis 24 x 7  |
+--------------------------------------------+-----------------+
| Top-ups in progress, aging          16 x 7 | By status 8 x 7 |
+--------------------------------------------+-----------------+
```

## 6. Card inventory (30 cards)

### Overview (11)

1. **Certificate face value by day** — bar with trendline. `sum(amount)` from
   `certificate` where `status = 'SENT'`, grouped by `date_trunc('day', sent_at)`.
2. **Production queue depth** — scalar. `count(*)` from `production_request` where
   `status = 'PENDING'`.
3. **Certificates sent (last N days)** — scalar. `count(*)` from `certificate` where
   `sent_at` inside the window.
4. **Time since last certificate sent** — scalar, integer minutes, suffix `" min"`.
   `now() - max(sent_at)`.
5. **Orders in progress** — scalar. `count(*)` from `order_` where `status IN
   ('AWAITING_PAYMENT','PAID','WAITING_CERT','SENDING')`, all partners.
6. **Top-ups in progress (count)** — scalar. `count(*)` from `topup_payout` where
   `status IN ('CREATE_RETRY','PENDING_POLL')`.
7. **Top-ups in progress (₽)** — scalar. `sum(amount)` over the same predicate.
8. **Capacity now — quota + cooldown** — scalar. See §7.5.
9. **Capacity now — quota + cooldown + balance** — scalar. See §7.5.
10. **Time since last order, by partner** — table `(partner, last order at, age)`,
    ordered Sber first then by age descending.
11. **Flow by hour** — line, four series: requested / produced / sent / ordered. See §7.4.

### Orders (11)

12. **Orders by hour — Sber** — stacked bar, cohort by outcome. See §7.1.
13. **Orders by hour — Other partners** — same query, inverted partner predicate.
14. **Orders in progress now, by status and partner** — grouped bar, x = the four
    non-terminal statuses, series = Sber / Other partners.
15. **Completion time percentiles** — grouped bar, p50/p75/p90/p95/p99 × Sber / Other.
    See §7.3.
16. **Orders aging — Sber** — stacked bar over the hour spine, ten age buckets. See §7.2.
17. **Orders aging — Other partners** — same query, inverted partner predicate.
18. **Certificates sent by hour** — bar over the hour spine.
19. **Sales distribution by partner** — bar, `sum(amount)` per `api_client.name` across
    all clients. Translated from the existing card; the full breakdown is kept here
    deliberately, since this is the one chart whose purpose is the per-partner split.
20. **Average check by day** — bar, `avg(amount)` of `SENT` certificates by day.
21. **Certificates sold by denomination** — pie, `count(*)` of `SENT` by `amount`.
22. **Certificate turnover by denomination** — pie, `sum(amount)` of `SENT` by `amount`,
    current year.

### Production & Capacity (5)

23. **Production requests by hour, by outcome** — stacked bar, cohort on
    `production_request.created_at`, segmented by current status.
24. **Certificates in pool by denomination** — bar, `count(*)` of `FREE` by `amount`.
25. **Production queue by denomination** — pie, `count(*)` of `PENDING`
    `production_request` by `amount`.
26. **Pool total value** — scalar, `sum(amount)` of `FREE` certificates.
27. **Capacity by hour** — line, two series (quota + cooldown, and also balance).
    See §7.6.

### Top-ups (3)

28. **Top-ups created by hour** — dual-axis, count on the left axis and total ₽ on the
    right. Counts `DISTINCT message_id`, see §7.7.
29. **Top-ups in progress, aging** — stacked bar over the hour spine, ten age buckets.
    See §7.8.
30. **Top-ups by status (now)** — bar, live `count(*)` per status across all nine
    `TopUpPayoutStatus` values, so `PARKED` rows awaiting manual triage stay visible.

## 7. Query semantics

### 7.0 Shared building blocks

Hour spine, so an idle hour renders as a zero rather than a gap:

```sql
WITH spine AS (
  SELECT generate_series(
    date_trunc('hour', now() - ({{days}} * INTERVAL '1 day')),
    date_trunc('hour', now()),
    INTERVAL '1 hour') AS h)
```

Partner bucket:

```sql
LEFT JOIN api_client c ON c.client_id = o.client_id
CASE WHEN c.name = 'sber' THEN 'Sber' ELSE 'Other partners' END
```

The join is `LEFT` on purpose: an order whose client row is missing lands in
*Other partners* rather than disappearing from the chart.

Age buckets — the nine requested thresholds yield ten buckets:
`<1m, 1–5m, 5–15m, 15–30m, 30m–1h, 1–3h, 3–6h, 6h–1d, 1–3d, >3d`.

### 7.1 Orders, cohort by outcome

Bucket on `created_at`; segment by where the order ended up:

```sql
CASE
  WHEN o.status = 'COMPLETED' THEN 'Completed'
  WHEN o.status IN ('REJECTED','EXPIRED','FAILED_MANUAL') THEN 'Failed'
  ELSE 'In progress'
END
```

Every term is exact: `created_at`, `completed_at` and the current status all exist. No
proxy timestamp is used anywhere in this card.

### 7.2 Orders, aging backlog

An order's open interval is `[created_at, completed_at)`, left open when `completed_at`
is null:

```sql
FROM spine s
JOIN order_ o
  ON o.created_at <= s.h
 AND (o.completed_at IS NULL OR o.completed_at > s.h)
WHERE o.status NOT IN ('REJECTED','EXPIRED','FAILED_MANUAL')
```

The `WHERE` clause implements D6. `COMPLETED` orders always carry `completed_at`, and
non-terminal orders are genuinely still open, so within the remaining set the interval is
exact.

### 7.3 Completion percentiles

```sql
percentile_cont(ARRAY[0.50,0.75,0.90,0.95,0.99])
  WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (o.completed_at - o.paid_at)))
FROM order_ o
WHERE o.status = 'COMPLETED'
  AND o.paid_at IS NOT NULL
  AND o.completed_at >= now() - ({{days}} * INTERVAL '1 day')
GROUP BY partner_bucket
```

The result is unpivoted to `(percentile, partner, seconds)` for the grouped bar.
`paid_at → completed_at` is deliberately the same span the orchestrator's own metric
measures (`mail/resend/DeliveryVerdictApplier.java:125`).

### 7.4 Flow by hour

Four series, `UNION ALL`ed into `(hour, series, n)` and joined to the spine. Unlike the
cohort charts, this one uses true event times, because all four exist:

| Series | Source |
| --- | --- |
| requested | `production_request.created_at` |
| produced | `task.succeeded_at` where `purpose = 'PRODUCTION'` and `status = 'SUCCESS'` |
| sent | `certificate.sent_at` |
| ordered | `order_.created_at` |

### 7.5 Capacity now

Gates mirror `PlacementRepository.findPlaceableCapacity`: device `IDLE`, agent available,
device and active-account `validation_status = 'GOOD'`, no open `account_cooldown` row,
`held_at IS NULL`, `busy_backoff_until` elapsed, agent under its concurrency cap, and the
active account having a `current_number_id`.

Headroom then follows `PlacementCapacity.fits` at `minCertificateAmount = 1000`:

```
by_tasks         = 6      - account_count24h
by_account_money = floor((35000  - account_money24h) / 1000)
by_number_day    = floor((500000 - number_money24h)  / 1000)
by_number_month  = floor((600000 - number_money30d) / 1000)

capacity          = greatest(0, least(by_tasks, by_account_money,
                                      by_number_day, by_number_month))
capacity_balance  = least(capacity, floor(available_balance / 1000))
```

`available_balance` is `number.balance` minus open `NUMBER` reservations, the same
expression as `QuotaLedgerRepository.SQL_OPEN_NUMBER_RESERVATIONS`.

Two deliberate details:

- `bufferAccount = 5000` is **not** applied. It belongs to
  `QuotaService.evaluateExhaustion`, which drives device switching, not to
  `QuotaService.accountFits`, which is the admission rule capacity is asking about.
- Aggregation is two-level — summed per account, then capped per number — because
  `device.active_account_id` is not unique. Summing per device would double-count the
  headroom of an account carried by two devices.

### 7.6 Capacity by hour (upper bound)

Ignores fleet readiness, which is not historised. Titled as an upper bound on the card
itself.

Quota windows come from a range join against the spine, with the same status predicate
enforcement uses (`QuotaLedgerRepository.SQL_ENFORCED_STATUSES`):

```sql
FROM spine s
JOIN quota_ledger l
  ON l.subject_type = 'ACCOUNT'
 AND l.status IN ('RESERVED','COMMITTED')
 AND l.ts > s.h - INTERVAL '24 hours'
 AND l.ts <= s.h
GROUP BY s.h, l.subject_id
```

The `NUMBER` 24-hour and 30-day windows are built the same way. Cooldown at hour `h`:

```sql
applied_at <= s.h
AND (until      IS NULL OR until      > s.h)
AND (cleared_at IS NULL OR cleared_at > s.h)
```

Balance at hour `h` is the last `balance_ledger.balance_after` for that number at or
before `h`. The subject set is the accounts currently attached to a device with a
`current_number_id`.

### 7.7 Top-ups created by hour

`topup_payout` holds **one row per `(message_id, provider_code)`** — the provider-chain
cursor. A message that falls through three providers writes three rows for the *same*
money. Counting rows would report it as three top-ups.

"Created" therefore counts `DISTINCT message_id`, and the amount sums one row per
message (`DISTINCT ON (message_id) ORDER BY message_id, id`).

The in-flight KPIs (cards 6 and 7) keep counting **rows**, because that is precisely what
`TopUpInFlightService.inFlightAmount` /
`TopUpPayoutRepository.sumNonFinalAmountByNumberId` do, and only one row per message is
non-final at a time — the others have been retired to `REFUSED`, `EXHAUSTED`, `SPLIT` or
`SUPERSEDED`.

### 7.8 Top-ups aging

`topup_payout` carries `updated_at`, which orders lack, so the interval is
`[created_at, updated_at)` for rows that have reached a terminal status and
`[created_at, now())` for rows still in `CREATE_RETRY`/`PENDING_POLL`. `updated_at` is
bumped on every poll (`bumpPollAndReschedule`), so it means "last touch" — which for a
terminal row is exactly the moment it became terminal, and terminal rows are what the
reconstruction reads.

## 8. Risks

| # | Risk | Handling |
| --- | --- | --- |
| R1 | **Timezone skew.** `docker-compose.yaml` sets no `TZ` for Postgres, so naive `timestamp` columns are most likely UTC, while Metabase reports Europe/Moscow and buckets `timestamptz` columns in Moscow — a three-hour offset between the two families of cards. | **Blocking.** The first implementation step runs `SELECT now(), LOCALTIMESTAMP, current_setting('TimeZone')` against the instance. All SQL then normalises to one explicitly declared zone. |
| R2 | Capacity-by-hour cost scales with `days`: at `days = 30` the spine × ledger join reaches roughly 25M row pairs against a 30-second timeout. | Time the card during implementation; set `cache_ttl` on it; document a recommended `days <= 14` for that tab. |
| R3 | Quota limits are hard-coded into two cards and go stale silently if `app.quota` changes. | Both cards carry the limits in a single `limits` CTE, commented and cross-referenced to `application.yml`. |
| R4 | Native SQL loses Metabase's click-through drill-down on charts. | Accepted — the cost of a uniform `days` parameter across MBQL-inexpressible cards. |
| R5 | `apply` replaces a dashboard's entire `dashcards` array, so a dashcard added through the UI is deleted on the next run. | Standard repository workflow; `export --overwrite` first if a UI-added dashcard must be kept. |
| R6 | 1152 accounts × a 168-hour spine is a large intermediate result even for the 24-hour window. | `quota_ledger` is only 34.5k rows total, so the range join stays under roughly 5.8M pairs at `days = 7`. Measured before the tab is assembled. |

## 9. Verification

1. Resolve R1 before writing any SQL.
2. Author and time each of the four reconstruction cards individually against the live
   instance: orders aging (×2), capacity by hour, top-ups aging. Each must return well
   inside 30 seconds at `days = 7`.
3. `./mbc validate` — offline structural and reference checks.
4. `./mbc diff` — expect exit code `2` with 1 collection, 30 cards and 1 dashboard as
   creates, and **no** updates to any existing entity.
5. `./mbc apply --yes`.
6. Commit `.state/<host>.yaml` alongside the new files.

## 10. Out of scope

- Any change to the existing dashboard or its nine Russian cards.
- Adding terminal timestamps to `order_` or `production_request` in the orchestrator.
  This was considered and deferred: it would fix D5 and D6 properly but blocks this
  dashboard on a backend change.
- Historising fleet state so that capacity-by-hour could apply the real placement gates.
- Alerts, subscriptions and permissions, which the repository does not manage.
