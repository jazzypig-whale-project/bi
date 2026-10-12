# Pear Cider dashboard: legacy + dealer data sources — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Pear Cider (automated) dashboard show honest totals across the migration of API clients from the legacy `orchestrator` DB (Metabase database 2) to the `dealer` DB (database 3): stop double-counting the DEALER pseudo-client, add dealer twins of the order-flow and email cards, and add a migration-progress section.

**Architecture:** Everything is Metabase-as-code YAML (`cards/`, `dashboards/`). A native query cannot read two databases, so each dealer number is a separate card on database 3 that is either stacked as a `series` on the legacy dashcard (charts) or placed next to it (scalars, tables). A repo-level pytest file pins the invariants (DEALER exclusion, twin conventions, filter mappings on series cards, non-overlapping layout); every SQL is also executed over the Metabase MCP against its database.

**Tech Stack:** Metabase (native SQL cards, MBQL 5 wrapper), PostgreSQL, `./mbc` (Python, `mbcode/`), pytest, PyYAML, Metabase MCP (`mcp__metabase__execute_query`).

**Spec:** `docs/superpowers/specs/2026-10-12-dealer-dual-source-dashboard-design.md`

## Global Constraints

- NEVER write to, migrate, or run DDL on either app database. Metabase reads them through the read-only `metabase` role only. Anything that would touch `orchestrator` or `dealer` on write/schema/load: stop and ask the owner.
- `./mbc apply` changes the live Metabase instance: run it ONLY after the owner reviews `./mbc diff` and says go.
- Commit and push ONLY with the owner's explicit permission in the same message (`CLAUDE.md`). Tasks therefore end with a diff checkpoint, not a commit; the single commit is in Task 12.
- Twin file name: `cards/pca-<legacy-key>-dealer.yaml`, `database: 3`, `collection: pear-cider-automated`.
- Twins return the same column names as the legacy card and use the same `{{days}}` / `{{granularity}}` template-tag names (new tag `id`s, listed per task).
- DEALER exclusion literal (alias adapted to the query; `<>` where `client_id` comes straight from `order_`, which is `NOT NULL`):
  `AND o.client_id <> '00000000-0000-0000-0000-000000000002'  -- DEALER pseudo-client: dealer purchases, counted in the dealer DB`
- Amounts: dealer `face_amount / 100.0` for RUB. Another currency is never added to rubles; it becomes its own series labelled with its `face_currency`.
- Time: dealer columns are `timestamptz` → `(col AT TIME ZONE 'Europe/Moscow')`; legacy columns are naive UTC → `(col AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')`. Window start on a dealer column: `(<Moscow-naive expression> AT TIME ZONE 'Europe/Moscow')`. `mbcode/lint_timezone.py` forbids a bare `now()`; no twin needs `-- tz-ok`.
- Series and pair titles: "Legacy · …" / "Dealer · …" (the separator is U+00B7 MIDDLE DOT with a space on each side).
- Every touched SQL starts with a header comment: what it counts, which database, exclusions and why. Legacy headers start with `-- Database 2 (legacy orchestrator).`, twin headers with `-- Database 3 (dealer).`
- CLI style: long flags (`--verbose`, not `-v`). Shell: prefix every command with `unset HTTP_PROXY HTTPS_PROXY NO_PROXY;`.

### Shared commands (used by several tasks)

Run the repo tests:

```bash
unset HTTP_PROXY HTTPS_PROXY NO_PROXY; .venv/bin/pytest tests/test_dealer_dashboard.py --verbose
```

Offline validation (structure, references, timezone lint):

```bash
unset HTTP_PROXY HTTPS_PROXY NO_PROXY; ./mbc validate
```

Render a card's SQL with the dashboard defaults substituted, to paste into the Metabase MCP `execute_query` (native query, `database_id` = the card's `database`):

```bash
unset HTTP_PROXY HTTPS_PROXY NO_PROXY; .venv/bin/python - cards/<card-key>.yaml <<'EOF'
import sys, yaml
doc = yaml.safe_load(open(sys.argv[1]))
sql = doc["dataset_query"]["stages"][0]["native"]
print(f"-- database {doc['dataset_query']['database']}")
print(sql.replace("{{granularity}}", "'1 day'").replace("{{days}}", "7"))
EOF
```

A Metabase text template tag is substituted as a quoted string, which is why `{{granularity}}` becomes `'1 day'` (the SQL then casts it with `::text::interval`).

## Spec deviations (decided against the dealer code; confirm with the owner at plan review)

The spec says the email mapping "is fixed while writing the SQL, against dealer code". Reading `orchestrator/src/main/java/com/example/orchestrator/dealer/dms/Shipment.java` and `DealerShipmentOutcomes.java` fixes it as follows; four points contradict the spec text:

1. **`late_delivered_at` is not a late-bounce resolution.** `Shipment.stampLateDelivered` = "a delivery after FAILED". The dealer has no resolution step for a late bounce at all. The dealer twin of "Unresolved late bounces" therefore counts every shipment with `late_bounce_at` set and is titled "Dealer · Late bounces (no resolution step)"; its column is `late_bounces`. Spec said `late_bounce_at IS NOT NULL AND late_delivered_at IS NULL`.
2. **`BOUNCED` is not a failure.** `Shipment.markBounced` = "a retryable bounce waiting for its next attempt". The failed-emails twin counts `shipment.status = 'FAILED'` only, matching legacy's terminal-only `FAILED_MANUAL`. `BOUNCED` is counted as in progress in the delivery table. Spec said `status IN ('FAILED','BOUNCED')`.
3. **Late-bounce lag is measured from the delivery verdict, not `sent_at`.** The delivering attempt's success verdict is `shipment_attempt.verdict_at` with `category IS NULL` (`ShipmentAttemptRepository.stamp`: "a null category (a success verdict)"). That matches legacy's `late_bounce_at - delivered_at` and keeps the same bucket labels, so the two series line up. Spec said `late_bounce_at - sent_at`.
4. **By-client exclusion uses `IS DISTINCT FROM`.** In `pca-certificate-face-value-by-day-by-client` the order is `LEFT JOIN`ed; `o.client_id <> '…02'` would be NULL for bulk-order certificates and silently drop them. Spec gave the `<>` literal.

And one addition: **the reconciliation check is not an exact identity.** Dealer purchases are per order (`supplier_purchase.order_id NOT NULL`), but a dealer order can fail after its voucher was bought; the voucher then sits in dealer stock (`voucher.status = 'FREE'`) while legacy counted it as sold. Task 12 checks `DEALER mirror face value = dealer COMPLETED face value + face value of dealer vouchers bought in the window and not issued`, within window-edge timing.

## Review Focus

1. **Bulk-order certificates vanish from the legacy by-client chart** if the DEALER exclusion is written `<>` on a `LEFT JOIN`ed order. Expect Bulk/Unknown series unchanged. Pinned in Task 4 (`test_by_client_exclusion_keeps_null_order`).
2. **A twin stacked as a series ignores Days/Granularity** when its dashcard lacks a `card:`-scoped mapping (it silently runs on the tag default `7` / `'1 day'`). Expect every series card to follow the dashboard filters. Pinned in Task 2 (`test_every_series_card_gets_every_filter_of_its_dashcard`).
3. **Pairs overlap after shrinking** a full-width legacy dashcard to half width and inserting the twin. Expect no two dashcards on a tab to share a grid cell. Pinned in Task 2 (`test_dashcards_do_not_overlap`).
4. **Mixed currencies summed as rubles.** A non-RUB dealer order must be its own series, never added into a RUB bar. Pinned in Task 4 (`test_by_client_twin_never_mixes_currencies`).
5. **Metabase does not render a breakout card with breakout series** the way the plan assumes. Expect a stacked chart with one legend entry per source and value. Settled by Task 1 before any card is written, with a recorded fallback (pairs); re-checked visually in Task 12.

---

### Task 1: Spike — does Metabase render series on breakout cards?

Six dashcards (by-client, flow, emails sent, emails failed, late bounces by day, migration orders) stack a two-dimension card (time + breakout) with a two-dimension twin. This must work in the deployed Metabase version before building on it.

**Files:**
- Modify: `docs/superpowers/plans/2026-10-12-dealer-dual-source-dashboard.md` (append the decision under "Task 1 outcome" at the end of this task)

**Interfaces:**
- Consumes: nothing.
- Produces: decision `SERIES_MODE = stacked | pairs` used by Tasks 4, 5, 8, 10, 11.

- [ ] **Step 1: Read the deployed Metabase version**

```bash
unset HTTP_PROXY HTTPS_PROXY NO_PROXY; set -a; . ./.env; set +a; curl --silent --show-error --max-time 60 --proxy "${METABASE_PROXY:-http://localhost:2080}" --user "$METABASE_BASIC_USERNAME:$METABASE_BASIC_PASSWORD" --header "X-API-KEY: $METABASE_API_KEY" "$METABASE_BASE_URL/api/session/properties" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["version"]["tag"])'
```

Expected: a tag such as `v0.5x.y`. If the body is not JSON, print it raw (drop the python pipe) and check the proxy (memory note: direct HTTPS stalls on larger responses).

- [ ] **Step 2: Read the series model source at that tag**

Fetch (WebFetch) `https://github.com/metabase/metabase/blob/<tag>/frontend/src/metabase/visualizations/echarts/cartesian/model/series.ts` and find how a card with a breakout column is turned into series when the dashcard has more than one card (search for `hasMultipleCards` and `breakout`). Record: (a) whether breakout cards in a multi-card dashcard produce one series per breakout value per card; (b) how the legend name is built (card name prefix or not). Link the exact lines in the outcome.

- [ ] **Step 3: Record the decision**

Append to this task:

```markdown
#### Task 1 outcome
- Metabase version: <tag>
- Source: <github link with line anchors>
- Breakout cards combine as series: yes | no
- Legend name for a series card's breakout value: "<value>" | "<card name>: <value>"
- SERIES_MODE: stacked | pairs
```

`SERIES_MODE = stacked` if (a) is yes. If (a) is no, `SERIES_MODE = pairs`: in Tasks 4, 5, 8, 10 and 11 every "series" dashcard change becomes a pair instead (legacy dashcard at half width, twin dashcard on the same row in the other half, each with its own Days/Granularity mappings and no `card:` key) — the twin cards themselves are unchanged. A legend that prefixes the card name is acceptable and needs no change ("<card name>: Dealer · Sber" is redundant but unambiguous).

#### Task 1 outcome
- Metabase version: v0.60.7 (hash 6a29414, 2026-05-19)
- Source: https://github.com/metabase/metabase/blob/v0.60.7/frontend/src/metabase/visualizations/echarts/cartesian/model/series.ts — `getCardsSeriesModels` [L164-L181](https://github.com/metabase/metabase/blob/v0.60.7/frontend/src/metabase/visualizations/echarts/cartesian/model/series.ts#L164-L181) (`hasMultipleCards = rawSeries.length > 1`, `flatMap` over every card); breakout branch of `getCardSeriesModels` [L262-L333](https://github.com/metabase/metabase/blob/v0.60.7/frontend/src/metabase/visualizations/echarts/cartesian/model/series.ts#L262-L333) (one series per distinct breakout value, per card); legend name `getDefaultSeriesName` [L143-L162](https://github.com/metabase/metabase/blob/v0.60.7/frontend/src/metabase/visualizations/echarts/cartesian/model/series.ts#L143-L162); key `getSeriesVizSettingsKey` [L55-L81](https://github.com/metabase/metabase/blob/v0.60.7/frontend/src/metabase/visualizations/echarts/cartesian/model/series.ts#L55-L81)
- Breakout cards combine as series: yes (source-level only; not rendered in the live UI)
- Legend name for a series card's breakout value: "<card name>: <value>" (applies to every card in a multi-card dashcard, the first one included)
- SERIES_MODE: stacked

