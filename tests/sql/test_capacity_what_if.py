"""Scenario tests for cards/pca-capacity-what-if-by-hour.yaml against a throwaway postgres.

Scenario rows are placed relative to spine hours: spine_h(k) is the start of the hourly Moscow
bin k hours before the current one. Larger k = earlier.
"""
import os
import uuid
from types import SimpleNamespace

import pytest

from tests.sql.conftest import card_sql

pytestmark = pytest.mark.sql

CARD = "pca-capacity-what-if-by-hour"
CEILING = "Ceiling (quota)"
COOLDOWNS = "+ cooldowns"
MONEY = "+ money (24h flow)"
FLEET = "+ fleet ready"
REAL = "Real output (previous 24h)"


def _ts(k):
    return "NULL" if k is None else f"spine_h({k})"


def _ts_utc(k):
    return "NULL" if k is None else f"spine_h_utc({k})"


def _lit(value):
    return "NULL" if value is None else f"'{value}'"


def run_card(db, days=2, granularity="1 hour"):
    """Card rows as (k, series, capacity): k = whole hours between the row's hour and the current
    spine hour. k is computed in the same statement as the card, so one now() serves both and a
    test that straddles an hour boundary cannot misalign scenario rows and results."""
    card = card_sql(CARD, days, granularity).rstrip().rstrip(";")
    rows = db.rows(
        "SELECT round(extract(epoch FROM (spine_h(0) AT TIME ZONE 'Europe/Moscow') - q.hour) / 3600)::int,"
        f" q.series, q.capacity FROM (\n{card}\n) q")
    return [(int(k), series, int(capacity)) for k, series, capacity in rows]


def value(rows, series, k):
    """capacity of `series` at spine hour spine_h(k); fails if the card has no such row."""
    found = [r[2] for r in rows if r[0] == k and r[1] == series]
    assert len(found) == 1, f"{series!r} at k={k}: {found}"
    return found[0]


def set_device(db, device, *rows):
    """Replace a device's journal. Each row: dict(frm, to, state, validation, held, agent,
    agent_disabled); frm / to / held / agent_disabled are spine hours-ago (None = open / null)."""
    db.exec(f"DELETE FROM device_state_tenure WHERE device_id = '{device}'")
    for i, r in enumerate(rows):
        db.exec(f"""
            INSERT INTO device_state_tenure
              (device_id, state, validation_status, held_at, agent_status, agent_disabled_at,
               source, valid_from, valid_to)
            VALUES ('{device}', '{r.get("state", "IDLE")}', '{r.get("validation", "VALIDATED")}',
                    {_ts(r.get("held"))}, {_lit(r.get("agent", "ONLINE"))}, {_ts(r.get("agent_disabled"))},
                    '{"SEED" if i == 0 else "SNAPSHOT"}', {_ts(r["frm"])}, {_ts(r.get("to"))})""")


def set_account(db, account, device, *rows):
    """Replace an account's state journal. Each row: dict(frm, to, status, archived)."""
    db.exec(f"DELETE FROM account_state_tenure WHERE account_id = '{account}'")
    for r in rows:
        db.exec(f"""
            INSERT INTO account_state_tenure
              (account_id, validation_status, archived_at, device_id, source, valid_from, valid_to)
            VALUES ('{account}', '{r.get("status", "GOOD")}', {_ts(r.get("archived"))}, '{device}',
                    'TEST', {_ts(r["frm"])}, {_ts(r.get("to"))})""")


def seed_fleet(db, *, numbers=1, accounts=1, provider="beeline", mode="DEFAULT",
               balance=10_000_000):
    """History start 48h back (SEED device row), one ready device, `numbers` numbers on
    `provider` with `accounts` GOOD accounts each, all valid since spine_h(48). `balance` is the
    DEFAULT-mode stock of every number at spine_h(48) (None = no balance row)."""
    device = uuid.uuid4()
    db.exec(f"INSERT INTO phone_provider (code, mode) VALUES ('{provider}', '{mode}') "
            "ON CONFLICT DO NOTHING")
    set_device(db, device, dict(frm=48))
    fleet = SimpleNamespace(device=device, numbers=[], accounts=[], provider=provider)
    for _ in range(numbers):
        number = uuid.uuid4()
        fleet.numbers.append(number)
        db.exec(f"INSERT INTO number_provider_tenure (number_id, provider_code, valid_from) "
                f"VALUES ('{number}', '{provider}', spine_h(48))")
        if balance is not None:
            db.exec(f"INSERT INTO balance_ledger (number_id, delta, balance_after, source, created_at) "
                    f"VALUES ('{number}', {balance}, {balance}, 'INIT', spine_h(48))")
        for _ in range(accounts):
            account = uuid.uuid4()
            fleet.accounts.append(account)
            set_account(db, account, device, dict(frm=48))
            db.exec(f"INSERT INTO account_number_tenure (account_id, number_id, valid_from) "
                    f"VALUES ('{account}', '{number}', spine_h(48))")
    return fleet


