import os
import sqlite3
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import telebot
from telebot import types

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError("TOKEN environment variable is required")

bot = telebot.TeleBot(TOKEN)
DB = os.getenv("DB_PATH", "expenses.db")

ALINA = 463620997
NATASHA = 831511518
PEOPLE = {ALINA: "Алина", NATASHA: "Наташа"}
ALLOWED_USERS = set(PEOPLE)
db_lock = threading.RLock()

c = sqlite3.connect(DB, check_same_thread=False, timeout=30)
with db_lock:
    c.execute("""CREATE TABLE IF NOT EXISTS expenses(
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
        debtor_name TEXT,
        creditor_id INTEGER,
        creditor_name TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS settlements(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        closed_at TEXT NOT NULL,
        through_expense_id INTEGER NOT NULL,
        period TEXT NOT NULL,
        net_amount REAL NOT NULL,
        debtor_id INTEGER,
        creditor_id INTEGER,
        debtor_name TEXT,
        creditor_name TEXT
    )""")
    c.commit()

pending = {}


def ok(m):
    return m.from_user.id in ALLOWED_USERS


def money(x):
    return f"{x:,.0f} ₽".replace(",", " ")


def month():
    return datetime.now().strftime("%Y-%m")


def menu():
    k = types.ReplyKeyboardMarkup(resize_keyboard=True)
    k.row("➕ Добавить трату", "💰 Кто кому должен?")
    k.row("📋 История", "📊 Отчёты")
    k.row("📈 Статистика", "🔄 Взаиморасчёт")
    k.row("⚙️ Управление", "❓ Инструкция")
    return k


def person_keyboard():
    k = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    k.row("👩🏻 Алина", "👩🏻 Наташа")
    return k


def rows(where="", params=()):
    with db_lock:
        return c.execute(
            "SELECT id,payer_id,payer_name,amount,expense_type,debtor_id,debtor_name,note,created_at,creditor_id,creditor_name FROM expenses "
            + where + " ORDER BY created_at DESC,id DESC", params
        ).fetchall()


def cutoff():
    with db_lock:
        r = c.execute("SELECT through_expense_id FROM settlements ORDER BY id DESC LIMIT 1").fetchone()
    return r[0] if r else 0


def openrows():
    return rows("WHERE id>?", (cutoff(),))


def calc(rr=None):
    rr = openrows() if rr is None else rr
    n = {ALINA: 0.0, NATASHA: 0.0}
    for r in rr:
        _, payer, _, amount, kind, debtor, _, _, _, creditor, _ = r
        if kind == "shared" and payer in n:
            other = NATASHA if payer == ALINA else ALINA
            n[payer] += amount / 2
            n[other] -= amount / 2
        elif kind == "debt" and debtor in n and creditor in n:
            n[debtor] += amount
            n[creditor] -= amount
    return n


def balance_text():
    x = calc()[ALINA]
    if abs(x) < 0.01:
        return "✨ Всё ровно. Никто никому не должен!"
    if x > 0:
        return f"💸 Наташа должна Алине: {money(x)}"
    return f"💸 Алина должна Наташе: {money(-x)}"


def label(r):
    _, _, payer, amount, kind, debtor, debtor_name, note, created, creditor, creditor_name = r
    kind_text = "🤝 50/50" if kind == "shared" else f"💸 Долг: {creditor_name} → {debtor_name}"
    return f"{datetime.fromisoformat(created):%d.%m %H:%M} · {payer} · {money(amount)} · {kind_text}\n   {note or 'Без комментария'}"


def notify(uid, text):
    try:
        bot.send_message(uid, text)
    except Exception as e:
        print(f"Notification error for {uid}: {e}", flush=True)


def other_user(uid):
    return NATASHA if uid == ALINA else ALINA


