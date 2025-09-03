# L3 — Owner (Events, Contracts & Data‑Flow) v0.10 (final)

Синхронизировано с **L1 v0.10**, **L3_Common v0.10**, **L2_Owner v0.10 revD**, **UI_Owner v0.10 revD**.

---

## 0. Соглашения и базовые сервисы

- Все timestamps — **UTC**. Отображение для пользователя через Dual TZ:
  ```python
  def format_dual_tz(utc_dt: datetime, course_tz: str, user_tz: str) -> str:
      """Пример: '2025‑09‑15 23:59 (Europe/Moscow) · у вас сейчас ≈ 22:59'"""
  ```
- **Callback data** (≤64B). Короткие action‑коды + минимальные параметры; длинные параметры через **StateStore**:
  ```text
  r=o;a=<code>;w=<Wxx?>;t=<p|m|n|s|v?>;s=<step?>;k=<uuid?>
  ```
  где `a ∈ {hm,c,ci,lr,im,sp,as,mt,ar,rp,im,ix,go}`.
- **StateStore** (Redis):
  - TTL ключа: **900 сек** (15 минут).
  - JSON‑структура:
    ```json
    {
      "role": "owner",
      "action": "ci|imp|mt|ar|rp|im|go|sp|as",
      "params": {"w":"W03","t":"m","id":"123","csv_meta":{"rows":120}},
      "exp": 900
    }
    ```
  - Ошибка при отсутствии/просрочке/недоступности → `E_STATE_INVALID`; пользователь получает подсказку «Сессия истекла, повторите шаг»; FSM откатывается к стартовому состоянию сценария.
- **Idempotency** — для критических операций обязателен `request_id` (UUID v4) и/или уникальные ключи на уровне БД.
- **Audit** — каждое действие логируется: `{ts, request_id, actor_id, role='owner', event, payload}`.
- **Навигация** — в любом подменю доступны `⬅️ Назад`, `🏠 Главное меню`.

---

## 1. FSM (Enum) и переходы

### 1.1 OwnerRegistrationStates
```python
class OwnerRegistrationStates(Enum):
    verify_tg = auto()
    enter_fio = auto()
    enter_email = auto()
    ask_is_teacher = auto()
    enter_capacity = auto()
    confirm = auto()
    done = auto()
```
**Переходы/TTL:**
- `/start` → `verify_tg` (проверка `tg_id ∈ TELEGRAM_OWNER_IDS`).
- ok → `enter_fio` → `enter_email` → `ask_is_teacher` → (`enter_capacity` if yes) → `confirm` → `done`.
- `TTL 15 min` неактивности на любом шаге → `verify_tg`.
- Ошибка состояния → `E_STATE_INVALID` + повтор шага.

### 1.2 CourseInitStates (двухшаговая инициализация)
```python
class CourseInitStates(Enum):
    enter_params = auto()   # s=p
    upload_csv = auto()     # s=u
    confirm = auto()        # s=c
    done = auto()           # s=d
```
**Переходы/TTL:** `enter_params → upload_csv → confirm → done`, TTL 15 мин/шаг. При TTL/ошибке → `enter_params`.

### 1.3 ImportStates (students/teachers)
```python
class ImportStates(Enum):
    choose_type = auto()
    upload_file = auto()
    validate = auto()
    confirm = auto()
    done = auto()
```
**Переходы/TTL:** `choose_type → upload_file → validate → confirm → done`, TTL 15 мин/шаг. При TTL/ошибке → `choose_type`.

### 1.4 MaterialUploadStates
```python
class MaterialUploadStates(Enum):
    pick_week = auto()
    pick_type = auto()
    upload_file = auto()
    confirm = auto()
    done = auto()
```
**Переходы/TTL:** `pick_week → pick_type → upload_file → confirm → done`, TTL 15 мин/шаг. При TTL/ошибке → `pick_week`.

### 1.5 ImpersonationStates
```python
class ImpersonationStates(Enum):
    enter_tg = auto()
    confirming = auto()
    active = auto()
```
**Переходы/TTL:** `enter_tg → confirming → active`; Owner→Owner запрещено (`E_ACCESS_DENIED`). В `active` критические действия disabled (см. §8). Выход `ix` всегда идемпотентен → сброс состояния.

---

