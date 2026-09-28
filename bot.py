import os
import sqlite3
import threading
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import telebot
from telebot import types

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError("TOKEN environment variable is required")

bot = telebot.TeleBot(TOKEN)
DB = os.getenv("DB_PATH", "expenses.db")

# Only Alina and Natasha can use the bot.
PEOPLE = {
    463620997: "Алина",
    831511518: "Наташа",
}
ALLOWED_USERS = set(PEOPLE) | {
    int(x) for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip().isdigit()
}

conn = sqlite3.connect(DB, check_same_thread=False)
conn.execute("""CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    month TEXT NOT NULL,
    payer_id INTEGER NOT NULL,
    payer_name TEXT NOT NULL,
    amount REAL NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    expense_type TEXT NOT NULL DEFAULT 'shared',
    debtor_id INTEGER,
    debtor_name TEXT
)""")

# Migrate an older database created by the first version of the bot.
columns = {row[1] for row in conn.execute("PRAGMA table_info(expenses)").fetchall()}
if "expense_type" not in columns:
    conn.execute("ALTER TABLE expenses ADD COLUMN expense_type TEXT NOT NULL DEFAULT 'shared'")
if "debtor_id" not in columns:
    conn.execute("ALTER TABLE expenses ADD COLUMN debtor_id INTEGER")
if "debtor_name" not in columns:
    conn.execute("ALTER TABLE expenses ADD COLUMN debtor_name TEXT")
conn.commit()

DB_LOCK = threading.Lock()
pending = {}


def allowed(message):
    return message.from_user.id in ALLOWED_USERS


def menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("➕ Добавить трату", "💰 Баланс")
    kb.row("📋 История", "📊 Отчёты")
    kb.row("🔎 Поиск", "📈 Статистика")
    kb.row("😂 Прикол", "❓ Помощь")
    return kb


def report_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.row("📅 Сегодня", "📆 Неделя")
    kb.row("🗓 Месяц", "📅 Выбрать месяц")
    kb.row("◀️ Назад")
    return kb


def current_month():
    return datetime.now().strftime("%Y-%m")


def money(v):
    return f"{v:,.0f} ₽".replace(",", " ")


def person_name(user_id):
    return PEOPLE.get(user_id, "Пользователь")


def parse_amount(text):
    try:
        value = Decimal(text.replace(" ", "").replace(",", "."))
        if value <= 0:
            raise ValueError
        return float(value)
    except (InvalidOperation, ValueError):
        return None


def all_rows(where="", params=()):
    with DB_LOCK:
        return conn.execute(
            "SELECT id, payer_id, payer_name, amount, expense_type, debtor_id, debtor_name, note, created_at "
            "FROM expenses " + where + " ORDER BY created_at DESC, id DESC",
            params,
        ).fetchall()


def insert_expense(state, user_id):
    payer_name = person_name(state["payer_id"])
    debtor_id = state.get("debtor_id")
    debtor_name = person_name(debtor_id) if debtor_id else None
    created_at = datetime.now().isoformat(timespec="seconds")
    month = created_at[:7]
    with DB_LOCK:
        conn.execute(
            "INSERT INTO expenses(month,payer_id,payer_name,amount,category,note,created_at,expense_type,debtor_id,debtor_name) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                month,
                state["payer_id"],
                payer_name,
                state["amount"],
                "",
                state["note"],
                created_at,
                state["expense_type"],
                debtor_id,
                debtor_name,
            ),
        )
        conn.commit()


def expense_label(row):
    _, payer_id, payer_name, amount, expense_type, debtor_id, debtor_name, note, created_at = row
    dt = datetime.fromisoformat(created_at).strftime("%d.%m %H:%M")
    if expense_type == "shared":
        kind = "🤝 50/50"
    elif expense_type == "personal":
        kind = "🙋 Личная"
    else:
        kind = f"💸 Долг: {debtor_name} → {payer_name}"
    return f"{dt} · {payer_name} · {money(amount)} · {kind}\n   {note}"


