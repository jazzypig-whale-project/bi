"""Load/save the YAML tree: collections/, cards/, dashboards/."""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field

import yaml

KIND_DIRS = {"collection": "collections", "card": "cards", "dashboard": "dashboards"}
DIR_KINDS = {v: k for k, v in KIND_DIRS.items()}


@dataclass
class Tree:
    root: str
    collections: dict = field(default_factory=dict)  # key -> doc
    cards: dict = field(default_factory=dict)
    dashboards: dict = field(default_factory=dict)
    paths: dict = field(default_factory=dict)        # (section, key) -> file path

    def section(self, name: str) -> dict:
        return getattr(self, name)


def relpath(root: str, path: str) -> str:
    return os.path.relpath(path, root)


def load_tree(root: str):
    """Return (tree, problems). Problems are structural: bad YAML, key/stem mismatch, duplicates."""
    tree = Tree(root=root)
    problems = []
    for dirname, kind in DIR_KINDS.items():
        pattern = os.path.join(root, dirname, "*.yaml")
        for path in sorted(glob.glob(pattern)):
            rel = relpath(root, path)
            doc = _load_doc(path, rel, problems)
            if doc is None:
                continue
            if doc.get("kind") != kind:
                problems.append(f"{rel}: kind must be '{kind}' in {dirname}/, got {doc.get('kind')!r}")
                continue
            stem = os.path.splitext(os.path.basename(path))[0]
            key = doc.get("key")
            if key != stem:
                problems.append(f"{rel}: key {key!r} must equal the filename stem {stem!r}")
                continue
            section = tree.section(dirname)
            if key in section:
                problems.append(f"{rel}: duplicate key {key!r}")
                continue
            section[key] = doc
            tree.paths[(dirname, key)] = rel
    return tree, problems


def _load_doc(path: str, rel: str, problems: list):
    try:
        with open(path, encoding="utf-8") as handle:
            doc = yaml.safe_load(handle)
    except yaml.YAMLError as err:
        problems.append(f"{rel}: unparseable YAML: {err}")
        return None
    if not isinstance(doc, dict):
        problems.append(f"{rel}: top level must be a mapping")
        return None
    return doc


def dump_doc(path: str, doc: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(doc, handle, allow_unicode=True, sort_keys=False,
                       default_flow_style=False, indent=2, width=120)
