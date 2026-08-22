"""client.py: no DELETE code path, secret redaction under --verbose, no double-slash URLs.

Every test here stubs the internal urllib opener so nothing hits the network.
"""
from __future__ import annotations

import http.client
import inspect
import io
import threading

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


# --- fakes for the keep-alive connection pool ----------------------------------

class _FakeHTTPResponse:
    def __init__(self, status=200, reason="OK", body=b"{}"):
        self.status = status
        self.reason = reason
        self.headers = {}
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeConnection:
    """Stands in for http.client.HTTPConnection/HTTPSConnection.

    `behavior` is a list of zero-arg callables, one per getresponse() call on this
    connection; each either returns a _FakeHTTPResponse or raises. Once exhausted,
    getresponse() keeps returning a plain 200.
    """

    def __init__(self, host, port, timeout=None, behavior=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.requests = []
        self.closed = False
        self.tunnel = None
        self._behavior = list(behavior or [])

    def set_tunnel(self, host, port=None, headers=None):
        self.tunnel = (host, port, dict(headers) if headers else headers)

    def request(self, method, path, body=None, headers=None):
        self.requests.append((method, path, body, headers))

    def getresponse(self):
        if self._behavior:
            return self._behavior.pop(0)()
        return _FakeHTTPResponse()

    def close(self):
        self.closed = True


def _connection_factory(behavior_by_index=None):
    """Returns a `connection_class`-compatible callable that records every connection
    it creates in `.created`, applying the canned `behavior_by_index[i]` (a list of
    getresponse() actions) to the i-th connection created."""
    behavior_by_index = behavior_by_index or {}
    created = []

    def factory(host, port, timeout=None):
        conn = _FakeConnection(host, port, timeout, behavior_by_index.get(len(created)))
        created.append(conn)
        return conn

    factory.created = created
    return factory


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
    assert public_methods == {"get", "get_or_none", "get_many", "get_many_or_none",
                              "post", "put", "request"}


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


# --- keep-alive connection pool -------------------------------------------------

def test_pool_reuses_one_connection_across_sequential_requests():
    factory = _connection_factory()
    c = client_mod.Client(_config())
    c._opener = client_mod._KeepAlivePool(c.base_url, connection_class=factory)

    for _ in range(5):
        c.get("/api/card/1")

    assert len(factory.created) == 1
    assert len(factory.created[0].requests) == 5


def test_pool_uses_one_connection_per_thread():
    factory = _connection_factory()
    c = client_mod.Client(_config())
    c._opener = client_mod._KeepAlivePool(c.base_url, connection_class=factory)

    threads = [threading.Thread(target=c.get, args=("/api/card/1",)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(factory.created) == 4
    assert all(len(conn.requests) == 1 for conn in factory.created)


def test_pool_retries_get_once_on_a_stale_connection():
    def stale():
        raise http.client.RemoteDisconnected("Remote end closed connection")

    factory = _connection_factory(behavior_by_index={0: [stale]})
    c = client_mod.Client(_config())
    c._opener = client_mod._KeepAlivePool(c.base_url, connection_class=factory)

    result = c.get("/api/card/1")

    assert result == {}
    assert len(factory.created) == 2
    assert factory.created[0].closed
    assert len(factory.created[1].requests) == 1


def test_pool_retries_put_once_on_a_stale_connection():
    def stale():
        raise http.client.RemoteDisconnected("Remote end closed connection")

    factory = _connection_factory(behavior_by_index={0: [stale]})
    c = client_mod.Client(_config())
    c._opener = client_mod._KeepAlivePool(c.base_url, connection_class=factory)

    result = c.put("/api/card/1", {"name": "x"})

    assert result == {}
    assert len(factory.created) == 2


def test_pool_never_retries_a_post_on_a_stale_connection():
    def stale():
        raise http.client.RemoteDisconnected("Remote end closed connection")

    factory = _connection_factory(behavior_by_index={0: [stale]})
    c = client_mod.Client(_config())
    c._opener = client_mod._KeepAlivePool(c.base_url, connection_class=factory)

    with pytest.raises(client_mod.ApiError) as excinfo:
        c.post("/api/card", {"name": "x"})

    assert excinfo.value.status == 0
    assert len(factory.created) == 1


def test_pool_picks_http_connection_for_an_http_base_url():
    pool = client_mod._KeepAlivePool("http://localhost:8080")
    assert pool._connection_class is http.client.HTTPConnection


def test_pool_picks_https_connection_for_an_https_base_url():
    pool = client_mod._KeepAlivePool("https://metabase.example.test:8443")
    assert pool._connection_class is http.client.HTTPSConnection


def test_pool_propagates_a_real_404_without_retrying_or_losing_the_status():
    """HTTPError is an OSError subclass — the retry-on-stale-connection clause must not
    catch it, or a real 4xx/5xx gets retried needlessly and its status is lost (turning
    into ApiError status=0, which breaks get_or_none's `err.status == 404` check)."""
    def not_found():
        return _FakeHTTPResponse(status=404, reason="Not Found", body=b'{"error": "gone"}')

    factory = _connection_factory(behavior_by_index={0: [not_found]})
    c = client_mod.Client(_config())
    c._opener = client_mod._KeepAlivePool(c.base_url, connection_class=factory)

    with pytest.raises(client_mod.ApiError) as excinfo:
        c.get("/api/card/1")

    assert excinfo.value.status == 404
    assert len(factory.created) == 1  # no retry for a genuine HTTP response


def test_pool_tunnels_through_http_proxy_for_https_target(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.test:3128")
    factory = _connection_factory()

    pool = client_mod._KeepAlivePool("https://metabase.example.test", connection_class=factory)
    pool.open(client_mod.urllib.request.Request("https://metabase.example.test/api/card/1"))

    conn = factory.created[0]
    assert conn.host == "proxy.example.test"
    assert conn.port == 3128
    assert conn.tunnel == ("metabase.example.test", 443, None)
    assert conn.requests[0][1] == "/api/card/1"  # selector, not the absolute URL


def test_pool_uses_absolute_uri_through_http_proxy_for_http_target(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.test:3128")
    factory = _connection_factory()

    pool = client_mod._KeepAlivePool("http://metabase.example.test", connection_class=factory)
    pool.open(client_mod.urllib.request.Request("http://metabase.example.test/api/card/1"))

    conn = factory.created[0]
    assert conn.host == "proxy.example.test"
    assert conn.port == 3128
    assert conn.tunnel is None
    assert conn.requests[0][1] == "http://metabase.example.test/api/card/1"


def test_pool_bypasses_proxy_for_no_proxy_host(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.test:3128")
    monkeypatch.setenv("NO_PROXY", "metabase.example.test")
    factory = _connection_factory()

    pool = client_mod._KeepAlivePool("https://metabase.example.test", connection_class=factory)
    pool.open(client_mod.urllib.request.Request("https://metabase.example.test/api/card/1"))

    conn = factory.created[0]
    assert conn.host == "metabase.example.test"
    assert conn.tunnel is None


def test_pool_bypasses_proxy_for_no_proxy_star(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.test:3128")
    monkeypatch.setenv("NO_PROXY", "*")
    factory = _connection_factory()

    pool = client_mod._KeepAlivePool("https://metabase.example.test", connection_class=factory)
    pool.open(client_mod.urllib.request.Request("https://metabase.example.test/api/card/1"))

    assert factory.created[0].host == "metabase.example.test"


def test_pool_sends_proxy_authorization_from_userinfo_on_https_tunnel(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxyuser:proxypass@proxy.example.test:3128")
    factory = _connection_factory()

    pool = client_mod._KeepAlivePool("https://metabase.example.test", connection_class=factory)
    pool.open(client_mod.urllib.request.Request("https://metabase.example.test/api/card/1"))

    header = factory.created[0].tunnel[2]["Proxy-Authorization"]
    assert header.startswith("Basic ")
    import base64
    assert base64.b64decode(header.removeprefix("Basic ")).decode() == "proxyuser:proxypass"


def test_pool_sends_proxy_authorization_from_userinfo_on_http_request(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://proxyuser:proxypass@proxy.example.test:3128")
    factory = _connection_factory()

    pool = client_mod._KeepAlivePool("http://metabase.example.test", connection_class=factory)
    pool.open(client_mod.urllib.request.Request("http://metabase.example.test/api/card/1"))

    headers = factory.created[0].requests[0][3]
    assert headers["Proxy-Authorization"].startswith("Basic ")


def test_pool_rejects_socks_proxy_scheme(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "socks5h://localhost:2080")

    with pytest.raises(client_mod.ConfigError, match="socks5h"):
        client_mod._KeepAlivePool("https://metabase.example.test")


def test_pool_rejects_unsupported_scheme_without_leaking_credentials(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "socks5://proxyuser:proxypass@proxy.example.test:1080")

    with pytest.raises(client_mod.ConfigError, match="socks5") as excinfo:
        client_mod._KeepAlivePool("https://metabase.example.test")

    assert "proxypass" not in str(excinfo.value)


def test_pool_decodes_percent_encoded_proxy_credentials(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxyuser:p%40ss@proxy.example.test:3128")
    factory = _connection_factory()

    pool = client_mod._KeepAlivePool("https://metabase.example.test", connection_class=factory)
    pool.open(client_mod.urllib.request.Request("https://metabase.example.test/api/card/1"))

    header = factory.created[0].tunnel[2]["Proxy-Authorization"]
    import base64
    assert base64.b64decode(header.removeprefix("Basic ")).decode() == "proxyuser:p@ss"


def test_pool_accepts_a_schemeless_proxy_value(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "proxy.example.test:3128")
    factory = _connection_factory()

    pool = client_mod._KeepAlivePool("https://metabase.example.test", connection_class=factory)
    pool.open(client_mod.urllib.request.Request("https://metabase.example.test/api/card/1"))

    conn = factory.created[0]
    assert conn.host == "proxy.example.test"
    assert conn.port == 3128


def test_pool_ignores_http_proxy_for_an_https_target(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.test:3128")
    factory = _connection_factory()

    pool = client_mod._KeepAlivePool("https://metabase.example.test", connection_class=factory)
    pool.open(client_mod.urllib.request.Request("https://metabase.example.test/api/card/1"))

    assert factory.created[0].host == "metabase.example.test"
    assert pool.proxy_display is None


def test_pool_proxy_display_redacts_credentials(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxyuser:proxypass@proxy.example.test:3128")

    pool = client_mod._KeepAlivePool("https://metabase.example.test")

    assert pool.proxy_display == "http://proxy.example.test:3128"
    assert "proxypass" not in pool.proxy_display


def test_verbose_client_traces_the_proxy_when_configured(monkeypatch, capsys):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxyuser:proxypass@proxy.example.test:3128")

    client_mod.Client(_config(base_url="https://metabase.example.test"), verbose=True)

    captured = capsys.readouterr()
    assert "via proxy http://proxy.example.test:3128" in captured.err
    assert "proxypass" not in captured.err


def test_non_verbose_client_does_not_trace_proxy(monkeypatch, capsys):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.test:3128")

    client_mod.Client(_config(base_url="https://metabase.example.test"), verbose=False)

    captured = capsys.readouterr()
    assert captured.err == ""


# --- get_many / get_many_or_none -------------------------------------------------

def test_get_many_returns_results_in_input_order_regardless_of_completion_order():
    order = []

    def opener_open(req, timeout=None):
        path = req.selector
        # the /slow/ path takes longer, but must still land at its input index
        if "slow" in path:
            import time
            time.sleep(0.05)
        order.append(path)
        return _FakeResponse(f'{{"path": "{path}"}}'.encode())

    c = client_mod.Client(_config(), jobs=4)
    c._opener = type("O", (), {"open": staticmethod(opener_open)})()

    results = c.get_many(["/api/card/1-slow", "/api/card/2", "/api/card/3"])

    assert [r["path"] for r in results] == ["/api/card/1-slow", "/api/card/2", "/api/card/3"]


def test_get_many_with_jobs_1_runs_inline():
    c = client_mod.Client(_config(), jobs=1)
    opener = _FakeOpener()
    c._opener = opener
    c.get_many(["/api/card/1", "/api/card/2"])
    assert [req.selector for req in opener.requests] == ["/api/card/1", "/api/card/2"]


def test_get_many_raises_the_failure_at_its_input_index():
    def opener_open(req, timeout=None):
        if req.selector == "/api/card/2":
            raise client_mod.urllib.error.HTTPError(
                req.full_url, 500, "Internal Server Error", {}, io.BytesIO(b"boom"))
        return _FakeResponse(b"{}")

    c = client_mod.Client(_config(), jobs=4)
    c._opener = type("O", (), {"open": staticmethod(opener_open)})()

    with pytest.raises(client_mod.ApiError) as excinfo:
        c.get_many(["/api/card/1", "/api/card/2", "/api/card/3"])

    assert excinfo.value.status == 500
    assert "/api/card/2" in excinfo.value.url


def test_get_many_or_none_maps_404_to_none_and_still_raises_on_500():
    def opener_open(req, timeout=None):
        if req.selector == "/api/card/missing":
            raise client_mod.urllib.error.HTTPError(
                req.full_url, 404, "Not Found", {}, io.BytesIO(b"missing"))
        if req.selector == "/api/card/broken":
            raise client_mod.urllib.error.HTTPError(
                req.full_url, 500, "Internal Server Error", {}, io.BytesIO(b"boom"))
        return _FakeResponse(b"{}")

    c = client_mod.Client(_config(), jobs=4)
    c._opener = type("O", (), {"open": staticmethod(opener_open)})()

    results = c.get_many_or_none(["/api/card/1", "/api/card/missing"])
    assert results == [{}, None]

    with pytest.raises(client_mod.ApiError):
        c.get_many_or_none(["/api/card/broken"])


def test_non_json_200_body_is_truncated_in_the_error():
    c = client_mod.Client(_config())
    opener = _FakeOpener()
    opener.open = lambda req, timeout=None: _FakeResponse(b"x" * 5000)
    c._opener = opener

    with pytest.raises(client_mod.ApiError) as excinfo:
        c.get("/api/card/1")

    assert len(excinfo.value.body) == 500