@bot.message_handler(commands=["start"])
def start(message):
    if not allowed(message):
        bot.reply_to(message, "⛔ Этот бот только для Алины и Наташи 💅")
        return
    bot.send_message(
        message.chat.id,
        "💅 Привет! Это финансовый бот Алина × Наташа.\n\n"
        "Я буду помнить каждую трату, кто заплатил, что делим 50/50, а что является долгом.\n\n"
        "Никаких странных категорий вроде «хата/коммуналка» — только смысл траты.",
        reply_markup=menu(),
    )


@bot.message_handler(commands=["help"])
def help_command(message):
    if allowed(message):
        bot.send_message(message.chat.id, help_text(), reply_markup=menu())


def help_text():
    return (
        "🧠 Как пользоваться:\n\n"
        "➕ Добавить трату → сумма → кто заплатил → тип траты → комментарий.\n\n"
        "🤝 50/50 — расход считается общим и делится поровну.\n"
        "🙋 Личная — просто записывается в историю и не меняет взаимный баланс.\n"
        "💸 Долг/заём — вся сумма записывается как долг того, кто должен, тому, кто заплатил.\n\n"
        "💰 Баланс — кто кому сколько должен.\n"
        "📊 Отчёты — день / неделя / месяц.\n"
        "🔎 Поиск — ищет по описанию и участнику.\n"
        "📈 Статистика — суммы, количество трат и самые дорогие покупки.\n"
        "😂 Прикол — иногда финансовая аналитика с характером."
    )


@bot.message_handler(func=lambda m: m.text == "❓ Помощь")
def help_msg(message):
    if allowed(message):
        bot.send_message(message.chat.id, help_text(), reply_markup=menu())


@bot.message_handler(func=lambda m: m.text == "➕ Добавить трату")
def add_start(message):
    if not allowed(message):
        return
    pending[message.from_user.id] = {"step": "amount"}
    bot.send_message(message.chat.id, "💸 Сколько потратили?\nНапиши сумму, например: 3500")


@bot.message_handler(func=lambda m: m.text == "💰 Баланс")
def balance(message):
    if not allowed(message):
        return

    rows = all_rows()
    # Net settlement consists of shared expenses plus explicit debts.
    net = {463620997: 0.0, 831511518: 0.0}
    for row in rows:
        _, payer_id, _, amount, expense_type, debtor_id, _, _, _ = row
        if expense_type == "shared":
            net[payer_id] += amount / 2
            other = 831511518 if payer_id == 463620997 else 463620997
            net[other] -= amount / 2
        elif expense_type == "debt" and debtor_id in net:
            net[debtor_id] += amount
            net[payer_id] -= amount

    lines = ["💰 БАЛАНС", "", f"Алина: {money(abs(net[463620997]))}", f"Наташа: {money(abs(net[831511518]))}", ""]
    if abs(net[463620997]) < 0.01:
        lines.append("✨ Всё ровно. Никто никому не должен!")
    elif net[463620997] > 0:
        lines.append(f"💸 Наташа должна Алине: {money(net[463620997])}")
    else:
        lines.append(f"💸 Алина должна Наташе: {money(-net[463620997])}")

    # Explicit debt reminder.
    debts = [r for r in rows if r[4] == "debt"]
    if debts:
        lines.append("\n📌 Долги/займы:")
        for r in debts[:10]:
            lines.append(f"• {r[7]} — {money(r[3])}: {r[6]} → {r[2]}")

    bot.send_message(message.chat.id, "\n".join(lines), reply_markup=menu())


@bot.message_handler(func=lambda m: m.text == "📋 История")
def history(message):
    if not allowed(message):
        return
    rows = all_rows("WHERE month=?", (current_month(),))[:30]
    if not rows:
        bot.send_message(message.chat.id, "Пока за этот месяц пусто 🫠", reply_markup=menu())
        return
    text = "📋 ИСТОРИЯ — текущий месяц\n\n" + "\n\n".join(expense_label(r) for r in rows)
    bot.send_message(message.chat.id, text[:3900], reply_markup=menu())


@bot.message_handler(func=lambda m: m.text == "📊 Отчёты")
def reports_start(message):
    if allowed(message):
        bot.send_message(message.chat.id, "Какой отчёт показать?", reply_markup=report_menu())


