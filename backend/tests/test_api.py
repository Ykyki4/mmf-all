import uuid
from datetime import date, timedelta

from .conftest import PASSWORD, auth_headers, login


def assert_error(response, status_code: int, error_code: str | None = None) -> None:
    assert response.status_code == status_code, response.text
    body = response.json()
    assert body["success"] is False
    assert set(body) == {"success", "data", "message", "error_code"}
    if error_code:
        assert body["error_code"] == error_code, body


# ── Аутентификация ────────────────────────────────────────────────────────────

def test_login_returns_jwt_in_unified_format(client):
    response = client.post("/auth/login", json={"email": "hr@itprof.ru", "password": PASSWORD})
    body = response.json()
    assert response.status_code == 200
    assert body["success"] is True and body["error_code"] is None
    assert body["data"]["access_token"].count(".") == 2
    assert body["data"]["user"]["role"] == "hr"


def test_login_with_prehashed_password(client):
    import hashlib

    response = client.post("/auth/login", json={
        "email": "hr@itprof.ru", "password": hashlib.sha256(PASSWORD.encode()).hexdigest(),
        "password_prehashed": True,
    })
    assert response.status_code == 200


def test_login_wrong_password_401(client):
    response = client.post("/auth/login", json={"email": "hr@itprof.ru", "password": "wrong-pass1"})
    assert_error(response, 401, "INVALID_CREDENTIALS")
    assert response.json()["message"] == "Неверный email или пароль"


def test_invalid_email_400(client):
    assert_error(client.post("/auth/login", json={"email": "not-an-email", "password": "x"}), 400, "VALIDATION_ERROR")


def test_protected_endpoints_require_jwt(client):
    for path in ("/users/me", "/confirmations", "/dashboard/summary", "/analytics/skills", "/users/1/rating"):
        assert_error(client.get(path), 401, "UNAUTHORIZED")
    assert_error(client.get("/users/me", headers=auth_headers("garbage.token.value")), 401, "INVALID_TOKEN")


def test_refresh_and_logout_invalidate_tokens(client):
    tokens = login(client, "analyst@itprof.ru")
    refreshed = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200
    new_tokens = refreshed.json()["data"]
    # старый refresh-токен после ротации недействителен
    assert_error(client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}), 401)

    headers = auth_headers(new_tokens["access_token"])
    assert client.get("/users/me", headers=headers).status_code == 200
    assert client.post("/auth/logout", headers=headers).status_code == 200
    assert_error(client.get("/users/me", headers=headers), 401, "SESSION_REVOKED")


def test_register_validation_and_duplicate(client, new_user):
    _, user = new_user
    short = client.post("/auth/register", json={
        "email": "x@itprof.ru", "password": "short1", "password_confirm": "short1",
        "last_name": "Тест", "first_name": "Тест",
    })
    assert_error(short, 400, "VALIDATION_ERROR")
    duplicate = client.post("/auth/register", json={
        "email": user["email"].upper(), "password": PASSWORD, "password_confirm": PASSWORD,
        "last_name": "Тест", "first_name": "Тест",
    })
    assert_error(duplicate, 400, "DUPLICATE")


def test_two_factor_login(client):
    first = client.post("/auth/login", json={"email": "zakharov@itprof.ru", "password": PASSWORD}).json()["data"]
    assert first["requires_2fa"] is True
    if first["debug_code"]:
        second = client.post("/auth/2fa", json={"two_factor_token": first["two_factor_token"],
                                                 "code": first["debug_code"]})
        assert second.status_code == 200 and second.json()["data"]["access_token"]


# ── Профиль ───────────────────────────────────────────────────────────────────

def test_update_profile_and_xss_rejected(client, new_user):
    headers, _ = new_user
    digits = f"{uuid.uuid4().int % 10**7:07d}"
    response = client.put("/users/me", headers=headers,
                          json={"phone": f"+7 (920) {digits[:3]}-{digits[3:5]}-{digits[5:]}", "city": "Казань"})
    assert response.status_code == 200
    assert response.json()["data"]["phone"] == f"+7920{digits}"
    assert_error(client.put("/users/me", headers=headers, json={"about": "<script>alert(1)</script>"}), 400)
    # тот же телефон у другого пользователя → UNIQUE в БД → 400
    other_headers = auth_headers(login(client, "analyst@itprof.ru")["access_token"])
    assert_error(client.put("/users/me", headers=other_headers, json={"phone": f"+7920{digits}"}), 400, "DUPLICATE")


