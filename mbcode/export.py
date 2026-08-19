"""Export the live instance into the YAML tree + state file (read-only against Metabase)."""
from __future__ import annotations

import os

from . import repo
from .model import unique_slug
from .normalize import _parent_from_location, strip_lib_uuid
from .state import State


def run_export(config, client, root_dir: str, overwrite: bool) -> int:
    state = State.load(root_dir, config.host_slug, config.base_url)
    properties = client.get("/api/session/properties")
    version_tag = (properties.get("version") or {}).get("tag")

    listed_collections = _fetch_collections(client)
    # Archived collections are absent from the listing; the inventory sweep only walks
    # the listed (unarchived) ones, whose contents are the only listable ones.
    collection_ids = ["root"] + [c["id"] for c in listed_collections]
    live_collections = listed_collections + _readopt(
        client, state, root_dir, "collections", "/api/collection/{}",
        {c["id"] for c in listed_collections})
    card_items, dashboard_items = _inventory(client, collection_ids)
    dashboards = [client.get(f"/api/dashboard/{item['id']}") for item in dashboard_items]
    dashboards += _readopt(client, state, root_dir, "dashboards", "/api/dashboard/{}",
                           {d["id"] for d in dashboards})
    card_ids = [item["id"] for item in card_items]
    # Dashboard-internal questions (dashboard_id set) are absent from collection
    # item listings — pick them up from the dashcards that reference them. The
    # referenced list is deduped against itself: one card may back several dashcards.
    listed = set(card_ids)
    card_ids += [cid for cid in dict.fromkeys(_referenced_card_ids(dashboards))
                 if cid not in listed]
    cards = [client.get(f"/api/card/{cid}") for cid in card_ids]
    cards += _readopt(client, state, root_dir, "cards", "/api/card/{}", set(card_ids))

    coll_keys = _assign_keys(live_collections, state.reverse_map("collections"))
    card_keys = _assign_keys(cards, state.reverse_map("cards"))
    dash_keys = _assign_keys(dashboards, state.reverse_map("dashboards"))
    coll_key_by_id = {c["id"]: coll_keys[i] for i, c in enumerate(live_collections)}
    card_key_by_id = {c["id"]: card_keys[i] for i, c in enumerate(cards)}

    docs = []  # (section, key, doc, live)
    for i, coll in enumerate(live_collections):
        docs.append(("collections", coll_keys[i], _collection_doc(coll, coll_keys[i], coll_key_by_id), coll))
    for i, card in enumerate(cards):
        docs.append(("cards", card_keys[i], _card_doc(card, card_keys[i], coll_key_by_id), card))

    new_state = _base_state(config, version_tag)
    for section, key, doc, live in docs:
        new_state[section][key] = {"id": live["id"], "entity_id": live.get("entity_id")}

    for i, dash in enumerate(dashboards):
        key = dash_keys[i]
        old_entry = state.data["dashboards"].get(key) or {}
        doc, tabs_map, dashcards_map = _dashboard_doc(dash, key, coll_key_by_id, card_key_by_id, old_entry)
        docs.append(("dashboards", key, doc, dash))
        new_state["dashboards"][key] = {
            "id": dash["id"], "entity_id": dash.get("entity_id"),
            "tabs": tabs_map, "dashcards": dashcards_map,
        }

    error = _refuse_clobber(root_dir, docs, overwrite)
    if error:
        print(error)
        return 1

    for section, key, doc, _ in docs:
        repo.dump_doc(os.path.join(root_dir, section, f"{key}.yaml"), doc)
    state.data = new_state
    state.save()
    print(f"exported {len(live_collections)} collections, {len(cards)} cards, "
          f"{len(dashboards)} dashboards -> {root_dir}")
    print(f"state written to {state.path}")
    return 0


def _base_state(config, version_tag):
    return {
        "version": 1,
        "instance": config.base_url,
        "metabase_version": version_tag,
        "collections": {},
        "cards": {},
        "dashboards": {},
    }


def _fetch_collections(client) -> list:
    collections = client.get("/api/collection")
    return [
        c for c in collections
        if c.get("id") != "root" and not c.get("is_personal")
        and c.get("namespace") is None and not c.get("archived")
    ]