## 2. Callback‑actions (полный перечень, ≤64B)

| Код | Назначение | Паттерн |
|---|---|---|
| `hm` | главное меню | `r=o;a=hm` |
| `c`  | управление курсом | `r=o;a=c` |
| `ci` | шаг course init | `r=o;a=ci;s=p|u|c|d` |
| `lr` | люди и роли | `r=o;a=lr` |
| `im` | импорт (тип) | `r=o;a=im;t=s|t` |
| `sp` | поиск профиля | `r=o;a=sp;k=<uuid>` |
| `as` | assignment матрица | `r=o;a=as;s=p|c` |
| `mt` | материалы | `r=o;a=mt;w=<Wxx>;t=p|m|n|s|v` |
| `ar` | архив | `r=o;a=ar;t=m|u` |
| `rp` | отчёты/бэкап | `r=o;a=rp;t=a|g|x|c|b` |
| `im` | начать имперсонизацию | `r=o;a=im` |
| `ix` | завершить имперсонизацию | `r=o;a=ix` |
| `go` | grade override | `r=o;a=go;k=<uuid>` |

> Любые потенциально длинные параметры (fio, фильтры, массивы id, текстовые комментарии) — через StateStore `k=<uuid>`.

---

## 3. Модель данных (уточнения)

```python
@dataclass
class Week:
    code: str          # 'W01'
    topic: str
    description: str
    deadline_utc: datetime

@dataclass
class Material:
    id: int
    week: str          # 'W01'
    type: Literal['p','m','n','s','v']
    version: int
    is_active: bool
    checksum: str
    storage_uri: str | None
    link: str | None
    created_at: datetime
    created_by: int    # owner_id

@dataclass
class BackupMeta:
    backup_id: str
    type: Literal["full","incremental"]
    started_at_utc: datetime
    finished_at_utc: datetime
    status: Literal["success","failed","partial"]
    manifest_uri: str
    objects_count: int
    bytes_total: int

@dataclass
class ImpersonationSession:
    owner_id: int
    target_user_id: int
    target_tg_id: int
    target_role: Literal["student","teacher"]
    started_at_utc: datetime
    last_seen_at_utc: datetime
    badge_text: str          # "IMPERSONATING: <fio> (<role>)"
    request_id: str
```

Версионирование — см. §6 (политика архива).

```python
@dataclass
class Grade:
    student_id: int
    week: str
    score: int
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
```

Финальная оценка: `override or base` (см. L3_Student).

---

## 4. Repository Protocols

```python
from typing import Protocol, Optional, Iterable, List, Tuple, Dict

class Transaction(Protocol):
    async def __aenter__(self): ...
    async def __aexit__(self, exc_type, exc, tb): ...

class CourseRepositoryProtocol(Protocol):
    async def create_or_update(self, params: Dict) -> "Course": ...
    async def list_weeks(self) -> List[Week]: ...
    async def upsert_weeks(self, weeks: List[Week]) -> None: ...
    def transaction(self) -> Transaction: ...

class UserRepositoryProtocol(Protocol):
    async def import_students(self, rows: Iterable[Dict], request_id: str) -> Tuple[int,int]: ...  # (success, errors)
    async def import_teachers(self, rows: Iterable[Dict], request_id: str) -> Tuple[int,int]: ...
    async def search_by_fio(self, fio_prefix: str, limit: int = 20) -> List["User"]: ...
    async def get_by_tg(self, tg_id: int) -> Optional["User"]: ...
    async def is_owner(self, tg_id: int) -> bool: ...
    def transaction(self) -> Transaction: ...
    async def import_exists(self, checksum: str) -> bool: ...

class MaterialRepositoryProtocol(Protocol):
    async def upload(self, week: str, type_: str, file_bytes: bytes | None, link: str | None,
                     checksum: str, request_id: str) -> Material: ...
    async def history(self, week: str, type_: str) -> List[Material]: ...
    async def get_active(self, week: str, type_: str) -> Optional[Material]: ...
    async def set_active(self, material_id: int) -> None: ...
    async def archive(self, material_id: int) -> None: ...
    async def delete(self, material_id: int) -> None: ...
    async def get_by_checksum(self, week: str, type_: str, checksum: str) -> Optional[Material]: ...
    def transaction(self) -> Transaction: ...

class AssignmentRepositoryProtocol(Protocol):
    async def preview_matrix(self, strategy: str = "round_robin") -> List[List[str]]: ...
    async def commit_matrix(self, matrix: List[List[str]], request_id: str) -> None: ...
    def transaction(self) -> Transaction: ...

class GradeRepositoryProtocol(Protocol):
    async def set_override(self, student_id: int, week: str, score: int, letter: str,
                           comment: Optional[str], owner_id: int, request_id: str) -> GradeOverride: ...

class BackupRepositoryProtocol(Protocol):
    async def get_last_success(self, limit: int = 3) -> List[BackupMeta]: ...
    async def create_backup_record(self, backup_meta: BackupMeta) -> None: ...
```

