"""apply.py: payload building, with the HTTP client stubbed via FakeClient."""
from __future__ import annotations

import pytest

from helpers import FakeClient, minimal_card, minimal_dashboard, minimal_dashcard
from mbcode import apply as apply_mod
from mbcode.client import ApiError
from mbcode import repo
from mbcode.diff import _collections_parent_first
from mbcode.state import State
from mbcode.validate import FORBIDDEN_KEYS

LIVE_ONLY_DASHCARD_KEYS = {
    "entity_id", "action_id", "collection_authority_level", "created_at",
    "updated_at", "dashboard_id", "card", "collection_id",
}


def _state(**sections):
    data = {"collections": {}, "cards": {}, "dashboards": {}}
    data.update(sections)
    return State("unused", data)


# --- new dashboard: two-phase create (POST scalars, then PUT tabs+dashcards) ---

def test_create_dashboard_is_a_two_phase_post_then_put(tmp_path):
    doc = minimal_dashboard(
        key="overview", collection="root",
        tabs=[{"key": "t1", "name": "Tab 1"}],
        dashcards=[{"key": "chart", "card": "daily-revenue", "tab": "t1",
                    "row": 0, "col": 0, "size_x": 12, "size_y": 6,
                    "visualization_settings": {}, "parameter_mappings": [],
                    "series": [], "inline_parameters": []}],
    )
    tree = repo.Tree(root=str(tmp_path), dashboards={"overview": doc},
                      cards={"daily-revenue": minimal_card()})
    state = State(str(tmp_path / ".state" / "test.yaml"), {
        "version": 1, "instance": "http://x", "metabase_version": None,
        "collections": {}, "cards": {"daily-revenue": {"id": 7, "entity_id": "c7"}},
        "dashboards": {},
    })

    def post_dashboard(body):
        assert "dashcards" not in body
        assert "tabs" not in body
        assert body["name"] == "Overview"
        return {"id": 100, "entity_id": "dash-100"}

    def put_dashboard(body):
        assert "dashcards" in body
        assert "tabs" in body
        tabs = [{"id": 500, "name": t["name"]} for t in body["tabs"]]
        dashcards = [{**dc, "id": 900} for dc in body["dashcards"]]
        return {"tabs": tabs, "dashcards": dashcards}

    client = FakeClient(posts={"/api/dashboard": post_dashboard},
                        puts={"/api/dashboard/100": put_dashboard})
    plan = {"creates": [{"section": "dashboards", "key": "overview"}], "updates": [], "orphans": []}

    rc = apply_mod.run_apply(tree, state, client, plan, yes=True, dry_run=False)

    assert rc == 0
    assert [(m, p) for m, p, _ in client.calls] == [
        ("POST", "/api/dashboard"), ("PUT", "/api/dashboard/100"),
    ]


# --- new dashcards get negative ids, existing ones keep their real positive ids

def test_new_dashcards_get_negative_ids_existing_keep_real_ids(tmp_path):
    doc = minimal_dashboard(
        key="overview", collection="root",
        dashcards=[
            {"key": "existing", "card": "daily-revenue",
             "row": 0, "col": 0, "size_x": 12, "size_y": 6,
             "visualization_settings": {}, "parameter_mappings": [],
             "series": [], "inline_parameters": []},
            {"key": "brand-new", "card": "daily-revenue",
             "row": 6, "col": 0, "size_x": 12, "size_y": 6,
             "visualization_settings": {}, "parameter_mappings": [],
             "series": [], "inline_parameters": []},
        ],
    )
    tree = repo.Tree(root=".", dashboards={"overview": doc}, cards={"daily-revenue": minimal_card()})
    state = State(str(tmp_path / ".state" / "test.yaml"), {
        "collections": {}, "cards": {"daily-revenue": {"id": 7}},
        "dashboards": {"overview": {"id": 100, "entity_id": "e100",
                                    "tabs": {}, "dashcards": {"existing": 55}}},
    })

    captured = {}

    def put_dashboard(body):
        captured["body"] = body
        return {"tabs": [], "dashcards": []}

    client = FakeClient(puts={"/api/dashboard/100": put_dashboard})
    apply_mod._update_dashboard(tree, state, client, "overview")

    written_existing = next(dc for dc in captured["body"]["dashcards"] if dc["id"] == 55)
    written_new = next(dc for dc in captured["body"]["dashcards"] if dc["id"] != 55)
    assert written_existing["id"] == 55
    assert written_new["id"] < 0


