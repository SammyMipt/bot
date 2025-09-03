# L2 — UX-спецификация: Student v0.10 (final)
Синхронизировано с L1 v0.10 и L3_Common v0.10

---

## Регистрация студента

### Стартовый экран
👋 Добро пожаловать в курс!
Выберите роль для регистрации:
[🎓 Зарегистрироваться как студент] [👨‍🏫 Зарегистрироваться как преподаватель]

### FSM States (Enum, согласно L3_Common):
```python
from enum import Enum, auto

class StudentRegistrationStates(Enum):
    enter_email = auto()
    confirm = auto()
    done = auto()
```

### FSM Transitions:
- `enter_email` → `confirm` (при валидном email)
- `confirm` → `done` (при подтверждении)
- Любое состояние → `enter_email` (reset при ошибке/отмене)
- Auto-reset при TTL 15 минут

Callback data: `r=s;a=reg`

### Error mapping (L3_Common registry):
- E_EMAIL_INVALID → «⛔ Некорректный формат email»
- E_EMAIL_NOT_FOUND → «❌ Такой email не найден»
- E_EMAIL_ALREADY_USED → «⚠️ Email уже используется»
- E_STATE_INVALID → «⛔ Некорректное состояние запроса»
- E_ACCESS_DENIED → «⛔ Доступ запрещён»
- E_TZ_INVALID → «⛔ Некорректный часовой пояс»

### Audit log:
`STUDENT_REGISTER_EMAIL {tg_id} {email} {success}`

---

## Главное меню `/student`
```
📘 WIC — Работа с неделями
📅 Мои записи на сдачу
📊 Мои оценки
📜 История сдач
🏠 Главное меню
```
**Навигационные правила (L3_Common):**
- Всегда доступны `⬅️ Назад` и `🏠 Главное меню`
- Для длинных списков: пагинация `◀︎` / `▶︎`

---

## 📘 WIC — Работа с неделями

### Экран выбора недели
Сообщение: «Выберите неделю:»
Кнопки: полный список `W01..Wnn` (без пагинации)
Callback: `r=s;a=w;w=<Wxx>`

### Меню недели `Wxx`
```
ℹ️ Описание и дедлайн
📖 Домашние задачи и материалы для подготовки
📝 Конспекты
📊 Презентации
🎥 Записи лекций
📤 Загрузить решение
⏰ Записаться на сдачу
❌ Отменить запись
✅ Узнать оценку
⬅️ Назад 🏠 Главное меню
```

#### ℹ️ Описание и дедлайн
- **Deadline формат**: `TimeService.format_dual_tz(utc_dt: datetime, course_tz: str, user_tz: str) -> str`
- **Отображение**: `2025-09-15 23:59 (course_tz) (у вас сейчас: 08:59)`
- **Преподаватель**: «Ваш преподаватель по Wxx: ФИО» (из матрицы назначений)

#### 📖/📝/📊 Материалы и 🎥 Записи лекций
- **Callback**: `r=s;a=mat;w=<Wxx>;t=<type>`
- **Types**: `prep`, `notes`, `slides`, `video` (согласно L1)
- **Поведение**:
  - `prep|notes|slides` → отправка активного файла (visibility=student, state=active)
  - `video` → сообщение с кликабельной ссылкой
- **Сообщения**: `📂 Материал получен` / `🔗 Ссылка на запись лекции`
- **Примечание**: материалы с visibility=teacher студентам не видны

---

## 📤 Загрузить решение

### FSM States:
```python
from enum import Enum, auto

class SolutionUploadStates(Enum):
    choosing_week = auto()
    uploading_file = auto()
    confirming_action = auto()
    done = auto()
```

### FSM Transitions:
- `choosing_week` → `uploading_file` (файл получен)
- `uploading_file` → `confirming_action` (файл валиден)
- `confirming_action` → `done` (подтверждение)
- Auto-reset при TTL 15 минут или критической ошибке

### Параметры:
- **Типы**: PNG/JPG/JPEG/PDF
- **Лимиты**: ≤5 файлов на неделю, ≤30 МБ суммарно
- **Callback основной**: `r=s;a=up;w=<Wxx>`
- **Callback удаления**: `r=s;a=del;w=<Wxx>;id=<file_id>`

### StateStore стратегия (L3_Common):
- **Условие**: payload > 64B
- **Паттерн**: `r=s;a=x;k=<uuid>`
- **TTL**: 15 минут
- **Cleanup**: автоматический по expired keys
- **Error handling**: при отсутствии key → E_STATE_INVALID

### Idempotency:
- **Дедупликация**: по checksum содержимого файла
- **Поведение**: если содержимое совпадает (даже при другом имени) → no-op
- **Сообщение**: «⚠️ Такой файл уже загружен (дубликат)»
- **Повторное удаление**: по `file_id` → no-op