def add_tasks(db, count, amount, at_k, minutes_before=0):
    db.exec(f"""
        INSERT INTO task (status, amount, succeeded_at)
        SELECT 'SUCCESS', {amount}, spine_h_utc({at_k}) - INTERVAL '{minutes_before} minutes'
        FROM generate_series(1, {count})""")


def add_cooldown(db, account, reason, applied_k, until_k=None, cleared_k=None):
    db.exec(f"""
        INSERT INTO account_cooldown (account_id, reason, applied_at, until, cleared_at)
        VALUES ('{account}', '{reason}', {_ts_utc(applied_k)}, {_ts_utc(until_k)}, {_ts_utc(cleared_k)})""")


def add_balance(db, number, k, balance_after, delta=None, source="INIT"):
    delta = balance_after if delta is None else delta
    db.exec(f"INSERT INTO balance_ledger (number_id, delta, balance_after, source, created_at) "
            f"VALUES ('{number}', {delta}, {balance_after}, '{source}', {_ts(k)})")


def add_deposit(db, provider, k, kind, amount):
    db.exec(f"INSERT INTO phone_provider_deposit_ledger (provider_code, kind, amount, created_at) "
            f"VALUES ('{provider}', '{kind}', {amount}, {_ts(k)})")


# ---- Task 2: spine, base set, Ceiling, Real output ----

def test_ceiling_one_account(db):
    seed_fleet(db)
    rows = run_card(db)
    assert value(rows, CEILING, 1) == 6  # least(6, floor(35000 / 1000 fallback))


def test_ceiling_uses_avg_amount(db):
    seed_fleet(db)
    add_tasks(db, 5, 7000, at_k=3)
    assert value(run_card(db), CEILING, 1) == 5  # floor(35000 / 7000)


def test_number_cap_from_month_window(db):
    seed_fleet(db)
    db.exec("INSERT INTO quota_ledger (subject_type, subject_id, amount, ts, status) "
            f"SELECT 'NUMBER', number_id, 599000, spine_h_utc(3), 'COMMITTED' "
            "FROM account_number_tenure")
    assert value(run_card(db), CEILING, 1) == 1


def test_archived_account_drops_at_archive_hour(db):
    fleet = seed_fleet(db)
    set_account(db, fleet.accounts[0], fleet.device,
                dict(frm=48, to=10), dict(frm=10, archived=10))
    rows = run_card(db)
    assert value(rows, CEILING, 11) == 6
    assert value(rows, CEILING, 9) == 0


def test_operator_block_drops_number(db):
    fleet = seed_fleet(db, accounts=2)
    add_cooldown(db, fleet.accounts[0], "BLOCKED_BY_OPERATOR", applied_k=5)
    rows = run_card(db)
    assert value(rows, CEILING, 4) == 0
    assert value(rows, CEILING, 6) == 12


def test_valid_to_equal_to_hour_is_exclusive(db):
    fleet = seed_fleet(db)
    set_account(db, fleet.accounts[0], fleet.device,
                dict(frm=48, to=3), dict(frm=3, status="BAD"))
    rows = run_card(db)
    assert value(rows, CEILING, 4) == 6
    assert value(rows, CEILING, 3) == 0


def test_real_output_previous_24h(db):
    seed_fleet(db)
    add_tasks(db, 3, 1000, at_k=2, minutes_before=10)
    rows = run_card(db, days=3)
    assert value(rows, REAL, 0) == 3
    assert value(rows, REAL, 1) == 3
    assert value(rows, REAL, 26) == 0


def test_spine_starts_at_history_start(db):
    seed_fleet(db)
    hours = {r[0] for r in run_card(db, days=30)}
    assert max(hours) == 48 and min(hours) == 0
    daily = {r[0] for r in run_card(db, days=30, granularity="1 day")}
    assert 1 <= len(daily) <= 3
    assert max(daily) <= 48


# ---- Task 3: cooldowns ----

def test_cooldown_cleared_mid_range(db):
    fleet = seed_fleet(db, accounts=2)
    add_cooldown(db, fleet.accounts[0], "APPLE_VELOCITY", applied_k=8, cleared_k=4)
    rows = run_card(db)
    assert value(rows, COOLDOWNS, 6) == 6
    assert value(rows, COOLDOWNS, 2) == 12
    assert value(rows, CEILING, 6) == 12
    assert value(rows, CEILING, 2) == 12


def test_cooldown_until_elapsed(db):
    fleet = seed_fleet(db)
    add_cooldown(db, fleet.accounts[0], "APPLE_VELOCITY", applied_k=8, until_k=5)
    rows = run_card(db)
    assert value(rows, COOLDOWNS, 6) == 0
    assert value(rows, COOLDOWNS, 5) == 6


# ---- Task 4: money ----

def test_default_number_capped_by_balance_plus_topups(db):
    fleet = seed_fleet(db, balance=None)
    number = fleet.numbers[0]
    add_balance(db, number, 30, 1000)
    add_balance(db, number, 2, 3000, delta=2000, source="PROVIDER_TOPUP")
    rows = run_card(db)
    assert value(rows, MONEY, 3) == 1
    assert value(rows, MONEY, 1) == 5


