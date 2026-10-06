from collections import Counter
from dataclasses import dataclass

from bot.users.profile import HELMET_SIZES

FRAME_ORDER = ["XS", "S", "M", "L", "XL", "XXL"]
_PLURAL = {"bike": "велосипеды", "pedals": "педали", "shoes": "туфли", "helmet": "шлем"}
_SINGULAR = {"bike": "велосипед", "pedals": "педали", "shoes": "туфли", "helmet": "шлем"}
_ORDER = ["bike", "pedals", "shoes", "helmet"]


@dataclass(frozen=True)
class OrderLine:
    user_id: int
    user_name: str
    kind: str
    label: str
    frame: str | None = None
    shoe_eu: int | None = None
    pedal_type: str | None = None
    helmet_size: str | None = None


def _size(line: OrderLine):
    if line.kind == "bike":
        return line.frame
    if line.kind == "pedals":
        return line.pedal_type
    if line.kind == "shoes":
        return line.shoe_eu
    return line.helmet_size


def _sort_key(kind: str, size):
    if kind == "bike":
        return (FRAME_ORDER.index(size) if size in FRAME_ORDER else len(FRAME_ORDER), str(size))
    if kind == "shoes":
        return (size, "")
    if kind == "helmet":
        return (HELMET_SIZES.index(size) if size in HELMET_SIZES else len(HELMET_SIZES), str(size))
    return (0, str(size))


def summarize(lines: list[OrderLine]) -> str:
    parts: list[str] = []
    for kind in _ORDER:
        counts = Counter(_size(l) for l in lines if l.kind == kind)
        if not counts:
            continue
        sizes = sorted(counts, key=lambda s: _sort_key(kind, s))
        parts.append(f"{_PLURAL[kind]} " + ", ".join(f"{s}×{counts[s]}" for s in sizes))
    return "; ".join(parts) if parts else "Заказов нет"


def breakdown(lines: list[OrderLine]) -> str:
    by_user: dict[int, list[OrderLine]] = {}  # по id: однофамильцы не сливаются в одну строку
    for line in lines:
        by_user.setdefault(line.user_id, []).append(line)
    rows = []
    for items in by_user.values():
        items.sort(key=lambda l: _ORDER.index(l.kind))
        rows.append(f"{items[0].user_name}: " + ", ".join(f"{_SINGULAR[l.kind]} {_size(l)} ({l.label})" for l in items))
    return "\n".join(rows)