def test_employer_mode_hides_contacts(client, hr_headers):
    profile = client.get("/users/ITP-00006", params={"mode": "employer"}, headers=hr_headers).json()["data"]
    assert profile["email"] is None and profile["phone"] is None and profile["first_name"] is None
    assert profile["full_name"].endswith("(анонимный профиль)")
    levels = [skill["level"] for skill in profile["skills"]]
    assert levels == sorted(levels, reverse=True)
    assert all("phone" not in (exp["company"] or {}) for exp in profile["experience"])


def test_private_profile_forbidden_for_regular_user(client, new_user, hr_headers):
    headers, _ = new_user
    found = client.get("/users/search", params={"page_size": 100}, headers=hr_headers).json()["data"]
    assert all(item["id"] != 30 for item in found["items"])  # закрытый профиль (id=30) не ищется
    assert_error(client.get("/users/30", headers=headers), 403, "PROFILE_PRIVATE")
    assert_error(client.get("/users/99999", headers=headers), 404)


# ── Данные профиля и ограничения БД ───────────────────────────────────────────

def test_education_crud_and_date_order(client, new_user):
    headers, _ = new_user
    created = client.post("/education", headers=headers, json={
        "education_type": "higher", "institution_name": "Тестовый университет",
        "specialty": "Информатика", "start_date": "2015-09-01", "end_date": "2019-06-30",
    })
    assert created.status_code == 201
    record_id = created.json()["data"]["id"]
    updated = client.put(f"/education/{record_id}", headers=headers, json={"result": "Красный диплом"})
    assert updated.json()["data"]["result"] == "Красный диплом"
    # дата окончания раньше начала → триггер/CHECK БД → 400
    bad = client.put(f"/education/{record_id}", headers=headers, json={"end_date": "2010-01-01"})
    assert_error(bad, 400, "INVALID_DATE_RANGE")
    assert client.delete(f"/education/{record_id}", headers=headers).status_code == 200
    assert_error(client.delete(f"/education/{record_id}", headers=headers), 404)


def test_experience_overlap_blocked(client, new_user):
    headers, _ = new_user
    first = client.post("/experience", headers=headers, json={
        "employment_type": "labor_contract", "company_name": "ООО «Ромашка»", "position": "Python Developer",
        "start_date": "2020-01-01", "end_date": "2022-12-31",
    })
    assert first.status_code == 201
    overlap = client.post("/experience", headers=headers, json={
        "employment_type": "freelance", "position": "Фрилансер", "start_date": "2022-06-01",
    })
    assert_error(overlap, 400, "EXPERIENCE_OVERLAP")
    # дата окончания раньше начала при частичном обновлении → 400 (не 500)
    bad_dates = client.put(f"/experience/{first.json()['data']['id']}", headers=headers,
                           json={"end_date": "2019-01-01"})
    assert_error(bad_dates, 400, "INVALID_DATE_RANGE")


def test_skill_level_range_and_duplicates(client, new_user):
    headers, _ = new_user
    assert_error(client.post("/skills", headers=headers, json={"technology_name": "Python", "level": 15}), 400)
    created = client.post("/skills", headers=headers, json={"technology_name": "Python", "level": 7})
    assert created.status_code == 201
    skill_id = created.json()["data"]["id"]
    assert_error(client.post("/skills", headers=headers, json={"skill_id": created.json()["data"]["technology_id"],
                                                              "level": 5}), 400, "DUPLICATE")
    assert client.put(f"/skills/{skill_id}", headers=headers, json={"level": 9}).json()["data"]["level"] == 9
    assert_error(client.put(f"/skills/{skill_id}", headers=headers, json={"level": -1}), 400)
    assert client.delete(f"/skills/{skill_id}", headers=headers).status_code == 200