@bot.message_handler(commands=["start"])
def start(m):
    if not ok(m):
        return bot.reply_to(m, "⛔ Этот бот только для Алины и Наташи 💅")
    bot.send_message(
        m.chat.id,
        """ЗДАРОВА, ЧЕЛИХА 👋🏻

Это ваш личный финансовый бот для АЛИНЫ И НАТАШИ.

Я создан для того, чтобы вы не запутались в деньгах, не вспоминали через месяц «а кто, блять, тогда платил?» и не сидели с калькулятором, выясняя, кто кому должен 😂

💸 ТРАТА — сумма → как учитывать → кто заплатил → что это было.
💰 ДОЛГ / ЗАЁМ — сумма → кто дал деньги → второй человек автоматически считается тем, кто взял.
🧮 ВЗАИМОРАСЧЁТ — я сама считаю чистую разницу.
⚙️ УПРАВЛЕНИЕ — если где-то накосячили и надо удалить.
📊 ОТЧЁТЫ — день, неделя, месяц.

Без бухгалтерской ебанины. Финансовый пиздец раскладываю по полочкам 😂

ЛАЙФ КУ БИ ДРИМ 💅🏻""",
        reply_markup=menu(),
    )


@bot.message_handler(func=lambda m: m.text in ("❓ Инструкция", "❓ Помощь"))
def help_(m):
    if ok(m):
        bot.send_message(
            m.chat.id,
            """🧠 КАК ЭТО РАБОТАЕТ

➕ Добавить трату
1. Сумма.
2. 🤝 50/50 или 💸 Долг / заём.
3. Для 50/50 — кто заплатил.
4. Для долга — кто дал. Второй человек автоматически считается тем, кто взял.
5. Что это было.

💰 Кто кому должен? — текущий баланс.
🔄 Взаиморасчёт — чистая разница и возможность закрыть период.
📋 История — все записи.
⚙️ Управление — отмена последней, удаление конкретной или очистка текущего периода.

Если что-то записали криво — не паникуем, бухгалтерская карма переживёт 😂

ЛАЙФ КУ БИ ДРИМ.""",
            reply_markup=menu(),
        )


@bot.message_handler(func=lambda m: m.text == "➕ Добавить трату")
def add(m):
    if not ok(m):
        return
    pending[m.from_user.id] = {"step": "amount"}
    bot.send_message(m.chat.id, "💸 Сколько потратили?\n\nНапиши сумму цифрами, например: 3500")


@bot.message_handler(func=lambda m: m.text == "💰 Кто кому должен?")
def balance(m):
    if ok(m):
        bot.send_message(m.chat.id, "💰 ТЕКУЩИЙ ВЗАИМОРАСЧЁТ\n\n" + balance_text(), reply_markup=menu())


@bot.message_handler(func=lambda m: m.text == "🔄 Взаиморасчёт")
def settlement(m):
    if not ok(m):
        return
    rr = openrows()
    x = calc(rr)[ALINA]
    text = "✨ Сейчас всё ровно." if not rr or abs(x) < .01 else (f"Наташа должна Алине: {money(x)}" if x > 0 else f"Алина должна Наташе: {money(-x)}")
    k = types.InlineKeyboardMarkup()
    if rr:
        k.add(types.InlineKeyboardButton("🔒 Закрыть взаиморасчёт", callback_data="settle_confirm"))
    bot.send_message(m.chat.id, "🔄 ВЗАИМОРАСЧЁТ\n\n" + text + "\n\nИстория не удаляется.", reply_markup=k if rr else menu())


@bot.callback_query_handler(func=lambda q: q.data == "settle_confirm")
def settle_confirm(q):
    if q.from_user.id not in ALLOWED_USERS:
        return
    x = calc(openrows())[ALINA]
    text = "✨ Никто никому не должен." if abs(x) < .01 else (f"Наташа → Алине: {money(x)}" if x > 0 else f"Алина → Наташе: {money(-x)}")
    k = types.InlineKeyboardMarkup()
    k.row(types.InlineKeyboardButton("✅ Да, закрыть", callback_data="settle_yes"), types.InlineKeyboardButton("❌ Отмена", callback_data="settle_no"))
    bot.send_message(q.message.chat.id, "🔒 Закрываем взаиморасчёт?\n\n" + text + "\n\nВсе траты останутся в истории. Текущий баланс станет 0 ₽.", reply_markup=k)
    bot.answer_callback_query(q.id)


