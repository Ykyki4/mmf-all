# Цифровые рекомендации ИТ-Проф

Единая серверная часть (REST API + PostgreSQL) для трёх клиентов: desktop (HR), web и mobile.

**Стек:** Python 3.12, FastAPI, SQLModel + SQLAlchemy 2 (async, asyncpg), Alembic, PostgreSQL 16, Docker Compose.

## Быстрый старт

```bash
cp .env.example .env        # заменить POSTGRES_PASSWORD и SECRET_KEY
docker compose up -d --build
```

При первом запуске backend применяет миграции (`alembic upgrade head`) и, если БД пустая, загружает тестовые данные (`SEED_ON_START=true`).

| Сервис | Адрес |
|---|---|
| API | http://localhost:8000/api/v1 |
| Swagger (встроенный) | http://localhost:8000/api/v1/docs |
| Swagger UI (отдельный контейнер) | http://localhost:8080 |
| API по HTTPS (nginx, self-signed, HSTS) | https://localhost:8443/api/v1 |
| PostgreSQL | localhost:5432 |

Пути из ТЗ без префикса (`POST /auth/login`) тоже работают — это алиасы версии `/api/v1`.

### Тестовые пользователи (пароль у всех `Password123`)

| Email | Роль | Назначение |
|---|---|---|
| hr@itprof.ru | HR-менеджер | desktop: поиск, подборки, отчёты, аналитика |
| lead@itprof.ru | Руководитель команды | desktop |
| analyst@itprof.ru | Аналитик | desktop (без подборок) |
| admin@itprof.ru | Администратор | смена ролей, блокировка |
| user@itprof.ru | ИТ-специалист (Петров И.С., `ITP-00006`) | web/mobile: есть входящие и исходящие запросы |
| zakharov@itprof.ru | ИТ-специалист с 2FA | вход через `POST /auth/2fa` |

Тестовые данные: 25 специалистов, 8 компаний, 12 учебных заведений, 30 технологий, 10 вакансий, подтверждения, интеграции, подборки, история рейтинга за 120 дней. Один профиль (`ITP-00030`) закрыт, на нём можно проверить 403.

## Структура

```
docker-compose.yml       db, backend, proxy (HTTPS), swagger-ui
proxy/                   nginx + генерация self-signed сертификата
backend/
  alembic/versions/      0001 — схема, 0002 — ограничения, триггеры, пересчёт рейтинга
  app/
    main.py              приложение, middleware, подключение роутеров
    config.py            настройки из переменных окружения
    auth.py              пароли (Argon2id), JWT, CSRF, зависимости ролей
    errors.py            единый формат ошибок, маппинг ошибок PostgreSQL → 400
    middleware.py        журнал запросов (файл + таблица), CSRF, rate limit
    models/              таблицы SQLModel
    schemas/             Pydantic-схемы запросов и ответов
    routers/             HTTP-слой (контроллеры)
    services/            бизнес-логика: профили, поиск, рейтинг, отчёты, PDF/Excel
  seed_data.py           тестовые данные
  create_user.py         создание пользователя из консоли
  reset_db.py            пересоздание схемы и данных (разработка)
  tests/                 интеграционные тесты API
```

## API

Формат любого ответа: `{"success": bool, "data": {...}, "message": str, "error_code": str|null}`.
Ошибки: 400 (валидация и нарушения ограничений БД), 401, 403, 404, 409, 410, 429, 500.
Авторизация: `Authorization: Bearer <access_token>`, токен выдаёт `POST /auth/login`.

