"""Desired (YAML) vs live comparison and plan rendering."""
from __future__ import annotations

import json

from . import model
from .export import _fetch_collections, _inventory
from .model import Resolver
from .normalize import (fallback_dashcard_key, normalize_card, normalize_collection,
                        normalize_dashboard)

ABSENT = "(absent)"


# --- desired-side normalization ---------------------------------------------
def desired_collection_normalized(doc, resolver):
    return normalize_collection(model.desired_collection(doc, resolver))


def desired_card_normalized(doc, resolver):
    return normalize_card(model.desired_card(doc, resolver))


def desired_dashboard_normalized(doc, resolver):
    obj = model.desired_dashboard_scalars(doc, resolver)
    tabs = model.desired_tabs(doc, resolver)
    pairs = model.desired_dashcards(doc, resolver)
    obj["tabs"] = [tab for _, tab in tabs]
    obj["dashcards"] = [dc for _, dc in pairs]
    keys = [key for key, _ in pairs]
    return normalize_dashboard(obj, lambda dc, index: keys[index])


def live_dashboard_normalized(live, state, dashboard_key):
    reverse = {dc_id: dkey for dkey, dc_id in
               state.dashboard_entry(dashboard_key)["dashcards"].items()}
    return normalize_dashboard(
        live, lambda dc, index: reverse.get(dc.get("id")) or fallback_dashcard_key(dc))


# --- plan building ----------------------------------------------------------
def build_plan(tree, state, client):
    resolver = Resolver(state)
    plan = {"creates": [], "updates": [], "orphans": []}
    for key in _collections_parent_first(tree):
        doc = tree.collections[key]
        desired = desired_collection_normalized(doc, resolver)
        _plan_entity(plan, "collections", key, desired,
                     _fetch_live(client, state, "collections", key, "/api/collection/{}"),
                     normalize_collection)
    for key, doc in tree.cards.items():
        desired = desired_card_normalized(doc, resolver)
        _plan_entity(plan, "cards", key, desired,
                     _fetch_live(client, state, "cards", key, "/api/card/{}"),
                     normalize_card)
    for key, doc in tree.dashboards.items():
        desired = desired_dashboard_normalized(doc, resolver)
        live = _fetch_live(client, state, "dashboards", key, "/api/dashboard/{}")
        live_n = live and live_dashboard_normalized(live, state, key)
        _plan_normalized(plan, "dashboards", key, desired, live_n)
    plan["orphans"] = _find_orphans(tree, state, client)
    return plan


def _collections_parent_first(tree):
    ordered, seen = [], set()

    def visit(key):
        if key in seen or key not in tree.collections:
            return
        seen.add(key)
        parent = tree.collections[key].get("parent")
        if parent not in (None, "root"):
            visit(parent)
        ordered.append(key)

    for key in tree.collections:
        visit(key)
    return ordered


def _fetch_live(client, state, section, key, path_template):
    entity_id = state.entity_id_of(section, key)
    if entity_id is None:
        return None
    return client.get_or_none(path_template.format(entity_id))


def _plan_entity(plan, section, key, desired, live, normalizer):
    live_n = normalizer(live) if live is not None else None
    _plan_normalized(plan, section, key, desired, live_n)


def _plan_normalized(plan, section, key, desired, live_n):
    if live_n is None:
        plan["creates"].append({"section": section, "key": key})
        return
    changes = _entity_changes(live_n, desired)
    if changes:
        plan["updates"].append({"section": section, "key": key, "changes": changes})


def _entity_changes(live_n, desired_n):
    changes = []
    for field in sorted(set(live_n) | set(desired_n)):
        if field == "dashcards":
            changes.extend(_dashcard_changes(live_n.get(field) or {}, desired_n.get(field) or {}))
            continue
        before = live_n.get(field, ABSENT)
        after = desired_n.get(field, ABSENT)
        if before != after:
            changes.append({"field": field, "before": before, "after": after})
    return changes


