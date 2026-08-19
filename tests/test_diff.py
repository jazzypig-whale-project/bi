"""diff.py: exit-code contract, orphan handling, and change rendering."""
from __future__ import annotations

import pytest

from mbcode import cli, diff as diff_mod


@pytest.mark.parametrize("plan,expected", [
    ({"creates": [], "updates": [], "orphans": []}, False),
    ({"creates": [{"section": "cards", "key": "a"}], "updates": [], "orphans": []}, True),
    ({"creates": [], "updates": [{"section": "cards", "key": "a", "changes": []}],
     "orphans": []}, True),
    ({"creates": [], "updates": [], "orphans": [{"section": "cards", "id": 1, "name": "x"}]},
     False),
    ({"creates": [{"section": "cards", "key": "a"}], "updates": [],
     "orphans": [{"section": "cards", "id": 1, "name": "x"}]}, True),
])
def test_has_changes_exit_code_contract(plan, expected):
    assert diff_mod.has_changes(plan) is expected


def test_render_plan_shows_field_level_before_and_after():
    plan = {"creates": [], "updates": [
        {"section": "cards", "key": "daily-revenue", "changes": [
            {"field": "display", "before": "table", "after": "bar"},
        ]},
    ], "orphans": []}
    text = diff_mod.render_plan(plan)
    assert '"table"' in text
    assert '"bar"' in text
    assert "display" in text
    assert "UPDATE card daily-revenue" in text


def test_render_plan_lists_orphans_as_informational():
    plan = {"creates": [], "updates": [],
            "orphans": [{"section": "cards", "id": 99, "name": "Ghost Card"}]}
    text = diff_mod.render_plan(plan)
    assert "ORPHAN card id=99 'Ghost Card'" in text
    assert "not in code; left alone" in text
    assert "No changes." in text  # orphans alone are not a change


def test_render_plan_no_changes_message():
    plan = {"creates": [], "updates": [], "orphans": []}
    assert "No changes." in diff_mod.render_plan(plan)


# --- CLI exit-code contract, with build_plan stubbed so no network is touched --

def _prepare_dir(tmp_path):
    for section in ("collections", "cards", "dashboards"):
        (tmp_path / section).mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "METABASE_BASE_URL=https://metabase.example.test\n"
        "METABASE_BASIC_USERNAME=u\nMETABASE_BASIC_PASSWORD=p\nMETABASE_API_KEY=k\n"
    )
    return env_file


def test_cli_diff_exit_code_0_when_clean(tmp_path, monkeypatch):
    env_file = _prepare_dir(tmp_path)
    args = cli.build_parser().parse_args(
        ["--dir", str(tmp_path), "--env-file", str(env_file), "diff"])
    monkeypatch.setattr(cli.diff_mod, "build_plan",
                        lambda tree, state, client: {"creates": [], "updates": [], "orphans": []})
    assert cli.cmd_diff(args) == 0


def test_cli_diff_exit_code_2_when_changes_found(tmp_path, monkeypatch):
    env_file = _prepare_dir(tmp_path)
    args = cli.build_parser().parse_args(
        ["--dir", str(tmp_path), "--env-file", str(env_file), "diff"])
    plan = {"creates": [{"section": "cards", "key": "x"}], "updates": [], "orphans": []}
    monkeypatch.setattr(cli.diff_mod, "build_plan", lambda tree, state, client: plan)
    assert cli.cmd_diff(args) == 2


def test_cli_diff_exit_code_2_from_orphans_plus_a_real_change(tmp_path, monkeypatch):
    env_file = _prepare_dir(tmp_path)
    args = cli.build_parser().parse_args(
        ["--dir", str(tmp_path), "--env-file", str(env_file), "diff"])
    plan = {"creates": [], "updates": [{"section": "cards", "key": "x", "changes": []}],
            "orphans": [{"section": "cards", "id": 1, "name": "ghost"}]}
    monkeypatch.setattr(cli.diff_mod, "build_plan", lambda tree, state, client: plan)
    assert cli.cmd_diff(args) == 2


def test_cli_diff_exit_code_0_when_only_orphans(tmp_path, monkeypatch):
    env_file = _prepare_dir(tmp_path)
    args = cli.build_parser().parse_args(
        ["--dir", str(tmp_path), "--env-file", str(env_file), "diff"])
    plan = {"creates": [], "updates": [],
            "orphans": [{"section": "cards", "id": 1, "name": "ghost"}]}
    monkeypatch.setattr(cli.diff_mod, "build_plan", lambda tree, state, client: plan)
    assert cli.cmd_diff(args) == 0


# --- --fail-on-orphans (Fix 6) -------------------------------------------------

def _diff_args(tmp_path, env_file, *extra):
    return cli.build_parser().parse_args(
        ["--dir", str(tmp_path), "--env-file", str(env_file), "diff", *extra])


