"""Canonicalisation and strip rules used by the diff engine.

Everything stripped here is server-generated noise; both the desired (YAML-derived)
and the live (GET-derived) sides pass through the same functions before comparison.
"""
from __future__ import annotations

import json

LIB_UUID = "lib/uuid"

STRIP_COMMON = frozenset((
    "id", "entity_id", "created_at", "updated_at", "creator_id", "creator",
    "made_public_by_id", "public_uuid", "last-edit-info", "can_write", "can_restore",
    "can_delete", "can_run_adhoc_query", "can_manage_db", "can_set_cache_policy",
    "is_remote_synced", "moderation_reviews", "collection", "collection_authority_level",
    "archived_directly", "archive_operation_id", "collection_position", "view_count",
    "last_viewed_at", "last_used_at", "initially_published_at", "workspace_id", "position",
))

STRIP_CARD = frozenset((
    "result_metadata", "metabase_version", "card_schema", "query_type", "database_id",
    "table_id", "dashboard_count", "dashboard", "dashboard_id", "parameter_usage_count",
    "average_query_time", "last_query_start", "based_on_upload", "param_fields",
    "persisted", "legacy_query", "dimensions", "dimension_mappings", "document_id",
    "cache_invalidated_at", "collection_preview", "embedding_type", "embedding_params",
    "enable_embedding", "caveats", "points_of_interest", "show_in_getting_started",
))

STRIP_DASHBOARD = frozenset((
    "param_fields", "last_used_param_values", "enable_embedding", "caveats",
    "points_of_interest", "show_in_getting_started",
))

STRIP_COLLECTION = frozenset((
    "slug", "location", "effective_location", "effective_ancestors", "personal_owner_id",
    "is_personal", "is_sample", "namespace", "type", "authority_level", "parent_id",
))

# Keys where absent, null and {}/[] all mean "nothing".
EMPTYISH_KEYS = ("visualization_settings", "parameters", "parameter_mappings",
                 "inline_parameters", "series")

# The only dashcard fields the tool manages (matches the write shape minus id).
DASHCARD_FIELDS = ("card_id", "dashboard_tab_id", "row", "col", "size_x", "size_y",
                   "visualization_settings", "parameter_mappings", "series",
                   "inline_parameters")


def _strip(obj: dict, extra=frozenset(), prefixes=()) -> dict:
    return {
        key: value for key, value in obj.items()
        if key not in STRIP_COMMON and key not in extra
        and not any(key.startswith(p) for p in prefixes)
    }


def strip_lib_uuid(value):
    if isinstance(value, dict):
        return {k: strip_lib_uuid(v) for k, v in value.items() if k != LIB_UUID}
    if isinstance(value, list):
        return [strip_lib_uuid(v) for v in value]
    return value


def _canon_column_settings_key(key):
    if not isinstance(key, str):
        return key
    try:
        return json.dumps(json.loads(key), sort_keys=True, separators=(",", ":"))
    except ValueError:
        return key


def canon_viz(settings):
    if not isinstance(settings, dict):
        return settings
    out = dict(settings)
    column_settings = out.get("column_settings")
    if isinstance(column_settings, dict):
        out["column_settings"] = {
            _canon_column_settings_key(k): v for k, v in column_settings.items()
        }
    return out


def _drop_empty(obj: dict) -> dict:
    for key in EMPTYISH_KEYS:
        if key in obj and obj[key] in (None, {}, []):
            del obj[key]
    return obj


def normalize_collection(obj: dict) -> dict:
    parent_id = obj.get("parent_id")
    if parent_id is None:
        parent_id = _parent_from_location(obj.get("location"))
    out = _strip(obj, STRIP_COLLECTION, prefixes=("can_",))
    out["parent_id"] = parent_id
    out.setdefault("description", None)
    out.setdefault("archived", False)
    return out


def _parent_from_location(location):
    if not location:
        return None
    parts = [p for p in str(location).split("/") if p]
    return int(parts[-1]) if parts else None


def normalize_card(obj: dict) -> dict:
    card = _strip(obj, STRIP_CARD)
    if card.get("source_card_id") is None:
        card.pop("source_card_id", None)
    if "dataset_query" in card:
        card["dataset_query"] = strip_lib_uuid(card["dataset_query"])
    if "visualization_settings" in card:
        card["visualization_settings"] = canon_viz(card["visualization_settings"])
    card.setdefault("type", "question")
    card.setdefault("archived", False)
    card.setdefault("description", None)
    card.setdefault("cache_ttl", None)
    if card["archived"]:
        # Metabase forces an archived card's collection_id to wherever archiving
        # parked it and ignores collection_id in the same/later PUT (verified against
        # the live instance: PUT echoes the requested value but a follow-up GET shows
        # it reverted) -- so this field isn't ours to manage once archived.
        card.pop("collection_id", None)
    return _drop_empty(card)


def normalize_dashcard(dc: dict) -> dict:
    out = {key: dc[key] for key in DASHCARD_FIELDS if key in dc}
    out.setdefault("card_id", None)
    out.setdefault("dashboard_tab_id", None)
    series = out.get("series")
    if series:
        out["series"] = sorted(
            (item["id"] if isinstance(item, dict) else item for item in series), key=repr)
    if "visualization_settings" in out:
        out["visualization_settings"] = canon_viz(out["visualization_settings"])
    return _drop_empty(out)


def normalize_tab(tab: dict) -> dict:
    return {"id": tab.get("id"), "name": tab.get("name")}


def normalize_dashboard(obj: dict, dashcard_key_fn) -> dict:
    """dashcard_key_fn(dc_dict, index) -> logical key for the dashcard."""
    dash = _strip(obj, STRIP_DASHBOARD, prefixes=("embedding_",))
    dash.pop("dashcards", None)
    dash.pop("tabs", None)
    tabs = [normalize_tab(t) for t in obj.get("tabs") or []]
    if tabs:
        dash["tabs"] = tabs
    dashcards = {}
    for index, dc in enumerate(obj.get("dashcards") or []):
        dashcards[dashcard_key_fn(dc, index)] = normalize_dashcard(dc)
    dash["dashcards"] = dashcards
    dash.setdefault("description", None)
    dash.setdefault("cache_ttl", None)
    dash.setdefault("archived", False)
    dash.setdefault("collection_id", None)
    return _drop_empty(dash)


def fallback_dashcard_key(dc: dict) -> str:
    return f"@{dc.get('card_id')}:{dc.get('row')}:{dc.get('col')}"
