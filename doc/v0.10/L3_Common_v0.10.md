
# L3_Common — Архитектурные решения и общие паттерны (v0.10)

> Документ для разработчиков. Содержит технические решения и стандарты, общие для всех ролей.
> Синхронизирован с L1 v0.10 и L2/L3 {Owner|Teacher|Student} v0.10.

---

## 1. Архитектурные решения

### Технологический стек
- **Фреймворк**: aiogram v3 (FSM, middleware)
- **Хранилище**: SQLite через SQLAlchemy (production), InMemory (тесты)
- **Dependency Injection**: строгий DI через Protocol/ABC интерфейсы

### Обоснование
- aiogram выбран вместо python-telegram-bot (поддержка FSM, middleware)
- SQLite вместо CSV — транзакционность и конкурентный доступ
- Repository pattern — тестируемость и сопровождение

---

## 2. Callback Data Strategy

### Проблема
Ограничение Telegram: 64 байта на payload.

### Решение
- Короткие ключи: `r=o;a=mat;w=1` вместо длинных строк
- State Store: хранение больших данных по UUID ключам
- Формат: `a=action;k=uuid`

### Стандарты callback_data
| Параметр | Ключ | Формат | Пример |
|----------|------|--------|--------|
| Role     | r    | o/t/s  | r=o    |
| Action   | a    | 1-3 символа | a=mat |
| Week     | w    | 1-2 цифры   | w=1   |
| Type     | t    | 1 символ    | t=p   |
| ID       | i    | число       | i=123 |
| Page     | p    | число       | p=2   |
| StateKey | k    | UUID        | k=abc123 |

---

## 3. Time Management

### Проблема
Ошибки при конвертации временных зон и переходах DST.

### Решение: TimeService
Единый модуль `app/services/common/time_service.py`

#### API (сигнатуры)
```python
from datetime import datetime

def parse_deadline(dt_str: str, course_tz: str) -> datetime: ...
def format_for_student(utc_dt: datetime, course_tz: str) -> str: ...
def format_dual_tz(utc_dt: datetime, course_tz: str, user_tz: str) -> str: ...
def current_course_week(course_start: datetime, course_tz: str) -> int: ...
```

#### Правила
- Хранение: все timestamps в UTC
- Отображение: course_tz как основное, при необходимости dual
- Валидация: timezone-aware, DST корректность

---

## 4. Repository Pattern, DI и доступ к данным

### Архитектура слоев
```
Handlers → Services → Repositories → SQLite/SQLAlchemy | InMemory
```

### Domain Ports (интерфейсы — расширенные контракты)
`app/domain/ports/` (фрагменты протоколов, отражающие реально используемые операции):

