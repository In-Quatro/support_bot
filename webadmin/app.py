"""Веб-админка на aiohttp (без новых зависимостей): пользователи, заявки, доступ.

Запуск — вместе с ботом, если в .env заданы WEB_ENABLED=true и WEB_PASSWORD.
Вход — по паролю, сессия в HttpOnly-cookie. Не выставлять в интернет без HTTPS!
"""
from __future__ import annotations

import hmac
import html
import logging
import secrets

from aiohttp import web

from config import settings
from database import (
    STATUS_LABELS,
    TYPE_LABELS,
    count_tickets,
    count_users,
    get_all_users,
    get_ticket,
    get_user,
    get_user_tickets,
    list_tickets,
    search_users,
    set_access,
    set_engineer_msg_id,
    update_profile,
    update_ticket_status,
)
from handlers.common import fmt_dt, notify_user, photos_of, ticket_card
from keyboards.inline import ticket_controls

log = logging.getLogger(__name__)

PAGE = 20
COOKIE = "webadmin_sid"

CSS = """
body{font-family:system-ui,sans-serif;max-width:1000px;margin:0 auto;padding:16px;color:#222}
nav a{margin-right:12px}nav{margin-bottom:16px;padding-bottom:8px;border-bottom:1px solid #ccc}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:6px 8px;text-align:left}
tr:nth-child(even){background:#f6f6f6}.muted{color:#777}.err{color:#a00}.ok{color:#0a0}
input,select,button{padding:6px 8px;margin:2px}form.inline{display:inline}
.badge{padding:2px 8px;border-radius:8px;background:#eee}
"""

ACCESS_RU = {"pending": "⏳ На рассмотрении", "approved": "✅ Одобрен", "denied": "🚫 Закрыт"}


def esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=ru><head><meta charset=utf-8>"
        f"<title>{esc(title)}</title><style>{CSS}</style></head><body>"
        "<nav><b>Админка</b> <a href='/users'>👥 Пользователи</a>"
        "<a href='/tickets'>🧾 Заявки</a> <a href='/logout'>Выйти</a></nav>"
        f"<h2>{esc(title)}</h2>{body}</body></html>"
    )


def login_page(err: str = "") -> str:
    msg = f"<p class=err>{esc(err)}</p>" if err else ""
    return (
        "<!doctype html><html lang=ru><head><meta charset=utf-8><title>Вход</title>"
        f"<style>{CSS}</style></head><body><h2>Вход в админку</h2>{msg}"
        "<form method=post action='/login'>Пароль: "
        "<input type=password name=password autofocus> <button>Войти</button></form>"
        "</body></html>"
    )


def _authed(request: web.Request) -> bool:
    return request.cookies.get(COOKIE, "") in request.app["sessions"]


def _uname(u: dict) -> str:
    return (
        (u.get("fio") or "").strip()
        or (("@" + u["username"]) if u.get("username") else f"id {u.get('telegram_id')}")
    )


def _ulabel(u: dict) -> str:
    clinic = (u.get("clinic_short") or "").strip() or "—"
    return f"{clinic} — {_uname(u)}"


