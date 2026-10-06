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
