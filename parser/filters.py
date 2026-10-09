from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from telethon.tl import types
from telethon.tl.types import (
    StarGiftAttributeBackdrop,
    StarGiftAttributeModel,
    StarGiftAttributePattern,
    StarGiftUnique,
)


@dataclass
class AttributeChoice:
    kind: str
    name: str
    document_id: int | None = None
    backdrop_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "document_id": self.document_id,
            "backdrop_id": self.backdrop_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AttributeChoice:
        return cls(
            kind=data["kind"],
            name=data["name"],
            document_id=data.get("document_id"),
            backdrop_id=data.get("backdrop_id"),
        )

    def to_api_id(self) -> types.TypeStarGiftAttributeId:
        if self.kind == "model":
            return types.StarGiftAttributeIdModel(document_id=self.document_id)
        if self.kind == "pattern":
            return types.StarGiftAttributeIdPattern(document_id=self.document_id)
        if self.kind == "backdrop":
            return types.StarGiftAttributeIdBackdrop(backdrop_id=self.backdrop_id)
        raise ValueError(f"Unsupported API attribute: {self.kind}")


def _join_names(names: list[str]) -> str:
    if not names:
        return "любой"
    if len(names) <= 3:
        return ", ".join(names)
    return f"{names[0]} +{len(names) - 1}"


@dataclass
class WatchFilter:
    gift_id: int
    gift_title: str
    models: list[AttributeChoice]
    backdrops: list[AttributeChoice] = field(default_factory=list)
    patterns: list[AttributeChoice] = field(default_factory=list)

    def label(self) -> str:
        return (
            f"{self.gift_title} · "
            f"{_join_names([m.name for m in self.models])} · "
            f"{_join_names([b.name for b in self.backdrops])} · "
            f"{_join_names([p.name for p in self.patterns])}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gift_id": self.gift_id,
            "gift_title": self.gift_title,
            "models": [m.to_dict() for m in self.models],
            "backdrops": [b.to_dict() for b in self.backdrops],
            "patterns": [p.to_dict() for p in self.patterns],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WatchFilter:
        return cls(
            gift_id=data["gift_id"],
            gift_title=data.get("gift_title") or "",
            models=[AttributeChoice.from_dict(m) for m in data.get("models") or []],
            backdrops=[AttributeChoice.from_dict(b) for b in data.get("backdrops") or []],
            patterns=[AttributeChoice.from_dict(p) for p in data.get("patterns") or []],
        )


def _gift_has_model(gift: StarGiftUnique, models: list[AttributeChoice]) -> bool:
    if not models:
        return True
    for attr in gift.attributes or []:
        if not isinstance(attr, StarGiftAttributeModel):
            continue
        doc_id = getattr(attr.document, "id", None)
        for model in models:
            if attr.name == model.name or doc_id == model.document_id:
                return True
    return False


def _gift_has_backdrop(gift: StarGiftUnique, backdrops: list[AttributeChoice]) -> bool:
    if not backdrops:
        return True
    for attr in gift.attributes or []:
        if not isinstance(attr, StarGiftAttributeBackdrop):
            continue
        for choice in backdrops:
            if attr.name == choice.name or attr.backdrop_id == choice.backdrop_id:
                return True
    return False


def _gift_has_pattern(gift: StarGiftUnique, patterns: list[AttributeChoice]) -> bool:
    if not patterns:
        return True
    for attr in gift.attributes or []:
        if not isinstance(attr, StarGiftAttributePattern):
            continue
        doc_id = getattr(attr.document, "id", None)
        for choice in patterns:
            if attr.name == choice.name or doc_id == choice.document_id:
                return True
    return False


def gift_matches_watch(gift: StarGiftUnique, watch: WatchFilter) -> bool:
    return (
        _gift_has_model(gift, watch.models)
        and _gift_has_backdrop(gift, watch.backdrops)
        and _gift_has_pattern(gift, watch.patterns)
    )


def _single_api_choice(choices: list[AttributeChoice]) -> AttributeChoice | None:
    if len(choices) != 1:
        return None
    choice = choices[0]
    if choice.kind == "backdrop":
        if choice.backdrop_id is None:
            return None
    elif choice.document_id is None:
        return None
    return choice


def watch_api_attributes(watch: WatchFilter) -> list | None:
    attrs: list[types.TypeStarGiftAttributeId] = []
    for choices in (watch.models, watch.backdrops, watch.patterns):
        choice = _single_api_choice(choices)
        if choice:
            attrs.append(choice.to_api_id())
    return attrs or None


def watch_api_filter_label(watch: WatchFilter) -> str | None:
    parts: list[str] = []
    for kind, choices in (
        ("model", watch.models),
        ("backdrop", watch.backdrops),
        ("pattern", watch.patterns),
    ):
        choice = _single_api_choice(choices)
        if choice:
            parts.append(f"{kind}={choice.name}")
    return ", ".join(parts) if parts else None
