"""HTTP client for the Metabase API. GET/POST/PUT only — DELETE does not exist here."""
from __future__ import annotations

import base64
import http.client
import io
import json
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .config import ConfigError

TIMEOUT = 30
USER_AGENT = "mbcode/1.0"
ALLOWED_METHODS = ("GET", "POST", "PUT")
DEFAULT_JOBS = 8

# GET/PUT are idempotent, so a connection the peer silently closed (keep-alive
# timeout) is retried once on a fresh connection. POST is never retried — a retried
# create could double-post the same card/dashboard.
_RETRIABLE_METHODS = ("GET", "PUT")
_CONNECTION_CLASSES = {"https": http.client.HTTPSConnection, "http": http.client.HTTPConnection}


class ApiError(Exception):
    def __init__(self, method: str, url: str, status: int, body: str):
        self.method = method
        self.url = url
        self.status = status
        self.body = body[:500]
        super().__init__(f"{method} {url} -> HTTP {status}: {self.body}")


class _Proxy:
    """A resolved HTTP forward-proxy: where to connect, what to send in
    Proxy-Authorization (if any), and a credential-free string for --verbose."""

    def __init__(self, host: str, port: int, headers: dict, display: str):
        self.host = host
        self.port = port
        self.headers = headers
        self.display = display


def _resolve_proxy(scheme: str, host: str, port):
    """Resolve an HTTP forward-proxy for `scheme://host:port` from the standard
    HTTP_PROXY/HTTPS_PROXY/NO_PROXY environment variables (via urllib.request's own
    lookup, so lowercase precedence and the CGI HTTP_PROXY guard match stdlib
    behavior). Returns None when no proxy applies to this request.

    Only http:// proxy URLs are supported: http.client (and mbc's stdlib-only
    dependency set) cannot speak SOCKS, nor tunnel TLS-to-a-TLS proxy.
    """
    proxy_url = urllib.request.getproxies().get(scheme)
    if not proxy_url:
        return None
    netloc = host if port is None else f"{host}:{port}"
    if urllib.request.proxy_bypass(netloc):
        return None
    parsed = urllib.parse.urlsplit(proxy_url)
    if "://" not in proxy_url:
        # a bare "host:port" (curl/urllib both accept this form) has no scheme to parse;
        # urlsplit instead misreads the host as the scheme, so re-split it as http://.
        parsed = urllib.parse.urlsplit(f"http://{proxy_url}")
    if not parsed.hostname:
        raise ConfigError("proxy URL from the environment has no host")
    display_netloc = parsed.hostname if parsed.port is None else f"{parsed.hostname}:{parsed.port}"
    display = f"http://{display_netloc}"
    if parsed.scheme not in ("http", ""):
        raise ConfigError(
            f"proxy scheme '{parsed.scheme}' from the environment is not supported by mbc "
            f"(http:// only) -- '{display}' looks like a SOCKS or HTTPS proxy")
    headers = {}
    if parsed.username:
        username = urllib.parse.unquote(parsed.username)
        credentials = username
        if parsed.password is not None:
            credentials = f"{username}:{urllib.parse.unquote(parsed.password)}"
        headers["Proxy-Authorization"] = "Basic " + base64.b64encode(credentials.encode()).decode()
    return _Proxy(host=parsed.hostname, port=parsed.port or 80, headers=headers, display=display)


