"""YAML <-> API payload conversion: key slugs and ref-sugar resolution."""
from __future__ import annotations

import re

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

RU_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
}

DOC_ONLY_KEYS = ("kind", "key", "collection")


def slugify(name: str) -> str:
    chars = []
    for ch in str(name).lower():
        if ch in RU_LAT:
            chars.append(RU_LAT[ch])
            continue
        if ch.isascii() and ch.isalnum():
            chars.append(ch)
            continue
        chars.append("-")
    slug = re.sub("-+", "-", "".join(chars)).strip("-")
    return slug or "item"


def unique_slug(name: str, taken) -> str:
    base = slugify(name)
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


class Resolver:
    """Resolve logical keys to instance ids via the state file.

    Unknown refs (entities not created yet) resolve to '<new:kind:key>' placeholders;
    they only ever appear in diff/dry-run output, never in a real HTTP body.
    """

    def __init__(self, state):
        self.state = state

    def collection_id(self, key):
        if key in (None, "root"):
            return None
        return self.state.collection_id(key) or f"<new:collection:{key}>"

    def card_id(self, key):
        return self.state.card_id(key) or f"<new:card:{key}>"

    def tab_id(self, dashboard_key, tab_key):
        tabs = self.state.dashboard_entry(dashboard_key)["tabs"]
        return tabs.get(tab_key) or f"<new:tab:{tab_key}>"


def desired_collection(doc: dict, resolver: Resolver) -> dict:
    return {
        "name": doc.get("name"),
        "description": doc.get("description"),
        "parent_id": resolver.collection_id(doc.get("parent")),
        "archived": bool(doc.get("archived", False)),
    }


def desired_card(doc: dict, resolver: Resolver) -> dict:
    payload = {k: v for k, v in doc.items() if k not in DOC_ONLY_KEYS}
    payload["collection_id"] = resolver.collection_id(doc.get("collection"))
    payload.setdefault("type", "question")
    payload.setdefault("archived", False)
    return payload


def desired_dashboard_scalars(doc: dict, resolver: Resolver) -> dict:
    skip = DOC_ONLY_KEYS + ("tabs", "dashcards")
    payload = {k: v for k, v in doc.items() if k not in skip}
    payload["collection_id"] = resolver.collection_id(doc.get("collection"))
    payload.setdefault("archived", False)
    return payload


def desired_tabs(doc: dict, resolver: Resolver) -> list:
    """[(tab_key, {'id': real-or-placeholder, 'name': ...}), ...] in YAML order."""
    return [
        (tab["key"], {"id": resolver.tab_id(doc["key"], tab["key"]), "name": tab.get("name")})
        for tab in doc.get("tabs") or []
    ]


def desired_dashcards(doc: dict, resolver: Resolver) -> list:
    """[(dashcard_key, write-shape dict without 'id'), ...] in YAML order."""
    out = []
    for dc in doc.get("dashcards") or []:
        card_id = resolver.card_id(dc["card"]) if "card" in dc else None
        tab_id = resolver.tab_id(doc["key"], dc["tab"]) if "tab" in dc else None
        out.append((dc["key"], {
            "card_id": card_id,
            "dashboard_tab_id": tab_id,
            "row": dc.get("row"),
            "col": dc.get("col"),
            "size_x": dc.get("size_x"),
            "size_y": dc.get("size_y"),
            "visualization_settings": dc.get("visualization_settings") or {},
            "parameter_mappings": dc.get("parameter_mappings") or [],
            "series": [{"id": resolver.card_id(k)} for k in dc.get("series") or []],
            "inline_parameters": dc.get("inline_parameters") or [],
        }))
    return out
