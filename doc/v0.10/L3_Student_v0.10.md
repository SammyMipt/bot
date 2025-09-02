# L3 — Student (Events, Contracts & Data-Flow) v0.10 (final)

Синхронизировано с **L1 v0.10**, **L3_Common v0.10**, **L2_Student v0.10 (revB)** и **UI_Student v0.10 (revB)**.

---

## 0. Соглашения и базовые сервисы

- Все timestamps хранятся в **UTC**. Отображение через TimeService:
  ```python
  from domain.time_service import TimeService

  def format_dual_tz(utc_dt: datetime, course_tz: str, user_tz: str) -> str:
      """Пример: '2025‑09‑15 23:59 (course_tz) (у вас сейчас: 08:59)'"""
  ```
- **Callback data** (короткие action-коды, размер ≤64B; при превышении — StateStore):
  ```text
  r=s;a=<code>;w=<Wxx?>;id=<id?>;t=<type?>;k=<uuid?>
  ```
  где `r=s` — роль student; `a` ∈ {reg,w,mat,up,del,b,bc,g,mb,h}.
- **Ошибки**: использовать коды из L3_Common. Дополнительные в этом документе отмечены явно.
- **Idempotency**: для критических операций использовать `request_id` (UUID v4) и/или уникальные ключи БД.
- **Audit**: все события логируются с `{request_id}` (см. §10).

---

## 1. FSM (Enum) и правила сброса

### 1.1 Registration (Enum)
```python
from enum import Enum, auto

class StudentRegistrationStates(Enum):
    enter_email = auto()
    confirm = auto()
    done = auto()
```

**Переходы:**
- `start` → `enter_email`
- valid email найден → `confirm`
- confirm.ok → `done` (создаём User/StudentProfile, bind tg_id)
- cancel/назад → `enter_email`
- `🏠 Главное меню` → сброс FSM

**TTL/Reset:**
- **TTL 15 минут** неактивности → сброс в `enter_email`
- Любая ошибка `E_STATE_INVALID` → сброс и повтор шага с подсказкой

### 1.2 Solution Upload (Enum)
```python
class SolutionUploadStates(Enum):
    choosing_week = auto()
    uploading_file = auto()
    confirming_action = auto()
    done = auto()
```

**Переходы:**
- `start` → `choosing_week`
- pick Wxx → `uploading_file`
- file accepted → `confirming_action` (кнопки: ➕/🗑️)
- ➕ → остаётся `uploading_file`; 🗑️ → подтверждение удаления → `confirming_action`
- `🏠` или `⬅️` → сброс FSM
- TTL 15 мин на каждую фазу; по TTL → `choosing_week`

### 1.3 Booking (Enum)
```python
class BookingStates(Enum):
    choosing_slot = auto()
    confirming = auto()
    done = auto()
```

**Переходы:**
- open list → `choosing_slot`
- pick slot → `confirming`
- confirm.ok → `done` (создать booking)
- cancel/назад/🏠 → сброс
- TTL 15 мин; по TTL возврат к `choosing_slot`

**StateStore стратегия (общая):**
- Если callback payload **>64B**, создаём запись в StateStore (Redis) вида:
  ```json
  {
    "k": "uuid-v4",
    "role": "student",
    "action": "mat|up|del|b|bc|g|mb|h",
    "params": {"w":"W03","id":"...","t":"slides","checksum":"..."},
    "exp": 900  // TTL, сек
  }
  ```
- В callback отправляем укороченный payload: `r=s;a=<code>;k=<uuid>`.
- **Error handling**: При недоступности Redis → fallback на прямой payload (если ≤64B) или E_CONFIG_INVALID

---

## 2. Callback actions (полный перечень)

| Action | Назначение | Паттерн |
|---|---|---|
| `reg` | регистрация | `r=s;a=reg` |
| `w`   | открыть меню недели | `r=s;a=w;w=<Wxx>` |
| `mat` | получить материал | `r=s;a=mat;w=<Wxx>;t=<prep\|notes\|slides\|video>` |
| `up`  | загрузить решение | `r=s;a=up;w=<Wxx>` |
| `del` | удалить файл решения | `r=s;a=del;w=<Wxx>;id=<file_id>` |
| `b`   | создать запись (booking) | `r=s;a=b;w=<Wxx>;id=<slot_id>` |
| `bc`  | отменить запись | `r=s;a=bc;w=<Wxx>;id=<booking_id>` |
| `g`   | получить оценку | `r=s;a=g;w=<Wxx>` |
| `mb`  | карточка моей записи | `r=s;a=mb;w=<Wxx>;id=<booking_id>` |
| `h`   | показать историю | `r=s;a=h;w=<Wxx?>` |

