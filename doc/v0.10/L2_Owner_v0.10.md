# L2 — UX‑спецификация: Owner v0.10 (revD, финальная для UX‑ревью)

Синхронизировано с L1 v0.10 и L3_Common v0.10. Учитывает концепцию **master‑user**: вход Owner — отдельный entry point, без выбора роли. Кнопки ролей показываются **только** в режиме имперсонизации.

---

## 0) Соглашения и базовые правила

- **Callback data (≤64B)**: короткие action‑коды + минимальные параметры. Для длинных параметров используем **StateStore** с ключом `k=<uuid>`.
- **Везде доступны** кнопки: `⬅️ Назад`, `🏠 Главное меню`.
- **Dual TZ**: все даты/дедлайны отображаются как `TimeService.format_dual_tz(utc_dt, course_tz, user_tz)`.
- **Error registry**: коды из L3_Common (включая E_ACCESS_DENIED, E_STATE_INVALID, E_CONFIG_INVALID, E_STORAGE_IO, E_SIZE_LIMIT, E_TZ_INVALID).
- **Audit**: каждое критическое действие журналируется с `{request_id}`.

### StateStore (для больших payload) — детализировано
- **TTL**: 15 минут (900s).
- **JSON‑структура**:
  ```json
  {
    "role": "owner",
    "action": "ci|imp|mat|ar|rp|im|go|sp|as",
    "params": { "w":"W03", "t":"p", "id":"123", "csv_meta":{"rows":120} },
    "exp": 900
  }
  ```
- **Недоступность/просрочка**: если ключ не найден либо хранилище недоступно → `E_STATE_INVALID`, показать подсказку «Сессия истекла, начните шаг заново».

---

## 1) Entry point и регистрация Owner

### Entry point (master‑user)
- При `/start` выполняется **немедленная** проверка `tg_id ∈ TELEGRAM_OWNER_IDS` (без выбора роли).
  - **Нет** → `E_ACCESS_DENIED`: «⛔ Доступ запрещён: этот аккаунт не уполномочен как владелец.»
  - **Да** → если не зарегистрирован → предложение пройти регистрацию.

### Стартовое сообщение (только для Owner)
```
👋 Добро пожаловать!
Ваш Telegram ID подтверждён как владелец курса.
[Начать регистрацию]   (r=o;a=rg)
```

### FSM: OwnerRegistrationStates (Enum) + переходы
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
- `verify_tg → enter_fio` (если tg валиден)
- `enter_fio → enter_email` (валидный ввод «Фамилия Имя [Отчество]»)
- `enter_email → ask_is_teacher` (валидный email)
- `ask_is_teacher → enter_capacity | confirm` (по выбору Да/Нет)
- `enter_capacity → confirm` (целое в допустимых границах)
- `confirm.ok → done`, `confirm.cancel → enter_fio`
- **TTL 15 мин** на любой стадии → возврат в `verify_tg` с подсказкой
- Любая ошибка состояния → `E_STATE_INVALID` и повтор текущего шага

**Результат**: `User{role=owner, fio, email, tg_id, is_teacher}`; опц. `TeacherProfile{ weekly_limit }`.

**После регистрации**:
- Если `is_teacher=False` → сразу **Главное меню Owner**.
- Если `is_teacher=True` → показываем две кнопки:
  - `🏠 Главное меню владельца`
  - `📚 Главное меню преподавателя`

---

## 2) Главное меню Owner

```
⚙️ Управление курсом
👥 Люди и роли
📚 Материалы курса
🗄️ Архив
📊 Отчёты и аудит
👤 Имперсонизация
```
- Callback корня: `r=o;a=hm` (home)

---

## 3) Callback‑коды (короткие, унифицированные)

| Раздел | Действие | Паттерн (≤64B) | Примечание |
|---|---|---|---|
| Управление курсом | открыть | `r=o;a=c` | course root |
| Инициализация курса | шаг | `r=o;a=ci;s=p|u|c|d` | params / upload / confirm / done |
| Люди и роли | открыть | `r=o;a=lr` | people root |
| Импорт | старт | `r=o;a=im;t=s|t` | s=students, t=teachers |
| Поиск профиля | запрос | `r=o;a=sp;k=<uuid>` | fio в StateStore |
| Матрица назначений | шаг | `r=o;a=as;s=p|c` | preview / commit; детали при необходимости — StateStore |
| Материалы | действие | `r=o;a=mt;w=<Wxx>;t=p|m|n|s|v` | **m=teacher** (методические) — код **m** используется везде |
| Архив | открыть | `r=o;a=ar;t=m|u` | m=materials, u=submissions |
| Отчёты | экспорт | `r=o;a=rp;t=a|g|x|c` | audit / grades / assignments / course |
| Имперсонизация | старт/выход | `r=o;a=im` / `r=o;a=ix` | start / exit |
| Grade override | изменение | `r=o;a=go;k=<uuid>` | всегда через StateStore |