def test_cannot_modify_foreign_record(client, new_user):
    headers, _ = new_user
    demo = login(client, "user@itprof.ru")
    foreign_skill = client.get("/skills", headers=auth_headers(demo["access_token"])).json()["data"][0]["id"]
    assert_error(client.put(f"/skills/{foreign_skill}", headers=headers, json={"level": 1}), 403)


# ── Подтверждения и рейтинг ───────────────────────────────────────────────────

def test_confirmation_flow_updates_rating(client, new_user):
    headers, user = new_user
    skill = client.post("/skills", headers=headers, json={"technology_name": "Docker", "level": 6}).json()["data"]

    # самоподтверждение запрещено триггером
    self_request = client.post("/confirmations/request", headers=headers, json={
        "confirmer_id": user["id"], "confirmation_type": "skill", "subject_id": skill["id"],
    })
    assert_error(self_request, 400, "SELF_CONFIRMATION")

    colleague = login(client, "smirnova@itprof.ru")
    colleague_headers = auth_headers(colleague["access_token"])
    request = client.post("/confirmations/request", headers=headers, json={
        "confirmer_id": colleague["user"]["id"], "subject_id": skill["id"],
    }).json()["data"]
    assert request["status"] == "pending"

    before = client.get("/users/me/rating", headers=headers).json()["data"]
    accepted = client.post(f"/confirmations/{request['id']}/accept", headers=colleague_headers)
    assert accepted.json()["data"]["status"] == "accepted"
    after = client.get("/users/me/rating", headers=headers).json()["data"]
    assert after["confirmations_count"] == before["confirmations_count"] + 1
    assert after["competency_index"] > before["competency_index"]
    history = client.get("/users/me/rating/history", headers=headers).json()["data"]["items"]
    assert history[-1]["reason"] == "confirmation_accepted"

    # повторная обработка → 400, чужой пользователь не может ответить → 403
    assert_error(client.post(f"/confirmations/{request['id']}/reject", headers=colleague_headers), 400)
    assert_error(client.post(f"/confirmations/{request['id']}/accept", headers=headers), 403)

    revoked = client.post(f"/confirmations/{request['id']}/revoke", headers=headers)
    assert revoked.json()["data"]["status"] == "revoked"


def test_reject_does_not_change_rating(client, new_user):
    headers, _ = new_user
    skill = client.post("/skills", headers=headers, json={"technology_name": "Go", "level": 4}).json()["data"]
    colleague = login(client, "popova@itprof.ru")
    request = client.post("/confirmations/request", headers=headers, json={
        "confirmer_id": colleague["user"]["id"], "subject_id": skill["id"],
    }).json()["data"]
    before = client.get("/users/me/rating", headers=headers).json()["data"]
    client.post(f"/confirmations/{request['id']}/reject", headers=auth_headers(colleague["access_token"]))
    after = client.get("/users/me/rating", headers=headers).json()["data"]
    assert after["competency_index"] == before["competency_index"]
    incoming = client.get("/confirmations", params={"direction": "outgoing", "status": "rejected"},
                          headers=headers).json()["data"]
    assert incoming["total"] == 1


def test_rating_breakdown(client, hr_headers):
    rating = client.get("/users/ITP-00006/rating", headers=hr_headers).json()["data"]
    assert [item["weight"] for item in rating["breakdown"]] == [30, 30, 40]
    assert rating["trend"] in ("up", "flat", "down")


# ── Роли ──────────────────────────────────────────────────────────────────────

def test_role_change_logged_and_rbac(client, new_user, admin_headers):
    headers, user = new_user
    assert_error(client.get("/dashboard/summary", headers=headers), 403)
    changed = client.put(f"/admin/users/{user['id']}/role", headers=admin_headers, json={"role": "hr"})
    assert changed.json()["data"]["role"] == "hr"
    history = client.get("/admin/role-history", params={"user_id": user["id"]}, headers=admin_headers)
    records = history.json()["data"]
    assert records[0]["old_role"] == "user" and records[0]["new_role"] == "hr"
    assert records[0]["changed_by_id"] is not None


# ── HR: поиск, подборки, отчёты, аналитика ────────────────────────────────────

