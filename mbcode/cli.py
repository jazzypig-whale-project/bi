"""mbc — Metabase as code. Subcommands: export, validate, diff, apply."""
from __future__ import annotations

import argparse
import json
import os
import sys

try:
    import yaml  # noqa: F401
except ImportError:
    sys.stderr.write(
        "PyYAML is missing. Install it with:\n"
        "  python3 -m pip install --requirement requirements.txt\n"
        "or create a venv at .venv and install there.\n")
    sys.exit(1)

from . import apply as apply_mod
from . import diff as diff_mod
from . import export as export_mod
from .apply import ApplyError
from .client import ApiError, Client
from .config import ConfigError, load_config
from .state import State
from .validate import validate_tree

DEFAULT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _common_flags(parser, suppress: bool):
    default = argparse.SUPPRESS if suppress else None
    parser.add_argument("--dir", default=default if suppress else DEFAULT_DIR,
                        help="project directory (default: the directory containing mbc)")
    parser.add_argument("--env-file", default=default,
                        help="path to the .env file (default: <dir>/.env)")
    if suppress:
        parser.add_argument("--verbose", action="store_true", default=default)
        return
    parser.add_argument("--verbose", action="store_true")


def build_parser():
    parser = argparse.ArgumentParser(prog="mbc", description="Metabase as code")
    _common_flags(parser, suppress=False)
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export", help="live instance -> YAML tree + state")
    p_export.add_argument("--overwrite", action="store_true")
    p_validate = sub.add_parser("validate", help="offline validation of the YAML tree")
    p_diff = sub.add_parser("diff", help="compare YAML tree against the live instance")
    p_diff.add_argument("--json", action="store_true", dest="as_json")
    p_diff.add_argument("--fail-on-orphans", action="store_true",
                        help="exit 2 when the instance has entities that are not in code")
    p_apply = sub.add_parser("apply", help="apply the YAML tree to the live instance")
    p_apply.add_argument("--yes", action="store_true")
    p_apply.add_argument("--dry-run", action="store_true")
    p_apply.add_argument("--allow-duplicate-names", action="store_true",
                         help="apply even when a CREATE has the same name as an orphan "
                              "(usually a renamed key, which would create a duplicate)")

    for p in (p_export, p_validate, p_diff, p_apply):
        _common_flags(p, suppress=True)
    return parser


def _context(args):
    env_file = getattr(args, "env_file", None) or os.path.join(args.dir, ".env")
    config = load_config(env_file)
    client = Client(config, verbose=bool(getattr(args, "verbose", False)))
    state = State.load(args.dir, config.host_slug, config.base_url)
    return config, client, state


def _validated_tree(args):
    tree, problems = validate_tree(args.dir)
    for problem in problems:
        print(problem)
    return tree, problems


def cmd_export(args) -> int:
    config, client, _ = _context(args)
    return export_mod.run_export(config, client, args.dir, args.overwrite)


def cmd_validate(args) -> int:
    _, problems = _validated_tree(args)
    if problems:
        return 1
    print("OK")
    return 0


def _diff_exit_code(plan, fail_on_orphans: bool) -> int:
    if diff_mod.has_changes(plan):
        return 2
    if fail_on_orphans and plan["orphans"]:
        return 2
    return 0


def cmd_diff(args) -> int:
    tree, problems = _validated_tree(args)
    if problems:
        return 1
    _, client, state = _context(args)
    plan = diff_mod.build_plan(tree, state, client)
    if args.as_json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return _diff_exit_code(plan, args.fail_on_orphans)
    print(diff_mod.render_plan(plan))
    conflicts = diff_mod.duplicate_name_conflicts(tree, plan)
    if conflicts:
        print(diff_mod.render_duplicate_name_warning(conflicts))
    return _diff_exit_code(plan, args.fail_on_orphans)


def cmd_apply(args) -> int:
    tree, problems = _validated_tree(args)
    if problems:
        return 1
    _, client, state = _context(args)
    plan = diff_mod.build_plan(tree, state, client)
    print(diff_mod.render_plan(plan))
    if not diff_mod.has_changes(plan):
        return 0
    conflicts = diff_mod.duplicate_name_conflicts(tree, plan)
    if conflicts:
        print(diff_mod.render_duplicate_name_warning(conflicts))
    if conflicts and not args.allow_duplicate_names:
        print("aborted: refusing to create duplicates (use --allow-duplicate-names to proceed)")
        return 1
    return apply_mod.run_apply(tree, state, client, plan,
                               yes=args.yes, dry_run=args.dry_run)


COMMANDS = {
    "export": cmd_export,
    "validate": cmd_validate,
    "diff": cmd_diff,
    "apply": cmd_apply,
}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (ConfigError, ApiError, ApplyError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