# --- no forbidden / live-only key ever appears in a create-dashcard body ------

def test_create_dashcard_body_never_contains_forbidden_or_live_only_keys(tmp_path):
    doc = minimal_dashboard(
        key="overview", collection="root",
        dashcards=[{"key": "chart", "card": "daily-revenue",
                    "row": 0, "col": 0, "size_x": 12, "size_y": 6,
                    "visualization_settings": {}, "parameter_mappings": [],
                    "series": [], "inline_parameters": []}],
    )
    tree = repo.Tree(root=".", dashboards={"overview": doc}, cards={"daily-revenue": minimal_card()})
    state = State(str(tmp_path / ".state" / "test.yaml"), {
        "collections": {}, "cards": {"daily-revenue": {"id": 7}},
        "dashboards": {"overview": {"id": 100, "entity_id": "e100",
                                    "tabs": {}, "dashcards": {}}},
    })

    captured = {}

    def put_dashboard(body):
        captured["body"] = body
        return {"tabs": [], "dashcards": []}

    client = FakeClient(puts={"/api/dashboard/100": put_dashboard})
    apply_mod._update_dashboard(tree, state, client, "overview")

    # 'id' is a legitimate part of the wire shape here (negative temp id for a new
    # dashcard, or the real id for an existing one) — every other forbidden/server-owned
    # or live-only key must never appear.
    for dc in captured["body"]["dashcards"]:
        assert not ((FORBIDDEN_KEYS - {"id"}) & dc.keys())
        assert not (LIVE_ONLY_DASHCARD_KEYS & dc.keys())
        assert set(dc.keys()) == {
            "id", "card_id", "dashboard_tab_id", "row", "col", "size_x", "size_y",
            "visualization_settings", "parameter_mappings", "series", "inline_parameters",
        }


# --- collection PUT always carries an explicit `archived` ---------------------

def test_collection_put_body_always_carries_explicit_archived():
    state = _state(collections={"parent": {"id": 3}})
    lookup = apply_mod.StrictLookup(state)
    doc = {"name": "Sales", "description": None, "parent": "parent"}  # no 'archived' key
    body = apply_mod._collection_put_body(doc, lookup)
    assert body["archived"] is False


def test_collection_put_body_respects_explicit_archived_true():
    state = _state(collections={"parent": {"id": 3}})
    lookup = apply_mod.StrictLookup(state)
    doc = {"name": "Sales", "description": None, "parent": "parent", "archived": True}
    body = apply_mod._collection_put_body(doc, lookup)
    assert body["archived"] is True


# --- ordering: collections parent-first, then cards, then dashboards ----------

def test_collections_parent_first_ordering():
    # Insertion order deliberately scrambled: child listed before its ancestors.
    tree = repo.Tree(root=".", collections={
        "child": {"parent": "mid"}, "mid": {"parent": "grandparent"},
        "grandparent": {"parent": "root"},
    })
    ordered = _collections_parent_first(tree)
    assert ordered.index("grandparent") < ordered.index("mid") < ordered.index("child")


def test_ordered_ops_groups_collections_then_cards_then_dashboards_and_creates_before_updates():
    plan = {
        "creates": [
            {"section": "dashboards", "key": "d1"},
            {"section": "collections", "key": "c-parent"},
            {"section": "collections", "key": "c-child"},
            {"section": "cards", "key": "k1"},
        ],
        "updates": [{"section": "collections", "key": "c-parent"}],
    }
    ops = apply_mod._ordered_ops(plan)
    order_index = {"collections": 0, "cards": 1, "dashboards": 2}
    sections_seen = [order_index[o[0]] for o in ops]
    assert sections_seen == sorted(sections_seen)
    coll_ops = [op for op in ops if op[0] == "collections"]
    assert coll_ops == [
        ("collections", "create", "c-parent"),
        ("collections", "create", "c-child"),
        ("collections", "update", "c-parent"),
    ]


# --- orphans cause no writes ---------------------------------------------------

