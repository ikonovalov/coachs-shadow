"""Operations that span several features. Telegram handlers call these, never the services directly
for state-changing actions that must also update posts."""
import logging
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.billing.models import Charge, Subscription
from bot.billing.render import render_new_charges
from bot.billing.service import AttendanceResult, BillingService
from bot.billing.views import BillingViews
from bot.core.messenger import Button, Keyboard, Messenger
from bot.core.money import format_money
from bot.core.permissions import Role
from bot.core.settings import billing_enabled, get_setting
from bot.inventory.models import InventoryItem, InventoryOrder
from bot.inventory.service import InventoryService
from bot.participation.models import ParticipationStatus
from bot.participation.service import JoinOutcome, ParticipationService
from bot.publishing.render import KIND_NAMES, format_when
from bot.publishing.service import PublishService
from bot.schedule.models import Training, TrainingStatus
from bot.schedule.service import ReplacedTraining, ScheduleService, SeriesInput, TrainingEdit
from bot.users.models import User

log = logging.getLogger(__name__)


async def _cancel_participant_orders(
    session: AsyncSession, messenger: Messenger, training_id: int, user_id: int
) -> None:
    """Участник больше не идёт: его заказы инвентаря отменяются, единицы сразу свободны."""
    orders = await InventoryService(session).cancel_for_participant(training_id, user_id)
    if not orders:
        return
    item_ids = [o.item_id for o in orders]
    labels = list(
        await session.scalars(
            select(InventoryItem.label).where(InventoryItem.id.in_(item_ids)).order_by(InventoryItem.id)
        )
    )
    log.info("cancelled %d inventory orders training=%s user=%s", len(orders), training_id, user_id)
    await _notify_quietly(messenger, user_id, "Заказы инвентаря на тренировку отменены: " + ", ".join(labels))


async def _notify_quietly(messenger: Messenger, user_id: int, text: str, keyboard: Keyboard | None = None) -> None:
    """Уведомление после уже закоммиченного изменения: сбой доставки логируется и не прерывает use case
    (обновление постов и остальные уведомления должны выполниться)."""
    try:
        delivered = await messenger.notify(user_id, text, keyboard)
    except Exception:
        log.warning("notify to %s failed", user_id, exc_info=True)
        return
    if not delivered:
        log.info("notice not delivered to %s", user_id)


async def set_item_active(
    session: AsyncSession, messenger: Messenger, actor: User, item_id: int, active: bool, now: datetime
) -> tuple[InventoryItem, list[InventoryOrder]]:
    """Вывод единицы из парка / возврат. При выводе её активные заказы на будущие тренировки отменяются,
    каждому участнику приходит уведомление. Возврат больше ничего не меняет. Права (только админ) — в сервисе."""
    inventory = InventoryService(session)
    item = await inventory.set_active(actor, item_id, active)
    if active:
        return item, []
    # новые заказы на выведенную единицу уже невозможны (_take_item проверяет active), отменяем существующие
    cancelled = await inventory.cancel_for_item(actor, item_id, now)
    log.info("item %s deactivated by %s, cancelled orders %s", item_id, actor.telegram_id, [o.id for o in cancelled])
    if cancelled:
        tz = ZoneInfo(await get_setting(session, "club_timezone"))
        trainings = {
            t.id: t for t in await session.scalars(
                select(Training).where(Training.id.in_({o.training_id for o in cancelled}))
            )
        }
        for order in cancelled:
            training = trainings[order.training_id]
            when = format_when(training.starts_at, training.duration_min, tz)
            await _notify_quietly(
                messenger, order.user_id,
                f"Единицу «{item.label}» вывели из парка, поэтому её заказ на тренировку {when} отменён. "
                "Подобрать замену можно кнопкой «🚲 Инвентарь» на карточке тренировки.",
            )
    return item, cancelled


async def join_training(
    session: AsyncSession, messenger: Messenger, training_id: int, user_id: int, now: datetime
) -> JoinOutcome:
    outcome = await ParticipationService(session).join(training_id, user_id, now)
    log.info("join training=%s user=%s -> %s", training_id, user_id, outcome)
    if outcome == JoinOutcome.JOINED:
        await PublishService(session, messenger).refresh_posts(training_id)
    return outcome


async def decline_training(
    session: AsyncSession, messenger: Messenger, training_id: int, user_id: int, now: datetime
) -> str | None:
    previous = await ParticipationService(session).decline(training_id, user_id, now)
    log.info("decline training=%s user=%s (was %s)", training_id, user_id, previous)
    if previous == ParticipationStatus.GOING:
        await _cancel_participant_orders(session, messenger, training_id, user_id)
        await PublishService(session, messenger).refresh_posts(training_id)
    return previous


