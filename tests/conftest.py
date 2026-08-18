"""Shared pytest fixtures for the offline mbcode test suite.

Nothing in this suite touches the network or the real collections/cards/dashboards/.state
directories: every filesystem test uses tmp_path, and every HTTP call is served by the
FakeClient in helpers.py.
"""
from __future__ import annotations

import json
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import pytest

FIXTURES_DIR = os.path.join(TESTS_DIR, "fixtures")


@pytest.fixture
def fixtures_dir():
    return FIXTURES_DIR


@pytest.fixture
def load_fixture():
    def _load(name):
        with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as handle:
            return json.load(handle)
    return _load
