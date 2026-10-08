"""Imports every models module so Base.metadata is complete (Alembic, tests)."""
import bot.billing.models  # noqa: F401
import bot.core.settings  # noqa: F401
import bot.inventory.models  # noqa: F401
import bot.jobs.models  # noqa: F401
import bot.participation.models  # noqa: F401
import bot.publishing.models  # noqa: F401
import bot.schedule.models  # noqa: F401
import bot.users.models  # noqa: F401