---

## 5. Service Protocols

```python
class CourseService(Protocol):
    async def init_course(self, params: Dict, weeks_csv: bytes, request_id: str) -> None: ...
    async def update_params(self, params: Dict, request_id: str) -> None: ...

class ImportService(Protocol):
    async def import_students_csv(self, csv_bytes: bytes, request_id: str) -> Dict: ...
    async def import_teachers_csv(self, csv_bytes: bytes, request_id: str) -> Dict: ...

class MaterialService(Protocol):
    async def upload(self, week: str, type_: str, file: bytes | None, link: str | None, request_id: str) -> Material: ...
    async def archive(self, material_id: int, request_id: str) -> None: ...
    async def delete(self, material_id: int, request_id: str) -> None: ...
    async def get_active(self, week: str, type_: str, request_id: str) -> Optional[Material]: ...

class AssignmentService(Protocol):
    async def preview(self, request_id: str) -> List[List[str]]: ...
    async def commit(self, matrix: List[List[str]], request_id: str) -> None: ...

class ImpersonationService(Protocol):
    async def start(self, owner_id: int, target_tg_id: int, request_id: str) -> ImpersonationSession: ...
    async def exit(self, owner_id: int, request_id: str) -> None: ...
    async def get_active(self, owner_id: int) -> Optional[ImpersonationSession]: ...

class GradeService(Protocol):
    async def override(self, student_id: int, week: str, score: int, letter: str,
                       comment: Optional[str], owner_id: int, request_id: str) -> GradeOverride: ...

class BackupService(Protocol):
    async def get_last_success(self, limit: int = 3) -> List[BackupMeta]: ...
    async def trigger_backup(self, backup_type: str, request_id: str) -> str: ...  # returns backup_id
```

---

## 6. Бизнес‑правила и валидации

### 6.1 Инициализация курса (CourseInit)
- `weeks.csv` — заголовки строго: `week_id,topic,description,deadline`
- Доп. колонки → `E_CSV_SCHEMA`; пустые строки игнорируются; поддержка UTF‑8 BOM.
- Дедлайны: парсинг с учётом `course_tz` → хранение в UTC; ошибки → `E_DEADLINE_PARSE`.
- Идемпотентность: повторный `commit` без изменений → no‑op (сравнение checksum от параметров+csv).

### 6.2 Импорт (students/teachers)
- Обязательные колонки (см. L2 revD); лишние — `E_CSV_SCHEMA` (строки помечаются как error и пропускаются).
- Суммарная статистика `{row_count, success, errors}`.
- Идемпотентность: если checksum csv уже импортирован → no‑op.

### 6.3 Материалы и версионирование
- Типы: `p` (prep), `m` (teacher), `n` (notes), `s` (slides), `v` (video).
- На `(week,type)` всегда **одна активная** запись.
- Новая загрузка:
  1) Если `checksum` совпадает с активной → no‑op.
  2) Иначе активная версия → архив, новая версия становится активной.
- **Архивный лимит**: максимум **20** версий на `(week,type)` (конфигурируемо).
- **Автоочистка**: при превышении лимита — удалить самые старые архивные версии **после** подтверждения наличия свежего бэкапа.

### 6.4 Backup dependency и автоочистка
**Определение свежего бэкапа**:
- Успешный backup моложе 24 часов
- Lightweight верификация доступности объектов

```python
BACKUP_WINDOW = timedelta(hours=24)

def backup_recent(now_utc: datetime) -> bool:
    metas = backup_svc.get_last_success(limit=3)
    for m in metas:
        if m.finished_at_utc >= now_utc - BACKUP_WINDOW:
            if objstore.head(m.manifest_uri).ok and sample_check(m.manifest_uri, k=3):
                return True
    return False
```

