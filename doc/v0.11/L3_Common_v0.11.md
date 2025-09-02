# L3_Common_v0.11

## 0. Scope
Единые технические правила и контракты для всех ролей (Owner/Teacher/Student) и сервисов бота.
Этот документ заменяет L3_Common_v0.10. Все ссылки в L2/L3 должны указывать на данную версию.

---
## 1. Идентификаторы пользователей
- **PK (в БД):** `uuid` (UUID v4).
- **Humanized ID:** используется в UI/отчётах/путях файлов.
  - Teacher/TA: `TA01..TA99` (две цифры).
  - Student: `ST001..ST999` (три цифры, дальше расширяется при необходимости).
- Уникальность Humanized ID обеспечивается в своей категории.
- При конфликте при восстановлении из бэкапа берётся следующий свободный номер.

---
## 2. TimeService и формат времени
**Единый формат отображения времени в тексте UI и аудитах:**
```
YYYY-MM-DD HH:MM (Europe/Moscow) · у вас сейчас ≈ HH:MM
```
- Левая часть: дата/время в часовом поясе курса (например, Europe/Moscow).
- Правая часть: ориентировочное локальное время пользователя (без даты).
- Все примеры в L2/L3 обязаны использовать именно этот формат.

---
## 3. StateStore (эпhemeral)
**Назначение:** хранение короткоживущих состояний FSM и ссылок из callback_data.

**Схема (SQLite):**
- `state_store(`
  `key TEXT PRIMARY KEY,`          — UUID v4 (36 символов)
  `role TEXT NOT NULL,`            — {owner|teacher|student|system}
  `action TEXT NOT NULL,`          — короткий код операции (напр., "sl_appl")
  `params TEXT NOT NULL,`          — JSON-строка с параметрами
  `created_at DATETIME NOT NULL,`
  `expires_at DATETIME NOT NULL`
  `)`
- Индексы: `CREATE INDEX idx_state_store_exp ON state_store(expires_at);`

**TTL:** 15 минут по умолчанию.

**Очистка:**
- Ленивая: при каждом чтении удалять записи с `expires_at < NOW()`.
- Периодическая: каждые 2 минуты job выполняет `DELETE FROM state_store WHERE expires_at < NOW()`.
- При memory pressure / high load: дополнительно удалять все записи старше 30 минут (по `created_at`).

**Восстановление:** не предусмотрено (эпемерные данные).
В случае недоступности записи возвращать `E_STATE_EXPIRED`.

**Коды ошибок StateStore:**
- `E_STATE_EXPIRED` — запись протухла/не найдена.
- `E_STATE_ROLE_MISMATCH` — роль не соответствует текущей сессии.

---
## 4. Callback codec (≤64B)
**Стратегия:** `callback_data = "{op}:{uuid}"`
- `op` — короткий код (напр., `"sl_appl"` = apply slot, `"sl_rm"` = remove slot).
- `uuid` — ключ в `state_store.key`.

**Правила:**
1) Никаких JSON в `callback_data`.
2) Любые параметры — в `state_store.params` (JSON).
3) Хендлер:
   - парсит `op/uuid`,
   - читает запись из `state_store`,
   - валидирует роль/TTL,
   - действует по `action/params`.

---
## 5. Backup policy и Health-check
**Определение `backup_recent()`:**
- есть full backup не старше 24h;
- есть последний incremental backup не старше 60 минут.

**Health-check перед тяжёлыми операциями:**
- `backup_recent() == true`,
- доступность `BACKUP_PATH`,
- свободное место на диске ≥ 20%.
При нарушении возвращать `E_BACKUP_STALE` и **блокировать** операции:
- `OWNER_AUDIT_EXPORT`,
- `MASS_IMPORT_{USERS|ASSIGNMENTS|GRADES}`,
- `MASS_EXPORT_{GRADES|SUBMISSIONS}`,
- `SYSTEM_BACKUP_DAILY_COMMIT`.

---
## 6. File storage policy
**Директории:**
- Материалы: `/storage/materials/{week}/{uuid}.{ext}`
- Сабмишены: `/storage/submissions/{humanized_student_id}/{week}/{uuid}.{ext}`
  - пример: `/storage/submissions/ST001/W05/550e8400-e29b-41d4-a716-446655440000.pdf`
- Экспорты: `/storage/exports/{date}/{uuid}.csv|xlsx|zip`
- Аудит: `/storage/audit/{date}/{uuid}.log`

**Правила:**
- В пути сабмишенов использовать `humanized_student_id` и неделю (`Wxx`).
- Имя файла — **всегда UUID** + оригинальное расширение из белого списка.
- Белый список: `.pdf,.docx,.xlsx,.csv,.png,.jpg,.zip`.
- Лимиты: materials ≤ 50MB, submissions ≤ 100MB.
- Дедупликация: по (`sha256`, `size_bytes`).
- Квоты: per-course soft-limit 10GB → при превышении `E_STORAGE_QUOTA`.

---
## 7. Audit payloads (минимальный состав)
- `OWNER_MATERIAL_UPLOAD`:

  `actor_id, role, week, file_uuid, original_filename, mime, size_bytes, sha256, storage_path, result{ok|error,code?}`
- `TEACHER_GRADE_SET`:

  `actor_id, student_id, assignment_id, grade_value, previous_value?, comment?, result`
- `STUDENT_SUBMISSION_UPLOAD`:

  `actor_id=student_id, week, assignment_id, file_uuid, size_bytes, sha256, storage_path, result`

Любые действия в режиме имперсонизации пишутся с:

`actor_id = owner_id`, `as_role`, `as_id`.

---
## 8. SQLite runtime settings
- `PRAGMA journal_mode = WAL`
- `PRAGMA synchronous = NORMAL`
- `PRAGMA busy_timeout = 5000` (мс)
- Писать короткими транзакциями.
- Массовые вставки батчами по 500–1000 записей.

---
## 9. Text normalization (ФИО/поиск)
- Нормализация на приложении перед записью:

  - Unicode NFKD → удалить combining marks (диакритика)

  - lower-case, trim, collapse spaces
- В БД хранить `surname_norm`, `name_norm`, `patronymic_norm`.
- Поиск в UI и импортах идёт по *_norm.

---
## 10. Ошибки (фрагмент реестра)
- `E_STATE_EXPIRED`, `E_STATE_ROLE_MISMATCH`, `E_BACKUP_STALE`, `E_STORAGE_QUOTA`,
  `E_CSV_MISSING_FIELD`, `E_CSV_EMAIL_INVALID`, `E_CSV_DUPLICATE`, `E_CSV_DUPLICATE_EMAIL`, `E_CSV_CONFLICT`.
