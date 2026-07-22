"""Logic rieng cua Booking.com: vuot WAF, tim kiem, lay chi tiet khach san.

Cach hoat dong (da reverse-engineer, khac han Agoda vi Booking.com co AWS WAF JS-challenge):
  1. Bootstrap: mo 1 phien Playwright headless de trinh duyet tu giai JS-challenge, lay
     cookie tu browser context. Cookie nay TAI SU DUNG DUOC voi httpx thuan cho cac
     request sau (da xac nhan: khong can mo lai trinh duyet cho moi request).
  2. Tim kiem: GET /searchresults.html?ss=<tu khoa> -> HTML co nhung 1 khoi JSON la
     Apollo GraphQL cache (da normalize). Dung chung 1 co che cho ca tim ten khach san
     LAN tim theo vung/thanh pho - Booking khong phan biet 2 loai nay o cung 1 endpoint.
     GIOI HAN DA BIET: trang chi tra ve toi da ~25 khach san/lan tai (nbResultsPerPage=25);
     co the co hang nghin ket qua (nbResultsTotal) nhung co che phan trang that (client-side
     GraphQL, offset trong query variables) chua reverse-engineer duoc - tam thoi CHI lay
     duoc trang dau (toi da 25 khach san moi vung/tu khoa).
  3. Chi tiet 1 khach san: GET /hotel/<country>/<slug>.html -> cung ky thuat tach Apollo
     cache -> BasicPropertyData, PropertyReview, RoomData, RatingScore (category_scores
     nam o ROOT_QUERY.reviewsFrontend(...).ratingScores[], xem extract_category_scores)...
     GIOI HAN DA BIET: gia phong thuc te theo ngay (`checkin`/`checkout` query param)
     CHUA lay duoc - RoomTableQueryResult.roomCards tra ve rong ngay ca khi truyen du
     checkin/checkout/group_adults/no_rooms. Co the can 1 co che client-side khac chua
     tim ra. Cac truong gia (price/currency/rooms_available/room_offers) tam de None/rong.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from playwright.async_api import async_playwright

from .cache_parser import extract_apollo_cache, find_all, find_first, resolve_list, resolve_ref

BASE = "https://www.booking.com"
SEARCH_URL = f"{BASE}/searchresults.html"
CDN_BASE = "https://cf.bstatic.com"

# Dau hieu nhan biet trang van con bi AWS WAF JS-challenge chan (chua co cookie hop le).
_CHALLENGE_MARKER = "awsWafCookieDomainList"
# Tran an toan: so khach san toi da lay moi vung/tu khoa khi max_items=0 (khong gioi han).
_SEARCH_SAFETY_CAP = 200
# Booking.com hien chi xac nhan tra ve toi da 1 trang ket qua (~25) - xem docstring module.
_SEARCH_PAGE_SIZE = 25

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
BASE_HEADERS = {
    "User-Agent": _UA,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class BookingError(Exception):
    """Loi khi goi Booking.com cho 1 khach san (khong lam hong ca run)."""


class BookingClient:
    """Bao boc httpx.AsyncClient + 1 phien Playwright dung 1 lan de vuot WAF."""

    def __init__(
        self,
        proxy_url: str | None = None,
        language: str = "en-us",
        currency: str = "USD",
        timeout: float = 40.0,
    ) -> None:
        self.language = (language or "en-us").lower()
        self.currency = (currency or "USD").upper()
        self._client = httpx.AsyncClient(
            headers=BASE_HEADERS,
            timeout=timeout,
            follow_redirects=True,
            proxy=proxy_url,
        )
        self._bootstrapped = False

    async def __aenter__(self) -> "BookingClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._client.aclose()

    # --- 0) Vuot WAF ---------------------------------------------------------
    async def bootstrap(self) -> None:
        """Mo 1 phien Playwright headless de vuot AWS WAF JS-challenge, lay cookie.

        Cookie duoc ap vao httpx client va tai su dung cho cac request sau. Goi lai
        ham nay neu 1 request httpx bi phat hien van con challenge (xem _get()).
        """
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(locale="en-US", user_agent=_UA)
            page = await context.new_page()
            try:
                await page.goto(BASE, wait_until="networkidle", timeout=45000)
            except Exception:  # noqa: BLE001 - co the timeout do tracking script, cookie WAF thuong da co
                pass
            await page.wait_for_timeout(2000)
            cookies = await context.cookies()
            await browser.close()

        for c in cookies:
            self._client.cookies.set(c["name"], c["value"], domain=c.get("domain") or "", path=c.get("path") or "/")
        self._bootstrapped = True

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """GET co tu dong bootstrap lan dau + thu lai 1 lan neu phat hien con bi challenge."""
        if not self._bootstrapped:
            await self.bootstrap()
        resp = await self._client.get(url, params=params)
        if _looks_like_challenge(resp):
            await self.bootstrap()
            resp = await self._client.get(url, params=params)
        return resp

    # --- 1) Tim kiem (dung chung cho ten khach san va vung/thanh pho) --------
    async def search(self, term: str, max_items: int) -> list[dict[str, Any]]:
        """Tim khach san theo tu khoa (ten cu the hoac ten vung/thanh pho).

        CHI lay duoc trang dau (toi da _SEARCH_PAGE_SIZE khach san) - xem gioi han
        o docstring dau file. max_items > _SEARCH_PAGE_SIZE se bi cat bot kem canh bao.
        """
        params = {"ss": term, "lang": self.language, "selected_currency": self.currency}
        try:
            resp = await self._get(SEARCH_URL, params=params)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise BookingError(f"Tim kiem that bai cho '{term}': {exc}") from exc

        cache = extract_apollo_cache(resp.text)
        if not cache:
            raise BookingError(f"Khong tach duoc du lieu tim kiem cho '{term}' (co the van bi WAF chan)")

        results = _extract_search_results(cache)
        limit = min(max_items, _SEARCH_PAGE_SIZE) if max_items > 0 else _SEARCH_PAGE_SIZE
        return results[:limit]

    # --- 1b) URL search Booking.com -> tach tu khoa (fallback khi can) -------
    async def search_from_url(self, url: str, max_items: int) -> list[dict[str, Any]]:
        """Neu URL la trang search Booking.com, tach tham so ss= roi goi search()."""
        qs = parse_qs(urlparse(url).query)
        term = (qs.get("ss") or [None])[0]
        if not term:
            raise BookingError(f"URL khong co tham so 'ss=' de xac dinh tu khoa tim kiem: {url}")
        return await self.search(term, max_items)

    # --- 2) Chi tiet 1 khach san ---------------------------------------------
    async def fetch_hotel(
        self,
        property_url: str,
        search_criteria: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Lay du lieu 1 khach san tu URL trang chi tiet, tra ve record chuan.

        search_criteria (tuy chon): {"check_in","check_out","adults","rooms"} - duoc gan
        vao query string (checkin/checkout/group_adults/no_rooms) khi goi trang, nhung
        HIEN CHUA xac nhan duoc co gia phong that tra ve (xem gioi han o dau file) - cac
        truong gia trong record se de None cho toi khi nghien cuu them.
        """
        params: dict[str, Any] = {"lang": self.language, "selected_currency": self.currency}
        if search_criteria:
            params.update(
                {
                    "checkin": search_criteria["check_in"],
                    "checkout": search_criteria["check_out"],
                    "group_adults": search_criteria.get("adults", 2),
                    "no_rooms": search_criteria.get("rooms", 1),
                    "group_children": 0,
                }
            )

        try:
            resp = await self._get(property_url, params=params)
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise BookingError(f"Khong lay duoc khach san tu {property_url}: {exc}") from exc

        cache = extract_apollo_cache(resp.text)
        if not cache:
            raise BookingError(f"Khong tach duoc du lieu khach san tu {property_url} (co the van bi WAF chan)")

        return _map_hotel(cache, property_url, search_criteria)


