"""Админ-панель: управление пользователями (доступ по Telegram ID из ADMIN_IDS)."""
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import settings
from database import (
    STATUS_LABELS,
    TYPE_LABELS,
    count_users,
    get_all_users,
    get_ticket,
    get_user,
    get_user_tickets,
    search_users,
    set_access,
    set_engineer_msg_id,
    update_profile,
)
from keyboards.inline import ticket_controls

from .common import fmt_dt, notify_user, photos_of, ticket_card
from .fsm_states import AdminForm
from .user_handlers import _valid_fio

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == "private")

ACCESS_LABELS = {"pending": "⏳ На рассмотрении", "approved": "✅ Одобрен", "denied": "🚫 Закрыт"}
ACCESS_EMOJI = {"pending": "⏳", "approved": "✅", "denied": "🚫"}


def _is_admin(tg_id: int) -> bool:
    return tg_id in settings.admin_ids


def _uname(u: dict) -> str:
    if (u.get("fio") or "").strip():
        return u["fio"].strip()
    if u.get("username"):
        return "@" + u["username"]
    return f"id {u.get('telegram_id')}"


def _card(u: dict) -> str:
    access = (u.get("access") or "pending").strip() or "pending"
    return (
        f"👤 <b>{(u.get('fio') or '—').strip() or '—'}</b>\n"
        f"✉️ @{u.get('username') or '—'} (<code>{u.get('telegram_id')}</code>)\n"
        f"📞 Телефон: {(u.get('phone') or '—').strip() or '—'}\n"
        f"🏥 Поликлиника: {(u.get('clinic_short') or '—').strip() or '—'}\n"
        f"📍 Адрес: {(u.get('address') or '—').strip() or '—'}\n"
        f"💼 Должность: {(u.get('position') or '—').strip() or '—'}\n"
        f"🔑 Доступ: {ACCESS_LABELS.get(access, access)}"
    )


PAGE_SIZE = 20


def _label(u: dict) -> str:
    """Строка списка: «Номер поликлиники — ФИО»."""
    clinic = (u.get("clinic_short") or "").strip() or "—"
    name = (u.get("fio") or "").strip()
    if not name:
        name = ("@" + u["username"]) if u.get("username") else f"id {u.get('telegram_id')}"
    return f"{clinic} — {name}"


async def _users_kb(page: int = 0) -> tuple[InlineKeyboardMarkup, str]:
    """Весь список постранично. Возвращает (клавиатура, заголовок)."""
    total = await count_users()
    pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = min(max(page, 0), pages - 1)
    users = await get_all_users(PAGE_SIZE, page * PAGE_SIZE)
    rows = []
    for u in users:
        emo = ACCESS_EMOJI.get((u.get("access") or "pending").strip(), "❓")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{emo} {_label(u)}",
                    callback_data=f"adm:user:{u['telegram_id']}",
                )
            ]
        )
    nav = [InlineKeyboardButton(text="🔍 Поиск", callback_data="adm:search")]
    if page > 0:
        nav.insert(0, InlineKeyboardButton(text="⬅️", callback_data=f"adm:users:{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"adm:users:{page + 1}"))
    rows.append(nav)
    header = f"🛠 <b>Админ-панель</b>\n👥 Пользователи: всего {total}, стр. {page + 1}/{pages}"
    return InlineKeyboardMarkup(inline_keyboard=rows), header


async def _search_kb(query: str) -> tuple[InlineKeyboardMarkup, str]:
    users = await search_users(query)
    rows = []
    for u in users:
        emo = ACCESS_EMOJI.get((u.get("access") or "pending").strip(), "❓")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{emo} {_label(u)}",
                    callback_data=f"adm:user:{u['telegram_id']}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text="🔍 Новый поиск", callback_data="adm:search"),
            InlineKeyboardButton(text="⬅️ Все пользователи", callback_data="adm:users"),
        ]
    )
    header = f"🔍 Поиск «{query}»: найдено {len(users)}"
    return InlineKeyboardMarkup(inline_keyboard=rows), header