class _KeepAlivePool:
    """Reuses one http.client connection per thread instead of opening a fresh
    TCP+TLS connection for every request (what a plain urllib opener does).

    Exposes the same `.open(req, timeout=...)` interface as a urllib opener, so
    Client.request()'s ApiError translation (HTTPError/URLError -> ApiError) is
    unchanged. `connection_class` is injectable for tests; it defaults to
    http.client.HTTPSConnection/HTTPConnection picked from the base URL's scheme.

    Honors HTTP_PROXY/HTTPS_PROXY/NO_PROXY: an https target tunnels through the
    proxy with CONNECT (TLS stays end-to-end to the target host); an http target
    is requested with an absolute-URI and Proxy-Authorization, per RFC 7230 §5.3.2.
    """

    def __init__(self, base_url: str, connection_class=None):
        parsed = urllib.parse.urlsplit(base_url)
        self._host = parsed.hostname
        self._port = parsed.port
        self._scheme = parsed.scheme
        self._connection_class = connection_class or _CONNECTION_CLASSES[parsed.scheme]
        self._local = threading.local()
        self._proxy = _resolve_proxy(parsed.scheme, parsed.hostname, parsed.port)

    @property
    def proxy_display(self):
        return self._proxy.display if self._proxy else None

    def _connection(self, timeout):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._open_connection(timeout)
            self._local.conn = conn
        return conn

    def _open_connection(self, timeout):
        if self._proxy is None:
            return self._connection_class(self._host, self._port, timeout=timeout)
        conn = self._connection_class(self._proxy.host, self._proxy.port, timeout=timeout)
        if self._scheme == "https":
            conn.set_tunnel(self._host, self._port or 443, headers=self._proxy.headers or None)
        return conn

    def _drop_connection(self):
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
        self._local.conn = None

    def open(self, req, timeout=None):
        method = req.get_method()
        try:
            return self._send(method, req, timeout)
        except urllib.error.HTTPError:
            # A real HTTP response (even 4xx/5xx) is not a connection failure — HTTPError
            # is an OSError subclass, so it must be excluded from the clause below or a
            # legitimate 404 gets retried and its status code destroyed.
            raise
        except (OSError, http.client.HTTPException) as err:
            self._drop_connection()
            if method not in _RETRIABLE_METHODS:
                raise urllib.error.URLError(err) from err
            try:
                return self._send(method, req, timeout)
            except (OSError, http.client.HTTPException) as retry_err:
                self._drop_connection()
                raise urllib.error.URLError(retry_err) from retry_err

    def _send(self, method, req, timeout):
        conn = self._connection(timeout)
        headers = dict(req.header_items())
        path = req.selector
        if self._proxy is not None and self._scheme != "https":
            path = req.full_url
            headers.update(self._proxy.headers)
        conn.request(method, path, body=req.data, headers=headers)
        resp = conn.getresponse()
        if resp.status >= 400:
            body = resp.read()
            raise urllib.error.HTTPError(req.full_url, resp.status, resp.reason,
                                         resp.headers, io.BytesIO(body))
        return resp


class Client:
    def __init__(self, config, verbose: bool = False, jobs: int = DEFAULT_JOBS):
        self.base_url = config.base_url
        self.verbose = verbose
        self.jobs = jobs
        credentials = f"{config.basic_username}:{config.basic_password}".encode()
        self._headers = {
            "X-API-KEY": config.api_key,
            "Authorization": "Basic " + base64.b64encode(credentials).decode(),
            "User-Agent": USER_AGENT,
        }
        self._opener = _KeepAlivePool(self.base_url)
        if self._opener.proxy_display:
            self._trace(f"-> via proxy {self._opener.proxy_display}")

    def _trace(self, message: str) -> None:
        if not self.verbose:
            return
        print(message, file=sys.stderr)

    def request(self, method: str, path: str, body=None):
        if method not in ALLOWED_METHODS:
            raise ValueError(f"method {method} is not allowed")
        url = self.base_url + path
        headers = dict(self._headers)
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        self._trace(f"-> {method} {url} (X-API-KEY: <redacted>, Authorization: <redacted>)")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=TIMEOUT) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")
            raise ApiError(method, url, err.code, detail) from None
        except urllib.error.URLError as err:
            raise ApiError(method, url, 0, str(err.reason)) from None
        self._trace(f"<- {len(payload)} bytes")
        if not payload:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            # A 200 that is not JSON means something in front of Metabase answered
            # (proxy login/error page); surface it as an ApiError, not a stray traceback.
            body = payload.decode("utf-8", "replace")
            raise ApiError(method, url, 200, body[:500]) from None

    def get(self, path: str):
        return self.request("GET", path)

    def get_or_none(self, path: str):
        try:
            return self.request("GET", path)
        except ApiError as err:
            if err.status == 404:
                return None
            raise

    def post(self, path: str, body):
        return self.request("POST", path, body)

    def put(self, path: str, body):
        return self.request("PUT", path, body)

    def get_many(self, paths):
        """GET every path; results come back in input order. jobs<=1, or a single
        path, runs inline. On failure, the ApiError raised is always the one for
        the first-failing path by input index, not by completion time."""
        return self._map(self.get, paths)

    def get_many_or_none(self, paths):
        """Like get_many, but a 404 for any path maps to None instead of raising."""
        return self._map(self.get_or_none, paths)

    def _map(self, fn, paths):
        paths = list(paths)
        if self.jobs <= 1 or len(paths) <= 1:
            return [fn(p) for p in paths]
        with ThreadPoolExecutor(max_workers=self.jobs) as pool:
            futures = [pool.submit(fn, p) for p in paths]
            try:
                return [f.result() for f in futures]
            except BaseException:
                for f in futures:
                    f.cancel()
                raise
