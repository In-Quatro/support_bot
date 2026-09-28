"""Inline-клавиатуры: управление заявкой + пагинация «Моих обращений»."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database import STATUS_LABELS


def ticket_controls(ticket_id: int, status: str = "new") -> InlineKeyboardMarkup:
    """Кнопки для специалистов в рабочем чате."""
    write = InlineKeyboardButton(text="✉️ Написать пользователю", callback_data=f"write:{ticket_id}")
    if status == "new":
        buttons = [
            [
                InlineKeyboardButton(text="🛠 В работу", callback_data=f"take:{ticket_id}"),
                InlineKeyboardButton(text="✅ Выполнено", callback_data=f"done:{ticket_id}"),
            ],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{ticket_id}")],
            [write],
        ]
    elif status == "in_progress":
        buttons = [
            [
                InlineKeyboardButton(text="✅ Выполнено", callback_data=f"done:{ticket_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{ticket_id}"),
            ],
            [InlineKeyboardButton(text="🔄 Взять на себя", callback_data=f"retake:{ticket_id}")],
            [write],
        ]
    else:  # done / rejected — только связь с пользователем
        buttons = [[write]]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def my_tickets_list(tickets: list[dict]) -> InlineKeyboardMarkup:
    """Список обращений пользователя: кнопка на каждый тикет."""
    from handlers.common import fmt_date  # локальный импорт: без цикла

    rows = []
    for t in tickets:
        status = STATUS_LABELS.get(t.get("status", ""), t.get("status", ""))
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"#{t['id']} • {fmt_date(t.get('created_at'))} • {status}",
                    callback_data=f"my:{t['id']}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Поля, доступные для правки после предпросмотра: callback "editf:<kind>:<field>"
SUPPORT_EDIT_FIELDS = [
    ("clinic_num", "🏥 Поликлиника"),
    ("address", "📍 Адрес"),
    ("tech_point", "🖨 Техточка"),
    ("cabinet", "🚪 Кабинет"),
    ("ip_address", "💻 IP"),
    ("description", "📝 Проблема"),
    ("photos", "📷 Фото"),
]

CARTRIDGE_POINT_EDIT_FIELDS = [
    ("scope", "📦 Куда (точка/поликлиника)"),
    ("clinic_num", "🏥 Поликлиника"),
    ("address", "📍 Адрес"),
    ("tech_point", "🖨 Техточка"),
    ("cabinet", "🚪 Кабинет"),
    ("comment", "💬 Комментарий"),
]

CARTRIDGE_CLINIC_EDIT_FIELDS = [
    ("scope", "📦 Куда (точка/поликлиника)"),
    ("clinic_num", "🏥 Поликлиника"),
    ("address", "📍 Адрес"),
    ("models", "🧾 Модели картриджей"),
    ("comment", "💬 Комментарий"),
]


def edit_fields_kb(kind: str, scope: str = "") -> InlineKeyboardMarkup:
    """Кнопки «исправить поле» под предпросмотром заявки."""
    if kind == "support":
        fields = SUPPORT_EDIT_FIELDS
    else:
        fields = CARTRIDGE_CLINIC_EDIT_FIELDS if scope == "clinic" else CARTRIDGE_POINT_EDIT_FIELDS
    rows = [
        [InlineKeyboardButton(text=f"✏️ {label}", callback_data=f"editf:{kind}:{field}")]
        for field, label in fields
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reopen_kb(ticket_id: int) -> InlineKeyboardMarkup:
    """Кнопка возврата заявки в работу (для выполненных/отклонённых)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Вернуть в работу", callback_data=f"reopen:{ticket_id}")]
        ]
    )


def profile_kb() -> InlineKeyboardMarkup:
    """Кнопки правки профиля."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить ФИО", callback_data="prof:fio")],
            [InlineKeyboardButton(text="✏️ Изменить телефон", callback_data="prof:phone")],
            [InlineKeyboardButton(text="✏️ Изменить поликлинику", callback_data="prof:clinic")],
            [InlineKeyboardButton(text="✏️ Изменить адрес", callback_data="prof:address")],
            [InlineKeyboardButton(text="✏️ Изменить должность", callback_data="prof:position")],
        ]
    )


def resend_access_kb() -> InlineKeyboardMarkup:
    """Повторная отправка запроса доступа после отклонения."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📨 Запросить доступ повторно", callback_data="access_resend")]
        ]
    )


def access_review_kb(tg_id: int) -> InlineKeyboardMarkup:
    """Кнопки одобрения/отклонения доступа (чат специалистов)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Одобрить", callback_data=f"access_ok:{tg_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"access_no:{tg_id}"),
            ]
        ]
    )


def place_kb(kind: str) -> InlineKeyboardMarkup:
    """Подтверждение поликлиники/адреса из профиля: верно или ввести свои."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Верно", callback_data=f"place_ok:{kind}")],
            [InlineKeyboardButton(text="✏️ Ввести свои", callback_data=f"place_own:{kind}")],
        ]
    )


def cart_scope_kb() -> InlineKeyboardMarkup:
    """Выбор: картридж на одну точку или на всю поликлинику."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🖨 На один принтер (точка)", callback_data="cartscope:point")],
            [InlineKeyboardButton(text="🏥 На всю поликлинику", callback_data="cartscope:clinic")],
        ]
    )


# Каталог картриджей: код -> подпись
CARTRIDGES: list[tuple[str, str]] = [
    ("cf280", "CF280 (HP M401/M425)"),
    ("cf226", "CF226 (HP M426)"),
    ("cf287", "CF287 (HP M501)"),
    ("cf259", "CF259 (HP M404)"),
    ("dl5120", "DL-5120 (PANTUM)"),
    ("tl5120", "TL-5120 (PANTUM)"),
]

CARTRIDGE_LABELS = {code: label for code, label in CARTRIDGES}


def cart_multi_kb(selected: list[str]) -> InlineKeyboardMarkup:
    """Множественный выбор моделей (запрос на поликлинику). Callback cartsel:<code>/done."""
    rows = []
    for code, label in CARTRIDGES:
        mark = "✅ " if code in selected else ""
        rows.append([InlineKeyboardButton(text=f"{mark}{label}", callback_data=f"cartsel:{code}")])
    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data="cartsel:done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
