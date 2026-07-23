"""Dieu phoi actor: doc input -> gom property_url -> lay du lieu -> push_data."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from typing import Any

from apify import Actor

from .booking import (
    BASE,
    BookingClient,
    BookingError,
    extract_search_criteria_from_url,
    is_search_url,
    is_url,
)

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

            # Gom tat ca property_url can crawl (kem search_criteria + price_hint rieng
            # neu co) - de-dup theo URL.
            targets: dict[str, dict[str, Any]] = {}

            def _add_target(
                url: str,
                search_criteria: dict[str, Any] | None,
                price_hint: dict[str, Any] | None = None,
            ) -> None:
                targets.setdefault(url, {"search_criteria": search_criteria, "price_hint": price_hint})

            # 1) hotelIds -> Booking.com CAN slug (vd "vn/the-chum-boutique"), khong ho
            # tro tim theo ID so don thuan nhu Agoda (khong co co che redirect tuong duong).
            # KHONG di qua buoc tim kiem -> khong co gia (xem README).
            for hid in raw_hotel_ids:
                hid_str = str(hid).strip().strip("/")
                if _BARE_NUMERIC_ID.match(hid_str):
                    Actor.log.warning(
                        f"Booking.com khong ho tro tim theo ID so don thuan ('{hid_str}'). "
                        "Can dang '<ma_quoc_gia>/<slug>' (vd 'vn/the-chum-boutique') hoac dung propertyUrls."
                    )
                    continue
                _add_target(f"{BASE}/hotel/{hid_str}.html", global_criteria)

            # 2) propertyUrls -> dung truc tiep, tach search_criteria tu chinh URL neu co
            # (fallback global_criteria). KHONG di qua buoc tim kiem -> khong co gia.
            for url in property_urls:
                _add_target(url, extract_search_criteria_from_url(url) or global_criteria)

            # 3) searchTerms -> tim ten khach san cu the qua search(). Neu co
            # global_criteria, ket qua tim kiem da co san gia (price_hint).
            for term in search_terms:
                try:
                    candidates = await client.search(term, max_items=5, search_criteria=global_criteria)
                except BookingError as exc:
                    Actor.log.warning(str(exc))
                    continue
                if not candidates:
                    Actor.log.warning(f"Khong tim thay khach san cho tu khoa: '{term}'")
                for cand in candidates:
                    price_hint = (
                        {"price": cand["price"], "currency": cand["currency"]}
                        if cand.get("price") is not None
                        else None
                    )
                    _add_target(cand["property_url"], global_criteria, price_hint)

            # 4) locations -> tim theo vung/thanh pho (ten hoac link search Booking.com).
            # Neu la link co checkin/checkout, dung tieu chi do (uu tien hon global_criteria)
            # va ket qua da co san gia. search() tu gom nhieu "trang" (loc hang sao + sap xep
            # khac nhau) de vuot qua gioi han ~25/lan-tai cua Booking.com - xem booking.py.
            for loc in locations:
                loc_criteria = extract_search_criteria_from_url(loc) if is_url(loc) else None
                effective_criteria = loc_criteria or global_criteria
                try:
                    if is_url(loc) and is_search_url(loc):
                        candidates = await client.search_from_url(loc, max_items_per_location)
                    else:
                        candidates = await client.search(loc, max_items_per_location, search_criteria=effective_criteria)
                except BookingError as exc:
                    Actor.log.warning(str(exc))
                    continue
                if len(candidates) < max_items_per_location:
                    Actor.log.info(
                        f"Vung '{loc}': tim thay {len(candidates)} khach san "
                        "(da het khach san co the tim thay qua cac to hop loc/sap xep hien co)"
                    )
                else:
                    Actor.log.info(f"Vung '{loc}': tim thay {len(candidates)} khach san")
                for cand in candidates:
                    price_hint = (
                        {"price": cand["price"], "currency": cand["currency"]}
                        if cand.get("price") is not None
                        else None
                    )
                    _add_target(cand["property_url"], effective_criteria, price_hint)

            if not targets:
                Actor.log.warning("Khong co khach san nao de crawl. Kiem tra lai input.")
                return

            Actor.log.info(f"Tong so khach san se crawl: {len(targets)}")

            pushed = 0
            for i, (property_url, target_info) in enumerate(targets.items()):
                if max_items and pushed >= max_items:
                    Actor.log.info(f"Da dat gioi han maxItems={max_items}, dung lai.")
                    break
                try:
                    record = await client.fetch_hotel(
                        property_url,
                        target_info["search_criteria"],
                        target_info["price_hint"],
                    )
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

    Chi co gia thuc te (price_hint) cho searchTerms/locations (di qua buoc search()) -
    xem booking.py. propertyUrls/hotelIds van dung tieu chi nay de goi trang chi tiet
    (anh huong ngon ngu/hien thi) nhung KHONG co gia vi khong di qua buoc tim kiem.
    """
    check_in = actor_input.get("checkIn")
    if not check_in:
        return None
    try:
        datetime.strptime(check_in, "%Y-%m-%d")
    except ValueError:
        Actor.log.warning(f"checkIn khong dung dinh dang YYYY-MM-DD: '{check_in}', bo qua.")
        return None

    check_out = actor_input.get("checkOut")
    if check_out:
        try:
            datetime.strptime(check_out, "%Y-%m-%d")
        except ValueError:
            Actor.log.warning(f"checkOut khong dung dinh dang YYYY-MM-DD: '{check_out}', bo qua.")
            return None
    else:
        length_of_stay = int(actor_input.get("lengthOfStay") or 1)
        check_out = (datetime.strptime(check_in, "%Y-%m-%d") + timedelta(days=max(length_of_stay, 1))).strftime(
            "%Y-%m-%d"
        )
    return {
        "check_in": check_in,
        "check_out": check_out,
        "adults": int(actor_input.get("adults") or 2),
        "rooms": int(actor_input.get("rooms") or 1),
    }