### 6.5 Имперсонизация
- Owner→Owner запрещено (`E_ACCESS_DENIED`).
- В impersonate‑режиме запрещены: `CourseInit`, Backup/Restore, безвозвратные `delete` из архива, изменение assignment matrix, загрузка/удаление материалов; попытки → `E_ACCESS_DENIED`.
- TTL 2h с sliding window; auto-expire с уведомлением.
- Повторный `start` того же tg_id → no‑op; `exit` всегда идемпотентен.

### 6.6 Grade override
- Только для существующего `student_id` и валидной недели.
- Результат — создаётся новая запись `GradeOverride`; финальная оценка = override или base.
- Обязателен `request_id`. Audit: `OWNER_GRADE_OVERRIDE`.

---

## 7. StateStore Management

### 7.1 Expired keys cleanup
- Redis с `maxmemory-policy: volatile-lru` или `allkeys-lru`
- Периодический GC job каждые 5 минут:
  ```python
  # SCAN по префиксу state:o:* и удаление TTL < 0
  ```

### 7.2 Memory pressure handling
- При Redis OOM/TIMEOUT → `E_STATE_INVALID`
- Пользователь получает: "Сессия истекла, повторите шаг"
- FSM откатывается к начальному состоянию сценария

### 7.3 ImpersonationSession lifecycle
- Регистрация в Redis: `imp:{owner_id}` с TTL=7200s (2h)
- Sliding TTL на каждый запрос owner
- Auto-expire с уведомлением при следующем запросе

---

## 8. Права в режиме имперсонизации (permission matrix)

| Операция | Доступ в impersonation |
|---|---|
| Просмотр отчётов/аудита | ✅ |
| Экспорт отчётов | ✅ |
| Инициализация курса | ❌ (`E_ACCESS_DENIED`) |
| Бэкап/Restore | ❌ |
| Импорт CSV (люди) | ❌ |
| Изменение assignment matrix | ❌ |
| Загрузка/удаление материалов | ❌ |
| Просмотр материалов и истории | ✅ |
| Просмотр профилей | ✅ |
| Grade override | ❌ |

---

## 9. Ошибки (расширенный mapping)

- `E_ACCESS_DENIED` — доступ запрещён/ограничение impersonation
- `E_STATE_INVALID` — истёкший или отсутствующий StateStore ключ/некорректный переход
- `E_EMAIL_INVALID`, `E_EMAIL_ALREADY_USED`
- `E_CSV_SCHEMA` — проблемы со схемой/колонками
- `E_WEEK_DUP` — дубли недель
- `E_DEADLINE_PARSE` — плохой формат дедлайна
- `E_NOT_FOUND` — объект/пользователь не найден
- `E_CONFIG_INVALID` — неверные параметры/курс не инициализирован
- `E_STORAGE_IO` — сбой хранилища (включая отсутствие бэкапа при очистке архива)
- `E_SIZE_LIMIT` — превышение размера файла
- `E_TZ_INVALID` — неверный часовой пояс

Каждая ошибка отображается toast‑сообщением из реестра UI, логируется с `{request_id}`.

---

## 10. Идемпотентность (технические детали)

- Course init: ключ `idem:course_init:{checksum}` (TTL 1ч) + сравнение конфигурации.
- Import: ключ `idem:import:{checksum}` → повтор — пропуск.
- Materials: `get_by_checksum(week,type,checksum)` + ключ `idem:material:{week}:{type}:{checksum}` (TTL 1ч).
- Impersonation: ключ `imp:{owner_id}`; если уже активна та же цель — no‑op.

---

## 11. Псевдокод ключевых сценариев

### 11.1 Init course
```python
async def init_course(params, weeks_csv, request_id):
    checksum = sha256(params_bytes + weeks_csv)
    if await cache.exists(f"idem:course_init:{checksum}"):
        return  # no-op
    weeks = parse_weeks_csv(weeks_csv)  # validate headers, deadlines
    async with course_repo.transaction():
        await course_repo.create_or_update(params)
        await course_repo.upsert_weeks(weeks)
    audit.log("OWNER_COURSE_UPDATE", request_id, {
        "weeks": len(weeks), "course_tz": params.get("tz")
    })
    cache.setex(f"idem:course_init:{checksum}", 3600, 1)
```

