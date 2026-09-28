# Telegram-бот приёма заявок в ТП + запросы картриджей

aiogram 3.x, SQLite (aiosqlite), Docker + Compose. Пользователи создают заявки через личку с ботом,
специалисты обрабатывают их кнопками в закрытом общем чате.

## Как это работает

1. Пользователь в ЛС: при первом обращении бот просит ФИО, телефон, номер поликлиники
   (сокращённо), адрес и должность → заявка на доступ падает в чат специалистов, где её ✅ одобряет
   или ❌ отклоняет (с причиной) любой участник. До одобрения бот недоступен.
   Кнопка 👤 «Мои данные» — просмотр/правка (+ статус доступа, повторный запрос).
   Далее: 📝 заявка в ТП или 🖨 запрос картриджей — поликлиника и адрес подтягиваются
   из профиля, бот уточняет «Данные верны?» (можно ввести свои).
   Заявка в ТП: номер техточки строго в формате `001-0001`, кнопки ✏️ правки каждого поля.
   Картриджи: сначала «на один принтер» (только номер техточки + номер кабинета,
   количество всегда 1) или «на всю поликлинику» (выбор 1+ моделей из списка:
   CF280/CF226/CF287/CF259/DL-5120/TL-5120 — каждая выводится в чате с новой строки).
   📁 Свои обращения; выполненную/отклонённую заявку можно «↩️ Вернуть в работу» с комментарием.
2. Карточка заявки падает в закрытый чат специалистов (`GROUP_CHAT_ID`) с кнопками
   🛠 / ✅ / ❌, а также «🔄 Взять на себя» (смена ответственного у заявки в работе)
   и «✉️ Написать пользователю» (сообщение без reply — уйдёт пользователю по заявке).
   Ответ (reply) на карточку уходит пользователю как комментарий; ответ пользователя (reply на сообщение
   бота) возвращается в ветку заявки в чате. Запасной вариант: `/comment <id> <текст>`.
3. Кнопка меняет статус в БД, правит карточку и шлёт уведомление пользователю
   («взята в работу, ожидайте выполнения» — без ника специалиста)
   (если пользователь заблокировал бота — в чат пишется предупреждение, бот не падает).

## Деплой на VPS за 3 шага

**1. Подготовьте сервер и код:**
```bash
# на VPS: Docker + Compose уже стоят (иначе: curl -fsSL https://get.docker.com | sh)
git clone <your-repo> support_bot && cd support_bot
cp .env.example .env
nano .env   # впишите BOT_TOKEN (от @BotFather), GROUP_CHAT_ID и ADMIN_IDS
```

> Как узнать `GROUP_CHAT_ID`: добавьте бота в закрытый чат специалистов, дайте ему права администратора
> (чтобы мог читать и править сообщения), отправьте туда любое сообщение и посмотрите ID через
> `@userinfobot` / `@getmyid_bot` (вид `-100...`).

**2. Запустите:**
```bash
docker compose up -d --build
docker compose logs -f
```

**3. Проверьте:**
- напишите боту `/start`, создайте тестовую заявку;
- карточка должна прийти в чат специалистов; нажмите «🛠 В работу» → пользователю придёт уведомление.

## Полезные команды

```bash
docker compose logs -f          # логи
docker compose restart          # перезапуск
docker compose down             # остановить (данные в ./data сохранятся)
```

## Структура

```
bot.py                  # точка входа
config.py               # настройки из .env (pydantic-settings)
database/
  __init__.py
  db.py                 # init, CRUD тикетов/пользователей
handlers/
  user_handlers.py      # ЛС: регистрация, FSM-опросы, «Мои обращения», возвраты
  engineer_handlers.py  # чат специалистов: кнопки, смена ответственного, написать пользователю,
                        # отклонения, доступ, комментарии, /status, /comment
  admin_handlers.py     # /admin: список пользователей, правка ФИО/адреса/поликлиники, блок
  fsm_states.py         # SupportForm / CartridgeForm / RejectForm / ProfileForm / AccessForm / AdminForm
  common.py             # карточки, уведомления, сохранение фото
keyboards/
  reply.py              # главное меню, отмена/пропуск/подтверждение
  inline.py             # кнопки 🛠/✅/❌ и список обращений
data/                   # bot_database.db + photos/ (volume, в git не коммитится)
Dockerfile
docker-compose.yml
```

