"""Load configuration from a .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

REQUIRED_KEYS = (
    "METABASE_BASE_URL",
    "METABASE_BASIC_USERNAME",
    "METABASE_BASIC_PASSWORD",
    "METABASE_API_KEY",
)


class ConfigError(Exception):
    pass


def parse_env_file(path: str) -> dict:
    if not os.path.isfile(path):
        raise ConfigError(f"env file not found: {path}")
    values = {}
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("'\"")
    return values


@dataclass
class Config:
    base_url: str
    basic_username: str
    basic_password: str
    api_key: str

    @property
    def host_slug(self) -> str:
        netloc = urlparse(self.base_url).netloc
        return netloc.replace(".", "-").replace(":", "-")


def load_config(env_file: str) -> Config:
    values = parse_env_file(env_file)
    missing = [key for key in REQUIRED_KEYS if not values.get(key)]
    if missing:
        raise ConfigError(f"missing keys in {env_file}: {', '.join(missing)}")
    return Config(
        base_url=values["METABASE_BASE_URL"].rstrip("/"),
        basic_username=values["METABASE_BASIC_USERNAME"],
        basic_password=values["METABASE_BASIC_PASSWORD"],
        api_key=values["METABASE_API_KEY"],
    )
