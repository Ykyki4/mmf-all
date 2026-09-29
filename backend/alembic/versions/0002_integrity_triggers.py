"""integrity constraints, triggers and rating recalculation

Бизнес-правила целостности реализованы средствами БД:
  * email/телефон уникальны (UNIQUE в 0001 + нормализация email триггером);
  * периоды работы одного пользователя не пересекаются (триггер + EXCLUDE);
  * уровень навыка 0–10 (CHECK в 0001);
  * дата окончания не раньше начала, даты начала и регистрации не в будущем (триггеры);
  * автоматический пересчёт рейтинга при изменении подтверждений/профиля (триггеры);
  * журналирование смены ролей в role_history (триггер);
  * запрет самоподтверждения и проверка принадлежности подтверждаемого объекта (триггер).

Все триггеры выполняются в транзакции исходной операции: при ошибке откатывается всё целиком.
Триггеры поднимают исключения с HINT = код ошибки API и MESSAGE = текст для пользователя.

Revision ID: 0002
Revises: 0001
"""
from collections.abc import Sequence

from sqlalchemy.util import await_only

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ROLES_SQL = """
INSERT INTO roles (code, title, is_employer) VALUES
    ('user',      'ИТ-специалист',        false),
    ('hr',        'HR-менеджер',          true),
    ('team_lead', 'Руководитель команды', true),
    ('analyst',   'Аналитик',             true),
    ('admin',     'Администратор',        true)
ON CONFLICT (code) DO NOTHING;
"""

CONSTRAINTS_SQL = """
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- Гарантия непересечения периодов работы даже при конкурентных вставках
ALTER TABLE work_experiences
    ADD CONSTRAINT ex_work_experiences_20_no_overlap
    EXCLUDE USING gist (
        user_id WITH =,
        daterange(start_date, COALESCE(end_date, 'infinity'::date), '[]') WITH &&
    );

-- Не более одного ожидающего запроса на один и тот же объект к одному коллеге
CREATE UNIQUE INDEX uq_confirmations_pending_item ON confirmations (
    confirmer_id, confirmation_type,
    COALESCE(user_skill_id, work_experience_id, education_record_id)
) WHERE status = 'pending';

CREATE INDEX ix_confirmations_responded_at ON confirmations (responded_at);
CREATE INDEX ix_user_skills_updated_at ON user_skills (COALESCE(updated_at, created_at));
"""