## Локальный запуск без Docker

```bash
python -m venv .venv && .venv\Scripts\activate   # Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env   # Linux: cp .env.example .env — заполнить!
python bot.py
```

## Админ-панель

В `.env` укажите `ADMIN_IDS` (Telegram ID через запятую). Команда `/admin` в личке показывает
всех пользователей в формате «Поликлиника — ФИО» (по 20 на странице, есть 🔍 поиск
по ФИО/телефону/поликлинике/адресу/ID): карточка, правка ФИО/адреса/поликлиники,
закрытие/открытие доступа.

## Схема БД

- `users(id, telegram_id UNIQUE, full_name, username, fio, phone, clinic_short, address, position,
  access['pending'|'approved'|'denied'], access_reason, access_by, access_msg_id)`
- `tickets(id, user_id, telegram_id, type['support'|'cartridge'], clinic_num, address, tech_point, cabinet,
  ip_address, printer_model, quantity, description, photos_json[file_id...], status['new'|'in_progress'|'done'|'rejected'],
  reject_reason, engineer_username, engineer_msg_id, author_name, author_phone, reopen_comment,
  scope['point'|'clinic'], created_at, updated_at)`
- `msg_links(ticket_id, engineer_msg_id, user_msg_id)` — связки переписки по заявке

## Как собрать Docker-образ вручную (по шагам)

Если `docker compose` не нужен и хочется собрать/проверить образ самому.

