"""export.py: state merging (archived entities), referenced-card dedupe, clobber refusal.

Everything here is offline: the FakeClient from helpers.py serves every HTTP call and the
tree/state live under tmp_path.
"""
from __future__ import annotations

import os
import types

import yaml

from helpers import FakeClient, write_doc
from mbcode import export as export_mod

HOST_SLUG = "mb-example-test"
BASE_URL = "https://mb.example.test"


def _config():
    return types.SimpleNamespace(host_slug=HOST_SLUG, base_url=BASE_URL)


def _state_path(root):
    return os.path.join(str(root), ".state", f"{HOST_SLUG}.yaml")


def _write_state(root, data) -> None:
    path = _state_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)


def _read_state(root):
    with open(_state_path(root), encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _read_doc(root, section, key):
    with open(os.path.join(str(root), section, f"{key}.yaml"), encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _base_gets(collections=(), items=None):
    """GET responses for session properties, the collection listing and empty item listings."""
    gets = {
        "/api/session/properties": {"version": {"tag": "v0.60.7"}},
        "/api/collection": list(collections),
    }
    for cid in ["root"] + [c["id"] for c in collections]:
        for model in ("card", "dashboard"):
            gets[f"/api/collection/{cid}/items?models={model}"] = {"data": []}
    gets.update(items or {})
    return gets


def _collection(cid, name, archived=False):
    return {"id": cid, "name": name, "description": None, "location": "/",
            "entity_id": f"coll-{cid}", "archived": archived}


def _card(cid, name):
    return {
        "id": cid, "name": name, "description": None, "collection_id": None,
        "entity_id": f"card-{cid}", "type": "question", "display": "table", "cache_ttl": None,
        "archived": False,
        "dataset_query": {"lib/type": "mbql/query", "database": 2,
                          "stages": [{"lib/type": "mbql.stage/mbql", "source-table": 1}]},
        "visualization_settings": {}, "parameters": [],
    }


def _dashcard(dc_id, card_id, col, series=()):
    return {"id": dc_id, "card_id": card_id, "dashboard_tab_id": None, "row": 0, "col": col,
            "size_x": 6, "size_y": 4, "visualization_settings": {}, "parameter_mappings": [],
            "series": list(series), "inline_parameters": []}


# --- Fix 1: the export merges into the existing state instead of replacing it ---

def test_export_keeps_state_entry_of_an_archived_collection(tmp_path):
    """An archived collection is absent from /api/collection, but its key still has a YAML
    file, so the export re-fetches it by id and keeps its state entry (otherwise the next
    apply would create a duplicate)."""
    write_doc(tmp_path, "collections", "old-reports", {
        "kind": "collection", "key": "old-reports", "name": "Old reports",
        "description": None, "parent": "root", "archived": True})
    _write_state(tmp_path, {
        "version": 1, "instance": BASE_URL, "metabase_version": "v0.60.7",
        "collections": {"old-reports": {"id": 7, "entity_id": "coll-7"}},
        "cards": {}, "dashboards": {},
    })
    client = FakeClient(gets=_base_gets(
        collections=[], items={"/api/collection/7": _collection(7, "Old reports", archived=True)}))

    assert export_mod.run_export(_config(), client, str(tmp_path), overwrite=True) == 0

    state = _read_state(tmp_path)
    assert state["collections"]["old-reports"] == {"id": 7, "entity_id": "coll-7"}
    assert _read_doc(tmp_path, "collections", "old-reports")["archived"] is True


def test_export_keeps_state_entries_of_archived_cards_and_dashboards(tmp_path):
    write_doc(tmp_path, "cards", "old-card", {"kind": "card", "key": "old-card"})
    write_doc(tmp_path, "dashboards", "old-dash", {"kind": "dashboard", "key": "old-dash"})
    _write_state(tmp_path, {
        "version": 1, "instance": BASE_URL, "metabase_version": None,
        "collections": {},
        "cards": {"old-card": {"id": 41, "entity_id": "card-41"}},
        "dashboards": {"old-dash": {"id": 5, "entity_id": "dash-5",
                                    "tabs": {}, "dashcards": {"tile": 91}}},
    })
    archived_card = dict(_card(41, "Old card"), archived=True)
    archived_dash = {"id": 5, "name": "Old dash", "description": None, "collection_id": None,
                     "entity_id": "dash-5", "width": "fixed", "auto_apply_filters": True,
                     "cache_ttl": None, "archived": True, "parameters": [], "tabs": [],
                     "dashcards": [_dashcard(91, 41, 0)]}
    client = FakeClient(gets=_base_gets(items={
        "/api/card/41": archived_card, "/api/dashboard/5": archived_dash}))

    assert export_mod.run_export(_config(), client, str(tmp_path), overwrite=True) == 0

    state = _read_state(tmp_path)
    assert state["cards"]["old-card"]["id"] == 41
    assert state["dashboards"]["old-dash"]["id"] == 5
    # the dashcard key -> id map survives too
    assert state["dashboards"]["old-dash"]["dashcards"] == {"tile": 91}


def test_export_drops_state_entry_when_the_yaml_file_is_gone(tmp_path):
    """No file, no ownership: the entry is not carried forward and nothing is re-fetched."""
    _write_state(tmp_path, {
        "version": 1, "instance": BASE_URL, "metabase_version": None,
        "collections": {"deleted-key": {"id": 7, "entity_id": "coll-7"}},
        "cards": {}, "dashboards": {},
    })
    client = FakeClient(gets=_base_gets())

    assert export_mod.run_export(_config(), client, str(tmp_path), overwrite=True) == 0

    assert _read_state(tmp_path)["collections"] == {}
    assert ("GET", "/api/collection/7", None) not in client.calls


def test_export_drops_state_entry_when_the_entity_is_gone_from_the_instance(tmp_path, capsys):
    write_doc(tmp_path, "collections", "old-reports", {
        "kind": "collection", "key": "old-reports", "name": "Old reports",
        "description": None, "parent": "root", "archived": True})
    _write_state(tmp_path, {
        "version": 1, "instance": BASE_URL, "metabase_version": None,
        "collections": {"old-reports": {"id": 7, "entity_id": "coll-7"}},
        "cards": {}, "dashboards": {},
    })
    client = FakeClient(gets=_base_gets())  # /api/collection/7 is unconfigured -> None

    assert export_mod.run_export(_config(), client, str(tmp_path), overwrite=True) == 0

    assert _read_state(tmp_path)["collections"] == {}
    assert "not on the instance" in capsys.readouterr().out


# --- Fix 5: referenced card ids are deduped against themselves ------------------

def test_referenced_card_ids_are_deduped_preserving_order():
    dashboards = [{"dashcards": [
        _dashcard(1, 40, 0, series=[{"id": 41}]),
        _dashcard(2, 40, 6),
        _dashcard(3, 41, 12),
    ]}]
    ids = export_mod._referenced_card_ids(dashboards)
    assert list(dict.fromkeys(ids)) == [40, 41]


def test_card_referenced_by_two_dashcards_is_exported_once(tmp_path):
    dash = {"id": 3, "name": "Overview", "description": None, "collection_id": None,
            "entity_id": "dash-3", "width": "fixed", "auto_apply_filters": True,
            "cache_ttl": None, "archived": False, "parameters": [], "tabs": [],
            "dashcards": [_dashcard(11, 40, 0, series=[{"id": 40}]), _dashcard(12, 40, 6)]}
    gets = _base_gets(items={"/api/dashboard/3": dash, "/api/card/40": _card(40, "Shared card")})
    gets["/api/collection/root/items?models=dashboard"] = {"data": [{"id": 3, "name": "Overview"}]}
    client = FakeClient(gets=gets)

    assert export_mod.run_export(_config(), client, str(tmp_path), overwrite=True) == 0

    card_files = sorted(os.listdir(os.path.join(str(tmp_path), "cards")))
    assert card_files == ["shared-card.yaml"]
    assert _read_state(tmp_path)["cards"] == {"shared-card": {"id": 40, "entity_id": "card-40"}}
    assert [c for c in client.calls if c[1] == "/api/card/40"] == [("GET", "/api/card/40", None)]


# --- clobber refusal ------------------------------------------------------------

def test_export_refuses_to_overwrite_existing_files_without_the_flag(tmp_path, capsys):
    write_doc(tmp_path, "collections", "sales", {
        "kind": "collection", "key": "sales", "name": "Sales", "description": None,
        "parent": "root", "archived": False})
    client = FakeClient(gets=_base_gets(collections=[_collection(2, "Sales")]))

    assert export_mod.run_export(_config(), client, str(tmp_path), overwrite=False) == 1

    out = capsys.readouterr().out
    assert "refusing to overwrite existing files" in out
    assert "collections/sales.yaml" in out
    assert not os.path.exists(_state_path(tmp_path))