def _looks_like_challenge(resp: httpx.Response) -> bool:
    """True neu response co dau hieu van con bi AWS WAF JS-challenge chan."""
    if resp.status_code == 202:
        return True
    ct = resp.headers.get("content-type", "")
    if "text/html" in ct and len(resp.text) < 20000 and _CHALLENGE_MARKER in resp.text:
        return True
    return False


# --- Ham thuan (pure) --------------------------------------------------------

def is_url(text: str) -> bool:
    """True neu chuoi la 1 URL (bat dau bang http:// hoac https://)."""
    return bool(text) and text.strip().lower().startswith(("http://", "https://"))


def is_search_url(url: str) -> bool:
    """True neu URL la trang search Booking.com (khong phai trang khach san)."""
    return "/searchresults" in url


def _extract_search_results(cache: dict[str, Any]) -> list[dict[str, Any]]:
    """Tach danh sach khach san tu ROOT_QUERY.searchQueries."search(...)".results[]."""
    root = cache.get("ROOT_QUERY") or {}
    search_queries = root.get("searchQueries") or {}
    search_key = next((k for k in search_queries if k.startswith("search(")), None)
    if not search_key:
        return []
    raw_results = (search_queries.get(search_key) or {}).get("results") or []

    out: list[dict[str, Any]] = []
    for item in raw_results:
        bpd = item.get("basicPropertyData") or {}
        hotel_id = bpd.get("id")
        page_name = bpd.get("pageName")
        country_code = ((bpd.get("location") or {}).get("countryCode")) or ""
        if not hotel_id or not page_name:
            continue
        property_url = f"{BASE}/hotel/{country_code}/{page_name}.html"
        display_name = (item.get("displayName") or {}).get("text")
        out.append(
            {
                "hotel_id": hotel_id,
                "hotel_name": display_name or bpd.get("name"),
                "property_url": property_url,
            }
        )
    return out


