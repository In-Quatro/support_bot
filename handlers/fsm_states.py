"""FSM-состояния опросов."""
from aiogram.fsm.state import State, StatesGroup


class SupportForm(StatesGroup):
    clinic_num = State()  # свои данные (если не подтвердил из профиля)
    address = State()
    place = State()  # подтверждение поликлиники/адреса из профиля
    tech_point = State()
    cabinet = State()
    ip_address = State()
    description = State()
    photos = State()
    confirm = State()


class CartridgeForm(StatesGroup):
    clinic_num = State()  # свои данные (если не подтвердил из профиля)
    address = State()
    place = State()  # подтверждение поликлиники/адреса из профиля
    scope = State()  # point (один принтер) | clinic (вся поликлиника)
    tech_point = State()  # для scope=point
    cabinet = State()  # для scope=point
    models = State()  # множественный выбор моделей (scope=clinic)
    comment = State()
    confirm = State()


class RejectForm(StatesGroup):
    """Ввод причины отклонения (в чате специалистов)."""
    waiting_reason = State()


class ProfileForm(StatesGroup):
    """Регистрация: ФИО, телефон, поликлиника, адрес, должность."""
    waiting_fio = State()
    waiting_phone = State()
    waiting_clinic = State()
    waiting_address = State()
    waiting_position = State()


class AccessForm(StatesGroup):
    """Отклонение запроса доступа: ввод причины (в чате специалистов)."""
    waiting_reason = State()


class AdminForm(StatesGroup):
    """Ввод нового значения поля пользователя админом + поиск."""
    waiting_value = State()
    waiting_search = State()


class WriteForm(StatesGroup):
    """Сообщение пользователю через кнопку «Написать» (в чате специалистов)."""
    waiting_text = State()


class ReopenForm(StatesGroup):
    """Возврат заявки в работу пользователем: комментарий."""
    waiting_comment = State()
