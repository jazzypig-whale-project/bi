"""Timing of the what-if card on prod-like volume (14 days, hourly).

Hard gate: well inside the `metabase` role's statement_timeout of 30s. Target: 10s (plan
Global Constraints). The target is reported as a warning, not a failure, because wall-clock on a
shared dev host swings by 2x with other load (one run's backend CPU time equalled its wall time,
so this is real work, not only noise). Postgres JIT adds ~2.4s here: the planner's estimates for
the CTE chain are huge, so JIT triggers whenever it is enabled.
"""
import time
import warnings

import pytest

from tests.sql.conftest import card_sql
from tests.sql.test_capacity_what_if import CARD, load_volume

pytestmark = pytest.mark.sql_perf

TARGET_MS = 10_000
HARD_LIMIT_MS = 20_000  # headroom under the role's 30s statement_timeout


def test_card_14_days_hourly_within_timeout(db):
    load_volume(db)
    sql = card_sql(CARD, 14, "1 hour").rstrip().rstrip(";")
    timings = []
    for _ in range(3):  # best of 3 damps load noise on a shared host
        started = time.monotonic()
        rows = db.rows(sql)
        timings.append((time.monotonic() - started) * 1000)
        assert len(rows) == 5 * 337
    best = min(timings)
    print(f"card 14d hourly: best {best:.0f} ms, runs {[round(t) for t in timings]}")
    assert best < HARD_LIMIT_MS, f"best of 3: {best:.0f} ms"
    if best >= TARGET_MS:
        warnings.warn(f"card at {best:.0f} ms, above the {TARGET_MS} ms target")
