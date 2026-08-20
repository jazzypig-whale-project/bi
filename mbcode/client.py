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


class _KeepAlivePool:
    """Reuses one http.client connection per thread instead of opening a fresh
    TCP+TLS connection for every request (what a plain urllib opener does).

    Exposes the same `.open(req, timeout=...)` interface as a urllib opener, so
    Client.request()'s ApiError translation (HTTPError/URLError -> ApiError) is
    unchanged. `connection_class` is injectable for tests; it defaults to
    http.client.HTTPSConnection/HTTPConnection picked from the base URL's scheme.
    """

    def __init__(self, base_url: str, connection_class=None):
        parsed = urllib.parse.urlsplit(base_url)
        self._host = parsed.hostname
        self._port = parsed.port
        self._connection_class = connection_class or _CONNECTION_CLASSES[parsed.scheme]
        self._local = threading.local()

    def _connection(self, timeout):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connection_class(self._host, self._port, timeout=timeout)
            self._local.conn = conn
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
        path = req.selector
        conn = self._connection(timeout)
        conn.request(method, path, body=req.data, headers=dict(req.header_items()))
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
