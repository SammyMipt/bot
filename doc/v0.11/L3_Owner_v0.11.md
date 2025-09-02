# L3_Owner_v0.11

## 0. Scope
Техническая спецификация сценариев Owner. Заменяет L3_owner_v0.10. Использует контракты из L3_Common_v0.11.

---
## 1. Роли и доступ
- Owner: полные права настройки курса, импорты/экспорты, имперсонизация.
- Любые тяжёлые операции выполняются только если `backup_recent() == true` (см. L3_Common §5).

---
## 2. Имперсонизация
Используется для тестирования и поддержки.
- Сущность `impersonation_session` (см. L3_Common §3/ImpersonationSession).
- TTL 30 минут, одна активная на Owner, запрет nested.
- Любые действия в аудит с `actor_id = owner_id`, `as_role/as_id = target`.
- UI обязан явно показывать «Вы действуете как <роль/ID>, осталось: <минут>».

Хендлеры Owner:
- `owner_impersonate_start(role, target_id)` → создаёт сессию, пишет аудит `OWNER_IMPERSONATE_START`.
- `owner_impersonate_stop()` → завершает (идемпотентно), аудит `OWNER_IMPERSONATE_STOP`.
- Ошибки: `E_IMPERSONATE_FORBIDDEN`, `E_IMPERSONATE_ACTIVE`, `E_IMPERSONATE_EXPIRED`.

---
## 3. Импорт пользователей
Система поддерживает **два отдельных формата**: Teachers (TA) и Students.

### 3.1 Teachers (TA)
**Обязательные поля:** `surname`, `name`
**Опциональные:** `patronymic`, `email`

**Правила и идентификаторы:**
- Humanized ID генерируется автоматически: `TA01`, `TA02`, … `TA99`.
- В БД PK = `uuid`; Humanized ID — уникальное поле.

**Валидация:**
- Пустые обязательные → `E_CSV_MISSING_FIELD`
- Дубликат ФИО (surname+name+patronymic) → `E_CSV_DUPLICATE`
- Дубликат `email` (если указан) → `E_CSV_DUPLICATE_EMAIL`

**Отчёт об ошибках (CSV):** `row_index, field, error_code, message`

Пример заголовков CSV: `surname,name,patronymic,email`

### 3.2 Students
**Обязательные поля:** `surname`, `name`, `email`, `group`
**Опциональные:** `patronymic`

**Правила и идентификаторы:**
- Humanized ID генерируется автоматически: `ST001`, `ST002`, …
- В БД PK = `uuid`; Humanized ID — уникальное поле.

**Валидация:**
- Пустые обязательные → `E_CSV_MISSING_FIELD`
- Невалидный email → `E_CSV_EMAIL_INVALID`
- Дубликат email → `E_CSV_DUPLICATE_EMAIL`
- Конфликт (email уже закреплён за другим ST-ID) → `E_CSV_CONFLICT`

**Отчёт об ошибках (CSV):** `row_index, field, error_code, message`

Пример заголовков CSV: `surname,name,patronymic,email,group`

---
## 4. Материалы курса (загрузка/архив)
- Загрузка: лимит 50MB, допустимые расширения см. L3_Common §6.
- Сохранение: `/storage/materials/{week}/{uuid}.{ext}`; аудит `OWNER_MATERIAL_UPLOAD`.
- Архивирование: перевод в состояние `archived`; доступно только Owner.
- На операции экспорт/массовые изменения распространяется `backup_recent()`.

---
## 5. Экспорт/аудит/отчёты
- `OWNER_AUDIT_EXPORT` доступен только при `backup_recent()`.
- Форматы: CSV/XLSX/ZIP. Пути: `/storage/exports/{date}/{uuid}.{ext}`.
- Аудит содержит `actor_id`, `as_role/as_id` (если под имперсонизацией).

---
## 6. Callback и StateStore
- Все кнопки Owner используют кодек: `"{op}:{uuid}"` (см. L3_Common §4).
- Параметры операций складываются в `state_store.params` (TTL 15 минут).
- На входе каждого хендлера — валидация роли и TTL.

---
## 7. Ошибки Owner (дополнение к общему реестру)
- `E_BACKUP_STALE` — запрет тяжёлых операций без свежего бэкапа.
- `E_IMPORT_FORMAT` — структура файла не соответствует ожидаемой.
- `E_IMPORT_OVER_LIMIT` — файл > 10MB.
- `E_IMPERSONATE_*` — см. §2.