def _readopt(client, state, root_dir, section, path_template, live_ids) -> list:
    """Re-fetch entities the listings cannot see (archived ones never appear there).

    An entity is re-adopted when the state file still maps a key to it AND that key
    still has a YAML file: those are entities this repo owns. Fetching them by id keeps
    the export a picture of reality instead of dropping their state entries, which would
    make the next apply create a duplicate. Keys whose YAML file is gone, and keys whose
    id no longer exists on the instance, are dropped from the state.
    """
    entities = []
    for key, entry in state.data[section].items():
        entity_id = entry.get("id")
        if entity_id is None or entity_id in live_ids:
            continue
        if not os.path.isfile(os.path.join(root_dir, section, f"{key}.yaml")):
            continue
        entity = client.get_or_none(path_template.format(entity_id))
        if entity is None:
            print(f"warning: {section[:-1]} {key!r} (id {entity_id}) is in the state file "
                  "but not on the instance; its state entry is dropped")
            continue
        entities.append(entity)
    return entities


def _inventory(client, collection_ids):
    """Return ([{'id','name'}, ...] cards, same for dashboards) across all collections."""
    cards, dashboards = [], []
    targets = {"card": cards, "dashboard": dashboards}
    for cid in collection_ids:
        for model, bucket in targets.items():
            resp = client.get(f"/api/collection/{cid}/items?models={model}")
            items = resp.get("data") if isinstance(resp, dict) else resp
            bucket.extend({"id": item["id"], "name": item.get("name")} for item in items or [])
    return cards, dashboards


def _referenced_card_ids(dashboards) -> list:
    ids = []
    for dash in dashboards:
        for dc in dash.get("dashcards") or []:
            if dc.get("card_id") is not None:
                ids.append(dc["card_id"])
            ids.extend(item["id"] if isinstance(item, dict) else item
                       for item in dc.get("series") or [])
    return ids


def _assign_keys(entities, reverse_map) -> list:
    taken = set(reverse_map.values())
    keys = []
    for entity in entities:
        known = reverse_map.get(entity["id"])
        key = known or unique_slug(entity.get("name", "item"), taken)
        taken.add(key)
        keys.append(key)
    return keys


def _collection_doc(coll, key, coll_key_by_id) -> dict:
    parent_id = coll.get("parent_id")
    if parent_id is None:
        parent_id = _parent_from_location(coll.get("location"))
    return {
        "kind": "collection",
        "key": key,
        "name": coll.get("name"),
        "description": coll.get("description"),
        "parent": coll_key_by_id.get(parent_id),
        "archived": bool(coll.get("archived", False)),
    }


def _collection_ref(collection_id, coll_key_by_id):
    if collection_id is None:
        return "root"
    return coll_key_by_id.get(collection_id, "root")


def _card_doc(card, key, coll_key_by_id) -> dict:
    doc = {
        "kind": "card",
        "key": key,
        "name": card.get("name"),
        "description": card.get("description"),
        "collection": _collection_ref(card.get("collection_id"), coll_key_by_id),
        "type": card.get("type", "question"),
        "display": card.get("display"),
        "cache_ttl": card.get("cache_ttl"),
        "archived": bool(card.get("archived", False)),
        "dataset_query": strip_lib_uuid(card.get("dataset_query")),
        "visualization_settings": card.get("visualization_settings") or {},
        "parameters": card.get("parameters") or [],
    }
    if card.get("parameter_mappings"):
        doc["parameter_mappings"] = card["parameter_mappings"]
    return doc


