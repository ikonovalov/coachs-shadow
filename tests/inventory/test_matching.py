import pytest

from bot.inventory.matching import (
    Candidate, MatchStatus, Profile, Wanted, missing_profile_fields, own_shoes_problem, set_problems, suggest,
)

COMPAT = {("none", "flat"), ("spd", "spd"), ("look", "look"), ("spd_sl", "spd_sl")}


def bike(id, frame, lo, hi):
    return Candidate(id=id, kind="bike", label=f"Велосипед №{id}", frame=frame, height_min=lo, height_max=hi)


def shoes(id, size, cleat):
    return Candidate(id=id, kind="shoes", label=f"Туфли №{id}", shoe_eu=size, cleat_type=cleat)


def pedals(id, kind):
    return Candidate(id=id, kind="pedals", label=f"Педали №{id}", pedal_type=kind)


def helmet(id, size):
    return Candidate(id=id, kind="helmet", label=f"Шлем №{id}", helmet_size=size)


def one(kinds, profile, items, pedal_type=None):
    result = suggest(profile, Wanted(frozenset(kinds), pedal_type=pedal_type), items, COMPAT)
    assert len(result) == 1
    return result[0]


# ---- велосипед ----

def test_bike_exact_by_height_range_lowest_id_wins():
    s = one({"bike"}, Profile(172, None, None), [bike(2, "M", 165, 178), bike(1, "M", 165, 178), bike(3, "L", 178, 190)])
    assert (s.status, s.item_id) == (MatchStatus.EXACT, 1)


def test_bike_nearest_when_no_range_fits_with_explanation():
    s = one({"bike"}, Profile(195, None, None), [bike(1, "M", 165, 178), bike(2, "L", 178, 190)])
    assert (s.status, s.item_id) == (MatchStatus.NEAREST, 2)
    assert "195" in s.text and "178" in s.text and "190" in s.text


def test_bike_none_when_park_is_empty():
    s = one({"bike"}, Profile(172, None, None), [])
    assert (s.status, s.item_id) == (MatchStatus.NONE, None)
    assert "нет в наличии" in s.text.lower() and "тренер" in s.text.lower()


# ---- педали ----

def test_pedals_by_type_only():
    s = one({"pedals"}, Profile(None, None, None), [pedals(1, "spd"), pedals(2, "look")], pedal_type="look")
    assert (s.status, s.item_id) == (MatchStatus.EXACT, 2)
    s = one({"pedals"}, Profile(None, None, None), [pedals(1, "spd")], pedal_type="look")
    assert s.status == MatchStatus.NONE


# ---- туфли ----

def test_shoes_exact_size_and_compatible_cleats_with_chosen_pedals():
    items = [shoes(1, 42, "look"), shoes(2, 42, "spd")]
    s = one({"shoes"}, Profile(None, 42, None), items, pedal_type="spd")
    assert (s.status, s.item_id) == (MatchStatus.EXACT, 2)


def test_shoes_nearest_is_plus_minus_one_prefers_larger():
    items = [shoes(1, 41, "spd"), shoes(2, 43, "spd")]
    s = one({"shoes"}, Profile(None, 42, None), items, pedal_type="spd")
    assert (s.status, s.item_id) == (MatchStatus.NEAREST, 2)
    assert "42" in s.text


def test_shoes_two_sizes_off_is_not_a_match():
    s = one({"shoes"}, Profile(None, 42, None), [shoes(1, 44, "spd")], pedal_type="spd")
    assert s.status == MatchStatus.NONE


def test_shoes_without_pedal_choice_ignore_cleat_type():
    s = one({"shoes"}, Profile(None, 42, None), [shoes(1, 42, "look")])
    assert s.status == MatchStatus.EXACT


# ---- шлем ----

def test_helmet_exact_nearest_and_tie_prefers_larger():
    assert one({"helmet"}, Profile(None, None, "M"), [helmet(1, "S"), helmet(2, "M")]).item_id == 2
    s = one({"helmet"}, Profile(None, None, "M"), [helmet(1, "S"), helmet(2, "L")])
    assert (s.status, s.item_id) == (MatchStatus.NEAREST, 2)
    s = one({"helmet"}, Profile(None, None, "S"), [helmet(1, "L")])
    assert (s.status, s.item_id) == (MatchStatus.NEAREST, 1)
    assert one({"helmet"}, Profile(None, None, "M"), []).status == MatchStatus.NONE


# ---- общее ----

def test_only_wanted_kinds_in_fixed_order():
    items = [bike(1, "M", 160, 180), helmet(2, "M")]
    result = suggest(Profile(170, None, "M"), Wanted(frozenset({"helmet", "bike"})), items, COMPAT)
    assert [s.kind for s in result] == ["bike", "helmet"]


def test_missing_profile_fields_only_for_selected_kinds():
    empty = Profile(None, None, None)
    assert missing_profile_fields(empty, {"pedals"}) == []
    assert missing_profile_fields(empty, {"bike", "helmet"}) == ["height_cm", "helmet_size"]
    assert missing_profile_fields(Profile(170, None, None), {"bike", "shoes"}) == ["foot_eu"]


def test_own_shoes_problem():
    assert own_shoes_problem("spd", "spd", COMPAT) is None
    assert own_shoes_problem("spd", None, COMPAT) is None
    assert own_shoes_problem(None, "spd", COMPAT) is None
    message = own_shoes_problem("spd_sl", "spd", COMPAT)
    assert "SPD-SL" in message and "SPD" in message


def test_set_problems_flags_incompatible_park_pedals_and_shoes():
    assert set_problems([pedals(1, "spd"), shoes(2, 42, "spd")], COMPAT) == []
    problems = set_problems([pedals(1, "look"), shoes(2, 42, "spd")], COMPAT)
    assert len(problems) == 1 and "Педали №1" in problems[0] and "Туфли №2" in problems[0]
    assert set_problems([bike(1, "M", 1, 2), shoes(2, 42, "spd")], COMPAT) == []
