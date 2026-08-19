"""validate.py: offline structural and reference checks. One test per rejection rule."""
from __future__ import annotations

import glob as glob_module

import pytest

from helpers import minimal_card, minimal_collection, minimal_dashboard, minimal_dashcard, write_doc
from mbcode.validate import FORBIDDEN_KEYS, validate_tree


def _problems(tmp_path):
    _, problems = validate_tree(str(tmp_path))
    return problems


def test_well_formed_tree_validates_clean(tmp_path):
    write_doc(tmp_path, "collections", "sales", minimal_collection())
    write_doc(tmp_path, "cards", "daily-revenue", minimal_card(collection="sales"))
    write_doc(tmp_path, "dashboards", "overview", minimal_dashboard(
        collection="sales", dashcards=[minimal_dashcard()]))
    assert _problems(tmp_path) == []


def test_filename_stem_must_equal_key(tmp_path):
    write_doc(tmp_path, "cards", "foo", minimal_card(key="bar"))
    problems = _problems(tmp_path)
    assert any("must equal the filename stem" in p for p in problems)


def test_key_must_be_a_slug(tmp_path):
    write_doc(tmp_path, "cards", "Foo", minimal_card(key="Foo"))
    problems = _problems(tmp_path)
    assert any("is not a slug" in p for p in problems)


def test_duplicate_key(tmp_path, monkeypatch):
    """Two files whose basenames collide inside one section. This is unreachable through a
    real filesystem given the key==filename-stem rule (a directory cannot hold two files with
    the same name), so glob.glob is monkeypatched to simulate the collision directly and
    exercise repo.load_tree's duplicate-detection branch."""
    (tmp_path / "collections").mkdir()
    (tmp_path / "elsewhere").mkdir()
    first = tmp_path / "collections" / "dup.yaml"
    second = tmp_path / "elsewhere" / "dup.yaml"
    first.write_text("kind: collection\nkey: dup\nname: Dup1\nparent: root\narchived: false\n")
    second.write_text("kind: collection\nkey: dup\nname: Dup2\nparent: root\narchived: false\n")

    real_glob = glob_module.glob

    def fake_glob(pattern):
        if pattern.endswith("collections/*.yaml") or pattern.endswith("collections\\*.yaml"):
            return [str(first), str(second)]
        return real_glob(pattern)

    monkeypatch.setattr(glob_module, "glob", fake_glob)
    problems = _problems(tmp_path)
    assert any("duplicate key" in p for p in problems)


def test_unresolvable_collection_reference(tmp_path):
    write_doc(tmp_path, "cards", "daily-revenue", minimal_card(collection="ghost"))
    problems = _problems(tmp_path)
    assert any("collection 'ghost' does not resolve" in p for p in problems)


def test_unresolvable_card_reference(tmp_path):
    write_doc(tmp_path, "dashboards", "overview", minimal_dashboard(dashcards=[minimal_dashcard()]))
    problems = _problems(tmp_path)
    assert any("card 'daily-revenue' does not resolve" in p for p in problems)


def test_unresolvable_tab_reference(tmp_path):
    write_doc(tmp_path, "cards", "daily-revenue", minimal_card())
    dc = minimal_dashcard(tab="ghost")
    write_doc(tmp_path, "dashboards", "overview", minimal_dashboard(
        tabs=[{"key": "real", "name": "Real"}], dashcards=[dc]))
    problems = _problems(tmp_path)
    assert any("tab 'ghost' does not resolve" in p for p in problems)


def test_collection_parent_cycle(tmp_path):
    write_doc(tmp_path, "collections", "a", minimal_collection(key="a", parent="b"))
    write_doc(tmp_path, "collections", "b", minimal_collection(key="b", parent="a"))
    problems = _problems(tmp_path)
    assert any("parent cycle" in p for p in problems)


@pytest.mark.parametrize("overrides,expected_snippet", [
    ({"col": 20, "size_x": 10}, "exceeds the 24-column grid"),
    ({"row": -1}, "row must be an integer >= 0"),
    ({"size_x": 0}, "size_x must be an integer >= 1"),
])
def test_dashcard_grid_violations(tmp_path, overrides, expected_snippet):
    write_doc(tmp_path, "cards", "daily-revenue", minimal_card())
    dc = minimal_dashcard(**overrides)
    write_doc(tmp_path, "dashboards", "overview", minimal_dashboard(dashcards=[dc]))
    problems = _problems(tmp_path)
    assert any(expected_snippet in p for p in problems)


def test_duplicate_dashcard_key(tmp_path):
    write_doc(tmp_path, "cards", "daily-revenue", minimal_card())
    dashcards = [minimal_dashcard(), minimal_dashcard(col=12)]
    write_doc(tmp_path, "dashboards", "overview", minimal_dashboard(dashcards=dashcards))
    problems = _problems(tmp_path)
    assert any("duplicate dashcard key" in p for p in problems)


def test_dashcard_with_both_card_and_virtual_card(tmp_path):
    write_doc(tmp_path, "cards", "daily-revenue", minimal_card())
    dc = minimal_dashcard(visualization_settings={"virtual_card": {"display": "text"}, "text": "hi"})
    write_doc(tmp_path, "dashboards", "overview", minimal_dashboard(dashcards=[dc]))
    problems = _problems(tmp_path)
    assert any("has both 'card:' and a virtual_card" in p for p in problems)


def test_dashcard_with_neither_card_nor_virtual_card(tmp_path):
    dc = {k: v for k, v in minimal_dashcard().items() if k != "card"}
    write_doc(tmp_path, "dashboards", "overview", minimal_dashboard(dashcards=[dc]))
    problems = _problems(tmp_path)
    assert any("needs either 'card:' or a virtual_card" in p for p in problems)


