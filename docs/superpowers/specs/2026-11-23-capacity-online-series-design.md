# Quota headroom: "+ online" series — design

Date: 2026-11-23. Size: small change. Status: approved by owner.

## Intent

With 4,242 production requests PENDING, the "Quota headroom over time (24h rotation ceiling)" chart
(`cards/pca-capacity-by-hour.yaml`) still read 1,295 certificates on its lowest series, while live
placement capacity was 0. Diagnosis (live DB, 2026-11-23 19:00 MSK):

- ~all of the balance-backed headroom sits on accounts of OFFLINE devices (233 of 487 devices);
- on online devices the non-cooled, balance-capped headroom is ~1 certificate;
- the chart has no device/agent gate by design, so it cannot show this.

The owner wants the existing chart to show the "real" low value next to the upper bounds.

## Goal

Add a fourth series, "Quota + cooldown + balance + online (upper bound)", to the existing card: the
same computation as "Quota + cooldown + balance", restricted to accounts whose device is online.

## Non-goals

- Active-account-only counting (would hide rotation headroom).
- mts `DEPOSIT` provider-mode money (mts `number.balance` is 0, so the balance line is already right).
- Queue-demand overlay; a second card; historised snapshots.

## Constraints

- Read-only SQL against `orchestrator` (role `metabase`); no app-DB writes.
- Existing three series must stay byte-for-byte equal in value.
- Device and agent state are current-only: today's online set is applied to every past hour (stated
  in the card description, like validation status already is).

## Approaches

1. **Fourth series on the existing card** — picked by the owner: one chart, new line directly under
   the others, no duplicated SQL.
2. New card copying the SQL with an online filter — rejected: ~250 duplicated SQL lines, two charts.
3. "Online only" parameter on the existing card — rejected: real capacity hidden behind a toggle.

## Design

- `subject` CTE: add `is_online` = device state NOT IN ('OFFLINE','DISABLED') AND agent
  `status = 'ONLINE'` AND `disabled_at IS NULL`.
- `account_headroom` carries `is_online`; `account_headroom_agg` adds
  `sum(tasks_possible) FILTER (WHERE is_online)`.
- `capped` applies the same number day/month caps to that sum → `quota_capacity_online`.
- New `UNION ALL` branch: `sum(least(quota_capacity_online, balance_capacity))`.
- Series setting: title "Quota + cooldown + balance + online", color `#EF8C8C`.
- Description: add the current-only online caveat; extend the ordering sentence to the fourth series
  (online subset ≤ Quota + cooldown + balance).

## Verification

Run the edited SQL live: the fourth series reads ~1 at 2026-11-23 19:00 MSK; the other three are
unchanged (1,295 / 4,665 / 6,443). Then `./mbc validate` and `./mbc diff`. No apply/commit without
the owner's OK.

## Left to the builder

Exact CTE wiring and wording of the description sentence.