async def cancel_trainings(
    session: AsyncSession, messenger: Messenger, actor: User, training_ids: list[int], now: datetime
) -> list[Training]:
    cancelled = await ScheduleService(session).cancel(actor, training_ids, now)
    log.info("cancelled trainings %s by user=%s", [t.id for t in cancelled], actor.telegram_id)
    publisher = PublishService(session, messenger)
    inventory = InventoryService(session)
    for training in cancelled:
        await inventory.cancel_for_training(training.id)
        await publisher.announce_cancelled(training.id)
    return cancelled


async def end_series(
    session: AsyncSession, messenger: Messenger, actor: User, series_id: int, now: datetime
) -> list[Training]:
    cancelled = await ScheduleService(session).end_series(actor, series_id, now)
    log.info("series %s ended by user=%s, cancelled trainings %s", series_id, actor.telegram_id, [t.id for t in cancelled])
    publisher = PublishService(session, messenger)
    inventory = InventoryService(session)
    for training in cancelled:
        await inventory.cancel_for_training(training.id)
        await publisher.announce_cancelled(training.id)
    return cancelled


async def update_series(
    session: AsyncSession, messenger: Messenger, actor: User, series_id: int, data: SeriesInput, now: datetime
) -> list[ReplacedTraining]:
    replaced = await ScheduleService(session).update_series(actor, series_id, data, now)
    publisher = PublishService(session, messenger)
    inventory = InventoryService(session)
    for item in replaced:
        await inventory.cancel_for_training(item.old_id)
        if item.new_id is not None:
            await publisher.publish(item.new_id, now)  # сразу, чтобы в уведомлении была ссылка на пост
        await publisher.announce_replaced(item.old_id, item.new_id)
    return replaced


async def update_training(
    session: AsyncSession, messenger: Messenger, actor: User, training_id: int, edit: TrainingEdit, now: datetime
) -> Training:
    training = await ScheduleService(session).update_training(actor, training_id, edit, now)
    await PublishService(session, messenger).refresh_posts(training_id)
    return training


async def manual_add_participant(
    session: AsyncSession, messenger: Messenger, actor: User, training_id: int, user_id: int, now: datetime,
    allow_over_limit: bool = False,
) -> JoinOutcome:
    outcome = await ParticipationService(session).manual_add(
        actor, training_id, user_id, now, allow_over_limit=allow_over_limit
    )
    log.info("manual add training=%s user=%s by=%s -> %s", training_id, user_id, actor.telegram_id, outcome)
    if outcome == JoinOutcome.JOINED:
        publisher = PublishService(session, messenger)
        await publisher.refresh_posts(training_id)
        await publisher.notify_manual_change(training_id, user_id, added=True)
    return outcome


async def manual_remove_participant(
    session: AsyncSession, messenger: Messenger, actor: User, training_id: int, user_id: int, now: datetime
) -> bool:
    removed = await ParticipationService(session).manual_remove(actor, training_id, user_id, now)
    log.info("manual remove training=%s user=%s by=%s -> %s", training_id, user_id, actor.telegram_id, removed)
    if removed:
        await _cancel_participant_orders(session, messenger, training_id, user_id)
        publisher = PublishService(session, messenger)
        await publisher.refresh_posts(training_id)
        await publisher.notify_manual_change(training_id, user_id, added=False)
    return removed


async def _title(session: AsyncSession, training: Training) -> str:
    tz = ZoneInfo(await get_setting(session, "club_timezone"))
    return f"{KIND_NAMES.get(training.kind, training.kind)}, {format_when(training.starts_at, training.duration_min, tz)}"


async def _admin_ids(session: AsyncSession) -> list[int]:
    return list(await session.scalars(select(User.telegram_id).where(User.role == Role.ADMIN)))


