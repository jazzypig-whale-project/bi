# YAML format reference

This document is the authoritative reference for the YAML files under
`collections/`, `cards/` and `dashboards/`. See the top-level `README.md`
for how these files are used (`export`, `validate`, `diff`, `apply`).

## Keys

Every entity has a `key`: a slug matching `^[a-z0-9][a-z0-9-]*$`. A file's
`key` field must equal its own filename stem — `cards/daily-revenue.yaml`
must have `key: daily-revenue`.

`root` is a reserved collection key. It refers to Metabase's root collection
("Our analytics") and has no file of its own; use `root` wherever a
collection reference is needed for content that lives at the top level.

## `collections/<key>.yaml`

A collection groups cards and dashboards, and can be nested inside another
collection.

```yaml
kind: collection
key: sales
name: Sales
description: Sales reporting and pipeline dashboards.
parent: root
archived: false
```

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `kind` | string | yes | Always `collection`. |
| `key` | string | yes | Slug, must match the filename stem. |
| `name` | string | yes | Display name shown in the Metabase UI. |
| `description` | string or null | no | Collection description. |
| `parent` | string or null | yes | Key of the parent collection, or `root` for a top-level collection. |
| `archived` | boolean | yes | If `true`, the collection is archived on apply. |

## `cards/<key>.yaml`

A card is a question, model, or metric.

```yaml
kind: card
key: daily-revenue
name: Daily revenue
description: Sum of certificate amounts, grouped by day.
collection: sales
type: question
display: bar
cache_ttl: null
archived: false
dataset_query:
  lib/type: mbql/query
  database: 2
  stages:
    - lib/type: mbql.stage/mbql
      source-table: 23
      aggregation:
        - - sum
          - {}
          - - field
            - base-type: type/BigInteger
            - 105
      breakout:
        - - field
          - base-type: type/DateTime
            temporal-unit: day
          - 111
visualization_settings:
  graph.show_values: true
  graph.x_axis.scale: timeseries
  graph.dimensions:
    - sent_at
  graph.metrics:
    - sum
  graph.show_trendline: true
parameters: []
```

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `kind` | string | yes | Always `card`. |
| `key` | string | yes | Slug, must match the filename stem. |
| `name` | string | yes | Display name. |
| `description` | string or null | no | Card description. |
| `collection` | string | yes | Collection key, or `root`. |
| `type` | string | yes | One of `question`, `model`, `metric`. |
| `display` | string | yes | Visualization type, e.g. `table`, `bar`, `line`, `scalar`. |
| `cache_ttl` | integer or null | no | Cache TTL in seconds; `null` for no explicit override. |
| `archived` | boolean | yes | If `true`, the card is archived on apply. |
| `dataset_query` | object | yes | MBQL 5 query, verbatim (see below). |
| `visualization_settings` | object | no | Passed through to Metabase as-is. |
| `parameters` | list | no | Card-level parameters (question filters). |
| `parameter_mappings` | list | no | Card-level parameter mappings, passed through verbatim; distinct from a dashcard's `parameter_mappings`. Every card in the current instance has this empty, so the field is normally absent from the file. |

### `dataset_query` (MBQL 5)

`dataset_query` is written in MBQL 5 and is otherwise passed through
verbatim:

```json
{
  "lib/type": "mbql/query",
  "database": 2,
  "stages": [
    {
      "lib/type": "mbql.stage/mbql",
      "source-table": 23
    }
  ]
}
```

`database` is required. Every stage carries `"lib/type":
"mbql.stage/mbql"`. The server generates `lib/uuid` keys throughout this
structure on every write; they are stripped when exporting and ignored when
comparing files to the instance — do not hand-maintain them.

### Timezone

Every chart buckets and compares time in Europe/Moscow. The app's database is
Postgres; most columns are naive `timestamp` storing UTC, a few are
`timestamptz`. Metabase's `report-timezone` setting (`MB_REPORT_TIMEZONE`,
`Europe/Moscow` here) only affects `timestamptz` values — it does nothing for
a naive column, so every naive-column chart must convert explicitly.
`mbcode/lint_timezone.py` enforces this in `mbc validate` (and therefore
`diff`/`apply`); the four idioms below are what it accepts.

