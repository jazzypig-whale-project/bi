"""Round-trip: live JSON -> export-to-YAML path -> desired-payload path -> empty diff.

Exercises export._card_doc / export._dashboard_doc (offline: called directly on
fixture dicts, no HTTP and no files written) followed by the same normalization/
resolution the diff engine uses, and asserts _entity_changes finds nothing.
"""
from __future__ import annotations

import pytest

from mbcode import export
from mbcode.diff import _entity_changes, desired_card_normalized, desired_dashboard_normalized, \
    live_dashboard_normalized
from mbcode.model import Resolver
from mbcode.normalize import normalize_card
from mbcode.state import State


def _state(**sections):
    data = {"collections": {}, "cards": {}, "dashboards": {}}
    data.update(sections)
    return State("unused", data)


@pytest.mark.parametrize("fixture_name,key,collection_key,collection_id", [
    ("card-65.json", "sales-by-partner", None, None),          # collection_id None -> root
    ("card-40.json", "cert-turnover-by-face-value", "certs", 9),
])
def test_card_export_round_trip_has_no_drift(load_fixture, fixture_name, key,
                                             collection_key, collection_id):
    live = load_fixture(fixture_name)
    coll_key_by_id = {collection_id: collection_key} if collection_key else {}

    doc = export._card_doc(live, key, coll_key_by_id)

    state = _state(
        cards={key: {"id": live["id"], "entity_id": live.get("entity_id")}},
        collections=({collection_key: {"id": collection_id}} if collection_key else {}),
    )
    resolver = Resolver(state)
    desired_n = desired_card_normalized(doc, resolver)
    live_n = normalize_card(live)

    assert _entity_changes(live_n, desired_n) == []


def test_dashboard_export_round_trip_has_no_drift(load_fixture):
    live = load_fixture("dashboard-1.json")
    key = "ops-overview"
    coll_key_by_id = {2: "examples"}
    card_key_by_id = {}  # dashboard-1's dashcards are all virtual; no real card refs

    doc, tabs_map, dashcards_map = export._dashboard_doc(
        live, key, coll_key_by_id, card_key_by_id, old_entry={})

    state = _state(
        collections={"examples": {"id": 2}},
        dashboards={key: {"id": live["id"], "entity_id": live.get("entity_id"),
                          "tabs": tabs_map, "dashcards": dashcards_map}},
    )
    resolver = Resolver(state)
    desired_n = desired_dashboard_normalized(doc, resolver)
    live_n = live_dashboard_normalized(live, state, key)

    assert _entity_changes(live_n, desired_n) == []


def test_dashboard_round_trip_preserves_tabs_and_virtual_dashcard_count(load_fixture):
    live = load_fixture("dashboard-1.json")
    doc, tabs_map, dashcards_map = export._dashboard_doc(
        live, "ops-overview", {2: "examples"}, {}, old_entry={})
    assert len(doc["tabs"]) == 3
    assert len(doc["dashcards"]) == 14
    assert all("card" not in dc for dc in doc["dashcards"])  # every dashcard is virtual
    assert len(tabs_map) == 3
    assert len(dashcards_map) == 14
