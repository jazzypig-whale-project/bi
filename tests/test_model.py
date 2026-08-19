"""model.py: slugify and ref-sugar resolution."""
from __future__ import annotations

import pytest

from mbcode.model import (Resolver, desired_dashcards, desired_tabs,
                          resolved_parameter_mappings, slugify, unique_slug)
from mbcode.state import State


# --- slugify ------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("Оборот сертификатов по номиналам", "oborot-sertifikatov-po-nominalam"),
    ("Средний чек по дням", "sredniy-chek-po-dnyam"),
])
def test_slugify_transliterates_cyrillic(name, expected):
    assert slugify(name) == expected


def test_slugify_strips_leading_and_trailing_separators():
    assert slugify("!!!Hello!!! World---") == "hello-world"


def test_slugify_all_punctuation_falls_back_to_item():
    assert slugify("!!!") == "item"
    assert slugify("---") == "item"


def test_unique_slug_de_duplicates_with_numeric_suffix():
    taken = set()
    first = unique_slug("Foo", taken)
    taken.add(first)
    second = unique_slug("Foo", taken)
    taken.add(second)
    third = unique_slug("Foo", taken)
    assert (first, second, third) == ("foo", "foo-2", "foo-3")


# --- Resolver / ref-sugar resolution -------------------------------------------

def _state(**sections):
    data = {"collections": {}, "cards": {}, "dashboards": {}}
    data.update(sections)
    return State("unused", data)


@pytest.mark.parametrize("ref", [None, "root"])
def test_resolver_collection_id_root_and_none_resolve_to_none(ref):
    resolver = Resolver(_state())
    assert resolver.collection_id(ref) is None


def test_resolver_collection_id_resolves_known_key():
    resolver = Resolver(_state(collections={"sales": {"id": 5}}))
    assert resolver.collection_id("sales") == 5


def test_resolver_collection_id_unknown_key_is_a_placeholder():
    resolver = Resolver(_state())
    assert resolver.collection_id("ghost") == "<new:collection:ghost>"


def test_resolver_card_id_resolves_known_key():
    resolver = Resolver(_state(cards={"daily-revenue": {"id": 42}}))
    assert resolver.card_id("daily-revenue") == 42


def test_resolver_card_id_unknown_key_is_a_placeholder():
    resolver = Resolver(_state())
    assert resolver.card_id("ghost") == "<new:card:ghost>"


def test_resolver_tab_id_resolves_known_key():
    state = _state(dashboards={"overview": {"tabs": {"main": 7}, "dashcards": {}}})
    resolver = Resolver(state)
    assert resolver.tab_id("overview", "main") == 7


def test_resolver_tab_id_unknown_key_is_a_placeholder():
    resolver = Resolver(_state(dashboards={"overview": {"tabs": {}, "dashcards": {}}}))
    assert resolver.tab_id("overview", "ghost") == "<new:tab:ghost>"


def test_desired_dashcards_series_becomes_id_objects():
    state = _state(cards={"card-a": {"id": 5}, "card-b": {"id": 6}})
    resolver = Resolver(state)
    doc = {"key": "overview", "dashcards": [
        {"key": "combo", "card": "card-a", "row": 0, "col": 0, "size_x": 4, "size_y": 4,
         "series": ["card-b"]},
    ]}
    pairs = desired_dashcards(doc, resolver)
    _, written = pairs[0]
    assert written["series"] == [{"id": 6}]


def test_desired_dashcards_tab_resolves_to_dashboard_tab_id():
    state = _state(cards={"card-a": {"id": 5}},
                   dashboards={"overview": {"tabs": {"main": 7}, "dashcards": {}}})
    resolver = Resolver(state)
    doc = {"key": "overview", "dashcards": [
        {"key": "chart", "card": "card-a", "tab": "main",
         "row": 0, "col": 0, "size_x": 4, "size_y": 4},
    ]}
    _, written = desired_dashcards(doc, resolver)[0]
    assert written["dashboard_tab_id"] == 7


def test_desired_tabs_resolves_each_tab_to_its_id():
    state = _state(dashboards={"overview": {"tabs": {"main": 7}, "dashcards": {}}})
    resolver = Resolver(state)
    doc = {"key": "overview", "tabs": [{"key": "main", "name": "Main"}]}
    pairs = desired_tabs(doc, resolver)
    assert pairs == [("main", {"id": 7, "name": "Main"})]


def test_desired_dashcards_virtual_dashcard_has_null_card_id():
    resolver = Resolver(_state())
    doc = {"key": "overview", "dashcards": [
        {"key": "heading", "row": 0, "col": 0, "size_x": 24, "size_y": 2,
         "visualization_settings": {"virtual_card": {"display": "heading"}, "text": "Hi"}},
    ]}
    pairs = desired_dashcards(doc, resolver)
    _, written = pairs[0]
    assert written["card_id"] is None


# --- resolved_parameter_mappings: card_id injection -----------------------------

def test_resolved_parameter_mappings_fills_in_card_id_when_omitted():
    dc = {"parameter_mappings": [
        {"parameter_id": "days", "target": ["variable", ["template-tag", "days"]]},
    ]}
    mappings = resolved_parameter_mappings(dc, 69)
    assert mappings == [
        {"parameter_id": "days", "target": ["variable", ["template-tag", "days"]], "card_id": 69},
    ]


def test_resolved_parameter_mappings_treats_explicit_none_like_omitted():
    dc = {"parameter_mappings": [{"parameter_id": "days", "card_id": None, "target": []}]}
    mappings = resolved_parameter_mappings(dc, 69)
    assert mappings[0]["card_id"] == 69


def test_resolved_parameter_mappings_leaves_an_explicit_card_id_untouched():
    dc = {"parameter_mappings": [{"parameter_id": "days", "card_id": 5, "target": []}]}
    mappings = resolved_parameter_mappings(dc, 69)
    assert mappings[0]["card_id"] == 5


def test_resolved_parameter_mappings_no_mappings_yields_empty_list():
    assert resolved_parameter_mappings({}, 69) == []
    assert resolved_parameter_mappings({"parameter_mappings": []}, 69) == []


def test_resolved_parameter_mappings_none_card_id_does_not_crash():
    dc = {"parameter_mappings": [{"parameter_id": "days", "target": []}]}
    mappings = resolved_parameter_mappings(dc, None)
    assert mappings == dc["parameter_mappings"]


def test_desired_dashcards_injects_card_id_into_parameter_mappings():
    state = _state(cards={"card-a": {"id": 69}})
    resolver = Resolver(state)
    doc = {"key": "overview", "dashcards": [
        {"key": "chart", "card": "card-a", "row": 0, "col": 0, "size_x": 4, "size_y": 4,
         "parameter_mappings": [
             {"parameter_id": "days", "target": ["variable", ["template-tag", "days"]]},
         ]},
    ]}
    _, written = desired_dashcards(doc, resolver)[0]
    assert written["parameter_mappings"][0]["card_id"] == 69
