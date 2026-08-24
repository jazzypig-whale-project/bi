"""normalize.py: canonicalisation and strip rules used by the diff engine."""
from __future__ import annotations

import pytest

from mbcode.normalize import (canon_viz, normalize_card, normalize_collection,
                              normalize_dashboard, normalize_dashcard, strip_lib_uuid)


def _contains_key(obj, key) -> bool:
    if isinstance(obj, dict):
        return key in obj or any(_contains_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_key(v, key) for v in obj)
    return False


# --- lib/uuid stripping ------------------------------------------------------

def test_strip_lib_uuid_recursive_over_a_query_with_a_join(load_fixture):
    query = load_fixture("card-65.json")["dataset_query"]
    assert _contains_key(query, "lib/uuid"), "fixture sanity check: query must contain lib/uuid"
    stripped = strip_lib_uuid(query)
    assert not _contains_key(stripped, "lib/uuid")


def test_strip_lib_uuid_preserves_join_and_aggregation_structure(load_fixture):
    query = load_fixture("card-65.json")["dataset_query"]
    stripped = strip_lib_uuid(query)
    stage = stripped["stages"][0]
    assert stage["source-table"] == 27
    assert stage["aggregation"][0][0] == "sum"
    assert stage["joins"][0]["alias"] == "Api Client - Client"
    assert stage["joins"][0]["strategy"] == "left-join"
    assert stage["order-by"][0][0] == "desc"


def test_strip_lib_uuid_leaves_non_uuid_scalars_alone():
    value = {"a": 1, "b": [1, "x", None], "lib/uuid": "drop-me"}
    assert strip_lib_uuid(value) == {"a": 1, "b": [1, "x", None]}
    assert strip_lib_uuid("plain string") == "plain string"
    assert strip_lib_uuid(None) is None


# --- absent vs null vs {}/[] equivalence ------------------------------------

_CARD_BASE = {
    "name": "Q", "type": "question", "display": "table",
    "dataset_query": {"lib/type": "mbql/query", "database": 2, "stages": []},
}


@pytest.mark.parametrize("key", ["visualization_settings", "parameters"])
@pytest.mark.parametrize("value", [None, {}, []])
def test_normalize_card_treats_absent_null_and_empty_as_equivalent(key, value):
    absent = normalize_card(dict(_CARD_BASE))
    with_value = normalize_card({**_CARD_BASE, key: value})
    assert key not in absent
    assert key not in with_value
    assert absent == with_value


# --- column_settings key canonicalisation -----------------------------------

def test_canon_viz_column_settings_key_ignores_order_and_whitespace():
    settings_a = {"column_settings": {
        '["ref",["field",1,{"base-type":"type/Integer"}]]': {"show_mini_bar": True},
    }}
    settings_b = {"column_settings": {
        '["ref", ["field", 1, {"base-type": "type/Integer"}]]': {"show_mini_bar": True},
    }}
    assert canon_viz(settings_a) == canon_viz(settings_b)


def test_canon_viz_falls_back_to_raw_string_for_non_json_key():
    settings = {"column_settings": {"not-json-{{{": {"show_mini_bar": True}}}
    out = canon_viz(settings)
    assert out["column_settings"] == {"not-json-{{{": {"show_mini_bar": True}}


def test_canon_viz_passes_through_non_dict_and_missing_column_settings():
    assert canon_viz("not-a-dict") == "not-a-dict"
    assert canon_viz({"graph.show_values": True}) == {"graph.show_values": True}


# --- server-generated fields stripped, per entity type ----------------------

def test_normalize_card_strips_server_generated_fields(load_fixture):
    live = load_fixture("card-40.json")
    out = normalize_card(live)
    forbidden = {"id", "entity_id", "created_at", "updated_at", "creator_id",
                "result_metadata", "collection_position", "table_id", "database_id",
                "dashboard_count", "dashboard", "dashboard_id"}
    assert not (forbidden & out.keys())


def test_normalize_card_drops_collection_id_when_archived():
    # Metabase forces an archived card's collection_id server-side and ignores
    # collection_id sent in the same/later PUT, so it isn't a field mbc can manage
    # once archived -- keeping it in the comparison produces a diff no apply can clear.
    out = normalize_card({**_CARD_BASE, "archived": True, "collection_id": 16})
    assert "collection_id" not in out


def test_normalize_card_keeps_collection_id_when_not_archived():
    out = normalize_card({**_CARD_BASE, "archived": False, "collection_id": 16})
    assert out["collection_id"] == 16


def test_normalize_collection_parent_id_from_location_when_field_is_present_but_null(load_fixture):
    live = load_fixture("collection-9.json")
    assert "parent_id" in live and live["parent_id"] is None  # real API shape, confirmed
    nested = dict(live)
    nested["id"] = 20
    nested["location"] = "/9/"  # nested under collection 9, per real API's location convention
    out = normalize_collection(nested)
    assert out["parent_id"] == 9


def test_normalize_collection_strips_server_generated_fields(load_fixture):
    live = load_fixture("collection-9.json")
    out = normalize_collection(live)
    forbidden = {"id", "entity_id", "created_at", "slug", "location",
                "effective_location", "effective_ancestors", "personal_owner_id", "namespace"}
    assert not (forbidden & out.keys())


def test_normalize_dashboard_strips_server_generated_fields(load_fixture):
    live = load_fixture("dashboard-1.json")
    out = normalize_dashboard(live, lambda dc, index: f"dc{index}")
    forbidden = {"id", "entity_id", "created_at", "updated_at", "creator_id",
                "collection_position", "param_fields", "enable_embedding", "caveats",
                "points_of_interest", "show_in_getting_started"}
    assert not (forbidden & out.keys())


# --- series reduced to card ids ---------------------------------------------

def test_normalize_dashcard_series_reduced_to_sorted_ids():
    dc = {"card_id": 1, "series": [{"id": 5, "name": "ignored"}, {"id": 3}]}
    assert normalize_dashcard(dc)["series"] == [3, 5]


def test_normalize_dashcard_series_handles_mixed_raw_and_dict_entries():
    dc = {"card_id": 1, "series": [7, {"id": 2}]}
    assert normalize_dashcard(dc)["series"] == [2, 7]


def test_normalize_dashcard_empty_series_is_dropped():
    dc = {"card_id": 1, "series": []}
    assert "series" not in normalize_dashcard(dc)


# --- dashcards compared by logical key, not array order ---------------------

def test_normalize_dashboard_dashcards_keyed_not_ordered():
    dashcards = [
        {"key": "a", "card_id": 1, "row": 0, "col": 0, "size_x": 4, "size_y": 4},
        {"key": "b", "card_id": 2, "row": 0, "col": 4, "size_x": 4, "size_y": 4},
    ]
    dash = {"name": "D", "dashcards": dashcards}
    reordered = {"name": "D", "dashcards": list(reversed(dashcards))}
    key_fn = lambda dc, index: dc["key"]
    assert normalize_dashboard(dash, key_fn) == normalize_dashboard(reordered, key_fn)
