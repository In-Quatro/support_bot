"""Общие хелперы: карточки заявок, уведомления, сохранение фото."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import Message

from config import settings
from database import STATUS_LABELS, TYPE_LABELS

log = logging.getLogger(__name__)


def fmt_dt(iso: str | None) -> str:
    """2026-09-28T08:39:41+00:00 -> 28.09.2026 08:39."""
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return iso[:16].replace("T", " ")


def fmt_date(iso: str | None) -> str:
    """Короткая дата для кнопок: 28.09.2026."""
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y")
    except ValueError:
        return (iso or "")[:10]


def ticket_card(t: dict, header: str = "", show_responsible: bool = True) -> str:
    """Структурированный текст заявки для чата специалистов / пользователя."""
    status = STATUS_LABELS.get(t.get("status", "new"), t.get("status", ""))
    ttype = TYPE_LABELS.get(t.get("type", "support"), t.get("type", ""))
    lines = [header] if header else []
    lines += [
        f"🧾 <b>Заявка #{t['id']}</b> • {ttype}",
        f"📌 Статус: <b>{status}</b>",
        f"👤 Инициатор: {t.get('author_name') or '—'} | 📞 {t.get('author_phone') or '—'}",
        f"🏥 Поликлиника №: {t.get('clinic_num') or '—'}",
        f"📍 Адрес: {t.get('address') or '—'}",
    ]
    if t.get("type") == "support":
        lines += [
            f"🖨 Техточка принтера: {t.get('tech_point') or '—'}",
            f"🚪 Кабинет: {t.get('cabinet') or '—'}",
            f"💻 IP АРМ: <code>{t.get('ip_address') or '—'}</code>",
            f"📝 Проблема: {t.get('description') or '—'}",
        ]
    else:
        scope = t.get("scope") or ""
        if scope == "point":
            lines += [
                "📦 Поставка: 🖨 На один принтер",
                f"🎯 Техточка: {t.get('tech_point') or '—'}",
                f"🚪 Кабинет: {t.get('cabinet') or '—'}",
                "🔢 Количество: 1",
            ]
        elif scope == "clinic":
            lines.append("📦 Поставка: 🏥 На всю поликлинику")
            lines.append("🧾 Модели:")
            models = [m for m in (t.get("printer_model") or "").splitlines() if m.strip()]
            lines += models if models else ["—"]
        else:  # старые заявки
            lines += [
                f"📦 Поставка: {scope or '—'}",
                f"🚪 Кабинет: {t.get('cabinet') or '—'}",
                f"🧾 Модели: {t.get('printer_model') or '—'}",
                f"🔢 Количество: {t.get('quantity') or '—'}",
            ]
        lines.append(f"💬 Комментарий: {t.get('description') or '—'}")
    if show_responsible and t.get("engineer_username"):
        lines.append(f"👷 Ответственный: @{t['engineer_username']}")
    if t.get("status") == "rejected" and t.get("reject_reason"):
        lines.append(f"⛔ Причина отклонения: {t['reject_reason']}")
    if t.get("reopen_comment"):
        lines.append(f"↩️ Комментарий пользователя: {t['reopen_comment']}")
    lines.append(f"🕒 Создана: {fmt_dt(t.get('created_at'))}")
    return "\n".join(lines)


def photos_of(t: dict) -> list[str]:
    try:
        data = json.loads(t.get("photos_json") or "[]")
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


async def send_user(bot: Bot, telegram_id: int, text: str):
    """Отправить сообщение пользователю. Вернёт Message или None (бот заблокирован)."""
    try:
        return await bot.send_message(telegram_id, text)
    except TelegramForbiddenError:
        log.warning("Пользователь %s заблокировал бота", telegram_id)
        return None
    except TelegramBadRequest as e:
        log.warning("Не удалось уведомить %s: %s", telegram_id, e)
        return None


async def notify_user(bot: Bot, telegram_id: int, text: str) -> bool:
    """Отправить уведомление пользователю. False — если бот заблокирован."""
    return await send_user(bot, telegram_id, text) is not None


async def save_photos_local(bot: Bot, file_ids: list[str], ticket_id: int) -> None:
    """Best-effort: скачать фото в ./data/photos/ticket_<id>/. Ошибки только логируются."""
    if not file_ids:
        return
    dest = Path(settings.PHOTOS_DIR) / f"ticket_{ticket_id}"
    dest.mkdir(parents=True, exist_ok=True)
    for i, fid in enumerate(file_ids):
        try:
            f = await bot.get_file(fid)
            if not f.file_path:
                continue
            ext = Path(f.file_path).suffix or ".jpg"
            await bot.download_file(f.file_path, dest / f"photo_{i + 1}{ext}")
        except Exception as e:  # noqa: BLE001 — фото не должны ронять заявку
            log.warning("Не удалось сохранить фото %s заявки #%s: %s", i, ticket_id, e)
