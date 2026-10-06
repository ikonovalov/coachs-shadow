import pytest

from bot.core.errors import InvalidAction
from bot.inventory.items import parse_item_line


def test_each_kind_parses_into_label_and_params():
    assert parse_item_line("bike", "Велосипед №7; M; 165; 178") == (
        "Велосипед №7", {"frame": "M", "height_min": 165, "height_max": 178})
    assert parse_item_line("pedals", "Педали №3; SPD") == ("Педали №3", {"pedal_type": "spd"})
    assert parse_item_line("shoes", "Туфли №5; 42; spd_sl") == ("Туфли №5", {"shoe_eu": 42, "cleat_type": "spd_sl"})
    assert parse_item_line("helmet", "Шлем №2; m") == ("Шлем №2", {"helmet_size": "M"})


@pytest.mark.parametrize("kind,line", [
    ("bike", "Велосипед; M; 178; 165"),   # min > max
    ("bike", "Велосипед; M; 165"),        # мало полей
    ("bike", "; M; 165; 178"),            # пустая метка
    ("pedals", "Педали; round"),
    ("shoes", "Туфли; 7; spd"),           # размер вне диапазона
    ("shoes", "Туфли; 42; xyz"),
    ("helmet", "Шлем; XXL"),
    ("kayak", "Лодка; 1"),
])
def test_invalid_lines_are_rejected_with_a_format_hint(kind, line):
    with pytest.raises(InvalidAction):
        parse_item_line(kind, line)


def test_error_contains_example_format():
    with pytest.raises(InvalidAction, match="Велосипед №7; M; 165; 178"):
        parse_item_line("bike", "что-то")
