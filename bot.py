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
PEOPLE = {463620997: "Алина", 831511518: "Наташа"}
ALLOWED_USERS = set(PEOPLE) | {int(x) for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip().isdigit()}
conn = sqlite3.connect(DB, check_same_thread=False)
conn.execute("""CREATE TABLE IF NOT EXISTS expenses (
 id INTEGER PRIMARY KEY AUTOINCREMENT, month TEXT NOT NULL, payer_id INTEGER NOT NULL,
 payer_name TEXT NOT NULL, amount REAL NOT NULL, category TEXT NOT NULL DEFAULT '',
 note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, expense_type TEXT NOT NULL DEFAULT 'shared',
 debtor_id INTEGER, debtor_name TEXT)""")
cols = {r[1] for r in conn.execute("PRAGMA table_info(expenses)")}
for col, definition in [("expense_type", "TEXT NOT NULL DEFAULT 'shared'"), ("debtor_id", "INTEGER"), ("debtor_name", "TEXT")]:
    if col not in cols:
        conn.execute(f"ALTER TABLE expenses ADD COLUMN {col} {definition}")
conn.commit()
LOCK = threading.Lock()
pending = {}


def allowed(m): return m.from_user.id in ALLOWED_USERS

def name(uid): return PEOPLE.get(uid, "Пользователь")
def money(v): return f"{v:,.0f} ₽".replace(",", " ")
def month(): return datetime.now().strftime("%Y-%m")

def menu():
    k=types.ReplyKeyboardMarkup(resize_keyboard=True)
    k.row("➕ Добавить трату","💰 Баланс")
    k.row("📋 История","📊 Отчёты")
    k.row("🔎 Поиск","📈 Статистика")
    k.row("🛠 Управление","😂 Прикол")
    k.row("❓ Помощь")
    return k

def rows(where="", params=()):
    with LOCK:
        return conn.execute("SELECT id,payer_id,payer_name,amount,expense_type,debtor_id,debtor_name,note,created_at FROM expenses "+where+" ORDER BY created_at DESC,id DESC",params).fetchall()

def label(r):
    _,_,payer,amount,typ,_,debtor,note,created=r
    dt=datetime.fromisoformat(created).strftime("%d.%m %H:%M")
    kind={"shared":"🤝 50/50","personal":"🙋 Личная","debt":f"💸 Долг: {debtor} → {payer}"}.get(typ,typ)
    return f"{dt} · {payer} · {money(amount)} · {kind}\n   {note}"

def help_text():
    return ("🧠 Как пользоваться:\n\n➕ Трата → сумма → кто заплатил → тип.\n"
            "🤝 50/50 — делится поровну.\n🙋 Личная — только история, баланс не меняет.\n"
            "💸 Долг/заём — долг одного человека другому.\n\n"
            "🛠 Управление — отмена, удаление операции и очистка месяца.\n"
            "📊 Отчёты — день / неделя / месяц.\n🔎 Поиск и 📈 статистика тоже доступны.")

@bot.message_handler(commands=["start","help"])
def start(m):
    if not allowed(m): return bot.reply_to(m,"⛔ Этот бот только для Алины и Наташи 💅")
    bot.send_message(m.chat.id,"💅 Алина × Наташа — общий финансовый мозг.\n\nЯ запоминаю кто заплатил, что делится 50/50 и что является долгом.\n\nИ да, теперь ошибочную трату можно отменить 😌",reply_markup=menu())

@bot.message_handler(func=lambda m:m.text=="❓ Помощь")
def help_(m):
    if allowed(m): bot.send_message(m.chat.id,help_text(),reply_markup=menu())

@bot.message_handler(func=lambda m:m.text=="➕ Добавить трату")
def add(m):
    if not allowed(m): return
    pending[m.from_user.id]={"step":"amount"}
    bot.send_message(m.chat.id,"💸 Сколько потратили? Например: 3500")

