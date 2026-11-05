"""Small HTTP client with a TTL cache (memory + /tmp) for serverless functions.

Vercel functions keep memory only while an instance is warm and only /tmp is
writable, so both layers are best-effort. The cache also keeps us polite with
free data sources: the same file is not downloaded again before its TTL.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import USER_AGENT

CACHE_DIR = Path(os.environ.get("CQE_CACHE_DIR") or Path(tempfile.gettempdir()) / "cqe-cache")
_memory: dict[str, tuple[float, "Fetched"]] = {}
_lock = threading.Lock()


class ProviderError(RuntimeError):
    """A provider could not deliver data (network, quota, auth, format)."""


@dataclass
class Fetched:
    url: str            # URL without secrets
    body: bytes
    status: int
    headers: dict
    fetched_at: float   # epoch seconds
    from_cache: bool = False


def _key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:32]


def _disk_get(key: str, ttl: float):
    meta = CACHE_DIR / f"{key}.json"
    body = CACHE_DIR / f"{key}.bin"
    try:
        info = json.loads(meta.read_text())
        if time.time() - info["fetched_at"] > ttl:
            return None
        return Fetched(info["url"], body.read_bytes(), info["status"], info["headers"], info["fetched_at"], True)
    except (OSError, ValueError, KeyError):
        return None


def _disk_put(key: str, item: Fetched) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / f"{key}.bin").write_bytes(item.body)
        (CACHE_DIR / f"{key}.json").write_text(json.dumps(
            {"url": item.url, "status": item.status, "headers": item.headers, "fetched_at": item.fetched_at}))
    except OSError:
        pass


def fetch(url: str, *, ttl: float, safe_url: str | None = None, max_bytes: int = 5_000_000,
          timeout: float = 15, headers: dict | None = None) -> Fetched:
    """GET ``url`` with caching. ``safe_url`` (without API keys) is what we log/show."""
    shown = safe_url or url
    key = _key(url)
    now = time.time()
    with _lock:
        hit = _memory.get(key)
    if hit and now - hit[0] <= ttl:
        item = hit[1]
        return Fetched(item.url, item.body, item.status, item.headers, item.fetched_at, True)
    disk = _disk_get(key, ttl)
    if disk:
        with _lock:
            _memory[key] = (disk.fetched_at, disk)
        return disk
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*", **(headers or {})})
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read(max_bytes + 1)
            status = resp.status
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(300).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - best effort detail
            pass
        if exc.code == 429:
            raise ProviderError(f"Limite de requisições da fonte atingido (HTTP 429) em {shown}.") from exc
        if exc.code in (401, 403):
            raise ProviderError(f"Acesso negado pela fonte (HTTP {exc.code}) em {shown}. Confira a chave/plano.") from exc
        raise ProviderError(f"HTTP {exc.code} em {shown}. {detail[:160]}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ProviderError(f"Falha de rede ao acessar {shown}: {exc}") from exc
    if len(body) > max_bytes:
        raise ProviderError(f"Resposta maior que o limite ({max_bytes // 1000} KB) em {shown}.")
    item = Fetched(shown, body, status, hdrs, time.time())
    with _lock:
        if len(_memory) > 400:
            _memory.clear()
        _memory[key] = (item.fetched_at, item)
    _disk_put(key, item)
    return item


def clear_cache() -> None:
    with _lock:
        _memory.clear()
