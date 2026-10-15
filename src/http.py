"""Shared HTTP helper: one place for user agent, timeout and simple retries."""
from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)
_cfg = {"user_agent": "cti-brief/0.1", "timeout": 20.0}


def configure(user_agent: str | None = None, timeout: float | None = None) -> None:
    if user_agent:
        _cfg["user_agent"] = user_agent
    if timeout:
        _cfg["timeout"] = float(timeout)


def get(url: str, *, headers: dict | None = None, params: dict | None = None, retries: int = 2) -> httpx.Response:
    return _request("GET", url, headers=headers, params=params, retries=retries)


def post(url: str, *, json=None, headers: dict | None = None, cookies: dict | None = None,
         retries: int = 2) -> httpx.Response:
    return _request("POST", url, json=json, headers=headers, cookies=cookies, retries=retries)


def _request(method: str, url: str, *, headers: dict | None = None, retries: int = 2, **kwargs) -> httpx.Response:
    hdrs = {"User-Agent": _cfg["user_agent"], "Accept": "*/*"}
    hdrs.update(headers or {})
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = httpx.request(method, url, headers=hdrs, timeout=_cfg["timeout"], follow_redirects=True, **kwargs)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp
        except httpx.HTTPError as exc:
            last = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise last  # type: ignore[misc]