> Любые потенциально длинные параметры (fio, фильтры, массивы id) — **только через `k=<uuid>`**.

---

## 4) Управление курсом

### 4.1 Инициализация курса (двухшаговая)
**FSM: CourseInitStates**
```python
class CourseInitStates(Enum):
    enter_params = auto()   # s=p
    upload_csv = auto()     # s=u
    confirm = auto()        # s=c
    done = auto()           # s=d
```
**Переходы**: `enter_params → upload_csv → confirm → done`
**TTL**: 15 мин на каждый этап; по TTL — возврат к `enter_params`.

- `params (s=p)`: название, описание, язык, course_tz (валидный tz или `E_TZ_INVALID`).
- `upload (s=u)`: `weeks.csv` — **строгий формат** заголовков:
  - `week_id,topic,description,deadline`
  - Доп. колонки → `E_CSV_SCHEMA`
  - Пустые строки → игнор
  - Поддержка UTF‑8 BOM
  - Дедлайны парсим; ошибки → `E_DEADLINE_PARSE`
- `confirm (s=c)`: предпросмотр недель, дедлайны показываем через **Dual TZ**.
- `done (s=d)`: фиксация. Идемпотентность: повторный commit без изменений → no‑op.

**Audit**: `OWNER_COURSE_UPDATE`, `{request_id}`.

---

## 5) Люди и роли

### 5.1 Импорт CSV
**FSM: ImportStates**
```python
class ImportStates(Enum):
    choose_type = auto()
    upload_file = auto()
    validate = auto()
    confirm = auto()
    done = auto()
```
**Переходы**: `choose_type → upload_file → validate → confirm → done`
**TTL**: 15 мин; по TTL — возврат к `choose_type`.

- `students.csv` — заголовки **строго**: `surname,name,patronymic,group,lms_email`
- `teachers.csv` — заголовки **строго**: `surname,name,patronymic,weekly_limit`
- Доп. колонки → `E_CSV_SCHEMA`
- Пустые строки → игнор
- UTF‑8 BOM → поддерживается
- Валидация строк с накоплением ошибок; отчёт по `row_count / errors_count`

**Callback**: `r=o;a=im;t=s|t` (тип), файлы и отчёты — через `k=<uuid>`.

**Idempotency**: повторный импорт **того же** файла (по checksum содержимого) → no‑op.

**Audit**: `OWNER_UPLOAD_STUDENTS | OWNER_UPLOAD_TEACHERS`, `{request_id}`.

### 5.2 Поиск и профиль
- Поиск по фамилии (prefix) → `r=o;a=sp;k=<uuid>`
- Карточка профиля: ФИО, email, роль, группа/лимит, история сдач, финальные оценки.

### 5.3 Матрица назначений (Assignment)
- Автоназначение **round‑robin**.
- Предпросмотр CSV: **студенты × недели → преподаватели**.
- Callback: `r=o;a=as;s=p|c` (preview/commit). Данные для превью — через `k=<uuid>`.
- Audit: `OWNER_ASSIGN_PREVIEW / OWNER_ASSIGN_COMMIT` с `{request_id}`.

---

## 6) Материалы курса (после инициализации)

### Типы (строго по L1) и коды
- 📖 `p` — Домашние задачи и материалы для подготовки (**prep**)
- 📘 `m` — Методические рекомендации (**teacher**) — **код m используется везде**
- 📝 `n` — Конспекты
- 📊 `s` — Презентации
- 🎥 `v` — Записи лекций

**FSM: MaterialUploadStates**
```python
class MaterialUploadStates(Enum):
    pick_week = auto()
    pick_type = auto()
    upload_file = auto()
    confirm = auto()
    done = auto()
```
**Переходы**: `pick_week → pick_type → upload_file → confirm → done`
**TTL**: 15 мин; по TTL — возврат к `pick_week`.

**Callback**: `r=o;a=mt;w=<Wxx>;t=p|m|n|s|v`
Файлы/ссылки и длинные метаданные — через `k=<uuid>`.

**Версионирование материалов**
- На одну неделю и тип — **одна активная версия**.
- Новая загрузка: старая автоматически → **архив** (история версий).
- **Лимит архива**: по умолчанию **20 версий на (week,type)** (конфигурируемо).
- **Автоочистка**: при превышении лимита — удаление **самых старых** версий после успешного бэкапа (см. §8).

**Idempotency**: повторная загрузка **идентичного** файла (по checksum) → no‑op.

**Audit**: `OWNER_MATERIAL_UPLOAD / OWNER_MATERIAL_ARCHIVE / OWNER_MATERIAL_DELETE` + `{request_id}`.

---

## 7) Архив