@bot.message_handler(func=lambda m:m.text=="💰 Баланс")
def balance(m):
    if not allowed(m): return
    net={463620997:0.0,831511518:0.0}
    for r in rows():
        _,payer,_,amount,typ,debtor,_,_,_=r
        if payer not in net: continue
        if typ=="shared":
            other=831511518 if payer==463620997 else 463620997
            net[payer]+=amount/2; net[other]-=amount/2
        elif typ=="debt" and debtor in net:
            net[debtor]+=amount; net[payer]-=amount
    if abs(net[463620997])<.01: result="✨ Всё ровно. Никто никому не должен!"
    elif net[463620997]>0: result=f"💸 Наташа должна Алине: {money(net[463620997])}"
    else: result=f"💸 Алина должна Наташе: {money(-net[463620997])}"
    bot.send_message(m.chat.id,f"💰 БАЛАНС\n\n{result}",reply_markup=menu())

@bot.message_handler(func=lambda m:m.text=="📋 История")
def history(m):
    if not allowed(m): return
    rr=rows("WHERE month=?",(month(),))[:30]
    bot.send_message(m.chat.id,"📋 ИСТОРИЯ\n\n"+("\n\n".join(label(r) for r in rr) if rr else "Пусто 🫠"),reply_markup=menu())

@bot.message_handler(func=lambda m:m.text=="📊 Отчёты")
def reports(m):
    if not allowed(m): return
    k=types.ReplyKeyboardMarkup(resize_keyboard=True,one_time_keyboard=True)
    k.row("📅 Сегодня","📆 Неделя"); k.row("🗓 Месяц","📅 Выбрать месяц"); k.row("◀️ Назад")
    bot.send_message(m.chat.id,"Какой отчёт?",reply_markup=k)

@bot.message_handler(func=lambda m:m.text in {"📅 Сегодня","📆 Неделя","🗓 Месяц"})
def period(m):
    if not allowed(m): return
    now=datetime.now()
    if m.text=="📅 Сегодня": start=now.replace(hour=0,minute=0,second=0,microsecond=0); title=f"Сегодня {now:%d.%m.%Y}"
    elif m.text=="📆 Неделя": start=(now-timedelta(days=6)).replace(hour=0,minute=0,second=0,microsecond=0); title=f"Неделя {start:%d.%m}–{now:%d.%m}"
    else: start=now.replace(day=1,hour=0,minute=0,second=0,microsecond=0); title=f"Месяц {now:%m.%Y}"
    report(m.chat.id,title,rows("WHERE created_at>=?",(start.isoformat(timespec="seconds"),)))

def report(chat,title,rr):
    if not rr: return bot.send_message(chat,f"📊 {title}\n\nПока пусто 🥹",reply_markup=menu())
    total=sum(r[3] for r in rr); shared=sum(r[3] for r in rr if r[4]=="shared"); personal=sum(r[3] for r in rr if r[4]=="personal"); debt=sum(r[3] for r in rr if r[4]=="debt")
    bot.send_message(chat,f"📊 {title}\n\n💸 Всего: {money(total)}\n🤝 50/50: {money(shared)}\n🙋 Личные: {money(personal)}\n💸 Долги/займы: {money(debt)}\n🧾 Операций: {len(rr)}",reply_markup=menu())

@bot.message_handler(func=lambda m:m.text=="🔎 Поиск")
def search_start(m):
    if allowed(m): pending[m.from_user.id]={"step":"search"}; bot.send_message(m.chat.id,"🔎 Что ищем? Например: такси, продукты, долг, 5000")

@bot.message_handler(func=lambda m:m.text=="📈 Статистика")
def stats(m):
    if not allowed(m): return
    rr=rows("WHERE month=?",(month(),))
    if not rr: return bot.send_message(m.chat.id,"📈 Пока статистики нет 🥲",reply_markup=menu())
    total=sum(r[3] for r in rr); biggest=max(rr,key=lambda r:r[3])
    bot.send_message(m.chat.id,f"📈 СТАТИСТИКА — {datetime.now():%m.%Y}\n\n💸 Операций: {len(rr)}\n💰 Потрачено: {money(total)}\n🧾 Средняя: {money(total/len(rr))}\n🏆 Самая большая: {money(biggest[3])} — {biggest[7]}",reply_markup=menu())

