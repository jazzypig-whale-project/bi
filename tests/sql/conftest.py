"""Throwaway-postgres harness for card SQL. Needs the `docker` CLI; skips every sql test without it."""
from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import time

import pytest
import yaml

SQL_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SQL_DIR))
IMAGE = "postgres:16"


def pytest_collection_modifyitems(config, items):
    if shutil.which("docker"):
        return
    skip = pytest.mark.skip(reason="docker CLI not on PATH")
    for item in items:
        if "sql" in item.keywords or "sql_perf" in item.keywords:
            item.add_marker(skip)


def _run(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, **kwargs)


class Db:
    def __init__(self, container):
        self.container = container

    def _psql(self, sql, *flags):
        cmd = ["docker", "exec", "-i", self.container, "psql", "-U", "postgres",
               "-v", "ON_ERROR_STOP=1", *flags]
        proc = _run(cmd, input=sql)
        if proc.returncode != 0:
            raise RuntimeError(f"psql failed: {proc.stderr.strip()}")
        return proc.stdout

    def exec(self, sql: str) -> None:
        self._psql(sql, "--quiet")

    def rows(self, sql: str) -> list[tuple[str, ...]]:
        out = self._psql(sql, "--csv", "--tuples-only")
        return [tuple(r) for r in csv.reader(io.StringIO(out))]


@pytest.fixture(scope="session")
def pg():
    if not shutil.which("docker"):
        pytest.skip("docker CLI not on PATH")
    started = _run(["docker", "run", "--rm", "--detach", "--env", "POSTGRES_PASSWORD=test", IMAGE])
    if started.returncode != 0:
        pytest.skip(f"cannot start {IMAGE}: {started.stderr.strip()}")
    container = started.stdout.strip()
    try:
        # The image runs a temporary init server, stops it, then starts the real one: the
        # "ready to accept connections" line appears twice. Wait for the second (final) one.
        for _ in range(90):
            logs = _run(["docker", "logs", container])
            ready = (logs.stdout + logs.stderr).count("ready to accept connections")
            probe = _run(["docker", "exec", container, "pg_isready", "--username", "postgres"])
            if ready >= 2 and probe.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("postgres did not become ready")
        yield container
    finally:
        _run(["docker", "rm", "--force", container])


@pytest.fixture
def db(pg):
    handle = Db(pg)
    handle.exec("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    with open(os.path.join(SQL_DIR, "schema.sql"), encoding="utf-8") as fh:
        handle.exec(fh.read())
    return handle


def card_sql(card_key: str, days: int, granularity: str) -> str:
    """Native SQL of cards/<key>.yaml with Metabase's template tags substituted."""
    path = os.path.join(REPO_ROOT, "cards", f"{card_key}.yaml")
    with open(path, encoding="utf-8") as fh:
        card = yaml.safe_load(fh)
    sql = card["dataset_query"]["stages"][0]["native"]
    return sql.replace("{{days}}", str(int(days))).replace("{{granularity}}", f"'{granularity}'")
