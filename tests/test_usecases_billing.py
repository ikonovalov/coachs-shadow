from datetime import timedelta

import pytest
from sqlalchemy import select

from bot.billing.catalog import CatalogService
from bot.billing.models import Charge, ChargeKind, ChargeStatus, Subscription
from bot.core.settings import set_setting
from bot.jobs.models import ScheduledJob
from bot.jobs.queue import JobKind
from bot.jobs.runner import run_due_jobs
from bot.participation.models import Participation, ParticipationStatus
from bot.schedule.models import Training, TrainingStatus
from bot.usecases import (
    close_training, issue_subscription, mark_charge_paid, mark_user_paid, send_reminders, set_attendance,
)
from tests.billing.helpers import done_training, ensure_user, give_subscription
from tests.factories import NOW


pytestmark = pytest.mark.usefixtures("billing_on")  # оплата включена; тесты «выключено» отключают её явно


async def scheduled_training(session, **kw):
    t = await done_training(session, **kw)
    t.status = TrainingStatus.SCHEDULED
    t.starts_at = NOW - timedelta(minutes=1)
    t.published_at = NOW - timedelta(hours=24)
    await session.commit()
    return t


async def test_close_training_closes_charges_and_notifies(session, messenger):
    t = await scheduled_training(session, going=[1, 2])
    await give_subscription(session, 1, [t.series_id], name="Утро")
    await close_training(session, messenger, t.id, NOW)
    assert (await session.get(Training, t.id)).status == TrainingStatus.DONE
    texts = dict(messenger.notifications)
    assert "Списано с абонемента «Утро»" in texts[1]
    assert "К оплате: 700 ₽" in texts[2]
    trainer_note = [text for uid, text in messenger.notifications if uid == 10]
    assert trainer_note and "Записалось 2" in trainer_note[0]
    assert messenger.notification_keyboards[-1][1][0][0].data == f"att:o:{t.id}"


async def test_close_training_twice_does_not_renotify_users_or_double_charge(session, messenger):
    t = await scheduled_training(session, going=[1])
    await close_training(session, messenger, t.id, NOW)
    user_notes = [text for uid, text in messenger.notifications if uid == 1]
    await close_training(session, messenger, t.id, NOW)
    assert [text for uid, text in messenger.notifications if uid == 1] == user_notes
    assert len(list(await session.scalars(select(Charge)))) == 1


async def test_retry_after_failed_close_still_charges_and_prompts_the_trainer(session, messenger, monkeypatch):
    from bot.publishing.service import PublishService

    t = await scheduled_training(session, going=[1])
    original = PublishService.refresh_posts
    calls = []

    async def flaky(self, training_id):
        calls.append(training_id)
        if len(calls) == 1:
            raise RuntimeError("telegram is down")  # закрытие уже закоммитило status=done
        return await original(self, training_id)

    monkeypatch.setattr(PublishService, "refresh_posts", flaky)
    with pytest.raises(RuntimeError):
        await close_training(session, messenger, t.id, NOW)
    assert (await session.get(Training, t.id)).status == TrainingStatus.DONE
    await close_training(session, messenger, t.id, NOW)  # повтор задачи
    assert any(uid == 1 and "К оплате: 700 ₽" in text for uid, text in messenger.notifications)
    assert any(uid == 10 and "Записалось 1" in text for uid, text in messenger.notifications)


async def test_close_job_runs_billing(session_factory, session, messenger):
    t = await scheduled_training(session, going=[1])
    session.add(ScheduledJob(kind=JobKind.CLOSE, run_at=NOW, training_id=t.id))
    await session.commit()
    await run_due_jobs(session_factory, messenger, NOW)
    assert len(list(await session.scalars(select(Charge)))) == 1


async def test_cancelled_training_has_no_charges_or_trainer_note(session, messenger):
    t = await scheduled_training(session, going=[1])
    t.status = TrainingStatus.CANCELLED
    await session.commit()
    await close_training(session, messenger, t.id, NOW)
    assert messenger.notifications == [] and list(await session.scalars(select(Charge))) == []