def test_orphans_only_plan_makes_no_apply_calls_and_returns_clean():
    tree = repo.Tree(root=".")
    state = _state()
    plan = {"creates": [], "updates": [], "orphans": [{"section": "cards", "id": 99, "name": "ghost"}]}
    client = FakeClient()
    rc = apply_mod.run_apply(tree, state, client, plan, yes=True, dry_run=False)
    assert rc == 0
    assert client.calls == []


# --- Fix 1: the PUT body must carry exactly what diff compared -----------------
#
# PUT /api/card/:id and PUT /api/dashboard/:id are partial updates for scalars, while
# diff compares the *normalized* desired shape, which setdefaults description/cache_ttl
# to null. An omitted key would leave the old server value and the same change would
# reappear on every later diff.

def _card_lookup():
    return apply_mod.StrictLookup(_state(cards={"daily-revenue": {"id": 7}}))


def test_card_put_body_sends_explicit_null_for_a_description_deleted_from_the_yaml(tmp_path):
    doc = minimal_card()
    doc.pop("description")  # author deleted the line, meaning "clear it"
    doc.pop("cache_ttl")
    tree = repo.Tree(root=".", cards={"daily-revenue": doc})
    state = State(str(tmp_path / ".state" / "test.yaml"),
                  {"collections": {}, "cards": {"daily-revenue": {"id": 7}}, "dashboards": {}})

    captured = {}
    client = FakeClient(puts={"/api/card/7": lambda body: captured.setdefault("body", body)})
    apply_mod._update_card(tree, state, client, "daily-revenue")

    body = captured["body"]
    assert "description" in body and body["description"] is None
    assert "cache_ttl" in body and body["cache_ttl"] is None


def test_card_put_body_keeps_an_explicit_description_value():
    body = apply_mod._card_body(minimal_card(description="still here"), _card_lookup())
    assert body["description"] == "still here"


def test_card_body_never_sends_null_for_a_field_the_server_ignores_nulls_on():
    doc = minimal_card(display=None, visualization_settings=None, parameters=None)
    body = apply_mod._card_body(doc, _card_lookup())
    # :non-nil fields — a null there is a silent no-op, so it must be omitted, not sent.
    assert not (apply_mod.CARD_NON_NIL & {k for k, v in body.items() if v is None})
    assert "display" not in body
    assert "visualization_settings" not in body


def test_card_post_body_carries_explicit_nulls_but_never_archived():
    doc = minimal_card()
    doc.pop("description")
    body = apply_mod._card_post_body(doc, _card_lookup())
    assert "archived" not in body  # the server rejects it on create
    assert "description" in body and body["description"] is None


def test_dashboard_put_body_sends_explicit_null_for_deleted_scalars():
    doc = minimal_dashboard(key="overview", collection="root", dashcards=[])
    doc.pop("description")
    doc.pop("cache_ttl")
    body, _, _ = apply_mod._dashboard_put_body(doc, {}, apply_mod.StrictLookup(_state()))
    assert "description" in body and body["description"] is None
    assert "cache_ttl" in body and body["cache_ttl"] is None
    assert body["collection_id"] is None  # :present set — null means "root"


def test_dashboard_put_body_never_sends_null_for_a_field_the_server_ignores_nulls_on():
    doc = minimal_dashboard(key="overview", collection="root", dashcards=[],
                            auto_apply_filters=None, parameters=None)
    body, _, _ = apply_mod._dashboard_put_body(doc, {}, apply_mod.StrictLookup(_state()))
    assert not (apply_mod.DASHBOARD_NON_NIL & {k for k, v in body.items() if v is None})


# --- card_id injection into parameter_mappings on write ------------------------

def test_dashboard_put_body_injects_card_id_into_a_mapping_that_omits_it():
    doc = minimal_dashboard(key="overview", collection="root", dashcards=[
        minimal_dashcard(parameter_mappings=[
            {"parameter_id": "days", "target": ["variable", ["template-tag", "days"]]},
        ]),
    ])
    state = _state(cards={"daily-revenue": {"id": 69}})
    body, _, _ = apply_mod._dashboard_put_body(doc, {}, apply_mod.StrictLookup(state))
    assert body["dashcards"][0]["parameter_mappings"][0]["card_id"] == 69


