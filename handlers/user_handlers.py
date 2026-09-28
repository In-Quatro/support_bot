"""Хендлеры пользователей (личные сообщения с ботом)."""
import ipaddress
import logging
import re

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InputMediaPhoto,
    Message,
)

from config import settings
from database import (
    STATUS_LABELS,
    TYPE_LABELS,
    create_ticket,
    get_link_by_user_msg,
    get_ticket,
    get_user,
    get_user_tickets,
    set_access,
    set_engineer_msg_id,
    set_ticket_reopened,
    update_profile,
    upsert_user,
)
from keyboards.inline import (
    CARTRIDGE_LABELS,
    access_review_kb,
    cart_multi_kb,
    cart_scope_kb,
    edit_fields_kb,
    my_tickets_list,
    place_kb,
    profile_kb,
    reopen_kb,
    resend_access_kb,
    ticket_controls,
)
from keyboards.reply import (
    BTN_BACK_MENU,
    BTN_CANCEL,
    BTN_CARTRIDGE,
    BTN_CONFIRM,
    BTN_MY_TICKETS,
    BTN_NEW_TICKET,
    BTN_PROFILE,
    BTN_SKIP,
    cancel_kb,
    confirm_kb,
    main_menu,
    phone_kb,
    skip_cancel_kb,
)

from .common import fmt_dt, photos_of, save_photos_local, ticket_card
from .fsm_states import CartridgeForm, ProfileForm, ReopenForm, SupportForm

log = logging.getLogger(__name__)
router = Router()

IP_EXAMPLE = "10.15.25.35"
TECH_EXAMPLE = "001-0001"
TECH_RE = re.compile(r"^\d{3}-\d{4}$")


# ---------- общие ----------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await upsert_user(
        message.from_user.id,
        message.from_user.full_name,
        message.from_user.username or "",
    )
    await message.answer(
        "👋 Здравствуйте! Я бот приёма обращений в техническую поддержку.\n\n"
        "📝 — создать заявку в ТП\n"
        "🖨 — запросить картриджи\n"
        "📁 — посмотреть свои обращения\n"
        "👤 — мои данные\n\n"
        "❗ Для пользования ботом нужно зарегистрироваться (ФИО, телефон, "
        "поликлиника, адрес, должность) и дождаться одобрения.\n"
        "💬 Комментарии специалистов приходят отдельными сообщениями — "
        "чтобы ответить, нажмите на такое сообщение и выберите «Ответить» (Reply).",
        reply_markup=main_menu(),
    )


@router.message(F.text == BTN_BACK_MENU)
async def back_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🏠 Главное меню:", reply_markup=main_menu())


@router.message(StateFilter("*"), F.text == BTN_CANCEL)
async def cancel_any(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("🏠 Главное меню:", reply_markup=main_menu())
        return
    await state.clear()
    await message.answer("❌ Опрос отменён. Данные не сохранены.", reply_markup=main_menu())


def _valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip())
        return True
    except ValueError:
        return False


def _valid_tech(value: str) -> bool:
    """Техточка строго в формате 000-0000 (например, 001-0001)."""
    return bool(TECH_RE.match((value or "").strip()))


def _valid_fio(value: str) -> bool:
    return len(value.split()) >= 2 and len(value.strip()) >= 5


