"""Хендлеры специалистов (работа в общем закрытом чате)."""
import logging
import re
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import settings
from database import (
    add_msg_link,
    get_ticket,
    get_ticket_by_engineer_msg,
    get_user,
    set_access,
    update_ticket_status,
)
from keyboards.inline import ticket_controls
from keyboards.reply import BTN_CANCEL

from .common import notify_user, ticket_card
from .fsm_states import AccessForm, RejectForm, WriteForm

log = logging.getLogger(__name__)
router = Router()

# Работаем только в чате специалистов
router.message.filter(F.chat.id == settings.GROUP_CHAT_ID)
router.callback_query.filter(F.message.chat.id == settings.GROUP_CHAT_ID)

_CB_RE = re.compile(r"^(take|done|reject):(\d+)$")


def _eng_name(user) -> str:
    return user.username or user.full_name or str(user.id)


async def _send_to_user(bot: Bot, telegram_id: int, text: str = "", photo_id: str = "") -> Optional[Message]:
    """Сообщение пользователю с возвратом Message (None — бот заблокирован)."""
    try:
        if photo_id:
            return await bot.send_photo(telegram_id, photo_id, caption=text)
        return await bot.send_message(telegram_id, text)
    except TelegramForbiddenError:
        log.warning("Пользователь %s заблокировал бота", telegram_id)
        return None
    except TelegramBadRequest as e:
        log.warning("Не удалось написать %s: %s", telegram_id, e)
        return None