---

### Task 2: Repo invariant tests

**Files:**
- Create: `tests/test_dealer_dashboard.py`

**Interfaces:**
- Consumes: nothing.
- Produces: module-level sets `DEALER_EXCLUDED`, `DEALER_INCLUDED_NOTE`, `LEGACY_HEADER_ONLY`, and dict `LEGACY_OF` that later tasks extend; helpers `_card(key) -> dict`, `_sql(doc) -> str`, `_tags(doc) -> set[str]`.

- [ ] **Step 1: Write the test file**

```python
"""Repo invariants for the Pear Cider legacy + dealer dashboard.

See docs/superpowers/specs/2026-10-12-dealer-dual-source-dashboard-design.md.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mbcode.validate import validate_tree

REPO = Path(__file__).resolve().parent.parent
DASHBOARD = REPO / "dashboards" / "pear-cider-by-erjan-solutions-automated.yaml"
LEGACY_DB = 2
DEALER_DB = 3
DEALER_CLIENT = "00000000-0000-0000-0000-000000000002"
LEGACY_HEADER = "-- Database 2 (legacy orchestrator)."
DEALER_HEADER = "-- Database 3 (dealer)."

# Legacy cards that must not count DEALER pseudo-client orders (spec sections 2, 4, 6).
DEALER_EXCLUDED: set[str] = set()
# Legacy cards whose numbers already include dealer volume; header says so (spec section 1).
DEALER_INCLUDED_NOTE: set[str] = set()
# Legacy cards that only gain a header comment (spec section 3, legacy email cards).
LEGACY_HEADER_ONLY: set[str] = set()
# Twin key -> legacy counterpart, where it is not the twin key minus "-dealer".
LEGACY_OF: dict[str, str] = {}


def _card(key):
    return yaml.safe_load((REPO / "cards" / f"{key}.yaml").read_text())


def _sql(doc):
    return doc["dataset_query"]["stages"][0]["native"]


def _tags(doc):
    return set(doc["dataset_query"]["stages"][0].get("template-tags") or {})


def _dashcards():
    return yaml.safe_load(DASHBOARD.read_text())["dashcards"]


def _dealer_twins():
    return sorted(path.stem for path in (REPO / "cards").glob("pca-*-dealer.yaml"))


def test_repo_tree_validates_clean():
    _, problems = validate_tree(str(REPO))
    assert problems == []


@pytest.mark.parametrize("dashcard", _dashcards(), ids=lambda dc: dc["key"])
def test_every_series_card_gets_every_filter_of_its_dashcard(dashcard):
    mappings = dashcard.get("parameter_mappings") or []
    own = {m["parameter_id"] for m in mappings if "card" not in m}
    for series_key in dashcard.get("series") or []:
        mapped = {m["parameter_id"] for m in mappings if m.get("card") == series_key}
        assert mapped == own, (
            f"{series_key} on {dashcard['key']}: maps {sorted(mapped)}, the dashcard's card maps {sorted(own)}")


def test_dashcards_do_not_overlap():
    taken = {}
    for dc in _dashcards():
        for row in range(dc["row"], dc["row"] + dc["size_y"]):
            for col in range(dc["col"], dc["col"] + dc["size_x"]):
                cell = (dc["tab"], row, col)
                assert cell not in taken, f"{dc['key']} overlaps {taken[cell]} at {cell}"
                taken[cell] = dc["key"]


@pytest.mark.parametrize("key", _dealer_twins())
def test_dealer_twin_follows_conventions(key):
    twin = _card(key)
    legacy = _card(LEGACY_OF.get(key, key.removesuffix("-dealer")))
    assert twin["dataset_query"]["database"] == DEALER_DB
    assert twin["collection"] == "pear-cider-automated"
    assert twin["name"].startswith("Dealer · ")
    assert _sql(twin).startswith(DEALER_HEADER)
    assert _tags(twin) == _tags(legacy)


@pytest.mark.parametrize("key", sorted(DEALER_EXCLUDED))
def test_legacy_card_excludes_dealer_client(key):
    doc = _card(key)
    sql = _sql(doc)
    assert doc["dataset_query"]["database"] == LEGACY_DB
    assert sql.startswith(LEGACY_HEADER)
    assert f"'{DEALER_CLIENT}'  -- DEALER pseudo-client: dealer purchases, counted in the dealer DB" in sql


@pytest.mark.parametrize("key", sorted(DEALER_INCLUDED_NOTE))
def test_legacy_card_states_dealer_volume_is_included(key):
    sql = _sql(_card(key))
    assert sql.startswith(LEGACY_HEADER)
    assert f"DEALER pseudo-client {DEALER_CLIENT}" in sql
    assert DEALER_CLIENT + "'" not in sql, "a category-1 card must not filter on DEALER"


@pytest.mark.parametrize("key", sorted(LEGACY_HEADER_ONLY))
def test_legacy_card_has_header(key):
    assert _sql(_card(key)).startswith(LEGACY_HEADER)
```

- [ ] **Step 2: Run it**

Run the repo tests (shared command). Expected: `test_repo_tree_validates_clean`, `test_dashcards_do_not_overlap` and every `test_every_series_card_gets_every_filter_of_its_dashcard[...]` PASS; the four parametrized tests over empty sets are reported as SKIPPED ("got empty parameter set"). If `from mbcode.validate import validate_tree` fails to import, check `tests/conftest.py` for how the other tests put the repo root on `sys.path` and do the same.

- [ ] **Step 3: Prove the overlap and mapping tests can fail**

Temporarily change `dc-orders-in-progress` in the dashboard YAML to `col: 0`, run the tests, expect `test_dashcards_do_not_overlap` FAIL naming `dc-orders-in-progress` and `dc-time-since-last-certificate`, then restore `col: 12` (`git diff dashboards/` must be empty afterwards).

- [ ] **Step 4: Checkpoint**

`git status --short` shows only `tests/test_dealer_dashboard.py` (plus this plan and `docs/superpowers/`). No commit (owner permission, Task 12).

---

### Task 3: Header comments and DEALER exclusion on cards without twins

Spec sections 1, 3 (legacy email header), 4. Only SQL comments and one `AND`/`WHERE` line per card; no dashboard change.

**Files:**
- Modify (header only, group A — certificate sales): `cards/pca-certificate-face-value-by-day.yaml`, `cards/pca-average-check-by-day.yaml`, `cards/pca-certificates-sent-window.yaml`, `cards/pca-certificates-sent-by-hour.yaml`, `cards/pca-certificates-sold-by-denomination.yaml`, `cards/pca-certificate-turnover-by-denomination.yaml`, `cards/pca-time-since-last-certificate.yaml`, `cards/pca-certificate-face-value-by-day-by-provider.yaml`
- Modify (header only, group B — pool and production): `cards/pca-produced-face-value-by-day-by-provider.yaml`, `cards/pca-certificates-produced-by-day.yaml`, `cards/pca-certificates-in-pool-by-denomination.yaml`, `cards/pca-pool-total-value.yaml`, `cards/pca-production-queue-depth.yaml`, `cards/pca-production-queue-by-denomination.yaml`, `cards/pca-production-requests-by-hour.yaml`
- Modify (exclusion, off-dashboard): `cards/pca-orders-by-hour-sber.yaml`, `cards/pca-orders-by-hour-other.yaml`, `cards/pca-orders-aging-sber.yaml`, `cards/pca-orders-aging-other.yaml`, `cards/pca-orders-in-progress-by-status-partner.yaml`, `cards/pca-completion-time-percentiles.yaml`, `cards/pca-sales-distribution-by-partner.yaml`
- Test: `tests/test_dealer_dashboard.py`

**Interfaces:**
- Consumes: Task 2 sets.
- Produces: nothing used later.

- [ ] **Step 1: Register the cards in the test sets (failing test)**

In `tests/test_dealer_dashboard.py` replace the two empty set literals:

```python
DEALER_EXCLUDED: set[str] = {
    "pca-orders-by-hour-sber",
    "pca-orders-by-hour-other",
    "pca-orders-aging-sber",
    "pca-orders-aging-other",
    "pca-orders-in-progress-by-status-partner",
    "pca-completion-time-percentiles",
    "pca-sales-distribution-by-partner",
}
# Legacy cards whose numbers already include dealer volume; header says so (spec section 1).
DEALER_INCLUDED_NOTE: set[str] = {
    "pca-certificate-face-value-by-day",
    "pca-average-check-by-day",
    "pca-certificates-sent-window",
    "pca-certificates-sent-by-hour",
    "pca-certificates-sold-by-denomination",
    "pca-certificate-turnover-by-denomination",
    "pca-time-since-last-certificate",
    "pca-certificate-face-value-by-day-by-provider",
    "pca-produced-face-value-by-day-by-provider",
    "pca-certificates-produced-by-day",
    "pca-certificates-in-pool-by-denomination",
    "pca-pool-total-value",
    "pca-production-queue-depth",
    "pca-production-queue-by-denomination",
    "pca-production-requests-by-hour",
}
```

`pca-emails-failed-top-domains` is deliberately in no set: the spec leaves it unchanged.

- [ ] **Step 2: Run the tests, expect failures**

Run the repo tests. Expected: 22 FAIL (7 `test_legacy_card_excludes_dealer_client`, 15 `test_legacy_card_states_dealer_volume_is_included`), all on `startswith(LEGACY_HEADER)`.

- [ ] **Step 3: Add the group A header**

In each group A card, insert these lines as the first lines of the `native: |` block (6-space indent, above any existing comment):

```sql
      -- Database 2 (legacy orchestrator). Includes dealer sales: the dealer buys every voucher it sells
      -- from legacy as an order of the DEALER pseudo-client 00000000-0000-0000-0000-000000000002, so those
      -- certificates are counted here. There is no dealer-DB twin: adding one would double-count.
```

- [ ] **Step 4: Add the group B header**

In each group B card, insert as the first lines of the `native: |` block:

```sql
      -- Database 2 (legacy orchestrator). Pool and production exist only here. Demand from dealer sales
      -- reaches them through orders of the DEALER pseudo-client 00000000-0000-0000-0000-000000000002,
      -- so it is included; the dealer DB has no equivalent to twin.
```

- [ ] **Step 5: Exclude DEALER in the off-dashboard cards**

For each card insert the header as the first lines of `native: |`, then add the exclusion line exactly where shown.

Header for all seven:

```sql
      -- Database 2 (legacy orchestrator). Legacy client orders only: orders of the DEALER pseudo-client
      -- (the dealer's own wholesale purchases) are excluded; clients switched to the dealer have their
      -- orders in the dealer DB, which this card does not read.
```

`pca-orders-by-hour-sber.yaml` and `pca-orders-by-hour-other.yaml` — in CTE `o`, first branch (from `order_`), after the `AND c.name = 'sber'` / `AND c.name IS DISTINCT FROM 'sber'` line of that branch (not the bulk branch):

```sql
          AND o.client_id <> '00000000-0000-0000-0000-000000000002'  -- DEALER pseudo-client: dealer purchases, counted in the dealer DB
```