> При payload >64B используется `k=<uuid>` и StateStore (§1).

---

## 3. Модель данных (уточнённая)

```python
@dataclass
class Grade:
    student_id: int
    week: str  # 'W03'
    score: int            # 1..10
    letter: Literal['A','B','C','D']
    comment: str | None
    teacher_id: int
    created_at: datetime

@dataclass
class GradeOverride:
    student_id: int
    week: str
    score: int
    letter: Literal['A','B','C','D']
    comment: str | None
    owner_id: int
    created_at: datetime

def compute_final_grade(grade: Grade | None, override: GradeOverride | None) -> Grade | GradeOverride | None:
    return override or grade
```

```python
@dataclass
class Slot:
    id: int
    teacher_id: int
    start_ts: datetime
    end_ts: datetime
    capacity: int
    status: Literal['open','closed','past']
    # available_spots НЕ храним — вычисляется: capacity - booked_count_active
```

---

## 4. Repository Protocols (строгие контракты)

```python
from typing import Protocol, Optional, List, Iterable
from domain.time_service import TimeService

class StudentRepositoryProtocol(Protocol):
    async def get_by_email(self, email: str) -> Optional["Student"]: ...
    async def get_by_tg(self, tg_id: int) -> Optional["Student"]: ...
    async def register(self, tg_id: int, email: str) -> "Student": ...

class MaterialRepositoryProtocol(Protocol):
    async def get_active_by_week_and_type(self, week: str, type_: str) -> Optional["Material"]: ...

class SubmissionRepositoryProtocol(Protocol):
    async def create(self, submission: "Submission") -> "Submission": ...
    async def list_by_student_week(self, student_id: int, week: str) -> List["Submission"]: ...
    async def sum_size_by_student_week(self, student_id: int, week: str) -> int: ...
    async def find_duplicate_by_checksum(self, student_id: int, week: str, checksum: str) -> Optional["Submission"]: ...
    async def archive(self, student_id: int, submission_id: int) -> None: ...
    def transaction(self) -> "Transaction": ...

class BookingRepositoryProtocol(Protocol):
    async def list_open_slots_for_assignment(self, teacher_id: int, week: str, now_utc: datetime) -> List["Slot"]: ...
    async def get_active_by_student_week(self, student_id: int, week: str) -> Optional["Booking"]: ...
    async def create_atomic(self, student_id: int, week: str, slot_id: int, request_id: str) -> "Booking": ...
    async def cancel(self, booking_id: int, student_id: int) -> None: ...
    def transaction(self) -> "Transaction": ...

class GradeRepositoryProtocol(Protocol):
    async def get_base(self, student_id: int, week: str) -> Optional["Grade"]: ...
    async def get_override(self, student_id: int, week: str) -> Optional["GradeOverride"]: ...
```

> `create_atomic` должен гарантировать атомарность проверки и записи через транзакции (см. §6).

---

## 5. Сервисные контракты

```python
class StudentAuthService(Protocol):
    async def start_registration(self, tg_id: int, request_id: str) -> None: ...
    async def confirm_registration(self, tg_id: int, email: str, request_id: str) -> "Student": ...

class MaterialService(Protocol):
    async def get_material(self, student_id: int, week: str, type_: str, request_id: str) -> "MaterialOrLink": ...

class SubmissionService(Protocol):
    async def upload(self, student_id: int, week: str, file: "InputFile", request_id: str) -> "Submission": ...
    async def delete(self, student_id: int, week: str, submission_id: int, request_id: str) -> None: ...

class BookingService(Protocol):
    async def list_slots(self, student_id: int, week: str, request_id: str) -> List["SlotVM"]: ...
    async def create(self, student_id: int, week: str, slot_id: int, request_id: str) -> "Booking": ...
    async def cancel(self, student_id: int, week: str, booking_id: int, request_id: str) -> None: ...

class GradeService(Protocol):
    async def get_final(self, student_id: int, week: str, request_id: str) -> Optional["FinalGradeVM"]: ...
    async def overview(self, student_id: int, request_id: str) -> List["FinalGradeVM"]: ...
```

---

## 6. Бизнес‑правила и валидации (детализация)