**Native SQL cards** — convert with `AT TIME ZONE`:

| situation | idiom |
|---|---|
| "now" vs a naive-UTC column | `(now() AT TIME ZONE 'UTC')` |
| "now" for a spine / display value | `(now() AT TIME ZONE 'Europe/Moscow')` |
| naive-UTC column → Moscow wall clock | `(col AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow')` |
| `timestamptz` column → Moscow wall clock | `(col AT TIME ZONE 'Europe/Moscow')` |

The lint forbids `CURRENT_DATE`/`CURRENT_TIMESTAMP`/`LOCALTIMESTAMP`/`::date`,
any `AT TIME ZONE` literal other than `'UTC'`/`'Europe/Moscow'`, a bare
`now()` with no conversion, and a `date_trunc(...)` argument with no `AT TIME
ZONE` in it. A column that is genuinely `timestamptz` already (so a bare
`now()` compares correctly) is exempt with an inline comment naming the
reason:

```sql
-- tz-ok: created_at is timestamptz; the bare now() below compares timestamptz to timestamptz.
```

**MBQL (GUI) cards** — a naive column cannot be bucketed correctly by
breakout/`time-interval` directly (they inherit `report-timezone`, which does
not apply). Add a `convert-timezone` expression to the stage and
breakout/filter on that instead of the raw field:

```yaml
expressions:
- - convert-timezone
  - lib/expression-name: sent_at_msk
  - - field
    - effective-type: type/DateTime
      base-type: type/DateTime
    - 111
  - Europe/Moscow
  - UTC
breakout:
- - expression
  - effective-type: type/DateTime
    base-type: type/DateTime
    temporal-unit: day
  - sent_at_msk
```

`convert-timezone`'s `source` argument is mandatory for a naive column. The
lint forbids a `temporal-unit` breakout or `time-interval` filter that
targets a raw `field` instead of an `expression`, and checks that the
targeted expression is a `convert-timezone` to `Europe/Moscow` defined in the
same stage. There is no per-card exemption for MBQL cards — a card built
directly in the Metabase UI with no such expression fails validation once
adopted into the repo; exempt one by key in `MBQL_TZ_OK` in
`mbcode/lint_timezone.py` only with a recorded reason.

## `dashboards/<key>.yaml`

```yaml
kind: dashboard
key: sales-overview
name: Sales overview
description: Weekly sales KPIs.
collection: sales
width: fixed
auto_apply_filters: true
cache_ttl: null
archived: false
parameters: []
tabs:
  - key: overview
    name: Overview
  - key: detail
    name: Detail
dashcards:
  - key: revenue-heading
    tab: overview
    row: 0
    col: 0
    size_x: 24
    size_y: 2
    visualization_settings:
      virtual_card:
        display: heading
      text: Revenue
      dashcard.background: false
  - key: revenue-chart
    card: daily-revenue
    tab: overview
    row: 2
    col: 0
    size_x: 12
    size_y: 6
    visualization_settings: {}
    parameter_mappings: []
    series: []
    inline_parameters: []
```

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `kind` | string | yes | Always `dashboard`. |
| `key` | string | yes | Slug, must match the filename stem. |
| `name` | string | yes | Display name. |
| `description` | string or null | no | Dashboard description. |
| `collection` | string | yes | Collection key, or `root`. |
| `width` | string | yes | `fixed` or `full`. |
| `auto_apply_filters` | boolean | yes | Whether filters apply automatically without an "Apply" click. |
| `cache_ttl` | integer or null | no | Cache TTL in seconds. |
| `archived` | boolean | yes | If `true`, the dashboard is archived on apply. |
| `parameters` | list | no | Dashboard-level filter/parameter definitions. |
| `tabs` | list | no | List of `{key, name}`. Omit only for an untabbed dashboard (no tabs at all). |
| `dashcards` | list | yes | List of dashcards, see below. |