```python
from typing import Protocol, Iterable, Optional, Sequence, Mapping
from datetime import datetime
from app.domain.models import User, Slot, Booking, Material, Grade, Week

class Transaction(Protocol):
    async def __aenter__(self) -> "Transaction": ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...

class UserRepositoryProtocol(Protocol):
    async def get_by_id(self, user_id: int) -> Optional[User]: ...
    async def get_by_tg(self, tg_id: int) -> Optional[User]: ...
    async def list_by_role(self, role: str, *, active: Optional[bool] = None) -> Sequence[User]: ...
    async def upsert(self, user: User) -> User: ...
    def transaction(self) -> Transaction: ...

class WeekRepositoryProtocol(Protocol):
    async def list_all(self) -> Sequence[Week]: ...
    async def bulk_upsert(self, weeks: Iterable[Week]) -> int: ...
    async def get_by_code(self, code: str) -> Optional[Week]: ...  # Wxx

class SlotRepositoryProtocol(Protocol):
    async def create(self, slot: Slot) -> Slot: ...
    async def bulk_create(self, slots: Iterable[Slot]) -> Mapping[str,int]: ...  # {"created":N,"skipped_duplicates":K}
    async def list_by_teacher(self, teacher_id: int, *, date_from: Optional[datetime]=None, date_to: Optional[datetime]=None) -> Sequence[Slot]: ...
    async def toggle(self, slot_id: int, *, new_state: str) -> None: ...
    async def edit(self, slot_id: int, *, changes: Mapping[str, object]) -> None: ...
    async def delete(self, slot_id: int) -> None: ...
    async def list_open_for_student(self, student_id: int, week_code: str) -> Sequence[Slot]: ...

class BookingRepositoryProtocol(Protocol):
    async def create(self, booking: Booking) -> Booking: ...
    async def cancel(self, booking_id: int) -> None: ...
    async def get_active_by_student_week(self, student_id: int, week_code: str) -> Optional[Booking]: ...
    async def list_by_slot(self, slot_id: int) -> Sequence[Booking]: ...

class MaterialRepositoryProtocol(Protocol):
    async def get_active(self, week_code: str, mtype: str, *, visibility: str) -> Optional[Material]: ...
    async def add(self, material: Material) -> Material: ...
    async def soft_delete(self, material_id: int) -> None: ...
    async def download_url(self, material_id: int) -> str: ...  # возвращает ссылку/путь, не blob

class GradeRepositoryProtocol(Protocol):
    async def upsert(self, student_id: int, week_code: str, score: int, comment: Optional[str], origin: str) -> Grade: ...
    async def get(self, student_id: int, week_code: str) -> Optional[Grade]: ...
    async def list_by_student(self, student_id: int) -> Sequence[Grade]: ...

class AuditRepositoryProtocol(Protocol):
    async def save(self, event_code: str, payload: Mapping[str, object], *, actor_id: str, request_id: str, impersonated: Optional[Mapping[str,str]] = None) -> None: ...
```

> **Файлы — не в БД**: репозитории хранят только метаданные (`file_ref`, `size_bytes`, `checksum`). «Blob» живёт в файловом сторидже (`MATERIALS_PATH`, `SUBMISSIONS_PATH`).

### Dependency Injection: контейнер поставщиков
Простой пример контейнера, который разрешает `Protocol → Implementation` по `STORAGE_BACKEND`/`DATABASE_URL`:

```python
# app/di/container.py
from functools import lru_cache
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.domain.ports import (UserRepositoryProtocol, WeekRepositoryProtocol, SlotRepositoryProtocol,
                              BookingRepositoryProtocol, MaterialRepositoryProtocol, GradeRepositoryProtocol,
                              AuditRepositoryProtocol)
from app.repositories.sqlite import (SqlUserRepo, SqlWeekRepo, SqlSlotRepo, SqlBookingRepo, SqlMaterialRepo, SqlGradeRepo, SqlAuditRepo)
from app.repositories.memory import (MemUserRepo, MemWeekRepo, MemSlotRepo, MemBookingRepo, MemMaterialRepo, MemGradeRepo, MemAuditRepo)

class Container:
    def __init__(self, env: dict):
        self.env = env
        self.backend = env["STORAGE_BACKEND"]
        self.db_url = env.get("DATABASE_URL")

        if self.backend == "sqlite":
            assert self.db_url, "DATABASE_URL is required for sqlite backend"
            engine = create_async_engine(self.db_url, future=True)
            self.session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        else:
            self.session_factory = None  # memory backend

    @lru_cache
    def user_repo(self) -> UserRepositoryProtocol:
        return SqlUserRepo(self.session_factory) if self.backend == "sqlite" else MemUserRepo()

    @lru_cache
    def week_repo(self) -> WeekRepositoryProtocol:
        return SqlWeekRepo(self.session_factory) if self.backend == "sqlite" else MemWeekRepo()

    @lru_cache
    def slot_repo(self) -> SlotRepositoryProtocol:
        return SqlSlotRepo(self.session_factory) if self.backend == "sqlite" else MemSlotRepo()

    @lru_cache
    def booking_repo(self) -> BookingRepositoryProtocol:
        return SqlBookingRepo(self.session_factory) if self.backend == "sqlite" else MemBookingRepo()

    @lru_cache
    def material_repo(self) -> MaterialRepositoryProtocol:
        return SqlMaterialRepo(self.session_factory) if self.backend == "sqlite" else MemMaterialRepo()

    @lru_cache
    def grade_repo(self) -> GradeRepositoryProtocol:
        return SqlGradeRepo(self.session_factory) if self.backend == "sqlite" else MemGradeRepo()

    @lru_cache
    def audit_repo(self) -> AuditRepositoryProtocol:
        return SqlAuditRepo(self.session_factory) if self.backend == "sqlite" else MemAuditRepo()
```

