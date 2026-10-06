from dataclasses import dataclass

from bot.inventory.matching import KIND_LABELS, MatchStatus, Suggestion

_KIND_ORDER = ["bike", "pedals", "shoes", "helmet"]


@dataclass(frozen=True)
class MyEntry:
    title: str
    status: str  # «идёте» / «не идёте»
    orders: list[str]


def kinds_markup_data(selected: set[str]) -> list[tuple[str, str]]:
    rows = [(("✅ " if kind in selected else "▫️ ") + KIND_LABELS[kind], f"iv:k:{kind}") for kind in _KIND_ORDER]
    rows.append(("Готово", "iv:kdone"))
    return rows


def can_confirm(suggestions: list[Suggestion]) -> bool:
    return any(s.item_id is not None for s in suggestions)


def format_review(suggestions: list[Suggestion], confirmed_labels: list[str], replacement: bool) -> str:
    lines: list[str] = []
    if replacement:
        lines.append("Эту единицу только что забрали — предлагаю замену:")
    else:
        lines.append("Подобрал:")
    lines += [s.text for s in suggestions]
    if any(s.status == MatchStatus.NEAREST for s in suggestions):
        lines.append("\nЧасть позиций — ближайшие по параметрам, проверьте.")
    if confirmed_labels:
        lines.append("\nУже заказано: " + ", ".join(confirmed_labels))
    if can_confirm(suggestions):
        lines.append("\nПодтвердить заказ?")
    return "\n".join(lines)


def render_my(entries: list[MyEntry]) -> str:
    if not entries:
        return "Записей пока нет. Расписание: /schedule"
    blocks = []
    for entry in entries:
        block = f"• {entry.title} — {entry.status}"
        if entry.orders:
            block += "\n   🚲 " + ", ".join(entry.orders)
        blocks.append(block)
    return "Мои тренировки:\n" + "\n".join(blocks)
