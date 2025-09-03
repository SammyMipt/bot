# L3_Teacher v0.10 — Техническая спецификация (консолидированная)

> Роль: **Преподаватель (Teacher)**
> Версия: **v0.10**
> Синхронизация: L1 v0.10, L2_Teacher v0.10, L3_Common v0.10
> БД: **SQLite** с производительностными оптимизациями

---

## 0. Ключевые принципы

- **Матрица назначений — жёсткая 1:1:** `(student, week) → teacher` (unique)
- **Лимиты валидируются только при создании** (create_day_slots/apply_preset)
- **Error Registry только из L3_Common** — новые коды не вводятся
- **TimeService API строго из L3_Common**
- **Callback payload ≤64B** — UUID+StateStore по необходимости
- **Soft delete для слотов** — аудит и метрики сохраняются

---

## 1. Архитектура компонентов

```
bot/routers/teacher/
├── registration.py       # Регистрация Teacher
├── menu.py              # Главное меню
├── schedule_create.py   # Создание расписания (manual/preset)
├── schedule_manage.py   # Управление слотами
├── presets.py           # CRUD пресетов
├── materials.py         # Методические материалы
└── checkwork.py         # Проверка работ (по дате/студенту)

services/teacher/
├── auth_service.py      # require_teacher, validate_code
├── schedule_service.py  # Слоты, лимиты, валидации
├── preset_service.py    # Пресеты (личные + глобальные)
├── assignment_service.py # Матрица назначений, поиск студентов
├── grade_service.py     # Базовые оценки (без override)
├── submission_service.py # Решения студентов
└── materials_service.py # Активные материалы

storage/repos/
├── sqlite/             # SQLite реализации
└── memory/             # InMemory для тестов

utils/
├── callback_codec.py   # Кодирование payload
├── text_normalizer.py  # normalize_text для поиска
├── validators.py       # Бизнес-валидации
└── pagination.py       # Списки с пагинацией
```

---

## 2. Модель данных (SQLite)

### 2.1. Основные таблицы

```sql
-- Пользователи
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER UNIQUE NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner','teacher','student')),
    surname TEXT,
    name TEXT,
    patronymic TEXT,
    email TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Нормализованные поля для поиска
    surname_norm TEXT,
    fullname_norm TEXT
);

CREATE INDEX ix_users_tg_id ON users(tg_id);
CREATE INDEX ix_users_role ON users(role);
CREATE INDEX ix_users_surname_norm ON users(surname_norm);
CREATE INDEX ix_users_fullname_norm ON users(fullname_norm);

-- Профили преподавателей
CREATE TABLE teachers (
    id INTEGER PRIMARY KEY,
    user_id INTEGER UNIQUE NOT NULL REFERENCES users(id),
    display_name TEXT,
    user_tz TEXT,
    weekly_limit INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
);

-- Недели курса
CREATE TABLE weeks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL, -- W01, W02, ...
    title TEXT NOT NULL,
    description TEXT,
    deadline_ts_utc TEXT NOT NULL,
    course_tz TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX ix_weeks_code ON weeks(code);
CREATE INDEX ix_weeks_active ON weeks(is_active);
```

### 2.2. Матрица назначений (жёсткая 1:1)

```sql
CREATE TABLE assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES users(id),
    week_id INTEGER NOT NULL REFERENCES weeks(id),
    teacher_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (student_id, week_id) -- Жёсткая матрица 1:1
);

CREATE INDEX ix_assignments_teacher_week ON assignments(teacher_id, week_id);
CREATE INDEX ix_assignments_student ON assignments(student_id);
```

### 2.3. Слоты и записи

```sql
CREATE TABLE slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id INTEGER NOT NULL REFERENCES users(id),
    date_utc TEXT NOT NULL, -- YYYY-MM-DD
    time_from_utc TEXT NOT NULL, -- HH:MM:SS
    time_to_utc TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('online','offline')),
    location TEXT, -- URL или аудитория
    capacity INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed','past')),
    is_deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (teacher_id, date_utc, time_from_utc, time_to_utc, location)
);

CREATE INDEX ix_slots_teacher_date ON slots(teacher_id, date_utc) WHERE is_deleted = 0;
CREATE INDEX ix_slots_date_status ON slots(date_utc, status) WHERE is_deleted = 0;

CREATE TABLE slot_bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id INTEGER NOT NULL REFERENCES slots(id),
    student_id INTEGER NOT NULL REFERENCES users(id),
    week_id INTEGER NOT NULL REFERENCES weeks(id),
    status TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('booked','cancelled')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ix_bookings_slot ON slot_bookings(slot_id);
CREATE INDEX ix_bookings_student_week ON slot_bookings(student_id, week_id);
```

