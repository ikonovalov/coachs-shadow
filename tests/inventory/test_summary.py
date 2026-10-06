from bot.inventory.summary import OrderLine, breakdown, summarize


def line(user, kind, label, **kw):
    return OrderLine(user_name=user, kind=kind, label=label, **kw)


LINES = [
    line("Анна", "bike", "Велосипед №7", frame="M"),
    line("Анна", "helmet", "Шлем №2", helmet_size="M"),
    line("Борис", "bike", "Велосипед №8", frame="M"),
    line("Вера", "bike", "Велосипед №9", frame="L"),
    line("Борис", "shoes", "Туфли №5", shoe_eu=42),
    line("Вера", "shoes", "Туфли №6", shoe_eu=42),
    line("Вера", "pedals", "Педали №1", pedal_type="spd"),
    line("Борис", "pedals", "Педали №2", pedal_type="spd"),
]


def test_summary_groups_by_kind_and_size():
    assert summarize(LINES) == "велосипеды M×2, L×1; педали spd×2; туфли 42×2; шлем M×1"


def test_summary_empty():
    assert summarize([]) == "Заказов нет"


def test_summary_sorts_sizes_sensibly():
    mixed = [line("a", "bike", "в", frame="L"), line("b", "bike", "в", frame="S"), line("c", "shoes", "т", shoe_eu=44),
             line("d", "shoes", "т", shoe_eu=40)]
    assert summarize(mixed) == "велосипеды S×1, L×1; туфли 40×1, 44×1"


def test_breakdown_per_participant():
    text = breakdown(LINES)
    assert text.splitlines()[0] == "Анна: велосипед M (Велосипед №7), шлем M (Шлем №2)"
    assert "Борис: велосипед M (Велосипед №8), педали spd (Педали №2), туфли 42 (Туфли №5)" in text
    assert breakdown([]) == ""