def extract_category_scores(cache: dict[str, Any]) -> dict[str, float]:
    """Diem review theo hang muc, lay tu ROOT_QUERY.reviewsFrontend(...).ratingScores[]
    (moi item: {name, translation, value}). Lam tron 1 chu so thap phan cho khop cach
    Booking.com tu hien thi (vd 9.4).
    """
    root = cache.get("ROOT_QUERY") or {}
    key = next((k for k in root if k.startswith("reviewsFrontend(")), None)
    if not key:
        return {}
    rating_scores = (root.get(key) or {}).get("ratingScores") or []

    scores: dict[str, float] = {}
    for item in rating_scores:
        name = item.get("translation")
        value = item.get("value")
        if name and value is not None:
            scores[name] = round(float(value), 1)
    return scores


def _resolve_facility_name(cache: dict[str, Any], facility_ref: Any) -> str | None:
    """RoomData.amenities[] -> BaseFacility -> instances[0] -> Instance.title."""
    facility = resolve_ref(cache, facility_ref)
    if not isinstance(facility, dict):
        return None
    instances = resolve_list(cache, facility.get("instances"))
    if instances and isinstance(instances[0], dict):
        return instances[0].get("title")
    return None


def _extract_rooms(cache: dict[str, Any]) -> list[dict[str, Any]]:
    """Danh sach phong co ban (ten, tien nghi, anh) tu RoomData - KHONG co gia (xem
    gioi han o dau file: RoomTableQueryResult.roomCards luon rong trong lan kiem tra).
    """
    rooms: list[dict[str, Any]] = []
    for room in find_all(cache, "RoomData"):
        translations = room.get("translations") or {}
        amenities = [
            name
            for ref in room.get("amenities") or []
            if (name := _resolve_facility_name(cache, ref))
        ]
        photos = resolve_list(cache, room.get("roomPhotos"))
        images = [_abs_image_url(p.get("photoUri")) for p in photos if isinstance(p, dict) and p.get("photoUri")]
        rooms.append(
            {
                "name": translations.get("name"),
                "room_id": room.get("id"),
                "amenities": amenities,
                "image_count": len(images),
                "images": images,
                # Chua xac nhan duoc gia/tinh trang phong that - xem docstring dau file.
                "price_per_night": None,
                "currency": None,
                "sold_out": None,
            }
        )
    return rooms


def _abs_image_url(uri: str | None) -> str | None:
    if not uri:
        return None
    if uri.startswith("http"):
        return uri
    return CDN_BASE + uri


def _map_hotel(
    cache: dict[str, Any],
    property_url: str,
    search_criteria: dict[str, Any] | None,
) -> dict[str, Any]:
    """Map Apollo cache -> record chuan."""
    warnings: list[str] = []

    basic = find_first(cache, "BasicPropertyData") or {}
    location = basic.get("location") or {}
    review = find_first(cache, "PropertyReview") or {}
    total_score = review.get("totalScore") or {}
    property_type = find_first(cache, "PropertyType") or {}

    hotel_id = basic.get("id")
    lat = location.get("latitude")
    lng = location.get("longitude")

    rooms = _extract_rooms(cache)

    record: dict[str, Any] = {
        "hotel_id": hotel_id,
        "hotel_name": basic.get("name"),
        "accommodation_type": property_type.get("type"),
        "address": location.get("formattedAddress"),
        "city": location.get("city"),
        "country": location.get("countryCode"),
        "review_score": _coerce_float(total_score.get("score")),
        "review_count": _coerce_int(total_score.get("reviewsCount")),
        "category_scores": extract_category_scores(cache),
        # Truong gia: CHUA xac nhan duoc co che lay gia that cho Booking.com (xem
        # docstring dau file) - de None/rong cho toi khi nghien cuu them.
        "price": None,
        "currency": None,
        "rooms_available": None,
        "check_in": search_criteria.get("check_in") if search_criteria else None,
        "check_out": search_criteria.get("check_out") if search_criteria else None,
        "room_types": [r["name"] for r in rooms if r.get("name")],
        "rooms": rooms,
        "coordinates": _format_coordinates(lat, lng),
        "property_url": property_url,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }

    if record["hotel_name"] is None:
        warnings.append("missing_name")
    if record["coordinates"] is None:
        warnings.append("missing_geo")
    record["warnings"] = warnings
    return record


def _format_coordinates(lat: Any, lng: Any) -> str | None:
    lat_f = _coerce_float(lat)
    lng_f = _coerce_float(lng)
    if lat_f is None or lng_f is None:
        return None
    return f"{lat_f},{lng_f}"


def _coerce_int(val: Any) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _coerce_float(val: Any) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