### Dashcard fields

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `key` | string | yes | Logical key for this dashcard; resolved to a server-assigned dashcard id via the state file. |
| `card` | string | only for card dashcards | Card key this dashcard displays. Omit for virtual dashcards. |
| `tab` | string | required if the dashboard declares any tabs | Tab key this dashcard belongs to. |
| `row` | integer | yes | Zero-based grid row. |
| `col` | integer | yes | Zero-based grid column. |
| `size_x` | integer | yes | Width in grid columns. |
| `size_y` | integer | yes | Height in grid rows. |
| `visualization_settings` | object | no | Per-dashcard visualization overrides; also where virtual cards are defined. |
| `parameter_mappings` | list | no | Maps dashboard parameters to card query fields. |
| `series` | list | no | List of card keys for a combo/multi-series chart. |
| `inline_parameters` | list | no | Parameters shown inline on the dashcard itself. |

`apply` writes a dashboard's `dashcards` list by replacing the instance's
entire `dashcards` array (`PUT /api/dashboard/:id`), not by diffing
individual dashcards. A dashcard added to a managed dashboard through the
Metabase UI is therefore not merely "unmanaged" — the next `apply` deletes
it, because it is absent from the file. Run `./mbc export --overwrite` to
adopt such a dashcard into the file before applying again.

## Reference sugar

Files reference other entities by logical key rather than by numeric id.
These are resolved through the state file:

| In the file | Resolves to |
| --- | --- |
| `collection: <key>` | `collection_id` |
| `card: <key>` | `card_id` |
| `tab: <key>` | `dashboard_tab_id` |
| `series: [<key>, ...]` | `[{"id": N}, ...]` |
| dashcard `key: <key>` | the dashcard's own server id, via the state file |

## The dashboard grid

The dashboard grid is 24 columns wide. For every dashcard, `col + size_x`
must be `<= 24`. `row` and `col` are zero-based.

## Virtual dashcards

A virtual dashcard (text, heading, link, iframe, action, placeholder) has no
`card:` key. Instead, it carries a `virtual_card` block inside
`visualization_settings`.

Text card:

```yaml
- key: intro-text
  tab: overview
  row: 0
  col: 0
  size_x: 24
  size_y: 2
  visualization_settings:
    virtual_card:
      display: text
    text: This dashboard tracks weekly sales performance.
```

Heading card (heading cards also carry `dashcard.background: false`):

```yaml
- key: section-heading
  tab: overview
  row: 2
  col: 0
  size_x: 24
  size_y: 1
  visualization_settings:
    virtual_card:
      display: heading
    text: Revenue
    dashcard.background: false
```

Valid values for `virtual_card.display` are `text`, `heading`, `link`,
`iframe`, `action`, and `placeholder`.

## Tabs

`validate` requires **every** dashcard to declare `tab:` whenever the
dashboard declares any tabs at all, starting from a single tab. Only a
dashboard with no `tabs` key (or an empty one) may omit `tab:` on its
dashcards.

Metabase itself only enforces this once a dashboard has two or more tabs,
rejecting the update otherwise with:

```
400 "This dashboard has tab, makes sure every card has a tab"
```

The tool is deliberately stricter than the server here: sending
`dashboard_tab_id: null` for a dashcard while the dashboard has a tab is not
reliably accepted, so `validate` requires `tab:` from one tab upward rather
than waiting for the second.

## Fields the server owns

The following fields must not appear in these YAML files, because Metabase
assigns or computes them: `id`, `entity_id`, `created_at`, `updated_at`,
`creator_id`, `result_metadata`, `collection_position`, `slug`, `location`,
`table_id`, `database_id`, `dashboard_count`.

`validate` rejects any file containing one of these fields.

## The state file

`.state/<host>.yaml` maps every logical key used in the files above to the
identifiers Metabase assigned it: `{id, entity_id}` for collections, cards
and dashboards, plus, per dashboard, a `tabs` key-to-id map and a
`dashcards` key-to-id map.

The state file is meant to be committed to the repository (see
`.gitignore`), because it is what lets the tool recognize "this YAML file
already exists on the instance" instead of creating a duplicate. If it is
lost or deleted, the next `apply` has no way to match files to existing
entities and will create everything anew, producing duplicates alongside
the originals. Recover by running `./mbc export` against the instance to
regenerate it from what actually exists.

Note: this only protects you once the directory is under version control.
See "Before first use" in the top-level `README.md` — as things stand, the
state file has no copy other than the one on disk.
