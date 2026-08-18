"""HTTP client for the Metabase API. GET/POST/PUT only — DELETE does not exist here."""
from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request

TIMEOUT = 30
USER_AGENT = "mbcode/1.0"
ALLOWED_METHODS = ("GET", "POST", "PUT")


class ApiError(Exception):
    def __init__(self, method: str, url: str, status: int, body: str):
        self.method = method
        self.url = url
        self.status = status
        self.body = body[:500]
        super().__init__(f"{method} {url} -> HTTP {status}: {self.body}")


class Client:
    def __init__(self, config, verbose: bool = False):
        self.base_url = config.base_url
        self.verbose = verbose
        credentials = f"{config.basic_username}:{config.basic_password}".encode()
        self._headers = {
            "X-API-KEY": config.api_key,
            "Authorization": "Basic " + base64.b64encode(credentials).decode(),
            "User-Agent": USER_AGENT,
        }
        # Ignore proxy environment variables entirely.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

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
