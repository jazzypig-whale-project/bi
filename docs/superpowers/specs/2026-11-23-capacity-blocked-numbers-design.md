# Capacity cards: exclude blocked numbers and archived accounts — design

Size: small change (three card YAMLs on the Pear Cider dashboard).

## Intent

"Quota headroom over time (24h rotation ceiling)" and the two "Capacity now" scalars should count only
capacity that could actually produce. A **blocked number** is a SIM the operator has blocked: it can no
longer be used as a payment method, so **no account bound to it can produce** — not only the account
that hit the failure.

## Current state

- A block is recorded per account: `account_cooldown` row with `reason` = failure category,
  `until IS NULL` (MANUAL cooldown, `app.failure-policy` in `orchestrator/.../application.yml`),
  `applied_at`, `cleared_at`. It is lifted (cleared) when the account's payment phone changes
  (`OperatorBlockLiftService`), so a block always belongs to the number held when it was applied.
- Account→number history: `account_number_tenure` (`valid_from` / `valid_to`, from migration V130).
- `pca-capacity-by-hour`: only the "Quota" series ignores cooldowns; the two other series already drop
  the cooled-down account itself, hour by hour. No line spreads a block to sibling accounts on the
  same number. Archived accounts (`account.archived_at`) are counted, though placement excludes them
  (`AccountRepository.sqlNotArchived`).
- `pca-capacity-now-quota` / `pca-capacity-now-quota-balance`: drop any open cooldown of the active
  account; no archived filter; no number-level block.

## Goals

1. A number is **blocked** while any account held it when an open-ended cooldown with reason
   `BLOCKED_BY_OPERATOR` or `NO_PAYMENT_METHOD_AVAILABLE` was applied, from `applied_at` until
   `cleared_at` (open = still blocked). A blocked number contributes 0 for **all** its accounts.
2. Archived accounts contribute 0 from `archived_at` onward.
3. Both rules apply to **all three series** of the by-hour chart (including "Quota") and to both
   "Capacity now" scalars, so series ordering and the scalars stay consistent.
4. Card descriptions updated to state the two new exclusions.

## Non-goals

- `ACCOUNT_LOCKED`, `CONFIRMATION_REQUIRED`, wrong-password etc. stay **account-only** (Apple-account
  problems, not phone problems) — handled as today by the cooldown series.
- No change to the temporary cooldown handling (`INSUFFICIENT_FUNDS`, `PAYMENT_REJECTED_APPLE`, …).
- No new number-state gates (cabinet OFFLINE, retiring, deposit-funded, `is_active`): task placement
  does not apply them either.
- No changes to the orchestrator app or its DB (read-only role only).

## Approaches

- **A. Number at block time via `account_number_tenure` — chosen by the owner.** Join each blocking
  cooldown to the tenure row covering `applied_at`. Correct history even after a phone swap. Cost: one
  extra join; blocks older than the tenure journal (pre-V130) find no row → fall back to blocking the
  account only.
- B. Number via today's `account.current_number_id` — rejected: after a phone change (which is exactly
  what lifts these blocks) the old block would be pinned onto the new, working SIM.
- C. Account-only (no spread) — rejected: owner confirmed sibling accounts on a blocked number do not
  produce.

## Decisions (mine, correct if wrong)

- The number stays blocked even if the account that triggered the block is later archived — the SIM
  is still blocked.
- In the by-hour card, archived accounts are filtered **per hour** (`archived_at > h`), not by today's
  state, since the timestamp exists. The `subject` set therefore includes archived accounts and the
  hour-level check drops them.
- In the "Capacity now" cards, "open block" = `cleared_at IS NULL`; number resolved through the same
  tenure lookup as by-hour, for one rule in both places.
- Timezone handling: `account_cooldown` / `account.archived_at` are `timestamp` (UTC),
  `account_number_tenure` is `timestamptz`; the spine is Moscow-local. Builder converts consistently
  with the existing `cooldown` CTE.

## Verification

- `./mbc validate`, `./mbc diff` (expected: only the three cards change).
- Run the new SQL read-only against prod via Metabase: by-hour values ≤ the old values per series and
  hour; series ordering Quota ≥ Quota+cooldown ≥ Quota+cooldown+balance holds; "Capacity now" ≤ old.
- Spot-check one known blocked number: all its accounts contribute 0 while blocked.
- Query duration stays within the role's `statement_timeout=30s`.
- `./mbc apply` only with owner's go-ahead; commit only with owner's explicit permission.