def test_search_python_and_rating_filter(client, hr_headers):
    python = client.get("/users/search", params={"skills": "Python"}, headers=hr_headers).json()["data"]
    assert python["total"] > 0
    assert all(any(skill["name"] == "Python" for skill in item["skills"]) for item in python["items"])

    top = client.get("/users/search", params={"min_rating": 80}, headers=hr_headers).json()["data"]
    assert top["total"] > 0
    assert all(item["competency_index"] >= 80 for item in top["items"])
    assert_error(client.get("/users/search", params={"page_size": 7}, headers=hr_headers), 400)


def test_shortlist_lifecycle(client, hr_headers):
    name = f"Тест {uuid.uuid4().hex[:6]}"
    shortlist = client.post("/shortlists", headers=hr_headers, json={"name": name}).json()["data"]
    added = client.post(f"/shortlists/{shortlist['id']}/candidates", headers=hr_headers, json={"candidate_id": 6})
    assert added.json()["data"]["candidates_count"] == 1
    detail = client.get(f"/shortlists/{shortlist['id']}", headers=hr_headers).json()["data"]
    assert detail["candidates"]["items"][0]["public_id"] == "ITP-00006"
    renamed = client.put(f"/shortlists/{shortlist['id']}", headers=hr_headers, json={"name": name + " (ред.)"})
    assert renamed.json()["data"]["name"].endswith("(ред.)")
    removed = client.delete(f"/shortlists/{shortlist['id']}/candidates/6", headers=hr_headers)
    assert removed.json()["data"]["candidates_count"] == 0
    assert client.delete(f"/shortlists/{shortlist['id']}", headers=hr_headers).status_code == 200
    assert_error(client.get(f"/shortlists/{shortlist['id']}", headers=hr_headers), 404)


def test_reports_json_pdf_xlsx(client, hr_headers):
    preview = client.get("/reports/candidate/ITP-00006", params={"period": "quarter"}, headers=hr_headers)
    assert preview.json()["data"]["tables"][0]["columns"] == ["Технология", "Уровень", "Подтверждений"]
    pdf = client.get("/reports/candidate/6", params={"format": "pdf"}, headers=hr_headers)
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    xlsx = client.get("/reports/shortlist/1", params={"format": "xlsx"}, headers=hr_headers)
    assert xlsx.status_code == 200 and xlsx.content.startswith(b"PK")


def test_analytics_and_dashboard(client, hr_headers):
    summary = client.get("/dashboard/summary", headers=hr_headers).json()["data"]
    assert summary["active_profiles"]["value"] >= 20
    top = client.get("/skills/top", headers=hr_headers).json()["data"]
    assert len(top) == 5
    skills = client.get("/analytics/skills", params={"period": 90}, headers=hr_headers).json()["data"]
    assert skills["distribution"][-1]["name"] == "Другое"
    assert len(skills["top_technologies"]) == 10
    trend = client.get("/analytics/rating/trend", params={"period": 30}, headers=hr_headers).json()["data"]
    assert len(trend["points"]) == 30
    suggest = client.get("/skills/suggest", params={"q": "py"}, headers=hr_headers).json()["data"]
    assert suggest[0]["name"] == "Python"


# ── Экспорт ───────────────────────────────────────────────────────────────────

def test_profile_export_link(client):
    headers = auth_headers(login(client, "user@itprof.ru")["access_token"])
    export = client.post("/users/me/export", headers=headers,
                         json={"format": "json", "ttl_days": 7, "privacy_mode": "anonymous"}).json()["data"]
    assert export["file_name"].endswith("_json.json")
    assert 6 * 86400 < export["expires_in_seconds"] <= 7 * 86400
    download = client.get(export["download_url"].split("/api/v1")[-1])
    assert download.status_code == 200 and download.json()["email"] is None
    assert_error(client.get("/exports/unknown-token"), 404)


def test_future_start_date_rejected(client, new_user):
    headers, _ = new_user
    tomorrow = (date.today() + timedelta(days=5)).isoformat()
    response = client.post("/experience", headers=headers, json={
        "employment_type": "freelance", "position": "Фрилансер", "start_date": tomorrow,
    })
    assert_error(response, 400)
