"""Ham thuan: tach + parse Apollo GraphQL cache nhung san trong HTML cua Booking.com.

Booking.com nhung toan bo cache Apollo (da normalize kieu "Type:id" -> object, hoac
{"__ref": "Type:id"} tro sang entry khac) vao 1 the:
  <script type="application/json" data-capla-application-context="..." ...>{...}</script>
Xuat hien tren CA trang ket qua tim kiem (/searchresults.html) LAN trang chi tiet khach san
(/hotel/<country>/<slug>.html). Day la nguon du lieu chinh, khong can goi API rieng.
"""

from __future__ import annotations

import json
import re
from typing import Any

_JSON_SCRIPT_RE = re.compile(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', re.DOTALL)


def extract_apollo_cache(html: str) -> dict[str, Any] | None:
    """Tim va parse khoi JSON chua ROOT_QUERY (Apollo cache) trong 1 trang HTML.

    Tra None neu khong tim thay (vd trang van dang bi WAF challenge chan).
    """
    for match in _JSON_SCRIPT_RE.finditer(html):
        raw = match.group(1)
        if '"ROOT_QUERY"' not in raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "ROOT_QUERY" in data:
            return data
    return None


def resolve_ref(cache: dict[str, Any], value: Any) -> Any:
    """Neu value la {"__ref": "Type:id"}, tra ve entry tuong ung trong cache; nguoc lai giu nguyen."""
    if isinstance(value, dict) and set(value.keys()) == {"__ref"}:
        return cache.get(value["__ref"])
    return value


def resolve_list(cache: dict[str, Any], values: list[Any] | None) -> list[Any]:
    """Ap dung resolve_ref cho tung phan tu 1 list, bo qua None sau khi resolve."""
    if not values:
        return []
    resolved = [resolve_ref(cache, v) for v in values]
    return [v for v in resolved if v is not None]


def find_first(cache: dict[str, Any], type_prefix: str) -> dict[str, Any] | None:
    """Tim entry dau tien trong cache co key bat dau bang "TypePrefix:" hoac == TypePrefix."""
    for key, value in cache.items():
        if key == type_prefix or key.startswith(type_prefix + ":"):
            return value
    return None


def find_all(cache: dict[str, Any], type_prefix: str) -> list[dict[str, Any]]:
    """Tim tat ca entry trong cache co key bat dau bang "TypePrefix:"."""
    return [v for k, v in cache.items() if k.startswith(type_prefix + ":")]
