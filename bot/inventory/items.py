from bot.core.errors import InvalidAction
from bot.inventory.matching import PEDAL_TYPES
from bot.inventory.models import ItemKind
from bot.users.profile import CLEAT_TYPES, HELMET_SIZES

ITEM_FORMATS = {
    ItemKind.BIKE: "Велосипед №7; M; 165; 178  (метка; рама; рост от; рост до)",
    ItemKind.PEDALS: "Педали №3; spd  (метка; тип: flat, spd, look, spd_sl)",
    ItemKind.SHOES: "Туфли №5; 42; spd  (метка; размер EU; шипы: none, spd, look, spd_sl)",
    ItemKind.HELMET: "Шлем №2; M  (метка; размер S, M или L)",
}
_FIELDS = {ItemKind.BIKE: 4, ItemKind.PEDALS: 2, ItemKind.SHOES: 3, ItemKind.HELMET: 2}


def parse_item_line(kind: str, text: str) -> tuple[str, dict]:
    if kind not in ITEM_FORMATS:
        raise InvalidAction("Неизвестный вид инвентаря")
    error = InvalidAction(f"Формат: {ITEM_FORMATS[kind]}")
    parts = [p.strip() for p in text.split(";")]
    if len(parts) != _FIELDS[kind] or not parts[0]:
        raise error
    label = parts[0]
    try:
        if kind == ItemKind.BIKE:
            lo, hi = int(parts[2]), int(parts[3])
            if not parts[1] or lo > hi or lo < 100 or hi > 230:
                raise error
            return label, {"frame": parts[1].upper(), "height_min": lo, "height_max": hi}
        if kind == ItemKind.PEDALS:
            pedal = parts[1].lower()
            if pedal not in PEDAL_TYPES:
                raise error
            return label, {"pedal_type": pedal}
        if kind == ItemKind.SHOES:
            size, cleat = int(parts[1]), parts[2].lower()
            if not 30 <= size <= 52 or cleat not in CLEAT_TYPES:
                raise error
            return label, {"shoe_eu": size, "cleat_type": cleat}
        helmet = parts[1].upper()
        if helmet not in HELMET_SIZES:
            raise error
        return label, {"helmet_size": helmet}
    except ValueError:
        raise error from None