def test_cli_diff_fail_on_orphans_turns_orphans_into_exit_2(tmp_path, monkeypatch):
    env_file = _prepare_dir(tmp_path)
    args = _diff_args(tmp_path, env_file, "--fail-on-orphans")
    plan = {"creates": [], "updates": [],
            "orphans": [{"section": "dashboards", "id": 9, "name": "made in the UI"}]}
    monkeypatch.setattr(cli.diff_mod, "build_plan", lambda tree, state, client: plan)
    assert cli.cmd_diff(args) == 2


def test_cli_diff_fail_on_orphans_is_still_0_without_orphans(tmp_path, monkeypatch):
    env_file = _prepare_dir(tmp_path)
    args = _diff_args(tmp_path, env_file, "--fail-on-orphans")
    monkeypatch.setattr(cli.diff_mod, "build_plan",
                        lambda tree, state, client: {"creates": [], "updates": [], "orphans": []})
    assert cli.cmd_diff(args) == 0


def test_cli_diff_fail_on_orphans_applies_to_json_output_too(tmp_path, monkeypatch):
    env_file = _prepare_dir(tmp_path)
    args = _diff_args(tmp_path, env_file, "--fail-on-orphans", "--json")
    plan = {"creates": [], "updates": [], "orphans": [{"section": "cards", "id": 1, "name": "x"}]}
    monkeypatch.setattr(cli.diff_mod, "build_plan", lambda tree, state, client: plan)
    assert cli.cmd_diff(args) == 2


# --- rename / duplicate-name guard (Fix 7) -------------------------------------

class _FakeTree:
    def __init__(self, sections):
        self._sections = sections

    def section(self, name):
        return self._sections.get(name, {})


def _rename_plan():
    return {
        "creates": [{"section": "cards", "key": "revenue-by-day"}],
        "updates": [],
        "orphans": [{"section": "cards", "id": 40, "name": "Daily revenue"}],
    }


def _rename_tree():
    return _FakeTree({"cards": {"revenue-by-day": {"name": "Daily revenue"}}})


def test_duplicate_name_conflict_detected_for_a_renamed_key():
    conflicts = diff_mod.duplicate_name_conflicts(_rename_tree(), _rename_plan())
    assert conflicts == [{"section": "cards", "key": "revenue-by-day",
                          "name": "Daily revenue", "orphan_id": 40}]


def test_duplicate_name_conflict_ignores_a_different_name():
    plan = _rename_plan()
    plan["orphans"][0]["name"] = "Something else"
    assert diff_mod.duplicate_name_conflicts(_rename_tree(), plan) == []


def test_duplicate_name_conflict_ignores_a_different_kind():
    plan = _rename_plan()
    plan["orphans"][0]["section"] = "dashboards"
    assert diff_mod.duplicate_name_conflicts(_rename_tree(), plan) == []


def test_duplicate_name_warning_names_both_sides():
    text = diff_mod.render_duplicate_name_warning(
        diff_mod.duplicate_name_conflicts(_rename_tree(), _rename_plan()))
    assert "WARNING" in text
    assert "CREATE card revenue-by-day" in text
    assert "ORPHAN card id=40" in text
    assert "--allow-duplicate-names" in text


def test_cli_diff_warns_about_the_rename_but_keeps_its_exit_code(tmp_path, monkeypatch, capsys):
    env_file = _prepare_dir(tmp_path)
    args = _diff_args(tmp_path, env_file)
    monkeypatch.setattr(cli.diff_mod, "build_plan", lambda tree, state, client: _rename_plan())
    monkeypatch.setattr(cli, "_validated_tree", lambda args: (_rename_tree(), []))
    assert cli.cmd_diff(args) == 2
    assert "WARNING" in capsys.readouterr().out


def test_cli_apply_refuses_a_rename_without_the_override(tmp_path, monkeypatch, capsys):
    env_file = _prepare_dir(tmp_path)
    args = cli.build_parser().parse_args(
        ["--dir", str(tmp_path), "--env-file", str(env_file), "apply", "--yes"])
    monkeypatch.setattr(cli.diff_mod, "build_plan", lambda tree, state, client: _rename_plan())
    monkeypatch.setattr(cli, "_validated_tree", lambda args: (_rename_tree(), []))
    monkeypatch.setattr(cli.apply_mod, "run_apply", _never_called)
    assert cli.cmd_apply(args) == 1
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "--allow-duplicate-names" in out


def test_cli_apply_proceeds_with_allow_duplicate_names(tmp_path, monkeypatch):
    env_file = _prepare_dir(tmp_path)
    args = cli.build_parser().parse_args(
        ["--dir", str(tmp_path), "--env-file", str(env_file), "apply", "--yes",
         "--allow-duplicate-names"])
    monkeypatch.setattr(cli.diff_mod, "build_plan", lambda tree, state, client: _rename_plan())
    monkeypatch.setattr(cli, "_validated_tree", lambda args: (_rename_tree(), []))
    monkeypatch.setattr(cli.apply_mod, "run_apply",
                        lambda tree, state, client, plan, yes, dry_run: 0)
    assert cli.cmd_apply(args) == 0


def _never_called(*args, **kwargs):
    raise AssertionError("run_apply must not be reached when a duplicate name is detected")