### 2.4. Пресеты расписания

```sql
CREATE TABLE presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('online','offline')),
    location TEXT,
    weekday INTEGER NOT NULL CHECK (weekday BETWEEN 1 AND 7), -- 1=Пн
    time_from_min INTEGER NOT NULL, -- минуты от полуночи
    duration_min INTEGER NOT NULL,
    capacity INTEGER NOT NULL,
    is_global INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ix_presets_owner ON presets(owner_user_id);
CREATE INDEX ix_presets_global ON presets(is_global);
```

### 2.5. Оценки и переопределения

```sql
-- Базовые оценки от преподавателей
CREATE TABLE grades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER UNIQUE NOT NULL REFERENCES assignments(id),
    score_int INTEGER NOT NULL CHECK (score_int BETWEEN 1 AND 10),
    score_letter TEXT CHECK (score_letter IN ('A','B','C','D')),
    comment TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ix_grades_assignment ON grades(assignment_id);

-- Переопределения владельца курса
CREATE TABLE grade_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id),
    owner_id INTEGER NOT NULL REFERENCES users(id),
    new_score_int INTEGER NOT NULL CHECK (new_score_int BETWEEN 1 AND 10),
    new_score_letter TEXT CHECK (new_score_letter IN ('A','B','C','D')),
    new_comment TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ix_grade_overrides_assignment_date ON grade_overrides(assignment_id, created_at DESC);
```

### 2.6. Материалы и решения

```sql
CREATE TABLE materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_id INTEGER NOT NULL REFERENCES weeks(id),
    type TEXT NOT NULL CHECK (type IN ('prep','teacher','notes','slides','video')),
    visibility TEXT NOT NULL CHECK (visibility IN ('student','teacher')),
    file_ref TEXT, -- путь к файлу
    link TEXT,     -- или ссылка
    size_bytes INTEGER,
    checksum TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ix_materials_week_type ON materials(week_id, type, is_active);

CREATE TABLE submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES users(id),
    week_id INTEGER NOT NULL REFERENCES weeks(id),
    storage_ref TEXT NOT NULL, -- JSON с файлами
    size_total_mb REAL NOT NULL,
    files_count INTEGER NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ix_submissions_student_week ON submissions(student_id, week_id, is_active);
```

---

## 3. Сервисы и контракты

### 3.1. AuthService
```python
async def require_teacher(tg_id: int) -> TeacherDTO:
    """Проверка роли Teacher, возврат профиля или E_ACCESS_DENIED"""

async def validate_course_code(code: str) -> bool:
    """Валидация секретного кода курса"""
```

### 3.2. ScheduleService
```python
async def create_day_slots(
    teacher_id: int,
    date: date,
    window_from: time,
    window_to: time,
    duration_min: int,
    mode: str,
    location: str,
    capacity: int
) -> CreateSlotsResult:
    """Создание эквидистантных слотов на день с валидацией лимитов"""

async def apply_preset(
    teacher_id: int,
    preset_id: int,
    period_from: date,
    period_to: date
) -> CreateSlotsResult:
    """Применение пресета к периоду дат"""

async def list_slots(
    teacher_id: int,
    date_filter: date | None = None,
    limit: int = 20,
    offset: int = 0
) -> List[SlotCard]:
    """Список слотов преподавателя"""

async def open_slot(slot_id: int) -> None:
async def close_slot(slot_id: int) -> None:
async def delete_slot(slot_id: int) -> None:
    """Управление статусом слотов (soft delete)"""

async def list_slot_students(slot_id: int) -> List[StudentCard]:
    """Студенты, записанные в слот"""
```

### 3.3. AssignmentService
```python
async def get_by_student_week(student_id: int, week_id: int) -> AssignmentDTO:
    """Получить назначение по студенту и неделе"""

async def list_student_weeks_for_teacher(
    student_id: int,
    teacher_id: int
) -> List[WeekCard]:
    """Недели студента, закреплённые за преподавателем"""

async def find_students_by_surname(
    teacher_id: int,
    query: str,
    limit: int = 20,
    offset: int = 0
) -> List[StudentCard]:
    """Поиск студентов по фамилии среди назначенных"""
```