def test_drying_sim_limited_by_stock(db):
    fleet = seed_fleet(db, balance=1000)
    rows = run_card(db)
    assert value(rows, MONEY, 1) == 1
    assert value(rows, CEILING, 1) == 6


def _deposit_fleet(db):
    fleet = seed_fleet(db, numbers=2, provider="mts", mode="DEPOSIT")
    add_deposit(db, "mts", 30, "DEPOSIT", 4000)
    add_deposit(db, "mts", 20, "TASK", 1000)  # the app stores TASK / WRITE_OFF amounts positive
    return fleet


def test_deposit_provider_pooled(db):
    _deposit_fleet(db)
    rows = run_card(db)
    assert value(rows, COOLDOWNS, 1) == 12
    assert value(rows, MONEY, 1) == 3


def test_deposit_flow_counts_last_24h_credits(db):
    _deposit_fleet(db)
    add_deposit(db, "mts", 2, "DEPOSIT", 2000)
    rows = run_card(db)
    assert value(rows, MONEY, 1) == 7
    assert value(rows, MONEY, 3) == 3


@pytest.mark.parametrize("kind", ["TASK", "WRITE_OFF"])
def test_overdrawn_deposit_pool_floors_at_zero(db, kind):
    _deposit_fleet(db)
    add_deposit(db, "mts", 10, kind, 6000)  # stock 3000 - 6000 < 0
    rows = run_card(db)
    assert value(rows, MONEY, 1) == 0
    assert all(r[2] >= 0 for r in rows)


def test_days_capped_at_14(db):
    fleet = seed_fleet(db)
    set_device(db, fleet.device, dict(frm=480))  # history starts 20 days back
    assert max(r[0] for r in run_card(db, days=30)) == 14 * 24


def test_negative_stock_floors_at_zero(db):
    fleet = seed_fleet(db, balance=None)
    add_balance(db, fleet.numbers[0], 30, -5000)
    rows = run_card(db)
    assert value(rows, MONEY, 1) == 0
    assert all(r[2] >= 0 for r in rows)


def test_provider_from_tenure_at_h(db):
    fleet = seed_fleet(db, balance=None)
    number = fleet.numbers[0]
    db.exec("INSERT INTO phone_provider (code, mode) VALUES ('mts', 'DEPOSIT')")
    db.exec(f"UPDATE number_provider_tenure SET valid_to = spine_h(10) WHERE number_id = '{number}'")
    db.exec(f"INSERT INTO number_provider_tenure (number_id, provider_code, valid_from) "
            f"VALUES ('{number}', 'mts', spine_h(10))")
    # Ledger rows at 40h back: a deposit 30h back would still be inside the 24h flow window at k=9.
    add_balance(db, number, 40, 2000)
    add_deposit(db, "mts", 40, "DEPOSIT", 4000)
    rows = run_card(db)
    assert value(rows, MONEY, 11) == 2
    assert value(rows, MONEY, 9) == 4


# ---- Task 5: fleet ready ----

def test_device_offline_mid_range(db):
    fleet = seed_fleet(db)
    set_device(db, fleet.device, dict(frm=48, to=6), dict(frm=6, to=3, state="OFFLINE"), dict(frm=3))
    rows = run_card(db)
    assert value(rows, FLEET, 4) == 0
    for k in (7, 2):
        assert value(rows, FLEET, k) == value(rows, MONEY, k) == 6


@pytest.mark.parametrize("override", [
    dict(validation="PENDING"),
    dict(held=10),
    dict(agent="OFFLINE"),
    dict(agent_disabled=10),
    dict(state="DISABLED"),
])
def test_device_not_ready(db, override):
    fleet = seed_fleet(db)
    set_device(db, fleet.device, dict(frm=48, **override))
    rows = run_card(db)
    assert value(rows, FLEET, 1) == 0
    assert value(rows, MONEY, 1) == 6


def test_device_without_row_not_ready(db):
    fleet = seed_fleet(db)
    db.exec(f"DELETE FROM device_state_tenure WHERE device_id = '{fleet.device}'")
    rows = run_card(db)
    assert value(rows, CEILING, 1) == 6
    assert value(rows, MONEY, 1) == 6
    assert value(rows, FLEET, 1) == 0


# ---- Task 6: ordering invariant on volume data ----

def load_volume(db):
    with open(os.path.join(os.path.dirname(__file__), "volume.sql"), encoding="utf-8") as fh:
        db.exec(fh.read())


def test_lines_are_ordered(db):
    load_volume(db)
    by_hour = {}
    for k, series, capacity in run_card(db, days=3):
        by_hour.setdefault(k, {})[series] = capacity
    assert by_hour
    for k, lines in by_hour.items():
        assert lines[CEILING] >= lines[COOLDOWNS] >= lines[MONEY] >= lines[FLEET], (k, lines)
    assert any(lines[FLEET] > 0 for lines in by_hour.values())
