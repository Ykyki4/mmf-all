"""Интеграционные тесты API. Запускаются против поднятого сервера с тестовыми данными:

    docker compose up -d
    API_BASE_URL=http://localhost:8000/api/v1 pytest
"""

import os
import uuid

import httpx
import pytest

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")
PASSWORD = "Password123"


@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=30) as http_client:
        yield http_client


def login(client: httpx.Client, email: str, password: str = PASSWORD) -> dict:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def hr_headers(client):
    return auth_headers(login(client, "hr@itprof.ru")["access_token"])


@pytest.fixture(scope="session")
def admin_headers(client):
    return auth_headers(login(client, "admin@itprof.ru")["access_token"])


@pytest.fixture
def new_user(client):
    """Свежий зарегистрированный специалист: (headers, user)."""
    email = f"test_{uuid.uuid4().hex[:10]}@itprof.ru"
    response = client.post("/auth/register", json={
        "email": email, "password": PASSWORD, "password_confirm": PASSWORD,
        "last_name": "Тестов", "first_name": "Тест",
    })
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    return auth_headers(data["access_token"]), data["user"]
