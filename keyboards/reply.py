"""Reply-клавиатуры (главное меню, служебные кнопки)."""
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_NEW_TICKET = "📝 Создать заявку в ТП"
BTN_CARTRIDGE = "🖨 Запросить картридж"
BTN_MY_TICKETS = "📁 Мои обращения"
BTN_PROFILE = "👤 Мои данные"
BTN_CANCEL = "❌ Отмена"
BTN_SKIP = "⏭ Пропустить"
BTN_CONFIRM = "✅ Отправить"
BTN_BACK_MENU = "🏠 В меню"
BTN_SEND_PHONE = "📱 Отправить номер"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_NEW_TICKET)],
            [KeyboardButton(text=BTN_CARTRIDGE)],
            [KeyboardButton(text=BTN_MY_TICKETS)],
            [KeyboardButton(text=BTN_PROFILE)],
        ],
        resize_keyboard=True,
    )


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
    )


def skip_cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SKIP)],
            [KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
    )


def confirm_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CONFIRM)],
            [KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
    )


def phone_kb() -> ReplyKeyboardMarkup:
    """Кнопка отправки контакта + отмена (для ввода телефона)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SEND_PHONE, request_contact=True)],
            [KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
    )