**Встраивание в сервисы**: сервис принимает протоколы, а контейнер подаёт реализации.
```python
# app/services/teacher/schedule_service.py
class ScheduleService:
    def __init__(self, slots: SlotRepositoryProtocol, audit: AuditRepositoryProtocol):
        self.slots = slots
        self.audit = audit
```

**Подача в хендлеры** через middleware/фабрики:
```python
# app/bot/middlewares/di.py
from aiogram import BaseMiddleware
class DI(BaseMiddleware):
    def __init__(self, container: Container): self.c = container
    async def __call__(self, handler, event, data):
        data["container"] = self.c
        data["repos"] = {
            "slots": self.c.slot_repo(),
            "audit": self.c.audit_repo(),
        }
        return await handler(event, data)
```

---

## 5. FSM Architecture

### Использование
- Сценарии: регистрация, создание расписания, загрузка материалов, инициализация курса

### Группы состояний (примеры; полный перечень — в коде `app/bot/fsm/*`)
```python
# Инициализация курса (Owner)
class CourseInitStates(StatesGroup):
    entering_params = State()      # ввод названия, локали, TZ (IANA)
    uploading_weeks = State()      # загрузка weeks.csv
    confirming = State()           # подтверждение, предпросмотр
    committed = State()            # финализация

# Создание расписания (Teacher)
class SchedCreateStates(StatesGroup):
    choose_mode = State()          # quick/manual
    quick_select_preset = State()  # выбор пресета
    quick_preview = State()        # предпросмотр слотов
    quick_commit = State()         # массовое создание
    manual_edit = State()          # по одному слоту

# Работа с решениями студента (Student)
class SolutionStates(StatesGroup):
    selecting_week = State()       # выбор Wxx
    uploading_file = State()       # прикрепление файла
    confirming_replace = State()   # перезагрузка (reupload) подтверждение
```

> В L3 по ролям приводим только роль‑специфичные переходы; универсальные правила хранения/изоляции FSM — здесь.

---

## 6. Error Registry & Handling (единый реестр)

### Принципы
- **Один код — один смысл**; бизнес‑ошибки мапятся в UI‑тексты на уровне handler/service.
- **Структурированные логи** и **Idempotency** — обязательны.

### Коды ошибок (реестр)
| Код               | Категория        | Описание (инвариант)                            | Типичные источники |
|-------------------|------------------|--------------------------------------------------|--------------------|
| E_INPUT_INVALID   | Validation       | Некорректный ввод / формат                      | формы, CSV, email  |
| E_SIZE_LIMIT      | Validation       | Превышены лимиты размера/количества             | upload материалов/решений |
| E_ALREADY_EXISTS  | Conflict         | Объект/связка уже существует                    | повторная регистрация, дубль слота |
| E_NOT_FOUND       | Lookup           | Объект не найден                                | сущности, файлы    |
| E_ACCESS_DENIED   | Authorization    | Нет прав для действия                           | попытка действия не по роли |
| E_STATE_INVALID   | State/FSM        | Некорректное состояние процесса                  | несогласованные шаги FSM |
| E_STORAGE_IO      | Infrastructure   | Ошибка файловой системы/хранилища               | диск/облако        |
| E_CSV_SCHEMA      | Validation       | Неверные заголовки/структура CSV                 | weeks.csv, teachers.csv |
| E_WEEK_GAP        | Validation       | Разрыв в последовательности недель               | weeks.csv          |
| E_WEEK_DUP        | Validation       | Дублирующийся идентификатор недели               | weeks.csv          |
| E_DEADLINE_PARSE  | Validation       | Невозможно распарсить дедлайн                    | weeks.csv          |
| E_TZ_INVALID      | Validation       | Неверный TZ (IANA)                               | параметры курса    |
| E_CODE_INVALID    | Validation       | Неверный секретный код                           | регистрация TA     |
| E_ALREADY_LINKED  | Conflict         | Пользователь уже привязан                        | регистрация TA     |
| E_PRESET_NOT_FOUND| Lookup           | Пресет не найден                                 | пресеты            |
| E_DURATION_EXCEEDED| Validation      | Превышена суточная длительность слотов           | пресеты/расписание |
| E_CAP_EXCEEDED    | Validation       | Превышена вместимость                            | пресеты/расписание |
| E_CONFIG_INVALID  | Configuration    | Некорректная/неполная конфигурация окружения     | стартовая валидация |

