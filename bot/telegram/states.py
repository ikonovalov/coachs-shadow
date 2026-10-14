from aiogram.fsm.state import State, StatesGroup


class NewTraining(StatesGroup):
    """Шаги создания серии или разовой тренировки."""
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
    """Ввод нового значения поля тренировки или серии."""
    value = State()  # data: target ("series"|"training"), id, field, text (после ввода, до подтверждения)


class Picker(StatesGroup):
    """Выбор пользователя по части имени."""
    query = State()  # data: purpose, ctx, roles


class AdminInput(StatesGroup):
    """Текстовый ввод в меню администратора (название уровня, значение настройки)."""
    value = State()  # data: purpose ("add_level" | "setting:<key>")


class ProfileInput(StatesGroup):
    """Ввод числового поля профиля."""
    value = State()  # data: field ("height_cm" | "foot_eu")


class InventoryFlow(StatesGroup):
    """Шаги заказа инвентаря участником."""
    kinds = State()
    pedal = State()
    shoes = State()
    cleat = State()
    profile = State()  # текстовый ввод роста/размера ноги; data: ask
    helmet = State()
    review = State()


class ParkInput(StatesGroup):
    """Ввод новой единицы парка одной строкой."""
    line = State()  # data: kind
