import pytest

from tests.sql.conftest import card_sql

pytestmark = pytest.mark.sql


def test_schema_loads(db):
    assert db.rows("SELECT count(*) FROM device_state_tenure") == [("0",)]


def test_spine_h_is_whole_moscow_hour(db):
    assert db.rows("SELECT date_trunc('hour', spine_h(0), 'Europe/Moscow') = spine_h(0)") == [("t",)]
    assert db.rows("SELECT spine_h(0) - spine_h(1)") == [("01:00:00",)]


def test_card_sql_substitutes_tags():
    sql = card_sql("pca-capacity-by-hour", 3, "1 hour")
    assert "{{" not in sql
    assert "'1 hour'::text::interval" in sql