@bot.callback_query_handler(func=lambda q: q.data == "settle_yes")
def settle_yes(q):
    if q.from_user.id not in ALLOWED_USERS:
        return
    rr = openrows()
    if not rr:
        return bot.answer_callback_query(q.id, "Нечего закрывать", show_alert=True)
    x = calc(rr)[ALINA]
    debtor = NATASHA if x > 0 else ALINA if x < 0 else None
    creditor = ALINA if x > 0 else NATASHA if x < 0 else None
    through = max(r[0] for r in rr)
    with db_lock:
        c.execute("INSERT INTO settlements(closed_at,through_expense_id,period,net_amount,debtor_id,creditor_id,debtor_name,creditor_name) VALUES(?,?,?,?,?,?,?,?)", (datetime.now().isoformat(timespec="seconds"), through, month(), abs(x), debtor, creditor, PEOPLE.get(debtor), PEOPLE.get(creditor)))
        c.commit()
    result = "✨ Никто никому не должен." if abs(x) < .01 else f"💸 {PEOPLE[debtor]} → {PEOPLE[creditor]}: {money(abs(x))}"
    bot.answer_callback_query(q.id, "Закрыто 💅")
    bot.send_message(q.message.chat.id, "🧹 ФИНАНСОВЫЙ СТОЛ ОЧИЩЕН\n\n" + result + "\n\nСтарые траты сохранены. Новый период начался с нуля.\n\nЛАЙФ КУ БИ ДРИМ 💅🏻", reply_markup=menu())
    notify(other_user(q.from_user.id), "🧹 АЛИНА И НАТАША ЗАКРЫЛИ ВЗАИМОРАСЧЁТ\n\n" + result + "\n\nСтарые траты сохранены, новый период — с нуля.\n\nДо следующего финансового пиздеца 😂")


@bot.callback_query_handler(func=lambda q: q.data == "settle_no")
def settle_no(q):
    bot.answer_callback_query(q.id, "Оставляем 😌")


@bot.message_handler(func=lambda m: m.text == "📋 История")
def history(m):
    if ok(m):
        rr = rows("WHERE month=?", (month(),))[:50]
        bot.send_message(m.chat.id, "📋 ИСТОРИЯ\n\n" + ("\n\n".join(label(r) for r in rr) if rr else "Пока пусто. Бухгалтерия девственно чиста 😭"), reply_markup=menu())


@bot.message_handler(func=lambda m: m.text == "📊 Отчёты")
def reports(m):
    if ok(m):
        k = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        k.row("📅 Сегодня", "📆 Неделя")
        k.row("🗓 Месяц", "◀️ Назад")
        bot.send_message(m.chat.id, "📊 Какой отчёт показать?", reply_markup=k)


@bot.message_handler(func=lambda m: m.text in {"📅 Сегодня", "📆 Неделя", "🗓 Месяц"})
def period(m):
    if not ok(m):
        return
    now = datetime.now()
    if m.text == "🗓 Месяц":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif m.text == "📆 Неделя":
        start = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rr = rows("WHERE created_at>=?", (start.isoformat(timespec="seconds"),))
    bot.send_message(m.chat.id, f"📊 ОТЧЁТ\n\n💸 Всего операций: {len(rr)}\n💰 Сумма операций: {money(sum(r[3] for r in rr))}", reply_markup=menu())


@bot.message_handler(func=lambda m: m.text == "📈 Статистика")
def stats(m):
    if ok(m):
        rr = rows("WHERE month=?", (month(),))
        shared = sum(r[3] for r in rr if r[4] == "shared")
        debts = sum(r[3] for r in rr if r[4] == "debt")
        bot.send_message(m.chat.id, f"📈 СТАТИСТИКА {month()}\n\n💸 Операций: {len(rr)}\n🤝 50/50: {money(shared)}\n💸 Долги/займы: {money(debts)}\n\n{balance_text()}", reply_markup=menu())