### 3.4. GradeService
```python
async def upsert_grade(
    assignment_id: int,
    score_int: int,
    score_letter: str | None = None,
    comment: str | None = None
) -> None:
    """Выставление/обновление базовой оценки"""

async def get_grade(assignment_id: int) -> GradeDTO | None:
    """Получение базовой оценки (без override)"""
```

### 3.5. MaterialsService, SubmissionService, PresetService
```python
# MaterialsService
async def list_weeks() -> List[WeekCard]
async def get_week_materials(week_id: int, teacher_view: bool = True) -> List[MaterialCard]

# SubmissionService
async def get_student_submission(student_id: int, week_id: int) -> SubmissionDTO | None

# PresetService
async def create_preset(teacher_id: int, preset_data: PresetCreateDTO) -> int
async def list_presets(teacher_id: int, include_global: bool = True) -> List[PresetCard]
async def delete_preset(preset_id: int, teacher_id: int) -> None
```

---

## 4. Текстовая нормализация и поиск

### 4.1. Функция normalize_text (Python)
```python
import unicodedata
import re

_space_re = re.compile(r"\s+")

def normalize_text(s: str | None) -> str:
    """NULL-safe нормализация для поиска"""
    if not s:
        return ""

    # Удаление диакритики
    nfkd = unicodedata.normalize("NFKD", s)
    no_marks = "".join(ch for ch in nfkd if unicodedata.category(ch) != "Mn")

    # Нижний регистр + схлопывание пробелов
    lowered = no_marks.lower().strip()
    return _space_re.sub(" ", lowered)
```

### 4.2. Поиск студентов с нормализацией
```python
async def find_students_by_surname(
    db, teacher_id: int, query: str, limit: int = 20, offset: int = 0
) -> List[StudentCard]:
    q_norm = normalize_text(query)

    cursor = await db.execute("""
        SELECT DISTINCT st.id, st.surname, st.name, st."group"
        FROM users st
        JOIN assignments a ON a.student_id = st.id
        WHERE a.teacher_id = ?
          AND st.surname_norm LIKE ? || '%'
        ORDER BY st.surname, st.name
        LIMIT ? OFFSET ?
    """, (teacher_id, q_norm, limit, offset))

    return [StudentCard.from_row(row) for row in await cursor.fetchall()]
```

---

## 5. FSM и состояния

### 5.1. Структура состояний
```python
from enum import Enum, auto

# Регистрация
class RegistrationTeacherStates(Enum):
    enter_code = auto()
    confirm = auto()
    done = auto()

# Создание расписания - точка входа
class ScheduleCreateEntryState(Enum):
    choose_mode = auto()  # "Создать слоты" vs "Применить пресет"

# Ветка manual (создание на день)
class ScheduleCreateManualStates(Enum):
    format = auto()
    date = auto()
    time = auto()
    duration = auto()
    capacity = auto()
    preview = auto()
    create = auto()

# Ветка preset (применение)
class ScheduleCreatePresetStates(Enum):
    select_preset = auto()
    period = auto()
    preview = auto()
    create = auto()

# Остальные модули
class ScheduleManageStates(Enum):
    pick_date = auto()
    list_slots = auto()
    slot_card = auto()

class PresetsStates(Enum):
    create_name = auto()
    format = auto()
    weekday = auto()
    time = auto()
    duration = auto()
    capacity = auto()
    preview = auto()

class CheckWorkStates(Enum):
    choose_entry = auto()
    # by_date ветка
    pick_date = auto()
    list_slots = auto()
    slot_students = auto()
    student_card = auto()
    grade_panel = auto()
    # by_student ветка
    input_surname = auto()
    pick_result = auto()
    student_weeks = auto()
    student_week_card = auto()
```

### 5.2. Переходы между состояниями
- Из `ScheduleCreateEntryState.choose_mode`:
  - кнопка "🧱 Создать слоты" → `ScheduleCreateManualStates.format`
  - кнопка "⚡ Применить пресет" → `ScheduleCreatePresetStates.select_preset`
- Возврат "⬅️ Назад" из любой точки ветки → `choose_mode`

---

## 6. Callback Data и StateStore

