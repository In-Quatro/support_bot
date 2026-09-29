"""Асинхронный слой SQLite (aiosqlite)."""
from __future__ import annotations

import aiosqlite
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config import settings

_db: Optional[aiosqlite.Connection] = None

# Человекочитаемые подписи
STATUS_LABELS = {
    "new": "🆕 Новая",
    "in_progress": "🛠 В работе",
    "done": "✅ Выполнена",
    "rejected": "❌ Отклонена",
}

TYPE_LABELS = {
    "support": "📝 Заявка в ТП",
    "cartridge": "🖨 Картриджи",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    full_name TEXT,
    username TEXT,
    fio TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    clinic_short TEXT DEFAULT '',
    address TEXT DEFAULT '',
    position TEXT DEFAULT '',
    access TEXT DEFAULT 'pending',
    access_reason TEXT DEFAULT '',
    access_by TEXT DEFAULT '',
    access_msg_id INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    telegram_id INTEGER NOT NULL,
    type TEXT NOT NULL DEFAULT 'support',
    clinic_num TEXT,
    address TEXT,
    tech_point TEXT,
    cabinet TEXT,
    ip_address TEXT,
    printer_model TEXT,
    quantity TEXT,
    description TEXT,
    photos_json TEXT DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'new',
    scope TEXT DEFAULT '',
    reject_reason TEXT DEFAULT '',
    engineer_username TEXT DEFAULT '',
    engineer_msg_id INTEGER DEFAULT 0,
    author_name TEXT DEFAULT '',
    author_phone TEXT DEFAULT '',
    reopen_comment TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS msg_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    engineer_msg_id INTEGER DEFAULT 0,
    user_msg_id INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tickets_telegram_id ON tickets(telegram_id);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def get_db() -> aiosqlite.Connection:
    """Ленивое singleton-соединение."""
    global _db
    if _db is None:
        Path(settings.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(settings.PHOTOS_DIR).mkdir(parents=True, exist_ok=True)
        _db = await aiosqlite.connect(settings.DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL;")
        await _db.execute("PRAGMA foreign_keys=ON;")
    return _db


async def _ensure_columns(table: str, wanted: dict[str, str]) -> None:
    db = await get_db()
    cols = {row["name"] async for row in (await db.execute(f"PRAGMA table_info({table})"))}
    for col, ddl in wanted.items():
        if col not in cols:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
    await db.commit()


async def init_db() -> None:
    db = await get_db()
    await db.executescript(SCHEMA)
    await db.commit()
    # Мягкая миграция, если БД создана старой версией схемы
    await _ensure_columns("users", {
        "fio": "fio TEXT DEFAULT ''",
        "phone": "phone TEXT DEFAULT ''",
        "clinic_short": "clinic_short TEXT DEFAULT ''",
        "address": "address TEXT DEFAULT ''",
        "position": "position TEXT DEFAULT ''",
        "access": "access TEXT DEFAULT 'pending'",
        "access_reason": "access_reason TEXT DEFAULT ''",
        "access_by": "access_by TEXT DEFAULT ''",
        "access_msg_id": "access_msg_id INTEGER DEFAULT 0",
    })
    # Уже пользовавшиеся ботом (есть заявки) — считаем одобренными.
    # По тикетам, а не по ФИО: иначе рестарт бота автоодобрит новых ожидающих модерации.
    await db.execute(
        """UPDATE users SET access = 'approved'
           WHERE access = 'pending'
             AND EXISTS (SELECT 1 FROM tickets WHERE tickets.telegram_id = users.telegram_id)"""
    )
    await db.commit()
    await _ensure_columns("tickets", {
        "printer_model": "printer_model TEXT",
        "quantity": "quantity TEXT",
        "reject_reason": "reject_reason TEXT DEFAULT ''",
        "engineer_username": "engineer_username TEXT DEFAULT ''",
        "telegram_id": "telegram_id INTEGER NOT NULL DEFAULT 0",
        "engineer_msg_id": "engineer_msg_id INTEGER DEFAULT 0",
        "author_name": "author_name TEXT DEFAULT ''",
        "author_phone": "author_phone TEXT DEFAULT ''",
        "reopen_comment": "reopen_comment TEXT DEFAULT ''",
        "scope": "scope TEXT DEFAULT ''",
    })
    # Индекс отдельно: на старых БД колонки появляются только после миграции выше
    await db.execute("CREATE INDEX IF NOT EXISTS idx_tickets_engineer_msg ON tickets(engineer_msg_id)")
    await db.commit()


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


async def upsert_user(telegram_id: int, full_name: str = "", username: str = "") -> int:
    """Создать/обновить пользователя, вернуть внутренний id."""
    db = await get_db()
    await db.execute(
        """
        INSERT INTO users (telegram_id, full_name, username)
        VALUES (?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET full_name=excluded.full_name, username=excluded.username
        """,
        (telegram_id, full_name, username or ""),
    )
    await db.commit()
    cur = await db.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
    row = await cur.fetchone()
    return int(row["id"])


async def get_user(telegram_id: int) -> Optional[dict[str, Any]]:
    """Профиль пользователя или None."""
    db = await get_db()
    cur = await db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def get_all_users(limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
    """Пользователи постранично (для админ-панели), от новых к старым."""
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM users ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
    )
    return [dict(r) async for r in cur]


async def count_users() -> int:
    db = await get_db()
    cur = await db.execute("SELECT COUNT(*) AS c FROM users")
    row = await cur.fetchone()
    return int(row["c"])


async def search_users(query: str, limit: int = 50) -> list[dict[str, Any]]:
    """Поиск по ФИО, телефону, поликлинике, адресу, должности, username, ID."""
    db = await get_db()
    q = f"%{query.strip()}%"
    cur = await db.execute(
        """SELECT * FROM users
           WHERE fio LIKE ? OR phone LIKE ? OR clinic_short LIKE ? OR address LIKE ?
              OR position LIKE ? OR username LIKE ? OR CAST(telegram_id AS TEXT) LIKE ?
           ORDER BY id DESC LIMIT ?""",
        (q, q, q, q, q, q, q, limit),
    )
    return [dict(r) async for r in cur]


async def update_profile(
    telegram_id: int,
    fio: str | None = None,
    phone: str | None = None,
    clinic_short: str | None = None,
    address: str | None = None,
    position: str | None = None,
) -> None:
    """Обновить данные профиля (строка пользователя создаётся при необходимости)."""
    db = await get_db()
    await db.execute("INSERT OR IGNORE INTO users (telegram_id) VALUES (?)", (telegram_id,))
    sets, args = [], []
    for col, val in (
        ("fio", fio),
        ("phone", phone),
        ("clinic_short", clinic_short),
        ("address", address),
        ("position", position),
    ):
        if val is not None:
            sets.append(f"{col} = ?")
            args.append(val)
    if sets:
        args.append(telegram_id)
        await db.execute(f"UPDATE users SET {', '.join(sets)} WHERE telegram_id = ?", args)
        await db.commit()


async def set_access(
    telegram_id: int,
    access: str,
    reason: str = "",
    by: str = "",
    msg_id: int | None = None,
) -> None:
    """Сменить статус доступа: pending / approved / denied."""
    db = await get_db()
    await db.execute("INSERT OR IGNORE INTO users (telegram_id) VALUES (?)", (telegram_id,))
    if msg_id is None:
        await db.execute(
            "UPDATE users SET access = ?, access_reason = ?, access_by = ? WHERE telegram_id = ?",
            (access, reason, by, telegram_id),
        )
    else:
        await db.execute(
            "UPDATE users SET access = ?, access_reason = ?, access_by = ?, access_msg_id = ? WHERE telegram_id = ?",
            (access, reason, by, msg_id, telegram_id),
        )
    await db.commit()


async def create_ticket(
    telegram_id: int,
    type: str,
    clinic_num: str = "",
    address: str = "",
    tech_point: str = "",
    cabinet: str = "",
    ip_address: str = "",
    printer_model: str = "",
    quantity: str = "",
    description: str = "",
    photos: list[str] | None = None,
    author_name: str = "",
    author_phone: str = "",
    scope: str = "",
) -> int:
    db = await get_db()
    user_id = await upsert_user(telegram_id)
    now = _now_iso()
    cur = await db.execute(
        """
        INSERT INTO tickets
            (user_id, telegram_id, type, clinic_num, address, tech_point, cabinet,
             ip_address, printer_model, quantity, description, photos_json, status,
             author_name, author_phone, scope, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            telegram_id,
            type,
            clinic_num,
            address,
            tech_point,
            cabinet,
            ip_address,
            printer_model,
            quantity,
            description,
            json.dumps(photos or [], ensure_ascii=False),
            author_name,
            author_phone,
            scope,
            now,
            now,
        ),
    )
    await db.commit()
    return int(cur.lastrowid)


async def get_ticket(ticket_id: int) -> Optional[dict[str, Any]]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def get_user_tickets(telegram_id: int, limit: int = 20) -> list[dict[str, Any]]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM tickets WHERE telegram_id = ? ORDER BY id DESC LIMIT ?",
        (telegram_id, limit),
    )
    return [dict(r) async for r in cur]


async def list_tickets(
    status: str = "", query: str = "", limit: int = 50, offset: int = 0
) -> list[dict[str, Any]]:
    """Все заявки с фильтром по статусу и поиском (для веб-админки)."""
    db = await get_db()
    where: list[str] = []
    args: list[Any] = []
    if status:
        where.append("status = ?")
        args.append(status)
    if query.strip():
        q = f"%{query.strip()}%"
        where.append(
            "(CAST(id AS TEXT) LIKE ? OR clinic_num LIKE ? OR address LIKE ? "
            "OR description LIKE ? OR author_name LIKE ? OR printer_model LIKE ?)"
        )
        args += [q] * 6
    sql = "SELECT * FROM tickets"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    cur = await db.execute(sql, (*args, limit, offset))
    return [dict(r) async for r in cur]


async def count_tickets(status: str = "", query: str = "") -> int:
    db = await get_db()
    where: list[str] = []
    args: list[Any] = []
    if status:
        where.append("status = ?")
        args.append(status)
    if query.strip():
        q = f"%{query.strip()}%"
        where.append(
            "(CAST(id AS TEXT) LIKE ? OR clinic_num LIKE ? OR address LIKE ? "
            "OR description LIKE ? OR author_name LIKE ? OR printer_model LIKE ?)"
        )
        args += [q] * 6
    sql = "SELECT COUNT(*) AS c FROM tickets"
    if where:
        sql += " WHERE " + " AND ".join(where)
    cur = await db.execute(sql, args)
    row = await cur.fetchone()
    return int(row["c"])


async def update_ticket_status(
    ticket_id: int,
    status: str,
    engineer_username: str = "",
    reject_reason: str = "",
) -> None:
    db = await get_db()
    await db.execute(
        """
        UPDATE tickets
        SET status = ?, engineer_username = ?, reject_reason = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, engineer_username, reject_reason, _now_iso(), ticket_id),
    )
    await db.commit()


async def set_engineer_msg_id(ticket_id: int, message_id: int) -> None:
    """Запомнить id карточки заявки в чате специалистов (для веток переписки и правок)."""
    db = await get_db()
    await db.execute(
        "UPDATE tickets SET engineer_msg_id = ?, updated_at = ? WHERE id = ?",
        (message_id, _now_iso(), ticket_id),
    )
    await db.commit()


async def get_ticket_by_engineer_msg(message_id: int) -> Optional[dict[str, Any]]:
    """Найти заявку по id её карточки в чате специалистов."""
    db = await get_db()
    cur = await db.execute("SELECT * FROM tickets WHERE engineer_msg_id = ?", (message_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def set_ticket_reopened(ticket_id: int, comment: str) -> None:
    """Вернуть заявку в работу с комментарием пользователя."""
    db = await get_db()
    await db.execute(
        "UPDATE tickets SET status = 'in_progress', reopen_comment = ?, updated_at = ? WHERE id = ?",
        (comment, _now_iso(), ticket_id),
    )
    await db.commit()


async def add_msg_link(ticket_id: int, engineer_msg_id: int, user_msg_id: int) -> None:
    """Связать сообщение бота у пользователя с заявкой (для ответов пользователя)."""
    db = await get_db()
    await db.execute(
        "INSERT INTO msg_links (ticket_id, engineer_msg_id, user_msg_id, created_at) VALUES (?, ?, ?, ?)",
        (ticket_id, engineer_msg_id, user_msg_id, _now_iso()),
    )
    await db.commit()


async def get_link_by_user_msg(user_msg_id: int) -> Optional[dict[str, Any]]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM msg_links WHERE user_msg_id = ? ORDER BY id DESC LIMIT 1", (user_msg_id,)
    )
    row = await cur.fetchone()
    return dict(row) if row else None
