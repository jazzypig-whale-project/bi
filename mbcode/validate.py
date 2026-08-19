"""Offline validation of the YAML tree."""
from __future__ import annotations

from . import repo
from .model import KEY_RE

FORBIDDEN_KEYS = frozenset((
    "id", "entity_id", "created_at", "updated_at", "creator_id", "result_metadata",
    "collection_position", "slug", "location", "table_id", "database_id",
    "dashboard_count",
))

ALLOWED_KEYS = {
    "collections": frozenset(("kind", "key", "name", "description", "parent", "archived")),
    "cards": frozenset((
        "kind", "key", "name", "description", "collection", "type", "display", "cache_ttl",
        "archived", "dataset_query", "visualization_settings", "parameters",
        "parameter_mappings",
    )),
    "dashboards": frozenset((
        "kind", "key", "name", "description", "collection", "width", "auto_apply_filters",
        "cache_ttl", "archived", "parameters", "tabs", "dashcards",
    )),
}

CARD_TYPES = ("question", "model", "metric")
DASHBOARD_WIDTHS = ("fixed", "full")
DASHCARD_ALLOWED_KEYS = frozenset((
    "key", "card", "tab", "row", "col", "size_x", "size_y",
    "visualization_settings", "parameter_mappings", "series", "inline_parameters",
))
TAB_ALLOWED_KEYS = frozenset(("key", "name"))


def validate_tree(root_dir: str):
    """Return (tree, problems) — one 'path: message' string per problem."""
    tree, problems = repo.load_tree(root_dir)
    for key, doc in tree.collections.items():
        _check_common(tree, "collections", key, doc, problems)
        _check_collection(tree, key, doc, problems)
    _check_parent_cycles(tree, problems)
    for key, doc in tree.cards.items():
        _check_common(tree, "cards", key, doc, problems)
        _check_card(tree, key, doc, problems)
    for key, doc in tree.dashboards.items():
        _check_common(tree, "dashboards", key, doc, problems)
        _check_dashboard(tree, key, doc, problems)
    return tree, problems


def _path(tree, section, key):
    return tree.paths.get((section, key), f"{section}/{key}.yaml")


def _check_common(tree, section, key, doc, problems):
    path = _path(tree, section, key)
    if not KEY_RE.match(key or ""):
        problems.append(f"{path}: key {key!r} is not a slug (^[a-z0-9][a-z0-9-]*$)")
    forbidden = sorted(FORBIDDEN_KEYS & doc.keys())
    if forbidden:
        problems.append(
            f"{path}: server-side keys must be removed: {', '.join(forbidden)}")
    unknown = sorted(doc.keys() - ALLOWED_KEYS[section] - FORBIDDEN_KEYS)
    if unknown:
        problems.append(
            f"{path}: unknown top-level keys (they would be ignored, remove them): "
            f"{', '.join(unknown)}")
    if not doc.get("name"):
        problems.append(f"{path}: 'name' is required")


def _check_collection_ref(tree, path, doc, problems):
    ref = doc.get("collection")
    if ref in (None, "root") or ref in tree.collections:
        return
    problems.append(f"{path}: collection {ref!r} does not resolve")


def _check_collection(tree, key, doc, problems):
    path = _path(tree, "collections", key)
    parent = doc.get("parent")
    if parent not in (None, "root") and parent not in tree.collections:
        problems.append(f"{path}: parent {parent!r} does not resolve")


def _check_parent_cycles(tree, problems):
    for key in tree.collections:
        seen = set()
        cursor = key
        while cursor not in (None, "root"):
            if cursor in seen:
                problems.append(
                    f"{_path(tree, 'collections', key)}: parent cycle involving {cursor!r}")
                break
            seen.add(cursor)
            doc = tree.collections.get(cursor)
            cursor = doc.get("parent") if doc else None


def _check_card(tree, key, doc, problems):
    path = _path(tree, "cards", key)
    _check_collection_ref(tree, path, doc, problems)
    card_type = doc.get("type", "question")
    if card_type not in CARD_TYPES:
        problems.append(f"{path}: type must be one of {CARD_TYPES}, got {card_type!r}")
    if not doc.get("display"):
        problems.append(f"{path}: 'display' is required")
    _check_dataset_query(path, doc.get("dataset_query"), problems)