async def test_set_attendance_notifies_student_and_admins(session, messenger):
    t = await done_training(session, price=0, surcharge=50000, going=[1, 2, 3])
    await ensure_user(session, 99, "admin")
    trainer = await ensure_user(session, 10, "trainer")
    from bot.billing.service import BillingService
    await BillingService(session).charge_training(t.id, NOW)
    await BillingService(session).mark_charge_paid(
        await ensure_user(session, 99, "admin"), (await session.scalars(select(Charge).where(Charge.user_id == 2))).first().id, NOW
    )
    await set_attendance(session, messenger, trainer, t.id, 3, False, NOW)
    texts = {uid: text for uid, text in messenger.notifications if uid != 99}
    assert "не засчитано" in texts[3]
    assert "750 ₽" in texts[1]  # новая доплата у неоплатившего
    admin_texts = [text for uid, text in messenger.notifications if uid == 99]
    assert any("666,67 ₽" in text and "750 ₽" in text for text in admin_texts)


async def test_set_attendance_reports_unit_returned_and_refund(session, messenger):
    t = await done_training(session, going=[1, 2])
    trainer = await ensure_user(session, 10, "trainer")
    admin = await ensure_user(session, 99, "admin")
    sub = await give_subscription(session, 1, [t.series_id], name="Утро")
    from bot.billing.service import BillingService
    await BillingService(session).charge_training(t.id, NOW)
    await BillingService(session).mark_charge_paid(admin, (await session.scalars(select(Charge).where(Charge.user_id == 2))).first().id, NOW)
    await set_attendance(session, messenger, trainer, t.id, 1, False, NOW)
    await set_attendance(session, messenger, trainer, t.id, 2, False, NOW)
    texts = {uid: text for uid, text in messenger.notifications}
    assert "Вернули на абонемент «Утро»" in texts[1]
    assert "деньги вернёт администратор" in texts[2]
    assert any(uid == 99 and "К возврату" in text for uid, text in messenger.notifications)


async def test_issue_subscription_and_payments_notify_the_student(session, messenger):
    t = await done_training(session, going=[1])
    admin = await ensure_user(session, 99, "admin")
    plan = await CatalogService(session).create_plan(admin, "Утро", 4, 300000, 1, [t.series_id])
    sub = await issue_subscription(session, messenger, admin, 1, plan.id, False, NOW)
    assert "Утро" in dict(messenger.notifications)[1] and "4 занят" in dict(messenger.notifications)[1]
    from bot.billing.service import BillingService
    await BillingService(session).charge_training(t.id, NOW)
    charge = (await session.scalars(select(Charge))).first()
    await mark_charge_paid(session, messenger, admin, charge.id, NOW)
    assert "Оплата принята" in messenger.notifications[-1][1]
    assert await mark_user_paid(session, messenger, admin, 1, NOW) == 1  # остался неоплаченный абонемент
    assert "Оплата принята" in messenger.notifications[-1][1]


async def test_reminder_includes_payment_hint(session, messenger):
    t = await scheduled_training(session, going=[1])
    t.starts_at = NOW + timedelta(hours=2)
    await session.commit()
    await send_reminders(session, messenger, t.id, NOW)
    assert "Скоро тренировка" in messenger.notifications[0][1] and "К оплате: 700 ₽" in messenger.notifications[0][1]


async def test_close_with_billing_off_only_closes_the_training(session, messenger):
    await set_setting(session, "billing_enabled", "0")
    t = await scheduled_training(session, going=[1])
    await close_training(session, messenger, t.id, NOW)
    assert (await session.get(Training, t.id)).status == TrainingStatus.DONE
    assert list(await session.scalars(select(Charge))) == [] and messenger.notifications == []


async def test_reminder_with_billing_off_has_no_payment_line(session, messenger):
    await set_setting(session, "billing_enabled", "0")
    t = await scheduled_training(session, going=[1])
    t.starts_at = NOW + timedelta(hours=2)
    await session.commit()
    await send_reminders(session, messenger, t.id, NOW)
    assert "Скоро тренировка" in messenger.notifications[0][1] and "К оплате" not in messenger.notifications[0][1]
