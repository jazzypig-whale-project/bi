"""Execute the plan: create/update collections, cards, dashboards. Never DELETE."""
from __future__ import annotations

import json
import sys

from . import model
from .model import Resolver


class ApplyError(Exception):
    pass


class StrictLookup:
    """Like Resolver, but refuses to emit placeholder ids into real HTTP bodies."""

    def __init__(self, state):
        self.state = state

    def collection_id(self, key):
        if key in (None, "root"):
            return None
        cid = self.state.collection_id(key)
        if cid is None:
            raise ApplyError(f"collection {key!r} has no instance id yet")
        return cid

    def card_id(self, key):
        cid = self.state.card_id(key)
        if cid is None:
            raise ApplyError(f"card {key!r} has no instance id yet")
        return cid


def run_apply(tree, state, client, plan, yes: bool, dry_run: bool) -> int:
    ops = _ordered_ops(plan)
    if dry_run:
        _print_dry_run(tree, state, ops)
        return 0
    if not _confirmed(yes, state.data.get("instance"), len(ops)):
        print("aborted: confirmation required (use --yes or answer y)")
        return 1
    executors = {
        ("collections", "create"): _create_collection,
        ("collections", "update"): _update_collection,
        ("cards", "create"): _create_card,
        ("cards", "update"): _update_card,
        ("dashboards", "create"): _create_dashboard,
        ("dashboards", "update"): _update_dashboard,
    }
    for section, verb, key in ops:
        print(f"{verb} {section[:-1]} {key} ...")
        executors[(section, verb)](tree, state, client, key)
    print(f"applied {len(ops)} change(s); state saved to {state.path}")
    return 0


def _ordered_ops(plan):
    """collections (parents first, creates before updates) -> cards -> dashboards."""
    ops = []
    for section in ("collections", "cards", "dashboards"):
        for verb, bucket in (("create", plan["creates"]), ("update", plan["updates"])):
            ops.extend((section, verb, item["key"]) for item in bucket
                       if item["section"] == section)
    return ops