def _valid_phone(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    return 10 <= len(digits) <= 15


async def _edited(state: FSMContext) -> bool:
    """True, если поле правится из предпросмотра (надо вернуться к проверке)."""
    return bool((await state.get_data()).get("return_to_confirm"))


# ---------- регистрация и доступ ----------

def _profile_fields(u: dict | None) -> tuple[str, str, str, str, str, str]:
    u = u or {}
    return (
        ((u.get("fio") or "").strip()),
        ((u.get("phone") or "").strip()),
        ((u.get("clinic_short") or "").strip()),
        ((u.get("address") or "").strip()),
        ((u.get("position") or "").strip()),
        ((u.get("access") or "pending").strip() or "pending"),
    )


async def _gate(message: Message, state: FSMContext, pending: str) -> bool:
    """Проверка доступа. True — пользоваться пока нельзя (пользователю уже ответили)."""
    await upsert_user(
        message.from_user.id, message.from_user.full_name, message.from_user.username or ""
    )
    u = await get_user(message.from_user.id)
    fio, phone, clinic, address, position, access = _profile_fields(u)
    if not (fio and phone and clinic and address and position):
        await state.update_data(pending=pending)
        await _ask_missing(message, state, fio, phone, clinic, address, position, fresh=not fio)
        return True
    if access == "approved":
        return False
    if access == "pending":
        await message.answer(
            "⏳ Ваша заявка на доступ на рассмотрении. "
            "Вы получите уведомление, когда её одобрят.",
            reply_markup=main_menu(),
        )
        return True
    reason = ((u or {}).get("access_reason") or "без указания причины").strip()
    await message.answer(
        f"❌ В доступе отказано: {reason}.\n"
        "Проверьте данные в «👤 Мои данные» и отправьте запрос повторно.",
        reply_markup=resend_access_kb(),
    )
    return True


async def _ask_missing(message, state, fio, phone, clinic, address, position, fresh=False) -> None:
    """Переход к первому незаполненному полю регистрации."""
    if fresh:
        await message.answer(
            "👤 Для пользования ботом нужно зарегистрироваться.\n"
            "Заполните данные — после проверки доступ одобрит специалист.",
            reply_markup=cancel_kb(),
        )
    if not fio:
        await state.set_state(ProfileForm.waiting_fio)
        await message.answer(
            "1/5. Введите <b>ФИО</b> (например, Иванов Иван Иванович):",
            reply_markup=cancel_kb(),
        )
    elif not phone:
        await state.set_state(ProfileForm.waiting_phone)
        await message.answer(
            "2/5. Введите <b>телефон</b> кнопкой ниже или вручную:",
            reply_markup=phone_kb(),
        )
    elif not clinic:
        await state.set_state(ProfileForm.waiting_clinic)
        await message.answer(
            "3/5. Введите <b>номер поликлиники (сокращённо)</b> (например, ГП №5):",
            reply_markup=cancel_kb(),
        )
    elif not address:
        await state.set_state(ProfileForm.waiting_address)
        await message.answer(
            "4/5. Введите <b>адрес поликлиники</b>:",
            reply_markup=cancel_kb(),
        )
    else:
        await state.set_state(ProfileForm.waiting_position)
        await message.answer(
            "5/5. Введите вашу <b>должность</b> (например, медсестра):",
            reply_markup=cancel_kb(),
        )


@router.message(ProfileForm.waiting_fio)
async def p_fio(message: Message, state: FSMContext, bot: Bot) -> None:
    fio = (message.text or "").strip()
    if not _valid_fio(fio):
        await message.answer("⚠️ Введите ФИО полностью (например, Иванов Иван Иванович):")
        return
    await update_profile(message.from_user.id, fio=fio)
    await _next_reg_step(message, state, bot)


@router.message(ProfileForm.waiting_phone, F.contact)
async def p_phone_contact(message: Message, state: FSMContext, bot: Bot) -> None:
    await _save_phone(message, state, bot, message.contact.phone_number or "")


@router.message(ProfileForm.waiting_phone)
async def p_phone_text(message: Message, state: FSMContext, bot: Bot) -> None:
    phone = (message.text or "").strip()
    if not _valid_phone(phone):
        await message.answer("⚠️ Не похоже на номер телефона. Пример: +7 900 123-45-67")
        return
    await _save_phone(message, state, bot, phone)


async def _save_phone(message: Message, state: FSMContext, bot: Bot, phone: str) -> None:
    await update_profile(message.from_user.id, phone=phone.strip())
    await _next_reg_step(message, state, bot)


@router.message(ProfileForm.waiting_clinic)
async def p_clinic(message: Message, state: FSMContext, bot: Bot) -> None:
    clinic = (message.text or "").strip()
    if not (1 <= len(clinic) <= 30):
        await message.answer("⚠️ Введите номер поликлиники сокращённо (например, ГП №5):")
        return
    await update_profile(message.from_user.id, clinic_short=clinic)
    await _next_reg_step(message, state, bot)


@router.message(ProfileForm.waiting_address)
async def p_address(message: Message, state: FSMContext, bot: Bot) -> None:
    address = (message.text or "").strip()
    if len(address) < 3:
        await message.answer("⚠️ Введите адрес поликлиники:")
        return
    await update_profile(message.from_user.id, address=address)
    await _next_reg_step(message, state, bot)


@router.message(ProfileForm.waiting_position)
async def p_position(message: Message, state: FSMContext, bot: Bot) -> None:
    position = (message.text or "").strip()
    if len(position) < 2:
        await message.answer("⚠️ Введите вашу должность (например, медсестра):")
        return
    await update_profile(message.from_user.id, position=position)
    await _next_reg_step(message, state, bot)


async def _next_reg_step(message: Message, state: FSMContext, bot: Bot) -> None:
    """Следующий шаг регистрации либо финал (отправка на проверку / возврат к делу)."""
    u = await get_user(message.from_user.id)
    fio, phone, clinic, address, position, access = _profile_fields(u)
    if not (fio and phone and clinic and address and position):
        await _ask_missing(message, state, fio, phone, clinic, address, position)
        return
    if access == "approved":
        # Правка данных одобренным пользователем: вернуться к отложенному действию
        data = await state.get_data()
        pending = data.get("pending")
        await state.clear()
        if pending == "support":
            await start_support(message, state)
        elif pending == "cartridge":
            await start_cartridge(message, state)
        elif pending == "my":
            await my_tickets(message, state)
        else:
            await message.answer("✅ Данные обновлены.", reply_markup=main_menu())
        return
    if access == "denied":
        await state.clear()
        await message.answer(
            "✅ Данные обновлены. Чтобы получить доступ, отправьте запрос повторно:",
            reply_markup=resend_access_kb(),
        )
        return
    # pending
    if (u or {}).get("access_msg_id"):
        await state.clear()
        await message.answer(
            "✅ Данные обновлены. Ваша заявка уже на рассмотрении — ожидайте уведомления.",
            reply_markup=main_menu(),
        )
        return
    await _submit_for_review(message, message.from_user, state, bot)


async def _submit_for_review(message: Message, tg_user, state: FSMContext, bot: Bot) -> None:
    """Отправить заявку на доступ в чат специалистов."""
    tg_id = tg_user.id
    u = await get_user(tg_id)
    fio, phone, clinic, address, position, _ = _profile_fields(u)
    nick = f"@{tg_user.username}" if tg_user.username else "—"
    text = (
        "🆕 <b>Запрос доступа к боту</b>\n"
        f"👤 ФИО: {fio}\n"
        f"📞 Телефон: {phone}\n"
        f"🏥 Поликлиника: {clinic}\n"
        f"📍 Адрес: {address}\n"
        f"💼 Должность: {position}\n"
        f"✉️ Telegram: {nick} (<code>{tg_id}</code>)"
    )
    try:
        sent = await bot.send_message(
            settings.GROUP_CHAT_ID, text, reply_markup=access_review_kb(tg_id)
        )
    except Exception as e:  # noqa: BLE001
        log.error("Не удалось отправить запрос доступа %s в чат: %s", tg_id, e)
        await state.clear()
        await message.answer(
            "⚠️ Данные сохранены, но не удалось отправить заявку на проверку. "
            "Попробуйте позже.",
            reply_markup=main_menu(),
        )
        return
    await set_access(tg_id, "pending", msg_id=sent.message_id)
    await state.clear()
    await message.answer(
        "✅ Данные отправлены на проверку. Вы получите уведомление, "
        "когда специалист одобрит доступ.",
        reply_markup=main_menu(),
    )


@router.callback_query(F.data == "access_resend")
async def access_resend(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Повторная отправка запроса доступа после отклонения."""
    if callback.message.chat.type != "private":
        await callback.answer()
        return
    u = await get_user(callback.from_user.id)
    fio, phone, clinic, address, position, access = _profile_fields(u)
    if access != "denied":
        await callback.answer("Повторный запрос не требуется", show_alert=True)
        return
    if not (fio and phone and clinic and address and position):
        await callback.answer("Сначала заполните все данные в «👤 Мои данные»", show_alert=True)
        return
    await state.clear()
    await _submit_for_review(callback.message, callback.from_user, state, bot)
    await callback.answer()


ACCESS_STATUS_LABELS = {
    "pending": "⏳ На рассмотрении",
    "approved": "✅ Одобрен",
    "denied": "❌ Отклонён",
}


@router.message(F.text == BTN_PROFILE)
@router.message(Command("profile"))
async def show_profile(message: Message, state: FSMContext) -> None:
    await state.clear()
    await upsert_user(
        message.from_user.id, message.from_user.full_name, message.from_user.username or ""
    )
    u = await get_user(message.from_user.id)
    fio, phone, clinic, address, position, access = _profile_fields(u)
    status = ACCESS_STATUS_LABELS.get(access, access)
    text = (
        "👤 <b>Мои данные:</b>\n"
        f"ФИО: {fio or '—'}\n"
        f"Телефон: {phone or '—'}\n"
        f"Поликлиника: {clinic or '—'}\n"
        f"Адрес: {address or '—'}\n"
        f"Должность: {position or '—'}\n"
        f"Доступ: {status}"
    )
    if access == "denied":
        reason = ((u or {}).get("access_reason") or "").strip()
        if reason:
            text += f" ({reason})"
    await message.answer(text, reply_markup=profile_kb())
    if access == "denied":
        await message.answer(
            "Чтобы получить доступ, отправьте запрос повторно:",
            reply_markup=resend_access_kb(),
        )


@router.callback_query(F.data.startswith("prof:"))
async def prof_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message.chat.type != "private":
        await callback.answer()
        return
    what = callback.data.split(":", 1)[1]
    await state.clear()
    if what == "fio":
        await state.set_state(ProfileForm.waiting_fio)
        await callback.message.answer("✏️ Введите новое <b>ФИО</b>:", reply_markup=cancel_kb())
    elif what == "phone":
        await state.set_state(ProfileForm.waiting_phone)
        await callback.message.answer(
            "✏️ Введите новый <b>телефон</b> (кнопкой или вручную):",
            reply_markup=phone_kb(),
        )
    elif what == "clinic":
        await state.set_state(ProfileForm.waiting_clinic)
        await callback.message.answer(
            "✏️ Введите новый <b>номер поликлиники (сокращённо)</b>:", reply_markup=cancel_kb()
        )
    elif what == "address":
        await state.set_state(ProfileForm.waiting_address)
        await callback.message.answer(
            "✏️ Введите новый <b>адрес поликлиники</b>:", reply_markup=cancel_kb()
        )
    else:
        await state.set_state(ProfileForm.waiting_position)
        await callback.message.answer(
            "✏️ Введите новую <b>должность</b>:", reply_markup=cancel_kb()
        )
    await callback.answer()


# ---------- создание заявок ----------

async def _author_snapshot(tg_id: int, tg_user) -> tuple[str, str]:
    """ФИО/телефон инициатора из профиля (fallback — имя из Telegram)."""
    u = await get_user(tg_id)
    fio = ((u or {}).get("fio") or tg_user.full_name or "").strip()
    phone = ((u or {}).get("phone") or "").strip()
    return fio, phone


async def _finish_support(message: Message, state: FSMContext, bot: Bot) -> None:
    """Создание заявки в ТП + отправка в чат специалистов."""
    data = await state.get_data()
    photos: list[str] = data.get("photos", [])
    fio, phone = await _author_snapshot(message.from_user.id, message.from_user)
    ticket_id = await create_ticket(
        telegram_id=message.from_user.id,
        type="support",
        clinic_num=data.get("clinic_num", ""),
        address=data.get("address", ""),
        tech_point=data.get("tech_point", ""),
        cabinet=data.get("cabinet", ""),
        ip_address=data.get("ip_address", ""),
        description=data.get("description", ""),
        photos=photos,
        author_name=fio,
        author_phone=phone,
    )
    await state.clear()
    await message.answer(
        f"✅ Заявка <b>#{ticket_id}</b> отправлена специалистам! "
        "Статус можно посмотреть в «📁 Мои обращения».",
        reply_markup=main_menu(),
    )
    await _push_to_engineers(message, bot, ticket_id)


async def _push_to_engineers(message: Message, bot: Bot, ticket_id: int) -> None:
    """Карточка заявки -> рабочий чат специалистов + фото отдельными сообщениями."""
    t = await get_ticket(ticket_id)
    if not t:
        return
    header = "👶 <b>Новая заявка!</b>"
    try:
        sent = await bot.send_message(
            settings.GROUP_CHAT_ID,
            ticket_card(t, header=header),
            reply_markup=ticket_controls(ticket_id, "new"),
        )
    except Exception as e:  # noqa: BLE001
        log.error("Не удалось отправить заявку #%s в чат специалистов: %s", ticket_id, e)
        await message.answer(
            "⚠️ Заявка сохранена, но не удалось переслать её специалистам. "
            "Попробуйте позже или свяжитесь с ТП напрямую."
        )
        return
    await set_engineer_msg_id(ticket_id, sent.message_id)
    for fid in photos_of(t):
        try:
            await bot.send_photo(settings.GROUP_CHAT_ID, fid, reply_to_message_id=sent.message_id)
        except Exception as e:  # noqa: BLE001
            log.warning("Не удалось переслать фото заявки #%s: %s", ticket_id, e)
    # Локальная копия фото (best-effort, не блокирует)
    await save_photos_local(bot, photos_of(t), ticket_id)


# ---------- сценарий 1: заявка в ТП ----------

@router.message(F.text == BTN_NEW_TICKET)
async def start_support(message: Message, state: FSMContext) -> None:
    if await _gate(message, state, "support"):
        return
    await state.clear()
    await _ask_place(message, state, "support")


async def _ask_place(message: Message, state: FSMContext, kind: str) -> None:
    """Подтянуть поликлинику/адрес из профиля и уточнить, верны ли данные."""
    u = await get_user(message.from_user.id)
    _, _, clinic, address, _, _ = _profile_fields(u)
    await state.update_data(clinic_num=clinic, address=address)
    form_place = SupportForm.place if kind == "support" else CartridgeForm.place
    await state.set_state(form_place)
    await message.answer(
        f"🏥 Поликлиника: <b>{clinic}</b>\n📍 Адрес: <b>{address}</b>\n\nДанные верны?",
        reply_markup=place_kb(kind),
    )


@router.callback_query(F.data.startswith("place_"))
async def place_choice(callback: CallbackQuery, state: FSMContext) -> None:
    """place_ok:<kind> — данные из профиля верны; place_own:<kind> — ввести свои."""
    if callback.message.chat.type != "private":
        await callback.answer()
        return
    try:
        how, kind = callback.data.split(":")
    except ValueError:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    if kind == "support":
        if await state.get_state() != SupportForm.place.state:
            await callback.answer("Заявка уже отправлена или отменена", show_alert=True)
            return
        if how == "place_ok":
            await state.set_state(SupportForm.tech_point)
            await callback.message.answer(
                f"🖨 Введите <b>номер технологической точки</b> (пример: {TECH_EXAMPLE}):",
                reply_markup=cancel_kb(),
            )
        else:
            await state.set_state(SupportForm.clinic_num)
            await callback.message.answer(
                "🏥 Введите <b>свой номер поликлиники</b>:", reply_markup=cancel_kb()
            )
    else:
        if await state.get_state() != CartridgeForm.place.state:
            await callback.answer("Запрос уже отправлен или отменён", show_alert=True)
            return
        if how == "place_ok":
            await _ask_cart_scope(callback.message, state)
        else:
            await state.set_state(CartridgeForm.clinic_num)
            await callback.message.answer(
                "🏥 Введите <b>свой номер поликлиники</b>:", reply_markup=cancel_kb()
            )
    await callback.answer()


@router.message(SupportForm.clinic_num)
async def s_clinic(message: Message, state: FSMContext) -> None:
    await state.update_data(clinic_num=(message.text or "").strip())
    if await _edited(state):
        await _show_support_preview(message, state)
        return
    await state.set_state(SupportForm.address)
    await message.answer("📍 Введите <b>адрес поликлиники</b>:", reply_markup=cancel_kb())


@router.message(SupportForm.address)
async def s_address(message: Message, state: FSMContext) -> None:
    await state.update_data(address=(message.text or "").strip())
    if await _edited(state):
        await _show_support_preview(message, state)
        return
    await state.set_state(SupportForm.tech_point)
    await message.answer(
        f"🖨 Введите <b>номер технологической точки</b> (пример: {TECH_EXAMPLE}):",
        reply_markup=cancel_kb(),
    )


@router.message(SupportForm.tech_point)
async def s_tech(message: Message, state: FSMContext) -> None:
    tech = (message.text or "").strip()
    if not _valid_tech(tech):
        await message.answer(
            f"⚠️ Неверный формат. Номер технологической точки всегда выглядит так: {TECH_EXAMPLE}.\n"
            "Введите три цифры, дефис и четыре цифры:",
            reply_markup=cancel_kb(),
        )
        return
    await state.update_data(tech_point=tech)
    if await _edited(state):
        await _show_support_preview(message, state)
        return
    await state.set_state(SupportForm.cabinet)
    await message.answer("🚪 Введите <b>номер кабинета</b>:", reply_markup=cancel_kb())


@router.message(SupportForm.cabinet)
async def s_cabinet(message: Message, state: FSMContext) -> None:
    await state.update_data(cabinet=(message.text or "").strip())
    if await _edited(state):
        await _show_support_preview(message, state)
        return
    await state.set_state(SupportForm.ip_address)
    await message.answer(
        f"💻 Введите <b>IP-адрес АРМ</b> (пример: {IP_EXAMPLE}):", reply_markup=cancel_kb()
    )


@router.message(SupportForm.ip_address)
async def s_ip(message: Message, state: FSMContext) -> None:
    ip = (message.text or "").strip()
    if not _valid_ip(ip):
        await message.answer(
            f"⚠️ Это не похоже на IP-адрес. Попробуйте ещё раз (пример: {IP_EXAMPLE}):"
        )
        return
    await state.update_data(ip_address=ip)
    if await _edited(state):
        await _show_support_preview(message, state)
        return
    await state.set_state(SupportForm.description)
    await message.answer("📝 <b>Опишите проблему</b> подробно:", reply_markup=cancel_kb())


@router.message(SupportForm.description)
async def s_desc(message: Message, state: FSMContext) -> None:
    if not (message.text or "").strip():
        await message.answer("⚠️ Опишите проблему текстом:")
        return
    if await _edited(state):
        # При правке одного поля уже загруженные фото не трогаем
        await state.update_data(description=message.text.strip())
        await _show_support_preview(message, state)
        return
    await state.update_data(description=message.text.strip(), photos=[])
    await state.set_state(SupportForm.photos)
    await message.answer(
        f"📷 Пришлите <b>фото</b> (до {settings.MAX_PHOTOS}), по одному. "
        "Когда закончите — нажмите «⏭ Пропустить».",
        reply_markup=skip_cancel_kb(),
    )


@router.message(SupportForm.photos, F.photo)
async def s_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    photos: list[str] = data.get("photos", [])
    photos.append(message.photo[-1].file_id)  # самое большое разрешение
    await state.update_data(photos=photos)
    if len(photos) >= settings.MAX_PHOTOS:
        await _show_support_preview(message, state)
        return
    await message.answer(
        f"📎 Фото {len(photos)}/{settings.MAX_PHOTOS} принято. "
        "Пришлите ещё или нажмите «⏭ Пропустить».",
        reply_markup=skip_cancel_kb(),
    )


@router.message(SupportForm.photos, F.text == BTN_SKIP)
async def s_photos_skip(message: Message, state: FSMContext) -> None:
    await _show_support_preview(message, state)


@router.message(SupportForm.photos)
async def s_photos_wrong(message: Message, state: FSMContext) -> None:
    await message.answer("📷 Пришлите фото или нажмите «⏭ Пропустить».", reply_markup=skip_cancel_kb())


async def _show_support_preview(message: Message, state: FSMContext) -> None:
    await state.update_data(return_to_confirm=False)
    data = await state.get_data()
    preview = (
        "🔍 <b>Проверьте заявку:</b>\n"
        f"🏥 Поликлиника: {data.get('clinic_num')}\n"
        f"📍 Адрес: {data.get('address')}\n"
        f"🖨 Техточка: {data.get('tech_point')}\n"
        f"🚪 Кабинет: {data.get('cabinet')}\n"
        f"💻 IP: <code>{data.get('ip_address')}</code>\n"
        f"📝 Проблема: {data.get('description')}\n"
        f"📷 Фото: {len(data.get('photos', []))}"
    )
    await state.set_state(SupportForm.confirm)
    photos = data.get("photos", [])
    if photos:
        media = [InputMediaPhoto(media=fid) for fid in photos]
        await message.answer_media_group(media)
    await message.answer(preview, reply_markup=edit_fields_kb("support"))
    await message.answer(
        "Всё верно? Если нашли ошибку — нажмите ✏️ выше, иначе «✅ Отправить».",
        reply_markup=confirm_kb(),
    )


@router.message(SupportForm.confirm, F.text == BTN_CONFIRM)
async def s_confirm(message: Message, state: FSMContext, bot: Bot) -> None:
    await _finish_support(message, state, bot)


@router.message(SupportForm.confirm)
async def s_confirm_wrong(message: Message, state: FSMContext) -> None:
    await message.answer("Нажмите «✅ Отправить» или «❌ Отмена».", reply_markup=confirm_kb())


# ---------- правка полей из предпросмотра ----------

SUPPORT_PROMPTS = {
    "clinic_num": (SupportForm.clinic_num, "🏥 Введите новый <b>номер поликлиники</b>:"),
    "address": (SupportForm.address, "📍 Введите новый <b>адрес поликлиники</b>:"),
    "tech_point": (SupportForm.tech_point, f"🖨 Введите новый <b>номер технологической точки</b> (пример: {TECH_EXAMPLE}):"),
    "cabinet": (SupportForm.cabinet, "🚪 Введите новый <b>номер кабинета</b>:"),
    "ip_address": (SupportForm.ip_address, f"💻 Введите новый <b>IP-адрес АРМ</b> (пример: {IP_EXAMPLE}):"),
    "description": (SupportForm.description, "📝 Введите новое <b>описание проблемы</b>:"),
    "photos": (SupportForm.photos, ""),
}
@router.callback_query(F.data.startswith("editf:"))
async def edit_field(callback: CallbackQuery, state: FSMContext) -> None:
    """Переход к повторному вводу одного поля, затем возврат к проверке."""
    if callback.message.chat.type != "private":
        await callback.answer()
        return
    try:
        _, kind, field = callback.data.split(":")
    except ValueError:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    want = (SupportForm.confirm if kind == "support" else CartridgeForm.confirm).state
    if await state.get_state() != want:
        await callback.answer("Заявка уже отправлена или отменена", show_alert=True)
        return
    if kind == "cartridge":
        await _edit_cartridge_field(callback, state, field)
        return
    if field not in SUPPORT_PROMPTS:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    new_state, prompt = SUPPORT_PROMPTS[field]
    await state.update_data(return_to_confirm=True)
    await state.set_state(new_state)
    if field == "photos":
        await state.update_data(photos=[])
        await callback.message.answer(
            f"📷 Пришлите <b>фото</b> заново (до {settings.MAX_PHOTOS}), по одному, "
            "или нажмите «⏭ Пропустить».",
            reply_markup=skip_cancel_kb(),
        )
    else:
        await callback.message.answer(prompt, reply_markup=cancel_kb())
    await callback.answer()


async def _edit_cartridge_field(callback: CallbackQuery, state: FSMContext, field: str) -> None:
    """Правка поля картриджного запроса (набор полей зависит от scope)."""
    data = await state.get_data()
    scope = data.get("cart_scope") or "point"
    await state.update_data(return_to_confirm=True)
    msg = callback.message
    if field == "scope":
        await _ask_cart_scope(msg, state)
    elif field == "clinic_num":
        await state.set_state(CartridgeForm.clinic_num)
        await msg.answer("🏥 Введите <b>свой номер поликлиники</b>:", reply_markup=cancel_kb())
    elif field == "address":
        await state.set_state(CartridgeForm.address)
        await msg.answer("📍 Введите <b>свой адрес поликлиники</b>:", reply_markup=cancel_kb())
    elif field == "tech_point":
        await state.set_state(CartridgeForm.tech_point)
        await msg.answer(
            f"🖨 Введите <b>номер технологической точки</b> (пример: {TECH_EXAMPLE}):",
            reply_markup=cancel_kb(),
        )
    elif field == "cabinet":
        await state.set_state(CartridgeForm.cabinet)
        await msg.answer("🚪 Введите <b>номер кабинета</b>:", reply_markup=cancel_kb())
    elif field == "models":
        sel = data.get("cart_sel", [])
        await state.set_state(CartridgeForm.models)
        await msg.answer(
            "🧾 Выберите <b>один или несколько картриджей</b>, затем «Готово»:",
            reply_markup=cart_multi_kb(sel),
        )
    elif field == "comment":
        await state.set_state(CartridgeForm.comment)
        await msg.answer(
            "💬 Введите новый <b>комментарий</b> (или «⏭ Пропустить», чтобы оставить прежний):",
            reply_markup=skip_cancel_kb(),
        )
    else:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    await callback.answer()


# ---------- сценарий 2: картриджи ----------

@router.message(F.text == BTN_CARTRIDGE)
async def start_cartridge(message: Message, state: FSMContext) -> None:
    if await _gate(message, state, "cartridge"):
        return
    await state.clear()
    await _ask_place(message, state, "cartridge")


async def _ask_cart_scope(message: Message, state: FSMContext) -> None:
    await state.set_state(CartridgeForm.scope)
    await message.answer(
        "📦 Запрос на поставку — <b>на конкретный принтер</b> или <b>на всю поликлинику</b>?",
        reply_markup=cart_scope_kb(),
    )


@router.message(CartridgeForm.clinic_num)
async def c_clinic(message: Message, state: FSMContext) -> None:
    await state.update_data(clinic_num=(message.text or "").strip())
    if await _edited(state):
        await _show_cartridge_preview(message, state)
        return
    await state.set_state(CartridgeForm.address)
    await message.answer("📍 Введите <b>свой адрес поликлиники</b>:", reply_markup=cancel_kb())


@router.message(CartridgeForm.address)
async def c_address(message: Message, state: FSMContext) -> None:
    await state.update_data(address=(message.text or "").strip())
    if await _edited(state):
        await _show_cartridge_preview(message, state)
        return
    await _ask_cart_scope(message, state)


@router.callback_query(F.data.startswith("cartscope:"))
async def cart_scope(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message.chat.type != "private":
        await callback.answer()
        return
    if await state.get_state() != CartridgeForm.scope.state:
        await callback.answer("Запрос уже отправлен или отменён", show_alert=True)
        return
    scope = callback.data.split(":", 1)[1]
    if scope not in ("point", "clinic"):
        await callback.answer("Некорректные данные", show_alert=True)
        return
    await state.update_data(cart_scope=scope, tech_point="", cabinet="", cart_sel=[])
    if scope == "point":
        await state.set_state(CartridgeForm.tech_point)
        await callback.message.answer(
            f"🖨 Введите <b>номер технологической точки принтера</b> (пример: {TECH_EXAMPLE}).\n"
            "Количество для одной точки — всегда 1 картридж.",
            reply_markup=cancel_kb(),
        )
    else:
        await state.set_state(CartridgeForm.models)
        await callback.message.answer(
            "🧾 Выберите <b>один или несколько картриджей</b> из списка, затем «Готово»:",
            reply_markup=cart_multi_kb([]),
        )
    await callback.answer()


@router.message(CartridgeForm.tech_point)
async def c_tech(message: Message, state: FSMContext) -> None:
    tech = (message.text or "").strip()
    if not _valid_tech(tech):
        await message.answer(
            f"⚠️ Неверный формат. Номер технологической точки всегда выглядит так: {TECH_EXAMPLE}.",
            reply_markup=cancel_kb(),
        )
        return
    await state.update_data(tech_point=tech)
    if await _edited(state):
        await _show_cartridge_preview(message, state)
        return
    await state.set_state(CartridgeForm.cabinet)
    await message.answer("🚪 Введите <b>номер кабинета</b>:", reply_markup=cancel_kb())


@router.message(CartridgeForm.cabinet)
async def c_cabinet(message: Message, state: FSMContext) -> None:
    await state.update_data(cabinet=(message.text or "").strip())
    if await _edited(state):
        await _show_cartridge_preview(message, state)
        return
    await state.set_state(CartridgeForm.comment)
    await message.answer(
        "💬 <b>Дополнительный комментарий</b> (или «⏭ Пропустить»):", reply_markup=skip_cancel_kb()
    )


@router.callback_query(F.data.startswith("cartsel:"))
async def cart_multi(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message.chat.type != "private":
        await callback.answer()
        return
    if await state.get_state() != CartridgeForm.models.state:
        await callback.answer("Запрос уже отправлен или отменён", show_alert=True)
        return
    code = callback.data.split(":", 1)[1]
    data = await state.get_data()
    sel: list[str] = data.get("cart_sel", [])
    if code == "done":
        if not sel:
            await callback.answer("Выберите хотя бы один картридж", show_alert=True)
            return
        if any(c not in CARTRIDGE_LABELS for c in sel):
            await callback.answer("Некорректные данные", show_alert=True)
            return
        await state.set_state(CartridgeForm.comment)
        await callback.message.answer(
            "💬 <b>Дополнительный комментарий</b> (или «⏭ Пропустить»):",
            reply_markup=skip_cancel_kb(),
        )
        await callback.answer()
        return
    if code not in CARTRIDGE_LABELS:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    if code in sel:
        sel.remove(code)
    else:
        sel.append(code)
    await state.update_data(cart_sel=sel)
    try:
        await callback.message.edit_reply_markup(reply_markup=cart_multi_kb(sel))
    except Exception:  # noqa: BLE001 — не критично
        pass
    await callback.answer()


@router.message(CartridgeForm.comment, F.text == BTN_SKIP)
async def c_comment_skip(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("comment"):
        await state.update_data(comment="")
    await _show_cartridge_preview(message, state)


@router.message(CartridgeForm.comment)
async def c_comment(message: Message, state: FSMContext) -> None:
    await state.update_data(comment=(message.text or "").strip())
    await _show_cartridge_preview(message, state)


async def _show_cartridge_preview(message: Message, state: FSMContext) -> None:
    await state.update_data(return_to_confirm=False)
    data = await state.get_data()
    scope = data.get("cart_scope") or "point"
    scope_label = "🖨 На один принтер" if scope == "point" else "🏥 На всю поликлинику"
    lines = [
        "🔍 <b>Проверьте запрос:</b>",
        f"🏥 Поликлиника: {data.get('clinic_num')}",
        f"📍 Адрес: {data.get('address')}",
        f"📦 Поставка: {scope_label}",
    ]
    if scope == "point":
        lines += [
            f"🎯 Техточка: {data.get('tech_point')}",
            f"🚪 Кабинет: {data.get('cabinet')}",
            "🔢 Количество: 1 (автоматически)",
        ]
    else:
        lines.append("🧾 Модели:")
        for code in data.get("cart_sel", []):
            if code in CARTRIDGE_LABELS:
                lines.append(CARTRIDGE_LABELS[code])
    lines.append(f"💬 Комментарий: {data.get('comment') or '—'}")
    await state.set_state(CartridgeForm.confirm)
    await message.answer("\n".join(lines), reply_markup=edit_fields_kb("cartridge", scope))
    await message.answer(
        "Всё верно? Если нашли ошибку — нажмите ✏️ выше, иначе «✅ Отправить».",
        reply_markup=confirm_kb(),
    )


@router.message(CartridgeForm.confirm, F.text == BTN_CONFIRM)
async def c_confirm(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    scope = data.get("cart_scope") or "point"
    fio, phone = await _author_snapshot(message.from_user.id, message.from_user)
    if scope == "point":
        printer_model = ""
        quantity = "1"
        tech_point = data.get("tech_point", "")
        cabinet = data.get("cabinet", "")
    else:
        labels = [CARTRIDGE_LABELS[c] for c in data.get("cart_sel", []) if c in CARTRIDGE_LABELS]
        printer_model = "\n".join(labels)
        quantity = ""
        tech_point = ""
        cabinet = ""
    ticket_id = await create_ticket(
        telegram_id=message.from_user.id,
        type="cartridge",
        clinic_num=data.get("clinic_num", ""),
        address=data.get("address", ""),
        tech_point=tech_point,
        cabinet=cabinet,
        printer_model=printer_model,
        quantity=quantity,
        description=data.get("comment", ""),
        author_name=fio,
        author_phone=phone,
        scope=scope,
    )
    await state.clear()
    await message.answer(
        f"✅ Запрос на картриджи <b>#{ticket_id}</b> отправлен специалистам!",
        reply_markup=main_menu(),
    )
    await _push_to_engineers(message, bot, ticket_id)


@router.message(CartridgeForm.confirm)
async def c_confirm_wrong(message: Message) -> None:
    await message.answer("Нажмите «✅ Отправить» или «❌ Отмена».", reply_markup=confirm_kb())


# ---------- сценарий 3: мои обращения ----------

@router.message(F.text == BTN_MY_TICKETS)
@router.message(Command("my"))
async def my_tickets(message: Message, state: FSMContext) -> None:
    if await _gate(message, state, "my"):
        return
    tickets = await get_user_tickets(message.from_user.id)
    if not tickets:
        await message.answer("📭 У вас пока нет обращений.", reply_markup=main_menu())
        return
    lines = []
    for t in tickets[:20]:
        status = STATUS_LABELS.get(t["status"], t["status"])
        ttype = TYPE_LABELS.get(t["type"], t["type"])
        lines.append(f"#{t['id']} • {ttype} • {status} • {fmt_dt(t.get('created_at'))}")
    await message.answer(
        "📁 <b>Ваши обращения</b> (нажмите, чтобы открыть детали):\n\n" + "\n".join(lines),
        reply_markup=my_tickets_list(tickets[:20]),
    )


@router.callback_query(F.data.startswith("my:"))
async def my_ticket_detail(callback: CallbackQuery) -> None:
    try:
        ticket_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID", show_alert=True)
        return
    t = await get_ticket(ticket_id)
    if not t or t["telegram_id"] != callback.from_user.id:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if t["status"] in ("done", "rejected"):
        await callback.message.answer(
            ticket_card(t, show_responsible=False), reply_markup=reopen_kb(ticket_id)
        )
    else:
        await callback.message.answer(ticket_card(t, show_responsible=False))
    await callback.answer()


# ---------- возврат заявки в работу ----------

@router.callback_query(F.data.startswith("reopen:"))
async def reopen_start(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message.chat.type != "private":
        await callback.answer()
        return
    try:
        ticket_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректный ID", show_alert=True)
        return
    t = await get_ticket(ticket_id)
    if not t or t["telegram_id"] != callback.from_user.id:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if t["status"] not in ("done", "rejected"):
        await callback.answer("Возвращать можно только выполненные/отклонённые заявки", show_alert=True)
        return
    await state.update_data(reopen_id=ticket_id)
    await state.set_state(ReopenForm.waiting_comment)
    await callback.message.answer(
        f"↩️ Напишите комментарий к заявке <b>#{ticket_id}</b> — что осталось невыполненным:",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(ReopenForm.waiting_comment, F.chat.type == "private")
async def reopen_done(message: Message, state: FSMContext, bot: Bot) -> None:
    comment = (message.text or "").strip()
    if not comment:
        await message.answer("⚠️ Напишите комментарий текстом:")
        return
    data = await state.get_data()
    ticket_id = data.get("reopen_id")
    t = await get_ticket(ticket_id) if ticket_id else None
    if not t or t["telegram_id"] != message.from_user.id:
        await state.clear()
        await message.answer("⚠️ Заявка не найдена.", reply_markup=main_menu())
        return
    await set_ticket_reopened(ticket_id, comment)
    await state.clear()
    t = await get_ticket(ticket_id)

    # Обновляем карточку в чате специалистов и постим комментарий в её ветку
    card_id = t.get("engineer_msg_id") or 0
    try:
        if card_id:
            await bot.edit_message_text(
                ticket_card(t),
                chat_id=settings.GROUP_CHAT_ID,
                message_id=card_id,
                reply_markup=ticket_controls(ticket_id, "in_progress"),
            )
            await bot.send_message(
                settings.GROUP_CHAT_ID,
                f"↩️ <b>Пользователь вернул заявку #{ticket_id} в работу</b>:\n{comment}",
                reply_to_message_id=card_id,
            )
        else:
            await bot.send_message(
                settings.GROUP_CHAT_ID,
                ticket_card(t, header="↩️ <b>Заявка возвращена в работу!</b>")
                + f"\n\n💬 Комментарий: {comment}",
                reply_markup=ticket_controls(ticket_id, "in_progress"),
            )
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось обновить чат специалистов по возврату #%s: %s", ticket_id, e)
    await message.answer(
        f"↩️ Заявка <b>#{ticket_id}</b> снова в работе. Специалисты уведомлены.",
        reply_markup=main_menu(),
    )


# ---------- ответы пользователя специалистам (ветка переписки) ----------

@router.message(
    StateFilter(None),
    F.chat.type == "private",
    F.reply_to_message,
    ~F.from_user.is_bot,
)
async def user_thread_reply(message: Message, bot: Bot) -> None:
    """Ответ пользователя на сообщение специалистов: уходит в ветку заявки в чате."""
    link = await get_link_by_user_msg(message.reply_to_message.message_id)
    if not link:
        return  # ответ не на сообщение специалистов — игнорируем
    t = await get_ticket(link["ticket_id"])
    if not t:
        return
    text = (message.text or message.caption or "").strip()
    u = await get_user(message.from_user.id)
    fio = ((u or {}).get("fio") or "").strip()
    clinic = ((u or {}).get("clinic_short") or "").strip()
    if fio:
        who = f"{fio} ({clinic})" if clinic else fio
    elif message.from_user.username:
        who = f"@{message.from_user.username}"
    else:
        who = message.from_user.full_name
    card_id = t.get("engineer_msg_id") or 0
    kwargs = {"reply_to_message_id": card_id} if card_id else {}
    try:
        if message.photo:
            body = f"💬 <b>Фото от {who} по заявке #{t['id']}</b>"
            if text:
                body += f":\n{text}"
            await bot.send_photo(settings.GROUP_CHAT_ID, message.photo[-1].file_id, caption=body, **kwargs)
        elif text:
            await bot.send_message(
                settings.GROUP_CHAT_ID,
                f"💬 <b>Ответ {who} по заявке #{t['id']}</b>:\n{text}",
                **kwargs,
            )
        else:
            return
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось доставить ответ пользователя по заявке #%s: %s", t["id"], e)
        await message.answer("⚠️ Не удалось доставить сообщение специалистам. Попробуйте позже.")


# ---------- fallback ----------

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🏠 Главное меню:", reply_markup=main_menu())
