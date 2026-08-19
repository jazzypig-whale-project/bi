"""client.py: no DELETE code path, secret redaction under --verbose, no double-slash URLs.

Every test here stubs the internal urllib opener so nothing hits the network.
"""
from __future__ import annotations

import inspect

import pytest

from mbcode import client as client_mod
from mbcode.config import Config, load_config


class _FakeResponse:
    def __init__(self, body=b"{}"):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return self._body


class _FakeOpener:
    def __init__(self):
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        return _FakeResponse()


def _config(**overrides):
    defaults = dict(base_url="https://metabase.example.test", basic_username="secret-user",
                    basic_password="secret-pass", api_key="super-secret-key")
    defaults.update(overrides)
    return Config(**defaults)


# --- no DELETE code path -------------------------------------------------------

def test_delete_is_not_an_allowed_method():
    assert "DELETE" not in client_mod.ALLOWED_METHODS
    assert set(client_mod.ALLOWED_METHODS) == {"GET", "POST", "PUT"}


def test_client_exposes_no_delete_method():
    public_methods = {name for name in dir(client_mod.Client) if not name.startswith("_")}
    assert "delete" not in public_methods
    assert public_methods == {"get", "get_or_none", "post", "put", "request"}


def test_request_rejects_delete_before_touching_the_network():
    c = client_mod.Client(_config())
    c._opener = _FakeOpener()  # would fail loudly if request() got this far
    with pytest.raises(ValueError):
        c.request("DELETE", "/api/card/1")


def test_module_source_never_issues_a_delete_http_call():
    source = inspect.getsource(client_mod)
    # the docstring documents the guarantee; ALLOWED_METHODS enforces it. Neither is a call site.
    assert 'method="DELETE"' not in source
    assert "method='DELETE'" not in source
    assert ".delete(" not in source


# --- verbose tracing redacts secrets -------------------------------------------

def test_verbose_tracing_redacts_api_key_and_authorization(capsys):
    c = client_mod.Client(_config(), verbose=True)
    c._opener = _FakeOpener()

    result = c.get("/api/session/properties")

    assert result == {}
    captured = capsys.readouterr()
    assert "X-API-KEY: <redacted>" in captured.err
    assert "Authorization: <redacted>" in captured.err
    assert "super-secret-key" not in captured.err
    assert "secret-user" not in captured.err
    assert "secret-pass" not in captured.err


def test_non_verbose_client_prints_nothing(capsys):
    c = client_mod.Client(_config(), verbose=False)
    c._opener = _FakeOpener()
    c.get("/api/session/properties")
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


# --- base URL trailing slash stripped, no // in built URLs --------------------

def test_config_strips_trailing_slash_from_base_url(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "METABASE_BASE_URL=https://metabase.example.test/\n"
        "METABASE_BASIC_USERNAME=u\n"
        "METABASE_BASIC_PASSWORD=p\n"
        "METABASE_API_KEY=k\n"
    )
    config = load_config(str(env_file))
    assert config.base_url == "https://metabase.example.test"


def test_built_url_has_no_double_slash_after_the_scheme():
    c = client_mod.Client(_config(base_url="https://metabase.example.test"))
    opener = _FakeOpener()
    c._opener = opener
    c.get("/api/collection")
    url = opener.requests[0].full_url
    scheme, rest = url.split("://", 1)
    assert "//" not in rest
    assert url == "https://metabase.example.test/api/collection"


# --- a 200 with a non-JSON body is an ApiError, not a raw JSONDecodeError -------

def test_non_json_200_body_is_wrapped_in_an_api_error():
    c = client_mod.Client(_config())
    opener = _FakeOpener()
    opener.open = lambda req, timeout=None: _FakeResponse(
        b"<html><body>Proxy login required</body></html>")
    c._opener = opener

    with pytest.raises(client_mod.ApiError) as excinfo:
        c.get("/api/card/1")

    err = excinfo.value
    assert err.status == 200
    assert "Proxy login required" in err.body
    assert len(err.body) <= 500


def test_non_json_200_body_is_truncated_in_the_error():
    c = client_mod.Client(_config())
    opener = _FakeOpener()
    opener.open = lambda req, timeout=None: _FakeResponse(b"x" * 5000)
    c._opener = opener

    with pytest.raises(client_mod.ApiError) as excinfo:
        c.get("/api/card/1")

    assert len(excinfo.value.body) == 500