def _confirmed(yes, instance, count) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        return False
    answer = input(f"Apply {count} change(s) to {instance}? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


# --- collections ------------------------------------------------------------
def _collection_put_body(doc, lookup):
    return {
        "name": doc.get("name"),
        "description": doc.get("description"),
        "parent_id": lookup.collection_id(doc.get("parent")),
        # explicit always: the server defaults archived to false on PUT
        "archived": bool(doc.get("archived", False)),
    }


def _create_collection(tree, state, client, key):
    doc = tree.collections[key]
    lookup = StrictLookup(state)
    body = {"name": doc.get("name"), "description": doc.get("description"),
            "parent_id": lookup.collection_id(doc.get("parent"))}
    created = client.post("/api/collection", body)
    state.set_entity("collections", key, created)
    state.save()
    if doc.get("archived"):
        client.put(f"/api/collection/{created['id']}", _collection_put_body(doc, lookup))


def _update_collection(tree, state, client, key):
    doc = tree.collections[key]
    body = _collection_put_body(doc, StrictLookup(state))
    client.put(f"/api/collection/{state.collection_id(key)}", body)


# --- explicit nulls ---------------------------------------------------------
# PUT /api/card/:id and PUT /api/dashboard/:id are partial updates: an omitted key
# leaves the server value untouched. Fields in the endpoint's `:present` set are
# cleared by an explicit null; fields in its `:non-nil` set ignore nulls, so a null
# there is a silent no-op and must never be sent. CLEARABLE lists the `:present`
# fields the normalized desired shape always defines (normalize.py setdefaults them),
# so the body carries exactly what diff compared.
CARD_CLEARABLE = ("description", "cache_ttl")
CARD_NON_NIL = frozenset((
    "dataset_query", "display", "name", "visualization_settings", "archived", "type",
    "parameters", "parameter_mappings",
))
DASHBOARD_CLEARABLE = ("description", "cache_ttl")
DASHBOARD_NON_NIL = frozenset((
    "name", "parameters", "archived", "auto_apply_filters", "enable_embedding",
))


def _with_explicit_nulls(body, clearable, non_nil):
    for field in clearable:
        body.setdefault(field, None)
    return {k: v for k, v in body.items() if v is not None or k not in non_nil}


# --- cards ------------------------------------------------------------------
def _card_body(doc, lookup):
    return _with_explicit_nulls(model.desired_card(doc, lookup), CARD_CLEARABLE,
                                CARD_NON_NIL)


def _card_post_body(doc, lookup):
    """POST /api/card body: the server rejects `archived` on create."""
    body = _card_body(doc, lookup)
    body.pop("archived", None)
    return body


def _create_card(tree, state, client, key):
    doc = tree.cards[key]
    body = _card_post_body(doc, StrictLookup(state))
    created = client.post("/api/card", body)
    state.set_entity("cards", key, created)
    state.save()
    if doc.get("archived"):
        client.put(f"/api/card/{created['id']}", {"archived": True})


def _update_card(tree, state, client, key):
    doc = tree.cards[key]
    body = _card_body(doc, StrictLookup(state))
    client.put(f"/api/card/{state.card_id(key)}", body)


# --- dashboards -------------------------------------------------------------
def _dashboard_post_body(doc, lookup):
    return {
        "name": doc.get("name"),
        "description": doc.get("description"),
        "collection_id": lookup.collection_id(doc.get("collection")),
        "cache_ttl": doc.get("cache_ttl"),
        "parameters": doc.get("parameters") or [],
    }


def _dashboard_put_body(doc, entry, lookup):
    """Full-body PUT: scalars + tabs + dashcards (full-set replacement, negative temp ids).

    Returns (body, sent_tabs, sent_dashcards) where the two `sent_*` lists carry
    (logical key, what was written) in YAML order for the id-recording step.
    """
    body = _with_explicit_nulls(model.desired_dashboard_scalars(doc, lookup),
                                DASHBOARD_CLEARABLE, DASHBOARD_NON_NIL)
    counter = iter(range(-1, -10_000, -1))
    sent_tabs, tab_temp_ids = [], {}
    tabs = []
    for tab in doc.get("tabs") or []:
        tab_id = entry.get("tabs", {}).get(tab["key"]) or next(counter)
        tab_temp_ids[tab["key"]] = tab_id
        sent_tabs.append((tab["key"], tab_id))
        tabs.append({"id": tab_id, "name": tab.get("name")})
    if tabs or entry.get("tabs"):
        body["tabs"] = tabs
    dashcards, sent_dashcards = [], []
    for dc in doc.get("dashcards") or []:
        dc_id = entry.get("dashcards", {}).get(dc["key"]) or next(counter)
        written = {
            "id": dc_id,
            "card_id": lookup.card_id(dc["card"]) if "card" in dc else None,
            "dashboard_tab_id": tab_temp_ids.get(dc.get("tab")),
            "row": dc.get("row"), "col": dc.get("col"),
            "size_x": dc.get("size_x"), "size_y": dc.get("size_y"),
            "visualization_settings": dc.get("visualization_settings") or {},
            "parameter_mappings": dc.get("parameter_mappings") or [],
            "series": [{"id": lookup.card_id(k)} for k in dc.get("series") or []],
            "inline_parameters": dc.get("inline_parameters") or [],
        }
        dashcards.append(written)
        sent_dashcards.append((dc["key"], written))
    body["dashcards"] = dashcards
    return body, sent_tabs, sent_dashcards


def _create_dashboard(tree, state, client, key):
    doc = tree.dashboards[key]
    lookup = StrictLookup(state)
    created = client.post("/api/dashboard", _dashboard_post_body(doc, lookup))
    state.set_entity("dashboards", key, created)
    state.save()
    _put_dashboard(state, client, key, doc, lookup)


def _update_dashboard(tree, state, client, key):
    _put_dashboard(state, client, key, tree.dashboards[key], StrictLookup(state))


def _put_dashboard(state, client, key, doc, lookup):
    entry = state.dashboard_entry(key)
    dashboard_id = state.entity_id_of("dashboards", key)
    _heal_stale_ids(entry, client, dashboard_id)
    body, sent_tabs, sent_dashcards = _dashboard_put_body(doc, entry, lookup)
    resp = client.put(f"/api/dashboard/{dashboard_id}", body)
    _record_tabs(entry, sent_tabs, resp)
    _record_dashcards(entry, sent_dashcards, resp)
    state.save()


def _heal_stale_ids(entry, client, dashboard_id):
    """Drop state-claimed tab/dashcard ids the live dashboard no longer has.

    A positive id the server does not know is treated as "create" by the PUT, so the
    fresh row would be orphaned from its key and deleted-and-recreated on every later
    run. Dropping the claim makes _dashboard_put_body allocate a negative temp id.
    """
    if not (entry["tabs"] or entry["dashcards"]):
        return
    live = client.get_or_none(f"/api/dashboard/{dashboard_id}")
    if not live:
        return
    for field in ("tabs", "dashcards"):
        live_ids = {item.get("id") for item in live.get(field) or []}
        entry[field] = {k: v for k, v in entry[field].items() if v in live_ids}


def _record_tabs(entry, sent_tabs, resp):
    """Record only ids the PUT response actually returned; re-match the rest by position."""
    resp_tabs = sorted(resp.get("tabs") or [], key=lambda t: t.get("position", 0))
    resp_ids = {tab.get("id") for tab in resp_tabs}
    new_map = {key: tab_id for key, tab_id in sent_tabs
               if tab_id > 0 and tab_id in resp_ids}
    pool = [tab for tab in resp_tabs if tab.get("id") not in set(new_map.values())]
    pending = [key for key, _ in sent_tabs if key not in new_map]
    new_map.update({key: tab["id"] for key, tab in zip(pending, pool)})
    entry["tabs"] = new_map


def _record_dashcards(entry, sent_dashcards, resp):
    """Record only ids the PUT response actually returned; re-match the rest by signature.

    Keeping a sent id the response omits is what made stale ids immortal: the server
    had silently created a different row, and the next run would delete it again.
    """
    resp_dcs = resp.get("dashcards") or []
    resp_ids = {dc.get("id") for dc in resp_dcs}
    new_map = {key: written["id"] for key, written in sent_dashcards
               if written["id"] > 0 and written["id"] in resp_ids}
    pool = [dc for dc in resp_dcs if dc.get("id") not in set(new_map.values())]
    pending = [(key, w) for key, w in sent_dashcards if key not in new_map]
    leftover = []
    for dc_key, written in pending:
        match = _match_dashcard(pool, written)
        if match is None:
            leftover.append(dc_key)
            continue
        pool.remove(match)
        new_map[dc_key] = match["id"]
    # One unclaimed key and one unclaimed row: the pairing is forced, not a guess.
    if len(leftover) == 1 and len(pool) == 1:
        new_map[leftover[0]] = pool[0]["id"]
    entry["dashcards"] = new_map


def _dashcard_signature(dc):
    return tuple(dc.get(f) for f in ("card_id", "row", "col", "size_x", "size_y"))


def _match_dashcard(pool, written):
    """Exactly one signature match, or nothing: guessing cross-wires key -> id, and
    every later apply would then rewrite the wrong dashcard."""
    signature = _dashcard_signature(written)
    matches = [dc for dc in pool if _dashcard_signature(dc) == signature]
    return matches[0] if len(matches) == 1 else None


# --- dry-run ---------------------------------------------------------------
def _print_dry_run(tree, state, ops):
    print("dry-run: the following HTTP calls would be made (ids <new:...> are")
    print("placeholders resolved at execution time, after their targets are created):")
    resolver = Resolver(state)
    builders = {
        ("collections", "create"): lambda doc, key: [
            ("POST", "/api/collection",
             {"name": doc.get("name"), "description": doc.get("description"),
              "parent_id": resolver.collection_id(doc.get("parent"))})],
        ("collections", "update"): lambda doc, key: [
            ("PUT", f"/api/collection/{state.collection_id(key)}",
             _collection_put_body(doc, resolver))],
        ("cards", "create"): lambda doc, key: [
            ("POST", "/api/card", _card_post_body(doc, resolver))],
        ("cards", "update"): lambda doc, key: [
            ("PUT", f"/api/card/{state.card_id(key)}", _card_body(doc, resolver))],
        ("dashboards", "create"): lambda doc, key: [
            ("POST", "/api/dashboard", _dashboard_post_body(doc, resolver)),
            ("PUT", "/api/dashboard/<new>",
             _dashboard_put_body(doc, {}, resolver)[0])],
        ("dashboards", "update"): lambda doc, key: [
            ("PUT", f"/api/dashboard/{state.entity_id_of('dashboards', key)}",
             _dashboard_put_body(doc, state.dashboard_entry(key), resolver)[0])],
    }
    for section, verb, key in ops:
        doc = tree.section(section)[key]
        for method, path, body in builders[(section, verb)](doc, key):
            print(f"{method} {path}")
            print(f"  {json.dumps(body, ensure_ascii=False)}")
