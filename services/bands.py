from __future__ import annotations

from parser.serializers import format_ton_amount, is_ton_listing
from config import PriceBand, Settings
from services.gift_floors import gift_floor_ton


OTHER_BAND_KEY = "other"
NANOTON = 1_000_000_000


def ton_nano_to_stars(ton_nano: int, stars_per_ton: float) -> float:
    return (float(ton_nano) / NANOTON) * float(stars_per_ton)


def match_band(listing: dict, settings: Settings) -> PriceBand | None:
    value = gift_floor_ton(listing.get("title"))
    if value is None:
        return None

    for band in settings.price_bands:
        if band.currency != "ton":
            continue
        if value < band.min_value:
            continue
        if band.max_value is not None and value >= band.max_value:
            continue
        return band
    return None


def band_key_for_listing(listing: dict, settings: Settings) -> str:
    band = match_band(listing, settings)
    return band.key if band else OTHER_BAND_KEY


def price_label(listing: dict, settings: Settings | None = None) -> str:
    stars = listing.get("resell_stars")
    ton_nano = listing.get("resell_ton")

    if is_ton_listing(listing) and ton_nano is not None:
        ton_txt = format_ton_amount(int(ton_nano))
        if settings is not None:
            eq = ton_nano_to_stars(int(ton_nano), settings.stars_per_ton)
            return f"{ton_txt} TON ≈ {eq:,.0f} ⭐"
        return f"{ton_txt} TON"

    if stars is not None and ton_nano is not None:
        ton_txt = format_ton_amount(int(ton_nano))
        return f"{int(stars):,} ⭐ / {ton_txt} TON"

    if stars is not None:
        return f"{int(stars):,} ⭐"
    return "?"
