import os
import sqlite3
from datetime import datetime
from decimal import Decimal, InvalidOperation

import telebot
from telebot import types

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError("TOKEN environment variable is required")

bot = telebot.TeleBot(TOKEN)
DB = os.getenv("DB_PATH", "expenses.db")
ALLOWED_USERS = {int(x) for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip().isdigit()}

conn = sqlite3.connect(DB, check_same_thread=False)
conn.execute("""CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    month TEXT NOT NULL,
    payer_id INTEGER NOT NULL,
    payer_name TEXT NOT NULL,
    amount REAL NOT NULL,
    category TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
)""")
conn.commit()

pending = {}


def allowed(message):
    return not ALLOWED_USERS or message.from_user.id in ALLOWED_USERS


def menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("➕ Добавить трату", "📊 Баланс")
    kb.row("📋 История", "📈 Отчёт")
    kb.row("🏠 Хата / коммуналка", "❓ Помощь")
    return kb


def current_month():
    return datetime.now().strftime("%Y-%m")


def money(v):
    return f"{v:,.0f} ₽".replace(",", " ")


@bot.message_handler(commands=["start"])
def start(message):
    if not allowed(message):
        bot.reply_to(message, "⛔ Этот бот только для Алины и Наташи 💅")
        return
    bot.send_message(message.chat.id, "💅 Привет! Это ваш общий кошелёк Алина × Наташа 💸\n\nДобавляйте траты — я сам посчитаю, кто сколько внёс и кто кому должен.", reply_markup=menu())


@bot.message_handler(func=lambda m: m.text == "❓ Помощь")
def help_msg(message):
    if allowed(message):
        bot.send_message(message.chat.id, "💸 Добавляй каждую общую трату.\n\n➕ Кто заплатил → сумма → категория → комментарий.\n📊 Баланс покажет, кто кому должен.\n📈 Отчёт покажет расходы за месяц.\n🏠 Хата и коммуналка можно вести отдельной категорией.", reply_markup=menu())


@bot.message_handler(func=lambda m: m.text == "➕ Добавить трату")
def add_start(message):
    if not allowed(message): return
    pending[message.from_user.id] = {"step": "amount"}
    bot.send_message(message.chat.id, "💸 Сколько потратила?\nНапиши сумму, например: 3500")


@bot.message_handler(func=lambda m: m.text == "📊 Баланс")
def balance(message):
    if not allowed(message): return
    rows = conn.execute("SELECT payer_id, payer_name, SUM(amount) FROM expenses WHERE month=? GROUP BY payer_id, payer_name", (current_month(),)).fetchall()
    if not rows:
        bot.send_message(message.chat.id, "Пока за этот месяц трат нет 🥹", reply_markup=menu()); return
    total = sum(r[2] for r in rows)
    lines = [f"💅 Баланс за {current_month()[5:]}.{current_month()[:4]}", f"Общие расходы: {money(total)}", ""]
    for _, name, amount in rows:
        lines.append(f"• {name}: {money(amount)}")
    lines.append(f"\nЕсли делим общие расходы 50/50: {money(total/2)} с каждой.")
    by_name = {r[1]: r[2] for r in rows}
    if len(by_name) == 2:
        a, b = list(by_name.items())
        diff = (a[1] - b[1]) / 2
        if abs(diff) < 0.01:
            lines.append("✨ Всё ровно, никто никому не должен!")
        elif diff > 0:
            lines.append(f"💸 {b[0]} должна {a[0]}: {money(diff)}")
        else:
            lines.append(f"💸 {a[0]} должна {b[0]}: {money(-diff)}")
    bot.send_message(message.chat.id, "\n".join(lines), reply_markup=menu())


@bot.message_handler(func=lambda m: m.text == "📋 История")
def history(message):
    if not allowed(message): return
    rows = conn.execute("SELECT payer_name, amount, category, note FROM expenses WHERE month=? ORDER BY id DESC LIMIT 30", (current_month(),)).fetchall()
    if not rows:
        bot.send_message(message.chat.id, "Пока пусто 🫠", reply_markup=menu()); return
    text = "📋 Траты за текущий месяц:\n\n"
    for name, amount, cat, note in rows:
        text += f"• {name} — {money(amount)} — {cat}\n  {note}\n"
    bot.send_message(message.chat.id, text, reply_markup=menu())


@bot.message_handler(func=lambda m: m.text == "📈 Отчёт")
def report(message):
    if not allowed(message): return
    rows = conn.execute("SELECT payer_name, SUM(amount) FROM expenses WHERE month=? GROUP BY payer_name", (current_month(),)).fetchall()
    total = sum(x[1] for x in rows)
    text = f"📊 ОТЧЁТ — {current_month()[5:]}.{current_month()[:4]}\n\n💸 Всего: {money(total)}\n"
    for name, amount in rows:
        text += f"👛 {name}: {money(amount)}\n"
    bot.send_message(message.chat.id, text, reply_markup=menu())


@bot.message_handler(func=lambda m: m.text == "🏠 Хата / коммуналка")
def home_report(message):
    if not allowed(message): return
    rows = conn.execute("SELECT payer_name, SUM(amount) FROM expenses WHERE month=? AND category IN ('Хата','Коммуналка') GROUP BY payer_name", (current_month(),)).fetchall()
    total = sum(x[1] for x in rows)
    text = f"🏠 ХАТА + КОММУНАЛКА\nВсего: {money(total)}\n\n" + "\n".join(f"• {n}: {money(a)}" for n,a in rows)
    bot.send_message(message.chat.id, text, reply_markup=menu())


@bot.message_handler(func=lambda m: True)
def flow(message):
    if not allowed(message): return
    state = pending.get(message.from_user.id)
    if not state: return
    uid = message.from_user.id
    if state["step"] == "amount":
        try: amount = float(Decimal(message.text.replace(" ", "").replace(",", ".")))
        except (InvalidOperation, ValueError):
            bot.send_message(message.chat.id, "Напиши сумму цифрами, например 2500"); return
        state.update(amount=amount, step="category")
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        for x in ["Общак", "Хата", "Коммуналка", "Продукты", "Развлечения", "Другое"]: kb.add(types.KeyboardButton(x))
        bot.send_message(message.chat.id, "Куда отнести трату? 💅", reply_markup=kb)
    elif state["step"] == "category":
        state.update(category=message.text, step="note")
        bot.send_message(message.chat.id, "Что купили/за что заплатили? Напиши коротко 📝")
    elif state["step"] == "note":
        name = message.from_user.first_name or "Пользователь"
        conn.execute("INSERT INTO expenses(month,payer_id,payer_name,amount,category,note,created_at) VALUES(?,?,?,?,?,?,?)", (current_month(), uid, name, state["amount"], state["category"], message.text, datetime.now().isoformat(timespec="seconds")))
        conn.commit()
        pending.pop(uid, None)
        bot.send_message(message.chat.id, f"Записала 💅\n{money(state['amount'])} · {state['category']}\n{message.text}\n\nТеперь можно посмотреть 📊 Баланс.", reply_markup=menu())


if __name__ == "__main__":
    bot.delete_webhook(drop_pending_updates=True)
    bot.infinity_polling(skip_pending=True)