`pca-orders-aging-sber.yaml` and `pca-orders-aging-other.yaml` — after the `AND c.name = 'sber'` / `AND c.name IS DISTINCT FROM 'sber'` line:

```sql
          AND o.client_id <> '00000000-0000-0000-0000-000000000002'  -- DEALER pseudo-client: dealer purchases, counted in the dealer DB
```

`pca-orders-in-progress-by-status-partner.yaml` — in CTE `live`, the closing `)` of the CTE moves to its own line, because the test asserts the literal `'…0002'  -- DEALER pseudo-client: …` (closing quote, two spaces, comment) with nothing in between:

```sql
        WHERE o.status IN ('AWAITING_PAYMENT', 'PAID', 'WAITING_CERT', 'SENDING')
          AND o.client_id <> '00000000-0000-0000-0000-000000000002'  -- DEALER pseudo-client: dealer purchases, counted in the dealer DB
        )
```

`pca-completion-time-percentiles.yaml` — in CTE `spans`, before the `AND o.completed_at >= …` line:

```sql
          AND o.client_id <> '00000000-0000-0000-0000-000000000002'  -- DEALER pseudo-client: dealer purchases, counted in the dealer DB
```

`pca-sales-distribution-by-partner.yaml` — between `LEFT JOIN api_client c …` and `GROUP BY 1`:

```sql
      WHERE o.client_id <> '00000000-0000-0000-0000-000000000002'  -- DEALER pseudo-client: dealer purchases, counted in the dealer DB
```

Update the `description:` of each of the seven cards by appending: ` Legacy client orders only; DEALER pseudo-client purchases are excluded.`

- [ ] **Step 6: Run the tests and validate**

Run the repo tests: all PASS. Run `./mbc validate`: no problems. (`_flatten_sql` strips everything after `--`, so the UUID literal and the trailing comment do not trip the timezone lint.)

- [ ] **Step 7: Run every edited off-dashboard SQL on database 2**

For each of the seven exclusion cards: render (shared command) and run via Metabase MCP `execute_query` on database 2. Expected: no error; row counts equal to or lower than before the edit. One spot check of the difference — run on database 2:

```sql
SELECT count(*) FILTER (WHERE client_id = '00000000-0000-0000-0000-000000000002') AS dealer_orders,
       count(*) AS all_orders
FROM order_
WHERE created_at >= (now() AT TIME ZONE 'UTC') - INTERVAL '7 days'
```

`dealer_orders` is how many rows left the "Other"/"Unknown" buckets.

- [ ] **Step 8: Checkpoint**

`git diff --stat cards/ tests/` lists 22 card files and the test file. No commit.

---

### Task 4: By-client face value — exclude DEALER, add dealer twin as series

**Files:**
- Modify: `cards/pca-certificate-face-value-by-day-by-client.yaml`
- Create: `cards/pca-certificate-face-value-by-day-by-client-dealer.yaml`
- Modify: `dashboards/pear-cider-by-erjan-solutions-automated.yaml` (dashcard `dc-certificate-face-value-by-day-by-client`)
- Test: `tests/test_dealer_dashboard.py`

**Interfaces:**
- Consumes: Task 1 `SERIES_MODE`; Task 2 helpers and sets.
- Produces: card `pca-certificate-face-value-by-day-by-client-dealer` with columns `day` (timestamp), `client` (text), `face_value` (numeric, rubles).

- [ ] **Step 1: Write the failing tests**

Add `"pca-certificate-face-value-by-day-by-client"` to `DEALER_EXCLUDED`, and append:

```python
def test_by_client_exclusion_keeps_null_order():
    # Certificates of bulk orders have no reserved order: o.client_id is NULL there.
    # `<>` would drop them; IS DISTINCT FROM keeps them.
    sql = _sql(_card("pca-certificate-face-value-by-day-by-client"))
    assert f"o.client_id IS DISTINCT FROM '{DEALER_CLIENT}'" in sql
    assert f"o.client_id <> '{DEALER_CLIENT}'" not in sql


def test_by_client_twin_never_mixes_currencies():
    sql = _sql(_card("pca-certificate-face-value-by-day-by-client-dealer"))
    # The series label carries the currency for anything not RUB, and the label is a GROUP BY key.
    assert "CASE WHEN face_currency = 'RUB' THEN ''" in sql
    assert "GROUP BY 1, 2" in sql


def test_by_client_dashcard_stacks_the_twin():
    dc = next(d for d in _dashcards() if d["key"] == "dc-certificate-face-value-by-day-by-client")
    assert dc["series"] == ["pca-certificate-face-value-by-day-by-client-dealer"]
```

Because `test_legacy_card_excludes_dealer_client` asserts the `'…0002'  -- DEALER pseudo-client…` literal and this card uses `IS DISTINCT FROM`, the literal still matches (`IS DISTINCT FROM '…0002'  -- DEALER …` ends the same way).

If Task 1 decided `SERIES_MODE = pairs`, replace `test_by_client_dashcard_stacks_the_twin` with an assertion that a dashcard `dc-certificate-face-value-by-day-by-client-dealer` exists on tab `orders` with `card: pca-certificate-face-value-by-day-by-client-dealer`.

- [ ] **Step 2: Run, expect failure**

Run the repo tests. Expected FAIL: the four new/extended tests (missing twin file → `FileNotFoundError`, missing header, missing series).

- [ ] **Step 3: Edit the legacy card**

Replace the comment block and the `cert` CTE start of `native:` so it reads:

```sql
      -- Database 2 (legacy orchestrator). Face value of SENT certificates per bucket, by API client.
      -- Excludes certificates reserved for DEALER pseudo-client orders: those are vouchers the dealer bought
      -- for its own clients, shown by client in the "Dealer · …" series from the dealer DB. Stacked together,
      -- the two equal "Certificate face value over time".
      -- Days are Europe/Moscow days; certificate.sent_at is naive UTC.
      -- Client = the API client of the order the certificate was reserved for, else of its bulk order
      -- (falling back to bulk_order.client_name). No link at all = Unknown.
      WITH cert AS (
```

In the `WHERE` of `cert`, after `AND c.sent_at <  (now() AT TIME ZONE 'UTC')`, add:

```sql
          AND o.client_id IS DISTINCT FROM '00000000-0000-0000-0000-000000000002'  -- DEALER pseudo-client: dealer purchases, counted in the dealer DB
```

Change the label expression in the final `SELECT` to:

```sql
             'Legacy · ' || CASE WHEN client_name = 'sber' THEN 'Sber' ELSE COALESCE(initcap(client_name), 'Unknown') END AS client,
```

Change `series_settings` keys to `Legacy · Sber` and `Legacy · Unknown` (same colors). Append to `description:` ` Legacy only: certificates bought by the dealer (DEALER pseudo-client) are excluded; the dealer's clients are the stacked Dealer series.`

- [ ] **Step 4: Create the twin**

`cards/pca-certificate-face-value-by-day-by-client-dealer.yaml`:

```yaml
kind: card
key: pca-certificate-face-value-by-day-by-client-dealer
name: Dealer · Certificate face value over time by client, ₽
description: Face value of COMPLETED dealer orders per bucket, Europe/Moscow, stacked by API client. Read from the dealer database; stacked on the legacy by-client chart. RUB in rubles; any other currency is its own series labelled with its currency code.
collection: pear-cider-automated
type: question
display: bar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 3
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Database 3 (dealer). Face value of COMPLETED dealer orders per bucket, by API client.
      -- These are the orders of clients switched to the dealer. Legacy holds the same vouchers under the
      -- DEALER pseudo-client, which the legacy by-client card excludes, so the stacked total is not doubled.
      -- face_amount is BIGINT minor units: RUB is shown in rubles (/ 100.0). Another currency is never
      -- added to rubles: it becomes its own series, labelled with its currency code. Orders without a face
      -- value (face_amount NULL, a NONE-kind SKU) are not counted.
      -- Bucketed on completed_at (timestamptz) in Europe/Moscow.
      WITH o AS (
        SELECT date_bin({{granularity}}::text::interval, (o.completed_at AT TIME ZONE 'Europe/Moscow'), TIMESTAMP '2000-01-03') AS day,
               o.face_amount,
               o.face_currency,
               c.name AS client_name
        FROM dealer_order o
        JOIN api_client c ON c.client_id = o.client_id
        WHERE o.status = 'COMPLETED'
          AND o.face_amount IS NOT NULL
          AND o.completed_at >= (date_bin({{granularity}}::text::interval, (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day'), TIMESTAMP '2000-01-03') AT TIME ZONE 'Europe/Moscow')
      )
      SELECT day,
             'Dealer · ' || CASE WHEN client_name = 'sber' THEN 'Sber' ELSE initcap(client_name) END
               || CASE WHEN face_currency = 'RUB' THEN '' ELSE ' (' || COALESCE(face_currency, 'no currency') || ')' END AS client,
             sum(face_amount) / 100.0 AS face_value
      FROM o
      GROUP BY 1, 2
      ORDER BY 1, 2
    template-tags:
      days:
        id: 0370f0c7-71b0-477e-8fca-f483a508acfe
        name: days
        display-name: Days
        type: number
        default: '7'
      granularity:
        id: 7fbe7444-7de7-4478-a01c-d61ae3859563
        name: granularity
        display-name: Granularity
        type: text
        default: '1 day'
visualization_settings:
  graph.dimensions:
  - day
  - client
  graph.metrics:
  - face_value
  graph.x_axis.scale: timeseries
  graph.y_axis.title_text: Certificate face value, ₽
  graph.y_axis.auto_split: false
  graph.show_values: false
  stackable.stack_type: stacked
  column_settings:
    '["name","face_value"]':
      suffix: ' ₽'
      decimals: 0
parameters: []
```

- [ ] **Step 5: Wire the dashcard**

In `dashboards/pear-cider-by-erjan-solutions-automated.yaml`, dashcard `dc-certificate-face-value-by-day-by-client`: set `series` and add the twin's mappings (stacked mode):

```yaml
  parameter_mappings:
  - parameter_id: 821ec3b1
    target:
    - variable
    - - template-tag
      - days
  - parameter_id: 3f9a20c7
    target:
    - variable
    - - template-tag
      - granularity
  - parameter_id: 821ec3b1
    card: pca-certificate-face-value-by-day-by-client-dealer
    target:
    - variable
    - - template-tag
      - days
  - parameter_id: 3f9a20c7
    card: pca-certificate-face-value-by-day-by-client-dealer
    target:
    - variable
    - - template-tag
      - granularity
  series:
  - pca-certificate-face-value-by-day-by-client-dealer
```