FUNCTIONS_SQL = r"""
-- ── Пользователи: нормализация email, дата регистрации не в будущем ─────────
CREATE OR REPLACE FUNCTION trg_users_validate() RETURNS trigger AS $$
BEGIN
    NEW.email := lower(btrim(NEW.email));
    IF NEW.created_at > now() + interval '1 minute' THEN
        RAISE EXCEPTION 'Дата регистрации не может быть в будущем'
            USING ERRCODE = 'check_violation', HINT = 'REGISTRATION_DATE_IN_FUTURE';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        NEW.updated_at := now();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER users_validate
    BEFORE INSERT OR UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION trg_users_validate();


-- ── Логическая последовательность дат (образование и опыт) ────────────────
-- Триггеры одного события выполняются в алфавитном порядке имён, поэтому префиксы
-- 10_/20_ гарантируют: сначала проверка дат, затем проверка пересечения периодов.
CREATE OR REPLACE FUNCTION trg_validate_period_dates() RETURNS trigger AS $$
BEGIN
    IF NEW.end_date IS NOT NULL AND NEW.end_date < NEW.start_date THEN
        RAISE EXCEPTION 'Дата окончания (%) раньше даты начала (%)', NEW.end_date, NEW.start_date
            USING ERRCODE = 'check_violation', HINT = 'INVALID_DATE_RANGE';
    END IF;
    IF NEW.start_date > CURRENT_DATE THEN
        RAISE EXCEPTION 'Дата начала не может быть в будущем'
            USING ERRCODE = 'check_violation', HINT = 'DATE_IN_FUTURE';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER education_records_10_validate_dates
    BEFORE INSERT OR UPDATE ON education_records
    FOR EACH ROW EXECUTE FUNCTION trg_validate_period_dates();

CREATE TRIGGER work_experiences_10_validate_dates
    BEFORE INSERT OR UPDATE ON work_experiences
    FOR EACH ROW EXECUTE FUNCTION trg_validate_period_dates();


-- ── Непересечение периодов работы (понятное сообщение до срабатывания EXCLUDE) ─
CREATE OR REPLACE FUNCTION trg_work_experiences_no_overlap() RETURNS trigger AS $$
DECLARE
    v_conflict work_experiences%ROWTYPE;
BEGIN
    SELECT * INTO v_conflict
    FROM work_experiences we
    WHERE we.user_id = NEW.user_id
      AND we.id IS DISTINCT FROM NEW.id
      AND daterange(we.start_date, COALESCE(we.end_date, 'infinity'::date), '[]')
          && daterange(NEW.start_date, COALESCE(NEW.end_date, 'infinity'::date), '[]')
    LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION 'Период работы пересекается с «%» (% — %)',
            v_conflict.position,
            to_char(v_conflict.start_date, 'DD.MM.YYYY'),
            COALESCE(to_char(v_conflict.end_date, 'DD.MM.YYYY'), 'по наст. время')
            USING ERRCODE = 'check_violation', HINT = 'EXPERIENCE_OVERLAP';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER work_experiences_20_no_overlap
    BEFORE INSERT OR UPDATE OF user_id, start_date, end_date ON work_experiences
    FOR EACH ROW EXECUTE FUNCTION trg_work_experiences_no_overlap();


-- ── Навыки: отметка времени изменения ──────────────────────────────────────
CREATE OR REPLACE FUNCTION trg_touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER user_skills_touch
    BEFORE UPDATE ON user_skills
    FOR EACH ROW EXECUTE FUNCTION trg_touch_updated_at();

CREATE TRIGGER shortlists_touch
    BEFORE UPDATE ON shortlists
    FOR EACH ROW EXECUTE FUNCTION trg_touch_updated_at();


-- ── Подтверждения: запрет самоподтверждения, владелец объекта, переходы статусов ─
CREATE OR REPLACE FUNCTION trg_confirmations_validate() RETURNS trigger AS $$
DECLARE
    v_owner_id integer;
BEGIN
    IF NEW.requester_id = NEW.confirmer_id THEN
        RAISE EXCEPTION 'Нельзя подтвердить самого себя'
            USING ERRCODE = 'check_violation', HINT = 'SELF_CONFIRMATION';
    END IF;

    IF TG_OP = 'INSERT' THEN
        -- Подтверждать можно только собственные навыки/опыт/образование запрашивающего
        v_owner_id := CASE NEW.confirmation_type
            WHEN 'skill'      THEN (SELECT user_id FROM user_skills       WHERE id = NEW.user_skill_id)
            WHEN 'experience' THEN (SELECT user_id FROM work_experiences  WHERE id = NEW.work_experience_id)
            WHEN 'education'  THEN (SELECT user_id FROM education_records WHERE id = NEW.education_record_id)
        END;
        IF v_owner_id IS DISTINCT FROM NEW.requester_id THEN
            RAISE EXCEPTION 'Подтверждаемый объект не принадлежит пользователю'
                USING ERRCODE = 'check_violation', HINT = 'CONFIRMATION_SUBJECT_MISMATCH';
        END IF;
        IF NEW.status <> 'pending' AND NEW.responded_at IS NULL THEN
            NEW.responded_at := now();
        END IF;
    END IF;

    IF TG_OP = 'UPDATE' AND NEW.status IS DISTINCT FROM OLD.status THEN
        -- Допустимые переходы: pending → accepted|rejected, accepted → revoked
        IF NOT (
            (OLD.status = 'pending' AND NEW.status IN ('accepted', 'rejected'))
            OR (OLD.status = 'accepted' AND NEW.status = 'revoked')
        ) THEN
            RAISE EXCEPTION 'Недопустимая смена статуса подтверждения: % → %', OLD.status, NEW.status
                USING ERRCODE = 'check_violation', HINT = 'INVALID_STATUS_TRANSITION';
        END IF;
        IF NEW.status IN ('accepted', 'rejected') THEN
            NEW.responded_at := now();
        ELSIF NEW.status = 'revoked' THEN
            NEW.revoked_at := now();
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER confirmations_validate
    BEFORE INSERT OR UPDATE ON confirmations
    FOR EACH ROW EXECUTE FUNCTION trg_confirmations_validate();


-- ── Журнал смены ролей ─────────────────────────────────────────────────────
-- Инициатор изменения передаётся приложением через set_config('app.current_user_id', ...)
CREATE OR REPLACE FUNCTION trg_users_log_role_change() RETURNS trigger AS $$
BEGIN
    INSERT INTO role_history (user_id, old_role, new_role, changed_by_id, changed_at)
    VALUES (
        NEW.id,
        CASE WHEN TG_OP = 'UPDATE' THEN OLD.role END,
        NEW.role,
        NULLIF(current_setting('app.current_user_id', true), '')::integer,
        now()
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER users_log_role_insert
    AFTER INSERT ON users
    FOR EACH ROW EXECUTE FUNCTION trg_users_log_role_change();

CREATE TRIGGER users_log_role_change
    AFTER UPDATE OF role ON users
    FOR EACH ROW
    WHEN (OLD.role IS DISTINCT FROM NEW.role)
    EXECUTE FUNCTION trg_users_log_role_change();


-- ── Пересчёт рейтинга ──────────────────────────────────────────────────────
-- Индекс компетенций (0–100) = 30% образование + 30% опыт + 40% подтверждения:
--   образование: ВУЗ 50, СПО 30, курсы 15, сертификат 10 баллов за запись (макс. 100);
--   опыт: 10 лет суммарного стажа = 100 (периоды не пересекаются, поэтому просто сумма);
--   подтверждения: каждое принятое подтверждение = 10 баллов (макс. 100).
-- Уровень доверия сообщества (0–100) = 85% × доля принятых подтверждений, выданных
--   верифицированными (через Госуслуги) пользователями + 5 за каждый подключённый сервис.
-- Отклонённые запросы не влияют на рейтинг.
CREATE OR REPLACE FUNCTION recalc_user_rating(p_user_id integer, p_reason varchar)
RETURNS void AS $$
DECLARE
    v_education numeric;
    v_experience numeric;
    v_confirmation numeric;
    v_competency numeric;
    v_trust numeric;
    v_accepted integer;
    v_accepted_verified integer;
    v_integrations integer;
    v_old ratings%ROWTYPE;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM users WHERE id = p_user_id) THEN
        RETURN;  -- пользователь удаляется каскадно
    END IF;

    SELECT LEAST(100, COALESCE(SUM(CASE education_type
                WHEN 'higher' THEN 50 WHEN 'secondary' THEN 30
                WHEN 'course' THEN 15 WHEN 'certificate' THEN 10 ELSE 0 END), 0))
    INTO v_education
    FROM education_records WHERE user_id = p_user_id;

    SELECT LEAST(100, COALESCE(SUM(LEAST(COALESCE(end_date, CURRENT_DATE), CURRENT_DATE) - start_date), 0)
                      / (365.25 * 10) * 100)
    INTO v_experience
    FROM work_experiences WHERE user_id = p_user_id;

    SELECT COUNT(*),
           COUNT(*) FILTER (WHERE EXISTS (
               SELECT 1 FROM user_integrations ui
               WHERE ui.user_id = c.confirmer_id AND ui.provider = 'gosuslugi' AND ui.status = 'connected'))
    INTO v_accepted, v_accepted_verified
    FROM confirmations c
    WHERE c.requester_id = p_user_id AND c.status = 'accepted';

    SELECT COUNT(*) INTO v_integrations
    FROM user_integrations WHERE user_id = p_user_id AND status = 'connected';

    v_confirmation := LEAST(100, v_accepted * 10);
    v_competency := round(0.3 * v_education + 0.3 * v_experience + 0.4 * v_confirmation, 2);
    v_trust := round(LEAST(100,
        CASE WHEN v_accepted > 0 THEN 85.0 * v_accepted_verified / v_accepted ELSE 0 END
        + 5 * v_integrations), 2);
    v_education := round(v_education, 2);
    v_experience := round(v_experience, 2);
    v_confirmation := round(v_confirmation, 2);

    SELECT * INTO v_old FROM ratings WHERE user_id = p_user_id FOR UPDATE;

    IF NOT FOUND THEN
        INSERT INTO ratings (user_id, competency_index, trust_level, education_score, experience_score,
                             confirmation_score, confirmations_count, updated_at)
        VALUES (p_user_id, v_competency, v_trust, v_education, v_experience,
                v_confirmation, v_accepted, now());
    ELSIF v_old.competency_index = v_competency AND v_old.trust_level = v_trust
          AND v_old.confirmations_count = v_accepted THEN
        RETURN;  -- ничего не изменилось — историю не пополняем
    ELSE
        UPDATE ratings SET
            competency_index = v_competency, trust_level = v_trust,
            education_score = v_education, experience_score = v_experience,
            confirmation_score = v_confirmation, confirmations_count = v_accepted,
            updated_at = now()
        WHERE user_id = p_user_id;

        IF v_old.competency_index <> v_competency OR v_old.trust_level <> v_trust THEN
            INSERT INTO notifications (user_id, notification_type, title, content, created_at)
            VALUES (p_user_id, 'rating_change', 'Рейтинг изменён',
                    format('Индекс компетенций: %s%% (%s), доверие сообщества: %s%% (%s)',
                           v_competency, to_char(v_competency - v_old.competency_index, 'SG990.00'),
                           v_trust, to_char(v_trust - v_old.trust_level, 'SG990.00')),
                    now());
        END IF;
    END IF;

    INSERT INTO rating_history (user_id, competency_index, trust_level, education_score,
                                experience_score, confirmation_score, reason, recorded_at)
    VALUES (p_user_id, v_competency, v_trust, v_education, v_experience, v_confirmation, p_reason, now());
END;
$$ LANGUAGE plpgsql;


CREATE OR REPLACE FUNCTION trg_confirmations_recalc_rating() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        PERFORM recalc_user_rating(OLD.requester_id, 'confirmation_deleted');
        RETURN OLD;
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.status IS NOT DISTINCT FROM OLD.status THEN
        RETURN NEW;
    END IF;
    PERFORM recalc_user_rating(NEW.requester_id, 'confirmation_' || NEW.status);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER confirmations_recalc_rating
    AFTER INSERT OR UPDATE OF status OR DELETE ON confirmations
    FOR EACH ROW EXECUTE FUNCTION trg_confirmations_recalc_rating();


CREATE OR REPLACE FUNCTION trg_profile_recalc_rating() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        PERFORM recalc_user_rating(OLD.user_id, TG_ARGV[0]);
    ELSE
        PERFORM recalc_user_rating(NEW.user_id, TG_ARGV[0]);
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER education_records_recalc_rating
    AFTER INSERT OR UPDATE OF education_type, user_id OR DELETE ON education_records
    FOR EACH ROW EXECUTE FUNCTION trg_profile_recalc_rating('education_changed');

CREATE TRIGGER work_experiences_recalc_rating
    AFTER INSERT OR UPDATE OF start_date, end_date, user_id OR DELETE ON work_experiences
    FOR EACH ROW EXECUTE FUNCTION trg_profile_recalc_rating('experience_changed');


-- Интеграции: меняют доверие самого пользователя, а верификация через Госуслуги —
-- ещё и доверие всех, кому он выдал подтверждения
CREATE OR REPLACE FUNCTION trg_integrations_recalc_rating() RETURNS trigger AS $$
DECLARE
    v_row user_integrations%ROWTYPE;
    v_requester integer;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_row := OLD;
    ELSE
        v_row := NEW;
    END IF;
    PERFORM recalc_user_rating(v_row.user_id, 'integration_changed');
    IF v_row.provider = 'gosuslugi' THEN
        FOR v_requester IN
            SELECT DISTINCT requester_id FROM confirmations
            WHERE confirmer_id = v_row.user_id AND status = 'accepted'
        LOOP
            PERFORM recalc_user_rating(v_requester, 'confirmer_verified');
        END LOOP;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER user_integrations_recalc_rating
    AFTER INSERT OR UPDATE OF status OR DELETE ON user_integrations
    FOR EACH ROW EXECUTE FUNCTION trg_integrations_recalc_rating();


-- Новый пользователь сразу получает запись рейтинга
CREATE OR REPLACE FUNCTION trg_users_init_rating() RETURNS trigger AS $$
BEGIN
    PERFORM recalc_user_rating(NEW.id, 'registration');
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER users_init_rating
    AFTER INSERT ON users
    FOR EACH ROW EXECUTE FUNCTION trg_users_init_rating();
"""