@router.callback_query(F.data.regexp(r"^(take|done|reject):\d+$"))
async def engineer_action(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    m = _CB_RE.match(callback.data or "")
    if not m:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    action, ticket_id_s = m.group(1), m.group(2)
    ticket_id = int(ticket_id_s)
    t = await get_ticket(ticket_id)
    if not t:
        await callback.answer(f"Заявка #{ticket_id} не найдена в БД", show_alert=True)
        return

    engineer = _eng_name(callback.from_user)

    if action == "reject":
        # Переходим к вводу причины (следующее сообщение специалиста в чате)
        await state.set_state(RejectForm.waiting_reason)
        await state.update_data(ticket_id=ticket_id, msg_id=callback.message.message_id)
        await callback.message.reply(
            f"⛔ Укажите <b>причину отклонения заявки #{ticket_id}</b> следующим сообщением "
            f"(или «{BTN_CANCEL}» для отмены):"
        )
        await callback.answer("Введите причину отклонения")
        return

    new_status = "in_progress" if action == "take" else "done"
    if t["status"] == new_status or (t["status"] == "done" and action == "take"):
        await callback.answer("Статус уже актуален", show_alert=True)
        return

    await update_ticket_status(ticket_id, new_status, engineer_username=engineer)
    t = await get_ticket(ticket_id)

    # Обновляем карточку в чате (ответственный уже виден в самой карточке)
    suffix = "" if action == "take" else f"\n\n✅ Выполнил: @{engineer}"
    try:
        await callback.message.edit_text(
            ticket_card(t) + suffix, reply_markup=ticket_controls(ticket_id, new_status)
        )
    except TelegramBadRequest as e:
        log.warning("Не удалось отредактировать карточку #%s: %s", ticket_id, e)

    # Уведомление пользователю
    if action == "take":
        text = f"🛠 Ваша заявка <b>#{ticket_id}</b> взята в работу. Ожидайте выполнения."
    else:
        text = (
            f"✅ Заявка <b>#{ticket_id}</b> выполнена! "
            "Если проблема осталась — верните её в работу из «📁 Мои обращения»."
        )
    ok = await notify_user(bot, t["telegram_id"], text)
    if not ok:
        await callback.message.reply(
            f"⚠️ Не удалось уведомить пользователя по заявке #{ticket_id} (бот заблокирован)."
        )
    await callback.answer("Готово ✅")


@router.message(RejectForm.waiting_reason, F.text == BTN_CANCEL)
async def reject_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.reply("❌ Отклонение отменено.")


@router.callback_query(F.data.regexp(r"^retake:\d+$"))
async def retake_ticket(callback: CallbackQuery, bot: Bot) -> None:
    """Смена ответственного: «🔄 Взять на себя» — заявка остаётся в работе."""
    ticket_id = int(callback.data.split(":", 1)[1])
    t = await get_ticket(ticket_id)
    if not t:
        await callback.answer(f"Заявка #{ticket_id} не найдена в БД", show_alert=True)
        return
    if t["status"] != "in_progress":
        await callback.answer("Переназначить можно только заявку в работе", show_alert=True)
        return
    engineer = _eng_name(callback.from_user)
    if (t.get("engineer_username") or "") == engineer:
        await callback.answer("Вы уже ответственный по этой заявке", show_alert=True)
        return
    await update_ticket_status(ticket_id, "in_progress", engineer_username=engineer)
    t = await get_ticket(ticket_id)
    try:
        await callback.message.edit_text(
            ticket_card(t), reply_markup=ticket_controls(ticket_id, "in_progress")
        )
    except TelegramBadRequest as e:
        log.warning("Не удалось отредактировать карточку #%s: %s", ticket_id, e)
    ok = await notify_user(
        bot, t["telegram_id"], f"🔄 По вашей заявке <b>#{ticket_id}</b> назначен новый ответственный. Ожидайте выполнения."
    )
    if not ok:
        await callback.message.reply(
            f"⚠️ Не удалось уведомить пользователя по заявке #{ticket_id} (бот заблокирован)."
        )
    await callback.answer("Вы — ответственный ✅")


@router.callback_query(F.data.regexp(r"^write:\d+$"))
async def write_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка «✉️ Написать пользователю»: следующее сообщение уйдёт пользователю."""
    ticket_id = int(callback.data.split(":", 1)[1])
    t = await get_ticket(ticket_id)
    if not t:
        await callback.answer(f"Заявка #{ticket_id} не найдена в БД", show_alert=True)
        return
    await state.update_data(write_id=ticket_id)
    await state.set_state(WriteForm.waiting_text)
    await callback.message.reply(
        f"✉️ Напишите <b>сообщение пользователю по заявке #{ticket_id}</b> "
        "следующим сообщением (или «❌ Отмена»):"
    )
    await callback.answer("Введите сообщение")


@router.message(WriteForm.waiting_text)
async def write_send(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    ticket_id = data.get("write_id")
    t = await get_ticket(ticket_id) if ticket_id else None
    if not t:
        await state.clear()
        await message.reply("⚠️ Заявка не найдена.")
        return
    text = (message.text or message.caption or "").strip()
    photo_id = message.photo[-1].file_id if message.photo else ""
    if not text and not photo_id:
        await message.reply("⚠️ Напишите сообщение текстом (можно с фото) или отмените.")
        return
    await state.clear()
    await _forward_to_user(bot, t, text, photo_id=photo_id)
    await message.reply(f"✉️ Сообщение по заявке #{ticket_id} отправлено пользователю.")


@router.message(RejectForm.waiting_reason)
async def reject_reason_entered(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    msg_id = data.get("msg_id")
    reason = (message.text or "").strip()
    if not reason:
        await message.reply("⚠️ Введите причину текстом или отмените.")
        return

    t = await get_ticket(ticket_id)
    if not t:
        await state.clear()
        await message.reply(f"⚠️ Заявка #{ticket_id} не найдена.")
        return

    engineer = _eng_name(message.from_user)
    await update_ticket_status(ticket_id, "rejected", engineer_username=engineer, reject_reason=reason)
    await state.clear()
    t = await get_ticket(ticket_id)

    # Пытаемся обновить исходную карточку
    updated = False
    if msg_id:
        try:
            await bot.edit_message_text(
                ticket_card(t) + f"\n\n⛔ Отклонил: @{engineer}",
                chat_id=message.chat.id,
                message_id=msg_id,
                reply_markup=ticket_controls(ticket_id, "rejected"),
            )
            updated = True
        except TelegramBadRequest as e:
            log.warning("Не удалось отредактировать карточку #%s: %s", ticket_id, e)
    if not updated:
        await message.reply(ticket_card(t))

    ok = await notify_user(bot, t["telegram_id"], f"❌ Заявка <b>#{ticket_id}</b> отклонена. Причина: {reason}")
    if not ok:
        await message.reply(f"⚠️ Пользователь по заявке #{ticket_id} заблокировал бота.")
    else:
        await message.reply(f"⛔ Заявка #{ticket_id} отклонена.")


# ---------- одобрение доступа (модерация новичков) ----------

@router.callback_query(F.data.regexp(r"^access_(ok|no):\d+$"))
async def access_review(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    action, tg_s = callback.data.split(":")
    tg_id = int(tg_s)
    u = await get_user(tg_id)
    if not u or (u.get("access") or "pending") != "pending":
        await callback.answer("Запрос уже рассмотрен", show_alert=True)
        return

    engineer = _eng_name(callback.from_user)
    fio = (u.get("fio") or "").strip() or str(tg_id)

    if action == "access_no":
        await state.set_state(AccessForm.waiting_reason)
        await state.update_data(tg_id=tg_id, msg_id=callback.message.message_id)
        await callback.message.reply(
            f"⛔ Укажите <b>причину отказа для {fio}</b> следующим сообщением "
            f"(или «{BTN_CANCEL}» для отмены):"
        )
        await callback.answer("Введите причину отказа")
        return

    await set_access(tg_id, "approved", by=engineer)
    try:
        await callback.message.edit_text(
            callback.message.html_text + f"\n\n✅ Одобрил: @{engineer}",
            reply_markup=None,
        )
    except TelegramBadRequest as e:
        log.warning("Не удалось обновить запрос доступа %s: %s", tg_id, e)
    ok = await notify_user(
        bot, tg_id, "✅ <b>Доступ одобрен!</b> Теперь можно пользоваться ботом — нажмите /start."
    )
    if not ok:
        await callback.message.reply(
            f"⚠️ Пользователь {fio} ({tg_id}) одобрен, но уведомить не удалось (бот заблокирован)."
        )
    await callback.answer("Доступ одобрен ✅")


@router.message(AccessForm.waiting_reason, F.text == BTN_CANCEL)
async def access_deny_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.reply("❌ Отказ отменён.")


@router.message(AccessForm.waiting_reason)
async def access_deny_reason(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    tg_id = data.get("tg_id")
    msg_id = data.get("msg_id")
    reason = (message.text or "").strip()
    if not reason:
        await message.reply("⚠️ Введите причину текстом или отмените.")
        return
    u = await get_user(tg_id) if tg_id else None
    if not u:
        await state.clear()
        await message.reply("⚠️ Пользователь не найден.")
        return
    engineer = _eng_name(message.from_user)
    await set_access(tg_id, "denied", reason=reason, by=engineer)
    await state.clear()
    fio = (u.get("fio") or str(tg_id)).strip()
    if msg_id:
        try:
            await bot.edit_message_text(
                f"🆕 Запрос доступа: <b>{fio}</b>\n\n⛔ Отклонил: @{engineer}\nПричина: {reason}",
                chat_id=message.chat.id,
                message_id=msg_id,
                reply_markup=None,
            )
        except TelegramBadRequest as e:
            log.warning("Не удалось обновить запрос доступа %s: %s", tg_id, e)
    ok = await notify_user(bot, tg_id, f"❌ В доступе отказано: {reason}.")
    if not ok:
        await message.reply(f"⚠️ Пользователь {tg_id} заблокировал бота.")
    else:
        await message.reply("⛔ Доступ отклонён, пользователь уведомлён.")


# ---------- комментарии специалистов пользователю (ветка заявки) ----------

REPLY_HINT = "\n\n<i>↩️ Чтобы ответить специалистам — ответьте на это сообщение (Reply).</i>"

async def _forward_to_user(bot: Bot, t: dict, text: str, photo_id: str = "") -> None:
    """Переслать комментарий специалиста пользователю + запомнить связку для ответов."""
    if photo_id:
        body = f"💬 <b>Фото от специалистов по заявке #{t['id']}</b>"
        if text:
            body += f":\n{text}"
        body += REPLY_HINT
        sent = await _send_to_user(bot, t["telegram_id"], body, photo_id=photo_id)
    else:
        sent = await _send_to_user(
            bot,
            t["telegram_id"],
            f"💬 <b>Сообщение от специалистов по заявке #{t['id']}</b>:\n{text}{REPLY_HINT}",
        )
    if sent is None:
        # пользователь заблокировал бота — кидаем предупреждение в чат
        await bot.send_message(
            settings.GROUP_CHAT_ID,
            f"⚠️ Не удалось доставить комментарий пользователю по заявке #{t['id']} (бот заблокирован).",
        )
        return
    await add_msg_link(t["id"], t.get("engineer_msg_id") or 0, sent.message_id)


@router.message(
    StateFilter(None),
    F.reply_to_message,
    ~F.from_user.is_bot,
)
async def engineer_thread_comment(message: Message, bot: Bot) -> None:
    """Ответ специалиста на карточку заявки (reply): уходит пользователю, сохраняя связь."""
    reply_id = message.reply_to_message.message_id
    t = await get_ticket_by_engineer_msg(reply_id)
    if not t:
        return  # ответ не на карточку заявки — не трогаем
    text = (message.text or message.caption or "").strip()
    photo_id = message.photo[-1].file_id if message.photo else ""
    if not text and not photo_id:
        return
    await _forward_to_user(bot, t, text, photo_id=photo_id)
    await message.reply(f"💬 Комментарий по заявке #{t['id']} отправлен пользователю.")


@router.message(F.text.startswith("/comment"))
async def engineer_comment_cmd(message: Message, bot: Bot) -> None:
    """/comment <id> <текст> — комментарий пользователю без reply (на случай потери ветки)."""
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[1].lstrip("#").isdigit():
        await message.reply("Использование: <code>/comment 1001 Приедем завтра до обеда</code>")
        return
    t = await get_ticket(int(parts[1].lstrip("#")))
    if not t:
        await message.reply("Заявка не найдена.")
        return
    await _forward_to_user(bot, t, parts[2].strip())
    await message.reply(f"💬 Комментарий по заявке #{t['id']} отправлен пользователю.")


@router.message(F.text.startswith("/status"))
async def chat_status(message: Message) -> None:
    """/status <id> — быстро посмотреть статус заявки прямо из чата."""
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].lstrip("#").isdigit():
        await message.reply("Использование: <code>/status 1001</code>")
        return
    t = await get_ticket(int(parts[1].lstrip("#")))
    if not t:
        await message.reply("Заявка не найдена.")
        return
    await message.reply(ticket_card(t), reply_markup=ticket_controls(t["id"], t["status"]))