### 6.1 Загрузка решений
- **MIME/EXT whitelist**: `image/png,image/jpeg,application/pdf` + `.png,.jpg,.jpeg,.pdf`
- **Суммарный лимит**: `sum_size_by_student_week(student,week) + file.size ≤ 30*1024*1024`
- **Кол-во файлов**: `len(list_by_student_week) < 5`
- **Checksum**: SHA‑256 по содержимому файла (независимо от имени).
  Дубликат определяется `find_duplicate_by_checksum(student, week, checksum)` → если есть, **idempotent no-op**.
- **Удаление**: `archive(submission_id)`; повторное удаление — idempotent.

### 6.2 Бронирование слота
- **Назначение преподавателя**: из `AssignmentMatrix(student, week)`; фильтрация только по teacher_id.
- **Статусы**:
  - `open` + `available_spots > 0` → 🟢
  - `open` + `0 < available_spots < capacity` → 🟡
  - `open` + `available_spots == 0` → 🔴
  - `closed` → 🚫
  - `past` → ⚫
- **Проверка дедлайна**: если `now > deadline(week)` → `E_PAST_DEADLINE`.
- **Единственность записи**: уникальный ключ `(student_id, week, status='active')`.
- **Атомарность через Repository transaction pattern**:
  ```python
  async with booking_repo.transaction() as tx:
      available = await tx.get_slot_availability(slot_id)
      if available <= 0:
          raise E_SLOT_FULL
      booking = await tx.create_booking(student_id, week, slot_id, request_id)
      await tx.decrement_slot_availability(slot_id)
      return booking
  ```
- **Idempotency**: уникальный `request_id` в таблице `bookings`; повтор запроса → вернуть ту же запись.

### 6.3 Оценки
- **Финальная**: `override or base`. `override` создаётся/меняется только Owner‑ом.
- **Доступ студента**: только чтение финальной.

---

## 7. StateStore (детализация)

- Реализация: Redis с JSON, ключ `state:<k>`; TTL 900s (15 мин).
- Содержимое:
  ```json
  {"role":"student","action":"up","params":{"w":"W03","checksum":"..."},"exp":900}
  ```
- Переиспользование: после успешного действия ключ удаляется. Просрочка → `E_STATE_INVALID` и повтор шага.
- Использование `k=<uuid>` обязательно, если сериализованный payload превысит 64 байта.
- **Error handling**: При недоступности Redis (network failure) → fallback на прямой callback если возможно, иначе E_CONFIG_INVALID с retry предложением.

---

## 8. Error mapping (дополненный)

Общие из L3_Common + студенческие:
- E_EMAIL_INVALID — «⛔ Некорректный формат email»
- E_EMAIL_NOT_FOUND — «❌ Такой email не найден»
- E_EMAIL_ALREADY_USED — «⚠️ Email уже используется»
- E_FILE_TYPE — «⛔ Неподдерживаемый тип файла»
- E_FILES_COUNT_LIMIT — «⚠️ Превышено число файлов (5)»
- E_BYTES_LIMIT — «⚠️ Превышен лимит: ≤30 МБ суммарно»
- E_STORAGE_IO — «⛔ Ошибка хранения файла»
- E_ALREADY_BOOKED — «⚠️ У вас уже есть запись на эту неделю»
- E_SLOT_FULL — «⚠️ Слот полностью занят»
- E_SLOT_CLOSED — «⚠️ Слот закрыт для записи»
- E_PAST_DEADLINE — «⚠️ Запись недоступна после дедлайна»
- E_NOT_FOUND — «❌ Объект не найден»
- E_STATE_INVALID — «⛔ Некорректное состояние запроса»
- E_ACCESS_DENIED — «⛔ Доступ запрещён»
- E_CONFIG_INVALID — «⛔ Некорректная конфигурация курса/недели»
- E_TZ_INVALID — «⛔ Некорректный часовой пояс»

**Retry policy**: Для временных ошибок (E_STORAGE_IO, Redis timeout) — автоматический retry с exponential backoff (3 попытки).

---

## 9. Идемпотентность (технические детали)

- **Registration**: уникальный `(tg_id,email)`; повтор → no-op.
- **Upload**: ключ `idem:upload:{student}:{week}:{checksum}` в Redis (TTL 1ч). Повтор → не создаём новую запись.
- **Delete**: `archive` повторно → no-op.
- **Booking**: уникальный `(student,week,status='active')` + `request_id` в `bookings`. Повтор по тому же `request_id` → вернуть ту же запись.
- **GradeGet**: идемпотентно по природе (GET).

---

## 10. Audit events (c request_id)