| Группа | Эндпоинты |
|---|---|
| auth | `POST /auth/register`, `/login`, `/2fa`, `/refresh`, `/logout` (`{"all_devices": true}` — выход на всех устройствах), `/password/forgot`, `/password/reset`, `GET /auth/csrf-token`, `POST /auth/oauth/{google\|github}` (заглушка) |
| users | `GET/PUT /users/me`, `POST /users/me/avatar`, `GET /users/me/recommendations`, `GET /users/{id}?mode=full\|public\|employer`, `GET /users/search` (HR), `GET /users/lookup` |
| rating | `GET /users/{id}/rating` (`id` = число, `ITP-00012` или `me`), `GET /users/me/rating/history?period=30\|90\|365\|all` |
| профиль | `GET/POST /education`, `PUT /education/reorder`, `PUT/DELETE /education/{id}`, аналогично `/experience` и `/skills`, `GET /skills/suggest`, `GET /skills/top` |
| confirmations | `POST /confirmations/request`, `POST /confirmations/{id}/accept\|reject\|revoke`, `GET /confirmations?direction=&status=`, `GET /confirmations/{id}`, `GET /confirmations/counters` |
| notifications | `GET /notifications`, `GET /notifications/unread-count`, `POST /notifications/{id}/read`, `POST /notifications/read-all`, `DELETE /notifications/{id}` |
| integrations | `GET /integrations`, `POST /integrations/{gosuslugi\|github\|telegram}/connect`, `DELETE /integrations/{provider}` |
| exports | `GET /users/me/export/preview`, `POST /users/me/export`, `GET /users/me/exports`, `GET /exports/{token}` (скачивание по ссылке, 410 после истечения срока) |
| HR (desktop) | `GET /dashboard/summary`, `/shortlists` (CRUD, кандидаты, `POST /shortlists/candidates` — сразу в несколько подборок), `GET /reports/candidate/{id}`, `GET /reports/shortlist/{id}` (`format=json\|pdf\|xlsx`, `period=month\|quarter\|year`), `GET /reports/suggest`, `GET /analytics/skills`, `GET /analytics/rating/trend`, `GET /analytics/export` (CSV) |
| справочники | `GET /companies`, `GET /institutions`, `GET /vacancies` |
| admin | `GET /admin/users`, `PUT /admin/users/{id}/role`, `PUT /admin/users/{id}/active`, `GET /admin/role-history` |

## Ограничения целостности в БД

Реализованы в миграции `0002_integrity_triggers.py`:

- email и телефон уникальны (`UNIQUE`), триггер приводит email к нижнему регистру;
- уровень навыка 0–10 (`CHECK`);
- периоды работы одного пользователя не пересекаются: триггер с понятным сообщением и `EXCLUDE USING gist`;
- дата окончания не раньше даты начала, дата начала и дата регистрации не в будущем (триггеры и `CHECK`);
- самоподтверждение запрещено, а подтверждаемый навык, опыт или образование должен принадлежать запрашивающему (триггер);
- статусы подтверждения меняются только так: `pending → accepted | rejected`, `accepted → revoked` (триггер);
- рейтинг пересчитывается автоматически (`recalc_user_rating`) при подтверждении, отзыве, изменении образования, опыта или интеграций. Каждое изменение пишется в `rating_history`, пользователь получает уведомление;
- смена роли записывается в `role_history` (кто, когда, старая и новая роль).

Все триггеры выполняются в транзакции исходной операции: при ошибке откатывается всё. Триггер передаёт машинный код ошибки в `HINT`, и API возвращает 400 с этим `error_code` (например, `EXPERIENCE_OVERLAP`, `SELF_CONFIRMATION`).

**Формула рейтинга.** Индекс компетенций (0–100) = 30% образование + 30% опыт + 40% подтверждения. Уровень доверия сообщества = 85% × доля принятых подтверждений от пользователей, верифицированных через Госуслуги, плюс 5 за каждый подключённый сервис. Отклонённые запросы на рейтинг не влияют.

## Безопасность

- Пароли хранятся как Argon2id. Клиент может отправить пароль уже захешированным (SHA-256, флаг `password_prehashed`).
- Access-токен JWT живёт 15 минут и привязан к сессии (`sid`). Refresh-токен ротируется при каждом обновлении, в БД хранится только его SHA-256. После `logout` оба токена сразу перестают действовать.
- Ролевой доступ: `user`, `hr`, `team_lead`, `analyst`, `admin`.
- Все входные данные проверяются Pydantic, HTML-разметка и скрипты в текстовых полях отклоняются (400). SQL строится только через ORM или параметризованные запросы.
- CSRF: браузерный запрос с чужого origin, который что-то изменяет, должен нести `X-CSRF-Token` (выдаёт `GET /auth/csrf-token`). Desktop и mobile не отправляют `Origin`, поэтому их это не касается.
- Rate limit на auth-эндпоинты (`AUTH_RATE_LIMIT_PER_MINUTE`).
- Журнал всех запросов пишется в `logs/api.log` и таблицу `api_request_logs`: метод, путь, user_id, статус, длительность. Тела запросов, токены и query-параметры в журнал не попадают, email маскируется.
- Секреты задаются только через `.env`, файл в `.gitignore`.

## Разработка

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate      # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                                  # DATABASE_URL на localhost:5432
alembic upgrade head && python seed_data.py
uvicorn app.main:app --reload

ruff check .                                          # линтер (конфигурация в pyproject.toml)
pip-audit -r requirements.txt                         # проверка уязвимостей зависимостей
```

Интеграционные тесты идут против запущенного API с тестовыми данными (25 тестов):

```bash
API_BASE_URL=http://localhost:8000/api/v1 pytest
```

Новая миграция после изменения моделей: `alembic revision --autogenerate -m "описание"`.