### Error mapping:
- E_FILE_TYPE → «⛔ Неподдерживаемый тип файла»
- E_FILES_COUNT_LIMIT → «⚠️ Превышено число файлов (5)»
- E_BYTES_LIMIT → «⚠️ Превышен лимит: ≤30 МБ суммарно»
- E_STORAGE_IO → «⛔ Ошибка хранения файла»
- E_STATE_INVALID → «⛔ Сессия истекла. Начните заново.»
- E_ACCESS_DENIED → «⛔ Доступ запрещён»

### Audit log:
- `STUDENT_UPLOAD {student_id} {week} {file_id} {checksum}`
- `STUDENT_SOLUTION_DELETE {student_id} {week} {file_id}`

---

## ⏰ Записаться на сдачу

### FSM States:
```python
from enum import Enum, auto

class BookingStates(Enum):
    choosing_slot = auto()
    confirming = auto()
    done = auto()
```

### FSM Transitions:
- `choosing_slot` → `confirming` (слот выбран)
- `confirming` → `done` (подтверждение)
- Auto-reset при TTL 15 минут

### Логика отображения:
- **Источник слотов**: ТОЛЬКО назначенный преподаватель для недели (из матрицы назначений)
- **Callback**: `r=s;a=b;w=<Wxx>;id=<slot_id>`

### Status mapping (привязка к тех. статусам):
- `status=open` и `available_spots == capacity` → 🟢 свободно
- `status=open` и `0 < available_spots < capacity` → 🟡 частично
- `status=open` и `available_spots == 0` → 🔴 занят
- `status=closed` → 🚫 закрыт
- `status=past` → ⚫ прошёл

### Error mapping:
- E_ALREADY_BOOKED → «⚠️ У вас уже есть запись на эту неделю»
- E_SLOT_FULL → «⚠️ Слот полностью занят»
- E_SLOT_CLOSED → «⚠️ Слот закрыт для записи»
- E_PAST_DEADLINE → «⚠️ Запись недоступна после дедлайна»
- E_NOT_FOUND → «❌ Слот не найден»
- E_STATE_INVALID → «⛔ Сессия истекла. Начните заново.»

### Idempotency:
- **Повторная запись на тот же слот**: no-op, сообщение «ℹ️ Вы уже записаны на этот слот»
- **Нет доступных слотов**: «⚠️ Нет доступных слотов»

### Audit log:
- `STUDENT_BOOKING_CREATE {student_id} {slot_id} {week}`
- `STUDENT_BOOKING_CANCEL {student_id} {booking_id}`

---

## ❌ Отменить запись
- **Callback**: `r=s;a=bc;w=<Wxx>;id=<booking_id>`
- **Подтверждение**: «Отменить запись?» (Да/Нет)
- **Сообщение**: `❌ Запись отменена`

---

## ✅ Узнать оценку
- **Callback**: `r=s;a=g;w=<Wxx>`
- **Логика**: показывается финальная оценка (с учетом override владельца)
- **Нет оценки**: «⚠️ Оценка пока отсутствует»

---

## 📅 Мои записи
- **Callback**: `r=s;a=mb;w=<Wxx>;id=<booking_id>`
- Список всех активных записей по неделям
- Кнопка: неделя + дата/время + преподаватель

---

## 📊 Мои оценки
- Таблица по всем неделям с финальными оценками
- Нет оценок → «⚠️ У вас пока нет оценок»

---

## 📜 История сдач
- **Callback**: `r=s;a=h;w=<Wxx>;id=<booking_id>`
- Список прошедших сдач (status=past)
- Нет истории → «⚠️ У вас пока нет прошедших сдач»

---

## Toast & Confirm Registry (полный)
- ✅ Регистрация завершена
- 📂 Материал получен
- 🔗 Ссылка на запись лекции
- 📤 Файл загружен
- 🗑️ Файл удалён
- ✅ Запись создана
- ❌ Запись отменена
- ⚠️ Нет доступных слотов
- ⚠️ Слот временно недоступен
- ℹ️ Вы уже записаны на этот слот
- ⚠️ Такой файл уже загружен (дубликат)
- ⛔ Доступ запрещён
- ⛔ Сессия истекла. Начните заново.

---

## Audit Log Events (канонические)
- STUDENT_REGISTER_EMAIL {tg_id} {email} {success}
- STUDENT_UPLOAD {student_id} {week} {file_id} {checksum}
- STUDENT_SOLUTION_DELETE {student_id} {week} {file_id}
- STUDENT_BOOKING_CREATE {student_id} {slot_id} {week}
- STUDENT_BOOKING_CANCEL {student_id} {booking_id}
- STUDENT_GRADE_GET {student_id} {week}