DOWNGRADE_SQL = """
DROP TRIGGER IF EXISTS users_init_rating ON users;
DROP TRIGGER IF EXISTS user_integrations_recalc_rating ON user_integrations;
DROP TRIGGER IF EXISTS work_experiences_recalc_rating ON work_experiences;
DROP TRIGGER IF EXISTS education_records_recalc_rating ON education_records;
DROP TRIGGER IF EXISTS confirmations_recalc_rating ON confirmations;
DROP TRIGGER IF EXISTS users_log_role_change ON users;
DROP TRIGGER IF EXISTS users_log_role_insert ON users;
DROP TRIGGER IF EXISTS confirmations_validate ON confirmations;
DROP TRIGGER IF EXISTS shortlists_touch ON shortlists;
DROP TRIGGER IF EXISTS user_skills_touch ON user_skills;
DROP TRIGGER IF EXISTS work_experiences_20_no_overlap ON work_experiences;
DROP TRIGGER IF EXISTS work_experiences_10_validate_dates ON work_experiences;
DROP TRIGGER IF EXISTS education_records_10_validate_dates ON education_records;
DROP TRIGGER IF EXISTS users_validate ON users;

DROP FUNCTION IF EXISTS trg_users_init_rating();
DROP FUNCTION IF EXISTS trg_integrations_recalc_rating();
DROP FUNCTION IF EXISTS trg_profile_recalc_rating();
DROP FUNCTION IF EXISTS trg_confirmations_recalc_rating();
DROP FUNCTION IF EXISTS recalc_user_rating(integer, varchar);
DROP FUNCTION IF EXISTS trg_users_log_role_change();
DROP FUNCTION IF EXISTS trg_confirmations_validate();
DROP FUNCTION IF EXISTS trg_touch_updated_at();
DROP FUNCTION IF EXISTS trg_work_experiences_no_overlap();
DROP FUNCTION IF EXISTS trg_validate_period_dates();
DROP FUNCTION IF EXISTS trg_users_validate();

DROP INDEX IF EXISTS ix_user_skills_updated_at;
DROP INDEX IF EXISTS ix_confirmations_responded_at;
DROP INDEX IF EXISTS uq_confirmations_pending_item;
ALTER TABLE work_experiences DROP CONSTRAINT IF EXISTS ex_work_experiences_no_overlap;
"""


def _execute_script(sql: str) -> None:
    """Выполняет многооператорный SQL-скрипт напрямую через asyncpg (simple query protocol),
    в той же транзакции миграции и без обработки bind-параметров SQLAlchemy."""
    driver_connection = op.get_bind().connection.driver_connection
    await_only(driver_connection.execute(sql))


def upgrade() -> None:
    _execute_script(ROLES_SQL)
    _execute_script(CONSTRAINTS_SQL)
    _execute_script(FUNCTIONS_SQL)


def downgrade() -> None:
    _execute_script(DOWNGRADE_SQL)