def test_dashboard_put_body_leaves_an_explicit_card_id_in_a_mapping_untouched():
    doc = minimal_dashboard(key="overview", collection="root", dashcards=[
        minimal_dashcard(parameter_mappings=[
            {"parameter_id": "days", "card_id": 5, "target": []},
        ]),
    ])
    state = _state(cards={"daily-revenue": {"id": 69}})
    body, _, _ = apply_mod._dashboard_put_body(doc, {}, apply_mod.StrictLookup(state))
    assert body["dashcards"][0]["parameter_mappings"][0]["card_id"] == 5


# --- Fix 2: id recording after a PUT -------------------------------------------

def _dash_tree(dashcards, tabs=None):
    doc = minimal_dashboard(key="overview", collection="root", dashcards=dashcards)
    if tabs is not None:
        doc["tabs"] = tabs
    return repo.Tree(root=".", dashboards={"overview": doc},
                     cards={"daily-revenue": minimal_card()})


def _dash_state(tmp_path, tabs=None, dashcards=None):
    return State(str(tmp_path / ".state" / "test.yaml"), {
        "collections": {}, "cards": {"daily-revenue": {"id": 7}},
        "dashboards": {"overview": {"id": 100, "entity_id": "e100",
                                    "tabs": tabs or {}, "dashcards": dashcards or {}}},
    })


def _recorded(state):
    return state.data["dashboards"]["overview"]


def _echo_put(captured, ids):
    """PUT handler echoing the sent dashcards/tabs back with the given server ids."""
    def handler(body):
        captured["body"] = body
        return {
            "tabs": [{**t, "id": next(ids["tabs"]), "position": i}
                     for i, t in enumerate(body.get("tabs") or [])],
            "dashcards": [{**dc, "id": next(ids["dashcards"])} for dc in body["dashcards"]],
        }
    return handler


def test_create_dashboard_records_the_ids_the_put_response_returned(tmp_path):
    tree = _dash_tree([{"key": "chart", "card": "daily-revenue", "tab": "t1",
                        "row": 0, "col": 0, "size_x": 12, "size_y": 6}],
                      tabs=[{"key": "t1", "name": "Tab 1"}])
    state = State(str(tmp_path / ".state" / "test.yaml"), {
        "collections": {}, "cards": {"daily-revenue": {"id": 7}}, "dashboards": {}})
    captured = {}
    ids = {"tabs": iter([500]), "dashcards": iter([900])}
    client = FakeClient(posts={"/api/dashboard": lambda body: {"id": 100, "entity_id": "e100"}},
                        puts={"/api/dashboard/100": _echo_put(captured, ids)})

    apply_mod._create_dashboard(tree, state, client, "overview")

    assert _recorded(state)["tabs"] == {"t1": 500}
    assert _recorded(state)["dashcards"] == {"chart": 900}
    # nothing was claimed yet, so the heal GET is skipped entirely
    assert [(m, p) for m, p, _ in client.calls] == [
        ("POST", "/api/dashboard"), ("PUT", "/api/dashboard/100")]


def test_stale_state_dashcard_id_is_demoted_to_a_temp_id_and_healed(tmp_path):
    # state claims 55, but someone deleted that dashcard in the UI. Sending 55 would
    # make the server create a fresh row that state never learns about.
    tree = _dash_tree([{"key": "chart", "card": "daily-revenue",
                        "row": 0, "col": 0, "size_x": 12, "size_y": 6}])
    state = _dash_state(tmp_path, dashcards={"chart": 55})
    captured = {}
    client = FakeClient(
        gets={"/api/dashboard/100": {"tabs": [], "dashcards": [{"id": 77}]}},
        puts={"/api/dashboard/100": _echo_put(captured, {"tabs": iter([]),
                                                         "dashcards": iter([900])})})

    apply_mod._update_dashboard(tree, state, client, "overview")

    assert captured["body"]["dashcards"][0]["id"] < 0  # demoted, i.e. "create"
    assert _recorded(state)["dashcards"] == {"chart": 900}
    assert [(m, p) for m, p, _ in client.calls] == [
        ("GET", "/api/dashboard/100"), ("PUT", "/api/dashboard/100")]