def _detail_kb(u: dict) -> InlineKeyboardMarkup:
    tg = u["telegram_id"]
    access = (u.get("access") or "pending").strip() or "pending"
    if access == "pending":
        access_row = [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"adm:approve:{tg}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm:deny:{tg}"),
        ]
    elif access == "denied":
        access_row = [InlineKeyboardButton(text="✅ Открыть доступ", callback_data=f"adm:unblock:{tg}")]
    else:
        access_row = [InlineKeyboardButton(text="🚫 Закрыть доступ", callback_data=f"adm:block:{tg}")]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ ФИО", callback_data=f"adm:ffio:{tg}")],
            [InlineKeyboardButton(text="✏️ Адрес", callback_data=f"adm:faddr:{tg}")],
            [InlineKeyboardButton(text="✏️ Поликлиника", callback_data=f"adm:fclinic:{tg}")],
            [InlineKeyboardButton(text="📁 Обращения", callback_data=f"adm:tickets:{tg}")],
            access_row,
            [InlineKeyboardButton(text="⬅️ К списку", callback_data="adm:users")],
        ]
    )


def _ticket_back_kb(owner_tg: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ К пользователю", callback_data=f"adm:user:{owner_tg}")]
        ]
    )


def _ticket_kb(ticket_id: int, owner_tg: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📨 Дублировать в чат", callback_data=f"adm:resend:{ticket_id}")],
            [InlineKeyboardButton(text="⬅️ К пользователю", callback_data=f"adm:user:{owner_tg}")],
        ]
    )


async def _edit_menu(bot: Bot, chat_id: int, msg_id: int, text: str, kb=None) -> int | None:
    """Править меню-сообщение. Вернёт msg_id или None, если править нечего/нельзя."""
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=kb)
        return msg_id
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return msg_id
        return None
    except Exception:  # noqa: BLE001
        return None


async def _show_menu(bot: Bot, chat_id: int, state: FSMContext, text: str, kb=None) -> None:
    """Показать меню в единственном сообщении: правим старое, иначе шлём новое."""
    data = await state.get_data()
    old_id = data.get("adm_menu")
    new_id = None
    if old_id:
        new_id = await _edit_menu(bot, chat_id, old_id, text, kb)
    if new_id is None:
        sent = await bot.send_message(chat_id, text, reply_markup=kb)
        new_id = sent.message_id
    await state.update_data(adm_menu=new_id)


async def _tidy(message: Message, bot: Bot) -> None:
    """Удалить ввод пользователя, чтобы чат не засорялся."""
    try:
        await bot.delete_message(message.chat.id, message.message_id)
    except Exception:  # noqa: BLE001
        pass


@router.message(Command("admin"))
async def admin_menu(message: Message, state: FSMContext, bot: Bot) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    data = await state.get_data()
    old_id = data.get("adm_menu")
    await state.clear()
    if old_id:
        await state.update_data(adm_menu=old_id)
    kb, header = await _users_kb(0)
    await _show_menu(bot, message.chat.id, state, header, kb)
    await _tidy(message, bot)  # убираем саму команду /admin


