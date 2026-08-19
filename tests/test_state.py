"""state.py: the identity store must survive a crash and a hand-edited file."""
from __future__ import annotations

import os

import pytest
import yaml

from mbcode import state as state_mod
from mbcode.state import State


def _state(path, **sections):
    data = {"version": 1, "instance": "http://x", "metabase_version": None,
            "collections": {}, "cards": {}, "dashboards": {}}
    data.update(sections)
    return State(str(path), data)


# --- atomic save ---------------------------------------------------------------

def test_save_writes_the_file_and_leaves_no_temp_behind(tmp_path):
    path = tmp_path / ".state" / "host.yaml"
    st = _state(path, cards={"daily-revenue": {"id": 7, "entity_id": "c7"}})

    st.save()

    assert yaml.safe_load(path.read_text(encoding="utf-8"))["cards"] == {
        "daily-revenue": {"id": 7, "entity_id": "c7"}}
    assert os.listdir(tmp_path / ".state") == ["host.yaml"]


def test_save_is_atomic_a_crash_mid_dump_leaves_the_previous_state_intact(tmp_path, monkeypatch):
    path = tmp_path / ".state" / "host.yaml"
    st = _state(path, cards={"daily-revenue": {"id": 7}})
    st.save()
    before = path.read_text(encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("disk full halfway through the dump")

    monkeypatch.setattr(state_mod.yaml, "safe_dump", boom)
    st.data["cards"]["daily-revenue"]["id"] = 8
    with pytest.raises(OSError):
        st.save()

    # The identity store is the only link between keys and instance ids: a partial
    # write here would orphan every managed entity.
    assert path.read_text(encoding="utf-8") == before
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["cards"]["daily-revenue"]["id"] == 7


def test_save_replaces_an_existing_file_rather_than_appending(tmp_path):
    path = tmp_path / ".state" / "host.yaml"
    st = _state(path, cards={"a": {"id": 1}, "b": {"id": 2}})
    st.save()
    st.data["cards"].pop("b")
    st.save()

    assert yaml.safe_load(path.read_text(encoding="utf-8"))["cards"] == {"a": {"id": 1}}


def test_saved_state_round_trips_through_load(tmp_path):
    path = tmp_path / ".state" / "host.yaml"
    _state(path, dashboards={"overview": {"id": 100, "entity_id": "e100",
                                          "tabs": {"t1": 500},
                                          "dashcards": {"chart": 900}}}).save()

    loaded = State.load(str(tmp_path), "host", "http://x")

    assert loaded.dashboard_entry("overview") == {
        "id": 100, "entity_id": "e100", "tabs": {"t1": 500}, "dashcards": {"chart": 900}}


# --- hand-edited entries -------------------------------------------------------

def test_dashboard_entry_repairs_hand_edited_null_tabs_and_dashcards(tmp_path):
    # `tabs:` with nothing after it parses as None; setdefault would keep the None
    # and the next .get() would raise AttributeError.
    st = _state(tmp_path / "s.yaml",
                dashboards={"overview": {"id": 100, "tabs": None, "dashcards": None}})

    entry = st.dashboard_entry("overview")

    assert entry["tabs"] == {}
    assert entry["dashcards"] == {}
    assert entry["tabs"].get("t1") is None  # would have raised on a None


def test_dashboard_entry_repairs_a_file_written_with_null_maps(tmp_path):
    path = tmp_path / ".state" / "host.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("version: 1\ndashboards:\n  overview:\n    id: 100\n    tabs:\n"
                    "    dashcards:\n", encoding="utf-8")

    entry = State.load(str(tmp_path), "host", "http://x").dashboard_entry("overview")

    assert entry == {"id": 100, "tabs": {}, "dashcards": {}}


def test_dashboard_entry_keeps_existing_maps_untouched(tmp_path):
    st = _state(tmp_path / "s.yaml",
                dashboards={"overview": {"id": 100, "tabs": {"t1": 500},
                                         "dashcards": {"chart": 900}}})

    entry = st.dashboard_entry("overview")

    assert entry["tabs"] == {"t1": 500}
    assert entry["dashcards"] == {"chart": 900}


def test_dashboard_entry_creates_a_blank_entry_for_an_unknown_key(tmp_path):
    st = _state(tmp_path / "s.yaml")
    assert st.dashboard_entry("brand-new") == {"tabs": {}, "dashcards": {}}