### 11.2 Import CSV (students/teachers)
```python
async def import_students_csv(csv_bytes, request_id):
    checksum = sha256(csv_bytes)
    if await user_repo.import_exists(checksum):
        return {"status":"noop"}
    rows = parse_students_csv(csv_bytes)  # strict headers, ignore empty, BOM ok
    async with user_repo.transaction():
        ok, err = await user_repo.import_students(rows, request_id)
    audit.log("OWNER_UPLOAD_STUDENTS", request_id, {
        "rows": len(rows), "ok": ok, "errors": err, "checksum": checksum
    })
    return {"ok": ok, "err": err}
```

### 11.3 Upload material (with versioning and backup dependency)
```python
async def upload_material(week, type_, file_or_link, request_id):
    file_bytes, link = normalize(file_or_link)
    checksum = sha256(file_bytes or link.encode())
    if await mat_repo.get_by_checksum(week, type_, checksum):
        return AlreadyExists
    async with mat_repo.transaction():
        active = await mat_repo.get_active(week, type_)
        if active:
            await mat_repo.archive(active.id)
        new = await mat_repo.upload(week, type_, file_bytes, link, checksum, request_id)
        hist = await mat_repo.history(week, type_)
        archived = [m for m in hist if not m.is_active]
        excess = len(archived) - 20
        if excess > 0:
            if not backup_recent(now_utc=utcnow()):
                raise E_STORAGE_IO("No recent backup for archive cleanup")
            to_delete = sorted(archived, key=lambda x: x.created_at)[:excess]
            for m in to_delete:
                await mat_repo.delete(m.id)
    audit.log("OWNER_MATERIAL_UPLOAD", request_id, {
        "week": week, "type": type_, "version": new.version,
        "file_size": len(file_bytes) if file_bytes else 0,
        "checksum": checksum, "storage_uri": new.storage_uri
    })
    return new
```

### 11.4 Grade override
```python
async def grade_override(student_id, week, score, letter, comment, owner_id, request_id):
    session = await impersonation_svc.get_active(owner_id)
    if session:
        raise E_ACCESS_DENIED("Grade override not allowed in impersonation mode")
    ov = await grade_repo.set_override(student_id, week, score, letter, comment, owner_id, request_id)
    audit.log("OWNER_GRADE_OVERRIDE", request_id, {
        "student_id": student_id, "week": week, "score": score,
        "letter": letter, "comment": comment
    })
    return ov
```

### 11.5 Impersonation start/exit
```python
async def impersonate_start(owner_id, target_tg_id, request_id):
    target = await user_repo.get_by_tg(target_tg_id)
    if not target:
        raise E_NOT_FOUND("User not found")
    if await user_repo.is_owner(target_tg_id):
        raise E_ACCESS_DENIED("Owner→Owner impersonation forbidden")

    session = ImpersonationSession(
        owner_id=owner_id,
        target_user_id=target.id,
        target_tg_id=target_tg_id,
        target_role=target.role,
        started_at_utc=utcnow(),
        last_seen_at_utc=utcnow(),
        badge_text=f"IMPERSONATING: {target.fio} ({target.role})",
        request_id=request_id
    )

    # Store in Redis with sliding TTL
    await redis.setex(f"imp:{owner_id}", 7200, session.to_json())

    audit.log("DEV_IMPERSONATE_START", request_id, {
        "target_tg_id": target_tg_id, "target_role": target.role
    })
    return session

async def impersonate_exit(owner_id, request_id):
    await redis.delete(f"imp:{owner_id}")
    audit.log("DEV_IMPERSONATE_EXIT", request_id, {})
```

---

## 12. Метрики и мониторинг

### 12.1 SLO targets
- Импорт CSV (P95): ≤ 5s на 5k строк
- Материал upload (P95): ≤ 2s до активной версии
- Предпросмотр assignment (P95): ≤ 3s при 1k студентов × 12 недель
- Уровень ошибок Redis StateStore (rate): ≤ 1% за 15 мин