def _check_dataset_query(path, query, problems):
    if not isinstance(query, dict):
        problems.append(f"{path}: 'dataset_query' must be a mapping")
        return
    if not isinstance(query.get("database"), int):
        problems.append(f"{path}: dataset_query.database must be an integer database id")
    if query.get("lib/type") != "mbql/query" or not isinstance(query.get("stages"), list):
        problems.append(
            f"{path}: dataset_query must be MBQL 5 (lib/type: mbql/query with a stages list)")


def _check_dashboard(tree, key, doc, problems):
    path = _path(tree, "dashboards", key)
    _check_collection_ref(tree, path, doc, problems)
    width = doc.get("width", "fixed")
    if width not in DASHBOARD_WIDTHS:
        problems.append(f"{path}: width must be one of {DASHBOARD_WIDTHS}, got {width!r}")
    if "dashcards" not in doc:
        problems.append(f"{path}: 'dashcards' is required — an empty list means "
                        "'this dashboard has no cards' and removes every card on apply")
    tab_keys = _check_tabs(path, doc.get("tabs") or [], problems)
    _check_dashcards(tree, path, doc, tab_keys, problems)


def _check_tabs(path, tabs, problems):
    tab_keys = []
    for i, tab in enumerate(tabs):
        if not isinstance(tab, dict):
            problems.append(f"{path}: tabs[{i}] must be a mapping")
            continue
        unknown = sorted(tab.keys() - TAB_ALLOWED_KEYS)
        if unknown:
            problems.append(f"{path}: tabs[{i}] has unknown keys: {', '.join(unknown)}")
        tkey = tab.get("key")
        if not tkey or not KEY_RE.match(str(tkey)):
            problems.append(f"{path}: tabs[{i}] key {tkey!r} is not a slug")
        if tkey in tab_keys:
            problems.append(f"{path}: duplicate tab key {tkey!r}")
        if not tab.get("name"):
            problems.append(f"{path}: tabs[{i}] needs a 'name'")
        tab_keys.append(tkey)
    return tab_keys


def _check_dashcards(tree, path, doc, tab_keys, problems):
    seen = set()
    for i, dc in enumerate(doc.get("dashcards") or []):
        if not isinstance(dc, dict):
            problems.append(f"{path}: dashcards[{i}] must be a mapping")
            continue
        label = f"{path}: dashcards[{i}]"
        unknown = sorted(dc.keys() - DASHCARD_ALLOWED_KEYS)
        if unknown:
            problems.append(f"{label} has unknown keys (remove them): {', '.join(unknown)}")
        dkey = dc.get("key")
        if not dkey or not KEY_RE.match(str(dkey)):
            problems.append(f"{label} key {dkey!r} is not a slug")
        if dkey in seen:
            problems.append(f"{label} duplicate dashcard key {dkey!r}")
        seen.add(dkey)
        _check_dashcard_refs(tree, label, dc, tab_keys, problems)
        _check_dashcard_grid(label, dc, problems)


def _check_dashcard_refs(tree, label, dc, tab_keys, problems):
    card = dc.get("card")
    if card is not None and card not in tree.cards:
        problems.append(f"{label} card {card!r} does not resolve")
    virtual = isinstance(dc.get("visualization_settings"), dict) \
        and "virtual_card" in dc["visualization_settings"]
    if card is None and not virtual:
        problems.append(f"{label} needs either 'card:' or a virtual_card in visualization_settings")
    if card is not None and virtual:
        problems.append(f"{label} has both 'card:' and a virtual_card — pick one")
    tab = dc.get("tab")
    if tab is not None and tab not in tab_keys:
        problems.append(f"{label} tab {tab!r} does not resolve")
    if tab_keys and tab is None:
        problems.append(f"{label} must declare 'tab:' (dashboard declares {len(tab_keys)} tab(s);"
                        " every dashcard needs one)")
    for skey in dc.get("series") or []:
        if skey not in tree.cards:
            problems.append(f"{label} series card {skey!r} does not resolve")


def _check_dashcard_grid(label, dc, problems):
    bounds = (("row", 0), ("col", 0), ("size_x", 1), ("size_y", 1))
    for field, minimum in bounds:
        value = dc.get(field)
        if not isinstance(value, int) or value < minimum:
            problems.append(f"{label} {field} must be an integer >= {minimum}, got {value!r}")
            return
    if dc["col"] + dc["size_x"] > 24:
        problems.append(f"{label} col + size_x = {dc['col'] + dc['size_x']} exceeds the 24-column grid")