def test_live_dashcard_id_still_present_is_kept_and_reused(tmp_path):
    tree = _dash_tree([{"key": "chart", "card": "daily-revenue",
                        "row": 0, "col": 0, "size_x": 12, "size_y": 6}])
    state = _dash_state(tmp_path, dashcards={"chart": 55})
    captured = {}
    client = FakeClient(
        gets={"/api/dashboard/100": {"tabs": [], "dashcards": [{"id": 55}]}},
        puts={"/api/dashboard/100": lambda body: captured.setdefault(
            "body", body) or {"tabs": [], "dashcards": [{**body["dashcards"][0]}]}})

    apply_mod._update_dashboard(tree, state, client, "overview")

    assert captured["body"]["dashcards"][0]["id"] == 55
    assert _recorded(state)["dashcards"] == {"chart": 55}


def test_stale_tab_id_is_demoted_and_rerecorded(tmp_path):
    tree = _dash_tree([], tabs=[{"key": "t1", "name": "Tab 1"}])
    state = _dash_state(tmp_path, tabs={"t1": 42})
    captured = {}
    client = FakeClient(
        gets={"/api/dashboard/100": {"tabs": [{"id": 43}], "dashcards": []}},
        puts={"/api/dashboard/100": _echo_put(captured, {"tabs": iter([500]),
                                                         "dashcards": iter([])})})

    apply_mod._update_dashboard(tree, state, client, "overview")

    assert captured["body"]["tabs"][0]["id"] < 0
    assert _recorded(state)["tabs"] == {"t1": 500}


def test_an_empty_dashcards_array_in_the_response_records_no_ids(tmp_path):
    # Nothing came back, so nothing is provably ours: keeping 55 is what made the
    # stale id immortal.
    tree = _dash_tree([{"key": "chart", "card": "daily-revenue",
                        "row": 0, "col": 0, "size_x": 12, "size_y": 6}])
    state = _dash_state(tmp_path, dashcards={"chart": 55})
    client = FakeClient(gets={"/api/dashboard/100": {"tabs": [], "dashcards": [{"id": 55}]}},
                        puts={"/api/dashboard/100": lambda body: {"tabs": [], "dashcards": []}})

    apply_mod._update_dashboard(tree, state, client, "overview")

    assert _recorded(state)["dashcards"] == {}


def test_a_partial_dashcards_response_records_only_what_came_back(tmp_path):
    tree = _dash_tree([
        {"key": "kept", "card": "daily-revenue", "row": 0, "col": 0, "size_x": 12, "size_y": 6},
        {"key": "missing", "card": "daily-revenue", "row": 6, "col": 0, "size_x": 12, "size_y": 6},
    ])
    state = _dash_state(tmp_path, dashcards={"kept": 55, "missing": 56})
    live = {"tabs": [], "dashcards": [{"id": 55}, {"id": 56}]}
    client = FakeClient(
        gets={"/api/dashboard/100": live},
        puts={"/api/dashboard/100": lambda body: {"tabs": [], "dashcards": [{"id": 55}]}})

    apply_mod._update_dashboard(tree, state, client, "overview")

    assert _recorded(state)["dashcards"] == {"kept": 55}


# --- Fix 3: ambiguous matches record nothing instead of guessing ---------------

def test_ambiguous_signatures_record_no_mapping_at_all(tmp_path):
    # Same card at the same position on the same tab: the signature cannot tell the
    # two response rows apart, and a guess would cross-wire key -> id.
    tree = _dash_tree(
        [{"key": "a", "card": "daily-revenue", "tab": "t1",
          "row": 0, "col": 0, "size_x": 12, "size_y": 6},
         {"key": "b", "card": "daily-revenue", "tab": "t1",
          "row": 0, "col": 0, "size_x": 12, "size_y": 6}],
        tabs=[{"key": "t1", "name": "Tab 1"}])
    state = _dash_state(tmp_path)
    captured = {}
    ids = {"tabs": iter([500]), "dashcards": iter([901, 902])}
    client = FakeClient(puts={"/api/dashboard/100": _echo_put(captured, ids)})

    apply_mod._update_dashboard(tree, state, client, "overview")

    assert _recorded(state)["dashcards"] == {}
    assert _recorded(state)["tabs"] == {"t1": 500}