### 6.1. Схема кодирования (≤64B)
```python
# Базовые ключи (L3_Common)
r = role          # t=teacher
a = action        # sch=schedule, ckw=checkwork
w = week          # 1-99
i = id           # числовой ID
d = date         # YYYYMMDD
p = page         # пагинация
k = uuid         # StateStore ключ

# Примеры
"r=t;a=sch;d=20250901"           # 18 байт
"r=t;a=ckw;w=7;s=123"           # 19 байт
"r=t;a=ckw;w=7;s=123;d=20250901" # 30 байт
```

### 6.2. Политика использования UUID
- **Inline payload по умолчанию** пока прогноз ≤48 байт (запас)
- **UUID + StateStore** если:
  - Превышение 48 байт
  - Переменная длина данных (URLs, фильтры)
  - Составные навигации с множественными параметрами

### 6.3. StateStore (Redis/Memory)
```python
# TTL = 15 минут
# При истечении → FSM reset + сообщение "⛔ Сессия истекла, начните заново"

class StateStore:
    async def save_data(self, data: dict, ttl: int = 900) -> str:
        """Сохранить данные, вернуть UUID ключ"""

    async def get_data(self, key: str) -> dict | None:
        """Получить данные по ключу"""

    async def delete_data(self, key: str) -> None:
        """Удалить данные"""
```

---

## 7. Валидации и бизнес-правила

### 7.1. Алгоритмы валидации
```python
# Длительность слота
def validate_duration(duration_min: int) -> None:
    if not (10 <= duration_min <= 120):
        raise DomainError("E_INPUT_INVALID", "Длительность вне диапазона")

# Суточный лимит (≤6 часов)
async def validate_daily_limit(
    db, teacher_id: int, date: date, new_duration_sum: int
) -> None:
    cursor = await db.execute("""
        SELECT COALESCE(SUM(
            (strftime('%s', time_to_utc) - strftime('%s', time_from_utc)) / 60
        ), 0) AS minutes
        FROM slots
        WHERE teacher_id = ? AND date_utc = ? AND is_deleted = 0
    """, (teacher_id, date.isoformat()))

    existing_minutes = (await cursor.fetchone())[0]
    if existing_minutes + new_duration_sum > 360:
        raise DomainError("E_DURATION_EXCEEDED", "Превышен лимит 6 часов")

# Вместимость
def validate_capacity(mode: str, capacity: int) -> None:
    if mode == "online" and capacity > 3:
        raise DomainError("E_CAP_EXCEEDED", "Онлайн ≤3")
    elif mode == "offline" and capacity > 50:
        raise DomainError("E_CAP_EXCEEDED", "Очно ≤50")

# Пересечения интервалов [start, end)
async def validate_no_conflicts(
    db, teacher_id: int, date: date,
    new_from: time, new_to: time, location: str
) -> None:
    cursor = await db.execute("""
        SELECT EXISTS (
            SELECT 1 FROM slots s
            WHERE s.teacher_id = ? AND s.date_utc = ? AND s.is_deleted = 0
              AND NOT (? <= s.time_from_utc OR s.time_to_utc <= ?)
        )
    """, (teacher_id, date.isoformat(), new_to.isoformat(), new_from.isoformat()))

    if (await cursor.fetchone())[0]:
        raise DomainError("E_ALREADY_EXISTS", "Конфликт времени")
```

---

## 8. Обработка ошибок

### 8.1. Маппинг на UI тексты (L3_Common Error Registry)
```python
ERROR_MESSAGES = {
    "E_CODE_INVALID": "⛔ Неверный код",
    "E_NOT_FOUND": "⛔ Объект не найден",
    "E_INPUT_INVALID": "⛔ Некорректный ввод",
    "E_DURATION_EXCEEDED": "⚠️ Превышен лимит 6 часов",
    "E_CAP_EXCEEDED": "⚠️ Превышена вместимость",
    "E_ALREADY_EXISTS": "⚠️ Дубликат/конфликт",
    "E_ACCESS_DENIED": "⛔ Нет прав для действия",
    "E_STATE_INVALID": "⛔ Некорректное состояние",
    "E_STORAGE_IO": "⛔ Ошибка сохранения"
}

async def handle_domain_error(update, error: DomainError):
    message = ERROR_MESSAGES.get(error.code, "⛔ Произошла ошибка")
    await update.message.reply_text(message)
```

---

## 9. Миграции SQLite