@bot.message_handler(func=lambda m: m.text in {"📅 Сегодня", "📆 Неделя", "🗓 Месяц"})
def report_period(message):
    if not allowed(message):
        return
    now = datetime.now()
    if message.text == "📅 Сегодня":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        title = f"СЕГОДНЯ — {now:%d.%m.%Y}"
    elif message.text == "📆 Неделя":
        start = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
        title = f"НЕДЕЛЯ — {start:%d.%m}–{now:%d.%m}"
    else:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        title = f"МЕСЯЦ — {now:%m.%Y}"

    rows = all_rows("WHERE created_at >= ?", (start.isoformat(timespec="seconds"),))
    send_report(message.chat.id, title, rows)


@bot.message_handler(func=lambda m: m.text == "📅 Выбрать месяц")
def choose_month(message):
    if not allowed(message):
        return
    pending[message.from_user.id] = {"step": "month"}
    bot.send_message(message.chat.id, "Напиши месяц в формате ММ.ГГГГ, например 09.2026")


@bot.message_handler(func=lambda m: m.text == "◀️ Назад")
def back(message):
    if allowed(message):
        pending.pop(message.from_user.id, None)
        bot.send_message(message.chat.id, "Ок 💅", reply_markup=menu())


def send_report(chat_id, title, rows):
    if not rows:
        bot.send_message(chat_id, f"📊 {title}\n\nПока трат нет 🥹", reply_markup=menu())
        return
    total_all = sum(r[3] for r in rows)
    shared = sum(r[3] for r in rows if r[4] == "shared")
    personal = sum(r[3] for r in rows if r[4] == "personal")
    debts = sum(r[3] for r in rows if r[4] == "debt")
    by_payer = {}
    for r in rows:
        by_payer[r[2]] = by_payer.get(r[2], 0) + r[3]
    text = (
        f"📊 {title}\n\n"
        f"💸 Всего записано: {money(total_all)}\n"
        f"🤝 Общих 50/50: {money(shared)}\n"
        f"🙋 Личных: {money(personal)}\n"
        f"💸 Долгов/займов: {money(debts)}\n\n"
        + "\n".join(f"👛 {name}: {money(amount)}" for name, amount in by_payer.items())
    )
    bot.send_message(chat_id, text, reply_markup=menu())


@bot.message_handler(func=lambda m: m.text == "🔎 Поиск")
def search_start(message):
    if not allowed(message):
        return
    pending[message.from_user.id] = {"step": "search"}
    bot.send_message(message.chat.id, "🔎 Что ищем? Например: такси, продукты, Алина, долг, 5000")


@bot.message_handler(func=lambda m: m.text == "📈 Статистика")
def statistics(message):
    if not allowed(message):
        return
    rows = all_rows("WHERE month=?", (current_month(),))
    if not rows:
        bot.send_message(message.chat.id, "📈 Статистика пока пустая 🥲", reply_markup=menu())
        return
    total = sum(r[3] for r in rows)
    avg = total / len(rows)
    biggest = max(rows, key=lambda r: r[3])
    shared_count = sum(1 for r in rows if r[4] == "shared")
    debt_count = sum(1 for r in rows if r[4] == "debt")
    text = (
        f"📈 СТАТИСТИКА — {datetime.now():%m.%Y}\n\n"
        f"💸 Трат: {len(rows)}\n"
        f"💰 Сумма: {money(total)}\n"
        f"🧾 Средняя трата: {money(avg)}\n"
        f"🤝 Общих: {shared_count}\n"
        f"💸 Долгов/займов: {debt_count}\n\n"
        f"🏆 Самая дорогая: {money(biggest[3])} — {biggest[7]}"
    )
    bot.send_message(message.chat.id, text, reply_markup=menu())


@bot.message_handler(func=lambda m: m.text == "😂 Прикол")
def joke(message):
    if not allowed(message):
        return
    rows = all_rows("WHERE month=?", (current_month(),))
    total = sum(r[3] for r in rows)
    jokes = [
        f"😂 За этот месяц вы уже потратили {money(total)}. Деньги просто решили пожить у других людей.",
        "😂 Финансовое правило №1: если не смотреть баланс, кажется, что всё нормально.",
        "😂 Я не осуждаю ваши траты. Я их документирую. Это хуже.",
        "😂 Долг — это когда деньги ушли в отпуск, но обещали вернуться.",
        "😂 50/50 — потому что 100/0 почему-то никто не согласовывает.",
    ]
    import random
    bot.send_message(message.chat.id, random.choice(jokes), reply_markup=menu())


