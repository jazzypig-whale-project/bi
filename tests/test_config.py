"""Tests for mbcode.config: parsing .env into Config."""
from __future__ import annotations

from mbcode.config import load_config


def _write_env(tmp_path, extra=""):
    path = tmp_path / ".env"
    path.write_text(
        "METABASE_BASE_URL=https://metabase.example.test\n"
        "METABASE_BASIC_USERNAME=secret-user\n"
        "METABASE_BASIC_PASSWORD=secret-pass\n"
        "METABASE_API_KEY=super-secret-key\n"
        + extra
    )
    return str(path)


def test_load_config_leaves_proxy_empty_when_the_key_is_absent(tmp_path):
    env_file = _write_env(tmp_path)

    config = load_config(env_file)

    assert config.proxy == ""


def test_load_config_reads_metabase_proxy(tmp_path):
    env_file = _write_env(tmp_path, "METABASE_PROXY=http://proxy.example.test:3128\n")

    config = load_config(env_file)

    assert config.proxy == "http://proxy.example.test:3128"
