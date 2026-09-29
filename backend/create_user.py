"""
Создание пользователя в БД.

Использование:
    python create_user.py --email hr3@itprof.ru --password Secret123 --role hr
    python create_user.py --email admin2@itprof.ru --password Secret123 --role admin --last-name Иванов
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select

from app.auth import hash_password
from app.config import settings
from app.models import User
from app.models.enums import UserRole


async def create_user(args: argparse.Namespace) -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        existing = (await session.execute(select(User).where(User.email == args.email.lower()))).scalar_one_or_none()
        if existing:
            print(f"[skip]  {args.email} — уже существует (role={existing.role})")
        else:
            user = User(
                email=args.email.lower(),
                first_name=args.first_name,
                last_name=args.last_name,
                patronymic=args.patronymic,
                hashed_password=hash_password(args.password),
                role=args.role,
            )
            session.add(user)
            await session.commit()
            print(f"[ok]    {user.email}  |  role={user.role}  |  id={user.id}")

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Создать пользователя в БД")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--first-name", default="Тест", dest="first_name")
    parser.add_argument("--last-name", default="Тестов", dest="last_name")
    parser.add_argument("--patronymic", default=None)
    parser.add_argument("--role", default=UserRole.user.value, choices=[role.value for role in UserRole])

    asyncio.run(create_user(parser.parse_args()))