def test_headings_sharing_a_position_across_tabs_are_told_apart_by_their_tab(tmp_path):
    # Virtual cards carry no card_id, so every "row 0, col 0, 24x1" heading has the
    # same card_id/row/col/size signature; only the tab separates them.
    heading = {"row": 0, "col": 0, "size_x": 24, "size_y": 1,
               "visualization_settings": {"virtual_card": {"display": "heading"}}}
    tree = _dash_tree(
        [{"key": "h1", "tab": "t1", **heading}, {"key": "h2", "tab": "t2", **heading}],
        tabs=[{"key": "t1", "name": "Tab 1"}, {"key": "t2", "name": "Tab 2"}])
    state = _dash_state(tmp_path, tabs={"t1": 6, "t2": 7})
    captured = {}
    ids = {"tabs": iter([6, 7]), "dashcards": iter([901, 902])}
    client = FakeClient(gets={"/api/dashboard/100": {"tabs": [{"id": 6}, {"id": 7}],
                                                    "dashcards": []}},
                        puts={"/api/dashboard/100": _echo_put(captured, ids)})

    apply_mod._update_dashboard(tree, state, client, "overview")

    assert _recorded(state)["dashcards"] == {"h1": 901, "h2": 902}


def test_new_tabs_temp_ids_do_not_block_matching_their_dashcards(tmp_path):
    # The tabs are created by this same PUT: the request carries negative temp tab ids
    # and the response the real ones, so the signature must compare them as the same tab.
    heading = {"row": 0, "col": 0, "size_x": 24, "size_y": 1,
               "visualization_settings": {"virtual_card": {"display": "heading"}}}
    tree = _dash_tree(
        [{"key": "h1", "tab": "t1", **heading}, {"key": "h2", "tab": "t2", **heading}],
        tabs=[{"key": "t1", "name": "Tab 1"}, {"key": "t2", "name": "Tab 2"}])
    state = _dash_state(tmp_path)
    captured = {}

    def put_dashboard(body):
        captured["body"] = body
        real = {tab["id"]: 500 + i for i, tab in enumerate(body["tabs"])}
        return {
            "tabs": [{**tab, "id": real[tab["id"]], "position": i}
                     for i, tab in enumerate(body["tabs"])],
            "dashcards": [{**dc, "id": 901 + i,
                           "dashboard_tab_id": real[dc["dashboard_tab_id"]]}
                          for i, dc in enumerate(body["dashcards"])],
        }

    client = FakeClient(puts={"/api/dashboard/100": put_dashboard})

    apply_mod._update_dashboard(tree, state, client, "overview")

    assert captured["body"]["tabs"][0]["id"] < 0
    assert _recorded(state)["tabs"] == {"t1": 500, "t2": 501}
    assert _recorded(state)["dashcards"] == {"h1": 901, "h2": 902}


def test_match_dashcard_returns_none_when_two_rows_share_a_signature():
    written = {"card_id": 7, "row": 0, "col": 0, "size_x": 12, "size_y": 6}
    pool = [{**written, "id": 901}, {**written, "id": 902}]
    assert apply_mod._match_dashcard(pool, written, {}) is None


def test_match_dashcard_returns_none_when_nothing_matches():
    written = {"card_id": 7, "row": 0, "col": 0, "size_x": 12, "size_y": 6}
    assert apply_mod._match_dashcard([{"id": 901, "card_id": 9, "row": 4}], written, {}) is None
    assert apply_mod._match_dashcard([], written, {}) is None


def test_a_single_leftover_row_is_paired_by_elimination_not_by_guessing(tmp_path):
    # The server rewrote row/col, so the signature no longer matches — but with exactly
    # one unclaimed key and one unclaimed row the pairing is forced, not a guess.
    tree = _dash_tree([{"key": "chart", "card": "daily-revenue",
                        "row": 0, "col": 0, "size_x": 12, "size_y": 6}])
    state = _dash_state(tmp_path)
    client = FakeClient(puts={"/api/dashboard/100": lambda body: {
        "tabs": [],
        "dashcards": [{**body["dashcards"][0], "id": 901, "row": 3}]}})

    apply_mod._update_dashboard(tree, state, client, "overview")

    assert _recorded(state)["dashcards"] == {"chart": 901}


