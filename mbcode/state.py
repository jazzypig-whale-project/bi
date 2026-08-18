"""Read/write .state/<host>.yaml — the mapping between logical keys and instance ids."""
from __future__ import annotations

import os

import yaml


def _empty(instance: str) -> dict:
    return {
        "version": 1,
        "instance": instance,
        "metabase_version": None,
        "collections": {},
        "cards": {},
        "dashboards": {},
    }


class State:
    def __init__(self, path: str, data: dict):
        self.path = path
        self.data = data

    @classmethod
    def load(cls, root_dir: str, host_slug: str, instance: str) -> "State":
        path = os.path.join(root_dir, ".state", f"{host_slug}.yaml")
        if not os.path.isfile(path):
            return cls(path, _empty(instance))
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        base = _empty(instance)
        base.update(data)
        base["instance"] = instance
        for section in ("collections", "cards", "dashboards"):
            base[section] = base.get(section) or {}
        return cls(path, base)

    def save(self) -> None:
        """Write via a temp file + os.replace: a crash mid-dump must not truncate
        the identity store, which is the only link between keys and instance ids."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(self.data, handle, allow_unicode=True, sort_keys=False,
                           default_flow_style=False, indent=2)
        os.replace(tmp_path, self.path)

    # --- lookups -------------------------------------------------------
    def entity_id_of(self, section: str, key: str):
        entry = self.data[section].get(key)
        return entry.get("id") if entry else None

    def collection_id(self, key: str):
        return self.entity_id_of("collections", key)

    def card_id(self, key: str):
        return self.entity_id_of("cards", key)

    def dashboard_entry(self, key: str) -> dict:
        entry = self.data["dashboards"].setdefault(key, {})
        # `or {}` and not setdefault: a hand-edited `tabs:` with no value parses as
        # None, and setdefault would keep it and blow up on the first .get().
        entry["tabs"] = entry.get("tabs") or {}
        entry["dashcards"] = entry.get("dashcards") or {}
        return entry

    def reverse_map(self, section: str) -> dict:
        """instance id -> logical key."""
        return {entry["id"]: key for key, entry in self.data[section].items() if "id" in entry}

    # --- updates -------------------------------------------------------
    def set_entity(self, section: str, key: str, entity: dict) -> None:
        entry = self.data[section].setdefault(key, {})
        entry["id"] = entity["id"]
        entry["entity_id"] = entity.get("entity_id")