async def close_training(session: AsyncSession, messenger: Messenger, training_id: int, now: datetime) -> None:
    """Закрытие тренировки: статус, пост, начисления и уведомления. Идемпотентно: повтор после сбоя
    не создаёт начислений и не уведомляет повторно о уже созданных."""
    await PublishService(session, messenger).close(training_id, now)
    if not await billing_enabled(session):
        return  # оплата отключена: тренировка закрыта как раньше, без начислений и сообщений
    created = await BillingService(session).charge_training(training_id, now)
    training = await session.get(Training, training_id)
    if training is None or training.status != TrainingStatus.DONE:
        return
    title = await _title(session, training)
    sub_ids = {c.subscription_id for c in created if c.subscription_id is not None}
    sub_names = (
        {s.id: s.name for s in await session.scalars(select(Subscription).where(Subscription.id.in_(sub_ids)))}
        if sub_ids else {}
    )
    by_user: dict[int, list[Charge]] = defaultdict(list)
    for charge in created:
        by_user[charge.user_id].append(charge)
    for user_id, charges in by_user.items():
        await _notify_quietly(messenger, user_id, render_new_charges(title, charges, sub_names))
    going = await ParticipationService(session).going_count(training_id)
    if going:  # в конце успешного закрытия: задача завершается один раз, а при сбое раньше повтор дойдёт досюда
        await _notify_quietly(
            messenger, training.trainer_id,
            f"🏁 Началась тренировка: {title}. Записалось {going}. После занятия отметьте, кто пришёл.",
            [[Button("✅ Проверить посещение", f"att:o:{training_id}")]],
        )


async def set_attendance(
    session: AsyncSession, messenger: Messenger, actor: User, training_id: int, user_id: int, present: bool,
    now: datetime,
) -> AttendanceResult:
    result = await BillingService(session).set_attendance(actor, training_id, user_id, present, now)
    log.info("attendance training=%s user=%s present=%s -> %s", training_id, user_id, present, result)
    if not result.changed:
        return result
    training = await session.get(Training, training_id)
    title = await _title(session, training)
    if present:
        await _notify_quietly(messenger, user_id, f"✅ Тренер отметил, что вы были на тренировке: {title}")
    else:
        text = f"Занятие не засчитано: {title}."
        if result.returned_to:
            text += f" Вернули на абонемент «{result.returned_to}»."
        if result.refund_due:
            text += " Вы уже платили — деньги вернёт администратор."
        await _notify_quietly(messenger, user_id, text)
    for uid, amount in result.pending_changes:
        if uid != user_id:
            await _notify_quietly(messenger, uid, f"➕ Доплата за {title} изменилась: {format_money(amount)}")
    notes: list[str] = []
    if result.refund_due:
        notes.append(f"К возврату: {(await session.get(User, user_id)).name}, {title}")
    for uid, paid, due in result.paid_diffs:
        name = (await session.get(User, uid)).name
        notes.append(f"Доплата {title}: {name} оплатил(а) {format_money(paid)}, теперь {format_money(due)}")
    if notes:
        for admin_id in await _admin_ids(session):
            await _notify_quietly(messenger, admin_id, "💰 " + "\n".join(notes))
    return result


async def issue_subscription(
    session: AsyncSession, messenger: Messenger, actor: User, user_id: int, plan_id: int, paid: bool, now: datetime
) -> Subscription:
    sub = await BillingService(session).issue_subscription(actor, user_id, plan_id, paid=paid, now=now)
    tz = ZoneInfo(await get_setting(session, "club_timezone"))
    until = f"до {sub.expires_at.astimezone(tz):%d.%m.%Y}" if sub.expires_at else "бессрочный"
    text = f"🎟 Вам выдан абонемент «{sub.name}»: {sub.sessions_total} занятий, {until}."
    if not paid:
        text += f" К оплате: {format_money(sub.price)}."
    await _notify_quietly(messenger, user_id, text)
    return sub


async def mark_charge_paid(
    session: AsyncSession, messenger: Messenger, actor: User, charge_id: int, now: datetime
) -> Charge:
    charge = await BillingService(session).mark_charge_paid(actor, charge_id, now)
    await _notify_quietly(messenger, charge.user_id, f"✅ Оплата принята: {format_money(charge.amount)}. Спасибо!")
    return charge


async def mark_subscription_paid(
    session: AsyncSession, messenger: Messenger, actor: User, subscription_id: int, now: datetime
) -> Subscription:
    sub = await BillingService(session).mark_subscription_paid(actor, subscription_id, now)
    await _notify_quietly(messenger, sub.user_id, f"✅ Оплата принята: абонемент «{sub.name}», {format_money(sub.price)}.")
    return sub


async def mark_user_paid(
    session: AsyncSession, messenger: Messenger, actor: User, user_id: int, now: datetime
) -> int:
    closed = await BillingService(session).mark_user_paid(actor, user_id, now)
    if closed:
        await _notify_quietly(messenger, user_id, "✅ Оплата принята. Спасибо!")
    return closed


async def send_reminders(session: AsyncSession, messenger: Messenger, training_id: int, now: datetime) -> None:
    if not await billing_enabled(session):
        await PublishService(session, messenger).send_reminders(training_id, now)
        return
    views = BillingViews(session)
    await PublishService(session, messenger).send_reminders(
        training_id, now, hint_for=lambda user_id: views.payment_hint(training_id, user_id, now)
    )
