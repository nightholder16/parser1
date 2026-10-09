from __future__ import annotations

from datetime import datetime
import html

from config import Settings
from parser.serializers import is_ton_listing, normalize_owner_username


def _model_name(attrs: list[str] | None) -> str | None:
    for raw in attrs or []:
        if isinstance(raw, str) and raw.startswith("model:"):
            name = raw[6:].strip()
            if name:
                return name
    return None


def alert_price_label(row: dict) -> str:
    stars = row.get("resell_stars")
    ton_nano = row.get("resell_ton")

    if stars is not None and ton_nano is not None:
        return f"{int(stars)} Stars / {float(ton_nano) / 1_000_000_000:.2f} TON"
    if is_ton_listing(row) and ton_nano is not None:
        return f"{float(ton_nano) / 1_000_000_000:.2f} TON"
    if stars is not None:
        return f"{int(stars)} Stars"
    return "N/A"


def format_alert(row: dict) -> str:
    gift_name = row.get("title") or row.get("name") or "Gift"
    stars = int(row.get("resell_stars") or 0)
    ton_nano = row.get("resell_ton") or 0
    ton_price = f"{float(ton_nano) / 1_000_000_000:.2f}" if ton_nano else "0"

    model = _model_name(row.get("attrs")) or "—"
    level = row.get("level") or 1

    has_message = row.get("has_message") or False
    msg_status = "Paid" if row.get("is_paid") else ("Free" if not has_message else "Custom")

    has_premium = row.get("has_premium") or False
    prem_status = "Premium" if has_premium else "No premium"

    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    text = (
        "✨ <b>NEW GIFT LISTING :)</b>\n\n"
        f"🎁 <b>Gift:</b> {html.escape(str(gift_name))}\n"
        f"💎 <b>Price:</b> {stars} ⭐️ / {ton_price} TON\n"
        f"📝 <b>Model:</b> {html.escape(str(model))}\n"
        f"📊 <b>Level:</b> {level}\n"
        f"🗣 <b>Message:</b> {msg_status}\n"
        f"⚡️ <b>Status:</b> {prem_status}\n"
        f"⏱ {now_str}\n\n"
        "Made with love by <b>@night_holder</b>"
    )
    return text

