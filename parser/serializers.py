from __future__ import annotations

import re
from typing import Any

_USERNAME_RE = re.compile(r"^[a-zA-Z][\w\d]{4,31}$")

from telethon.tl.types import (
    PeerUser,
    StarGiftAttributeBackdrop,
    StarGiftAttributeModel,
    StarGiftAttributeOriginalDetails,
    StarGiftAttributePattern,
    StarGiftUnique,
    StarsAmount,
    StarsTonAmount,
)


def _parse_resell_amounts(
    amounts: list[Any] | None,
) -> tuple[int | None, int | None]:
    stars: int | None = None
    ton: int | None = None
    for amount in amounts or []:
        if isinstance(amount, StarsTonAmount):
            ton = amount.amount
        elif isinstance(amount, StarsAmount):
            stars = amount.amount
        else:
            val = getattr(amount, "amount", None)
            if val is not None and ton is None and stars is None:
                stars = val
    return stars, ton


def _attribute_line(attr: Any) -> str:
    if isinstance(attr, StarGiftAttributeModel):
        return f"model:{attr.name}"
    if isinstance(attr, StarGiftAttributePattern):
        return f"pattern:{attr.name}"
    if isinstance(attr, StarGiftAttributeBackdrop):
        return f"backdrop:{attr.name}"
    if isinstance(attr, StarGiftAttributeOriginalDetails):
        return "original_details"
    return type(attr).__name__


def format_ton_amount(amount: int) -> str:
    ton = amount / 1_000_000_000
    if abs(ton - round(ton)) < 1e-9:
        return f"{int(round(ton)):,}"
    text = f"{ton:.2f}".rstrip("0").rstrip(".")
    whole, _, frac = text.partition(".")
    return f"{int(whole):,}.{frac}" if frac else f"{int(whole):,}"


def is_ton_listing(row: dict) -> bool:
    if row.get("resale_ton_only"):
        return True
    if row.get("currency") == "ton":
        return True
    return row.get("resell_ton") is not None and row.get("resell_stars") is None


def owner_user_id_from_gift(gift: StarGiftUnique) -> int | None:
    peer = getattr(gift, "owner_id", None)
    if isinstance(peer, PeerUser):
        return peer.user_id
    return None


def normalize_owner_username(owner_name: str | None) -> str | None:
    if not owner_name:
        return None
    name = str(owner_name).strip().lstrip("@")
    if not name or not _USERNAME_RE.match(name):
        return None
    return name


def unique_listing_to_dict(gift: StarGiftUnique) -> dict[str, Any] | None:
    slug = getattr(gift, "slug", None)
    if not slug:
        return None

    resell_amount = getattr(gift, "resell_amount", None)
    if not resell_amount:
        return None

    resell_stars, resell_ton = _parse_resell_amounts(
        resell_amount if isinstance(resell_amount, list) else [resell_amount]
    )
    if resell_stars is None and resell_ton is None:
        return None

    currency = "ton" if resell_ton is not None and resell_stars is None else "stars"
    if resell_stars is not None and resell_ton is not None:
        currency = "mixed"

    return {
        "telegram_id": gift.id,
        "gift_id": gift.gift_id,
        "title": gift.title or "",
        "slug": slug,
        "num": gift.num,
        "resell_stars": resell_stars,
        "resell_ton": resell_ton,
        "currency": currency,
        "resale_ton_only": bool(getattr(gift, "resale_ton_only", False)),
        "owner_name": normalize_owner_username(getattr(gift, "owner_name", None)),
        "owner_user_id": owner_user_id_from_gift(gift),
        "attributes_json": [
            _attribute_line(a) for a in (getattr(gift, "attributes", None) or [])
        ],
    }