@router.callback_query(F.data.startswith("adm:"))
async def adm_router(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if callback.message.chat.type != "private" or not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа.", show_alert=True)
        return
    parts = callback.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "users":
        await state.clear()
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        kb, header = await _users_kb(page)
        try:
            await callback.message.edit_text(header, reply_markup=kb)
        except Exception:  # noqa: BLE001
            sent = await callback.message.answer(header, reply_markup=kb)
            await state.update_data(adm_menu=sent.message_id)
            await callback.answer()
            return
        await state.update_data(adm_menu=callback.message.message_id)
        await callback.answer()
        return

    if action == "search":
        await state.update_data(adm_menu=callback.message.message_id)
        await state.set_state(AdminForm.waiting_search)
        try:
            await callback.message.edit_text("🔍 Введите ФИО, телефон, поликлинику, адрес или ID:")
        except Exception:  # noqa: BLE001
            pass
        await callback.answer()
        return

    if action == "ticket":
        if len(parts) < 3 or not parts[2].isdigit():
            await callback.answer("Некорректные данные", show_alert=True)
            return
        t = await get_ticket(int(parts[2]))
        if not t:
            await callback.answer("Заявка не найдена", show_alert=True)
            return
        await callback.message.edit_text(
            ticket_card(t), reply_markup=_ticket_kb(t["id"], t["telegram_id"])
        )
        await state.update_data(adm_menu=callback.message.message_id)
        await callback.answer()
        return

    if action == "resend":
        if len(parts) < 3 or not parts[2].isdigit():
            await callback.answer("Некорректные данные", show_alert=True)
            return
        t = await get_ticket(int(parts[2]))
        if not t:
            await callback.answer("Заявка не найдена", show_alert=True)
            return
        tid = t["id"]
        try:
            sent = await bot.send_message(
                settings.GROUP_CHAT_ID,
                ticket_card(t, header="🔁 <b>Дубликат заявки (отправлен администратором)</b>"),
                reply_markup=ticket_controls(tid, t.get("status") or "new"),
            )
        except Exception as e:  # noqa: BLE001
            log.error("Админ не смог продублировать заявку #%s: %s", tid, e)
            await callback.answer("Не удалось отправить в чат", show_alert=True)
            return
        await set_engineer_msg_id(tid, sent.message_id)
        for fid in photos_of(t):
            try:
                await bot.send_photo(settings.GROUP_CHAT_ID, fid, reply_to_message_id=sent.message_id)
            except Exception as e:  # noqa: BLE001
                log.warning("Не удалось переслать фото дубликата #%s: %s", tid, e)
        await callback.answer(f"Заявка #{tid} продублирована в чат ✅")
        return

    if len(parts) < 3 or not parts[2].isdigit():
        await callback.answer("Некорректные данные", show_alert=True)
        return
    tg = int(parts[2])
    u = await get_user(tg)
    if not u:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    if action == "user":
        await state.clear()
        await callback.message.edit_text(_card(u), reply_markup=_detail_kb(u))
        await state.update_data(adm_menu=callback.message.message_id)
        await callback.answer()
    elif action == "tickets":
        tickets = await get_user_tickets(tg)
        if not tickets:
            await callback.message.edit_text(
                f"📭 У пользователя {_uname(u)} обращений нет.",
                reply_markup=_ticket_back_kb(tg),
            )
            await callback.answer()
            return
        rows = []
        for t in tickets[:30]:
            status = STATUS_LABELS.get(t.get("status", ""), t.get("status", ""))
            ttype = TYPE_LABELS.get(t.get("type", ""), t.get("type", ""))
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"#{t['id']} • {ttype} • {status} • {fmt_dt(t.get('created_at'))}",
                        callback_data=f"adm:ticket:{t['id']}",
                    )
                ]
            )
        rows.append(
            [InlineKeyboardButton(text="⬅️ К пользователю", callback_data=f"adm:user:{tg}")]
        )
        await callback.message.edit_text(
            f"📁 Обращения пользователя {_uname(u)} (всего {len(tickets)}):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        await state.update_data(adm_menu=callback.message.message_id)
        await callback.answer()
    elif action in ("ffio", "faddr", "fclinic"):
        field = {"ffio": "fio", "faddr": "address", "fclinic": "clinic_short"}[action]
        label = {
            "fio": "ФИО",
            "address": "адрес поликлиники",
            "clinic_short": "номер поликлиники (сокращённо)",
        }[field]
        await state.update_data(
            adm_target=tg, adm_field=field, adm_menu=callback.message.message_id
        )
        await state.set_state(AdminForm.waiting_value)
        try:
            await callback.message.edit_text(f"✏️ Введите новый <b>{label}</b> для {_uname(u)}:")
        except Exception:  # noqa: BLE001
            pass
        await callback.answer()
    elif action == "approve":
        if (u.get("access") or "pending").strip() != "pending":
            await callback.answer("Заявка уже рассмотрена", show_alert=True)
            return
        await set_access(tg, "approved", by=f"admin:{callback.from_user.id}")
        ok = await notify_user(bot, tg, "✅ <b>Доступ одобрен!</b> Теперь можно пользоваться ботом — нажмите /start.")
        u = await get_user(tg)
        await callback.message.edit_text(_card(u) + ("\n⚠️ Уведомить не удалось (бот заблокирован)." if not ok else ""), reply_markup=_detail_kb(u))
        await callback.answer("Доступ одобрен ✅")
    elif action == "deny":
        if (u.get("access") or "pending").strip() != "pending":
            await callback.answer("Заявка уже рассмотрена", show_alert=True)
            return
        await state.update_data(
            adm_target=tg, adm_field="deny", adm_menu=callback.message.message_id
        )
        await state.set_state(AdminForm.waiting_value)
        try:
            await callback.message.edit_text(f"⛔ Укажите <b>причину отказа</b> для {_uname(u)}:")
        except Exception:  # noqa: BLE001
            pass
        await callback.answer()
    elif action == "block":
        await set_access(tg, "denied", reason="Заблокировано администратором", by=f"admin:{callback.from_user.id}")
        ok = await notify_user(bot, tg, "❌ Доступ к боту закрыт администратором.")
        u = await get_user(tg)
        await callback.message.edit_text(_card(u) + ("\n⚠️ Уведомить не удалось (бот заблокирован)." if not ok else ""), reply_markup=_detail_kb(u))
        await callback.answer("Доступ закрыт")
    elif action == "unblock":
        await set_access(tg, "approved", by=f"admin:{callback.from_user.id}")
        ok = await notify_user(bot, tg, "✅ Доступ к боту открыт. Нажмите /start.")
        u = await get_user(tg)
        await callback.message.edit_text(_card(u) + ("\n⚠️ Уведомить не удалось (бот заблокирован)." if not ok else ""), reply_markup=_detail_kb(u))
        await callback.answer("Доступ открыт")
    else:
        await callback.answer("Некорректные данные", show_alert=True)


@router.message(AdminForm.waiting_search)
async def adm_search(message: Message, state: FSMContext, bot: Bot) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    query = (message.text or "").strip()
    data = await state.get_data()
    menu_id = data.get("adm_menu")
    if not query:
        if menu_id:
            await _edit_menu(bot, message.chat.id, menu_id, "⚠️ Введите запрос текстом:")
        await _tidy(message, bot)
        return
    await state.clear()
    if menu_id:
        await state.update_data(adm_menu=menu_id)
    kb, header = await _search_kb(query)
    await _show_menu(bot, message.chat.id, state, header, kb)
    await _tidy(message, bot)


@router.message(AdminForm.waiting_value)
async def adm_save(message: Message, state: FSMContext, bot: Bot) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    tg = data.get("adm_target")
    field = data.get("adm_field")
    menu_id = data.get("adm_menu")
    value = (message.text or "").strip()

    async def _prompt_again(text: str) -> None:
        if menu_id:
            await _edit_menu(bot, message.chat.id, menu_id, text)
        await _tidy(message, bot)

    if not tg or field not in ("fio", "address", "clinic_short", "deny") or not value:
        await _prompt_again("⚠️ Введите значение текстом:")
        return
    if field == "deny":
        await set_access(tg, "denied", reason=value, by=f"admin:{message.from_user.id}")
        await state.clear()
        if menu_id:
            await state.update_data(adm_menu=menu_id)
        ok = await notify_user(bot, tg, f"❌ В доступе отказано: {value}.")
        u = await get_user(tg)
        text = "⛔ Доступ отклонён" + ("" if ok else " (уведомить не удалось — бот заблокирован)")
        if u:
            text += ":\n\n" + _card(u)
            kb = _detail_kb(u)
        else:
            text += "."
            kb = None
        await _show_menu(bot, message.chat.id, state, text, kb)
        await _tidy(message, bot)
        return
    if field == "fio" and not _valid_fio(value):
        await _prompt_again("⚠️ Введите ФИО полностью (например, Иванов Иван Иванович):")
        return
    if field == "address" and len(value) < 3:
        await _prompt_again("⚠️ Введите адрес поликлиники:")
        return
    await update_profile(tg, **{field: value})
    await state.clear()
    if menu_id:
        await state.update_data(adm_menu=menu_id)
    u = await get_user(tg)
    if u:
        await _show_menu(bot, message.chat.id, state, "✅ Сохранено:\n\n" + _card(u), _detail_kb(u))
    else:
        await _show_menu(bot, message.chat.id, state, "✅ Сохранено.", None)
    await _tidy(message, bot)