### 12.2 Prometheus метрики
- `owner_import_duration_seconds{type="students|teachers"}` (histogram)
- `owner_material_upload_seconds{type}` (histogram)
- `owner_assignment_preview_seconds` (histogram)
- `owner_statestore_errors_total{reason="oom|timeout|missing|expired"}` (counter)
- `owner_archive_versions_count{week,type}` (gauge)
- `owner_backup_recent_gauge` (0/1)
- `owner_audit_events_total{event}` (counter)
- `owner_fsm_transitions_total{state,result}` (counter)

### 12.3 Критические алерты
- `owner_backup_recent_gauge==0` > 24h → critical
- `owner_archive_versions_count > 20` для любого `(week,type)` > 30m → warning
- `rate(owner_statestore_errors_total[15m]) > 0.01` → warning

---

## 13. Архитектурные слои

```
bot/
  routers/owner/           # callback handlers, FSM orchestration
  middlewares/             # request_id, audit, error mapping, impersonation badge
  keyboards/               # фабрики кнопок (короткий payload)

services/owner/
  course_service.py
  import_service.py
  material_service.py
  assignment_service.py
  impersonation_service.py
  grade_service.py
  backup_service.py

repositories/
  course_repo.py
  user_repo.py
  material_repo.py
  assignment_repo.py
  grade_repo.py
  backup_repo.py

domain/
  models.py
  time_service.py
  state_store.py          # Redis wrapper
  backup.py               # backup state checker

infra/
  db.py                   # транзакции
  redis.py                # StateStore
  logging.py              # audit sink
  object_storage.py       # backup verification
```

---

## 14. Audit events (полный реестр с расширенными payload)

- `OWNER_REGISTER_START / OWNER_REGISTER_DONE`
- `OWNER_COURSE_UPDATE` → `{weeks,count,course_tz}`
- `OWNER_UPLOAD_STUDENTS / OWNER_UPLOAD_TEACHERS` → `{rows,ok,errors,checksum}`
- `OWNER_SEARCH_PROFILE` → `{query,results_count}`
- `OWNER_ASSIGN_PREVIEW / OWNER_ASSIGN_COMMIT` → `{students,teachers,weeks,strategy}`
- `OWNER_MATERIAL_UPLOAD` → `{week,type,version,file_size,checksum,storage_uri}`
- `OWNER_MATERIAL_ARCHIVE` → `{material_id,week,type,version}`
- `OWNER_MATERIAL_DELETE` → `{material_id,week,type,version,reason}`
- `OWNER_ARCHIVE_DOWNLOAD / OWNER_ARCHIVE_DELETE` → `{type,items_count}`
- `OWNER_AUDIT_EXPORT / OWNER_REPORT_EXPORT` → `{report_type,records_count}`
- `OWNER_GRADE_OVERRIDE` → `{student_id,week,score,letter,comment?}`
- `SYSTEM_BACKUP_DAILY_COMMIT` → `{backup_id,type,bytes_total,objects_count}`
- `DEV_IMPERSONATE_START / DEV_IMPERSONATE_COMMIT / DEV_IMPERSONATE_EXIT` → `{target_tg_id?,target_role?}`

---

## 15. Совместимость и тесты (минимальный набор)

### 15.1 Unit тесты сервисов
- Entry point: доступ/регистрация Owner (ok/deny), TTL/StateStore ошибки
- Course init: params/csv ok; schema/deadline ошибки; идемпотентность commit
- Import: students/teachers ok; дубликат по checksum; отчёт по ошибкам/успехам
- Materials: upload/архив/история; лимит версий/автоочистка; no‑op по checksum
- Assignment: preview/commit; транзакционность
- Impersonation: start/exit; запреты действий; меню с бейджем
- Grade override: ok; запрет в impersonation; финальная оценка у студента обновляется
- Reports/backup: экспорт ok; ручной бэкап — только вне impersonation

### 15.2 Integration тесты
- FSM transitions: корректные переходы состояний с TTL
- StateStore: Redis операции с cleanup и error recovery
- Repository transactions: атомарность критических операций
- Backup dependency: материалы не удаляются без свежего бэкапа

### 15.3 End-to-end тесты
- Полный курс lifecycle: init → import → materials → assignment → grades
- Impersonation flow: start → ограничения → switch menus → exit
- Error scenarios: StateStore expiry, backup unavailable, permission denied

---

**Документ готов к реализации разработчиками.**
