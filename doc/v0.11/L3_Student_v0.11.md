# L3_Student_v0.11

## 0. Scope
Техническая спецификация сценариев Student. Заменяет L3_Student_final_v0.10. Общие контракты — L3_Common_v0.11.

---
## 1. Материалы и задания
- Просмотр материалов недели; ссылки/файлы публикуются Owner/Teacher.
- Отображение времени — формат L3_Common §2.

---
## 2. Запись на слоты
- Выбор временных слотов в пределах вместимости.
- Под капотом — короткий `op` + `state_store` (TTL 15 минут).

---
## 3. Сабмишены (загрузка работ)
- Директория: `/storage/submissions/{humanized_student_id}/{week}/{uuid}.{ext}`
- Лимит размера: ≤ 100MB; расширения — из белого списка (см. L3_Common §6).
- Аудит: `STUDENT_SUBMISSION_UPLOAD` с полями (file_uuid, size, sha256, storage_path).

---
## 4. Ошибки Student (дополнение)
- `E_SUBMISSION_TOO_LARGE`, `E_SUBMISSION_BAD_EXT`
- `E_STATE_EXPIRED` (просроченный `state_store`)