def _dashboard_doc(dash, key, coll_key_by_id, card_key_by_id, old_entry):
    live_tabs = dash.get("tabs") or []
    tab_keys = _tab_keys(live_tabs, old_entry.get("tabs") or {})
    tab_key_by_id = {t["id"]: tab_keys[i] for i, t in enumerate(live_tabs)}
    tab_order = {t["id"]: i for i, t in enumerate(live_tabs)}
    dashcards, dashcards_map = _dashcard_docs(
        dash.get("dashcards") or [], card_key_by_id, tab_key_by_id, tab_order,
        old_entry.get("dashcards") or {})
    doc = {
        "kind": "dashboard",
        "key": key,
        "name": dash.get("name"),
        "description": dash.get("description"),
        "collection": _collection_ref(dash.get("collection_id"), coll_key_by_id),
        "width": dash.get("width", "fixed"),
        "auto_apply_filters": dash.get("auto_apply_filters", True),
        "cache_ttl": dash.get("cache_ttl"),
        "archived": bool(dash.get("archived", False)),
        "parameters": dash.get("parameters") or [],
    }
    if live_tabs:
        doc["tabs"] = [{"key": tab_keys[i], "name": t.get("name")} for i, t in enumerate(live_tabs)]
    doc["dashcards"] = dashcards
    tabs_map = {tab_keys[i]: t["id"] for i, t in enumerate(live_tabs)}
    return doc, tabs_map, dashcards_map


def _tab_keys(live_tabs, old_map) -> list:
    reverse = {tab_id: tkey for tkey, tab_id in old_map.items()}
    taken = set(reverse.values())
    keys = []
    for tab in live_tabs:
        known = reverse.get(tab["id"])
        key = known or unique_slug(tab.get("name", "tab"), taken)
        taken.add(key)
        keys.append(key)
    return keys


def _dashcard_docs(live_dashcards, card_key_by_id, tab_key_by_id, tab_order, old_map):
    reverse = {dc_id: dkey for dkey, dc_id in old_map.items()}
    taken = set(reverse.values())
    docs, id_map = [], {}
    ordered = sorted(live_dashcards, key=lambda dc: (
        tab_order.get(dc.get("dashboard_tab_id"), 0), dc.get("row", 0), dc.get("col", 0)))
    for dc in ordered:
        key = reverse.get(dc["id"]) or unique_slug(_dashcard_basename(dc, card_key_by_id), taken)
        taken.add(key)
        id_map[key] = dc["id"]
        docs.append(_dashcard_doc(dc, key, card_key_by_id, tab_key_by_id))
    return docs, id_map


def _dashcard_basename(dc, card_key_by_id) -> str:
    card_id = dc.get("card_id")
    if card_id is not None:
        return card_key_by_id.get(card_id, f"card-{card_id}")
    settings = dc.get("visualization_settings") or {}
    display = (settings.get("virtual_card") or {}).get("display") or "virtual"
    text = str(settings.get("text") or "")
    words = "-".join(text.split()[:4])
    return f"{display}-{words}"[:48] if words else display


def _dashcard_doc(dc, key, card_key_by_id, tab_key_by_id) -> dict:
    doc = {"key": key}
    card_id = dc.get("card_id")
    if card_id is not None:
        doc["card"] = card_key_by_id.get(card_id, card_id)
    tab_id = dc.get("dashboard_tab_id")
    if tab_id is not None:
        doc["tab"] = tab_key_by_id.get(tab_id)
    doc.update({
        "row": dc.get("row"), "col": dc.get("col"),
        "size_x": dc.get("size_x"), "size_y": dc.get("size_y"),
        "visualization_settings": dc.get("visualization_settings") or {},
        "parameter_mappings": dc.get("parameter_mappings") or [],
        "series": _series_keys(dc.get("series") or [], card_key_by_id),
        "inline_parameters": dc.get("inline_parameters") or [],
    })
    return doc


def _series_keys(series, card_key_by_id) -> list:
    keys = []
    for item in series:
        card_id = item.get("id") if isinstance(item, dict) else item
        key = card_key_by_id.get(card_id)
        if key is None:
            print(f"warning: series card id {card_id} is outside the exported set; skipped")
            continue
        keys.append(key)
    return keys


def _refuse_clobber(root_dir, docs, overwrite):
    if overwrite:
        return None
    existing = [
        os.path.join(section, f"{key}.yaml")
        for section, key, _, _ in docs
        if os.path.isfile(os.path.join(root_dir, section, f"{key}.yaml"))
    ]
    if not existing:
        return None
    listing = "\n  ".join(existing)
    return f"refusing to overwrite existing files (use --overwrite):\n  {listing}"
