"""Durable, key-free price watching for public product pages.

The watcher reads structured price metadata from an explicitly supplied URL.
It deliberately rejects local/private network destinations and caps downloads,
so the model cannot turn it into an unrestricted request proxy.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

from jarvis_logger import get_logger

log = get_logger(__name__)

_WATCHES_FILE = Path(__file__).resolve().parent.parent / "memory" / "price_watches.json"
_MAX_BODY_BYTES = 2_000_000
_MAX_REDIRECTS = 5
_lock = threading.Lock()


@dataclass
class PriceWatch:
    id: str
    label: str
    url: str
    target_price: float | None
    currency: str = ""
    last_price: float | None = None
    last_checked: float | None = None
    last_error: str = ""
    last_notified_price: float | None = None
    target_notified: bool = False
    created_at: float = 0.0


class _PriceMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: list[tuple[str, str]] = []
        self.json_ld: list[str] = []
        self._in_json_ld = False
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag.lower() == "meta":
            key = values.get("property") or values.get("name") or values.get("itemprop")
            content = values.get("content", "")
            if key and content:
                self.meta.append((key.lower(), content.strip()))
        if tag.lower() == "script" and "ld+json" in values.get("type", "").lower():
            self._in_json_ld = True
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_json_ld:
            self.json_ld.append("".join(self._script_parts))
            self._in_json_ld = False


def _amount(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value) if float(value) >= 0 else None
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"[^0-9.,]", "", value).replace(",", "")
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return number if number >= 0 else None


def _walk_price(node: Any) -> tuple[float, str] | None:
    if isinstance(node, list):
        for child in node:
            found = _walk_price(child)
            if found:
                return found
    elif isinstance(node, dict):
        offers = node.get("offers")
        if offers is not None:
            found = _walk_price(offers)
            if found:
                return found
        if "price" in node:
            value = _amount(node.get("price"))
            if value is not None:
                return value, str(node.get("priceCurrency") or "")
        for key in ("lowPrice", "highPrice"):
            value = _amount(node.get(key))
            if value is not None:
                return value, str(node.get("priceCurrency") or "")
        for value in node.values():
            found = _walk_price(value)
            if found:
                return found
    return None


def extract_price(html: str) -> tuple[float, str]:
    """Extract a product price from JSON-LD or standard product meta tags."""
    parser = _PriceMetadataParser()
    parser.feed(html)
    for raw in parser.json_ld:
        try:
            found = _walk_price(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
        if found:
            return found

    meta = dict(parser.meta)
    for key in ("product:price:amount", "og:price:amount", "price"):
        value = _amount(meta.get(key))
        if value is not None:
            currency = (
                meta.get("product:price:currency")
                or meta.get("og:price:currency")
                or meta.get("pricecurrency")
                or ""
            )
            return value, currency
    raise ValueError("No structured product price was found on the page.")


def _validate_public_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Price watch URLs must be public http or https pages.")
    if parsed.username or parsed.password:
        raise ValueError("Price watch URLs cannot contain credentials.")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".local"):
        raise ValueError("Local network addresses cannot be watched.")
    try:
        addresses = socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("The product site could not be resolved.") from exc
    for result in addresses:
        address = ipaddress.ip_address(result[4][0].split("%", 1)[0])
        if not address.is_global:
            raise ValueError("Local or private network addresses cannot be watched.")


def _fetch_price(url: str) -> tuple[float, str]:
    current = url
    timeout = float(os.getenv("PRICE_WATCH_TIMEOUT_S", "15") or 15)
    headers = {"User-Agent": "JARVIS-Price-Watch/1.0 (+personal assistant)"}
    for _ in range(_MAX_REDIRECTS + 1):
        _validate_public_url(current)
        response = requests.get(
            current, headers=headers, timeout=timeout, allow_redirects=False, stream=True
        )
        if 300 <= response.status_code < 400 and response.headers.get("Location"):
            current = urljoin(current, response.headers["Location"])
            response.close()
            continue
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "text/html").lower()
        if not any(kind in content_type for kind in ("html", "xhtml", "text/plain")):
            response.close()
            raise ValueError("The watched URL did not return a web page.")
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(65_536):
            size += len(chunk)
            if size > _MAX_BODY_BYTES:
                response.close()
                raise ValueError("The product page was too large to inspect safely.")
            chunks.append(chunk)
        encoding = response.encoding or "utf-8"
        response.close()
        return extract_price(b"".join(chunks).decode(encoding, errors="replace"))
    raise ValueError("The product page redirected too many times.")


def _load() -> list[PriceWatch]:
    if not _WATCHES_FILE.exists():
        return []
    try:
        raw = json.loads(_WATCHES_FILE.read_text(encoding="utf-8"))
        return [PriceWatch(**item) for item in raw.get("watches", [])]
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        log.warning("Could not load price watches: %s", exc)
        return []


def _save(items: list[PriceWatch]) -> None:
    _WATCHES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _WATCHES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"watches": [asdict(item) for item in items]}, indent=2), encoding="utf-8")
    tmp.replace(_WATCHES_FILE)


def add_price_watch(url: str, label: str = "", target_price: float | None = None) -> PriceWatch:
    url = (url or "").strip()
    _validate_public_url(url)
    if target_price is not None and float(target_price) < 0:
        raise ValueError("Target price cannot be negative.")
    item = PriceWatch(
        id="p" + uuid.uuid4().hex[:7],
        label=(label or "Product").strip(),
        url=url,
        target_price=float(target_price) if target_price is not None else None,
        created_at=time.time(),
    )
    try:
        item.last_price, item.currency = _fetch_price(url)
        item.last_checked = time.time()
        item.target_notified = item.target_price is not None and item.last_price <= item.target_price
    except (requests.RequestException, ValueError) as exc:
        item.last_error = str(exc)
        item.last_checked = time.time()
    with _lock:
        items = _load()
        items.append(item)
        _save(items)
    return item


def remove_price_watch(watch_id: str) -> bool:
    with _lock:
        items = _load()
        kept = [item for item in items if item.id != watch_id]
        if len(kept) == len(items):
            return False
        _save(kept)
    return True


def price_watches() -> list[PriceWatch]:
    with _lock:
        return _load()


def check_price_watches() -> list[dict[str, Any]]:
    """Refresh all watches and return newly interesting price transitions."""
    notices: list[dict[str, Any]] = []
    drop_percent = float(os.getenv("PRICE_WATCH_DROP_PERCENT", "5") or 5)
    with _lock:
        items = _load()
        for item in items:
            previous = item.last_price
            try:
                current, currency = _fetch_price(item.url)
                item.last_price = current
                item.currency = currency or item.currency
                item.last_error = ""
                if item.target_price is not None and current > item.target_price:
                    item.target_notified = False
                target_hit = (
                    item.target_price is not None
                    and current <= item.target_price
                    and not item.target_notified
                )
                dropped = previous is not None and current <= previous * (1 - drop_percent / 100)
                already_notified = item.last_notified_price == current
                if (target_hit or dropped) and not already_notified:
                    if target_hit:
                        item.target_notified = True
                    item.last_notified_price = current
                    notices.append({
                        "id": item.id,
                        "label": item.label,
                        "price": current,
                        "currency": item.currency,
                        "reason": "target" if target_hit else "drop",
                    })
            except (requests.RequestException, ValueError) as exc:
                item.last_error = str(exc)
            item.last_checked = time.time()
        _save(items)
    return notices


def describe_price_watches() -> str:
    items = price_watches()
    if not items:
        return "You are not watching any prices, sir."
    parts = []
    for item in items:
        current = "price unavailable" if item.last_price is None else f"{item.currency} {item.last_price:,.2f}".strip()
        target = "" if item.target_price is None else f", target {item.target_price:,.2f}"
        parts.append(f"({item.id}) {item.label}: {current}{target}")
    return "Price watches: " + "; ".join(parts) + "."