---

## 7. Application Logging (структурированное логирование)

> Назначение: трассировка работы системы и диагностика. Не равен Audit Log.
> Формат: **JSON-строки** по одной на запись.

- Уровни: `DEBUG < INFO < WARN < ERROR`
- Prod: `INFO`, Dev/CI: `DEBUG`
- Поля: `ts, level, logger, msg, request_id` + (`actor_id, role, event_code, payload, error.code, error.trace`)
- Retention: 14 дней (prod), ротация 50MB или 7 дней

---

## 8. Audit Log (журнал бизнес-событий)

> Назначение: неизменяемая фиксация бизнес-событий.
> Хранится отдельно от application logging.

### 8.1. Схема
- `ts, actor_id, event_code, payload, request_id`
- `impersonated_id, impersonated_role`
- Опц. `signature` (HMAC)

### 8.2. Канонические события
- Регистрация: `OWNER_REGISTER_COMMIT`, `TEACHER_REGISTER_SELECT`
- Курс: `OWNER_COURSE_INIT_PARAMS`, `OWNER_COURSE_INIT_COMMIT`
- Материалы: `OWNER_MATERIAL_UPLOAD`, …
- Решения: `STUDENT_UPLOAD`, `STUDENT_REUPLOAD`, …
- Пресеты: `OWNER_PRESET_CREATE`, `TEACHER_PRESET_CREATE`, …
- Назначения: `OWNER_ASSIGN_AUTO`, …
- Оценки: `GRADE_UPSERT`
- Отчёты: `OWNER_AUDIT_EXPORT`, `SYSTEM_BACKUP_DAILY_COMMIT`

### 8.3. Политики хранения
- Retention: 180 дней (prod), 30 (staging)
- Ротация: 100 MB или 7 дней
- Экспорт: CSV/XLSX с фильтрами

---

## 9. Configuration Management

### 9.1. Переменные окружения (расширенный список)
| Переменная          | Обязательна | По умолчанию | Описание |
|---------------------|-------------|--------------|----------|
| BOT_TOKEN           | ✅ | –    | токен Telegram |
| STORAGE_BACKEND     | ✅ | –    | `sqlite|memory` (prod: обязательно) |
| DATABASE_URL        | ✅ | –    | строка подключения БД (sqlite URI или др.) |
| BACKUP_PATH         | ✅ | –    | путь до каталога бэкапов (prod: обязательно) |
| MATERIALS_PATH      | ❌ | ./materials | каталог хранения материалов |
| SUBMISSIONS_PATH    | ❌ | ./submissions | каталог решений студентов |
| EXPORT_PATH         | ❌ | ./exports   | каталог экспорта (аудит/отчёты) |
| LOG_LEVEL           | ❌ | INFO | `DEBUG|INFO|WARN|ERROR` |
| LOG_PATH            | ❌ | ./logs | каталог логов приложения |
| LOG_JSON            | ❌ | true | форматировать логи в JSON |
| LOG_STACKTRACE_SINK | ❌ | stderr | `file|stderr|off` |
| TEACHER_SECRET      | ✅ | –    | код регистрации преподавателей |
| DEFAULT_COURSE_TZ   | ❌ | UTC  | дефолт dev/test; в prod курс задаёт Owner |
| AUDIT_RETENTION_DAYS| ❌ | 180  | срок хранения audit log (prod) |
| AUDIT_SIGNATURE_KEY | ❌ | –    | включает HMAC-подпись audit-записей |