(Pairs mode: leave this dashcard's `series: []`, set it to `size_x: 12`, and add `dc-certificate-face-value-by-day-by-client-dealer` at `tab: orders, row: 8, col: 12, size_x: 12, size_y: 8` with the two un-scoped Days/Granularity mappings.)

- [ ] **Step 6: Run tests and validate**

Run the repo tests: PASS. `./mbc validate`: clean.

- [ ] **Step 7: Run both SQLs over MCP**

Render and run the legacy card on database 2 and the twin on database 3. Expected: legacy has no error and its Bulk/Unknown/Sber rows are unchanged versus the pre-edit SQL (run `git show HEAD:cards/pca-certificate-face-value-by-day-by-client.yaml` through the render command for the comparison; only the rows labelled with the DEALER pseudo-client's name (or `Unknown`, if it has no `api_client` row) may shrink or disappear). Twin: no error; today ≤ 2 rows (`Dealer · Test`), since the dealer DB holds 2 COMPLETED test orders.

- [ ] **Step 8: Checkpoint**

`git diff --stat` — 2 cards (1 new), dashboard, test file. No commit.

---

### Task 5: Flow chart — DEALER out of legacy "Ordered", dealer "Ordered" stream as series

**Files:**
- Modify: `cards/pca-flow-by-hour.yaml`
- Create: `cards/pca-flow-by-hour-dealer.yaml`
- Modify: `dashboards/pear-cider-by-erjan-solutions-automated.yaml` (dashcard `dc-flow-by-hour`)
- Test: `tests/test_dealer_dashboard.py`

**Interfaces:**
- Consumes: Task 1 `SERIES_MODE`.
- Produces: card `pca-flow-by-hour-dealer` with columns `hour`, `series` (always `Dealer · Ordered`), `n`.

- [ ] **Step 1: Failing test**

Add `"pca-flow-by-hour"` to `DEALER_EXCLUDED` and append:

```python
def test_flow_excludes_dealer_only_from_ordered_stream():
    sql = _sql(_card("pca-flow-by-hour"))
    assert sql.count(DEALER_CLIENT) == 1
    ordered_branch = sql.split("SELECT 'Legacy · Ordered'")[1]
    assert DEALER_CLIENT in ordered_branch.split("series AS")[0]


def test_flow_dashcard_stacks_the_twin():
    dc = next(d for d in _dashcards() if d["key"] == "dc-flow-by-hour")
    assert dc["series"] == ["pca-flow-by-hour-dealer"]
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Edit the legacy card**

Replace the first comment line of `native:` with:

```sql
      -- Database 2 (legacy orchestrator). Four event streams per bucket. Requested/Produced/Sent include
      -- dealer volume (the dealer buys each voucher here as a DEALER pseudo-client order). "Legacy · Ordered"
      -- excludes those DEALER orders; client orders placed with the dealer are the "Dealer · Ordered" series
      -- from the dealer DB. Every series uses a real event timestamp; none needs a proxy.
```

In the Ordered branch of `events` change `SELECT 'Ordered',` to `SELECT 'Legacy · Ordered',` and replace its `WHERE` line + the CTE-closing `)` with:

```sql
        WHERE created_at >= (date_bin({{granularity}}::text::interval, (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day'), TIMESTAMP '2000-01-03') AT TIME ZONE 'Europe/Moscow' AT TIME ZONE 'UTC')
          AND client_id <> '00000000-0000-0000-0000-000000000002'  -- DEALER pseudo-client: dealer purchases, counted in the dealer DB
        ),
```

Change `series AS (SELECT unnest(ARRAY['Requested', 'Produced', 'Sent', 'Ordered']) AS series)` to `... 'Sent', 'Legacy · Ordered']) ...`. Rename `series_settings` key `Ordered` → `Legacy · Ordered` (color `#7172AD`) and add `Dealer · Ordered` with color `#A989C5`.

- [ ] **Step 4: Create the twin**

`cards/pca-flow-by-hour-dealer.yaml`:

```yaml
kind: card
key: pca-flow-by-hour-dealer
name: Dealer · Flow over time — ordered
description: Dealer orders created per bucket (all statuses), Europe/Moscow. Read from the dealer database; stacked on the legacy Flow chart as the Dealer · Ordered line. Idle buckets render as zero.
collection: pear-cider-automated
type: question
display: line
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 3
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Database 3 (dealer). The "Ordered" stream for clients switched to the dealer: dealer_order rows by
      -- created_at (timestamptz), Europe/Moscow buckets, every status. Requested/Produced/Sent stay on the
      -- legacy card only: production and certificates live there and already include dealer volume.
      WITH spine AS (
        SELECT generate_series(
          date_bin({{granularity}}::text::interval, (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day'), TIMESTAMP '2000-01-03'),
          (now() AT TIME ZONE 'Europe/Moscow'),
          {{granularity}}::text::interval) AS h),
      events AS (
        SELECT date_bin({{granularity}}::text::interval, (created_at AT TIME ZONE 'Europe/Moscow'), TIMESTAMP '2000-01-03') AS h
        FROM dealer_order
        WHERE created_at >= (date_bin({{granularity}}::text::interval, (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day'), TIMESTAMP '2000-01-03') AT TIME ZONE 'Europe/Moscow'))
      SELECT s.h AS hour, 'Dealer · Ordered' AS series, count(e.h) AS n
      FROM spine s
      LEFT JOIN events e ON e.h = s.h
      GROUP BY 1, 2
      ORDER BY 1, 2
    template-tags:
      days:
        id: 8e93ce46-4c0c-40a2-b2b7-e7b7cc1465d3
        name: days
        display-name: Days
        type: number
        default: '7'
      granularity:
        id: 0a47da98-8357-4f2e-a272-50fc03b2c4f5
        name: granularity
        display-name: Granularity
        type: text
        default: '1 day'
visualization_settings:
  graph.dimensions:
  - hour
  - series
  graph.metrics:
  - n
  graph.x_axis.scale: timeseries
  graph.y_axis.scale: log
  graph.y_axis.title_text: Events per bucket
  series_settings:
    Dealer · Ordered:
      color: '#A989C5'
parameters: []
```

- [ ] **Step 5: Wire the dashcard**

`dc-flow-by-hour`: append to `parameter_mappings`:

```yaml
  - parameter_id: 821ec3b1
    card: pca-flow-by-hour-dealer
    target:
    - variable
    - - template-tag
      - days
  - parameter_id: 3f9a20c7
    card: pca-flow-by-hour-dealer
    target:
    - variable
    - - template-tag
      - granularity
```

and set:

```yaml
  series:
  - pca-flow-by-hour-dealer
```

(Pairs mode: `dc-flow-by-hour` to `size_x: 12`; add `dc-flow-by-hour-dealer` at `tab: overview, row: 26, col: 12, size_x: 12, size_y: 7` with un-scoped Days/Granularity mappings.)

- [ ] **Step 6: Tests, validate, MCP**

Repo tests PASS; `./mbc validate` clean. Render + run legacy on database 2 (expect four series, `Legacy · Ordered` ≤ old `Ordered` per bucket) and twin on database 3 (expect one row per bucket, `n` mostly 0).

- [ ] **Step 7: Checkpoint** — `git diff --stat`; no commit.

---

### Task 6: Orders in progress — legacy without DEALER, dealer scalar beside it

**Files:**
- Modify: `cards/pca-orders-in-progress.yaml`
- Create: `cards/pca-orders-in-progress-dealer.yaml`
- Modify: `dashboards/pear-cider-by-erjan-solutions-automated.yaml` (dashcard `dc-orders-in-progress`, new `dc-orders-in-progress-dealer`)
- Test: `tests/test_dealer_dashboard.py`

**Interfaces:**
- Produces: card `pca-orders-in-progress-dealer`, column `orders_in_progress` (bigint).

- [ ] **Step 1: Failing test**

Add `"pca-orders-in-progress"` to `DEALER_EXCLUDED` and append:

```python
def _pair(legacy_dashcard_key, twin_dashcard_key):
    by_key = {d["key"]: d for d in _dashcards()}
    return by_key[legacy_dashcard_key], by_key[twin_dashcard_key]


def test_orders_in_progress_pair_sits_side_by_side():
    legacy, twin = _pair("dc-orders-in-progress", "dc-orders-in-progress-dealer")
    assert (legacy["row"], legacy["col"], legacy["size_x"]) == (13, 12, 6)
    assert (twin["row"], twin["col"], twin["size_x"], twin["size_y"]) == (13, 18, 6, legacy["size_y"])
    assert twin["card"] == "pca-orders-in-progress-dealer"
    assert _card("pca-orders-in-progress")["name"] == "Legacy · Orders in progress"
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Edit the legacy card**

Set `name: Legacy · Orders in progress`, `description: Legacy orders in a non-terminal state, including AWAITING_PAYMENT. Excludes the DEALER pseudo-client; open dealer orders are on the Dealer card beside it.` and `native:`:

```sql
      -- Database 2 (legacy orchestrator). Legacy orders in a non-terminal state, including AWAITING_PAYMENT.
      -- Excludes the DEALER pseudo-client: those are the dealer's own purchases; the dealer's open client
      -- orders are on the "Dealer · Orders in progress" card next to this one.
      SELECT count(*) AS orders_in_progress
      FROM order_
      WHERE status IN ('AWAITING_PAYMENT', 'PAID', 'WAITING_CERT', 'SENDING')
        AND client_id <> '00000000-0000-0000-0000-000000000002'  -- DEALER pseudo-client: dealer purchases, counted in the dealer DB
```

- [ ] **Step 4: Create the twin**

`cards/pca-orders-in-progress-dealer.yaml`:

```yaml
kind: card
key: pca-orders-in-progress-dealer
name: Dealer · Orders in progress
description: Dealer orders not yet in a terminal state (IN_QUEUE, IN_PROGRESS), all clients. Read from the dealer database.
collection: pear-cider-automated
type: question
display: scalar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 3
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Database 3 (dealer). Dealer orders not yet in a terminal state, all clients.
      -- Non-terminal: IN_QUEUE, IN_PROGRESS. Terminal: COMPLETED, FAILED, REJECTED (dealer_order_status_valid).
      SELECT count(*) AS orders_in_progress
      FROM dealer_order
      WHERE status IN ('IN_QUEUE', 'IN_PROGRESS')
visualization_settings: {}
parameters: []
```

- [ ] **Step 5: Dashboard**

`dc-orders-in-progress`: `size_x: 6` (row 13, col 12 unchanged). Add right after it:

```yaml
- key: dc-orders-in-progress-dealer
  card: pca-orders-in-progress-dealer
  tab: overview
  row: 13
  col: 18
  size_x: 6
  size_y: 3
  visualization_settings: {}
  parameter_mappings: []
  series: []
  inline_parameters: []
```

- [ ] **Step 6: Tests, validate, MCP** — repo tests PASS; validate clean; legacy on database 2 and twin on database 3 each return one row.

- [ ] **Step 7: Checkpoint** — no commit.

---

### Task 7: Time since last order — legacy without DEALER, dealer table beside it

**Files:**
- Modify: `cards/pca-time-since-last-order-by-partner.yaml`
- Create: `cards/pca-time-since-last-order-by-partner-dealer.yaml`
- Modify: dashboard (`dc-time-since-last-order-by-partner`, new `dc-time-since-last-order-by-partner-dealer`)
- Test: `tests/test_dealer_dashboard.py`

**Interfaces:**
- Consumes: `_pair` helper from Task 6.
- Produces: card `pca-time-since-last-order-by-partner-dealer`, columns `partner`, `last_order_at`, `hours_since`.

- [ ] **Step 1: Failing test**

Add `"pca-time-since-last-order-by-partner"` to `DEALER_EXCLUDED` and append:

```python
def test_time_since_last_order_pair_sits_side_by_side():
    legacy, twin = _pair("dc-time-since-last-order-by-partner", "dc-time-since-last-order-by-partner-dealer")
    assert (legacy["row"], legacy["col"], legacy["size_x"]) == (20, 0, 12)
    assert (twin["row"], twin["col"], twin["size_x"], twin["size_y"]) == (20, 12, 12, legacy["size_y"])
    assert _card("pca-time-since-last-order-by-partner")["name"] == "Legacy · Time since last order, by partner"
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Edit the legacy card**

`name: Legacy · Time since last order, by partner`; append to description ` Excludes the DEALER pseudo-client; switched clients appear on the Dealer table beside it.` `native:`:

```sql
      -- Database 2 (legacy orchestrator). Most recent legacy order per partner, stalest first.
      -- Excludes the DEALER pseudo-client (the dealer's own purchases). A client switched to the dealer stops
      -- ordering here and grows stale on purpose; its new orders are on the "Dealer · …" table beside it.
      -- order_.created_at is naive UTC: compare against naive-UTC now, display in Moscow.
      SELECT partner, last_order_at, hours_since
      FROM (
        SELECT CASE WHEN c.name = 'sber' THEN 'Sber' ELSE COALESCE(initcap(c.name), 'Unknown') END AS partner,
               max((o.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow'))        AS last_order_at,
               round(EXTRACT(EPOCH FROM ((now() AT TIME ZONE 'UTC') - max(o.created_at))) / 3600.0, 1)
                 AS hours_since
        FROM order_ o
        LEFT JOIN api_client c ON c.client_id = o.client_id
        WHERE o.client_id <> '00000000-0000-0000-0000-000000000002'  -- DEALER pseudo-client: dealer purchases, counted in the dealer DB
        GROUP BY 1) t
      ORDER BY hours_since DESC
```

- [ ] **Step 4: Create the twin**

`cards/pca-time-since-last-order-by-partner-dealer.yaml`:

```yaml
kind: card
key: pca-time-since-last-order-by-partner-dealer
name: Dealer · Time since last order, by partner
description: Most recent dealer order per API client, stalest first. Read from the dealer database. Last order time and hours since are both Europe/Moscow.
collection: pear-cider-automated
type: question
display: table
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 3
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Database 3 (dealer). Most recent dealer order per API client, stalest first, every status.
      -- created_at is timestamptz: shown in Europe/Moscow; hours since = Moscow now minus Moscow last order.
      SELECT partner, last_order_at, hours_since
      FROM (
        SELECT CASE WHEN c.name = 'sber' THEN 'Sber' ELSE initcap(c.name) END AS partner,
               max((o.created_at AT TIME ZONE 'Europe/Moscow')) AS last_order_at,
               round(EXTRACT(EPOCH FROM ((now() AT TIME ZONE 'Europe/Moscow') - max((o.created_at AT TIME ZONE 'Europe/Moscow')))) / 3600.0, 1)
                 AS hours_since
        FROM dealer_order o
        JOIN api_client c ON c.client_id = o.client_id
        GROUP BY 1) t
      ORDER BY hours_since DESC
visualization_settings:
  column_settings:
    '["name","partner"]':
      column_title: Partner
    '["name","last_order_at"]':
      column_title: Last order at
    '["name","hours_since"]':
      column_title: Hours since last order
      suffix: ' h'
parameters: []
```

- [ ] **Step 5: Dashboard**

`dc-time-since-last-order-by-partner`: `size_x: 12`. Add after it:

```yaml
- key: dc-time-since-last-order-by-partner-dealer
  card: pca-time-since-last-order-by-partner-dealer
  tab: overview
  row: 20
  col: 12
  size_x: 12
  size_y: 5
  visualization_settings: {}
  parameter_mappings: []
  series: []
  inline_parameters: []
```

- [ ] **Step 6: Tests, validate, MCP** — PASS / clean; twin on database 3 returns `Test` with the age of its last order.

- [ ] **Step 7: Checkpoint** — no commit.

---

### Task 8: Emails sent and failed — dealer twins as series

**Files:**
- Modify: `cards/pca-emails-sent-by-hour-by-domain.yaml`, `cards/pca-emails-failed-by-hour-by-domain.yaml`
- Create: `cards/pca-emails-sent-by-hour-by-domain-dealer.yaml`, `cards/pca-emails-failed-by-hour-by-domain-dealer.yaml`
- Modify: dashboard (`dc-emails-sent-by-hour-by-domain`, `dc-emails-failed-by-hour-by-domain`)
- Test: `tests/test_dealer_dashboard.py`

**Interfaces:**
- Produces: `pca-emails-sent-by-hour-by-domain-dealer` (`hour`, `domain`, `emails`); `pca-emails-failed-by-hour-by-domain-dealer` (`hour`, `domain`, `emails`).

- [ ] **Step 1: Failing test**

Add both legacy keys to `LEGACY_HEADER_ONLY` and append:

```python
def test_dealer_failed_emails_count_terminal_failures_only():
    # BOUNCED is a retryable bounce waiting for its next attempt (Shipment.markBounced), not a failure.
    sql = _sql(_card("pca-emails-failed-by-hour-by-domain-dealer"))
    assert "s.status = 'FAILED'" in sql
    assert "'BOUNCED'" not in sql


@pytest.mark.parametrize("dashcard_key, twin", [
    ("dc-emails-sent-by-hour-by-domain", "pca-emails-sent-by-hour-by-domain-dealer"),
    ("dc-emails-failed-by-hour-by-domain", "pca-emails-failed-by-hour-by-domain-dealer"),
])
def test_email_dashcards_stack_their_twins(dashcard_key, twin):
    dc = next(d for d in _dashcards() if d["key"] == dashcard_key)
    assert dc["series"] == [twin]
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Legacy sent card**

Prepend to `native:`:

```sql
      -- Database 2 (legacy orchestrator). Emails sent per bucket by sender domain, one per order
      -- (order_.sent_confirmed_at). No DEALER filter needed: DEALER pseudo-client orders are delivered over
      -- the API and send no email. Dealer emails are the stacked "Dealer · …" series from the dealer DB.
```

Change `lower(split_part(sent_from_address, '@', 2)) AS domain` to `'Legacy · ' || lower(split_part(sent_from_address, '@', 2)) AS domain`. Rename `series_settings` keys to `Legacy · apple-gift.org` and `Legacy · engine.smartfruit.org` (same colors).

- [ ] **Step 4: Legacy failed card**

Prepend to `native:`:

```sql
      -- Database 2 (legacy orchestrator). Orders whose certificate email failed terminally, by recipient
      -- domain. No DEALER filter needed: DEALER pseudo-client orders send no email. Dealer email failures
      -- are the stacked "Dealer · …" series from the dealer DB.
```

Change the final `SELECT s.h AS hour, se.series AS domain, …` to `SELECT s.h AS hour, 'Legacy · ' || se.series AS domain, count(l.series) AS emails`. Rename `series_settings` key `Other` → `Legacy · Other`.

- [ ] **Step 5: Create the sent twin**

`cards/pca-emails-sent-by-hour-by-domain-dealer.yaml`:

```yaml
kind: card
key: pca-emails-sent-by-hour-by-domain-dealer
name: Dealer · Emails sent over time, by sender domain
description: Dealer email send attempts per bucket over the Days window, split by the domain of the From address. Every attempt counts, including retries and courtesy resends. Read from the dealer database. Buckets follow the Granularity filter, Europe/Moscow.
collection: pear-cider-automated
type: question
display: bar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 3
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Database 3 (dealer). Email send attempts per bucket by sender domain: shipment_attempt rows with a
      -- sent_at (timestamptz), domain of from_address. Unlike legacy (one per order), every attempt counts:
      -- retries and courtesy resends of a delivered voucher are separate emails.
      WITH spine AS (
        SELECT generate_series(
          date_bin({{granularity}}::text::interval, (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day'), TIMESTAMP '2000-01-03'),
          (now() AT TIME ZONE 'Europe/Moscow'),
          {{granularity}}::text::interval) AS h),
      mail AS (
        SELECT date_bin({{granularity}}::text::interval, (a.sent_at AT TIME ZONE 'Europe/Moscow'), TIMESTAMP '2000-01-03') AS h,
               'Dealer · ' || lower(split_part(a.from_address, '@', 2)) AS domain
        FROM shipment_attempt a
        WHERE a.sent_at >= (date_bin({{granularity}}::text::interval, (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day'), TIMESTAMP '2000-01-03') AT TIME ZONE 'Europe/Moscow')
          AND a.from_address IS NOT NULL),
      domains AS (SELECT DISTINCT domain FROM mail)
      SELECT s.h AS hour, d.domain, count(m.domain) AS emails
      FROM spine s
      CROSS JOIN domains d
      LEFT JOIN mail m ON m.h = s.h AND m.domain = d.domain
      GROUP BY 1, 2
      ORDER BY 1, 2
    template-tags:
      days:
        id: 043ab384-9db9-4de1-a40f-d2cebdc33018
        name: days
        display-name: Days
        type: number
        default: '7'
      granularity:
        id: 374796c8-ea0b-4817-b26e-76546e843856
        name: granularity
        display-name: Granularity
        type: text
        default: '1 day'
visualization_settings:
  graph.dimensions:
  - hour
  - domain
  graph.metrics:
  - emails
  graph.x_axis.scale: timeseries
  graph.y_axis.title_text: Emails sent
  stackable.stack_type: stacked
parameters: []
```

- [ ] **Step 6: Create the failed twin**

`cards/pca-emails-failed-by-hour-by-domain-dealer.yaml`:

```yaml
kind: card
key: pca-emails-failed-by-hour-by-domain-dealer
name: Dealer · Failed emails over time, by recipient domain
description: Dealer shipments that failed terminally (status FAILED) per bucket over the Days window, by recipient domain — top 7 domains get their own series, the rest are Dealer · Other. Anchored to sent_at when known, else created_at. BOUNCED is a retryable state and is not counted. Read from the dealer database. Buckets follow the Granularity filter, Europe/Moscow.
collection: pear-cider-automated
type: question
display: bar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 3
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Database 3 (dealer). Shipments that failed terminally, by recipient domain.
      -- Failed = shipment.status 'FAILED' (Shipment.markFailed). BOUNCED is excluded on purpose: it is a
      -- retryable bounce waiting for its next attempt (Shipment.markBounced), like legacy SENDING, which the
      -- legacy card also leaves out. Anchored to sent_at (timestamptz) when known, else created_at.
      WITH failed AS (
        SELECT date_bin({{granularity}}::text::interval,
                 (COALESCE(s.sent_at, s.created_at) AT TIME ZONE 'Europe/Moscow'), TIMESTAMP '2000-01-03') AS h,
               lower(split_part(s.recipient, '@', 2)) AS domain
        FROM shipment s
        WHERE s.status = 'FAILED'
          AND COALESCE(s.sent_at, s.created_at) >= (date_bin({{granularity}}::text::interval, (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day'), TIMESTAMP '2000-01-03') AT TIME ZONE 'Europe/Moscow')),
      top_domains AS (
        SELECT domain FROM failed GROUP BY domain ORDER BY count(*) DESC, domain LIMIT 7),
      labelled AS (
        SELECT f.h, COALESCE(t.domain, 'Other') AS series
        FROM failed f LEFT JOIN top_domains t ON t.domain = f.domain),
      spine AS (
        SELECT generate_series(
          date_bin({{granularity}}::text::interval, (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day'), TIMESTAMP '2000-01-03'),
          (now() AT TIME ZONE 'Europe/Moscow'),
          {{granularity}}::text::interval) AS h),
      series AS (
        SELECT domain AS series FROM top_domains
        UNION ALL
        SELECT 'Other' WHERE (SELECT count(DISTINCT domain) FROM failed) > 7)
      SELECT s.h AS hour, 'Dealer · ' || se.series AS domain, count(l.series) AS emails
      FROM spine s
      CROSS JOIN series se
      LEFT JOIN labelled l ON l.h = s.h AND l.series = se.series
      GROUP BY 1, 2
      ORDER BY 1, 2
    template-tags:
      days:
        id: 89084468-6543-4ba9-b176-d2fa00e23b22
        name: days
        display-name: Days
        type: number
        default: '7'
      granularity:
        id: 24fd8557-3fb8-4054-8627-f34ffa1e7817
        name: granularity
        display-name: Granularity
        type: text
        default: '1 day'
visualization_settings:
  graph.dimensions:
  - hour
  - domain
  graph.metrics:
  - emails
  graph.x_axis.scale: timeseries
  graph.y_axis.title_text: Failed emails
  stackable.stack_type: stacked
  series_settings:
    Dealer · Other:
      color: '#C7CDD3'
parameters: []
```

- [ ] **Step 7: Wire both dashcards**

For `dc-emails-sent-by-hour-by-domain` append:

```yaml
  - parameter_id: 821ec3b1
    card: pca-emails-sent-by-hour-by-domain-dealer
    target:
    - variable
    - - template-tag
      - days
  - parameter_id: 3f9a20c7
    card: pca-emails-sent-by-hour-by-domain-dealer
    target:
    - variable
    - - template-tag
      - granularity
```

and `series: [pca-emails-sent-by-hour-by-domain-dealer]` (block style:
`series:` / `- pca-emails-sent-by-hour-by-domain-dealer`). For `dc-emails-failed-by-hour-by-domain` the same two entries with `card: pca-emails-failed-by-hour-by-domain-dealer` and `series:` / `- pca-emails-failed-by-hour-by-domain-dealer`.

(Pairs mode: both legacy dashcards to `size_x: 12`; twins at `col: 12, size_x: 12` on rows 1 and 7, `size_y: 6`.)

- [ ] **Step 8: Tests, validate, MCP** — PASS / clean; four SQLs run on their databases. Twin sent: domains only from rows with `from_address`; legacy domains now carry `Legacy · `.

- [ ] **Step 9: Checkpoint** — no commit.

---

### Task 9: Email delivery by domain — dealer table beside legacy

**Files:**
- Modify: `cards/pca-emails-delivery-by-domain.yaml`
- Create: `cards/pca-emails-delivery-by-domain-dealer.yaml`
- Modify: dashboard (`dc-emails-delivery-by-domain`, new `dc-emails-delivery-by-domain-dealer`)
- Test: `tests/test_dealer_dashboard.py`

**Interfaces:**
- Consumes: `_pair` (Task 6).
- Produces: card with the legacy column list: `domain, attempted, delivered, in_progress, failed, retried, late_bounced, late_bounce_open, delivered_pct, net_success_pct, median_delivery_sec`.

- [ ] **Step 1: Failing test**

Add `"pca-emails-delivery-by-domain"` to `LEGACY_HEADER_ONLY` and append:

```python
DELIVERY_COLUMNS = ["domain", "attempted", "delivered", "in_progress", "failed", "retried", "late_bounced",
                    "late_bounce_open", "delivered_pct", "net_success_pct", "median_delivery_sec"]


def test_delivery_twin_returns_legacy_columns_in_order():
    sql = _sql(_card("pca-emails-delivery-by-domain-dealer"))
    final_select = sql.rsplit("SELECT", 1)[1]
    positions = [final_select.find(f"AS {c}") if c != "domain" else final_select.find("c.domain") for c in DELIVERY_COLUMNS]
    assert -1 not in positions and positions == sorted(positions), positions


def test_delivery_pair_sits_side_by_side():
    legacy, twin = _pair("dc-emails-delivery-by-domain", "dc-emails-delivery-by-domain-dealer")
    assert (legacy["row"], legacy["col"], legacy["size_x"]) == (14, 0, 12)
    assert (twin["row"], twin["col"], twin["size_x"], twin["size_y"]) == (14, 12, 12, legacy["size_y"])
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Legacy card**

`name: Legacy · Email delivery by recipient domain`. Prepend to `native:`:

```sql
      -- Database 2 (legacy orchestrator). Per-recipient-domain delivery funnel of legacy order emails.
      -- No DEALER filter needed: DEALER pseudo-client orders are delivered over the API and send no email.
      -- Dealer emails are in the "Dealer · Email delivery by recipient domain" table beside this one.
```

- [ ] **Step 4: Create the twin**

`cards/pca-emails-delivery-by-domain-dealer.yaml`:

```yaml
kind: card
key: pca-emails-delivery-by-domain-dealer
name: Dealer · Email delivery by recipient domain
description: Per-recipient-domain delivery funnel of dealer email shipments over the Days window, one row per shipment (= order), anchored to sent_at, else created_at. Same columns as the legacy table; late_bounce_open is always empty because the dealer has no late-bounce resolution step. Read from the dealer database. Top 25 domains by attempted.
collection: pear-cider-automated
type: question
display: table
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 3
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Database 3 (dealer). Per-recipient-domain delivery funnel of dealer EMAIL shipments over the Days
      -- window, one row per shipment (= order), anchored to sent_at when known, else created_at (timestamptz).
      -- Legacy column -> dealer source:
      --   delivered    = status DELIVERED
      --   in_progress  = status PENDING, SENT or BOUNCED (BOUNCED = retryable bounce awaiting its next attempt)
      --   failed       = status FAILED
      --   retried      = more than one attempt (shipment.attempt > 1; courtesy resends also raise it)
      --   late_bounced = late_bounce_at set (a bounce after DELIVERED)
      --   late_bounce_open = NULL: the dealer has no resolution step for a late bounce
      --   median_delivery_sec = median seconds from an attempt's sent_at to its success verdict
      --                         (shipment_attempt.verdict_at where category IS NULL)
      WITH ship AS (
        SELECT s.id, lower(split_part(s.recipient, '@', 2)) AS domain, s.status, s.attempt, s.late_bounce_at
        FROM shipment s
        WHERE COALESCE(s.sent_at, s.created_at) >= (((now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day')) AT TIME ZONE 'Europe/Moscow')),
      counts AS (
        SELECT domain,
               count(*) AS attempted,
               count(*) FILTER (WHERE status = 'DELIVERED') AS delivered,
               count(*) FILTER (WHERE status IN ('PENDING', 'SENT', 'BOUNCED')) AS in_progress,
               count(*) FILTER (WHERE status = 'FAILED') AS failed,
               count(*) FILTER (WHERE attempt > 1) AS retried,
               count(*) FILTER (WHERE late_bounce_at IS NOT NULL) AS late_bounced,
               NULL::bigint AS late_bounce_open,
               round(100.0 * count(*) FILTER (WHERE status = 'DELIVERED') / nullif(count(*), 0), 1) AS delivered_pct,
               round(100.0 * count(*) FILTER (WHERE status = 'DELIVERED' AND late_bounce_at IS NULL)
                     / nullif(count(*), 0), 1) AS net_success_pct
        FROM ship
        GROUP BY domain),
      med AS (
        SELECT sh.domain,
               round(percentile_cont(0.5) WITHIN GROUP (
                       ORDER BY extract(epoch FROM a.verdict_at - a.sent_at))::numeric, 1) AS median_delivery_sec
        FROM ship sh
        JOIN shipment_attempt a ON a.shipment_id = sh.id
        WHERE a.verdict IS NOT NULL AND a.category IS NULL AND a.sent_at IS NOT NULL
        GROUP BY sh.domain)
      SELECT c.domain, c.attempted AS attempted, c.delivered AS delivered, c.in_progress AS in_progress,
             c.failed AS failed, c.retried AS retried, c.late_bounced AS late_bounced,
             c.late_bounce_open AS late_bounce_open, c.delivered_pct AS delivered_pct,
             c.net_success_pct AS net_success_pct, m.median_delivery_sec AS median_delivery_sec
      FROM counts c
      LEFT JOIN med m ON m.domain = c.domain
      ORDER BY c.attempted DESC, c.domain
      LIMIT 25
    template-tags:
      days:
        id: a3c5ac4f-1915-4e75-9a19-96b6d3b457fa
        name: days
        display-name: Days
        type: number
        default: '7'
visualization_settings: {}
parameters: []
```

- [ ] **Step 5: Dashboard**

`dc-emails-delivery-by-domain`: `size_x: 12`. Add after it:

```yaml
- key: dc-emails-delivery-by-domain-dealer
  card: pca-emails-delivery-by-domain-dealer
  tab: emails
  row: 14
  col: 12
  size_x: 12
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

- [ ] **Step 6: Tests, validate, MCP** — PASS / clean; twin on database 3 returns ≤ 25 rows, 11 columns in legacy order.

- [ ] **Step 7: Checkpoint** — no commit.

---

### Task 10: Late bounces — scalar pair, by-day series, lag series

**Files:**
- Modify: `cards/pca-emails-late-bounces-unresolved.yaml`, `cards/pca-emails-late-bounces-by-day.yaml`, `cards/pca-emails-late-bounce-lag.yaml`
- Create: `cards/pca-emails-late-bounces-unresolved-dealer.yaml`, `cards/pca-emails-late-bounces-by-day-dealer.yaml`, `cards/pca-emails-late-bounce-lag-dealer.yaml`
- Modify: dashboard (`dc-emails-late-bounces-unresolved`, new `dc-emails-late-bounces-unresolved-dealer`, `dc-emails-late-bounces-by-day`, `dc-emails-late-bounce-lag`)
- Test: `tests/test_dealer_dashboard.py`

**Interfaces:**
- Consumes: `_pair` (Task 6).
- Produces: `…-unresolved-dealer` (`late_bounces`), `…-by-day-dealer` (`day`, `state`, `late_bounces`), `…-lag-dealer` (`bucket`, `late_bounces`).

- [ ] **Step 1: Failing test**

Add the three legacy keys to `LEGACY_HEADER_ONLY` and append:

```python
def test_dealer_late_bounces_ignore_late_delivered_at():
    # late_delivered_at = delivered after FAILED (Shipment.stampLateDelivered), not a late-bounce resolution.
    for key in ("pca-emails-late-bounces-unresolved-dealer", "pca-emails-late-bounces-by-day-dealer",
                "pca-emails-late-bounce-lag-dealer"):
        assert "late_delivered_at IS" not in _sql(_card(key)), key


def test_lag_twin_uses_legacy_bucket_labels():
    legacy, twin = (_sql(_card(k)) for k in ("pca-emails-late-bounce-lag", "pca-emails-late-bounce-lag-dealer"))
    for label in ("01 <1 min", "02 1-5 min", "03 5-60 min", "04 1-6 h", "05 6-24 h", "06 >24 h", "07 no delivered_at"):
        assert f"'{label}'" in legacy and f"'{label}'" in twin, label


def test_late_bounce_unresolved_pair_and_series():
    legacy, twin = _pair("dc-emails-late-bounces-unresolved", "dc-emails-late-bounces-unresolved-dealer")
    assert (legacy["row"], legacy["col"], legacy["size_x"]) == (23, 0, 3)
    assert (twin["row"], twin["col"], twin["size_x"], twin["size_y"]) == (23, 3, 3, legacy["size_y"])
    by_key = {d["key"]: d for d in _dashcards()}
    assert by_key["dc-emails-late-bounces-by-day"]["series"] == ["pca-emails-late-bounces-by-day-dealer"]
    assert by_key["dc-emails-late-bounce-lag"]["series"] == ["pca-emails-late-bounce-lag-dealer"]
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Legacy cards**

`pca-emails-late-bounces-unresolved.yaml`: `name: Legacy · Unresolved late bounces`; prepend:

```sql
      -- Database 2 (legacy orchestrator). Open late bounces of legacy order emails. No DEALER filter
      -- needed: DEALER pseudo-client orders send no email. Dealer late bounces are on the card beside it.
```

`pca-emails-late-bounces-by-day.yaml`: prepend:

```sql
      -- Database 2 (legacy orchestrator). Late bounces of legacy order emails per bucket, by resolution.
      -- No DEALER filter needed: DEALER pseudo-client orders send no email. Dealer late bounces are the
      -- stacked "Dealer · Late bounce" series from the dealer DB.
```

and change the final `SELECT s.d AS day, st.state, …` to `SELECT s.d AS day, 'Legacy · ' || st.state AS state, count(l.state) AS late_bounces`; rename `series_settings` keys to `Legacy · Unresolved`, `Legacy · Resent`, `Legacy · Resolved manually` (same colors) and add `Dealer · Late bounce` with color `#F2A86F`.

`pca-emails-late-bounce-lag.yaml`: `name: Legacy · Late bounce lag`; prepend:

```sql
      -- Database 2 (legacy orchestrator). Late bounces of legacy order emails by lag after delivered_at.
      -- No DEALER filter needed: DEALER pseudo-client orders send no email. The dealer's lag is the
      -- "Dealer · Late bounce lag" series on the same buckets.
```

On the dashboard, give the lag dashcard a neutral title so it does not read "Legacy" while showing both: in `dc-emails-late-bounce-lag` set `visualization_settings: {card.title: 'Late bounce lag, legacy + dealer'}` (block style: `visualization_settings:` / `  card.title: Late bounce lag, legacy + dealer`).

- [ ] **Step 4: Create the three twins**

`cards/pca-emails-late-bounces-unresolved-dealer.yaml`:

```yaml
kind: card
key: pca-emails-late-bounces-unresolved-dealer
name: Dealer · Late bounces (no resolution step)
description: Dealer shipments flagged late_bounce_at (bounced after DELIVERED). The dealer has no late-bounce resolution step, so this counts every late-bounced shipment ever, not an open backlog. Read from the dealer database.
collection: pear-cider-automated
type: question
display: scalar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 3
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Database 3 (dealer). Shipments flagged late_bounce_at: a bounce after DELIVERED (Shipment.stampLateBounce).
      -- The dealer has no resolution step for a late bounce, so this is every late-bounced shipment ever,
      -- not an open backlog. late_delivered_at is a different flag (delivered after FAILED) and is not used.
      SELECT count(*) AS late_bounces
      FROM shipment
      WHERE late_bounce_at IS NOT NULL
visualization_settings: {}
parameters: []
```

`cards/pca-emails-late-bounces-by-day-dealer.yaml`:

```yaml
kind: card
key: pca-emails-late-bounces-by-day-dealer
name: Dealer · Late bounces over time
description: Dealer late bounces per bucket over the Days window (late_bounce_at bucketed). One series, Dealer · Late bounce, since the dealer has no resolution step. Read from the dealer database. Buckets follow the Granularity filter, Europe/Moscow.
collection: pear-cider-automated
type: question
display: bar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 3
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Database 3 (dealer). Late bounces per bucket: shipment.late_bounce_at (timestamptz), Europe/Moscow.
      -- One state only: the dealer has no resolution step (legacy splits Unresolved / Resent / Resolved manually).
      WITH spine AS (
        SELECT generate_series(
          date_bin({{granularity}}::text::interval, (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day'), TIMESTAMP '2000-01-03'),
          (now() AT TIME ZONE 'Europe/Moscow'),
          {{granularity}}::text::interval) AS d),
      lb AS (
        SELECT date_bin({{granularity}}::text::interval, (late_bounce_at AT TIME ZONE 'Europe/Moscow'), TIMESTAMP '2000-01-03') AS d
        FROM shipment
        WHERE late_bounce_at >= (date_bin({{granularity}}::text::interval, (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day'), TIMESTAMP '2000-01-03') AT TIME ZONE 'Europe/Moscow'))
      SELECT s.d AS day, 'Dealer · Late bounce' AS state, count(l.d) AS late_bounces
      FROM spine s
      LEFT JOIN lb l ON l.d = s.d
      GROUP BY 1, 2
      ORDER BY 1, 2
    template-tags:
      days:
        id: 1902ec1e-1e42-4ab3-9bbf-44de21518f2c
        name: days
        display-name: Days
        type: number
        default: '7'
      granularity:
        id: 535bc89a-d81c-4cb8-ae74-b061ffdcf037
        name: granularity
        display-name: Granularity
        type: text
        default: '1 day'
visualization_settings:
  graph.dimensions:
  - day
  - state
  graph.metrics:
  - late_bounces
  graph.x_axis.scale: timeseries
  graph.y_axis.title_text: Late bounces
  stackable.stack_type: stacked
  series_settings:
    Dealer · Late bounce:
      color: '#F2A86F'
parameters: []
```

`cards/pca-emails-late-bounce-lag-dealer.yaml`:

```yaml
kind: card
key: pca-emails-late-bounce-lag-dealer
name: Dealer · Late bounce lag
description: Dealer late bounces over the Days window bucketed by how long after the delivery verdict they landed. The delivery time is the success verdict of the shipment's attempts (shipment_attempt.verdict_at, category empty), the dealer's counterpart of legacy delivered_at. Same buckets as the legacy card. Read from the dealer database.
collection: pear-cider-automated
type: question
display: bar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 3
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Database 3 (dealer). Late bounces by lag after delivery. The dealer's shipment has no delivered_at:
      -- the delivery time is the latest success verdict (shipment_attempt.verdict_at, category IS NULL)
      -- at or before late_bounce_at. No such verdict = '07 no delivered_at', the legacy label, so the
      -- buckets line up with the legacy series.
      WITH buckets AS (
        SELECT * FROM (VALUES
          ('01 <1 min'), ('02 1-5 min'), ('03 5-60 min'), ('04 1-6 h'),
          ('05 6-24 h'), ('06 >24 h'), ('07 no delivered_at')) AS t(bucket)),
      lb AS (
        SELECT s.late_bounce_at,
               (SELECT max(a.verdict_at)
                FROM shipment_attempt a
                WHERE a.shipment_id = s.id
                  AND a.verdict IS NOT NULL AND a.category IS NULL
                  AND a.verdict_at <= s.late_bounce_at) AS delivered_at
        FROM shipment s
        WHERE s.late_bounce_at >= (((now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day')) AT TIME ZONE 'Europe/Moscow')),
      bucketed AS (
        SELECT CASE
                 WHEN delivered_at IS NULL                                  THEN '07 no delivered_at'
                 WHEN late_bounce_at - delivered_at < INTERVAL '1 minute'   THEN '01 <1 min'
                 WHEN late_bounce_at - delivered_at < INTERVAL '5 minutes'  THEN '02 1-5 min'
                 WHEN late_bounce_at - delivered_at < INTERVAL '1 hour'     THEN '03 5-60 min'
                 WHEN late_bounce_at - delivered_at < INTERVAL '6 hours'    THEN '04 1-6 h'
                 WHEN late_bounce_at - delivered_at < INTERVAL '24 hours'   THEN '05 6-24 h'
                 ELSE '06 >24 h' END AS bucket
        FROM lb)
      SELECT b.bucket, count(x.bucket) AS late_bounces
      FROM buckets b LEFT JOIN bucketed x ON x.bucket = b.bucket
      GROUP BY 1 ORDER BY 1
    template-tags:
      days:
        id: 1645ba32-886a-4dc0-99c5-96cbaf9b56e8
        name: days
        display-name: Days
        type: number
        default: '7'
visualization_settings:
  graph.dimensions:
  - bucket
  graph.metrics:
  - late_bounces
  graph.y_axis.title_text: Late bounces
parameters: []
```

- [ ] **Step 5: Dashboard**

`dc-emails-late-bounces-unresolved`: `size_x: 3`. Add after it:

```yaml
- key: dc-emails-late-bounces-unresolved-dealer
  card: pca-emails-late-bounces-unresolved-dealer
  tab: emails
  row: 23
  col: 3
  size_x: 3
  size_y: 4
  visualization_settings: {}
  parameter_mappings: []
  series: []
  inline_parameters: []
```

`dc-emails-late-bounces-by-day`: append mappings for `card: pca-emails-late-bounces-by-day-dealer` (Days `821ec3b1` → `days`, Granularity `3f9a20c7` → `granularity`, same shape as Task 5 Step 5) and `series:` / `- pca-emails-late-bounces-by-day-dealer`.

`dc-emails-late-bounce-lag`: append

```yaml
  - parameter_id: 821ec3b1
    card: pca-emails-late-bounce-lag-dealer
    target:
    - variable
    - - template-tag
      - days
```

and `series:` / `- pca-emails-late-bounce-lag-dealer`.

(Pairs mode for by-day and lag: legacy to half width, twin in the other half of the same row with its own un-scoped mappings.)

- [ ] **Step 6: Tests, validate, MCP** — PASS / clean; all six SQLs run. Lag twin today: 7 rows, all 0.

- [ ] **Step 7: Checkpoint** — no commit.

---

### Task 11: Migration progress section (Overview)

**Files:**
- Create: `cards/pca-dealer-clients-switched.yaml`, `cards/pca-orders-by-day-legacy.yaml`, `cards/pca-orders-by-day-dealer.yaml`
- Modify: dashboard (new `dc-heading-overview-migration`, `dc-dealer-clients-switched`, `dc-orders-by-day`)
- Test: `tests/test_dealer_dashboard.py`

**Interfaces:**
- Produces: `pca-dealer-clients-switched` (`clients_switched`); `pca-orders-by-day-legacy` / `-dealer` (`day`, `source`, `orders`).

- [ ] **Step 1: Failing test**

Add `"pca-orders-by-day-legacy"` to `DEALER_EXCLUDED`, `"pca-orders-by-day-dealer": "pca-orders-by-day-legacy"` to `LEGACY_OF`, and append:

```python
def test_migration_section_on_overview():
    by_key = {d["key"]: d for d in _dashcards()}
    heading = by_key["dc-heading-overview-migration"]
    assert (heading["tab"], heading["row"], heading["visualization_settings"]["text"]) == (
        "overview", 33, "Migration to dealer")
    assert by_key["dc-dealer-clients-switched"]["card"] == "pca-dealer-clients-switched"
    assert by_key["dc-orders-by-day"]["series"] == ["pca-orders-by-day-dealer"]
    assert _card("pca-dealer-clients-switched")["dataset_query"]["database"] == DEALER_DB
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Create the cards**

`cards/pca-dealer-clients-switched.yaml`:

```yaml
kind: card
key: pca-dealer-clients-switched
name: Dealer · Clients switched to the dealer
description: API clients moved from legacy to the dealer (api_client.v1_switched_at set). Read from the dealer database.
collection: pear-cider-automated
type: question
display: scalar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 3
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Database 3 (dealer). API clients switched from legacy to the dealer: api_client.v1_switched_at is set
      -- when a client's config is copied over. No order history moves with it.
      SELECT count(*) AS clients_switched
      FROM api_client
      WHERE v1_switched_at IS NOT NULL
visualization_settings: {}
parameters: []
```

`cards/pca-orders-by-day-legacy.yaml`:

```yaml
kind: card
key: pca-orders-by-day-legacy
name: Legacy · Client orders over time
description: Legacy client orders created per bucket, Europe/Moscow, excluding the DEALER pseudo-client. Stacked with the dealer's client orders to show the migration. Buckets follow the Granularity filter.
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
      -- Database 2 (legacy orchestrator). Client orders created per bucket, every status, Europe/Moscow
      -- (order_.created_at is naive UTC). Excludes the DEALER pseudo-client: those are the dealer's own
      -- purchases; the dealer's client orders are the stacked "Dealer · Orders" series. Bulk orders have no
      -- order_ row and are not counted.
      WITH spine AS (
        SELECT generate_series(
          date_bin({{granularity}}::text::interval, (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day'), TIMESTAMP '2000-01-03'),
          (now() AT TIME ZONE 'Europe/Moscow'),
          {{granularity}}::text::interval) AS h),
      o AS (
        SELECT date_bin({{granularity}}::text::interval, (o.created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow'), TIMESTAMP '2000-01-03') AS h
        FROM order_ o
        WHERE o.created_at >= (date_bin({{granularity}}::text::interval, (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day'), TIMESTAMP '2000-01-03') AT TIME ZONE 'Europe/Moscow' AT TIME ZONE 'UTC')
          AND o.client_id <> '00000000-0000-0000-0000-000000000002'  -- DEALER pseudo-client: dealer purchases, counted in the dealer DB
        )
      SELECT s.h AS day, 'Legacy · Orders' AS source, count(o.h) AS orders
      FROM spine s
      LEFT JOIN o ON o.h = s.h
      GROUP BY 1, 2
      ORDER BY 1, 2
    template-tags:
      days:
        id: 34890523-003f-4d4b-adf9-92f21b18f468
        name: days
        display-name: Days
        type: number
        default: '7'
      granularity:
        id: 854b3c4f-de74-4122-b160-319ef88d947e
        name: granularity
        display-name: Granularity
        type: text
        default: '1 day'
visualization_settings:
  graph.dimensions:
  - day
  - source
  graph.metrics:
  - orders
  graph.x_axis.scale: timeseries
  graph.y_axis.title_text: Client orders
  stackable.stack_type: stacked
  series_settings:
    Legacy · Orders:
      color: '#509EE3'
    Dealer · Orders:
      color: '#A989C5'
parameters: []
```

`cards/pca-orders-by-day-dealer.yaml`:

```yaml
kind: card
key: pca-orders-by-day-dealer
name: Dealer · Client orders over time
description: Dealer client orders created per bucket, every status, Europe/Moscow. Read from the dealer database; stacked on the legacy client orders chart. Buckets follow the Granularity filter.
collection: pear-cider-automated
type: question
display: bar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 3
  stages:
  - lib/type: mbql.stage/native
    native: |
      -- Database 3 (dealer). Client orders created per bucket, every status: dealer_order.created_at
      -- (timestamptz), Europe/Moscow.
      WITH spine AS (
        SELECT generate_series(
          date_bin({{granularity}}::text::interval, (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day'), TIMESTAMP '2000-01-03'),
          (now() AT TIME ZONE 'Europe/Moscow'),
          {{granularity}}::text::interval) AS h),
      o AS (
        SELECT date_bin({{granularity}}::text::interval, (created_at AT TIME ZONE 'Europe/Moscow'), TIMESTAMP '2000-01-03') AS h
        FROM dealer_order
        WHERE created_at >= (date_bin({{granularity}}::text::interval, (now() AT TIME ZONE 'Europe/Moscow') - ({{days}} * INTERVAL '1 day'), TIMESTAMP '2000-01-03') AT TIME ZONE 'Europe/Moscow'))
      SELECT s.h AS day, 'Dealer · Orders' AS source, count(o.h) AS orders
      FROM spine s
      LEFT JOIN o ON o.h = s.h
      GROUP BY 1, 2
      ORDER BY 1, 2
    template-tags:
      days:
        id: 59bd15e5-32ad-479e-a56f-f0da69d701b3
        name: days
        display-name: Days
        type: number
        default: '7'
      granularity:
        id: 7511e021-c1e8-4a5e-8a74-ed879818c54e
        name: granularity
        display-name: Granularity
        type: text
        default: '1 day'
visualization_settings:
  graph.dimensions:
  - day
  - source
  graph.metrics:
  - orders
  graph.x_axis.scale: timeseries
  graph.y_axis.title_text: Client orders
  stackable.stack_type: stacked
parameters: []
```

- [ ] **Step 4: Dashboard**

Insert after `dc-flow-by-hour` (Overview ends at row 33 today, so nothing shifts):

```yaml
- key: dc-heading-overview-migration
  tab: overview
  row: 33
  col: 0
  size_x: 24
  size_y: 1
  visualization_settings:
    virtual_card:
      display: heading
    text: Migration to dealer
    dashcard.background: false
- key: dc-dealer-clients-switched
  card: pca-dealer-clients-switched
  tab: overview
  row: 34
  col: 0
  size_x: 6
  size_y: 7
  visualization_settings: {}
  parameter_mappings: []
  series: []
  inline_parameters: []
- key: dc-orders-by-day
  card: pca-orders-by-day-legacy
  tab: overview
  row: 34
  col: 6
  size_x: 18
  size_y: 7
  visualization_settings:
    card.title: Client orders over time, legacy + dealer
  parameter_mappings:
  - parameter_id: 821ec3b1
    target:
    - variable
    - - template-tag
      - days
  - parameter_id: 3f9a20c7
    target:
    - variable
    - - template-tag
      - granularity
  - parameter_id: 821ec3b1
    card: pca-orders-by-day-dealer
    target:
    - variable
    - - template-tag
      - days
  - parameter_id: 3f9a20c7
    card: pca-orders-by-day-dealer
    target:
    - variable
    - - template-tag
      - granularity
  series:
  - pca-orders-by-day-dealer
  inline_parameters: []
```

(Pairs mode: `dc-orders-by-day` to `size_x: 9`; add `dc-orders-by-day-dealer` with `card: pca-orders-by-day-dealer` at `col: 15, size_x: 9`, un-scoped mappings, no `series`.)

- [ ] **Step 5: Tests, validate, MCP** — PASS / clean. `pca-dealer-clients-switched` on database 3 returns `0` today (the only dealer client, `test`, is not switched).

- [ ] **Step 6: Checkpoint** — no commit.

---

### Task 12: Verification, reconciliation and rollout (owner-gated)

**Files:**
- Modify (by `./mbc apply`, after the owner's go): `.state/bi-apple-certificate-solutions-8443.yaml`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Full local gate**

```bash
unset HTTP_PROXY HTTPS_PROXY NO_PROXY; make mbc-test && ./mbc validate
```

Expected: whole pytest suite PASS (existing `mbcode` tests plus `tests/test_dealer_dashboard.py`), validate clean.

- [ ] **Step 2: Every touched SQL runs on its database**

```bash
unset HTTP_PROXY HTTPS_PROXY NO_PROXY; git status --short cards/ | awk '{print $2}'
```

For each listed card: render (shared command) and run via MCP `execute_query` on the database the render prints. Record row count per card in the PR/commit notes. Any error is a stop.

- [ ] **Step 3: Reconciliation, same 7-day window**

Database 2:

```sql
-- Database 2 (legacy orchestrator). Certificate face value split: legacy clients vs DEALER mirror.
SELECT sum(c.amount)::bigint AS total,
       sum(c.amount) FILTER (WHERE o.client_id IS DISTINCT FROM '00000000-0000-0000-0000-000000000002')::bigint AS legacy_clients,
       sum(c.amount) FILTER (WHERE o.client_id = '00000000-0000-0000-0000-000000000002')::bigint AS dealer_mirror
FROM certificate c
LEFT JOIN order_ o ON o.order_id = c.reserved_order_id
WHERE c.status = 'SENT'
  AND c.sent_at >= (now() AT TIME ZONE 'UTC') - INTERVAL '7 days'
  AND c.sent_at <  (now() AT TIME ZONE 'UTC')
```

Database 3:

```sql
-- Database 3 (dealer). Dealer face value completed vs bought-but-not-issued, RUB.
SELECT (SELECT sum(face_amount) / 100.0 FROM dealer_order
        WHERE status = 'COMPLETED' AND face_currency = 'RUB'
          AND completed_at >= now() - INTERVAL '7 days') AS dealer_completed,
       (SELECT sum(v.face_amount) / 100.0 FROM voucher v
        JOIN supplier_purchase p ON p.id = v.purchase_id
        WHERE v.status <> 'ISSUED' AND v.face_currency = 'RUB'
          AND p.closed_at >= now() - INTERVAL '7 days') AS dealer_bought_not_issued
```

Checks: (a) `total = legacy_clients + dealer_mirror` exactly; (b) `legacy_clients` = Σ `face_value` of the legacy by-client card for Days 7 (the card's window starts at the bucket boundary, so compare with the same boundary or accept the edge bucket difference); (c) `dealer_mirror ≈ dealer_completed + dealer_bought_not_issued`, differences only from purchases straddling the window edge. Today all are tiny (2 test orders). Record the numbers; re-run once a real client switches.

- [ ] **Step 4: Catch dashcards added in the UI**

```bash
unset HTTP_PROXY HTTPS_PROXY NO_PROXY; SCRATCH=$(mktemp --directory) && cp --recursive mbc mbcode requirements.txt .env .state "$SCRATCH"/ && (cd "$SCRATCH" && ./mbc export) ; diff --recursive --brief dashboards "$SCRATCH"/dashboards; diff --recursive --brief cards "$SCRATCH"/cards
```

If `mbc` cannot run from a copy (absolute venv shebang), run `./mbc export --help` first and use its output-directory option instead. Expected: only the files this plan changed differ. A dashcard present on the instance but absent from the repo means someone added it in the UI: stop, tell the owner, adopt it with `./mbc export --overwrite` on their say-so, re-apply this plan's dashboard edits on top.

- [ ] **Step 5: Diff for the owner**

```bash
unset HTTP_PROXY HTTPS_PROXY NO_PROXY; ./mbc diff
```

Show the output to the owner. Expected: 13 new cards (10 twins, 3 migration cards), 32 changed cards, 1 changed dashboard. Wait for an explicit go.

- [ ] **Step 6: Apply (only after the owner's go)**

```bash
unset HTTP_PROXY HTTPS_PROXY NO_PROXY; ./mbc apply
```

Then `./mbc diff` again: expected no drift (exit 0). `.state/bi-apple-certificate-solutions-8443.yaml` now holds the new card and dashcard ids.

- [ ] **Step 7: Render check in the browser**

Open the dashboard. Overview, Orders, Emails tabs: each stacked dashcard shows a legend with both `Legacy · …` and `Dealer · …` entries; changing Days to 30 and Granularity to `6 hours` changes both series (the series-mapping test guards the YAML; this checks Metabase honours it). Pairs sit side by side. If a stacked chart does not render, apply the Task 1 pairs fallback for that dashcard and repeat Steps 5-6 with the owner.

- [ ] **Step 8: Commit (only with the owner's explicit permission)**

```bash
unset HTTP_PROXY HTTPS_PROXY NO_PROXY; git add cards/ dashboards/ .state/ tests/test_dealer_dashboard.py docs/superpowers/ && git commit --file=- <<'EOF'
feat(dashboard): show legacy and dealer data side by side on Pear Cider

Dealer sales are mirrored in legacy as DEALER pseudo-client orders, so
certificate and pool cards stay as they are with a header saying so.
Per-client and order-flow cards exclude DEALER and get dealer-DB twins
stacked as series or placed beside them; email cards get twins on
shipment / shipment_attempt; Overview gains a migration progress row.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

Push only if the owner asks.
