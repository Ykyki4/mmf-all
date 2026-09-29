"""
Пересоздаёт схему БД через миграции Alembic и загружает тестовые данные. Только для разработки.

    python reset_db.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


async def drop_schema() -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        print("Удаляю схему...")
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


def main() -> None:
    asyncio.run(drop_schema())
    print("Применяю миграции...")
    command.upgrade(Config(os.path.join(BASE_DIR, "alembic.ini")), "head")

    import seed_data

    asyncio.run(seed_data.main(if_empty=False))
    print("Готово.")


if __name__ == "__main__":
    main()