def test_two_unmatched_keys_and_two_unmatched_rows_record_nothing(tmp_path):
    tree = _dash_tree([
        {"key": "a", "card": "daily-revenue", "row": 0, "col": 0, "size_x": 12, "size_y": 6},
        {"key": "b", "card": "daily-revenue", "row": 6, "col": 0, "size_x": 12, "size_y": 6},
    ])
    state = _dash_state(tmp_path)
    client = FakeClient(puts={"/api/dashboard/100": lambda body: {
        "tabs": [],
        "dashcards": [{"id": 901, "card_id": 7, "row": 99, "col": 0, "size_x": 12, "size_y": 6},
                      {"id": 902, "card_id": 7, "row": 98, "col": 0, "size_x": 12, "size_y": 6}]}})

    apply_mod._update_dashboard(tree, state, client, "overview")

    assert _recorded(state)["dashcards"] == {}


# --- archived cascade: a 404 `archived` on a doc that wants archiving is a no-op ---

ARCHIVED_404_BODY = '{"message":"The object has been archived.","error_code":"archived"}'


def _archive_tree_and_state(tmp_path):
    """Collection + dashboard inside it, both marked archived, both already live."""
    tree = repo.Tree(
        root=str(tmp_path),
        collections={"examples": {"kind": "collection", "key": "examples", "name": "Examples",
                                  "description": None, "parent": None, "archived": True}},
        dashboards={"overview": minimal_dashboard(collection="examples", archived=True,
                                                  dashcards=[])},
        cards={},
    )
    state = State(str(tmp_path / ".state" / "test.yaml"), {
        "version": 1, "instance": "http://x", "metabase_version": None,
        "collections": {"examples": {"id": 2, "entity_id": "col-2"}}, "cards": {},
        "dashboards": {"overview": {"id": 1, "entity_id": "dash-1", "tabs": {}, "dashcards": {}}},
    })
    return tree, state


def _archived_put(path):
    def handler(body):
        raise ApiError("PUT", path, 404, ARCHIVED_404_BODY)
    return handler


def test_archived_404_on_a_doc_that_wants_archiving_is_not_an_error(tmp_path):
    tree, state = _archive_tree_and_state(tmp_path)
    client = FakeClient(puts={"/api/collection/2": {"id": 2, "archived": True},
                              "/api/dashboard/1": _archived_put("/api/dashboard/1")})
    plan = {"creates": [], "updates": [{"section": "collections", "key": "examples"},
                                       {"section": "dashboards", "key": "overview"}],
            "orphans": []}

    rc = apply_mod.run_apply(tree, state, client, plan, yes=True, dry_run=False)

    assert rc == 0
    assert ("PUT", "/api/collection/2", {"name": "Examples", "description": None,
                                         "parent_id": None, "archived": True}) in client.calls


def test_archived_404_still_raises_when_the_doc_wants_the_entity_alive(tmp_path):
    tree, state = _archive_tree_and_state(tmp_path)
    tree.dashboards["overview"]["archived"] = False
    client = FakeClient(puts={"/api/collection/2": {"id": 2, "archived": True},
                              "/api/dashboard/1": _archived_put("/api/dashboard/1")})
    plan = {"creates": [], "updates": [{"section": "dashboards", "key": "overview"}], "orphans": []}

    with pytest.raises(ApiError):
        apply_mod.run_apply(tree, state, client, plan, yes=True, dry_run=False)


def test_a_non_archived_404_always_raises(tmp_path):
    tree, state = _archive_tree_and_state(tmp_path)

    def gone(body):
        raise ApiError("PUT", "/api/dashboard/1", 404, '{"message":"Not found."}')

    client = FakeClient(puts={"/api/dashboard/1": gone})
    plan = {"creates": [], "updates": [{"section": "dashboards", "key": "overview"}], "orphans": []}

    with pytest.raises(ApiError):
        apply_mod.run_apply(tree, state, client, plan, yes=True, dry_run=False)


def test_archived_404_on_a_create_always_raises(tmp_path):
    """A create that 404s left no entity and no state id: never swallow it."""
    tree, state = _archive_tree_and_state(tmp_path)
    state.data["collections"] = {}

    def refused(body):
        raise ApiError("POST", "/api/collection", 404, ARCHIVED_404_BODY)

    client = FakeClient(posts={"/api/collection": refused})
    plan = {"creates": [{"section": "collections", "key": "examples"}], "updates": [], "orphans": []}

    with pytest.raises(ApiError):
        apply_mod.run_apply(tree, state, client, plan, yes=True, dry_run=False)

    assert state.data["collections"] == {}