async def _refresh_group_card(bot, t: dict) -> None:
    """Обновить карточку заявки в чате специалистов (best-effort)."""
    card_id = t.get("engineer_msg_id") or 0
    if not card_id:
        return
    suffix = ""
    if t.get("status") == "done":
        suffix = f"\n\n✅ Выполнил: @{t.get('engineer_username') or 'web'}"
    elif t.get("status") == "rejected":
        suffix = f"\n\n⛔ Отклонил: @{t.get('engineer_username') or 'web'}"
    try:
        await bot.edit_message_text(
            ticket_card(t) + suffix,
            chat_id=settings.GROUP_CHAT_ID,
            message_id=card_id,
            reply_markup=ticket_controls(t["id"], t.get("status") or "new"),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("Веб: не удалось обновить карточку #%s: %s", t.get("id"), e)


def _pager(base: str, total: int, offset: int, extra: str = "") -> str:
    pages = max((total + PAGE - 1) // PAGE, 1)
    cur = offset // PAGE
    h = f"<p class=muted>Всего {total}, стр. {cur + 1}/{pages} "
    if cur > 0:
        h += f"<a href='{base}?offset={(cur - 1) * PAGE}{extra}'>⬅️</a> "
    if cur < pages - 1:
        h += f"<a href='{base}?offset={(cur + 1) * PAGE}{extra}'>➡️</a>"
    return h + "</p>"


# ---------- auth ----------

async def index(request: web.Request) -> web.Response:
    raise web.HTTPFound("/users" if _authed(request) else "/login")


async def login_get(request: web.Request) -> web.Response:
    return web.Response(text=login_page(), content_type="text/html")


async def login_post(request: web.Request) -> web.Response:
    data = await request.post()
    if hmac.compare_digest(data.get("password", ""), settings.WEB_PASSWORD):
        sid = secrets.token_urlsafe(32)
        request.app["sessions"].add(sid)
        resp = web.HTTPFound("/users")
        resp.set_cookie(COOKIE, sid, httponly=True, samesite="Lax")
        return resp
    return web.Response(text=login_page("Неверный пароль"), content_type="text/html")


async def logout(request: web.Request) -> web.Response:
    request.app["sessions"].discard(request.cookies.get(COOKIE, ""))
    resp = web.HTTPFound("/login")
    resp.del_cookie(COOKIE)
    return resp


# ---------- users ----------

async def users_list(request: web.Request) -> web.Response:
    if not _authed(request):
        raise web.HTTPFound("/login")
    q = request.query.get("q", "").strip()
    try:
        offset = max(int(request.query.get("offset", 0)), 0)
    except ValueError:
        offset = 0
    if q:
        users = await search_users(q)
        total = len(users)
        users = users[offset : offset + PAGE]
    else:
        total = await count_users()
        users = await get_all_users(PAGE, offset)
    rows = "".join(
        f"<tr><td><a href='/users/{u['telegram_id']}'>{esc(_ulabel(u))}</a></td>"
        f"<td>{esc(u.get('phone') or '—')}</td>"
        f"<td><span class=badge>{esc(ACCESS_RU.get((u.get('access') or 'pending'), '?'))}</span></td></tr>"
        for u in users
    )
    body = (
        f"<form>Поиск: <input name=q value='{esc(q)}'> <button>Найти</button> "
        f"<a href='/users'>Сброс</a></form>"
        f"<table><tr><th>Поликлиника — ФИО</th><th>Телефон</th><th>Доступ</th></tr>{rows}</table>"
        + _pager("/users", total, offset, f"&q={esc(q)}" if q else "")
    )
    return web.Response(text=page("Пользователи", body), content_type="text/html")


async def user_detail(request: web.Request) -> web.Response:
    if not _authed(request):
        raise web.HTTPFound("/login")
    try:
        tg = int(request.match_info["tg"])
    except ValueError:
        raise web.HTTPNotFound()
    u = await get_user(tg)
    if not u:
        return web.Response(text=page("Нет такого", "<p class=err>Пользователь не найден.</p>"))
    access = (u.get("access") or "pending").strip()
    fields = "".join(
        f"<tr><td>{label}</td><td><input name={name} value='{esc(u.get(name) or '')}' size=40></td></tr>"
        for label, name in [
            ("ФИО", "fio"),
            ("Телефон", "phone"),
            ("Поликлиника", "clinic_short"),
            ("Адрес", "address"),
            ("Должность", "position"),
        ]
    )
    tickets = await get_user_tickets(tg, 50)
    trows = "".join(
        f"<tr><td><a href='/tickets/{t['id']}'>#{t['id']}</a></td>"
        f"<td>{esc(TYPE_LABELS.get(t.get('type', ''), ''))}</td>"
        f"<td>{esc(STATUS_LABELS.get(t.get('status', ''), ''))}</td>"
        f"<td>{esc(fmt_dt(t.get('created_at')))}</td></tr>"
        for t in tickets
    )
    body = (
        f"<table>{fields}<tr><td>Telegram</td><td>@{esc(u.get('username') or '—')} "
        f"(<code>{tg}</code>)</td></tr>"
        f"<tr><td>Доступ</td><td><span class=badge>{esc(ACCESS_RU.get(access, access))}</span>"
        + (f" ({esc(u.get('access_reason') or '')})" if access == "denied" else "")
        + "</td></tr></table>"
        f"<form method=post action='/users/{tg}/save'><button>💾 Сохранить данные</button></form> "
        f"<form class=inline method=post action='/users/{tg}/access'>"
        "<button name=action value=approve>✅ Одобрить</button> "
        "<button name=action value=deny>❌ Отклонить</button> "
        "Причина отказа: <input name=reason size=30></form>"
        f"<h3>Обращения ({len(tickets)})</h3>"
        f"<table><tr><th>ID</th><th>Тип</th><th>Статус</th><th>Создана</th></tr>{trows}</table>"
    )
    return web.Response(text=page(_ulabel(u), body), content_type="text/html")


async def user_save(request: web.Request) -> web.Response:
    if not _authed(request):
        raise web.HTTPFound("/login")
    tg = int(request.match_info["tg"])
    data = await request.post()
    await update_profile(
        tg,
        fio=data.get("fio", "").strip(),
        phone=data.get("phone", "").strip(),
        clinic_short=data.get("clinic_short", "").strip(),
        address=data.get("address", "").strip(),
        position=data.get("position", "").strip(),
    )
    raise web.HTTPFound(f"/users/{tg}")


async def user_access(request: web.Request) -> web.Response:
    if not _authed(request):
        raise web.HTTPFound("/login")
    tg = int(request.match_info["tg"])
    data = await request.post()
    bot = request.app["bot"]
    if data.get("action") == "approve":
        await set_access(tg, "approved", by="web")
        await notify_user(bot, tg, "✅ <b>Доступ одобрен!</b> Теперь можно пользоваться ботом — нажмите /start.")
    else:
        reason = data.get("reason", "").strip() or "без указания причины"
        await set_access(tg, "denied", reason=reason, by="web")
        await notify_user(bot, tg, f"❌ В доступе отказано: {reason}.")
    raise web.HTTPFound(f"/users/{tg}")


# ---------- tickets ----------

async def tickets_list(request: web.Request) -> web.Response:
    if not _authed(request):
        raise web.HTTPFound("/login")
    status = request.query.get("status", "").strip()
    q = request.query.get("q", "").strip()
    try:
        offset = max(int(request.query.get("offset", 0)), 0)
    except ValueError:
        offset = 0
    if status not in ("", "new", "in_progress", "done", "rejected"):
        status = ""
    total = await count_tickets(status, q)
    tickets = await list_tickets(status, q, PAGE, offset)
    opts = "".join(
        f"<option value='{s}'{' selected' if s == status else ''}>{l}</option>"
        for s, l in [("", "Все"), ("new", "Новые"), ("in_progress", "В работе"), ("done", "Выполнены"), ("rejected", "Отклонены")]
    )
    rows = "".join(
        f"<tr><td><a href='/tickets/{t['id']}'>#{t['id']}</a></td>"
        f"<td>{esc(TYPE_LABELS.get(t.get('type', ''), ''))}</td>"
        f"<td>{esc(t.get('clinic_short') or t.get('clinic_num') or '—')}</td>"
        f"<td>{esc(STATUS_LABELS.get(t.get('status', ''), ''))}</td>"
        f"<td>{esc(fmt_dt(t.get('created_at')))}</td></tr>"
        for t in tickets
    )
    body = (
        f"<form>Статус: <select name=status>{opts}</select> "
        f"Поиск: <input name=q value='{esc(q)}'> <button>Найти</button></form>"
        f"<table><tr><th>ID</th><th>Тип</th><th>Поликлиника</th><th>Статус</th><th>Создана</th></tr>{rows}</table>"
        + _pager("/tickets", total, offset, f"&status={status}&q={esc(q)}")
    )
    return web.Response(text=page("Заявки", body), content_type="text/html")


def _ticket_table(t: dict) -> str:
    models = "<br>".join(esc(m) for m in (t.get("printer_model") or "").splitlines() if m.strip())
    rows = [
        ("Тип", esc(TYPE_LABELS.get(t.get("type", ""), ""))),
        ("Статус", esc(STATUS_LABELS.get(t.get("status", ""), ""))),
        ("Инициатор", f"{esc(t.get('author_name') or '—')} | {esc(t.get('author_phone') or '—')}"),
        ("Поликлиника", esc(t.get("clinic_num") or "—")),
        ("Адрес", esc(t.get("address") or "—")),
        ("Техточка", esc(t.get("tech_point") or "—")),
        ("Кабинет", esc(t.get("cabinet") or "—")),
        ("IP", esc(t.get("ip_address") or "—")),
        ("Модели", models or "—"),
        ("Количество", esc(t.get("quantity") or "—")),
        ("Описание", esc(t.get("description") or "—")),
        ("Ответственный", f"@{esc(t.get('engineer_username') or '—')}"),
        ("Создана", esc(fmt_dt(t.get("created_at")))),
    ]
    if t.get("status") == "rejected" and t.get("reject_reason"):
        rows.append(("Причина отклонения", esc(t["reject_reason"])))
    if t.get("reopen_comment"):
        rows.append(("Комментарий пользователя", esc(t["reopen_comment"])))
    return "<table>" + "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows) + "</table>"


async def ticket_detail(request: web.Request) -> web.Response:
    if not _authed(request):
        raise web.HTTPFound("/login")
    try:
        tid = int(request.match_info["id"])
    except ValueError:
        raise web.HTTPNotFound()
    t = await get_ticket(tid)
    if not t:
        return web.Response(text=page("Нет такой", "<p class=err>Заявка не найдена.</p>"))
    body = (
        _ticket_table(t)
        + f"<p><a href='/users/{t['telegram_id']}'>👤 Открыть пользователя</a></p>"
        + f"<form method=post action='/tickets/{tid}/status'>"
        "<button name=action value=take>🛠 В работу</button> "
        "<button name=action value=done>✅ Выполнено</button> "
        "<button name=action value=reject>❌ Отклонить</button> "
        "Причина отклонения: <input name=reason size=30></form> "
        + f"<form class=inline method=post action='/tickets/{tid}/resend'>"
        "<button>📨 Дублировать в чат</button></form>"
    )
    return web.Response(text=page(f"Заявка #{tid}", body), content_type="text/html")


async def ticket_status(request: web.Request) -> web.Response:
    if not _authed(request):
        raise web.HTTPFound("/login")
    tid = int(request.match_info["id"])
    data = await request.post()
    bot = request.app["bot"]
    t = await get_ticket(tid)
    if not t:
        raise web.HTTPFound("/tickets")
    action = data.get("action", "")
    mapping = {"take": "in_progress", "done": "done", "reject": "rejected"}
    if action not in mapping:
        raise web.HTTPFound(f"/tickets/{tid}")
    reason = data.get("reason", "").strip() if action == "reject" else ""
    if action == "reject" and not reason:
        return web.Response(text=page("Ошибка", "<p class=err>Для отклонения укажите причину.</p>"))
    await update_ticket_status(tid, mapping[action], engineer_username="web", reject_reason=reason)
    t = await get_ticket(tid)
    await _refresh_group_card(bot, t)
    texts = {
        "take": f"🛠 Ваша заявка <b>#{tid}</b> взята в работу. Ожидайте выполнения.",
        "done": f"✅ Заявка <b>#{tid}</b> выполнена! Если проблема осталась — верните её в работу из «📁 Мои обращения».",
        "reject": f"❌ Заявка <b>#{tid}</b> отклонена. Причина: {reason}",
    }
    await notify_user(bot, t["telegram_id"], texts[action])
    raise web.HTTPFound(f"/tickets/{tid}")


async def ticket_resend(request: web.Request) -> web.Response:
    if not _authed(request):
        raise web.HTTPFound("/login")
    tid = int(request.match_info["id"])
    bot = request.app["bot"]
    t = await get_ticket(tid)
    if not t:
        raise web.HTTPFound("/tickets")
    try:
        sent = await bot.send_message(
            settings.GROUP_CHAT_ID,
            ticket_card(t, header="🔁 <b>Дубликат заявки (отправлен администратором)</b>"),
            reply_markup=ticket_controls(tid, t.get("status") or "new"),
        )
    except Exception as e:  # noqa: BLE001
        log.error("Веб: не удалось продублировать #%s: %s", tid, e)
        return web.Response(text=page("Ошибка", "<p class=err>Не удалось отправить в чат.</p>"))
    await set_engineer_msg_id(tid, sent.message_id)
    for fid in photos_of(t):
        try:
            await bot.send_photo(settings.GROUP_CHAT_ID, fid, reply_to_message_id=sent.message_id)
        except Exception as e:  # noqa: BLE001
            log.warning("Веб: фото дубликата #%s: %s", tid, e)
    raise web.HTTPFound(f"/tickets/{tid}")


def create_app(bot) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app["sessions"] = set()
    app.router.add_get("/", index)
    app.router.add_get("/login", login_get)
    app.router.add_post("/login", login_post)
    app.router.add_get("/logout", logout)
    app.router.add_get("/users", users_list)
    app.router.add_get("/users/{tg}", user_detail)
    app.router.add_post("/users/{tg}/save", user_save)
    app.router.add_post("/users/{tg}/access", user_access)
    app.router.add_get("/tickets", tickets_list)
    app.router.add_get("/tickets/{id}", ticket_detail)
    app.router.add_post("/tickets/{id}/status", ticket_status)
    app.router.add_post("/tickets/{id}/resend", ticket_resend)
    return app