@bot.message_handler(func=lambda m: m.text == "⚙️ Управление")
def manage(m):
    if ok(m):
        k = types.InlineKeyboardMarkup()
        k.add(types.InlineKeyboardButton("↩️ Отменить последнюю", callback_data="undo"))
        k.add(types.InlineKeyboardButton("🗑 Удалить операцию", callback_data="delete_choose"))
        k.add(types.InlineKeyboardButton("🧹 Очистить текущий период", callback_data="clear_current"))
        k.add(types.InlineKeyboardButton("🚨 Удалить всё незакрытое", callback_data="clear_all"))
        bot.send_message(m.chat.id, "⚙️ УПРАВЛЕНИЕ\n\nЗдесь можно исправить косяк. Не переживай, бухгалтерия не обидится 😂", reply_markup=k)


@bot.callback_query_handler(func=lambda q: q.data == "undo")
def undo(q):
    if q.from_user.id not in ALLOWED_USERS:
        return
    rr = openrows()
    if not rr:
        return bot.answer_callback_query(q.id, "Нечего отменять", show_alert=True)
    r = rr[0]
    with db_lock:
        c.execute("DELETE FROM expenses WHERE id=?", (r[0],))
        c.commit()
    bot.answer_callback_query(q.id, "Удалено 💅")
    bot.send_message(q.message.chat.id, "↩️ Последняя операция отменена.\n\n" + label(r), reply_markup=menu())
    notify(other_user(q.from_user.id), f"↩️ {PEOPLE[q.from_user.id]} отменила последнюю операцию.\n\n{label(r)}\n\nФинансовая карма восстановлена 😂")


@bot.callback_query_handler(func=lambda q: q.data == "delete_choose")
def delete_choose(q):
    if q.from_user.id not in ALLOWED_USERS:
        return
    rr = openrows()[:10]
    if not rr:
        return bot.answer_callback_query(q.id, "Нечего удалять", show_alert=True)
    k = types.InlineKeyboardMarkup()
    for r in rr:
        k.add(types.InlineKeyboardButton(f"#{r[0]} · {money(r[3])} · {r[7] or 'без названия'}", callback_data=f"delete:{r[0]}"))
    k.add(types.InlineKeyboardButton("❌ Закрыть", callback_data="manage_close"))
    bot.send_message(q.message.chat.id, "🗑 Что удалить?", reply_markup=k)
    bot.answer_callback_query(q.id)


@bot.callback_query_handler(func=lambda q: q.data.startswith("delete:"))
def delete_one(q):
    if q.from_user.id not in ALLOWED_USERS:
        return
    try:
        eid = int(q.data.split(":", 1)[1])
    except ValueError:
        return bot.answer_callback_query(q.id, "Ошибка", show_alert=True)
    rr = rows("WHERE id=?", (eid,))
    if not rr:
        return bot.answer_callback_query(q.id, "Уже удалено", show_alert=True)
    r = rr[0]
    with db_lock:
        c.execute("DELETE FROM expenses WHERE id=?", (eid,))
        c.commit()
    bot.answer_callback_query(q.id, "Удалено 💅")
    bot.send_message(q.message.chat.id, "🗑 Удалила:\n\n" + label(r), reply_markup=menu())
    notify(other_user(q.from_user.id), f"🗑 {PEOPLE[q.from_user.id]} удалила операцию:\n\n{label(r)}\n\nЕсли это был не косяк — разберитесь, пока бухгалтерский апокалипсис не начался 😂")


