# Pear Cider (automated) Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `Pear Cider by Erjan.Solutions (automated)` dashboard — one new collection, 30 new native-SQL cards and one four-tab dashboard driven by a single `days` filter — without touching the existing Russian dashboard or its nine cards.

**Architecture:** Every card is a native PostgreSQL query carried inside the MBQL-5 envelope (`lib/type: mbql.stage/native`), so percentiles, hour spines, age buckets and the placement-capacity formula are all expressible and diff readably. The dashboard declares one number parameter, `days`, mapped to the `{{days}}` template tag of the 14 time-windowed cards. Files are authored under `collections/`, `cards/` and `dashboards/` and pushed with `./mbc apply`; the state file records the server ids.

**Tech Stack:** Metabase v0.60.7 OSS (report timezone Europe/Moscow), PostgreSQL (orchestrator database, Metabase database id `2`), the repository's own `mbc` tool (Python 3.12 + PyYAML), YAML.

**Spec:** `docs/superpowers/specs/2026-08-18-pear-cider-automated-dashboard-design.md`

---

## Global Constraints

Every task's requirements implicitly include this section.

### Environment

- Metabase database id for every card: **`2`**.
- The Metabase DB role is `SELECT`-only with `statement_timeout = 30s`. Every card must finish well inside 30 s.
- The worktree has **no `.env`** of its own. Every command that talks to the instance must pass
  `--env-file ../../../.env` (the main checkout's `.env`, three levels up from this worktree).
  `./mbc validate` is offline and needs no `--env-file`.
- Command exit codes: `validate` → `0` clean / `1` problems. `diff` → `0` no difference / `2` difference / `1` error. Treat `2` from `diff` as "expected, review it".

### Timezone convention (spec R1 — RESOLVED, do not re-open)

Probed against the live instance on 2026-08-18:

```
SELECT now(), LOCALTIMESTAMP, current_setting('TimeZone')
  -> 2026-08-18T19:05:21+03:00 | 2026-08-18T19:05:21 | Europe/Moscow
SELECT max(sent_at) FROM certificate
  -> 2026-08-18T16:05:21          (three hours behind the Moscow wall clock)
SELECT max(created_at) FROM topup_payout
  -> 2026-08-18T19:03:58+03:00    (current)
```

So: **naive `timestamp` columns hold UTC**, `timestamptz` columns are correct absolute instants, and the
Metabase JDBC session runs in `Europe/Moscow`. Three rules, used verbatim in every card:

| Need | Write |
| --- | --- |
| "now" to compare against a naive (UTC) column | `(now() AT TIME ZONE 'UTC')` |
| "now" for the hour/day spine and for display | `(now() AT TIME ZONE 'Europe/Moscow')` |
| naive (UTC) column → Moscow wall clock | `(col AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')` |
| `timestamptz` column → Moscow wall clock | `(col AT TIME ZONE 'Europe/Moscow')` |

Naive columns: `order_.*`, `certificate.*`, `production_request.*`, `task.*`, `quota_ledger.ts`,
`account_cooldown.*`, `device.busy_backoff_until`, `agent.last_heartbeat_at`.
`timestamptz` columns: `topup_payout.created_at/updated_at`, `balance_ledger.created_at`,
`device.held_at`, `agent.disabled_at`, `number.last_demand_topup_at`, `account.validated_at`.

**Filter on the raw column against a converted constant** (`WHERE sent_at >= (now() AT TIME ZONE 'UTC') - ...`)
so the predicate stays index-friendly; convert only in `SELECT`/`GROUP BY` for display.

### The `days` window

Single dashboard parameter, id `821ec3b1`, type `number/=`, slug `days`, default `[7]`.
Card side, in the 14 time-windowed cards only:

```yaml
    template-tags:
      days:
        id: <the uuid this plan assigns to that card>
        name: days
        display-name: Days
        type: number
        default: '7'
```

Dashcard side, on those same 14 dashcards only:

```yaml
  parameter_mappings:
  - parameter_id: 821ec3b1
    card_id: <the card's server id, from the state file>
    target:
    - variable
    - - template-tag
      - days
```

**UPDATE (Task 1, verified live):** `card_id` was originally assumed omittable from the mapping, since
`metabase.parameters.schema/::parameter-mapping` (v0.60.7) declares it `{:optional true}`. Task 1 proved
this assumption wrong: with `card_id` omitted, `POST /api/dashboard/:id/dashcard/:id/card/:id/query` ran
the card with its *default* `days` value regardless of the `days` value passed in the request (both
`days=1` and `days=30` returned the same row count as the untouched default). Adding
`card_id: <card id>` to the mapping and re-applying fixed it — `days=1` then returned 2 rows and
`days=30` returned 24 rows, matching the card's raw SQL for those windows. **Every later dashcard's
`parameter_mappings` entry must include `card_id`, sourced from the state file after that card is
created**, not omitted as originally planned.

Window predicate, uniformly:

```sql
>= (now() AT TIME ZONE 'UTC') - ({{days}} * INTERVAL '1 day')      -- naive (UTC) column
>= now() - ({{days}} * INTERVAL '1 day')                            -- timestamptz column
```

### Verified schema and enum facts

Column names below are the live schema; do not guess others.

- `certificate(certificate_id, amount, status, source, reserved_order_id, created_at, reserved_at, sent_at, …)` — statuses in use: `FREE`, `SENT`.
- `order_(order_id, buyer_email, amount, status, payment_id, …, created_at, paid_at, completed_at, failure_reason, issuance_mode, client_id, …)`.
  `OrderStatus` = `CREATED, AWAITING_PAYMENT, PAID, SENDING, COMPLETED, WAITING_CERT, FAILED_MANUAL, REJECTED, EXPIRED`.
  The spec's in-progress set is the four the spec names (`AWAITING_PAYMENT, PAID, WAITING_CERT, SENDING`);
  `CREATED` is deliberately not counted there, per spec §6 card 5.
- `production_request(pr_id, amount, status, trigger, created_at, …)`. `ProductionRequestStatus` = `PENDING, PLACING, PRODUCING, AWAITING_INTAKE, CLOSED, FAILED`. A live snapshot may show zero `PENDING` rows — that is a healthy queue, not a broken card.
- `task(task_id, production_request_id, device_id, account_id, number_id, attempt_no, status, amount, started_at, timeout_at, failure_category, failure_reason, succeeded_at, purpose)`. `TaskStatus` = `PENDING, RESERVED, DISPATCHED, RUNNING, SUCCESS, FAILED`; `TaskPurpose` = `PRODUCTION, VALIDATION`.
- `topup_payout(id, message_id, number_id, phone_number, amount, task_id, provider_code, provider_order_id, external_payout_id, status, create_attempts, poll_count, next_poll_at, created_at, updated_at)`. `TopUpPayoutStatus` (all nine) = `CREATE_RETRY, REFUSED, EXHAUSTED, PENDING_POLL, COMPLETED, FAILED, PARKED, SPLIT, SUPERSEDED`.
- `quota_ledger(id, subject_type, subject_id, amount, ts, status, task_id)` — 34.6k rows.
- `balance_ledger(id, number_id, delta, balance_after, source, origin_id, provider_code, created_at)`.
- `account_cooldown(id, account_id, reason, applied_at, until, cleared_at, cleared_by)`.
- `device(device_id, agent_id, state, active_account_id, …, busy_backoff_until, …, validation_status, …, held_at, …)` — `state` in use: `IDLE`, `OFFLINE`; `validation_status` in use: `VALIDATED`, `NEW`, `NEEDS_REVIEW`.
- `agent(agent_id, status, last_heartbeat_at, max_concurrent_devices, name, token_hash, token_revoked_at, created_at, disabled_at)` — `status` in use: `ONLINE`, `OFFLINE`.
- `account(account_id, device_id, current_number_id, encrypted_pass, apple_id, validation_status, validation_reason, validated_at)` — account `validation_status` gate value is `GOOD` (**not** the device's `VALIDATED`).
- `number(number_id, is_active, phone_number, balance, last_demand_topup_at)`.
- `api_client(id, client_id, name, key_hash, status, created_at)` — nine clients: `sber`, `acme-partner`, `vpns`, `megafon`, `wildberries`, `tbank`, `mts`, `railly`, `gamepult`.

### Placement gates and quota limits (verified against orchestrator source)

`PlacementRepository.findPlaceableCapacity` composes exactly these predicates — reproduce them verbatim
(with `now()` replaced by `(now() AT TIME ZONE 'UTC')`, because those columns are naive UTC and the
orchestrator's own connection is not in Moscow):

```
d.state = 'IDLE'
a.status = 'ONLINE' AND a.disabled_at IS NULL                      -- AgentRepository.SQL_AGENT_AVAILABLE
d.validation_status = 'VALIDATED'                                  -- DeviceValidationStatus.SQL_DEVICE_PRODUCTION_GATE
(d.busy_backoff_until IS NULL OR d.busy_backoff_until <= now())    -- SQL_BUSY_BACKOFF_ELAPSED
acc.validation_status = 'GOOD'                                     -- AccountValidationStatus.SQL_ACCOUNT_PRODUCTION_GATE
NOT EXISTS (open account_cooldown row)                             -- AccountCooldownRepository.noOpenCooldown
(a.max_concurrent_devices = 0 OR (SELECT count(*) FROM device b WHERE b.agent_id = a.agent_id
   AND b.state NOT IN ('IDLE','OFFLINE','DISABLED')) < a.max_concurrent_devices)
d.held_at IS NULL                                                  -- SQL_DEVICE_NOT_HELD
```

Quota windows (`QuotaLedgerRepository`): `status IN ('RESERVED','COMMITTED')`; ACCOUNT window `ts > now - 24h`;
NUMBER windows `ts > now - 30 days` with a `FILTER (WHERE ts > now - 24h)` term for the 24 h money.
Open NUMBER reservations: `SUM(amount) WHERE subject_type='NUMBER' AND subject_id = n.number_id AND status='RESERVED'`.

Fit rule (`PlacementCapacity.fits`), at `amount = minCertificateAmount`:

```
availableBalance >= amount
accountCount24h + 1 <= maxTasksPerAccount
accountMoney24h + amount <= dayLimitAccount
numberMoney24h  + amount <= dayLimitNumber
numberMoney30d  + amount <= monthLimitNumber
```

`app.quota` values (orchestrator `src/main/resources/application.yml`, lines 159–167), carried in a single
commented `limits` CTE in both capacity cards (spec R3):
`maxTasksPerAccount: 6`, `dayLimitAccount: 35000`, `dayLimitNumber: 500000`, `monthLimitNumber: 600000`,
`minCertificateAmount: 1000`. `bufferAccount: 5000` is **not** applied (spec §7.5).

### Measured timings (live, `days = 7`)

`capacity now` 1.2 s · `completion percentiles` 0.4 s · `orders aging` 3.5 s · `top-ups aging` 1.9 s ·
`flow by hour` 0.5 s · `top-ups created by hour` 0.4 s · `capacity by hour` 5.9 s.
`capacity by hour` therefore carries `cache_ttl: 300`; the tab is documented as "keep `days` ≤ 14".

### House rules

- Card keys are all prefixed `pca-` so nothing collides with the existing card set (`certificates-in-pool` already exists).
- Collection for every new card: `pear-cider-automated`. No existing file is edited, ever.
- Age buckets are labelled with a two-digit sort prefix (`01 <1m` … `10 >3d`) so the stacked series order is stable.
- Money is whole roubles; `sum(...)` results are cast `::bigint` where a `numeric` would otherwise leak in.
- Every task ends with `./mbc diff` clean (exit `0`) and a commit that includes `.state/bi-apple-certificate-solutions-8443.yaml`.
- Commit style: `feat(dashboard): <what>`. Direct commits to `main` are allowed in this project.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `collections/pear-cider-automated.yaml` | The collection holding all 30 new cards. |
| `cards/pca-*.yaml` | One file per card; each carries its own SQL, display type and visualization settings. |
| `dashboards/pear-cider-by-erjan-solutions-automated.yaml` | Tabs, the `days` parameter, and the 30 dashcards with their grid positions. Grows one task at a time. |
| `.state/bi-apple-certificate-solutions-8443.yaml` | Written by `apply`; committed with every task. |
| `../mbsql.py` | Scratch helper (outside the repo, never committed) that runs one card's SQL against the live instance and times it. |

Card-key → spec-card map:

| # | Key | Tab | Windowed |
| --- | --- | --- | --- |
| 1 | `pca-certificate-face-value-by-day` | overview | yes |
| 2 | `pca-production-queue-depth` | overview | no |
| 3 | `pca-certificates-sent-window` | overview | yes |
| 4 | `pca-time-since-last-certificate` | overview | no |
| 5 | `pca-orders-in-progress` | overview | no |
| 6 | `pca-topups-in-progress-count` | overview | no |
| 7 | `pca-topups-in-progress-rub` | overview | no |
| 8 | `pca-capacity-now-quota` | overview | no |
| 9 | `pca-capacity-now-quota-balance` | overview | no |
| 10 | `pca-time-since-last-order-by-partner` | overview | no |
| 11 | `pca-flow-by-hour` | overview | yes |
| 12 | `pca-orders-by-hour-sber` | orders | yes |
| 13 | `pca-orders-by-hour-other` | orders | yes |
| 14 | `pca-orders-in-progress-by-status-partner` | orders | no |
| 15 | `pca-completion-time-percentiles` | orders | yes |
| 16 | `pca-orders-aging-sber` | orders | yes |
| 17 | `pca-orders-aging-other` | orders | yes |
| 18 | `pca-certificates-sent-by-hour` | orders | yes |
| 19 | `pca-sales-distribution-by-partner` | orders | no |
| 20 | `pca-average-check-by-day` | orders | yes |
| 21 | `pca-certificates-sold-by-denomination` | orders | no |
| 22 | `pca-certificate-turnover-by-denomination` | orders | no |
| 23 | `pca-production-requests-by-hour` | production | yes |
| 24 | `pca-certificates-in-pool-by-denomination` | production | no |
| 25 | `pca-production-queue-by-denomination` | production | no |
| 26 | `pca-pool-total-value` | production | no |
| 27 | `pca-capacity-by-hour` | production | yes |
| 28 | `pca-topups-created-by-hour` | topups | yes |
| 29 | `pca-topups-aging` | topups | yes |
| 30 | `pca-topups-by-status` | topups | no |

Template-tag uuids (one per windowed card, already generated — use these exact values):

| Card | uuid |
| --- | --- |
| 1 | `8f323b82-b255-4847-829a-3cbe13a6e4e9` |
| 3 | `fedaf608-cd94-4cbb-819a-7768d09c76c9` |
| 11 | `075963f3-60d4-4b8d-b8da-22a8ab74e6ac` |
| 12 | `822e0d63-f30e-44f9-b402-9a22f6d05e4c` |
| 13 | `6a2a8221-e790-4eef-aa8d-2c70378caa72` |
| 15 | `cbca1acb-eb40-481a-9168-a8ffbb8856e5` |
| 16 | `a6cd1738-5504-4c4f-9ffa-659418e68fec` |
| 17 | `bf00ff67-a05f-4aaf-8ccc-160c6cceef31` |
| 18 | `dbdb0727-f4bb-4aa6-9909-efd9a5d8ed5f` |
| 20 | `4b343176-a566-4071-80d2-21ceb298e6b5` |
| 23 | `99df7d5d-3788-4c0e-a410-c76641027be5` |
| 27 | `b7e3e38a-6980-41d1-867b-fdf430d0695e` |
| 28 | `17465025-1749-4282-83b0-d9fcb599bae9` |
| 29 | `4c3ded10-63bb-4af2-a75d-a5b849ef3ead` |

Grid layout (all satisfy `col + size_x <= 24`):

```
overview    row 0  col 0  24x8  face value by day
            row 8  col 0/6/12/18  6x3 each   prod queue | certs sent | last cert | orders in progress
            row 11 col 0/6/12/18  6x3 each   topups count | topups ₽ | capacity quota | capacity +balance
            row 14 col 0  24x5  time since last order, by partner
            row 19 col 0  24x7  flow by hour
orders      row 0  col 0/12  12x7 each  orders by hour Sber | Other
            row 7  col 0/12  12x6 each  in progress by status/partner | completion percentiles
            row 13 col 0/12  12x7 each  aging Sber | aging Other
            row 20 col 0     24x6       certificates sent by hour
            row 26 col 0/12  12x7 each  sales by partner | average check by day
            row 33 col 0/12  12x7 each  sold by denomination | turnover by denomination
production  row 0  col 0     24x7       production requests by hour
            row 7  col 0 9x7 | col 9 9x7 | col 18 6x7   pool by denomination | queue by denomination | pool total
            row 14 col 0     24x7       capacity by hour
topups      row 0  col 0     24x7       top-ups created by hour
            row 7  col 0 16x7 | col 16 8x7   aging | by status
```

---

### Task 1: Foundation — helper, collection, first card, dashboard skeleton

Proves the whole mechanism end to end: a native stage inside the MBQL-5 envelope, a `days` template tag,
a dashboard `number/=` parameter, and a dashcard mapping that carries no `card_id`.

**Files:**
- Create: `../mbsql.py` (outside the repo — not committed)
- Create: `collections/pear-cider-automated.yaml`
- Create: `cards/pca-certificate-face-value-by-day.yaml`
- Create: `dashboards/pear-cider-by-erjan-solutions-automated.yaml`
- Modify: `.state/bi-apple-certificate-solutions-8443.yaml` (written by `apply`)

**Interfaces:**
- Consumes: nothing.
- Produces: collection key `pear-cider-automated`; card key `pca-certificate-face-value-by-day`
  returning `(day timestamp, face_value bigint)`; dashboard key
  `pear-cider-by-erjan-solutions-automated` with tab keys `overview`, `orders`, `production`, `topups`
  and parameter id `821ec3b1`; the verified template-tag and parameter-mapping shapes every later task copies.

- [ ] **Step 1: Write the SQL helper**

Create `../mbsql.py` (one directory above the worktree, so git never sees it):

```python
#!/usr/bin/env python3
"""Run a card's native SQL against the live instance and report timing and first rows.

Usage, from the worktree root:
    python3 ../mbsql.py cards/pca-something.yaml [days]
    python3 ../mbsql.py --sql "SELECT 1 AS ok"
"""
import json
import os
import sys
import time

sys.path.insert(0, os.getcwd())

import yaml

from mbcode.client import Client
from mbcode.config import load_config

ENV_FILE = os.environ.get("MBC_ENV_FILE", "../../../.env")
DATABASE = 2


def card_sql(path, days):
    doc = yaml.safe_load(open(path, encoding="utf-8"))
    native = doc["dataset_query"]["stages"][0]["native"]
    return native.replace("{{days}}", str(days))


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    if args[0] == "--sql":
        sql = args[1]
    else:
        sql = card_sql(args[0], args[1] if len(args) > 1 else "7")
    client = Client(load_config(ENV_FILE))
    body = {"database": DATABASE, "lib/type": "mbql/query",
            "stages": [{"lib/type": "mbql.stage/native", "native": sql}]}
    started = time.time()
    resp = client.post("/api/dataset", body)
    elapsed = int((time.time() - started) * 1000)
    if resp.get("status") != "completed":
        print(json.dumps(resp)[:2000])
        sys.exit(1)
    data = resp["data"]
    print(f"{elapsed} ms, {len(data['rows'])} rows")
    print([col["name"] for col in data["cols"]])
    for row in data["rows"][:10]:
        print(row)


main()
```

- [ ] **Step 2: Prove the helper reaches the instance**

Run: `python3 ../mbsql.py --sql "SELECT 1 AS ok"`
Expected: `... ms, 1 rows`, then `['ok']`, then `[1]`.

- [ ] **Step 3: Run card 1's SQL before writing the card**

Run:

```bash
python3 ../mbsql.py --sql "
SELECT date_trunc('day', (c.sent_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')) AS day,
       sum(c.amount)::bigint                                                          AS face_value
FROM certificate c
WHERE c.status = 'SENT'
  AND c.sent_at >= (now() AT TIME ZONE 'UTC') - (7 * INTERVAL '1 day')
GROUP BY 1
ORDER BY 1"
```

Expected: under 1 s, one row per day of the last week, `['day', 'face_value']`.

- [ ] **Step 4: Write the collection file**

`collections/pear-cider-automated.yaml`:

```yaml
kind: collection
key: pear-cider-automated
name: Pear Cider (automated)
description: English card set behind the "Pear Cider by Erjan.Solutions (automated)" dashboard.
parent: root
archived: false
```

- [ ] **Step 5: Write card 1**

`cards/pca-certificate-face-value-by-day.yaml`:

```yaml
kind: card
key: pca-certificate-face-value-by-day
name: Certificate face value by day
description: Sum of SENT certificate face values per day, over the last {{days}} days.
collection: pear-cider-automated
type: question
display: bar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Days are Europe/Moscow days; certificate.sent_at is naive UTC.
      SELECT date_trunc('day', (c.sent_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')) AS day,
             sum(c.amount)::bigint                                                          AS face_value
      FROM certificate c
      WHERE c.status = 'SENT'
        AND c.sent_at >= (now() AT TIME ZONE 'UTC') - ({{days}} * INTERVAL '1 day')
      GROUP BY 1
      ORDER BY 1
    template-tags:
      days:
        id: 8f323b82-b255-4847-829a-3cbe13a6e4e9
        name: days
        display-name: Days
        type: number
        default: '7'
visualization_settings:
  graph.dimensions:
  - day
  graph.metrics:
  - face_value
  graph.x_axis.scale: timeseries
  graph.show_values: true
  graph.show_trendline: true
parameters: []
```

- [ ] **Step 6: Write the dashboard skeleton**

`dashboards/pear-cider-by-erjan-solutions-automated.yaml`:

```yaml
kind: dashboard
key: pear-cider-by-erjan-solutions-automated
name: Pear Cider by Erjan.Solutions (automated)
description: English rebuild of the Pear Cider dashboard plus operational metrics. The Days filter drives every time-windowed card.
collection: pear-cider-automated
width: fixed
auto_apply_filters: true
cache_ttl: null
archived: false
parameters:
- id: 821ec3b1
  name: Days
  slug: days
  sectionId: number
  type: number/=
  default:
  - 7
tabs:
- key: overview
  name: Overview
- key: orders
  name: Orders
- key: production
  name: Production & Capacity
- key: topups
  name: Top-ups
dashcards:
- key: dc-certificate-face-value-by-day
  card: pca-certificate-face-value-by-day
  tab: overview
  row: 0
  col: 0
  size_x: 24
  size_y: 8
  visualization_settings: {}
  parameter_mappings:
  - parameter_id: 821ec3b1
    target:
    - variable
    - - template-tag
      - days
  series: []
  inline_parameters: []
```

- [ ] **Step 7: Validate offline**

Run: `./mbc validate`
Expected: no output, exit `0`.

- [ ] **Step 8: Diff against the instance**

Run: `./mbc diff --env-file ../../../.env`
Expected: exit `2`; the plan lists exactly 1 collection create, 1 card create and 1 dashboard create,
and **no updates** to any existing entity. If any existing card or dashboard appears as an update, stop
and fix the file — the existing dashboard must not be touched.

- [ ] **Step 9: Apply**

Run: `./mbc apply --yes --env-file ../../../.env`
Expected: `applied 3 change(s); state saved to .state/bi-apple-certificate-solutions-8443.yaml`.

- [ ] **Step 10: Prove the round trip is clean**

Run: `./mbc diff --env-file ../../../.env`
Expected: exit `0`, "no differences".
If the diff instead reports an update to the card or the dashboard, the server has normalised something
the file omits (most likely a template-tag or parameter-mapping key). In that case run
`./mbc export --overwrite --env-file ../../../.env`, inspect the two regenerated files, adopt whatever the
server added into the file shape, and use that adopted shape for every later card. Re-run the diff until
it exits `0`.

- [ ] **Step 11: Prove the `days` parameter actually reaches the card**

Read the dashboard and dashcard ids from the state file, then run the dashcard query twice:

```bash
DASH=$(python3 -c "import yaml;d=yaml.safe_load(open('.state/bi-apple-certificate-solutions-8443.yaml'));print(d['dashboards']['pear-cider-by-erjan-solutions-automated']['id'])")
DC=$(python3 -c "import yaml;d=yaml.safe_load(open('.state/bi-apple-certificate-solutions-8443.yaml'));print(d['dashboards']['pear-cider-by-erjan-solutions-automated']['dashcards']['dc-certificate-face-value-by-day'])")
CARD=$(python3 -c "import yaml;d=yaml.safe_load(open('.state/bi-apple-certificate-solutions-8443.yaml'));print(d['cards']['pca-certificate-face-value-by-day']['id'])")
echo "$DASH $DC $CARD"
```

Then, with the same `Client` the helper uses:

```bash
python3 - <<'PY'
import os, sys, yaml
sys.path.insert(0, os.getcwd())
from mbcode.client import Client
from mbcode.config import load_config

state = yaml.safe_load(open(".state/bi-apple-certificate-solutions-8443.yaml"))
dash = state["dashboards"]["pear-cider-by-erjan-solutions-automated"]
dash_id, card_id = dash["id"], state["cards"]["pca-certificate-face-value-by-day"]["id"]
dc_id = dash["dashcards"]["dc-certificate-face-value-by-day"]
client = Client(load_config("../../../.env"))
path = f"/api/dashboard/{dash_id}/dashcard/{dc_id}/card/{card_id}/query"
for days in (1, 30):
    body = {"parameters": [{"id": "821ec3b1", "type": "number/=",
                            "target": ["variable", ["template-tag", "days"]],
                            "value": [days]}]}
    rows = client.post(path, body)["data"]["rows"]
    print(days, "days ->", len(rows), "rows")
PY
```

Expected: `1 days -> 1 rows` (or 2) and `30 days -> ~30 rows` — different row counts prove the parameter
is applied. If both calls return the same count, add `card_id: <card id>` to the dashcard's
`parameter_mappings` entry in the YAML, re-apply, re-run this step, and note in the plan that every later
dashcard mapping needs `card_id` too.

- [ ] **Step 12: Commit**

```bash
git add collections/pear-cider-automated.yaml \
        cards/pca-certificate-face-value-by-day.yaml \
        dashboards/pear-cider-by-erjan-solutions-automated.yaml \
        .state/bi-apple-certificate-solutions-8443.yaml \
        docs/superpowers/plans/2026-08-18-pear-cider-automated-dashboard.md
git commit -m "feat(dashboard): Pear Cider (automated) collection, dashboard skeleton and first card"
```

---

### Task 2: Overview KPI scalars (cards 2–7)

**Files:**
- Create: `cards/pca-production-queue-depth.yaml`, `cards/pca-certificates-sent-window.yaml`,
  `cards/pca-time-since-last-certificate.yaml`, `cards/pca-orders-in-progress.yaml`,
  `cards/pca-topups-in-progress-count.yaml`, `cards/pca-topups-in-progress-rub.yaml`
- Modify: `dashboards/pear-cider-by-erjan-solutions-automated.yaml` (append six dashcards)

**Interfaces:**
- Consumes: collection `pear-cider-automated`, dashboard tab `overview`, parameter id `821ec3b1`.
- Produces: card keys `pca-production-queue-depth` `(pending_requests bigint)`,
  `pca-certificates-sent-window` `(certificates_sent bigint)`,
  `pca-time-since-last-certificate` `(minutes bigint)`,
  `pca-orders-in-progress` `(orders_in_progress bigint)`,
  `pca-topups-in-progress-count` `(topups_in_progress bigint)`,
  `pca-topups-in-progress-rub` `(topups_in_progress_rub bigint)`.

- [ ] **Step 1: Run the six queries first**

```bash
python3 ../mbsql.py --sql "SELECT count(*) AS pending_requests FROM production_request WHERE status = 'PENDING'"
python3 ../mbsql.py --sql "SELECT count(*) AS certificates_sent FROM certificate WHERE status = 'SENT' AND sent_at >= (now() AT TIME ZONE 'UTC') - (7 * INTERVAL '1 day')"
python3 ../mbsql.py --sql "SELECT floor(EXTRACT(EPOCH FROM ((now() AT TIME ZONE 'UTC') - max(sent_at))) / 60)::bigint AS minutes FROM certificate WHERE status = 'SENT'"
python3 ../mbsql.py --sql "SELECT count(*) AS orders_in_progress FROM order_ WHERE status IN ('AWAITING_PAYMENT','PAID','WAITING_CERT','SENDING')"
python3 ../mbsql.py --sql "SELECT count(*) AS topups_in_progress FROM topup_payout WHERE status IN ('CREATE_RETRY','PENDING_POLL')"
python3 ../mbsql.py --sql "SELECT COALESCE(sum(amount), 0)::bigint AS topups_in_progress_rub FROM topup_payout WHERE status IN ('CREATE_RETRY','PENDING_POLL')"
```

Expected: each returns 1 row, under 1 s. `pending_requests` may legitimately be `0`.
`minutes` must be a small number (single or double digits) — a value near `180` would mean the timezone
rule was applied wrongly.

- [ ] **Step 2: Write the six card files**

`cards/pca-production-queue-depth.yaml`:

```yaml
kind: card
key: pca-production-queue-depth
name: Production queue depth
description: Production requests still waiting to be placed (status PENDING).
collection: pear-cider-automated
type: question
display: scalar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      SELECT count(*) AS pending_requests
      FROM production_request
      WHERE status = 'PENDING'
visualization_settings: {}
parameters: []
```

`cards/pca-certificates-sent-window.yaml`:

```yaml
kind: card
key: pca-certificates-sent-window
name: Certificates sent (last N days)
description: Certificates sent inside the Days window.
collection: pear-cider-automated
type: question
display: scalar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      SELECT count(*) AS certificates_sent
      FROM certificate
      WHERE status = 'SENT'
        AND sent_at >= (now() AT TIME ZONE 'UTC') - ({{days}} * INTERVAL '1 day')
    template-tags:
      days:
        id: fedaf608-cd94-4cbb-819a-7768d09c76c9
        name: days
        display-name: Days
        type: number
        default: '7'
visualization_settings: {}
parameters: []
```

`cards/pca-time-since-last-certificate.yaml`:

```yaml
kind: card
key: pca-time-since-last-certificate
name: Time since last certificate sent
description: Whole minutes since the most recent SENT certificate.
collection: pear-cider-automated
type: question
display: scalar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- certificate.sent_at is naive UTC, so "now" must be naive UTC too.
      SELECT floor(EXTRACT(EPOCH FROM ((now() AT TIME ZONE 'UTC') - max(sent_at))) / 60)::bigint AS minutes
      FROM certificate
      WHERE status = 'SENT'
visualization_settings:
  column_settings:
    '["name","minutes"]':
      suffix: ' min'
parameters: []
```

`cards/pca-orders-in-progress.yaml`:

```yaml
kind: card
key: pca-orders-in-progress
name: Orders in progress
description: Orders in a non-terminal state, all partners, including AWAITING_PAYMENT.
collection: pear-cider-automated
type: question
display: scalar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      SELECT count(*) AS orders_in_progress
      FROM order_
      WHERE status IN ('AWAITING_PAYMENT', 'PAID', 'WAITING_CERT', 'SENDING')
visualization_settings: {}
parameters: []
```

`cards/pca-topups-in-progress-count.yaml`:

```yaml
kind: card
key: pca-topups-in-progress-count
name: Top-ups in progress (count)
description: Non-final payout rows, the same predicate TopUpInFlightService uses.
collection: pear-cider-automated
type: question
display: scalar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Rows, not messages: TopUpPayoutRepository.sumNonFinalAmountByNumberId counts rows,
      -- and only one row per message is non-final at a time.
      SELECT count(*) AS topups_in_progress
      FROM topup_payout
      WHERE status IN ('CREATE_RETRY', 'PENDING_POLL')
visualization_settings: {}
parameters: []
```

`cards/pca-topups-in-progress-rub.yaml`:

```yaml
kind: card
key: pca-topups-in-progress-rub
name: Top-ups in progress (₽)
description: Money in flight, mirroring TopUpInFlightService.inFlightAmount.
collection: pear-cider-automated
type: question
display: scalar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      SELECT COALESCE(sum(amount), 0)::bigint AS topups_in_progress_rub
      FROM topup_payout
      WHERE status IN ('CREATE_RETRY', 'PENDING_POLL')
visualization_settings: {}
parameters: []
```

- [ ] **Step 3: Append six dashcards**

Append to `dashcards:` in `dashboards/pear-cider-by-erjan-solutions-automated.yaml`:

```yaml
- key: dc-production-queue-depth
  card: pca-production-queue-depth
  tab: overview
  row: 8
  col: 0
  size_x: 6
  size_y: 3
  visualization_settings: {}
  parameter_mappings: []
  series: []
  inline_parameters: []
- key: dc-certificates-sent-window
  card: pca-certificates-sent-window
  tab: overview
  row: 8
  col: 6
  size_x: 6
  size_y: 3
  visualization_settings: {}
  parameter_mappings:
  - parameter_id: 821ec3b1
    target:
    - variable
    - - template-tag
      - days
  series: []
  inline_parameters: []
- key: dc-time-since-last-certificate
  card: pca-time-since-last-certificate
  tab: overview
  row: 8
  col: 12
  size_x: 6
  size_y: 3
  visualization_settings: {}
  parameter_mappings: []
  series: []
  inline_parameters: []
- key: dc-orders-in-progress
  card: pca-orders-in-progress
  tab: overview
  row: 8
  col: 18
  size_x: 6
  size_y: 3
  visualization_settings: {}
  parameter_mappings: []
  series: []
  inline_parameters: []
- key: dc-topups-in-progress-count
  card: pca-topups-in-progress-count
  tab: overview
  row: 11
  col: 0
  size_x: 6
  size_y: 3
  visualization_settings: {}
  parameter_mappings: []
  series: []
  inline_parameters: []
- key: dc-topups-in-progress-rub
  card: pca-topups-in-progress-rub
  tab: overview
  row: 11
  col: 6
  size_x: 6
  size_y: 3
  visualization_settings: {}
  parameter_mappings: []
  series: []
  inline_parameters: []
```

- [ ] **Step 4: Validate, diff, apply, re-diff**

```bash
./mbc validate
./mbc diff --env-file ../../../.env      # exit 2: 6 card creates, 1 dashboard update, no other updates
./mbc apply --yes --env-file ../../../.env
./mbc diff --env-file ../../../.env      # exit 0
```

- [ ] **Step 5: Commit**

```bash
git add cards/pca-production-queue-depth.yaml cards/pca-certificates-sent-window.yaml \
        cards/pca-time-since-last-certificate.yaml cards/pca-orders-in-progress.yaml \
        cards/pca-topups-in-progress-count.yaml cards/pca-topups-in-progress-rub.yaml \
        dashboards/pear-cider-by-erjan-solutions-automated.yaml \
        .state/bi-apple-certificate-solutions-8443.yaml
git commit -m "feat(dashboard): overview KPI scalars for Pear Cider (automated)"
```

---

### Task 3: Capacity now (cards 8–9)

The two scalars that reproduce the orchestrator's admission rule. Both carry the `limits` CTE (spec R3).

**Files:**
- Create: `cards/pca-capacity-now-quota.yaml`, `cards/pca-capacity-now-quota-balance.yaml`
- Modify: `dashboards/pear-cider-by-erjan-solutions-automated.yaml` (append two dashcards)

**Interfaces:**
- Consumes: collection `pear-cider-automated`, tab `overview`.
- Produces: `pca-capacity-now-quota` `(capacity_now bigint)`,
  `pca-capacity-now-quota-balance` `(capacity_now_with_balance bigint)`.

- [ ] **Step 1: Run the capacity query first**

Save the SQL below to `../capacity-probe.sql` and run
`python3 ../mbsql.py --sql "$(cat ../capacity-probe.sql)"`:

```sql
WITH limits AS (
  SELECT 6::bigint      AS max_tasks_per_account,
         35000::bigint  AS day_limit_account,
         500000::bigint AS day_limit_number,
         600000::bigint AS month_limit_number,
         1000::bigint   AS min_certificate_amount
),
placeable AS (
  SELECT DISTINCT d.active_account_id AS account_id,
         acc.current_number_id        AS number_id,
         n.balance - COALESCE((SELECT sum(l.amount) FROM quota_ledger l
                               WHERE l.subject_type = 'NUMBER'
                                 AND l.subject_id = n.number_id
                                 AND l.status = 'RESERVED'), 0) AS available_balance
  FROM device d
  JOIN agent a     ON a.agent_id = d.agent_id
  JOIN account acc ON acc.account_id = d.active_account_id
  JOIN number n    ON n.number_id = acc.current_number_id
  WHERE d.state = 'IDLE'
    AND a.status = 'ONLINE' AND a.disabled_at IS NULL
    AND d.validation_status = 'VALIDATED'
    AND (d.busy_backoff_until IS NULL OR d.busy_backoff_until <= (now() AT TIME ZONE 'UTC'))
    AND acc.validation_status = 'GOOD'
    AND NOT EXISTS (SELECT 1 FROM account_cooldown c
                    WHERE c.account_id = d.active_account_id
                      AND c.cleared_at IS NULL
                      AND (c.until IS NULL OR c.until > (now() AT TIME ZONE 'UTC')))
    AND (a.max_concurrent_devices = 0
         OR (SELECT count(*) FROM device b
             WHERE b.agent_id = a.agent_id
               AND b.state NOT IN ('IDLE', 'OFFLINE', 'DISABLED')) < a.max_concurrent_devices)
    AND d.held_at IS NULL
),
account_window AS (
  SELECT subject_id, count(*) AS count24h, COALESCE(sum(amount), 0) AS money24h
  FROM quota_ledger
  WHERE subject_type = 'ACCOUNT' AND status IN ('RESERVED', 'COMMITTED')
    AND ts > (now() AT TIME ZONE 'UTC') - INTERVAL '24 hours'
  GROUP BY subject_id
),
number_window AS (
  SELECT subject_id,
         COALESCE(sum(amount) FILTER (WHERE ts > (now() AT TIME ZONE 'UTC') - INTERVAL '24 hours'), 0) AS money24h,
         COALESCE(sum(amount), 0) AS money30d
  FROM quota_ledger
  WHERE subject_type = 'NUMBER' AND status IN ('RESERVED', 'COMMITTED')
    AND ts > (now() AT TIME ZONE 'UTC') - INTERVAL '30 days'
  GROUP BY subject_id
),
account_headroom AS (
  SELECT p.number_id, p.available_balance,
         greatest(0, least(lim.max_tasks_per_account - COALESCE(aw.count24h, 0),
                           floor((lim.day_limit_account - COALESCE(aw.money24h, 0))
                                 / lim.min_certificate_amount))) AS tasks_possible
  FROM placeable p
  CROSS JOIN limits lim
  LEFT JOIN account_window aw ON aw.subject_id = p.account_id
),
number_headroom AS (
  SELECT ah.number_id,
         sum(ah.tasks_possible)     AS tasks_from_accounts,
         max(ah.available_balance)  AS available_balance
  FROM account_headroom ah
  GROUP BY ah.number_id
)
SELECT COALESCE(sum(greatest(0, least(
         nh.tasks_from_accounts,
         floor((lim.day_limit_number   - COALESCE(nw.money24h, 0)) / lim.min_certificate_amount),
         floor((lim.month_limit_number - COALESCE(nw.money30d, 0)) / lim.min_certificate_amount)))), 0)::bigint
         AS capacity_now,
       COALESCE(sum(greatest(0, least(
         nh.tasks_from_accounts,
         floor((lim.day_limit_number   - COALESCE(nw.money24h, 0)) / lim.min_certificate_amount),
         floor((lim.month_limit_number - COALESCE(nw.money30d, 0)) / lim.min_certificate_amount),
         floor(nh.available_balance / lim.min_certificate_amount)))), 0)::bigint
         AS capacity_now_with_balance
FROM number_headroom nh
CROSS JOIN limits lim
LEFT JOIN number_window nw ON nw.subject_id = nh.number_id
```

Expected: one row, roughly 1.2 s, two positive integers with
`capacity_now_with_balance <= capacity_now` (measured 330 / 319 on 2026-08-18).

- [ ] **Step 2: Write card 8**

`cards/pca-capacity-now-quota.yaml` — the query above with the final `SELECT` reduced to the first
column only:

```yaml
kind: card
key: pca-capacity-now-quota
name: Capacity now — quota + cooldown
description: Certificates the fleet could take right now under the placement gates and the quota windows, ignoring number balance.
collection: pear-cider-automated
type: question
display: scalar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Gates mirror PlacementRepository.findPlaceableCapacity; the fit rule mirrors
      -- PlacementCapacity.fits at amount = app.quota.minCertificateAmount.
      -- Aggregation is two-level (per account, then capped per number) because
      -- device.active_account_id is not unique.
      WITH limits AS (
        -- orchestrator/src/main/resources/application.yml, app.quota (lines 159-167).
        -- bufferAccount is deliberately NOT applied: it belongs to evaluateExhaustion,
        -- not to the admission rule this card reports.
        SELECT 6::bigint      AS max_tasks_per_account,   -- maxTasksPerAccount
               35000::bigint  AS day_limit_account,       -- dayLimitAccount
               500000::bigint AS day_limit_number,        -- dayLimitNumber
               600000::bigint AS month_limit_number,      -- monthLimitNumber
               1000::bigint   AS min_certificate_amount   -- minCertificateAmount
      ),
      placeable AS (
        SELECT DISTINCT d.active_account_id AS account_id,
               acc.current_number_id        AS number_id,
               n.balance - COALESCE((SELECT sum(l.amount) FROM quota_ledger l
                                     WHERE l.subject_type = 'NUMBER'
                                       AND l.subject_id = n.number_id
                                       AND l.status = 'RESERVED'), 0) AS available_balance
        FROM device d
        JOIN agent a     ON a.agent_id = d.agent_id
        JOIN account acc ON acc.account_id = d.active_account_id
        JOIN number n    ON n.number_id = acc.current_number_id
        WHERE d.state = 'IDLE'
          AND a.status = 'ONLINE' AND a.disabled_at IS NULL
          AND d.validation_status = 'VALIDATED'
          AND (d.busy_backoff_until IS NULL OR d.busy_backoff_until <= (now() AT TIME ZONE 'UTC'))
          AND acc.validation_status = 'GOOD'
          AND NOT EXISTS (SELECT 1 FROM account_cooldown c
                          WHERE c.account_id = d.active_account_id
                            AND c.cleared_at IS NULL
                            AND (c.until IS NULL OR c.until > (now() AT TIME ZONE 'UTC')))
          AND (a.max_concurrent_devices = 0
               OR (SELECT count(*) FROM device b
                   WHERE b.agent_id = a.agent_id
                     AND b.state NOT IN ('IDLE', 'OFFLINE', 'DISABLED')) < a.max_concurrent_devices)
          AND d.held_at IS NULL
      ),
      account_window AS (
        SELECT subject_id, count(*) AS count24h, COALESCE(sum(amount), 0) AS money24h
        FROM quota_ledger
        WHERE subject_type = 'ACCOUNT' AND status IN ('RESERVED', 'COMMITTED')
          AND ts > (now() AT TIME ZONE 'UTC') - INTERVAL '24 hours'
        GROUP BY subject_id
      ),
      number_window AS (
        SELECT subject_id,
               COALESCE(sum(amount) FILTER (WHERE ts > (now() AT TIME ZONE 'UTC') - INTERVAL '24 hours'), 0) AS money24h,
               COALESCE(sum(amount), 0) AS money30d
        FROM quota_ledger
        WHERE subject_type = 'NUMBER' AND status IN ('RESERVED', 'COMMITTED')
          AND ts > (now() AT TIME ZONE 'UTC') - INTERVAL '30 days'
        GROUP BY subject_id
      ),
      account_headroom AS (
        SELECT p.number_id,
               greatest(0, least(lim.max_tasks_per_account - COALESCE(aw.count24h, 0),
                                 floor((lim.day_limit_account - COALESCE(aw.money24h, 0))
                                       / lim.min_certificate_amount))) AS tasks_possible
        FROM placeable p
        CROSS JOIN limits lim
        LEFT JOIN account_window aw ON aw.subject_id = p.account_id
      ),
      number_headroom AS (
        SELECT ah.number_id, sum(ah.tasks_possible) AS tasks_from_accounts
        FROM account_headroom ah
        GROUP BY ah.number_id
      )
      SELECT COALESCE(sum(greatest(0, least(
               nh.tasks_from_accounts,
               floor((lim.day_limit_number   - COALESCE(nw.money24h, 0)) / lim.min_certificate_amount),
               floor((lim.month_limit_number - COALESCE(nw.money30d, 0)) / lim.min_certificate_amount)))), 0)::bigint
             AS capacity_now
      FROM number_headroom nh
      CROSS JOIN limits lim
      LEFT JOIN number_window nw ON nw.subject_id = nh.number_id
visualization_settings: {}
parameters: []
```

- [ ] **Step 3: Write card 9**

`cards/pca-capacity-now-quota-balance.yaml` — identical, except that `account_headroom` also carries
`p.available_balance`, `number_headroom` carries `max(ah.available_balance) AS available_balance`, the
final `least(...)` gains `floor(nh.available_balance / lim.min_certificate_amount)`, and the output column
is `capacity_now_with_balance`:

```yaml
kind: card
key: pca-capacity-now-quota-balance
name: Capacity now — quota + cooldown + balance
description: The same admission rule as the quota card, additionally capped by each number's available balance (balance minus open NUMBER reservations).
collection: pear-cider-automated
type: question
display: scalar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Gates mirror PlacementRepository.findPlaceableCapacity; the fit rule mirrors
      -- PlacementCapacity.fits at amount = app.quota.minCertificateAmount.
      -- available_balance is QuotaLedgerRepository.SQL_OPEN_NUMBER_RESERVATIONS subtracted
      -- from number.balance, the same expression placement itself uses.
      WITH limits AS (
        -- orchestrator/src/main/resources/application.yml, app.quota (lines 159-167).
        SELECT 6::bigint      AS max_tasks_per_account,   -- maxTasksPerAccount
               35000::bigint  AS day_limit_account,       -- dayLimitAccount
               500000::bigint AS day_limit_number,        -- dayLimitNumber
               600000::bigint AS month_limit_number,      -- monthLimitNumber
               1000::bigint   AS min_certificate_amount   -- minCertificateAmount
      ),
      placeable AS (
        SELECT DISTINCT d.active_account_id AS account_id,
               acc.current_number_id        AS number_id,
               n.balance - COALESCE((SELECT sum(l.amount) FROM quota_ledger l
                                     WHERE l.subject_type = 'NUMBER'
                                       AND l.subject_id = n.number_id
                                       AND l.status = 'RESERVED'), 0) AS available_balance
        FROM device d
        JOIN agent a     ON a.agent_id = d.agent_id
        JOIN account acc ON acc.account_id = d.active_account_id
        JOIN number n    ON n.number_id = acc.current_number_id
        WHERE d.state = 'IDLE'
          AND a.status = 'ONLINE' AND a.disabled_at IS NULL
          AND d.validation_status = 'VALIDATED'
          AND (d.busy_backoff_until IS NULL OR d.busy_backoff_until <= (now() AT TIME ZONE 'UTC'))
          AND acc.validation_status = 'GOOD'
          AND NOT EXISTS (SELECT 1 FROM account_cooldown c
                          WHERE c.account_id = d.active_account_id
                            AND c.cleared_at IS NULL
                            AND (c.until IS NULL OR c.until > (now() AT TIME ZONE 'UTC')))
          AND (a.max_concurrent_devices = 0
               OR (SELECT count(*) FROM device b
                   WHERE b.agent_id = a.agent_id
                     AND b.state NOT IN ('IDLE', 'OFFLINE', 'DISABLED')) < a.max_concurrent_devices)
          AND d.held_at IS NULL
      ),
      account_window AS (
        SELECT subject_id, count(*) AS count24h, COALESCE(sum(amount), 0) AS money24h
        FROM quota_ledger
        WHERE subject_type = 'ACCOUNT' AND status IN ('RESERVED', 'COMMITTED')
          AND ts > (now() AT TIME ZONE 'UTC') - INTERVAL '24 hours'
        GROUP BY subject_id
      ),
      number_window AS (
        SELECT subject_id,
               COALESCE(sum(amount) FILTER (WHERE ts > (now() AT TIME ZONE 'UTC') - INTERVAL '24 hours'), 0) AS money24h,
               COALESCE(sum(amount), 0) AS money30d
        FROM quota_ledger
        WHERE subject_type = 'NUMBER' AND status IN ('RESERVED', 'COMMITTED')
          AND ts > (now() AT TIME ZONE 'UTC') - INTERVAL '30 days'
        GROUP BY subject_id
      ),
      account_headroom AS (
        SELECT p.number_id, p.available_balance,
               greatest(0, least(lim.max_tasks_per_account - COALESCE(aw.count24h, 0),
                                 floor((lim.day_limit_account - COALESCE(aw.money24h, 0))
                                       / lim.min_certificate_amount))) AS tasks_possible
        FROM placeable p
        CROSS JOIN limits lim
        LEFT JOIN account_window aw ON aw.subject_id = p.account_id
      ),
      number_headroom AS (
        SELECT ah.number_id,
               sum(ah.tasks_possible)    AS tasks_from_accounts,
               max(ah.available_balance) AS available_balance
        FROM account_headroom ah
        GROUP BY ah.number_id
      )
      SELECT COALESCE(sum(greatest(0, least(
               nh.tasks_from_accounts,
               floor((lim.day_limit_number   - COALESCE(nw.money24h, 0)) / lim.min_certificate_amount),
               floor((lim.month_limit_number - COALESCE(nw.money30d, 0)) / lim.min_certificate_amount),
               floor(nh.available_balance / lim.min_certificate_amount)))), 0)::bigint
             AS capacity_now_with_balance
      FROM number_headroom nh
      CROSS JOIN limits lim
      LEFT JOIN number_window nw ON nw.subject_id = nh.number_id
visualization_settings: {}
parameters: []
```

- [ ] **Step 4: Run both card files through the helper**

```bash
python3 ../mbsql.py cards/pca-capacity-now-quota.yaml
python3 ../mbsql.py cards/pca-capacity-now-quota-balance.yaml
```

Expected: one row each, under 2 s, and the balance-capped number is the smaller of the two.

- [ ] **Step 5: Append two dashcards**

```yaml
- key: dc-capacity-now-quota
  card: pca-capacity-now-quota
  tab: overview
  row: 11
  col: 12
  size_x: 6
  size_y: 3
  visualization_settings: {}
  parameter_mappings: []
  series: []
  inline_parameters: []
- key: dc-capacity-now-quota-balance
  card: pca-capacity-now-quota-balance
  tab: overview
  row: 11
  col: 18
  size_x: 6
  size_y: 3
  visualization_settings: {}
  parameter_mappings: []
  series: []
  inline_parameters: []
```

- [ ] **Step 6: Validate, diff, apply, re-diff**

```bash
./mbc validate
./mbc diff --env-file ../../../.env      # exit 2: 2 card creates, 1 dashboard update
./mbc apply --yes --env-file ../../../.env
./mbc diff --env-file ../../../.env      # exit 0
```

- [ ] **Step 7: Commit**

```bash
git add cards/pca-capacity-now-quota.yaml cards/pca-capacity-now-quota-balance.yaml \
        dashboards/pear-cider-by-erjan-solutions-automated.yaml \
        .state/bi-apple-certificate-solutions-8443.yaml
git commit -m "feat(dashboard): capacity-now scalars mirroring the placement admission rule"
```

---

### Task 4: Overview partner table and flow chart (cards 10–11)

**Files:**
- Create: `cards/pca-time-since-last-order-by-partner.yaml`, `cards/pca-flow-by-hour.yaml`
- Modify: `dashboards/pear-cider-by-erjan-solutions-automated.yaml`

**Interfaces:**
- Consumes: collection `pear-cider-automated`, tab `overview`, parameter id `821ec3b1`.
- Produces: `pca-time-since-last-order-by-partner` `(partner text, last_order_at timestamp, minutes_since bigint)`,
  `pca-flow-by-hour` `(hour timestamp, series text, n bigint)`.

- [ ] **Step 1: Run both queries first**

```bash
python3 ../mbsql.py --sql "
SELECT partner, last_order_at, minutes_since
FROM (
  SELECT CASE WHEN c.name = 'sber' THEN 'Sber' ELSE COALESCE(c.name, 'unknown') END AS partner,
         max((o.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow'))        AS last_order_at,
         floor(EXTRACT(EPOCH FROM ((now() AT TIME ZONE 'UTC') - max(o.created_at))) / 60)::bigint AS minutes_since
  FROM order_ o
  LEFT JOIN api_client c ON c.client_id = o.client_id
  GROUP BY 1) t
ORDER BY (partner <> 'Sber'), minutes_since DESC"
```

Expected: one row per partner that has ever ordered, Sber first, under 1 s.

```bash
python3 ../mbsql.py cards/pca-flow-by-hour.yaml
```

(run after Step 3; the flow SQL was measured at 0.5 s and returns `4 × (hours + 1)` rows.)

- [ ] **Step 2: Write card 10**

`cards/pca-time-since-last-order-by-partner.yaml`:

```yaml
kind: card
key: pca-time-since-last-order-by-partner
name: Time since last order, by partner
description: Most recent order per API client, Sber first, then oldest silence first.
collection: pear-cider-automated
type: question
display: table
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- order_.created_at is naive UTC: compare against naive-UTC now, display in Moscow.
      SELECT partner, last_order_at, minutes_since
      FROM (
        SELECT CASE WHEN c.name = 'sber' THEN 'Sber' ELSE COALESCE(c.name, 'unknown') END AS partner,
               max((o.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow'))        AS last_order_at,
               floor(EXTRACT(EPOCH FROM ((now() AT TIME ZONE 'UTC') - max(o.created_at))) / 60)::bigint
                 AS minutes_since
        FROM order_ o
        LEFT JOIN api_client c ON c.client_id = o.client_id
        GROUP BY 1) t
      ORDER BY (partner <> 'Sber'), minutes_since DESC
visualization_settings:
  column_settings:
    '["name","minutes_since"]':
      suffix: ' min'
parameters: []
```

- [ ] **Step 3: Write card 11**

`cards/pca-flow-by-hour.yaml`:

```yaml
kind: card
key: pca-flow-by-hour
name: Flow by hour — requested / produced / sent / ordered
description: Four true event streams per hour. Idle hours render as zero, not as a gap.
collection: pear-cider-automated
type: question
display: line
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Every series uses a real event timestamp; none of the four needs a proxy.
      WITH spine AS (
        SELECT generate_series(
          date_trunc('hour', (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day')),
          date_trunc('hour', (now() AT TIME ZONE 'Europe/Moscow')),
          INTERVAL '1 hour') AS h),
      events AS (
        SELECT 'Requested' AS series,
               date_trunc('hour', (created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')) AS h
        FROM production_request
        WHERE created_at >= (now() AT TIME ZONE 'UTC') - ({{days}} * INTERVAL '1 day')
        UNION ALL
        SELECT 'Produced',
               date_trunc('hour', (succeeded_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow'))
        FROM task
        WHERE purpose = 'PRODUCTION' AND status = 'SUCCESS'
          AND succeeded_at >= (now() AT TIME ZONE 'UTC') - ({{days}} * INTERVAL '1 day')
        UNION ALL
        SELECT 'Sent',
               date_trunc('hour', (sent_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow'))
        FROM certificate
        WHERE status = 'SENT'
          AND sent_at >= (now() AT TIME ZONE 'UTC') - ({{days}} * INTERVAL '1 day')
        UNION ALL
        SELECT 'Ordered',
               date_trunc('hour', (created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow'))
        FROM order_
        WHERE created_at >= (now() AT TIME ZONE 'UTC') - ({{days}} * INTERVAL '1 day')),
      series AS (SELECT unnest(ARRAY['Requested', 'Produced', 'Sent', 'Ordered']) AS series)
      SELECT s.h AS hour, sr.series, count(e.series) AS n
      FROM spine s
      CROSS JOIN series sr
      LEFT JOIN events e ON e.h = s.h AND e.series = sr.series
      GROUP BY 1, 2
      ORDER BY 1, 2
    template-tags:
      days:
        id: 075963f3-60d4-4b8d-b8da-22a8ab74e6ac
        name: days
        display-name: Days
        type: number
        default: '7'
visualization_settings:
  graph.dimensions:
  - hour
  - series
  graph.metrics:
  - n
  graph.x_axis.scale: timeseries
parameters: []
```

- [ ] **Step 4: Append two dashcards**

```yaml
- key: dc-time-since-last-order-by-partner
  card: pca-time-since-last-order-by-partner
  tab: overview
  row: 14
  col: 0
  size_x: 24
  size_y: 5
  visualization_settings: {}
  parameter_mappings: []
  series: []
  inline_parameters: []
- key: dc-flow-by-hour
  card: pca-flow-by-hour
  tab: overview
  row: 19
  col: 0
  size_x: 24
  size_y: 7
  visualization_settings: {}
  parameter_mappings:
  - parameter_id: 821ec3b1
    target:
    - variable
    - - template-tag
      - days
  series: []
  inline_parameters: []
```

- [ ] **Step 5: Validate, diff, apply, re-diff**

```bash
./mbc validate
./mbc diff --env-file ../../../.env
./mbc apply --yes --env-file ../../../.env
./mbc diff --env-file ../../../.env      # exit 0
```

- [ ] **Step 6: Commit**

```bash
git add cards/pca-time-since-last-order-by-partner.yaml cards/pca-flow-by-hour.yaml \
        dashboards/pear-cider-by-erjan-solutions-automated.yaml \
        .state/bi-apple-certificate-solutions-8443.yaml
git commit -m "feat(dashboard): partner silence table and hourly flow chart"
```

The Overview tab is now complete (11 cards).

---

### Task 5: Orders by hour and live status breakdown (cards 12–14)

**Files:**
- Create: `cards/pca-orders-by-hour-sber.yaml`, `cards/pca-orders-by-hour-other.yaml`,
  `cards/pca-orders-in-progress-by-status-partner.yaml`
- Modify: `dashboards/pear-cider-by-erjan-solutions-automated.yaml`

**Interfaces:**
- Consumes: collection `pear-cider-automated`, tab `orders`, parameter id `821ec3b1`.
- Produces: `pca-orders-by-hour-sber` and `pca-orders-by-hour-other`, both
  `(hour timestamp, outcome text, orders bigint)`;
  `pca-orders-in-progress-by-status-partner` `(status text, partner text, orders bigint)`.

- [ ] **Step 1: Write card 12**

`cards/pca-orders-by-hour-sber.yaml`:

```yaml
kind: card
key: pca-orders-by-hour-sber
name: Orders by hour — Sber
description: Sber orders bucketed by the hour they were created, segmented by where they ended up.
collection: pear-cider-automated
type: question
display: bar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Cohort view on created_at: failed orders carry no terminal timestamp, so a
      -- cohort chart is the only one that invents nothing.
      WITH spine AS (
        SELECT generate_series(
          date_trunc('hour', (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day')),
          date_trunc('hour', (now() AT TIME ZONE 'Europe/Moscow')),
          INTERVAL '1 hour') AS h),
      o AS (
        SELECT date_trunc('hour', (o.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')) AS h,
               CASE
                 WHEN o.status = 'COMPLETED' THEN 'Completed'
                 WHEN o.status IN ('REJECTED', 'EXPIRED', 'FAILED_MANUAL') THEN 'Failed'
                 ELSE 'In progress'
               END AS outcome
        FROM order_ o
        LEFT JOIN api_client c ON c.client_id = o.client_id
        WHERE o.created_at >= (now() AT TIME ZONE 'UTC') - ({{days}} * INTERVAL '1 day')
          AND c.name = 'sber'),
      outcomes AS (SELECT unnest(ARRAY['Completed', 'In progress', 'Failed']) AS outcome)
      SELECT s.h AS hour, x.outcome, count(o.h) AS orders
      FROM spine s
      CROSS JOIN outcomes x
      LEFT JOIN o ON o.h = s.h AND o.outcome = x.outcome
      GROUP BY 1, 2
      ORDER BY 1, 2
    template-tags:
      days:
        id: 822e0d63-f30e-44f9-b402-9a22f6d05e4c
        name: days
        display-name: Days
        type: number
        default: '7'
visualization_settings:
  graph.dimensions:
  - hour
  - outcome
  graph.metrics:
  - orders
  graph.x_axis.scale: timeseries
  stackable.stack_type: stacked
parameters: []
```

- [ ] **Step 2: Write card 13**

`cards/pca-orders-by-hour-other.yaml` — identical except the key, name, description, uuid and the
partner predicate, which becomes `c.name IS DISTINCT FROM 'sber'` so an order whose client row is
missing lands in *Other partners* instead of vanishing:

```yaml
kind: card
key: pca-orders-by-hour-other
name: Orders by hour — Other partners
description: Non-Sber orders bucketed by the hour they were created, segmented by where they ended up.
collection: pear-cider-automated
type: question
display: bar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- IS DISTINCT FROM, not <>: a NULL client name (missing api_client row) belongs here.
      WITH spine AS (
        SELECT generate_series(
          date_trunc('hour', (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day')),
          date_trunc('hour', (now() AT TIME ZONE 'Europe/Moscow')),
          INTERVAL '1 hour') AS h),
      o AS (
        SELECT date_trunc('hour', (o.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')) AS h,
               CASE
                 WHEN o.status = 'COMPLETED' THEN 'Completed'
                 WHEN o.status IN ('REJECTED', 'EXPIRED', 'FAILED_MANUAL') THEN 'Failed'
                 ELSE 'In progress'
               END AS outcome
        FROM order_ o
        LEFT JOIN api_client c ON c.client_id = o.client_id
        WHERE o.created_at >= (now() AT TIME ZONE 'UTC') - ({{days}} * INTERVAL '1 day')
          AND c.name IS DISTINCT FROM 'sber'),
      outcomes AS (SELECT unnest(ARRAY['Completed', 'In progress', 'Failed']) AS outcome)
      SELECT s.h AS hour, x.outcome, count(o.h) AS orders
      FROM spine s
      CROSS JOIN outcomes x
      LEFT JOIN o ON o.h = s.h AND o.outcome = x.outcome
      GROUP BY 1, 2
      ORDER BY 1, 2
    template-tags:
      days:
        id: 6a2a8221-e790-4eef-aa8d-2c70378caa72
        name: days
        display-name: Days
        type: number
        default: '7'
visualization_settings:
  graph.dimensions:
  - hour
  - outcome
  graph.metrics:
  - orders
  graph.x_axis.scale: timeseries
  stackable.stack_type: stacked
parameters: []
```

- [ ] **Step 3: Write card 14**

`cards/pca-orders-in-progress-by-status-partner.yaml`:

```yaml
kind: card
key: pca-orders-in-progress-by-status-partner
name: Orders in progress now, by status and partner
description: Live non-terminal orders. Each of the four statuses is always shown, even at zero.
collection: pear-cider-automated
type: question
display: bar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      WITH statuses AS (
        SELECT unnest(ARRAY['AWAITING_PAYMENT', 'PAID', 'WAITING_CERT', 'SENDING']) AS status),
      partners AS (SELECT unnest(ARRAY['Sber', 'Other partners']) AS partner),
      live AS (
        SELECT o.status,
               CASE WHEN c.name = 'sber' THEN 'Sber' ELSE 'Other partners' END AS partner
        FROM order_ o
        LEFT JOIN api_client c ON c.client_id = o.client_id
        WHERE o.status IN ('AWAITING_PAYMENT', 'PAID', 'WAITING_CERT', 'SENDING'))
      SELECT st.status, p.partner, count(l.status) AS orders
      FROM statuses st
      CROSS JOIN partners p
      LEFT JOIN live l ON l.status = st.status AND l.partner = p.partner
      GROUP BY 1, 2
      ORDER BY 1, 2
visualization_settings:
  graph.dimensions:
  - status
  - partner
  graph.metrics:
  - orders
  graph.x_axis.scale: ordinal
parameters: []
```

- [ ] **Step 4: Run all three through the helper**

```bash
python3 ../mbsql.py cards/pca-orders-by-hour-sber.yaml
python3 ../mbsql.py cards/pca-orders-by-hour-other.yaml
python3 ../mbsql.py cards/pca-orders-in-progress-by-status-partner.yaml
```

Expected: the two hourly cards return `3 × (hours + 1)` rows in about 0.5 s; the status card returns
exactly 8 rows.

- [ ] **Step 5: Append three dashcards**

```yaml
- key: dc-orders-by-hour-sber
  card: pca-orders-by-hour-sber
  tab: orders
  row: 0
  col: 0
  size_x: 12
  size_y: 7
  visualization_settings: {}
  parameter_mappings:
  - parameter_id: 821ec3b1
    target:
    - variable
    - - template-tag
      - days
  series: []
  inline_parameters: []
- key: dc-orders-by-hour-other
  card: pca-orders-by-hour-other
  tab: orders
  row: 0
  col: 12
  size_x: 12
  size_y: 7
  visualization_settings: {}
  parameter_mappings:
  - parameter_id: 821ec3b1
    target:
    - variable
    - - template-tag
      - days
  series: []
  inline_parameters: []
- key: dc-orders-in-progress-by-status-partner
  card: pca-orders-in-progress-by-status-partner
  tab: orders
  row: 7
  col: 0
  size_x: 12
  size_y: 6
  visualization_settings: {}
  parameter_mappings: []
  series: []
  inline_parameters: []
```

- [ ] **Step 6: Validate, diff, apply, re-diff**

```bash
./mbc validate
./mbc diff --env-file ../../../.env
./mbc apply --yes --env-file ../../../.env
./mbc diff --env-file ../../../.env      # exit 0
```

- [ ] **Step 7: Commit**

```bash
git add cards/pca-orders-by-hour-sber.yaml cards/pca-orders-by-hour-other.yaml \
        cards/pca-orders-in-progress-by-status-partner.yaml \
        dashboards/pear-cider-by-erjan-solutions-automated.yaml \
        .state/bi-apple-certificate-solutions-8443.yaml
git commit -m "feat(dashboard): hourly order cohorts by partner and live status breakdown"
```

---

### Task 6: Completion time percentiles (card 15)

**Files:**
- Create: `cards/pca-completion-time-percentiles.yaml`
- Modify: `dashboards/pear-cider-by-erjan-solutions-automated.yaml`

**Interfaces:**
- Consumes: collection `pear-cider-automated`, tab `orders`, parameter id `821ec3b1`.
- Produces: `pca-completion-time-percentiles` `(percentile text, partner text, seconds numeric)`.

- [ ] **Step 1: Write the card**

`cards/pca-completion-time-percentiles.yaml`:

```yaml
kind: card
key: pca-completion-time-percentiles
name: Completion time percentiles — Sber vs Other partners
description: paid_at to completed_at for COMPLETED orders, the same span DeliveryVerdictApplier measures. Seconds.
collection: pear-cider-automated
type: question
display: bar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Both endpoints are naive UTC, so the difference needs no conversion; only the
      -- window predicate is compared against a converted "now".
      WITH spans AS (
        SELECT CASE WHEN c.name = 'sber' THEN 'Sber' ELSE 'Other partners' END AS partner,
               EXTRACT(EPOCH FROM (o.completed_at - o.paid_at))                AS seconds
        FROM order_ o
        LEFT JOIN api_client c ON c.client_id = o.client_id
        WHERE o.status = 'COMPLETED'
          AND o.paid_at IS NOT NULL
          AND o.completed_at >= (now() AT TIME ZONE 'UTC') - ({{days}} * INTERVAL '1 day')),
      pct AS (
        SELECT partner,
               percentile_cont(ARRAY[0.50, 0.75, 0.90, 0.95, 0.99])
                 WITHIN GROUP (ORDER BY seconds) AS p
        FROM spans
        GROUP BY partner)
      SELECT label AS percentile, partner, round(value::numeric, 1) AS seconds
      FROM pct, LATERAL unnest(ARRAY['p50', 'p75', 'p90', 'p95', 'p99'], p) AS t(label, value)
      ORDER BY percentile, partner
    template-tags:
      days:
        id: cbca1acb-eb40-481a-9168-a8ffbb8856e5
        name: days
        display-name: Days
        type: number
        default: '7'
visualization_settings:
  graph.dimensions:
  - percentile
  - partner
  graph.metrics:
  - seconds
  graph.x_axis.scale: ordinal
  graph.y_axis.title_text: seconds
parameters: []
```

- [ ] **Step 2: Run it**

Run: `python3 ../mbsql.py cards/pca-completion-time-percentiles.yaml`
Expected: 10 rows (5 percentiles × 2 partners) in well under 1 s, seconds in the single digits
(measured p50 ≈ 4.6 s Sber / 4.9 s Other on 2026-08-18). If a partner has no completed orders in the
window it is simply absent — that is correct, not a bug.

- [ ] **Step 3: Append the dashcard**

```yaml
- key: dc-completion-time-percentiles
  card: pca-completion-time-percentiles
  tab: orders
  row: 7
  col: 12
  size_x: 12
  size_y: 6
  visualization_settings: {}
  parameter_mappings:
  - parameter_id: 821ec3b1
    target:
    - variable
    - - template-tag
      - days
  series: []
  inline_parameters: []
```

- [ ] **Step 4: Validate, diff, apply, re-diff**

```bash
./mbc validate
./mbc diff --env-file ../../../.env
./mbc apply --yes --env-file ../../../.env
./mbc diff --env-file ../../../.env      # exit 0
```

- [ ] **Step 5: Commit**

```bash
git add cards/pca-completion-time-percentiles.yaml \
        dashboards/pear-cider-by-erjan-solutions-automated.yaml \
        .state/bi-apple-certificate-solutions-8443.yaml
git commit -m "feat(dashboard): order completion time percentiles by partner"
```

---

### Task 7: Orders aging backlog (cards 16–17)

The first heavy reconstruction. Measured at 3.5 s for Sber at `days = 7`.

**Files:**
- Create: `cards/pca-orders-aging-sber.yaml`, `cards/pca-orders-aging-other.yaml`
- Modify: `dashboards/pear-cider-by-erjan-solutions-automated.yaml`

**Interfaces:**
- Consumes: collection `pear-cider-automated`, tab `orders`, parameter id `821ec3b1`.
- Produces: `pca-orders-aging-sber`, `pca-orders-aging-other`, both `(hour timestamp, age_bucket text, n bigint)`.

- [ ] **Step 1: Write card 16**

`cards/pca-orders-aging-sber.yaml`:

```yaml
kind: card
key: pca-orders-aging-sber
name: Orders aging — Sber
description: How old the open Sber orders were at each hour. REJECTED/EXPIRED/FAILED_MANUAL orders are excluded — they carry no end timestamp, so their interval cannot be closed.
collection: pear-cider-automated
type: question
display: bar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Open interval is [created_at, completed_at), left open while completed_at is null.
      -- COMPLETED orders always carry completed_at and non-terminal ones are genuinely still
      -- open, so within the remaining set the interval is exact.
      WITH spine AS (
        SELECT generate_series(
          date_trunc('hour', (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day')),
          date_trunc('hour', (now() AT TIME ZONE 'Europe/Moscow')),
          INTERVAL '1 hour') AS h),
      o AS (
        SELECT (o.created_at   AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow') AS created_at,
               (o.completed_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow') AS completed_at
        FROM order_ o
        LEFT JOIN api_client c ON c.client_id = o.client_id
        WHERE o.status NOT IN ('REJECTED', 'EXPIRED', 'FAILED_MANUAL')
          AND c.name = 'sber')
      SELECT s.h AS hour,
             CASE
               WHEN s.h - o.created_at < INTERVAL '1 minute'   THEN '01 <1m'
               WHEN s.h - o.created_at < INTERVAL '5 minutes'  THEN '02 1-5m'
               WHEN s.h - o.created_at < INTERVAL '15 minutes' THEN '03 5-15m'
               WHEN s.h - o.created_at < INTERVAL '30 minutes' THEN '04 15-30m'
               WHEN s.h - o.created_at < INTERVAL '1 hour'     THEN '05 30m-1h'
               WHEN s.h - o.created_at < INTERVAL '3 hours'    THEN '06 1-3h'
               WHEN s.h - o.created_at < INTERVAL '6 hours'    THEN '07 3-6h'
               WHEN s.h - o.created_at < INTERVAL '1 day'      THEN '08 6h-1d'
               WHEN s.h - o.created_at < INTERVAL '3 days'     THEN '09 1-3d'
               ELSE '10 >3d'
             END AS age_bucket,
             count(*) AS n
      FROM spine s
      JOIN o ON o.created_at <= s.h AND (o.completed_at IS NULL OR o.completed_at > s.h)
      GROUP BY 1, 2
      ORDER BY 1, 2
    template-tags:
      days:
        id: a6cd1738-5504-4c4f-9ffa-659418e68fec
        name: days
        display-name: Days
        type: number
        default: '7'
visualization_settings:
  graph.dimensions:
  - hour
  - age_bucket
  graph.metrics:
  - n
  graph.x_axis.scale: timeseries
  stackable.stack_type: stacked
parameters: []
```

- [ ] **Step 2: Write card 17**

`cards/pca-orders-aging-other.yaml` — identical except the key, name, uuid and the partner predicate
`c.name IS DISTINCT FROM 'sber'`:

```yaml
kind: card
key: pca-orders-aging-other
name: Orders aging — Other partners
description: How old the open non-Sber orders were at each hour. REJECTED/EXPIRED/FAILED_MANUAL orders are excluded — they carry no end timestamp.
collection: pear-cider-automated
type: question
display: bar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      WITH spine AS (
        SELECT generate_series(
          date_trunc('hour', (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day')),
          date_trunc('hour', (now() AT TIME ZONE 'Europe/Moscow')),
          INTERVAL '1 hour') AS h),
      o AS (
        SELECT (o.created_at   AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow') AS created_at,
               (o.completed_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow') AS completed_at
        FROM order_ o
        LEFT JOIN api_client c ON c.client_id = o.client_id
        WHERE o.status NOT IN ('REJECTED', 'EXPIRED', 'FAILED_MANUAL')
          AND c.name IS DISTINCT FROM 'sber')
      SELECT s.h AS hour,
             CASE
               WHEN s.h - o.created_at < INTERVAL '1 minute'   THEN '01 <1m'
               WHEN s.h - o.created_at < INTERVAL '5 minutes'  THEN '02 1-5m'
               WHEN s.h - o.created_at < INTERVAL '15 minutes' THEN '03 5-15m'
               WHEN s.h - o.created_at < INTERVAL '30 minutes' THEN '04 15-30m'
               WHEN s.h - o.created_at < INTERVAL '1 hour'     THEN '05 30m-1h'
               WHEN s.h - o.created_at < INTERVAL '3 hours'    THEN '06 1-3h'
               WHEN s.h - o.created_at < INTERVAL '6 hours'    THEN '07 3-6h'
               WHEN s.h - o.created_at < INTERVAL '1 day'      THEN '08 6h-1d'
               WHEN s.h - o.created_at < INTERVAL '3 days'     THEN '09 1-3d'
               ELSE '10 >3d'
             END AS age_bucket,
             count(*) AS n
      FROM spine s
      JOIN o ON o.created_at <= s.h AND (o.completed_at IS NULL OR o.completed_at > s.h)
      GROUP BY 1, 2
      ORDER BY 1, 2
    template-tags:
      days:
        id: bf00ff67-a05f-4aaf-8ccc-160c6cceef31
        name: days
        display-name: Days
        type: number
        default: '7'
visualization_settings:
  graph.dimensions:
  - hour
  - age_bucket
  graph.metrics:
  - n
  graph.x_axis.scale: timeseries
  stackable.stack_type: stacked
parameters: []
```

- [ ] **Step 3: Time both cards, including the worst case**

```bash
python3 ../mbsql.py cards/pca-orders-aging-sber.yaml 7
python3 ../mbsql.py cards/pca-orders-aging-other.yaml 7
python3 ../mbsql.py cards/pca-orders-aging-sber.yaml 30
```

Expected: about 3.5 s at `days = 7`. The `days = 30` run must still finish under 30 s; if it does not,
add `cache_ttl: 600` to both cards and note the ceiling in their descriptions.

- [ ] **Step 4: Append two dashcards**

```yaml
- key: dc-orders-aging-sber
  card: pca-orders-aging-sber
  tab: orders
  row: 13
  col: 0
  size_x: 12
  size_y: 7
  visualization_settings: {}
  parameter_mappings:
  - parameter_id: 821ec3b1
    target:
    - variable
    - - template-tag
      - days
  series: []
  inline_parameters: []
- key: dc-orders-aging-other
  card: pca-orders-aging-other
  tab: orders
  row: 13
  col: 12
  size_x: 12
  size_y: 7
  visualization_settings: {}
  parameter_mappings:
  - parameter_id: 821ec3b1
    target:
    - variable
    - - template-tag
      - days
  series: []
  inline_parameters: []
```

- [ ] **Step 5: Validate, diff, apply, re-diff**

```bash
./mbc validate
./mbc diff --env-file ../../../.env
./mbc apply --yes --env-file ../../../.env
./mbc diff --env-file ../../../.env      # exit 0
```

- [ ] **Step 6: Commit**

```bash
git add cards/pca-orders-aging-sber.yaml cards/pca-orders-aging-other.yaml \
        dashboards/pear-cider-by-erjan-solutions-automated.yaml \
        .state/bi-apple-certificate-solutions-8443.yaml
git commit -m "feat(dashboard): order aging backlog reconstruction by partner"
```

---

### Task 8: Translated sales cards (cards 18–22)

The five metrics carried over from the existing Russian dashboard, re-authored in English.

**Files:**
- Create: `cards/pca-certificates-sent-by-hour.yaml`, `cards/pca-sales-distribution-by-partner.yaml`,
  `cards/pca-average-check-by-day.yaml`, `cards/pca-certificates-sold-by-denomination.yaml`,
  `cards/pca-certificate-turnover-by-denomination.yaml`
- Modify: `dashboards/pear-cider-by-erjan-solutions-automated.yaml`

**Interfaces:**
- Consumes: collection `pear-cider-automated`, tab `orders`, parameter id `821ec3b1`.
- Produces: `pca-certificates-sent-by-hour` `(hour timestamp, certificates bigint)`,
  `pca-sales-distribution-by-partner` `(partner text, revenue bigint)`,
  `pca-average-check-by-day` `(day timestamp, average_check bigint)`,
  `pca-certificates-sold-by-denomination` `(denomination bigint, certificates bigint)`,
  `pca-certificate-turnover-by-denomination` `(denomination bigint, turnover bigint)`.

- [ ] **Step 1: Write card 18**

`cards/pca-certificates-sent-by-hour.yaml`:

```yaml
kind: card
key: pca-certificates-sent-by-hour
name: Certificates sent by hour
description: Certificates sent per hour over the Days window. Supersedes the by-day form of the original card.
collection: pear-cider-automated
type: question
display: bar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      WITH spine AS (
        SELECT generate_series(
          date_trunc('hour', (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day')),
          date_trunc('hour', (now() AT TIME ZONE 'Europe/Moscow')),
          INTERVAL '1 hour') AS h),
      sent AS (
        SELECT date_trunc('hour', (sent_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')) AS h
        FROM certificate
        WHERE status = 'SENT'
          AND sent_at >= (now() AT TIME ZONE 'UTC') - ({{days}} * INTERVAL '1 day'))
      SELECT s.h AS hour, count(x.h) AS certificates
      FROM spine s
      LEFT JOIN sent x ON x.h = s.h
      GROUP BY 1
      ORDER BY 1
    template-tags:
      days:
        id: dbdb0727-f4bb-4aa6-9909-efd9a5d8ed5f
        name: days
        display-name: Days
        type: number
        default: '7'
visualization_settings:
  graph.dimensions:
  - hour
  graph.metrics:
  - certificates
  graph.x_axis.scale: timeseries
parameters: []
```

- [ ] **Step 2: Write card 19**

`cards/pca-sales-distribution-by-partner.yaml` — the one chart whose purpose is the full per-partner
split, so all nine clients are kept (spec §6 card 19):

```yaml
kind: card
key: pca-sales-distribution-by-partner
name: Sales distribution by partner
description: Order value per API client, all clients, all time. Translated from "Распределение продаж по партнерам".
collection: pear-cider-automated
type: question
display: bar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      SELECT COALESCE(c.name, 'unknown') AS partner,
             sum(o.amount)::bigint       AS revenue
      FROM order_ o
      LEFT JOIN api_client c ON c.client_id = o.client_id
      GROUP BY 1
      ORDER BY 2 DESC
visualization_settings:
  graph.dimensions:
  - partner
  graph.metrics:
  - revenue
  graph.x_axis.scale: ordinal
  graph.show_values: true
parameters: []
```

- [ ] **Step 3: Write card 20**

`cards/pca-average-check-by-day.yaml`:

```yaml
kind: card
key: pca-average-check-by-day
name: Average check by day
description: Mean face value of the certificates sent each day, over the Days window.
collection: pear-cider-automated
type: question
display: bar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      SELECT date_trunc('day', (sent_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')) AS day,
             round(avg(amount), 0)::bigint                                                AS average_check
      FROM certificate
      WHERE status = 'SENT'
        AND sent_at >= (now() AT TIME ZONE 'UTC') - ({{days}} * INTERVAL '1 day')
      GROUP BY 1
      ORDER BY 1
    template-tags:
      days:
        id: 4b343176-a566-4071-80d2-21ceb298e6b5
        name: days
        display-name: Days
        type: number
        default: '7'
visualization_settings:
  graph.dimensions:
  - day
  graph.metrics:
  - average_check
  graph.x_axis.scale: timeseries
  graph.show_values: true
parameters: []
```

- [ ] **Step 4: Write cards 21 and 22**

`cards/pca-certificates-sold-by-denomination.yaml`:

```yaml
kind: card
key: pca-certificates-sold-by-denomination
name: Certificates sold by denomination
description: How many certificates were sold at each face value, all time.
collection: pear-cider-automated
type: question
display: pie
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      SELECT amount AS denomination, count(*) AS certificates
      FROM certificate
      WHERE status = 'SENT'
      GROUP BY 1
      ORDER BY 1
visualization_settings:
  pie.slice_threshold: 0
parameters: []
```

`cards/pca-certificate-turnover-by-denomination.yaml`:

```yaml
kind: card
key: pca-certificate-turnover-by-denomination
name: Certificate turnover by denomination
description: Money sold per face value, current calendar year (Europe/Moscow).
collection: pear-cider-automated
type: question
display: pie
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      SELECT amount              AS denomination,
             sum(amount)::bigint AS turnover
      FROM certificate
      WHERE status = 'SENT'
        AND (sent_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')
            >= date_trunc('year', (now() AT TIME ZONE 'Europe/Moscow'))
      GROUP BY 1
      ORDER BY 1
visualization_settings:
  pie.slice_threshold: 0
parameters: []
```

- [ ] **Step 5: Run all five**

```bash
python3 ../mbsql.py cards/pca-certificates-sent-by-hour.yaml
python3 ../mbsql.py cards/pca-sales-distribution-by-partner.yaml
python3 ../mbsql.py cards/pca-average-check-by-day.yaml
python3 ../mbsql.py cards/pca-certificates-sold-by-denomination.yaml
python3 ../mbsql.py cards/pca-certificate-turnover-by-denomination.yaml
```

Expected: all under 1 s. `pca-sales-distribution-by-partner` returns up to nine rows;
the two denomination cards return one row per face value (1000, 1500, 2000, …).

- [ ] **Step 6: Append five dashcards**

```yaml
- key: dc-certificates-sent-by-hour
  card: pca-certificates-sent-by-hour
  tab: orders
  row: 20
  col: 0
  size_x: 24
  size_y: 6
  visualization_settings: {}
  parameter_mappings:
  - parameter_id: 821ec3b1
    target:
    - variable
    - - template-tag
      - days
  series: []
  inline_parameters: []
- key: dc-sales-distribution-by-partner
  card: pca-sales-distribution-by-partner
  tab: orders
  row: 26
  col: 0
  size_x: 12
  size_y: 7
  visualization_settings: {}
  parameter_mappings: []
  series: []
  inline_parameters: []
- key: dc-average-check-by-day
  card: pca-average-check-by-day
  tab: orders
  row: 26
  col: 12
  size_x: 12
  size_y: 7
  visualization_settings: {}
  parameter_mappings:
  - parameter_id: 821ec3b1
    target:
    - variable
    - - template-tag
      - days
  series: []
  inline_parameters: []
- key: dc-certificates-sold-by-denomination
  card: pca-certificates-sold-by-denomination
  tab: orders
  row: 33
  col: 0
  size_x: 12
  size_y: 7
  visualization_settings: {}
  parameter_mappings: []
  series: []
  inline_parameters: []
- key: dc-certificate-turnover-by-denomination
  card: pca-certificate-turnover-by-denomination
  tab: orders
  row: 33
  col: 12
  size_x: 12
  size_y: 7
  visualization_settings: {}
  parameter_mappings: []
  series: []
  inline_parameters: []
```

- [ ] **Step 7: Validate, diff, apply, re-diff**

```bash
./mbc validate
./mbc diff --env-file ../../../.env
./mbc apply --yes --env-file ../../../.env
./mbc diff --env-file ../../../.env      # exit 0
```

- [ ] **Step 8: Commit**

```bash
git add cards/pca-certificates-sent-by-hour.yaml cards/pca-sales-distribution-by-partner.yaml \
        cards/pca-average-check-by-day.yaml cards/pca-certificates-sold-by-denomination.yaml \
        cards/pca-certificate-turnover-by-denomination.yaml \
        dashboards/pear-cider-by-erjan-solutions-automated.yaml \
        .state/bi-apple-certificate-solutions-8443.yaml
git commit -m "feat(dashboard): English rebuild of the five sales cards"
```

The Orders tab is now complete (11 cards).

---

### Task 9: Production tab, pool and queue (cards 23–26)

**Files:**
- Create: `cards/pca-production-requests-by-hour.yaml`,
  `cards/pca-certificates-in-pool-by-denomination.yaml`,
  `cards/pca-production-queue-by-denomination.yaml`, `cards/pca-pool-total-value.yaml`
- Modify: `dashboards/pear-cider-by-erjan-solutions-automated.yaml`

**Interfaces:**
- Consumes: collection `pear-cider-automated`, tab `production`, parameter id `821ec3b1`.
- Produces: `pca-production-requests-by-hour` `(hour timestamp, outcome text, requests bigint)`,
  `pca-certificates-in-pool-by-denomination` `(denomination bigint, certificates bigint)`,
  `pca-production-queue-by-denomination` `(denomination bigint, requests bigint)`,
  `pca-pool-total-value` `(pool_value bigint)`.

- [ ] **Step 1: Write card 23**

`cards/pca-production-requests-by-hour.yaml`:

```yaml
kind: card
key: pca-production-requests-by-hour
name: Production requests by hour, by outcome
description: Requests bucketed by the hour they were created, segmented by their current status. production_request has no terminal timestamp, so this is a cohort view.
collection: pear-cider-automated
type: question
display: bar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- ProductionRequestStatus: PENDING, PLACING, PRODUCING, AWAITING_INTAKE, CLOSED, FAILED.
      WITH spine AS (
        SELECT generate_series(
          date_trunc('hour', (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day')),
          date_trunc('hour', (now() AT TIME ZONE 'Europe/Moscow')),
          INTERVAL '1 hour') AS h),
      pr AS (
        SELECT date_trunc('hour', (created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')) AS h,
               CASE
                 WHEN status = 'CLOSED' THEN 'Closed'
                 WHEN status = 'FAILED' THEN 'Failed'
                 ELSE 'In progress'
               END AS outcome
        FROM production_request
        WHERE created_at >= (now() AT TIME ZONE 'UTC') - ({{days}} * INTERVAL '1 day')),
      outcomes AS (SELECT unnest(ARRAY['Closed', 'In progress', 'Failed']) AS outcome)
      SELECT s.h AS hour, x.outcome, count(pr.h) AS requests
      FROM spine s
      CROSS JOIN outcomes x
      LEFT JOIN pr ON pr.h = s.h AND pr.outcome = x.outcome
      GROUP BY 1, 2
      ORDER BY 1, 2
    template-tags:
      days:
        id: 99df7d5d-3788-4c0e-a410-c76641027be5
        name: days
        display-name: Days
        type: number
        default: '7'
visualization_settings:
  graph.dimensions:
  - hour
  - outcome
  graph.metrics:
  - requests
  graph.x_axis.scale: timeseries
  stackable.stack_type: stacked
parameters: []
```

- [ ] **Step 2: Write cards 24, 25 and 26**

`cards/pca-certificates-in-pool-by-denomination.yaml`:

```yaml
kind: card
key: pca-certificates-in-pool-by-denomination
name: Certificates in pool by denomination
description: Free certificates available per face value. Merges the two original pool-by-denomination cards.
collection: pear-cider-automated
type: question
display: bar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      SELECT amount AS denomination, count(*) AS certificates
      FROM certificate
      WHERE status = 'FREE'
      GROUP BY 1
      ORDER BY 1
visualization_settings:
  graph.dimensions:
  - denomination
  graph.metrics:
  - certificates
  graph.x_axis.scale: ordinal
  graph.show_values: true
parameters: []
```

`cards/pca-production-queue-by-denomination.yaml`:

```yaml
kind: card
key: pca-production-queue-by-denomination
name: Production queue by denomination
description: Pending production requests per face value. An empty chart means an empty queue.
collection: pear-cider-automated
type: question
display: pie
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      SELECT amount AS denomination, count(*) AS requests
      FROM production_request
      WHERE status = 'PENDING'
      GROUP BY 1
      ORDER BY 1
visualization_settings:
  pie.slice_threshold: 0
parameters: []
```

`cards/pca-pool-total-value.yaml`:

```yaml
kind: card
key: pca-pool-total-value
name: Pool total value
description: Face value of every FREE certificate currently in the pool.
collection: pear-cider-automated
type: question
display: scalar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      SELECT COALESCE(sum(amount), 0)::bigint AS pool_value
      FROM certificate
      WHERE status = 'FREE'
visualization_settings: {}
parameters: []
```

- [ ] **Step 3: Run all four**

```bash
python3 ../mbsql.py cards/pca-production-requests-by-hour.yaml
python3 ../mbsql.py cards/pca-certificates-in-pool-by-denomination.yaml
python3 ../mbsql.py cards/pca-production-queue-by-denomination.yaml
python3 ../mbsql.py cards/pca-pool-total-value.yaml
```

Expected: all under 1 s. `pca-production-queue-by-denomination` may return zero rows — that is a healthy
queue, not a failure.

- [ ] **Step 4: Append four dashcards**

```yaml
- key: dc-production-requests-by-hour
  card: pca-production-requests-by-hour
  tab: production
  row: 0
  col: 0
  size_x: 24
  size_y: 7
  visualization_settings: {}
  parameter_mappings:
  - parameter_id: 821ec3b1
    target:
    - variable
    - - template-tag
      - days
  series: []
  inline_parameters: []
- key: dc-certificates-in-pool-by-denomination
  card: pca-certificates-in-pool-by-denomination
  tab: production
  row: 7
  col: 0
  size_x: 9
  size_y: 7
  visualization_settings: {}
  parameter_mappings: []
  series: []
  inline_parameters: []
- key: dc-production-queue-by-denomination
  card: pca-production-queue-by-denomination
  tab: production
  row: 7
  col: 9
  size_x: 9
  size_y: 7
  visualization_settings: {}
  parameter_mappings: []
  series: []
  inline_parameters: []
- key: dc-pool-total-value
  card: pca-pool-total-value
  tab: production
  row: 7
  col: 18
  size_x: 6
  size_y: 7
  visualization_settings: {}
  parameter_mappings: []
  series: []
  inline_parameters: []
```

- [ ] **Step 5: Validate, diff, apply, re-diff**

```bash
./mbc validate
./mbc diff --env-file ../../../.env
./mbc apply --yes --env-file ../../../.env
./mbc diff --env-file ../../../.env      # exit 0
```

- [ ] **Step 6: Commit**

```bash
git add cards/pca-production-requests-by-hour.yaml cards/pca-certificates-in-pool-by-denomination.yaml \
        cards/pca-production-queue-by-denomination.yaml cards/pca-pool-total-value.yaml \
        dashboards/pear-cider-by-erjan-solutions-automated.yaml \
        .state/bi-apple-certificate-solutions-8443.yaml
git commit -m "feat(dashboard): production request cohorts, pool and queue by denomination"
```

---

### Task 10: Capacity by hour (card 27)

The heaviest card: 5.9 s at `days = 7`. It ignores fleet readiness, which is not historised, and says so
in its own title.

**Files:**
- Create: `cards/pca-capacity-by-hour.yaml`
- Modify: `dashboards/pear-cider-by-erjan-solutions-automated.yaml`

**Interfaces:**
- Consumes: collection `pear-cider-automated`, tab `production`, parameter id `821ec3b1`.
- Produces: `pca-capacity-by-hour` `(hour timestamp, series text, capacity bigint)` with the two series
  `Quota + cooldown (upper bound)` and `Quota + cooldown + balance (upper bound)`.

- [ ] **Step 1: Write the card**

`cards/pca-capacity-by-hour.yaml`:

```yaml
kind: card
key: pca-capacity-by-hour
name: Capacity by hour (upper bound)
description: Historical capacity from quota windows, cooldowns and balances only. Fleet readiness is not historised, so this is an upper bound. Keep Days at 14 or below on this tab.
collection: pear-cider-automated
type: question
display: line
cache_ttl: 300
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Upper bound: device state, agent availability, validation status, holds and
      -- backoffs are current values only, so no historical query can apply them.
      -- Quota windows use the enforcement predicate QuotaLedgerRepository.SQL_ENFORCED_STATUSES.
      WITH limits AS (
        -- orchestrator/src/main/resources/application.yml, app.quota (lines 159-167).
        SELECT 6::bigint      AS max_tasks_per_account,   -- maxTasksPerAccount
               35000::bigint  AS day_limit_account,       -- dayLimitAccount
               500000::bigint AS day_limit_number,        -- dayLimitNumber
               600000::bigint AS month_limit_number,      -- monthLimitNumber
               1000::bigint   AS min_certificate_amount   -- minCertificateAmount
      ),
      spine AS (
        SELECT generate_series(
          date_trunc('hour', (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day')),
          date_trunc('hour', (now() AT TIME ZONE 'Europe/Moscow')),
          INTERVAL '1 hour') AS h),
      subject AS (
        -- The accounts currently attached to a device, with a current number.
        SELECT DISTINCT d.active_account_id AS account_id, acc.current_number_id AS number_id
        FROM device d
        JOIN account acc ON acc.account_id = d.active_account_id
        WHERE acc.current_number_id IS NOT NULL),
      ledger AS (
        SELECT subject_type, subject_id, amount,
               (ts AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow') AS ts
        FROM quota_ledger
        WHERE status IN ('RESERVED', 'COMMITTED')
          AND ts > (now() AT TIME ZONE 'UTC') - ({{days}} * INTERVAL '1 day') - INTERVAL '30 days'),
      account_window AS (
        SELECT s.h, l.subject_id, count(*) AS count24h, sum(l.amount) AS money24h
        FROM spine s
        JOIN ledger l ON l.subject_type = 'ACCOUNT'
                     AND l.ts > s.h - INTERVAL '24 hours' AND l.ts <= s.h
        GROUP BY 1, 2),
      number_window AS (
        SELECT s.h, l.subject_id,
               COALESCE(sum(l.amount) FILTER (WHERE l.ts > s.h - INTERVAL '24 hours'), 0) AS money24h,
               sum(l.amount) AS money30d
        FROM spine s
        JOIN ledger l ON l.subject_type = 'NUMBER'
                     AND l.ts > s.h - INTERVAL '30 days' AND l.ts <= s.h
        GROUP BY 1, 2),
      cooldown AS (
        SELECT s.h, c.account_id
        FROM spine s
        JOIN account_cooldown c
          ON (c.applied_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow') <= s.h
         AND (c.until      IS NULL OR (c.until      AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow') > s.h)
         AND (c.cleared_at IS NULL OR (c.cleared_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow') > s.h)
        GROUP BY 1, 2),
      balance AS (
        -- Last balance_after per number at or before the hour. balance_ledger.created_at is timestamptz.
        SELECT s.h, sn.number_id, bal.balance_after
        FROM spine s
        CROSS JOIN (SELECT DISTINCT number_id FROM subject) sn
        LEFT JOIN LATERAL (
          SELECT bl.balance_after
          FROM balance_ledger bl
          WHERE bl.number_id = sn.number_id
            AND (bl.created_at AT TIME ZONE 'Europe/Moscow') <= s.h
          ORDER BY bl.created_at DESC
          LIMIT 1) bal ON true),
      account_headroom AS (
        SELECT s.h, sub.number_id,
               CASE WHEN cd.account_id IS NOT NULL THEN 0
                    ELSE greatest(0, least(lim.max_tasks_per_account - COALESCE(aw.count24h, 0),
                                           floor((lim.day_limit_account - COALESCE(aw.money24h, 0))
                                                 / lim.min_certificate_amount)))
               END AS tasks_possible
        FROM spine s
        CROSS JOIN limits lim
        CROSS JOIN subject sub
        LEFT JOIN account_window aw ON aw.h = s.h AND aw.subject_id = sub.account_id
        LEFT JOIN cooldown cd       ON cd.h = s.h AND cd.account_id = sub.account_id),
      per_number AS (
        SELECT ah.h, ah.number_id,
               sum(ah.tasks_possible)             AS tasks_from_accounts,
               max(COALESCE(b.balance_after, 0))  AS balance
        FROM account_headroom ah
        LEFT JOIN balance b ON b.h = ah.h AND b.number_id = ah.number_id
        GROUP BY 1, 2),
      capped AS (
        SELECT pn.h, pn.number_id,
               greatest(0, least(pn.tasks_from_accounts,
                                 floor((lim.day_limit_number   - COALESCE(nw.money24h, 0)) / lim.min_certificate_amount),
                                 floor((lim.month_limit_number - COALESCE(nw.money30d, 0)) / lim.min_certificate_amount)))
                 AS quota_capacity,
               floor(pn.balance / lim.min_certificate_amount) AS balance_capacity
        FROM per_number pn
        CROSS JOIN limits lim
        LEFT JOIN number_window nw ON nw.h = pn.h AND nw.subject_id = pn.number_id)
      SELECT h AS hour, 'Quota + cooldown (upper bound)' AS series,
             sum(quota_capacity)::bigint AS capacity
      FROM capped
      GROUP BY 1
      UNION ALL
      SELECT h, 'Quota + cooldown + balance (upper bound)',
             sum(least(quota_capacity, balance_capacity))::bigint
      FROM capped
      GROUP BY 1
      ORDER BY 1, 2
    template-tags:
      days:
        id: b7e3e38a-6980-41d1-867b-fdf430d0695e
        name: days
        display-name: Days
        type: number
        default: '7'
visualization_settings:
  graph.dimensions:
  - hour
  - series
  graph.metrics:
  - capacity
  graph.x_axis.scale: timeseries
parameters: []
```

- [ ] **Step 2: Time it at 7 and at 14 days**

```bash
python3 ../mbsql.py cards/pca-capacity-by-hour.yaml 7
python3 ../mbsql.py cards/pca-capacity-by-hour.yaml 14
```

Expected: about 6 s at `days = 7`, roughly double at `days = 14`, `2 × (hours + 1)` rows, and the
balance series never above the quota series. If `days = 14` exceeds 20 s, raise `cache_ttl` to `900` and
state a `days ≤ 7` ceiling in the card description instead.

- [ ] **Step 3: Append the dashcard**

```yaml
- key: dc-capacity-by-hour
  card: pca-capacity-by-hour
  tab: production
  row: 14
  col: 0
  size_x: 24
  size_y: 7
  visualization_settings: {}
  parameter_mappings:
  - parameter_id: 821ec3b1
    target:
    - variable
    - - template-tag
      - days
  series: []
  inline_parameters: []
```

- [ ] **Step 4: Validate, diff, apply, re-diff**

```bash
./mbc validate
./mbc diff --env-file ../../../.env
./mbc apply --yes --env-file ../../../.env
./mbc diff --env-file ../../../.env      # exit 0
```

- [ ] **Step 5: Commit**

```bash
git add cards/pca-capacity-by-hour.yaml \
        dashboards/pear-cider-by-erjan-solutions-automated.yaml \
        .state/bi-apple-certificate-solutions-8443.yaml
git commit -m "feat(dashboard): hourly capacity upper bound from quota, cooldown and balance history"
```

The Production & Capacity tab is now complete (5 cards).

---

### Task 11: Top-ups tab (cards 28–30)

**Files:**
- Create: `cards/pca-topups-created-by-hour.yaml`, `cards/pca-topups-aging.yaml`,
  `cards/pca-topups-by-status.yaml`
- Modify: `dashboards/pear-cider-by-erjan-solutions-automated.yaml`

**Interfaces:**
- Consumes: collection `pear-cider-automated`, tab `topups`, parameter id `821ec3b1`.
- Produces: `pca-topups-created-by-hour` `(hour timestamp, topups bigint, total_rub bigint)`,
  `pca-topups-aging` `(hour timestamp, age_bucket text, n bigint)`,
  `pca-topups-by-status` `(status text, payout_rows bigint)`.

- [ ] **Step 1: Write card 28**

`cards/pca-topups-created-by-hour.yaml`:

```yaml
kind: card
key: pca-topups-created-by-hour
name: Top-ups created by hour
description: Distinct messages created per hour, with the money they carry. Counting rows would triple-count a message that fell through three providers.
collection: pear-cider-automated
type: question
display: combo
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- topup_payout holds one row per (message_id, provider_code): the provider-chain cursor.
      -- "Created" therefore counts DISTINCT message_id and sums one row per message.
      -- created_at is timestamptz, so it converts straight to Moscow.
      WITH spine AS (
        SELECT generate_series(
          date_trunc('hour', (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day')),
          date_trunc('hour', (now() AT TIME ZONE 'Europe/Moscow')),
          INTERVAL '1 hour') AS h),
      msg AS (
        SELECT DISTINCT ON (message_id)
               message_id,
               date_trunc('hour', (created_at AT TIME ZONE 'Europe/Moscow')) AS h,
               amount
        FROM topup_payout
        WHERE created_at >= now() - ({{days}} * INTERVAL '1 day')
        ORDER BY message_id, id)
      SELECT s.h AS hour,
             count(m.message_id)              AS topups,
             COALESCE(sum(m.amount), 0)::bigint AS total_rub
      FROM spine s
      LEFT JOIN msg m ON m.h = s.h
      GROUP BY 1
      ORDER BY 1
    template-tags:
      days:
        id: 17465025-1749-4282-83b0-d9fcb599bae9
        name: days
        display-name: Days
        type: number
        default: '7'
visualization_settings:
  graph.dimensions:
  - hour
  graph.metrics:
  - topups
  - total_rub
  graph.x_axis.scale: timeseries
  series_settings:
    topups:
      display: bar
      axis: left
    total_rub:
      display: line
      axis: right
parameters: []
```

- [ ] **Step 2: Write card 29**

`cards/pca-topups-aging.yaml`:

```yaml
kind: card
key: pca-topups-aging
name: Top-ups in progress, aging
description: How old the open payout rows were at each hour. updated_at is the last touch, which for a terminal row is the moment it became terminal.
collection: pear-cider-automated
type: question
display: bar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Interval is [created_at, updated_at) for rows that reached a terminal status and
      -- [created_at, now()) for rows still in CREATE_RETRY / PENDING_POLL.
      -- Both columns are timestamptz.
      WITH spine AS (
        SELECT generate_series(
          date_trunc('hour', (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day')),
          date_trunc('hour', (now() AT TIME ZONE 'Europe/Moscow')),
          INTERVAL '1 hour') AS h),
      tp AS (
        SELECT (created_at AT TIME ZONE 'Europe/Moscow') AS created_at,
               CASE WHEN status IN ('CREATE_RETRY', 'PENDING_POLL') THEN NULL
                    ELSE (updated_at AT TIME ZONE 'Europe/Moscow') END AS closed_at
        FROM topup_payout
        WHERE created_at >= now() - ({{days}} * INTERVAL '1 day') - INTERVAL '30 days')
      SELECT s.h AS hour,
             CASE
               WHEN s.h - tp.created_at < INTERVAL '1 minute'   THEN '01 <1m'
               WHEN s.h - tp.created_at < INTERVAL '5 minutes'  THEN '02 1-5m'
               WHEN s.h - tp.created_at < INTERVAL '15 minutes' THEN '03 5-15m'
               WHEN s.h - tp.created_at < INTERVAL '30 minutes' THEN '04 15-30m'
               WHEN s.h - tp.created_at < INTERVAL '1 hour'     THEN '05 30m-1h'
               WHEN s.h - tp.created_at < INTERVAL '3 hours'    THEN '06 1-3h'
               WHEN s.h - tp.created_at < INTERVAL '6 hours'    THEN '07 3-6h'
               WHEN s.h - tp.created_at < INTERVAL '1 day'      THEN '08 6h-1d'
               WHEN s.h - tp.created_at < INTERVAL '3 days'     THEN '09 1-3d'
               ELSE '10 >3d'
             END AS age_bucket,
             count(*) AS n
      FROM spine s
      JOIN tp ON tp.created_at <= s.h AND (tp.closed_at IS NULL OR tp.closed_at > s.h)
      GROUP BY 1, 2
      ORDER BY 1, 2
    template-tags:
      days:
        id: 4c3ded10-63bb-4af2-a75d-a5b849ef3ead
        name: days
        display-name: Days
        type: number
        default: '7'
visualization_settings:
  graph.dimensions:
  - hour
  - age_bucket
  graph.metrics:
  - n
  graph.x_axis.scale: timeseries
  stackable.stack_type: stacked
parameters: []
```

- [ ] **Step 3: Write card 30**

`cards/pca-topups-by-status.yaml`:

```yaml
kind: card
key: pca-topups-by-status
name: Top-ups by status (now)
description: Live payout rows per status, all nine TopUpPayoutStatus values, so PARKED rows awaiting manual triage stay visible.
collection: pear-cider-automated
type: question
display: bar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
  - lib/type: mbql.stage/native
    native: |
      WITH statuses AS (
        SELECT unnest(ARRAY['CREATE_RETRY', 'PENDING_POLL', 'COMPLETED', 'REFUSED', 'EXHAUSTED',
                            'FAILED', 'PARKED', 'SPLIT', 'SUPERSEDED']) AS status)
      SELECT st.status, count(t.id) AS payout_rows
      FROM statuses st
      LEFT JOIN topup_payout t ON t.status = st.status
      GROUP BY 1
      ORDER BY 2 DESC, 1
visualization_settings:
  graph.dimensions:
  - status
  graph.metrics:
  - payout_rows
  graph.x_axis.scale: ordinal
  graph.show_values: true
parameters: []
```

- [ ] **Step 4: Run all three**

```bash
python3 ../mbsql.py cards/pca-topups-created-by-hour.yaml
python3 ../mbsql.py cards/pca-topups-aging.yaml
python3 ../mbsql.py cards/pca-topups-by-status.yaml
```

Expected: created-by-hour about 0.4 s with one row per hour; aging about 1.9 s; by-status exactly 9 rows,
including zeros. Cross-check: the `CREATE_RETRY` + `PENDING_POLL` rows of card 30 must equal the
`Top-ups in progress (count)` scalar from Task 2.

- [ ] **Step 5: Append three dashcards**

```yaml
- key: dc-topups-created-by-hour
  card: pca-topups-created-by-hour
  tab: topups
  row: 0
  col: 0
  size_x: 24
  size_y: 7
  visualization_settings: {}
  parameter_mappings:
  - parameter_id: 821ec3b1
    target:
    - variable
    - - template-tag
      - days
  series: []
  inline_parameters: []
- key: dc-topups-aging
  card: pca-topups-aging
  tab: topups
  row: 7
  col: 0
  size_x: 16
  size_y: 7
  visualization_settings: {}
  parameter_mappings:
  - parameter_id: 821ec3b1
    target:
    - variable
    - - template-tag
      - days
  series: []
  inline_parameters: []
- key: dc-topups-by-status
  card: pca-topups-by-status
  tab: topups
  row: 7
  col: 16
  size_x: 8
  size_y: 7
  visualization_settings: {}
  parameter_mappings: []
  series: []
  inline_parameters: []
```

- [ ] **Step 6: Validate, diff, apply, re-diff**

```bash
./mbc validate
./mbc diff --env-file ../../../.env
./mbc apply --yes --env-file ../../../.env
./mbc diff --env-file ../../../.env      # exit 0
```

- [ ] **Step 7: Commit**

```bash
git add cards/pca-topups-created-by-hour.yaml cards/pca-topups-aging.yaml \
        cards/pca-topups-by-status.yaml \
        dashboards/pear-cider-by-erjan-solutions-automated.yaml \
        .state/bi-apple-certificate-solutions-8443.yaml
git commit -m "feat(dashboard): top-up creation, aging and status cards"
```

All 30 cards are now live.

---

### Task 12: Whole-dashboard verification

**Files:**
- Modify: none expected. Any fix found here edits the offending card or the dashboard file.

**Interfaces:**
- Consumes: everything from Tasks 1–11.
- Produces: a verified dashboard and a final commit.

- [ ] **Step 1: Confirm the file inventory**

```bash
ls cards/pca-*.yaml | wc -l
python3 -c "import yaml;d=yaml.safe_load(open('dashboards/pear-cider-by-erjan-solutions-automated.yaml'));print(len(d['dashcards']), len(d['tabs']))"
```

Expected: `30`, then `30 4`.

- [ ] **Step 2: Confirm every windowed card is mapped, and no other card is**

```bash
python3 - <<'PY'
import glob, yaml

windowed = set()
for path in sorted(glob.glob("cards/pca-*.yaml")):
    doc = yaml.safe_load(open(path))
    stage = doc["dataset_query"]["stages"][0]
    if "days" in (stage.get("template-tags") or {}):
        windowed.add(doc["key"])

dash = yaml.safe_load(open("dashboards/pear-cider-by-erjan-solutions-automated.yaml"))
mapped = {dc["card"] for dc in dash["dashcards"] if dc.get("parameter_mappings")}
print("windowed cards:", len(windowed))
print("mapped dashcards:", len(mapped))
print("windowed but unmapped:", sorted(windowed - mapped))
print("mapped but not windowed:", sorted(mapped - windowed))
PY
```

Expected: `windowed cards: 14`, `mapped dashcards: 14`, both difference lists empty.

- [ ] **Step 3: Confirm the grid**

```bash
./mbc validate
```

Expected: exit `0` (validate already enforces `col + size_x <= 24` and that every dashcard declares a tab).

- [ ] **Step 4: Confirm nothing existing was touched**

```bash
git diff --stat aca5636..HEAD -- cards dashboards collections | grep -v "pca-\|pear-cider-automated\|pear-cider-by-erjan-solutions-automated" || echo "no existing files changed"
```

Expected: `no existing files changed`. The nine Russian cards and
`dashboards/pear-cider-by-erjan-solutions.yaml` must be untouched.

- [ ] **Step 5: Run every card once through the live instance and record the slowest**

```bash
for f in cards/pca-*.yaml; do echo "== $f"; python3 ../mbsql.py "$f" 7 | head -2; done
```

Expected: every card completes; none approaches the 30 s statement timeout. Note the slowest three in
the commit message.

- [ ] **Step 6: Run each tab's cards at `days = 14`**

```bash
for f in cards/pca-orders-aging-sber.yaml cards/pca-orders-aging-other.yaml \
         cards/pca-capacity-by-hour.yaml cards/pca-topups-aging.yaml; do
  echo "== $f"; python3 ../mbsql.py "$f" 14 | head -1
done
```

Expected: all four finish inside 30 s. If `pca-capacity-by-hour` does not, lower the documented ceiling
in its description to `days ≤ 7` and raise `cache_ttl` to `900`, then re-apply.

- [ ] **Step 7: Final diff and commit**

```bash
./mbc diff --env-file ../../../.env      # exit 0
git add -A
git commit -m "chore(dashboard): verify Pear Cider (automated) end to end"
```

If `git add -A` stages nothing, the tree is already clean and the dashboard is done — skip the commit.

- [ ] **Step 8: Open the dashboard in the browser and eyeball it**

Check, in order: the four tabs exist and are named `Overview`, `Orders`, `Production & Capacity`,
`Top-ups`; `Certificate face value by day` is the first card on Overview; the `Days` filter shows `7`;
changing it to `1` visibly changes the windowed charts and leaves the live scalars unchanged; no card
shows an error. Report anything that renders wrong — visualization settings are the one thing this plan
cannot verify from the command line.
