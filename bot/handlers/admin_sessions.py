from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Document, Message, TelegramObject

from bot import formatters as fmt
from bot.admin_keyboards import (
    ADMIN_SESSION_ADD_FILE,
    ADMIN_SESSION_ADD_PHONE,
    ADMIN_SESSION_DEL,
    ADMIN_SESSION_DEL_PREFIX,
    ADMIN_SESSION_FILE_DONE,
    ADMIN_SESSIONS,
    AUTH_CODE_BACK,
    AUTH_CODE_DIGIT_PREFIX,
    AUTH_CODE_DONE,
    CANCEL,
    MENU_HOME,
    admin_home_kb,
    auth_code_kb,
    cancel_kb,
    sessions_delete_kb,
    sessions_menu_kb,
    upload_files_kb,
)
from bot.message_ui import edit_screen
from clients.market_pool import MarketSessionPool
from config import Settings
from storage.database import Database

logger = logging.getLogger(__name__)
router = Router()

_PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")


class AdminOnly(BaseFilter):
    async def __call__(self, event: TelegramObject, settings: Settings) -> bool:
        user = getattr(event, "from_user", None)
        if user and settings.is_admin(user.id):
            return True
        if isinstance(event, CallbackQuery):
            await event.answer("Нет доступа", show_alert=True)
        return False


router.message.filter(AdminOnly())
router.callback_query.filter(AdminOnly())


class AdminSessionWizard(StatesGroup):
    wait_file = State()
    enter_phone = State()
    enter_code = State()
    enter_password = State()


_album_buffers: dict[str, list[Message]] = {}


def _is_admin(user_id: int, settings: Settings) -> bool:
    return settings.is_admin(user_id)


def _normalize_phone(raw: str) -> str | None:
    text = raw.strip().replace(" ", "").replace("-", "")
    if not text.startswith("+") and text.isdigit():
        text = "+" + text
    if _PHONE_RE.match(text):
        return text
    return None


