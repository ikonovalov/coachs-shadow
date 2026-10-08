from aiogram.fsm.state import State, StatesGroup


class NewTraining(StatesGroup):
    kind = State()
    place = State()
    levels = State()
    weekdays = State()
    date = State()
    time = State()
    end_date = State()
    capacity = State()
    description = State()
    confirm = State()


class EditField(StatesGroup):
    value = State()  # data: target ("series"|"training"), id, field, text (после ввода, до подтверждения)


class Picker(StatesGroup):
    query = State()  # data: purpose, ctx, roles


class AdminInput(StatesGroup):
    value = State()  # data: purpose ("add_level" | "setting:<key>")


class ProfileInput(StatesGroup):
    value = State()  # data: field ("height_cm" | "foot_eu")


class InventoryFlow(StatesGroup):
    kinds = State()
    pedal = State()
    shoes = State()
    cleat = State()
    profile = State()  # текстовый ввод роста/размера ноги; data: ask
    helmet = State()
    review = State()


class ParkInput(StatesGroup):
    line = State()  # data: kind


class PlanForm(StatesGroup):
    line = State()  # одна строка: Название; занятий; цена; срок
    series = State()  # выбор серий; data: draft (dict), picked (list[int])


class PriceInput(StatesGroup):
    value = State()  # data: target ("type" | "series"), key (kind или id серии)