### 9.1. Создание базовых таблиц
```sql
-- Выполнить в maintenance window с WAL режимом
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;

-- Создание всех таблиц из раздела 2
-- ...

-- Заполнение нормализованных полей (батчами)
UPDATE users SET
    surname_norm = lower(trim(surname)),
    fullname_norm = lower(trim(COALESCE(surname, '') || ' ' || COALESCE(name, '')))
WHERE surname_norm IS NULL;
```

### 9.2. Триггеры поддержания целостности
```sql
-- Автообновление нормализованных полей
CREATE TRIGGER trg_users_norm_update
AFTER UPDATE OF surname, name ON users
FOR EACH ROW
WHEN NEW.surname != OLD.surname OR NEW.name != OLD.name
BEGIN
    UPDATE users SET
        surname_norm = lower(trim(NEW.surname)),
        fullname_norm = lower(trim(COALESCE(NEW.surname, '') || ' ' || COALESCE(NEW.name, '')))
    WHERE id = NEW.id;
END;

-- Автообновление updated_at для grades
CREATE TRIGGER trg_grades_updated_at
AFTER UPDATE ON grades
FOR EACH ROW
BEGIN
    UPDATE grades SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
```

---

## 10. Тестирование

### 10.1. Unit тесты сервисов
```python
@pytest.mark.asyncio
async def test_create_day_slots_validates_duration():
    service = ScheduleService(mock_repo)

    with pytest.raises(DomainError) as exc:
        await service.create_day_slots(
            teacher_id=1, date=date.today(),
            window_from=time(9,0), window_to=time(11,0),
            duration_min=5,  # < 10 минут
            mode="online", location="url", capacity=2
        )

    assert exc.value.code == "E_INPUT_INVALID"

@pytest.mark.asyncio
async def test_daily_limit_validation():
    # Моки для existing slots = 5 часов
    # Попытка добавить еще 2 часа → E_DURATION_EXCEEDED
    pass

@pytest.mark.asyncio
async def test_interval_overlap_detection():
    # [10:00,10:30) vs [10:30,11:00) → no conflict
    # [10:00,10:30) vs [10:15,11:00) → conflict
    pass
```

### 10.2. Integration тесты
```python
@pytest.mark.asyncio
async def test_complete_schedule_creation_flow():
    """Полный flow: choose_mode → manual → preview → create"""

@pytest.mark.asyncio
async def test_student_search_with_assignments():
    """Поиск студентов учитывает матрицу назначений"""

@pytest.mark.asyncio
async def test_grade_upsert_and_retrieval():
    """Выставление оценки и получение без override"""
```

---

## 11. Производительность и мониторинг

### 11.1. Критические метрики
- Время создания слотов (target: <500ms для 10 слотов)
- Время поиска студентов (target: <200ms для 100 записей)
- Утилизация StateStore (TTL cleanup, memory usage)
- Частота конфликтов при создании слотов

### 11.2. Индексы для производительности
```sql
-- Основные рабочие индексы уже в схеме
-- Дополнительные для аналитики:
CREATE INDEX ix_slots_teacher_created_at ON slots(teacher_id, created_at);
CREATE INDEX ix_bookings_created_at ON slot_bookings(created_at);
CREATE INDEX ix_grades_updated_at ON grades(updated_at);
```

---

## 12. Соответствие документам

### 12.1. Соответствие L1 v0.10
- ✅ Жёсткая матрица назначений `(student, week) → teacher`
- ✅ Лимиты слотов: онлайн ≤3, очно ≤50, ≤6ч/день
- ✅ Материалы: teacher-only vs student, активные версии
- ✅ Корректировка оценок Owner (через grade_overrides)
- ✅ Шаблоны расписания (глобальные + личные)

### 12.2. Соответствие L2_Teacher v0.10
- ✅ Все UI сценарии покрыты техническими контрактами
- ✅ FSM состояния соответствуют UX flows
- ✅ Callback стратегия поддерживает все навигации
- ✅ Error mapping на человекочитаемые сообщения

### 12.3. Соответствие L3_Common v0.10
- ✅ Repository pattern через Protocol интерфейсы
- ✅ Error Registry без дублирования кодов
- ✅ TimeService API (format_dual_tz, parse_deadline)
- ✅ Callback data ≤64B + StateStore strategy
- ✅ Structured logging + audit trail
- ✅ FSM architecture с aiogram

---

**Документ готов к реализации разработчиками.**
