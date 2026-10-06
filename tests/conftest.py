import pytest

import bot.registry  # noqa: F401  (registers all models)
from bot.core.db import Base, make_engine, make_session_factory


@pytest.fixture
async def engine(tmp_path):
    eng = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    async with eng.begin() as conn:
        await conn.exec_driver_sql("PRAGMA journal_mode=MEMORY")
        await conn.exec_driver_sql("PRAGMA synchronous=OFF")
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)


@pytest.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s


from tests.fakes import FakeMessenger  # noqa: E402


@pytest.fixture
def messenger():
    return FakeMessenger()