def _dashcard_changes(live_map, desired_map):
    changes = []
    for key in sorted(set(live_map) | set(desired_map)):
        prefix = f"dashcards.{key}"
        if key not in live_map:
            changes.append({"field": prefix, "before": ABSENT, "after": desired_map[key],
                            "note": "dashcard added"})
            continue
        if key not in desired_map:
            changes.append({"field": prefix, "before": live_map[key], "after": ABSENT,
                            "note": "dashcard REMOVED from the dashboard on apply"})
            continue
        live_dc, desired_dc = live_map[key], desired_map[key]
        for sub in sorted(set(live_dc) | set(desired_dc)):
            before = live_dc.get(sub, ABSENT)
            after = desired_dc.get(sub, ABSENT)
            if before != after:
                changes.append({"field": f"{prefix}.{sub}", "before": before, "after": after})
    return changes


def _find_orphans(tree, state, client):
    live_collections = _fetch_collections(client)
    collection_ids = ["root"] + [c["id"] for c in live_collections]
    live_cards, live_dashboards = _inventory(client, collection_ids)
    sections = (
        ("collections", [{"id": c["id"], "name": c.get("name")} for c in live_collections]),
        ("cards", live_cards),
        ("dashboards", live_dashboards),
    )
    orphans = []
    for section, items in sections:
        mapped = {entry.get("id") for key, entry in state.data[section].items()
                  if key in tree.section(section)}
        orphans.extend(
            {"section": section, "id": item["id"], "name": item.get("name")}
            for item in items if item["id"] not in mapped
        )
    return orphans


# --- rendering --------------------------------------------------------------
def _compact(value):
    if value == ABSENT:
        return ABSENT
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= 120 else text[:117] + "..."


def render_plan(plan) -> str:
    lines = []
    for item in plan["creates"]:
        lines.append(f"CREATE {item['section'][:-1]} {item['key']}")
    for item in plan["updates"]:
        lines.append(f"UPDATE {item['section'][:-1]} {item['key']}")
        for change in item["changes"]:
            note = f"  [{change['note']}]" if change.get("note") else ""
            lines.append(f"  ~ {change['field']}: {_compact(change['before'])}"
                         f" -> {_compact(change['after'])}{note}")
    for item in plan["orphans"]:
        lines.append(f"ORPHAN {item['section'][:-1]} id={item['id']} {item['name']!r}"
                     " (on instance, not in code; left alone)")
    total = len(plan["creates"]) + len(plan["updates"])
    lines.append("No changes." if total == 0 else f"{total} change(s) to apply.")
    return "\n".join(lines)


def has_changes(plan) -> bool:
    return bool(plan["creates"] or plan["updates"])


def duplicate_name_conflicts(tree, plan) -> list:
    """CREATEs whose name matches an ORPHAN of the same kind — the signature of a key rename.

    Renaming a logical key makes the tool plan a CREATE for the new key while the entity the
    old key pointed at becomes an orphan: applying that would produce a server-side duplicate
    instead of a rename.
    """
    orphans_by_name = {}
    for orphan in plan["orphans"]:
        orphans_by_name.setdefault((orphan["section"], orphan.get("name")), []).append(orphan)
    conflicts = []
    for item in plan["creates"]:
        doc = tree.section(item["section"]).get(item["key"]) or {}
        conflicts.extend(
            {"section": item["section"], "key": item["key"], "name": doc.get("name"),
             "orphan_id": orphan["id"]}
            for orphan in orphans_by_name.get((item["section"], doc.get("name")), [])
        )
    return conflicts


def render_duplicate_name_warning(conflicts) -> str:
    lines = ["WARNING: these CREATEs carry the name of an entity that is already on the "
             "instance but not in code:"]
    lines.extend(
        f"  CREATE {c['section'][:-1]} {c['key']} (name {c['name']!r})"
        f" vs ORPHAN {c['section'][:-1]} id={c['orphan_id']}"
        for c in conflicts
    )
    lines.append("This is what a renamed key looks like. Applying would create a DUPLICATE, "
                 "not rename anything.")
    lines.append("To rename: edit the key in the state file (.state/<host>.yaml) instead. "
                 "To create the duplicate on purpose: apply --allow-duplicate-names.")
    return "\n".join(lines)