@bot.message_handler(func=lambda m:m.text=="🛠 Управление")
def manage(m):
    if not allowed(m): return
    k=types.InlineKeyboardMarkup()
    k.add(types.InlineKeyboardButton("↩️ Отменить последнюю",callback_data="undo"))
    k.add(types.InlineKeyboardButton("🗑 Удалить операцию",callback_data="delete_choose"))
    k.add(types.InlineKeyboardButton("💣 Очистить месяц",callback_data="clear_month"))
    k.add(types.InlineKeyboardButton("🚨 Удалить всё",callback_data="clear_all"))
    bot.send_message(m.chat.id,"🛠 УПРАВЛЕНИЕ\n\nЗдесь можно исправить ошибку. Без паники — бухгалтерия не пострадает 😭",reply_markup=k)

@bot.callback_query_handler(func=lambda c:c.data=="undo")
def undo(c):
    if c.from_user.id not in ALLOWED_USERS: return
    rr=rows()[:1]
    if not rr: return bot.answer_callback_query(c.id,"Удалять нечего 😭",show_alert=True)
    r=rr[0]
    with LOCK: conn.execute("DELETE FROM expenses WHERE id=?",(r[0],)); conn.commit()
    bot.answer_callback_query(c.id,"Удалено 💅")
    bot.send_message(c.message.chat.id,f"↩️ Отменила последнюю операцию:\n\n{label(r)}\n\nБаланс пересчитан.",reply_markup=menu())

@bot.callback_query_handler(func=lambda c:c.data=="delete_choose")
def delete_choose(c):
    if c.from_user.id not in ALLOWED_USERS: return
    rr=rows()[:10]
    if not rr: return bot.answer_callback_query(c.id,"История пустая 🫠",show_alert=True)
    k=types.InlineKeyboardMarkup()
    for r in rr: k.add(types.InlineKeyboardButton(f"{r[2]} · {money(r[3])} · {r[7][:22]}",callback_data=f"del:{r[0]}"))
    bot.send_message(c.message.chat.id,"🗑 Выбери операцию для удаления:",reply_markup=k)
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c:c.data.startswith("del:"))
def delete_one(c):
    if c.from_user.id not in ALLOWED_USERS: return
    eid=int(c.data.split(":")[1]); rr=rows("WHERE id=?",(eid,))
    if not rr: return bot.answer_callback_query(c.id,"Эта операция уже удалена",show_alert=True)
    r=rr[0]
    with LOCK: conn.execute("DELETE FROM expenses WHERE id=?",(eid,)); conn.commit()
    bot.answer_callback_query(c.id,"Удалено 💅")
    bot.send_message(c.message.chat.id,f"🗑 Удалила:\n\n{label(r)}\n\nБаланс пересчитан.",reply_markup=menu())

@bot.callback_query_handler(func=lambda c:c.data in {"clear_month","clear_all"})
def clear_confirm(c):
    if c.from_user.id not in ALLOWED_USERS: return
    k=types.InlineKeyboardMarkup(); action=c.data
    k.add(types.InlineKeyboardButton("ДА, УДАЛИТЬ 😭",callback_data="confirm_"+action),types.InlineKeyboardButton("Нет, я передумала",callback_data="cancel_clear"))
    msg="весь текущий месяц" if action=="clear_month" else "ВСЮ историю навсегда"
    bot.send_message(c.message.chat.id,f"⚠️ Точно удалить {msg}?\n\nЭто уже не Ctrl+Z 🫠",reply_markup=k); bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c:c.data.startswith("confirm_clear_"))
def clear_confirmed(c):
    if c.from_user.id not in ALLOWED_USERS: return
    action=c.data.replace("confirm_","")
    with LOCK:
        if action=="clear_month": conn.execute("DELETE FROM expenses WHERE month=?",(month(),)); text="💣 Текущий месяц очищен."
        else: conn.execute("DELETE FROM expenses"); text="💣 ВСЯ история удалена. Бухгалтерия теперь девственна 😭"
        conn.commit()
    bot.answer_callback_query(c.id,"Готово")
    bot.send_message(c.message.chat.id,text,reply_markup=menu())

@bot.callback_query_handler(func=lambda c:c.data=="cancel_clear")
def cancel_clear(c):
    bot.answer_callback_query(c.id,"Фух 😮‍💨 Отмена"); bot.send_message(c.message.chat.id,"Фух, ничего не удаляю 😌",reply_markup=menu())

