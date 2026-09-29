"""Админ-панель: управление пользователями (доступ по Telegram ID из ADMIN_IDS)."""
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import settings
from database import count_users, get_all_users, get_user, search_users, set_access, update_profile

from .common import notify_user
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
            access_row,
            [InlineKeyboardButton(text="⬅️ К списку", callback_data="adm:users")],
        ]
    )


@router.message(Command("admin"))
async def admin_menu(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    await state.clear()
    kb, header = await _users_kb(0)
    await message.answer(header, reply_markup=kb)


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
            await callback.message.answer(header, reply_markup=kb)
        await callback.answer()
        return

    if action == "search":
        await state.set_state(AdminForm.waiting_search)
        await callback.message.answer("🔍 Введите ФИО, телефон, поликлинику, адрес или ID:")
        await callback.answer()
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
        await callback.answer()
    elif action in ("ffio", "faddr", "fclinic"):
        field = {"ffio": "fio", "faddr": "address", "fclinic": "clinic_short"}[action]
        label = {
            "fio": "ФИО",
            "address": "адрес поликлиники",
            "clinic_short": "номер поликлиники (сокращённо)",
        }[field]
        await state.update_data(adm_target=tg, adm_field=field)
        await state.set_state(AdminForm.waiting_value)
        await callback.message.answer(f"✏️ Введите новый <b>{label}</b> для {_uname(u)}:")
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
        await state.update_data(adm_target=tg, adm_field="deny")
        await state.set_state(AdminForm.waiting_value)
        await callback.message.answer(f"⛔ Укажите <b>причину отказа</b> для {_uname(u)}:")
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
async def adm_search(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    query = (message.text or "").strip()
    if not query:
        await message.answer("⚠️ Введите запрос текстом:")
        return
    await state.clear()
    kb, header = await _search_kb(query)
    await message.answer(header, reply_markup=kb)


@router.message(AdminForm.waiting_value)
async def adm_save(message: Message, state: FSMContext, bot: Bot) -> None:
    if not _is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    tg = data.get("adm_target")
    field = data.get("adm_field")
    value = (message.text or "").strip()
    if not tg or field not in ("fio", "address", "clinic_short", "deny") or not value:
        await message.answer("⚠️ Введите значение текстом:")
        return
    if field == "deny":
        await set_access(tg, "denied", reason=value, by=f"admin:{message.from_user.id}")
        await state.clear()
        ok = await notify_user(bot, tg, f"❌ В доступе отказано: {value}.")
        u = await get_user(tg)
        text = "⛔ Доступ отклонён" + ("" if ok else " (уведомить не удалось — бот заблокирован)")
        if u:
            await message.answer(text + ":\n\n" + _card(u), reply_markup=_detail_kb(u))
        else:
            await message.answer(text + ".")
        return
    if field == "fio" and not _valid_fio(value):
        await message.answer("⚠️ Введите ФИО полностью (например, Иванов Иван Иванович):")
        return
    if field == "address" and len(value) < 3:
        await message.answer("⚠️ Введите адрес поликлиники:")
        return
    await update_profile(tg, **{field: value})
    await state.clear()
    u = await get_user(tg)
    if u:
        await message.answer("✅ Сохранено:\n\n" + _card(u), reply_markup=_detail_kb(u))
    else:
        await message.answer("✅ Сохранено.")