async def show_home(
    target: Message,
    *,
    db: Database,
    pool: MarketSessionPool,
    edit: bool = False,
) -> None:
    text = fmt.admin_status_text(
        baseline_done=await db.is_baseline_done(),
        seen=await db.count_seen(),
        topics=len(await db.list_topics()),
        sessions_alive=pool.alive_count(),
        sessions_total=pool.total_count(),
    )
    markup = admin_home_kb()
    if edit:
        await edit_screen(target, text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


async def show_sessions(
    target: Message,
    pool: MarketSessionPool,
    *,
    edit: bool = True,
) -> None:
    items = pool.list_info()
    text = fmt.sessions_list_text(items)
    markup = sessions_menu_kb(has_sessions=bool(items))
    if edit:
        await edit_screen(target, text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


async def _cancel_to_sessions(
    *,
    message: Message,
    state: FSMContext,
    pool: MarketSessionPool,
    admin_id: int,
    edit: bool,
) -> None:
    await pool.cancel_auth(admin_id)
    await state.clear()
    await show_sessions(message, pool, edit=edit)


async def _add_session_from_document(
    *,
    bot: Bot,
    doc: Document,
    pool: MarketSessionPool,
    settings: Settings,
    admin_id: int,
) -> dict:
    name = doc.file_name or ""
    if not name.endswith(".session"):
        return {"ok": False, "file_name": name or "?", "error": "нужен файл .session"}

    settings.sessions_dir.mkdir(parents=True, exist_ok=True)
    tmp = settings.sessions_dir / f"_upload_{admin_id}_{doc.file_unique_id}.session"
    try:
        await bot.download(doc, destination=tmp)
        result = await pool.add_session_file(tmp, preferred_name=Path(name).stem)
    finally:
        tmp.unlink(missing_ok=True)
        Path(str(tmp) + "-journal").unlink(missing_ok=True)

    result["file_name"] = name
    return result


async def _reply_upload_results(message: Message, results: list[dict]) -> None:
    await message.answer(
        fmt.session_batch_upload_result_text(results),
        reply_markup=upload_files_kb(),
    )


async def _collect_album(message: Message) -> list[Message] | None:
    mgid = str(message.media_group_id)
    _album_buffers.setdefault(mgid, []).append(message)
    await asyncio.sleep(0.6)
    bucket = _album_buffers.get(mgid, [])
    if not bucket or message.message_id != max(item.message_id for item in bucket):
        return None
    return _album_buffers.pop(mgid)


@router.callback_query(F.data == MENU_HOME)
async def on_home(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    pool: MarketSessionPool,
    settings: Settings,
) -> None:
    uid = callback.from_user.id if callback.from_user else 0
    if not _is_admin(uid, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await pool.cancel_auth(uid)
    await state.clear()
    await show_home(callback.message, db=db, pool=pool, edit=True)
    await callback.answer()


@router.callback_query(F.data == ADMIN_SESSIONS)
async def on_sessions(
    callback: CallbackQuery,
    state: FSMContext,
    pool: MarketSessionPool,
    settings: Settings,
) -> None:
    uid = callback.from_user.id if callback.from_user else 0
    if not _is_admin(uid, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await pool.cancel_auth(uid)
    await state.clear()
    await show_sessions(callback.message, pool, edit=True)
    await callback.answer()


@router.callback_query(F.data == CANCEL)
async def on_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    pool: MarketSessionPool,
    settings: Settings,
) -> None:
    uid = callback.from_user.id if callback.from_user else 0
    if not _is_admin(uid, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _cancel_to_sessions(
        message=callback.message,
        state=state,
        pool=pool,
        admin_id=uid,
        edit=True,
    )
    await callback.answer()


@router.callback_query(F.data == ADMIN_SESSION_ADD_PHONE)
async def on_session_add_phone(
    callback: CallbackQuery,
    state: FSMContext,
    pool: MarketSessionPool,
    settings: Settings,
) -> None:
    uid = callback.from_user.id if callback.from_user else 0
    if not _is_admin(uid, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await pool.cancel_auth(uid)
    await state.set_state(AdminSessionWizard.enter_phone)
    await edit_screen(
        callback.message,
        fmt.session_phone_prompt_text(),
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == ADMIN_SESSION_ADD_FILE)
async def on_session_add_file(
    callback: CallbackQuery,
    state: FSMContext,
    pool: MarketSessionPool,
    settings: Settings,
) -> None:
    uid = callback.from_user.id if callback.from_user else 0
    if not _is_admin(uid, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await pool.cancel_auth(uid)
    await state.set_state(AdminSessionWizard.wait_file)
    await edit_screen(
        callback.message,
        fmt.session_add_prompt_text(),
        reply_markup=upload_files_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == ADMIN_SESSION_FILE_DONE)
async def on_session_files_done(
    callback: CallbackQuery,
    state: FSMContext,
    pool: MarketSessionPool,
    settings: Settings,
) -> None:
    uid = callback.from_user.id if callback.from_user else 0
    if not _is_admin(uid, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    current = await state.get_state()
    if current != AdminSessionWizard.wait_file.state:
        await callback.answer()
        return
    await state.clear()
    await edit_screen(
        callback.message,
        fmt.sessions_list_text(pool.list_info()),
        reply_markup=sessions_menu_kb(has_sessions=pool.total_count() > 0),
    )
    await callback.answer("Готово")


@router.message(AdminSessionWizard.enter_phone)
async def on_phone_entered(
    message: Message,
    state: FSMContext,
    pool: MarketSessionPool,
    settings: Settings,
) -> None:
    uid = message.from_user.id if message.from_user else 0
    if not _is_admin(uid, settings):
        return
    phone = _normalize_phone(message.text or "")
    if not phone:
        await message.answer("⚠️ Формат: <code>+79991234567</code>", reply_markup=cancel_kb())
        return

    result = await pool.start_phone_auth(uid, phone)
    if result == "banned":
        await state.clear()
        await message.answer(
            fmt.session_auth_failed_text("Номер заблокирован в Telegram."),
            reply_markup=sessions_menu_kb(has_sessions=pool.total_count() > 0),
        )
        return
    if result == "payment_required":
        await state.clear()
        await message.answer(
            fmt.session_auth_failed_text(
                "Telegram требует Premium для входа с этого номера. "
                "Загрузите готовый .session."
            ),
            reply_markup=sessions_menu_kb(has_sessions=pool.total_count() > 0),
        )
        return
    if result.startswith("flood:"):
        await state.clear()
        seconds = result.removeprefix("flood:")
        await message.answer(
            fmt.session_auth_failed_text(f"Слишком много попыток. Подождите {seconds} сек."),
            reply_markup=sessions_menu_kb(has_sessions=pool.total_count() > 0),
        )
        return
    if result != "code_sent":
        await state.clear()
        await message.answer(
            fmt.session_auth_failed_text(f"Не удалось отправить код: {result}"),
            reply_markup=sessions_menu_kb(has_sessions=pool.total_count() > 0),
        )
        return

    await state.update_data(auth_phone=phone, auth_code="")
    await state.set_state(AdminSessionWizard.enter_code)
    await message.answer(
        fmt.session_code_prompt_text(""),
        reply_markup=auth_code_kb(),
    )


@router.callback_query(AdminSessionWizard.enter_code, F.data.startswith(AUTH_CODE_DIGIT_PREFIX))
async def on_code_digit(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    uid = callback.from_user.id if callback.from_user else 0
    if not _is_admin(uid, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    digit = callback.data.removeprefix(AUTH_CODE_DIGIT_PREFIX)
    if not digit.isdigit():
        await callback.answer()
        return
    data = await state.get_data()
    code = (data.get("auth_code") or "") + digit
    if len(code) > 6:
        await callback.answer("Максимум 6 цифр", show_alert=True)
        return
    await state.update_data(auth_code=code)
    await edit_screen(
        callback.message,
        fmt.session_code_prompt_text(code),
        reply_markup=auth_code_kb(),
    )
    await callback.answer()


@router.callback_query(AdminSessionWizard.enter_code, F.data == AUTH_CODE_BACK)
async def on_code_back(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    uid = callback.from_user.id if callback.from_user else 0
    if not _is_admin(uid, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    code = (data.get("auth_code") or "")[:-1]
    await state.update_data(auth_code=code)
    await edit_screen(
        callback.message,
        fmt.session_code_prompt_text(code),
        reply_markup=auth_code_kb(),
    )
    await callback.answer()


@router.callback_query(AdminSessionWizard.enter_code, F.data == AUTH_CODE_DONE)
async def on_code_done(
    callback: CallbackQuery,
    state: FSMContext,
    pool: MarketSessionPool,
    settings: Settings,
) -> None:
    uid = callback.from_user.id if callback.from_user else 0
    if not _is_admin(uid, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    phone = data.get("auth_phone") or ""
    code = data.get("auth_code") or ""
    if len(code) < 5:
        await callback.answer("Введите код полностью", show_alert=True)
        return

    await callback.answer("Проверяю…")
    result = await pool.submit_code(uid, phone, code)

    if result == "ok":
        session = pool.sessions[-1] if pool.sessions else None
        await state.clear()
        await edit_screen(
            callback.message,
            fmt.session_added_text(
                label=session.label if session else "?",
                username=session.username if session else None,
                tg_user_id=session.tg_user_id if session else None,
            ),
            reply_markup=sessions_menu_kb(has_sessions=True),
        )
        return
    if result == "2fa_needed":
        await state.set_state(AdminSessionWizard.enter_password)
        await edit_screen(
            callback.message,
            fmt.session_password_prompt_text(),
            reply_markup=cancel_kb(),
        )
        return
    if result == "bad_code":
        await state.update_data(auth_code="")
        await edit_screen(
            callback.message,
            fmt.session_code_prompt_text(""),
            reply_markup=auth_code_kb(),
        )
        await callback.message.answer("⚠️ Неверный код. Введите заново.")
        return
    if result == "duplicate":
        await state.clear()
        await edit_screen(
            callback.message,
            fmt.session_auth_failed_text("Этот аккаунт уже добавлен."),
            reply_markup=sessions_menu_kb(has_sessions=pool.total_count() > 0),
        )
        return
    if result == "expired":
        await state.clear()
        await edit_screen(
            callback.message,
            fmt.session_auth_failed_text("Код истёк. Начните заново."),
            reply_markup=sessions_menu_kb(has_sessions=pool.total_count() > 0),
        )
        return

    await state.clear()
    await edit_screen(
        callback.message,
        fmt.session_auth_failed_text(f"Ошибка входа: {result}"),
        reply_markup=sessions_menu_kb(has_sessions=pool.total_count() > 0),
    )


@router.message(AdminSessionWizard.enter_password)
async def on_password_entered(
    message: Message,
    state: FSMContext,
    pool: MarketSessionPool,
    settings: Settings,
) -> None:
    uid = message.from_user.id if message.from_user else 0
    if not _is_admin(uid, settings):
        return
    password = (message.text or "").strip()
    if not password:
        await message.answer("⚠️ Отправьте пароль текстом.", reply_markup=cancel_kb())
        return

    try:
        await message.delete()
    except Exception:
        pass

    result = await pool.submit_password(uid, password)
    if result == "ok":
        session = pool.sessions[-1] if pool.sessions else None
        await state.clear()
        await message.answer(
            fmt.session_added_text(
                label=session.label if session else "?",
                username=session.username if session else None,
                tg_user_id=session.tg_user_id if session else None,
            ),
            reply_markup=sessions_menu_kb(has_sessions=True),
        )
        return
    if result == "duplicate":
        await state.clear()
        await message.answer(
            fmt.session_auth_failed_text("Этот аккаунт уже добавлен."),
            reply_markup=sessions_menu_kb(has_sessions=pool.total_count() > 0),
        )
        return

    await state.clear()
    await message.answer(
        fmt.session_auth_failed_text(f"Ошибка входа: {result}"),
        reply_markup=sessions_menu_kb(has_sessions=pool.total_count() > 0),
    )


@router.message(AdminSessionWizard.wait_file, F.document, F.media_group_id)
async def on_session_files_album(
    message: Message,
    bot: Bot,
    pool: MarketSessionPool,
    settings: Settings,
) -> None:
    uid = message.from_user.id if message.from_user else 0
    if not _is_admin(uid, settings):
        return

    messages = await _collect_album(message)
    if not messages:
        return

    results: list[dict] = []
    for item in sorted(messages, key=lambda msg: msg.message_id):
        doc = item.document
        if not doc:
            continue
        results.append(
            await _add_session_from_document(
                bot=bot,
                doc=doc,
                pool=pool,
                settings=settings,
                admin_id=uid,
            )
        )

    if not results:
        await message.answer(
            "⚠️ В альбоме нет файлов <code>.session</code>",
            reply_markup=upload_files_kb(),
        )
        return

    await _reply_upload_results(message, results)


@router.message(AdminSessionWizard.wait_file, F.document, F.media_group_id == None)
async def on_session_file(
    message: Message,
    bot: Bot,
    pool: MarketSessionPool,
    settings: Settings,
) -> None:
    uid = message.from_user.id if message.from_user else 0
    if not _is_admin(uid, settings):
        return
    doc = message.document
    if not doc:
        return

    result = await _add_session_from_document(
        bot=bot,
        doc=doc,
        pool=pool,
        settings=settings,
        admin_id=uid,
    )
    await _reply_upload_results(message, [result])


@router.message(AdminSessionWizard.wait_file)
async def on_session_file_wrong(message: Message, settings: Settings) -> None:
    uid = message.from_user.id if message.from_user else 0
    if not _is_admin(uid, settings):
        return
    await message.answer(
        "Отправьте файлы <code>.session</code>, альбомом или по одному. "
        "Когда закончите — нажмите «Готово».",
        reply_markup=upload_files_kb(),
    )


@router.callback_query(F.data == ADMIN_SESSION_DEL)
async def on_session_del_menu(
    callback: CallbackQuery,
    pool: MarketSessionPool,
    settings: Settings,
) -> None:
    uid = callback.from_user.id if callback.from_user else 0
    if not _is_admin(uid, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    items = pool.list_info()
    if not items:
        await callback.answer("Нет аккаунтов", show_alert=True)
        return
    await edit_screen(
        callback.message,
        fmt.session_delete_pick_text(),
        reply_markup=sessions_delete_kb(items),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(ADMIN_SESSION_DEL_PREFIX))
async def on_session_del(
    callback: CallbackQuery,
    pool: MarketSessionPool,
    settings: Settings,
) -> None:
    uid = callback.from_user.id if callback.from_user else 0
    if not _is_admin(uid, settings):
        await callback.answer("Нет доступа", show_alert=True)
        return
    label = callback.data.removeprefix(ADMIN_SESSION_DEL_PREFIX)
    ok = await pool.remove_session(label)
    if not ok:
        await callback.answer("Аккаунт не найден", show_alert=True)
        return
    await edit_screen(
        callback.message,
        fmt.session_deleted_text(label) + "\n\n" + fmt.sessions_list_text(pool.list_info()),
        reply_markup=sessions_menu_kb(has_sessions=pool.total_count() > 0),
    )
    await callback.answer("Удалено")