Каждое событие — `{ts, request_id, actor_id, role='student', event, payload}`:
- STUDENT_REGISTER_EMAIL `{tg_id,email,success}`
- STUDENT_WEEK_OPEN `{student_id,Wxx}`
- STUDENT_MATERIAL_GET `{student_id,Wxx,type}`
- STUDENT_UPLOAD `{student_id,week,file_id,checksum}`
- STUDENT_SOLUTION_DELETE `{student_id,week,file_id}`
- STUDENT_BOOKING_CREATE `{student_id,week,slot_id}`
- STUDENT_BOOKING_CANCEL `{student_id,week,booking_id}`
- STUDENT_GRADE_GET `{student_id,week}`
- STUDENT_GRADES_OVERVIEW `{student_id}`
- STUDENT_HISTORY_LIST `{student_id}`

---

## 11. Архитектурные слои и структура модулей

```
bot/
  routers/student/        # хендлеры callback/data, fsm orchestration
  middlewares/            # request_id, audit, error mapping
  keyboards/              # фабрики кнопок (короткий payload)

services/student/
  auth_service.py
  material_service.py
  submission_service.py
  booking_service.py
  grade_service.py

repositories/
  student_repo.py
  material_repo.py
  submission_repo.py
  booking_repo.py
  grade_repo.py

domain/
  models.py               # dataclasses (Grade, GradeOverride, Slot,...)
  time_service.py         # format_dual_tz
  state_store.py          # Redis-обёртка

infra/
  db.py                   # транзакции, пул
  redis.py                # StateStore
  logging.py              # audit sink
```

---

## 12. Совместимость с L2/UI

- Все экраны/кнопки из L2 покрыты actions (§2).
- Dual TZ в инфо/дедлайне через `TimeService.format_dual_tz` (§0).
- Материалы соответствуют типам из L1 (prep, notes, slides, video).
- Слоты — только назначенный преподаватель согласно `AssignmentMatrix`.

---

## 13. Псевдокод ключевых сценариев

### 13.1 Upload (idempotent)
```python
from domain.time_service import TimeService

async def upload(student_id, week, file, request_id):
    checksum = sha256(file.bytes)
    if await sub_repo.find_duplicate_by_checksum(student_id, week, checksum):
        return AlreadyExists  # дубликат содержимого
    size_sum = await sub_repo.sum_size_by_student_week(student_id, week)
    if size_sum + file.size > 30 * 1024 * 1024:
        raise E_BYTES_LIMIT
    items = await sub_repo.list_by_student_week(student_id, week)
    if len(items) >= 5:
        raise E_FILES_COUNT_LIMIT
    saved = await sub_repo.create(Submission(..., checksum=checksum))
    audit.log("STUDENT_UPLOAD", request_id, {...})
    return saved
```

### 13.2 Booking create (atomic)
```python
async def create_booking(student_id, week, slot_id, request_id):
    if await booking_repo.get_active_by_student_week(student_id, week):
        raise E_ALREADY_BOOKED
    try:
        async with booking_repo.transaction() as tx:
            booking = await tx.create_atomic(student_id, week, slot_id, request_id)
            await tx.commit()
    except SlotFull:
        raise E_SLOT_FULL
    except SlotClosed:
        raise E_SLOT_CLOSED
    audit.log("STUDENT_BOOKING_CREATE", request_id, {...})
    return booking
```

---

## 14. Производительность и мониторинг

**Критические метрики**:
- Время upload файлов (target: <2s для 5MB)
- Booking success rate (target: >95%)
- FSM reset frequency (TTL hits)
- StateStore hit/miss ratio
- Checkpoint дедупликации (duplicate file submissions)

**Мониторинг индексов**:
- `(student_id, week, checksum)` для дедупликации
- `(student_id, week, status='active')` для единственности booking
- `(teacher_id, week, status='open')` для списка слотов

---

## 15. Совместимость и тесты (минимальный набор)

- Регистрация: ok, дубликат, неверный email, TTL 15 мин.
- Материалы: 4 типа, недоступность/архив.
- Upload: 5 файлов, 30МБ суммарно, дубликаты по checksum, неверный MIME.
- Booking: список/фильтр по teacher, дедлайн, full/closed/past, атомарность.
- Grades: override приоритетнее base.
- История: только прошедшие.

---

**Готово.** Документ покрывает FSM (Enum), StateStore с fallback, Repository/Service контракты с транзакциями, корректные модели данных, полный набор callback-паттернов, детальные валидации/идемпотентность, audit, архитектурную структуру и производительность.
