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
DEALER_EXCLUDED: set[str] = {
    "pca-orders-by-hour-sber",
    "pca-orders-by-hour-other",
    "pca-orders-aging-sber",
    "pca-orders-aging-other",
    "pca-orders-in-progress-by-status-partner",
    "pca-completion-time-percentiles",
    "pca-sales-distribution-by-partner",
    "pca-certificate-face-value-by-day-by-client",
    "pca-orders-in-progress",
    "pca-time-since-last-order-by-partner",
    "pca-orders-by-day-legacy",
    "pca-orders-by-status-legacy",
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
# Legacy cards that only gain a header comment (spec section 3, legacy email cards).
LEGACY_HEADER_ONLY: set[str] = {
    "pca-emails-sent-by-hour-by-domain",
    "pca-emails-failed-by-hour-by-domain",
    "pca-emails-delivery-by-domain",
    "pca-emails-late-bounces-unresolved",
    "pca-emails-late-bounces-by-day",
    "pca-emails-late-bounce-lag",
}
# Twin key -> legacy counterpart, where it is not the twin key minus "-dealer".
LEGACY_OF: dict[str, str] = {
    "pca-orders-by-day-dealer": "pca-orders-by-day-legacy",
    "pca-orders-by-status-dealer": "pca-orders-by-status-legacy",
}


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


def test_by_client_exclusion_keeps_null_order():
    # Certificates of bulk orders have no reserved order: o.client_id is NULL there.
    # `<>` would drop them; IS DISTINCT FROM keeps them.
    sql = _sql(_card("pca-certificate-face-value-by-day-by-client"))
    assert f"o.client_id IS DISTINCT FROM '{DEALER_CLIENT}'" in sql
    assert f"o.client_id <> '{DEALER_CLIENT}'" not in sql


def test_by_client_twin_never_mixes_currencies():
    sql = _sql(_card("pca-certificate-face-value-by-day-by-client-dealer"))
    # The series label carries the currency for anything not RUB, and the label is a GROUP BY key.
    assert "CASE WHEN o.face_currency = 'RUB' THEN ''" in sql
    assert "GROUP BY 1, 2" in sql


def _legacy_of(twin_key):
    return LEGACY_OF.get(twin_key, twin_key.removesuffix("-dealer"))


def _dashboard_cards():
    return {key for dc in _dashcards() for key in [dc.get("card"), *(dc.get("series") or [])] if key}


# The Flow card stays: its Requested/Produced/Sent streams have no dealer counterpart.
KEPT_LEGACY_TWINS = {"pca-flow-by-hour-dealer"}


@pytest.mark.parametrize("key", sorted(set(_dealer_twins()) - KEPT_LEGACY_TWINS))
def test_legacy_counterpart_is_archived_and_off_the_dashboard(key):
    # Every partner now goes through the dealer: a legacy card with a dealer twin is retired.
    legacy = _legacy_of(key)
    assert _card(legacy)["archived"] is True, legacy
    assert legacy not in _dashboard_cards(), legacy
    assert key in _dashboard_cards(), key


def test_dashboard_shows_no_archived_card():
    archived = {key for key in _dashboard_cards() if _card(key)["archived"]}
    assert archived == set()


def test_flow_has_no_legacy_ordered_stream():
    sql = _sql(_card("pca-flow-by-hour"))
    assert "Legacy · Ordered" not in sql
    assert "FROM order_" not in sql
    assert DEALER_CLIENT not in sql


def test_flow_dashcard_stacks_the_twin():
    dc = next(d for d in _dashcards() if d["key"] == "dc-flow-by-hour")
    assert dc["series"] == ["pca-flow-by-hour-dealer"]


def test_dealer_failed_emails_count_terminal_failures_only():
    # BOUNCED is a retryable bounce waiting for its next attempt (Shipment.markBounced), not a failure.
    sql = _sql(_card("pca-emails-failed-by-hour-by-domain-dealer"))
    assert "s.status = 'FAILED'" in sql
    assert "'BOUNCED'" not in sql


DELIVERY_COLUMNS = ["domain", "attempted", "delivered", "in_progress", "failed", "retried", "late_bounced",
                    "late_bounce_open", "delivered_pct", "net_success_pct", "median_delivery_sec"]


def test_delivery_twin_returns_legacy_columns_in_order():
    sql = _sql(_card("pca-emails-delivery-by-domain-dealer"))
    final_select = sql.rsplit("SELECT", 1)[1]
    positions = [final_select.find(f"AS {c}") if c != "domain" else final_select.find("c.domain") for c in DELIVERY_COLUMNS]
    assert -1 not in positions and positions == sorted(positions), positions


def test_dealer_late_bounces_ignore_late_delivered_at():
    # late_delivered_at = delivered after FAILED (Shipment.stampLateDelivered), not a late-bounce resolution.
    for key in ("pca-emails-late-bounces-unresolved-dealer", "pca-emails-late-bounces-by-day-dealer",
                "pca-emails-late-bounce-lag-dealer"):
        assert "late_delivered_at IS" not in _sql(_card(key)), key


def test_lag_twin_uses_legacy_bucket_labels():
    legacy, twin = (_sql(_card(k)) for k in ("pca-emails-late-bounce-lag", "pca-emails-late-bounce-lag-dealer"))
    for label in ("01 <1 min", "02 1-5 min", "03 5-60 min", "04 1-6 h", "05 6-24 h", "06 >24 h", "07 no delivered_at"):
        assert f"'{label}'" in legacy and f"'{label}'" in twin, label


def test_late_bounce_dashcards_show_the_dealer_card_alone():
    by_key = {d["key"]: d for d in _dashcards()}
    for dc_key, card in (("dc-emails-late-bounces-by-day", "pca-emails-late-bounces-by-day-dealer"),
                         ("dc-emails-late-bounce-lag", "pca-emails-late-bounce-lag-dealer"),
                         ("dc-orders-by-day", "pca-orders-by-day-dealer")):
        dc = by_key[dc_key]
        assert (dc["card"], dc["series"]) == (card, []), dc_key
        assert all("card" not in m for m in dc["parameter_mappings"]), dc_key


def test_dealer_clients_switched_follows_twin_conventions():
    card = _card("pca-dealer-clients-switched")
    assert card["dataset_query"]["database"] == DEALER_DB
    assert card["collection"] == "pear-cider-automated"
    assert card["name"].startswith("Dealer · ")
    assert _sql(card).startswith(DEALER_HEADER)


def _stacked_dashcards():
    return [dc for dc in _dashcards() if dc.get("series")]


@pytest.mark.parametrize("dashcard", _stacked_dashcards(), ids=lambda dc: dc["key"])
def test_stacked_dashcard_series_colours_use_multi_card_keys(dashcard):
    # Metabase (series.ts getSeriesVizSettingsKey) keys series of a multi-card
    # dashcard as "<card name>: <label>"; bare card-level keys stop applying.
    cards = [_card(key) for key in [dashcard["card"], *dashcard["series"]]]
    names = {card["name"] for card in cards}
    labels = {
        label
        for card in cards
        for label in card["visualization_settings"].get("series_settings", {})
    }
    settings = dashcard["visualization_settings"].get("series_settings", {})
    for key in settings:
        name, sep, label = key.partition(": ")
        assert sep and name in names and label in labels, f"{dashcard['key']}: bad key {key!r}"
        # A "Dealer · " label is emitted only by the dealer card, any other label only by the legacy one.
        assert label.startswith("Dealer · ") == name.startswith("Dealer · "), (
            f"{dashcard['key']}: label of {key!r} is prefixed with the wrong card")
    expected_labels = {
        label
        for card in cards
        for label in card["visualization_settings"].get("series_settings", {})
    }
    assert {key.partition(": ")[2] for key in settings} == expected_labels or not labels


@pytest.mark.parametrize("dashcard", _stacked_dashcards(), ids=lambda dc: dc["key"])
def test_stacked_dashcard_has_explicit_title(dashcard):
    # Without card.title the header falls back to the first transformed series' name,
    # "<card name>: <first breakout value>" (CartesianChart.tsx: settings["card.title"] ?? card.name).
    assert dashcard["visualization_settings"].get("card.title"), dashcard["key"]


@pytest.mark.parametrize("dashcard", _stacked_dashcards(), ids=lambda dc: dc["key"])
def test_stacked_dashcard_legend_shows_bare_labels(dashcard):
    # series.ts: name = series_settings[<key>].title ?? "<card name>: <label>".
    for key, entry in dashcard["visualization_settings"].get("series_settings", {}).items():
        assert entry.get("title") == key.partition(": ")[2], f"{dashcard['key']}: {key!r}"