@bot.callback_query_handler(func=lambda q: q.data == "clear_current")
def clear_current(q):
    if q.from_user.id not in ALLOWED_USERS:
        return
    k = types.InlineKeyboardMarkup()
    k.row(types.InlineKeyboardButton("🔥 Да, снести", callback_data="clear_current_yes"), types.InlineKeyboardButton("❌ Нет", callback_data="manage_close"))
    bot.send_message(q.message.chat.id, "⚠️ Удалить все незакрытые траты текущего периода?\n\nСтарые закрытые периоды не трогаю.", reply_markup=k)
    bot.answer_callback_query(q.id)


@bot.callback_query_handler(func=lambda q: q.data == "clear_current_yes")
def clear_current_yes(q):
    if q.from_user.id not in ALLOWED_USERS:
        return
    ids = [r[0] for r in openrows()]
    with db_lock:
        if ids:
            c.executemany("DELETE FROM expenses WHERE id=?", [(i,) for i in ids])
            c.commit()
    bot.answer_callback_query(q.id, "Снесено 💥")
    bot.send_message(q.message.chat.id, f"🧹 Удалила {len(ids)} операций.\n\nФинансовый стол чист. С чистого листа, блять 😂", reply_markup=menu())
    notify(other_user(q.from_user.id), f"🧹 {PEOPLE[q.from_user.id]} очистила текущий финансовый период.\n\nУдалено операций: {len(ids)}.")


@bot.callback_query_handler(func=lambda q: q.data == "clear_all")
def clear_all(q):
    if q.from_user.id not in ALLOWED_USERS:
        return
    k = types.InlineKeyboardMarkup()
    k.row(types.InlineKeyboardButton("🚨 ДА, УДАЛИТЬ", callback_data="clear_all_yes"), types.InlineKeyboardButton("❌ Отмена", callback_data="manage_close"))
    bot.send_message(q.message.chat.id, "🚨 Это удалит ВСЕ незакрытые операции. Точно?", reply_markup=k)
    bot.answer_callback_query(q.id)


@bot.callback_query_handler(func=lambda q: q.data == "clear_all_yes")
def clear_all_yes(q):
    if q.from_user.id not in ALLOWED_USERS:
        return
    with db_lock:
        c.execute("DELETE FROM expenses WHERE id>?", (cutoff(),))
        c.commit()
    bot.answer_callback_query(q.id, "Готово")
    bot.send_message(q.message.chat.id, "🧹 Всё незакрытое удалено. Старые закрытые периоды не трогала.", reply_markup=menu())
    notify(other_user(q.from_user.id), f"🧹 {PEOPLE[q.from_user.id]} удалила все незакрытые операции.\n\nСтарые закрытые периоды сохранены.")


@bot.callback_query_handler(func=lambda q: q.data == "manage_close")
def manage_close(q):
    bot.answer_callback_query(q.id, "Закрыли 😌")
    try:
        bot.delete_message(q.message.chat.id, q.message.message_id)
    except Exception:
        pass