def test_dashcard_missing_tab_when_dashboard_has_multiple_tabs(tmp_path):
    write_doc(tmp_path, "cards", "daily-revenue", minimal_card())
    dash = minimal_dashboard(
        tabs=[{"key": "a", "name": "A"}, {"key": "b", "name": "B"}],
        dashcards=[minimal_dashcard()],  # no 'tab' key
    )
    write_doc(tmp_path, "dashboards", "overview", dash)
    problems = _problems(tmp_path)
    assert any("must declare 'tab:'" in p for p in problems)


@pytest.mark.parametrize("forbidden_key", sorted(FORBIDDEN_KEYS))
def test_forbidden_server_side_key_rejected(tmp_path, forbidden_key):
    doc = minimal_card()
    doc[forbidden_key] = "server-owned-value"
    write_doc(tmp_path, "cards", "daily-revenue", doc)
    problems = _problems(tmp_path)
    assert any(forbidden_key in p and "server-side keys" in p for p in problems)


def test_dataset_query_missing_database(tmp_path):
    doc = minimal_card()
    del doc["dataset_query"]["database"]
    write_doc(tmp_path, "cards", "daily-revenue", doc)
    problems = _problems(tmp_path)
    assert any("dataset_query.database must be an integer" in p for p in problems)


# --- top-level key allowlist ---------------------------------------------------

@pytest.mark.parametrize("section,doc,stray", [
    ("collections", minimal_collection(), "enable_embedding"),
    ("cards", minimal_card(), "caveats"),
    ("dashboards", minimal_dashboard(dashcards=[]), "points_of_interest"),
])
def test_unknown_top_level_key_rejected(tmp_path, section, doc, stray):
    """normalize.py strips these from both sides, so they diff clean and apply silently:
    validate has to catch them instead."""
    doc = dict(doc, **{stray: "whatever"})
    write_doc(tmp_path, section, doc["key"], doc)
    problems = _problems(tmp_path)
    assert any("unknown top-level keys" in p and stray in p for p in problems)


def test_unknown_key_message_names_only_the_offending_key(tmp_path):
    doc = minimal_card(collection="root")
    doc["caveats"] = "internal only"
    write_doc(tmp_path, "cards", "daily-revenue", doc)
    problems = [p for p in _problems(tmp_path) if "unknown top-level keys" in p]
    assert len(problems) == 1
    assert problems[0].endswith("caveats")


def test_server_side_key_keeps_its_specific_message(tmp_path):
    """A pasted API response must get the precise 'server-side keys' hint, not a generic one."""
    doc = minimal_card()
    doc["result_metadata"] = []
    write_doc(tmp_path, "cards", "daily-revenue", doc)
    problems = _problems(tmp_path)
    assert any("server-side keys must be removed: result_metadata" in p for p in problems)
    assert not any("unknown top-level keys" in p for p in problems)


@pytest.mark.parametrize("section,doc", [
    ("collections", minimal_collection()),
    ("cards", minimal_card(collection="root")),
    ("dashboards", minimal_dashboard(dashcards=[])),
])
def test_documented_fields_are_all_allowed(tmp_path, section, doc):
    write_doc(tmp_path, section, doc["key"], doc)
    assert not any("unknown top-level keys" in p for p in _problems(tmp_path))


def test_card_parameter_mappings_is_allowed(tmp_path):
    write_doc(tmp_path, "cards", "daily-revenue", minimal_card(parameter_mappings=[]))
    assert not any("unknown top-level keys" in p for p in _problems(tmp_path))


# --- a dashboard must declare 'dashcards' --------------------------------------

def test_dashboard_without_dashcards_key_rejected(tmp_path):
    """Omitting the key sends dashcards: [] and full-set replacement wipes the dashboard."""
    doc = {k: v for k, v in minimal_dashboard().items() if k != "dashcards"}
    write_doc(tmp_path, "dashboards", "overview", doc)
    problems = _problems(tmp_path)
    assert any("'dashcards' is required" in p for p in problems)


def test_dashboard_with_empty_dashcards_list_is_valid(tmp_path):
    write_doc(tmp_path, "dashboards", "overview", minimal_dashboard(dashcards=[]))
    assert _problems(tmp_path) == []


# --- 'tab:' is required whenever the dashboard declares any tab ----------------

def test_dashcard_missing_tab_on_a_single_tab_dashboard(tmp_path):
    write_doc(tmp_path, "cards", "daily-revenue", minimal_card())
    dash = minimal_dashboard(tabs=[{"key": "only", "name": "Only"}],
                             dashcards=[minimal_dashcard()])  # no 'tab' key
    write_doc(tmp_path, "dashboards", "overview", dash)
    problems = _problems(tmp_path)
    assert any("must declare 'tab:'" in p for p in problems)


def test_single_tab_dashboard_with_tab_declared_is_valid(tmp_path):
    write_doc(tmp_path, "cards", "daily-revenue", minimal_card())
    dash = minimal_dashboard(tabs=[{"key": "only", "name": "Only"}],
                             dashcards=[minimal_dashcard(tab="only")])
    write_doc(tmp_path, "dashboards", "overview", dash)
    assert _problems(tmp_path) == []


def test_untabbed_dashboard_still_allows_tabless_dashcards(tmp_path):
    write_doc(tmp_path, "cards", "daily-revenue", minimal_card())
    write_doc(tmp_path, "dashboards", "overview", minimal_dashboard(dashcards=[minimal_dashcard()]))
    assert _problems(tmp_path) == []
