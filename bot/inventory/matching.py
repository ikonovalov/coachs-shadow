from dataclasses import dataclass

from bot.inventory.models import ItemKind
from bot.users.profile import CLEAT_LABELS, HELMET_SIZES

PEDAL_TYPES = ("flat", "spd", "look", "spd_sl")
PEDAL_LABELS = {"flat": "плоские", "spd": "SPD", "look": "Look", "spd_sl": "SPD-SL"}
KIND_LABELS = {"bike": "Велосипед", "pedals": "Педали", "shoes": "Туфли", "helmet": "Шлем"}
_NO_STOCK = "нет в наличии на это время — напишите тренеру"


class MatchStatus:
    EXACT = "exact"
    NEAREST = "nearest"
    NONE = "none"


@dataclass(frozen=True)
class Candidate:
    id: int
    kind: str
    label: str
    frame: str | None = None
    height_min: int | None = None
    height_max: int | None = None
    shoe_eu: int | None = None
    cleat_type: str | None = None
    pedal_type: str | None = None
    helmet_size: str | None = None


@dataclass(frozen=True)
class Profile:
    height_cm: int | None
    foot_eu: int | None
    helmet_size: str | None


@dataclass(frozen=True)
class Wanted:
    kinds: frozenset[str]  # позиции из парка; свои туфли сюда не входят
    pedal_type: str | None = None
    own_shoes_cleat: str | None = None


@dataclass(frozen=True)
class Suggestion:
    kind: str
    status: str
    item_id: int | None
    text: str


def suggest(profile: Profile, wanted: Wanted, candidates: list[Candidate], compat: set[tuple[str, str]]) -> list[Suggestion]:
    by_kind = {kind: [c for c in candidates if c.kind == kind] for kind in ItemKind.ALL}
    result: list[Suggestion] = []
    if ItemKind.BIKE in wanted.kinds:
        result.append(_bike(profile.height_cm, by_kind[ItemKind.BIKE]))
    if ItemKind.PEDALS in wanted.kinds:
        result.append(_pedals(wanted.pedal_type, by_kind[ItemKind.PEDALS]))
    if ItemKind.SHOES in wanted.kinds:
        result.append(_shoes(profile.foot_eu, wanted.pedal_type, by_kind[ItemKind.SHOES], compat))
    if ItemKind.HELMET in wanted.kinds:
        result.append(_helmet(profile.helmet_size, by_kind[ItemKind.HELMET]))
    return result


def _bike(height: int, items: list[Candidate]) -> Suggestion:
    pool = [i for i in items if i.height_min is not None and i.height_max is not None]
    exact = sorted((i for i in pool if i.height_min <= height <= i.height_max), key=lambda i: i.id)
    if exact:
        item = exact[0]
        return Suggestion(ItemKind.BIKE, MatchStatus.EXACT, item.id, f"🚲 {item.label} (рама {item.frame})")
    if pool:
        item = min(pool, key=lambda i: (min(abs(height - i.height_min), abs(height - i.height_max)), i.id))
        return Suggestion(
            ItemKind.BIKE, MatchStatus.NEAREST, item.id,
            f"🚲 {item.label}: рама {item.frame} рассчитана на рост {item.height_min}–{item.height_max} см, "
            f"ваш рост {height} см — ближайшая из свободных",
        )
    return Suggestion(ItemKind.BIKE, MatchStatus.NONE, None, f"🚲 Велосипед: {_NO_STOCK}")


def _pedals(pedal_type: str | None, items: list[Candidate]) -> Suggestion:
    matching = sorted((i for i in items if i.pedal_type == pedal_type), key=lambda i: i.id)
    if matching:
        item = matching[0]
        return Suggestion(ItemKind.PEDALS, MatchStatus.EXACT, item.id, f"🦶 {item.label} ({PEDAL_LABELS[item.pedal_type]})")
    label = PEDAL_LABELS.get(pedal_type, "")
    return Suggestion(ItemKind.PEDALS, MatchStatus.NONE, None, f"🦶 Педали {label}: {_NO_STOCK}")


def _shoes(foot: int, pedal_type: str | None, items: list[Candidate], compat: set[tuple[str, str]]) -> Suggestion:
    pool = [
        i for i in items
        if i.shoe_eu is not None and (pedal_type is None or (i.cleat_type, pedal_type) in compat)
    ]
    exact = sorted((i for i in pool if i.shoe_eu == foot), key=lambda i: i.id)
    if exact:
        item = exact[0]
        return Suggestion(ItemKind.SHOES, MatchStatus.EXACT, item.id, f"👟 {item.label}, размер {item.shoe_eu}")
    near = [i for i in pool if abs(i.shoe_eu - foot) == 1]
    if near:
        item = min(near, key=lambda i: (-i.shoe_eu, i.id))  # при равенстве лучше чуть больше
        return Suggestion(
            ItemKind.SHOES, MatchStatus.NEAREST, item.id,
            f"👟 {item.label}: размер {item.shoe_eu} (ваш {foot}) — ближайший из свободных",
        )
    return Suggestion(ItemKind.SHOES, MatchStatus.NONE, None, f"👟 Туфли: {_NO_STOCK}")


def _helmet(size: str, items: list[Candidate]) -> Suggestion:
    pool = [i for i in items if i.helmet_size in HELMET_SIZES]
    want = HELMET_SIZES.index(size)

    def key(item: Candidate):
        idx = HELMET_SIZES.index(item.helmet_size)
        return (abs(idx - want), -idx, item.id)

    if pool:
        item = min(pool, key=key)
        if item.helmet_size == size:
            return Suggestion(ItemKind.HELMET, MatchStatus.EXACT, item.id, f"⛑ {item.label}, размер {size}")
        return Suggestion(
            ItemKind.HELMET, MatchStatus.NEAREST, item.id,
            f"⛑ {item.label}: размер {item.helmet_size} (ваш {size}) — ближайший из свободных",
        )
    return Suggestion(ItemKind.HELMET, MatchStatus.NONE, None, f"⛑ Шлем: {_NO_STOCK}")


def missing_profile_fields(profile: Profile, kinds: set[str] | frozenset[str]) -> list[str]:
    missing: list[str] = []
    if ItemKind.BIKE in kinds and profile.height_cm is None:
        missing.append("height_cm")
    if ItemKind.SHOES in kinds and profile.foot_eu is None:
        missing.append("foot_eu")
    if ItemKind.HELMET in kinds and profile.helmet_size is None:
        missing.append("helmet_size")
    return missing


def own_shoes_problem(own_cleat: str | None, pedal_type: str | None, compat: set[tuple[str, str]]) -> str | None:
    if own_cleat is None or pedal_type is None or (own_cleat, pedal_type) in compat:
        return None
    return (
        f"Ваши туфли ({CLEAT_LABELS[own_cleat]}) не подходят к педалям {PEDAL_LABELS[pedal_type]}."
    )


def set_problems(items: list[Candidate], compat: set[tuple[str, str]]) -> list[str]:
    pedals = next((i for i in items if i.kind == ItemKind.PEDALS), None)
    shoes = next((i for i in items if i.kind == ItemKind.SHOES), None)
    if pedals is not None and shoes is not None and (shoes.cleat_type, pedals.pedal_type) not in compat:
        return [f"«{shoes.label}» не подходят к «{pedals.label}»"]
    return []