@bot.message_handler(func=lambda m: True)
def flow(message):
    if not allowed(message):
        return
    uid = message.from_user.id
    state = pending.get(uid)
    if not state:
        return

    if state["step"] == "month":
        try:
            dt = datetime.strptime(message.text.strip(), "%m.%Y")
        except ValueError:
            bot.send_message(message.chat.id, "Нужно так: 09.2026")
            return
        month = dt.strftime("%Y-%m")
        rows = all_rows("WHERE month=?", (month,))
        pending.pop(uid, None)
        send_report(message.chat.id, f"МЕСЯЦ — {dt:%m.%Y}", rows)
        return

    if state["step"] == "search":
        query = message.text.strip().lower()
        pending.pop(uid, None)
        rows = all_rows()
        found = []
        for row in rows:
            haystack = " ".join(str(x or "") for x in row).lower()
            if query in haystack:
                found.append(row)
        if not found:
            bot.send_message(message.chat.id, "🔎 Ничего не нашла. Даже подозрительно 🕵️", reply_markup=menu())
            return
        text = "🔎 РЕЗУЛЬТАТЫ\n\n" + "\n\n".join(expense_label(r) for r in found[:30])
        bot.send_message(message.chat.id, text[:3900], reply_markup=menu())
        return

    if state["step"] == "amount":
        amount = parse_amount(message.text)
        if amount is None:
            bot.send_message(message.chat.id, "Напиши сумму цифрами, например 2500")
            return
        state.update(amount=amount, step="payer")
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.row("👩🏻 Алина", "👩🏼 Наташа")
        bot.send_message(message.chat.id, "Кто заплатил?", reply_markup=kb)
        return

    if state["step"] == "payer":
        payer_map = {"👩🏻 Алина": 463620997, "👩🏼 Наташа": 831511518}
        payer_id = payer_map.get(message.text)
        if not payer_id:
            bot.send_message(message.chat.id, "Выбери Алину или Наташу кнопкой 👆")
            return
        state.update(payer_id=payer_id, step="type")
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        kb.row("🤝 Делим 50/50", "🙋 Личная")
        kb.row("💸 Долг / заём")
        bot.send_message(message.chat.id, "Как учитывать эту трату?", reply_markup=kb)
        return

    if state["step"] == "type":
        type_map = {
            "🤝 Делим 50/50": "shared",
            "🙋 Личная": "personal",
            "💸 Долг / заём": "debt",
        }
        expense_type = type_map.get(message.text)
        if not expense_type:
            bot.send_message(message.chat.id, "Выбери один из вариантов кнопкой 👆")
            return
        state["expense_type"] = expense_type
        if expense_type == "debt":
            debtor_id = 831511518 if state["payer_id"] == 463620997 else 463620997
            state.update(debtor_id=debtor_id, step="note")
            debtor = person_name(debtor_id)
            bot.send_message(
                message.chat.id,
                f"💸 Записываю долг на {debtor}.\nКоротко напиши, за что/зачем был заём:",
            )
        else:
            state["step"] = "note"
            bot.send_message(message.chat.id, "Что это было? Напиши коротко 📝")
        return

    if state["step"] == "note":
        state["note"] = message.text.strip()
        insert_expense(state, uid)
        amount = state["amount"]
        payer = person_name(state["payer_id"])
        kind = {"shared": "🤝 делим 50/50", "personal": "🙋 личная", "debt": f"💸 долг {person_name(state['debtor_id'])}"}[state["expense_type"]]
        pending.pop(uid, None)
        bot.send_message(
            message.chat.id,
            f"✅ Записала!\n\n{money(amount)} · {payer}\n{kind}\n📝 {state['note']}\n\nМожешь открыть 💰 Баланс или 📊 Отчёты.",
            reply_markup=menu(),
        )


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Alina x Natasha finance bot is running")

    def log_message(self, format, *args):
        return


def run_web_server():
    port = int(os.getenv("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"Health server listening on port {port}")
    server.serve_forever()


if __name__ == "__main__":
    bot.delete_webhook(drop_pending_updates=True)
    threading.Thread(target=bot.infinity_polling, kwargs={"skip_pending": True}, daemon=True).start()
    run_web_server()