**Шаг 1. Проверьте, что Docker установлен:**
```bash
docker --version
```
Windows: нужен установленный [Docker Desktop](https://www.docker.com/products/docker-desktop/) и запущенное приложение (иконка кита в трее). Linux: `curl -fsSL https://get.docker.com | sh`, затем `sudo usermod -aG docker $USER` и перелогиньтесь.

**Шаг 2. Проверьте файлы для сборки** (все должны лежать в корне проекта):
- `Dockerfile` — инструкция сборки образа;
- `requirements.txt` — зависимости Python;
- `bot.py`, `config.py`, `database/`, `handlers/`, `keyboards/` — код;
- `.env` — **не** копируется в образ (он подставляется при запуске), но для локального теста сборки не нужен.

**Шаг 3. Соберите образ** (из корня проекта, там где `Dockerfile`):
```bash
docker build -t support_bot:latest .
```
- `-t support_bot:latest` — имя (`support_bot`) и тег (`latest`) образа;
- точка `.` в конце — обязательно: это путь к контексту сборки (текущая папка).
- Первая сборка качает `python:3.11-slim` и ставит зависимости — займёт несколько минут. Повторные сборки быстрые (слои кэшируются).

**Шаг 4. Проверьте, что образ создался:**
```bash
docker images support_bot
```

**Шаг 5. Запустите контейнер из образа для проверки:**
```bash
# Windows PowerShell:
docker run -d --name support_bot_test --env-file .env -v ${PWD}/data:/app/data support_bot:latest
# Linux:
docker run -d --name support_bot_test --env-file .env -v ./data:/app/data support_bot:latest
```
Что тут происходит:
- `--env-file .env` — секреты (`BOT_TOKEN`, `GROUP_CHAT_ID`, `ADMIN_IDS`) попадают в контейнер без вшивания в образ;
- `-v ./data:/app/data` — база и фото хранятся на хосте и не пропадут при удалении контейнера.

**Шаг 6. Проверьте логи и остановите тест:**
```bash
docker logs -f support_bot_test   # должны быть строки "БД готова" и "Бот запущен"
docker stop support_bot_test && docker rm support_bot_test
```

**Шаг 7. После изменений в коде — пересоберите:**
```bash
docker build -t support_bot:latest .
```
Флаг `--no-cache` нужен только если что-то «застряло»: `docker build --no-cache -t support_bot:latest .`

> ⚠️ Никогда не вшивайте `.env` с токеном в образ (`COPY .env` делать нельзя) и не выкладывайте
> собранный образ с секретами в публичные реестры.

## Как выложить проект на GitHub (по шагам)

**Шаг 0. Убедитесь, что секреты не уедут в git.** Файл `.gitignore` уже настроен и исключает:
`.env`, `data/` (база и фото), `.venv/`, `__pycache__/`. В репозиторий попадут только код,
`Dockerfile`, `docker-compose.yml`, `.env.example`, `requirements.txt`, `README.md`.

**Шаг 1. Проверьте git:**
```bash
git --version
```
Нет git — установите с [git-scm.com](https://git-scm.com/) (Windows: Git for Windows, при установке можно оставить всё по умолчанию).

**Шаг 2. Откройте папку проекта и инициализируйте репозиторий:**
```bash
cd D:\Dev\tlg_bot
git rev-parse --show-toplevel   # покажет корень текущего репозитория, если вы уже внутри какого-то
```

> ⚠️ Если команда выше показала папку *выше* `tlg_bot` (например `D:\Dev`) — значит проект лежит
> внутри чужого репозитория. Вложенный `git init` создаст «репозиторий в репозитории», и GitHub
> его нормально не примет. Правильно так: скопируйте папку `tlg_bot` отдельно
> (например в `D:\support_bot`) и выполняйте шаги ниже уже там:
```powershell
Copy-Item -Recurse D:\Dev\tlg_bot D:\support_bot
cd D:\support_bot
```

```bash
git init
git branch -M main
```

**Шаг 3. Проверьте, что в коммит не попадет лишнего:**
```bash
git status
```
В списке НЕ должно быть `.env`, `data/`, `.venv/`. Если есть — остановитесь и проверьте `.gitignore`.

**Шаг 4. Сделайте первый коммит:**
```bash
git add .
git commit -m "Telegram-бот приёма заявок в ТП и картриджей"
```

**Шаг 5. Создайте репозиторий на GitHub:**
1. Зайдите на [github.com](https://github.com) → `+` (справа вверху) → **New repository**.
2. Repository name, например `support_bot`, **без** галочки «Add a README» (он у нас уже есть).
3. Private или Public — на выбор (токена в коде нет, можно Public).
4. Нажмите **Create repository** — GitHub покажет адрес вида `https://github.com/ВАШ_ЛОГИН/support_bot.git`.

**Шаг 6. Привяжите локальный репозиторий и отправьте код:**
```bash
git remote add origin https://github.com/ВАШ_ЛОГИН/support_bot.git
git push -u origin main
```
Git попросит авторизацию: удобнее всего через браузер (GitHub попросит войти) либо Personal Access Token вместо пароля: GitHub → Settings → Developer settings → Personal access tokens → Generate new token (scope `repo`) → вставьте вместо пароля.

**Шаг 7. Проверьте:** обновите страницу репозитория — там должны быть все файлы, кроме `.env`/`data`/`.venv`.

**Дальнейшая работа (каждая новая порция изменений):**
```bash
git status                    # что изменилось
git add .                     # или конкретные файлы: git add handlers/admin_handlers.py
git commit -m "Коротко что сделано"
git push
```

**Как забрать код на VPS:**
```bash
git clone https://github.com/ВАШ_ЛОГИН/support_bot.git support_bot && cd support_bot
cp .env.example .env && nano .env   # заполнить секреты
docker compose up -d --build
```
Обновление бота на VPS потом делается так:
```bash
cd support_bot && git pull && docker compose up -d --build