@bot.message_handler(func=lambda m: m.from_user.id in ALLOWED_USERS and m.from_user.id in pending)
def process_pending(m):
    uid = m.from_user.id
    s = pending.get(uid)
    if not s:
        return
    step = s.get("step")

    if step == "amount":
        raw = (m.text or "").replace(" ", "").replace(",", ".")
        try:
            amount = float(raw)
        except ValueError:
            return bot.send_message(m.chat.id, "🤨 Сумма где? Напиши цифрами, например 3500.")
        if amount <= 0:
            return bot.send_message(m.chat.id, "Ноль рублей — это уже не трата 😂 Напиши сумму больше нуля.")
        s["amount"] = amount
        s["step"] = "type"
        k = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        k.row("🤝 50/50", "💸 Долг / заём")
        return bot.send_message(m.chat.id, "🧾 Как учитывать эту сумму?", reply_markup=k)

    if step == "type":
        text = m.text or ""
        if text.startswith("🤝"):
            s["expense_type"] = "shared"
            s["step"] = "payer"
            return bot.send_message(m.chat.id, "👤 Кто заплатил?", reply_markup=person_keyboard())
        if text.startswith("💸"):
            s["expense_type"] = "debt"
            s["step"] = "creditor"
            return bot.send_message(m.chat.id, "💸 Кто ДАЛ деньги?", reply_markup=person_keyboard())
        return bot.send_message(m.chat.id, "Выбери тип кнопкой 👆")

    if step == "payer":
        p = ALINA if "Алина" in (m.text or "") else NATASHA if "Наташа" in (m.text or "") else None
        if not p:
            return bot.send_message(m.chat.id, "Выбери Алину или Наташу 👆")
        s.update(payer_id=p, payer_name=PEOPLE[p], step="note")
        return bot.send_message(m.chat.id, "🧾 Что это было?\n\nНапиши коротко или «пропустить».")

    if step == "creditor":
        p = ALINA if "Алина" in (m.text or "") else NATASHA if "Наташа" in (m.text or "") else None
        if not p:
            return bot.send_message(m.chat.id, "Выбери Алину или Наташу 👆")
        debtor = other_user(p)
        s.update(creditor_id=p, creditor_name=PEOPLE[p], debtor_id=debtor, debtor_name=PEOPLE[debtor], payer_id=p, payer_name=PEOPLE[p], step="note")
        return bot.send_message(m.chat.id, f"💸 {PEOPLE[p]} дала деньги → {PEOPLE[debtor]} взяла.\n\n🧾 Что это было?\nНапиши коротко или «пропустить".)

    if step == "note":
        text = (m.text or "").strip()
        note = "" if text.lower() in {"пропустить", "-", "нет"} else text
        now = datetime.now().isoformat(timespec="seconds")
        expense_type = s.get("expense_type", "shared")
        if expense_type not in {"shared", "debt"}:
            expense_type = "shared"
        payer_id = s.get("payer_id", uid)
        payer_name = PEOPLE.get(payer_id, "Алина" if uid == ALINA else "Наташа")
        with db_lock:
            c.execute(
                "INSERT INTO expenses(month,payer_id,payer_name,amount,note,created_at,expense_type,debtor_id,debtor_name,creditor_id,creditor_name) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (month(), payer_id, payer_name, s["amount"], note, now, expense_type, s.get("debtor_id"), s.get("debtor_name"), s.get("creditor_id"), s.get("creditor_name")),
            )
            c.commit()
            eid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        r = rows("WHERE id=?", (eid,))[0]
        pending.pop(uid, None)
        bot.send_message(m.chat.id, "✅ Записала!\n\n" + label(r) + "\n\n💰 " + balance_text(), reply_markup=menu())
        other = other_user(uid)
        if expense_type == "shared":
            notify(other, f"🚨 {PEOPLE[uid].upper()} ЧТО-ТО ЗАНЕСЛА В БУХГАЛТЕРИЮ\n\n{label(r)}\n\nС тебя: {money(s['amount']/2)}\n\nНе благодари. Я просто разношу финансовый пиздец по полочкам 😂")
        else:
            notify(other, f"💸 ФИНАНСОВАЯ ДРАМА\n\n{PEOPLE[s['creditor_id']]} дала → {PEOPLE[s['debtor_id']]} взяла\n\nСумма: {money(s['amount'])}\n🧾 {note or 'Без комментария'}\n\nТеперь это официально записано. Отмазка «ой, я забыла» не принимается 😂")
        return

    pending.pop(uid, None)
    bot.send_message(m.chat.id, "Что-то пошло не так, начинаем заново 😅", reply_markup=menu())


@bot.message_handler(func=lambda m: m.text == "◀️ Назад")
def back(m):
    if ok(m):
        pending.pop(m.from_user.id, None)
        bot.send_message(m.chat.id, "Вернулись. Финансовый пиздец под контролем 😌", reply_markup=menu())


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def run_health():
    port = int(os.getenv("PORT", "10000"))
    ThreadingHTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()


if __name__ == "__main__":
    threading.Thread(target=run_health, daemon=True).start()
    print("Finance bot started", flush=True)
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
