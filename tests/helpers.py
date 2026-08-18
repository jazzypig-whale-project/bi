"""Test doubles and small document builders shared across the offline test suite."""
from __future__ import annotations

import os

import yaml


class FakeClient:
    """A drop-in stand-in for mbcode.client.Client that never touches the network.

    `gets`/`posts`/`puts` map a path to either a canned response or a callable that
    receives the request body (posts/puts) and returns a response. Every call is
    recorded in `.calls` as (method, path, body) so tests can assert on the exact
    request sequence a code path issues.
    """

    def __init__(self, gets=None, posts=None, puts=None):
        self.calls = []
        self._gets = dict(gets or {})
        self._posts = dict(posts or {})
        self._puts = dict(puts or {})

    def get(self, path):
        self.calls.append(("GET", path, None))
        if path not in self._gets:
            raise KeyError(f"FakeClient: no GET response configured for {path!r}")
        value = self._gets[path]
        return value(path) if callable(value) else value

    def get_or_none(self, path):
        self.calls.append(("GET", path, None))
        value = self._gets.get(path)
        return value(path) if callable(value) else value

    def post(self, path, body):
        self.calls.append(("POST", path, body))
        if path not in self._posts:
            raise KeyError(f"FakeClient: no POST response configured for {path!r}")
        handler = self._posts[path]
        return handler(body) if callable(handler) else handler

    def put(self, path, body):
        self.calls.append(("PUT", path, body))
        if path not in self._puts:
            raise KeyError(f"FakeClient: no PUT response configured for {path!r}")
        handler = self._puts[path]
        return handler(body) if callable(handler) else handler


def write_doc(root, section, key, doc) -> str:
    """Write a YAML doc into <root>/<section>/<key>.yaml (section: collections/cards/dashboards)."""
    path = os.path.join(str(root), section, f"{key}.yaml")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(doc, handle, allow_unicode=True, sort_keys=False)
    return path


def minimal_collection(**overrides) -> dict:
    doc = {
        "kind": "collection", "key": "sales", "name": "Sales", "description": None,
        "parent": "root", "archived": False,
    }
    doc.update(overrides)
    return doc


def minimal_card(**overrides) -> dict:
    doc = {
        "kind": "card", "key": "daily-revenue", "name": "Daily revenue", "description": None,
        "collection": "root", "type": "question", "display": "table", "cache_ttl": None,
        "archived": False,
        "dataset_query": {
            "lib/type": "mbql/query", "database": 2,
            "stages": [{"lib/type": "mbql.stage/mbql", "source-table": 1}],
        },
        "visualization_settings": {}, "parameters": [],
    }
    doc.update(overrides)
    return doc


def minimal_dashcard(**overrides) -> dict:
    dc = {
        "key": "chart", "card": "daily-revenue", "row": 0, "col": 0, "size_x": 12, "size_y": 6,
        "visualization_settings": {}, "parameter_mappings": [], "series": [], "inline_parameters": [],
    }
    dc.update(overrides)
    return dc


def minimal_dashboard(**overrides) -> dict:
    doc = {
        "kind": "dashboard", "key": "overview", "name": "Overview", "description": None,
        "collection": "root", "width": "fixed", "auto_apply_filters": True, "cache_ttl": None,
        "archived": False, "parameters": [],
        "dashcards": [minimal_dashcard()],
    }
    doc.update(overrides)
    return doc
