from aiogram import F, Router, types
from aiogram.filters import Command

from app.core.auth import Identity
from app.core.files import save_blob
from app.core.repos_epic4 import insert_submission, list_materials_by_week

router = Router(name="epic4")


@router.message(Command("materials"))
async def cmd_materials(message: types.Message):
    await message.answer("Введите номер недели (например, 1)")


@router.message(F.text.regexp(r"^\d+$"))
async def list_materials_for_week(message: types.Message):
    try:
        week_no = int(message.text)
    except Exception:
        return
    mats = list_materials_by_week(week_no)
    if not mats:
        await message.answer(f"Материалы для недели {week_no} не найдены.")
        return
    lines = [
        f"• material #{m.id} — sha256={m.sha256} size={m.size_bytes} path={m.path}"
        for m in mats
    ]
    await message.answer("\n".join(lines[:50]))


@router.message(Command("submit"))
async def cmd_submit(message: types.Message, actor: Identity):
    # как и раньше: отправить документ с подписью 'submit <assignment_id>'
    await message.answer("Отправьте документ с подписью: 'submit <assignment_id>'")


@router.message(F.document & F.caption.regexp(r"^submit\s+\d+$"))
async def receive_submission(message: types.Message, actor: Identity):
    assignment_id = int(message.caption.split()[1])
    doc = message.document
    file = await message.bot.get_file(doc.file_id)
    b = await message.bot.download_file(file.file_path)
    data = b.read()
    saved = save_blob(
        data, prefix="submissions", suggested_name=doc.file_name or "submission.bin"
    )
    # NB: submissions-таблица у вас уже есть по докам; репо для submissions оставляем прежним
    sub_id = insert_submission(
        assignment_id,
        actor.id,
        saved.sha256,
        saved.size_bytes,
        doc.file_name or "submission.bin",
    )
    await message.answer(f"✅ Сдача #{sub_id} сохранена (size={saved.size_bytes}).")