### 9.2. Валидация конфигурации при старте
Стартовый модуль выполняет проверку и поднимает `E_CONFIG_INVALID` при несоответствии.

```python
REQUIRED = ["BOT_TOKEN","STORAGE_BACKEND","DATABASE_URL","BACKUP_PATH","TEACHER_SECRET"]

def validate_config(env: dict) -> None:
    missing = [k for k in REQUIRED if not env.get(k)]
    if missing:
        raise DomainError("E_CONFIG_INVALID", f"Missing env: {', '.join(missing)}", {"missing": missing})

    if env["STORAGE_BACKEND"] not in {"sqlite","memory"}:
        raise DomainError("E_CONFIG_INVALID", "Invalid STORAGE_BACKEND", {"value": env["STORAGE_BACKEND"]})

    # sanity: пути должны быть непустыми; абсолютные или относительные — допустимы
    for pkey in ("BACKUP_PATH","MATERIALS_PATH","SUBMISSIONS_PATH","EXPORT_PATH","LOG_PATH"):
        p = env.get(pkey, "")
        # ошибка, если путь пустой ИЛИ состоит из пробелов
        if (p is not None) and (p.strip() == ""):
            raise DomainError("E_CONFIG_INVALID", f"Invalid path: {pkey}", {"value": p})
```

**Prod-правило**: запрещён запуск без явных `DATABASE_URL`, `BACKUP_PATH`, корректного `STORAGE_BACKEND` и `TEACHER_SECRET`.

### 9.3. Примеры .env
`.env.dev`
```env
BOT_TOKEN=dev-token
STORAGE_BACKEND=memory
DATABASE_URL=sqlite+aiosqlite:///./dev.db
BACKUP_PATH=./backups
MATERIALS_PATH=./materials
SUBMISSIONS_PATH=./submissions
EXPORT_PATH=./exports
LOG_LEVEL=DEBUG
LOG_PATH=./logs
LOG_JSON=true
TEACHER_SECRET=secret123
DEFAULT_COURSE_TZ=UTC
```

`.env.prod`
```env
BOT_TOKEN=prod-token
STORAGE_BACKEND=sqlite
DATABASE_URL=sqlite+aiosqlite:////var/lib/teachbot/prod.db
BACKUP_PATH=/var/teachbot/backups
MATERIALS_PATH=/var/teachbot/materials
SUBMISSIONS_PATH=/var/teachbot/submissions
EXPORT_PATH=/var/teachbot/exports
LOG_LEVEL=INFO
LOG_PATH=/var/log/teachbot
LOG_JSON=true
LOG_STACKTRACE_SINK=file
TEACHER_SECRET=${TEACHER_SECRET}
# В prod часовой пояс курса задаётся владельцем при инициализации, DEFAULT_COURSE_TZ не используется
```

---

## 10. Testing Strategy

### Дерево tests/
- unit/: сервисы, репозитории (InMemory)
- integration/: sqlite миграции, транзакции
- e2e/: сквозные сценарии ролей
- fixtures/: csv/json для студентов/недель/пресетов

### Фикстуры
- `app_container`, `request_id_ctx`
- `mocked_bot` (aiogram.tests.mocked_bot)
- `state` (FSM MemoryStorage)
- `caplog_json`, `audit_sink`

### Mock API
```python
mocked_bot.add_result_for("send_message", ok=True)
```

### FSM tests
```python
await state.set_state("CourseInitStates:uploading_weeks")
...
assert (await state.get_state()).endswith("confirming")
```

### Контракты портов
Тесты репозиториев против Protocol-интерфейсов.

### Error tests
Проверка, что операции возвращают коды из реестра (см. §6).

### Логи tests
Перехват structured log, проверка `request_id`, отсутствие PII.

### Audit tests
- наличие event_code
- idempotent=true при повторных операциях

### CI matrix
- Python 3.11/3.12
- Backends: memory/sqlite
- Маркировки: fast vs full