@bot.message_handler(func=lambda m:m.text=="😂 Прикол")
def joke(m):
    if allowed(m): bot.send_message(m.chat.id,"😂 Финансовая аналитика дня:\n\nЕсли кажется, что денег мало — это не кажется.\n\nНо зато теперь мы хотя бы знаем, КУДА они исчезли 💸",reply_markup=menu())

@bot.message_handler(func=lambda m:True)
def flow(m):
    if not allowed(m): return
    st=pending.get(m.from_user.id)
    if not st: return
    uid=m.from_user.id
    if st["step"]=="amount":
        try: value=Decimal(m.text.replace(" ","").replace(",",".")); assert value>0
        except (InvalidOperation,ValueError,AssertionError): return bot.send_message(m.chat.id,"Напиши сумму цифрами, например 2500")
        st.update(amount=float(value),step="payer")
        k=types.ReplyKeyboardMarkup(resize_keyboard=True,one_time_keyboard=True); k.row("👩🏻 Алина","👩🏼 Наташа")
        bot.send_message(m.chat.id,"👛 Кто заплатил?",reply_markup=k)
    elif st["step"]=="payer":
        payer=463620997 if "Алина" in m.text else 831511518 if "Наташа" in m.text else None
        if not payer: return bot.send_message(m.chat.id,"Выбери Алину или Наташу кнопкой 👇")
        st.update(payer_id=payer,step="type")
        k=types.ReplyKeyboardMarkup(resize_keyboard=True,one_time_keyboard=True); k.row("🤝 50/50","🙋 Личная"); k.row("💸 Долг / заём")
        bot.send_message(m.chat.id,"🧠 Как учитывать эту сумму?",reply_markup=k)
    elif st["step"]=="type":
        if m.text.startswith("🤝"): st.update(expense_type="shared",step="note")
        elif m.text.startswith("🙋"): st.update(expense_type="personal",step="note")
        elif m.text.startswith("💸"):
            st.update(expense_type="debt",step="debtor")
            other=831511518 if st["payer_id"]==463620997 else 463620997
            k=types.ReplyKeyboardMarkup(resize_keyboard=True,one_time_keyboard=True); k.add(types.KeyboardButton("👩🏻 Алина" if other==463620997 else "👩🏼 Наташа"))
            bot.send_message(m.chat.id,f"Кто должен вернуть {money(st['amount'])}?",reply_markup=k); return
        else: return
        bot.send_message(m.chat.id,"📝 Что это было? Напиши коротко, например: продукты, такси, кино")
    elif st["step"]=="debtor":
        st.update(debtor_id=463620997 if "Алина" in m.text else 831511518,step="note")
        bot.send_message(m.chat.id,"📝 За что долг? Например: заняла на квартиру")
    elif st["step"]=="note":
        st["note"]=m.text; created=datetime.now().isoformat(timespec="seconds")
        with LOCK:
            conn.execute("INSERT INTO expenses(month,payer_id,payer_name,amount,category,note,created_at,expense_type,debtor_id,debtor_name) VALUES(?,?,?,?,?,?,?,?,?,?)",(created[:7],st["payer_id"],name(st["payer_id"]),st["amount"],"",st["note"],created,st["expense_type"],st.get("debtor_id"),name(st["debtor_id"]) if st.get("debtor_id") else None)); conn.commit()
            eid=conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        pending.pop(uid,None)
        k=types.InlineKeyboardMarkup(); k.add(types.InlineKeyboardButton("↩️ Ой, отменить",callback_data=f"del:{eid}"))
        bot.send_message(m.chat.id,f"✅ Записала:\n\n{money(st['amount'])} · {name(st['payer_id'])}\n{st['note']}\n\nБаланс обновлён 💅",reply_markup=k)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type","text/plain; charset=utf-8"); self.end_headers(); self.wfile.write(b"Alina x Natasha finance bot is running")
    def log_message(self,format,*args): pass

def server():
    ThreadingHTTPServer(("0.0.0.0",int(os.getenv("PORT","10000"))),HealthHandler).serve_forever()

if __name__=="__main__":
    bot.delete_webhook(drop_pending_updates=True)
    threading.Thread(target=bot.infinity_polling,kwargs={"skip_pending":True},daemon=True).start()
    server()