- **Материалы**: `r=o;a=ar;t=m;w=<Wxx>` (детали при необходимости — `k=<uuid>`).
- **Решения студентов**: `r=o;a=ar;t=u;k=<uuid>` (fio/id и фильтры в StateStore).
- Действия: «📂 Скачать всё / 🗑️ Удалить всё» (только из архива).
- Ошибки: `E_NOT_FOUND`, `E_STORAGE_IO`.

---

## 8) Отчёты, аудит и бэкап

- Экспорт AuditLog: `r=o;a=rp;t=a`
- Экспорт оценок (финальных): `r=o;a=rp;t=g`
- Экспорт assignment matrix (активной): `r=o;a=rp;t=x`
- Экспорт конфигурации курса (weeks/materials): `r=o;a=rp;t=c`

**Бэкап**:
- Плановый: **ежедневно 03:00 UTC** → событие `SYSTEM_BACKUP_DAILY_COMMIT`.
- Вручную (кнопка «📦 Запустить бэкап сейчас» доступна вне impersonation): `r=o;a=rp;t=b` → триггер системного джоба.

**Audit события**: `OWNER_AUDIT_EXPORT / OWNER_REPORT_EXPORT / SYSTEM_BACKUP_DAILY_COMMIT` (+ `{request_id}`).

**Доп. toasts/edge cases**:
- ⚠️ «Файл идентичен активной версии — загрузка пропущена»
- ⚠️ «Импорт дублируется по checksum — пропущено»
- ⚠️ «Лишние колонки CSV — строка проигнорирована»
- ⚠️ «Удалены старые архивные версии: N»
- ⛔ «Недоступно в режиме имперсонизации»
- ✅ «Бэкап запущен» / ⛔ «Бэкап недоступен»

---

## 9) Имперсонизация (для тестирования)

- Start: `r=o;a=im` → ввести `tg_id` пользователя.
  - Не найден → `E_NOT_FOUND`.
  - **Owner→Owner** попытка → `E_ACCESS_DENIED` (запрещено).

**После старта**:
- Бейдж: `IMPERSONATING: <fio> (<role>)` (виден на всех экранах).
- В меню Owner **заменяем** «Имперсонизация» на «↩️ Завершить имперсонизацию» (`r=o;a=ix`).
- Дополнительные кнопки роли:
  - Если имперсонирован **преподаватель**: `[📚 Главное меню преподавателя]`
  - Если имперсонирован **студент**: `[🎓 Главное меню студента]`

**Матрица прав (сводно)**:
- **Разрешено**: чтение любых отчётов/материалов, просмотр профилей, экспорт (кроме бэкапа).
- **Запрещено**: инициализация курса, бэкап/restore, безвозвратные удаления из архива, изменение assignment matrix, загрузка/удаление материалов.

**Audit**: `DEV_IMPERSONATE_START / DEV_IMPERSONATE_COMMIT / DEV_IMPERSONATE_EXIT` (+ `{request_id}`).

---

## 10) Override оценок (право Owner)

- В карточке студента → «✏️ Изменить / ➕ Добавить оценку».
- Всегда через StateStore: `r=o;a=go;k=<uuid>` (`params`: `{student_id, week, score, letter, comment}`).
- На стороне Student видна **финальная** оценка (override приоритетнее).
- Audit: `OWNER_GRADE_OVERRIDE` (+ `{request_id}`).

---

## 11) Idempotency

- Импорт CSV: по checksum содержимого — повтор → no‑op.
- Загрузка материалов: по checksum — повтор → no‑op.
- Course init commit: повтор без изменений — no‑op.
- Имперсонизация: повторная активация того же tg_id — no‑op; выход — идемпотентен.

---

## 12) Репозитории (UX‑уровень требований, дополнено)

- `CourseRepository`:
  - `create_or_update(params)`
  - `list_weeks()`
  - `transaction()`  ← **атоматичные операции**
- `UserRepository`:
  - `import_students(csv, request_id)`
  - `import_teachers(csv, request_id)`
  - `search_by_fio(prefix)`
  - `transaction()`
  - `import_exists(checksum) -> bool`  ← для идемпотентности импорта
- `MaterialRepository`:
  - `upload(week, type_, file_bytes, request_id)`
  - `archive(material_id)`
  - `delete(material_id)`
  - `history(week, type_)`
  - `get_by_checksum(week, type_, checksum)`  ← **идемпотентность материалов**
  - `transaction()`
- `AssignmentRepository`:
  - `preview_matrix()`
  - `commit_matrix(request_id)`
  - `transaction()`

---

## 13) Навигация и toasts (расширено)

- Всегда доступны: `⬅️ Назад`, `🏠 Главное меню`.
- Дополнительные сообщения:
  - ✅ «Курс инициализирован»
  - ✅ «Импорт завершён: X строк, ошибок Y»
  - ✅ «Матрица создана»
  - ✅ «Материал загружен / версия архивирована»
  - ✅ «Бэкап запущен»
  - ⚠️ «Сессия истекла, начните шаг заново»
  - ⛔ «Недоступно в режиме имперсонизации»
