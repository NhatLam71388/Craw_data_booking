"""Dieu phoi actor: doc input -> gom property_url -> lay du lieu -> push_data."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from typing import Any

from apify import Actor

from .booking import BASE, BookingClient, BookingError, is_search_url, is_url

_BARE_NUMERIC_ID = re.compile(r"^\d+$")


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}

        search_terms: list[str] = actor_input.get("searchTerms") or []
        property_urls: list[str] = actor_input.get("propertyUrls") or []
        raw_hotel_ids: list[Any] = actor_input.get("hotelIds") or []
        locations: list[str] = actor_input.get("locations") or []
        max_items_per_location: int = int(actor_input.get("maxItemsPerLocation") or 50)
        max_items: int = int(actor_input.get("maxItems") or 0)
        request_delay: float = float(actor_input.get("requestDelay") or 2)
        language: str = actor_input.get("language") or "en-us"
        currency: str = actor_input.get("currency") or "USD"

        global_criteria = _build_global_criteria(actor_input)

        proxy_cfg = await Actor.create_proxy_configuration(
            actor_proxy_input=actor_input.get("proxyConfiguration")
        )
        proxy_url = await proxy_cfg.new_url() if proxy_cfg else None

        async with BookingClient(proxy_url=proxy_url, language=language, currency=currency) as client:
            Actor.log.info("Dang vuot qua kiem tra bao mat cua Booking.com (bootstrap)...")
            await client.bootstrap()
            Actor.log.info("Da san sang, bat dau thu thap du lieu.")

            # Gom tat ca property_url can crawl (kem search_criteria rieng neu co) - de-dup.
            targets: dict[str, dict[str, Any] | None] = {}

            # 1) hotelIds -> Booking.com CAN slug (vd "vn/the-chum-boutique"), khong ho
            # tro tim theo ID so don thuan nhu Agoda (khong co co che redirect tuong duong).
            for hid in raw_hotel_ids:
                hid_str = str(hid).strip().strip("/")
                if _BARE_NUMERIC_ID.match(hid_str):
                    Actor.log.warning(
                        f"Booking.com khong ho tro tim theo ID so don thuan ('{hid_str}'). "
                        "Can dang '<ma_quoc_gia>/<slug>' (vd 'vn/the-chum-boutique') hoac dung propertyUrls."
                    )
                    continue
                targets.setdefault(f"{BASE}/hotel/{hid_str}.html", None)

            # 2) propertyUrls -> dung truc tiep, tach search_criteria tu chinh URL neu co.
            for url in property_urls:
                targets.setdefault(url, None)

            # 3) searchTerms -> tim ten khach san cu the qua search().
            for term in search_terms:
                try:
                    candidates = await client.search(term, max_items=5)
                except BookingError as exc:
                    Actor.log.warning(str(exc))
                    continue
                if not candidates:
                    Actor.log.warning(f"Khong tim thay khach san cho tu khoa: '{term}'")
                for cand in candidates:
                    targets.setdefault(cand["property_url"], None)

            # 4) locations -> tim theo vung/thanh pho (ten hoac link search Booking.com).
            # GIOI HAN: chi lay duoc toi da ~25 khach san/vung (xem booking.py).
            for loc in locations:
                try:
                    if is_url(loc) and is_search_url(loc):
                        candidates = await client.search_from_url(loc, max_items_per_location)
                    else:
                        candidates = await client.search(loc, max_items_per_location)
                except BookingError as exc:
                    Actor.log.warning(str(exc))
                    continue
                if len(candidates) < max_items_per_location:
                    Actor.log.info(
                        f"Vung '{loc}': tim thay {len(candidates)} khach san "
                        f"(da dat gioi han hien tai ~25 khach san/vung cua Booking.com)"
                    )
                else:
                    Actor.log.info(f"Vung '{loc}': tim thay {len(candidates)} khach san")
                for cand in candidates:
                    targets.setdefault(cand["property_url"], None)

            if not targets:
                Actor.log.warning("Khong co khach san nao de crawl. Kiem tra lai input.")
                return

            Actor.log.info(f"Tong so khach san se crawl: {len(targets)}")

            pushed = 0
            for i, (property_url, url_criteria) in enumerate(targets.items()):
                if max_items and pushed >= max_items:
                    Actor.log.info(f"Da dat gioi han maxItems={max_items}, dung lai.")
                    break
                try:
                    search_criteria = url_criteria or global_criteria
                    record = await client.fetch_hotel(property_url, search_criteria)
                    await Actor.push_data(record)
                    pushed += 1
                    Actor.log.info(f"[{pushed}] Da luu khach san: {record.get('hotel_name') or property_url}")
                except BookingError as exc:
                    Actor.log.warning(str(exc))
                except Exception as exc:  # noqa: BLE001 - 1 loi khong lam hong ca run
                    Actor.log.exception(f"Loi khong mong doi voi {property_url}: {exc}")

                if request_delay and i < len(targets) - 1:
                    await asyncio.sleep(request_delay)

            Actor.log.info(f"Hoan tat. Tong ban ghi da luu: {pushed}")


def _build_global_criteria(actor_input: dict[str, Any]) -> dict[str, Any] | None:
    """Tieu chi ngay/khach mac dinh cho khach san khong co san checkin rieng.

    Luu y: hien Booking.com CHUA xac nhan tra ve gia phong thuc te (xem booking.py) -
    tieu chi nay van duoc gui kem request de san sang khi co the lay gia trong tuong lai.
    """
    check_in = actor_input.get("checkIn")
    if not check_in:
        return None
    length_of_stay = int(actor_input.get("lengthOfStay") or 1)
    try:
        check_out = (datetime.strptime(check_in, "%Y-%m-%d") + timedelta(days=max(length_of_stay, 1))).strftime(
            "%Y-%m-%d"
        )
    except ValueError:
        Actor.log.warning(f"checkIn khong dung dinh dang YYYY-MM-DD: '{check_in}', bo qua.")
        return None
    return {
        "check_in": check_in,
        "check_out": check_out,
        "adults": int(actor_input.get("adults") or 2),
        "rooms": int(actor_input.get("rooms") or 1),
    }
