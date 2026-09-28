import os, sqlite3, threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import telebot
from telebot import types

TOKEN=os.getenv("TOKEN")
if not TOKEN: raise RuntimeError("TOKEN environment variable is required")
bot=telebot.TeleBot(TOKEN)
DB=os.getenv("DB_PATH","expenses.db")
ALINA,NATASHA=463620997,831511518
PEOPLE={ALINA:"Алина",NATASHA:"Наташа"}
lock=threading.RLock()
c=sqlite3.connect(DB,check_same_thread=False,timeout=30)
with lock:
 c.execute("""CREATE TABLE IF NOT EXISTS expenses(
 id INTEGER PRIMARY KEY AUTOINCREMENT, month TEXT NOT NULL,
 payer_id INTEGER NOT NULL,payer_name TEXT NOT NULL,amount REAL NOT NULL,
 category TEXT NOT NULL DEFAULT '',note TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,expense_type TEXT NOT NULL DEFAULT 'shared',
 debtor_id INTEGER,debtor_name TEXT,creditor_id INTEGER,creditor_name TEXT)""")
 c.execute("""CREATE TABLE IF NOT EXISTS settlements(
 id INTEGER PRIMARY KEY AUTOINCREMENT,closed_at TEXT NOT NULL,
 through_expense_id INTEGER NOT NULL,period TEXT NOT NULL,net_amount REAL NOT NULL,
 debtor_id INTEGER,creditor_id INTEGER,debtor_name TEXT,creditor_name TEXT)""")
 c.commit()
pending={}

def ok(m): return m.from_user.id in PEOPLE
def other(uid): return NATASHA if uid==ALINA else ALINA
def money(x): return f"{x:,.0f} ₽".replace(","," ")
def month(): return datetime.now().strftime("%Y-%m")

def menu():
 k=types.ReplyKeyboardMarkup(resize_keyboard=True)
 k.row("➕ Добавить трату","💰 Кто кому должен?")
 k.row("📋 История","📊 Отчёты")
 k.row("📈 Статистика","🔄 Взаиморасчёт")
 k.row("⚙️ Управление","❓ Инструкция")
 return k

def people_kb():
 k=types.ReplyKeyboardMarkup(resize_keyboard=True,one_time_keyboard=True)
 k.row("👩🏻 Алина","👩🏻 Наташа")
 return k

def rows(where="",p=()):
 with lock:
  return c.execute("SELECT id,payer_id,payer_name,amount,expense_type,debtor_id,debtor_name,note,created_at,creditor_id,creditor_name FROM expenses "+where+" ORDER BY created_at DESC,id DESC",p).fetchall()

def cutoff():
 with lock:
  r=c.execute("SELECT through_expense_id FROM settlements ORDER BY id DESC LIMIT 1").fetchone()
 return r[0] if r else 0

def openrows(): return rows("WHERE id>?",(cutoff(),))

def calc(rr=None):
 rr=openrows() if rr is None else rr
 n={ALINA:0.0,NATASHA:0.0}
 for r in rr:
  _,payer,_,amount,kind,debtor,_,_,_,creditor,_=r
  if kind=="shared" and payer in n:
   o=other(payer); n[payer]+=amount/2; n[o]-=amount/2
  elif kind=="debt" and debtor in n and creditor in n:
   n[debtor]+=amount; n[creditor]-=amount
 return n

def balance_text():
 x=calc()[ALINA]
 if abs(x)<.01: return "✨ Всё ровно. Никто никому не должен!"
 return f"💸 Наташа должна Алине: {money(x)}" if x>0 else f"💸 Алина должна Наташе: {money(-x)}"

def label(r):
 _,_,payer,amount,kind,debtor,dname,note,created,creditor,cname=r
 kindtext="🤝 50/50" if kind=="shared" else f"💸 Долг: {cname} → {dname}"
 return f"{datetime.fromisoformat(created):%d.%m %H:%M} · {payer} · {money(amount)} · {kindtext}\n   {note or 'Без комментария'}"

def notify(uid,text):
 try: bot.send_message(uid,text)
 except Exception as e: print(f"Notification error: {e}",flush=True)

@bot.message_handler(commands=["start"])
def start(m):
 if not ok(m): return bot.reply_to(m,"⛔ Этот бот только для Алины и Наташи 💅")
 bot.send_message(m.chat.id, """ЗДАРОВА, ЧЕЛИХА 👋🏻

Это ваш личный финансовый бот для АЛИНЫ И НАТАШИ.

Чтобы вы не запутались в деньгах, не вспоминали «а кто, блять, тогда платил?» и не сидели с калькулятором 😂

💸 ТРАТА — сумма → 50/50 → кто заплатил → что это было.
💰 ДОЛГ / ЗАЁМ — сумма → кто дал → второй автоматически считается тем, кто взял.
🧮 ВЗАИМОРАСЧЁТ — я сама считаю чистую разницу.
⚙️ УПРАВЛЕНИЕ — исправить косяк.
📊 ОТЧЁТЫ — день, неделя, месяц.

ЛАЙФ КУ БИ ДРИМ 💅🏻""",reply_markup=menu())

@bot.message_handler(func=lambda m:m.text in ("❓ Инструкция","❓ Помощь"))
def help_(m):
 if ok(m): bot.send_message(m.chat.id,"""🧠 ИНСТРУКЦИЯ

➕ Добавить трату → сумма → 50/50 или долг.
🤝 50/50 → кто заплатил → комментарий.
💸 Долг → кто дал → второй человек автоматически тот, кто взял → комментарий.
💰 Кто кому должен? → текущий баланс.
🔄 Взаиморасчёт → закрывает текущий период, история остаётся.
⚙️ Управление → отмена/удаление/очистка.

Если финансовый пиздец — спокойно, я разберусь 😂

ЛАЙФ КУ БИ ДРИМ.""",reply_markup=menu())

@bot.message_handler(func=lambda m:m.text=="➕ Добавить трату")
def add(m):
 if ok(m):
  pending[m.from_user.id]={"step":"amount"}
  bot.send_message(m.chat.id,"💸 Сколько потратили?\n\nНапиши сумму цифрами, например: 3500")

@bot.message_handler(func=lambda m:m.text=="💰 Кто кому должен?")
def balance(m):
 if ok(m): bot.send_message(m.chat.id,"💰 ТЕКУЩИЙ ВЗАИМОРАСЧЁТ\n\n"+balance_text(),reply_markup=menu())

@bot.message_handler(func=lambda m:m.text=="🔄 Взаиморасчёт")
def settlement(m):
 if not ok(m): return
 rr=openrows(); x=calc(rr)[ALINA]
 text="✨ Сейчас всё ровно." if not rr or abs(x)<.01 else (f"Наташа должна Алине: {money(x)}" if x>0 else f"Алина должна Наташе: {money(-x)}")
 k=types.InlineKeyboardMarkup()
 if rr: k.add(types.InlineKeyboardButton("🔒 Закрыть взаиморасчёт",callback_data="settle_confirm"))
 bot.send_message(m.chat.id,"🔄 ВЗАИМОРАСЧЁТ\n\n"+text+"\n\nИстория не удаляется.",reply_markup=k if rr else menu())

@bot.callback_query_handler(func=lambda q:q.data=="settle_confirm")
def settle_confirm(q):
 if q.from_user.id not in PEOPLE: return
 x=calc(openrows())[ALINA]
 text="✨ Никто никому не должен." if abs(x)<.01 else (f"Наташа → Алине: {money(x)}" if x>0 else f"Алина → Наташе: {money(-x)}")
 k=types.InlineKeyboardMarkup(); k.row(types.InlineKeyboardButton("✅ Да, закрыть",callback_data="settle_yes"),types.InlineKeyboardButton("❌ Отмена",callback_data="settle_no"))
 bot.send_message(q.message.chat.id,"🔒 Закрываем взаиморасчёт?\n\n"+text+"\n\nВсе траты останутся в истории. Баланс станет 0 ₽.",reply_markup=k)
 bot.answer_callback_query(q.id)

@bot.callback_query_handler(func=lambda q:q.data=="settle_yes")
def settle_yes(q):
 if q.from_user.id not in PEOPLE: return
 rr=openrows()
 if not rr: return bot.answer_callback_query(q.id,"Нечего закрывать",show_alert=True)
 x=calc(rr)[ALINA]; debtor=NATASHA if x>0 else ALINA if x<0 else None; creditor=ALINA if x>0 else NATASHA if x<0 else None
 with lock:
  c.execute("INSERT INTO settlements(closed_at,through_expense_id,period,net_amount,debtor_id,creditor_id,debtor_name,creditor_name) VALUES(?,?,?,?,?,?,?,?)",(datetime.now().isoformat(timespec="seconds"),max(r[0] for r in rr),month(),abs(x),debtor,creditor,PEOPLE.get(debtor),PEOPLE.get(creditor))); c.commit()
 result="✨ Никто никому не должен." if abs(x)<.01 else f"💸 {PEOPLE[debtor]} → {PEOPLE[creditor]}: {money(abs(x))}"
 bot.answer_callback_query(q.id,"Закрыто 💅"); bot.send_message(q.message.chat.id,"🧹 ФИНАНСОВЫЙ СТОЛ ОЧИЩЕН\n\n"+result+"\n\nСтарые траты сохранены. Новый период начался с нуля.\n\nЛАЙФ КУ БИ ДРИМ 💅🏻",reply_markup=menu())
 notify(other(q.from_user.id),"🧹 АЛИНА И НАТАША ЗАКРЫЛИ ВЗАИМОРАСЧЁТ\n\n"+result+"\n\nСтарые траты сохранены, новый период — с нуля.\n\nДо следующего финансового пиздеца 😂")

@bot.callback_query_handler(func=lambda q:q.data=="settle_no")
def settle_no(q): bot.answer_callback_query(q.id,"Оставляем 😌")

@bot.message_handler(func=lambda m:m.text=="📋 История")
def history(m):
 if ok(m):
  rr=rows("WHERE month=?",(month(),))[:50]
  bot.send_message(m.chat.id,"📋 ИСТОРИЯ\n\n"+("\n\n".join(label(r) for r in rr) if rr else "Пока пусто. Бухгалтерия девственно чиста 😭"),reply_markup=menu())

@bot.message_handler(func=lambda m:m.text=="📊 Отчёты")
def reports(m):
 if ok(m):
  k=types.ReplyKeyboardMarkup(resize_keyboard=True,one_time_keyboard=True); k.row("📅 Сегодня","📆 Неделя"); k.row("🗓 Месяц","◀️ Назад")
  bot.send_message(m.chat.id,"📊 Какой отчёт показать?",reply_markup=k)

@bot.message_handler(func=lambda m:m.text in {"📅 Сегодня","📆 Неделя","🗓 Месяц"})
def period(m):
 if not ok(m): return
 now=datetime.now()
 start=now.replace(day=1,hour=0,minute=0,second=0,microsecond=0) if m.text=="🗓 Месяц" else ((now-timedelta(days=6)).replace(hour=0,minute=0,second=0,microsecond=0) if m.text=="📆 Неделя" else now.replace(hour=0,minute=0,second=0,microsecond=0))
 rr=rows("WHERE created_at>=?",(start.isoformat(timespec="seconds"),))
 bot.send_message(m.chat.id,f"📊 ОТЧЁТ\n\n💸 Операций: {len(rr)}\n💰 Сумма операций: {money(sum(r[3] for r in rr))}",reply_markup=menu())

@bot.message_handler(func=lambda m:m.text=="📈 Статистика")
def stats(m):
 if ok(m):
  rr=rows("WHERE month=?",(month(),)); shared=sum(r[3] for r in rr if r[4]=="shared"); debts=sum(r[3] for r in rr if r[4]=="debt")
  bot.send_message(m.chat.id,f"📈 СТАТИСТИКА {month()}\n\n💸 Операций: {len(rr)}\n🤝 50/50: {money(shared)}\n💸 Долги/займы: {money(debts)}\n\n{balance_text()}",reply_markup=menu())

@bot.message_handler(func=lambda m:m.text=="⚙️ Управление")
def manage(m):
 if ok(m):
  k=types.InlineKeyboardMarkup(); k.add(types.InlineKeyboardButton("↩️ Отменить последнюю",callback_data="undo")); k.add(types.InlineKeyboardButton("🗑 Удалить операцию",callback_data="delete_choose")); k.add(types.InlineKeyboardButton("🧹 Очистить текущий период",callback_data="clear_current")); k.add(types.InlineKeyboardButton("🚨 Удалить всё незакрытое",callback_data="clear_all"))
  bot.send_message(m.chat.id,"⚙️ УПРАВЛЕНИЕ\n\nЗдесь исправляем косяки. Без случайной отмены после каждой траты 😂",reply_markup=k)

@bot.callback_query_handler(func=lambda q:q.data=="undo")
def undo(q):
 if q.from_user.id not in PEOPLE: return
 rr=openrows()
 if not rr: return bot.answer_callback_query(q.id,"Нечего отменять",show_alert=True)
 r=rr[0]
 with lock: c.execute("DELETE FROM expenses WHERE id=?",(r[0],)); c.commit()
 bot.answer_callback_query(q.id,"Удалено 💅"); bot.send_message(q.message.chat.id,"↩️ Последняя операция отменена.\n\n"+label(r),reply_markup=menu()); notify(other(q.from_user.id),f"↩️ {PEOPLE[q.from_user.id]} отменила последнюю операцию.\n\n{label(r)}")

@bot.callback_query_handler(func=lambda q:q.data=="delete_choose")
def delete_choose(q):
 if q.from_user.id not in PEOPLE: return
 rr=openrows()[:10]
 if not rr: return bot.answer_callback_query(q.id,"Нечего удалять",show_alert=True)
 k=types.InlineKeyboardMarkup()
 for r in rr: k.add(types.InlineKeyboardButton(f"#{r[0]} · {money(r[3])} · {r[7] or 'без названия'}",callback_data=f"delete:{r[0]}"))
 k.add(types.InlineKeyboardButton("❌ Закрыть",callback_data="manage_close")); bot.send_message(q.message.chat.id,"🗑 Что удалить?",reply_markup=k); bot.answer_callback_query(q.id)

@bot.callback_query_handler(func=lambda q:q.data.startswith("delete:"))
def delete_one(q):
 if q.from_user.id not in PEOPLE: return
 try: eid=int(q.data.split(":",1)[1])
 except ValueError: return bot.answer_callback_query(q.id,"Ошибка",show_alert=True)
 rr=rows("WHERE id=?",(eid,))
 if not rr: return bot.answer_callback_query(q.id,"Уже удалено",show_alert=True)
 r=rr[0]
 with lock: c.execute("DELETE FROM expenses WHERE id=?",(eid,)); c.commit()
 bot.answer_callback_query(q.id,"Удалено 💅"); bot.send_message(q.message.chat.id,"🗑 Удалила:\n\n"+label(r),reply_markup=menu()); notify(other(q.from_user.id),f"🗑 {PEOPLE[q.from_user.id]} удалила операцию:\n\n{label(r)}")

@bot.callback_query_handler(func=lambda q:q.data=="clear_current")
def clear_current(q):
 if q.from_user.id not in PEOPLE: return
 k=types.InlineKeyboardMarkup(); k.row(types.InlineKeyboardButton("🔥 Да, снести",callback_data="clear_current_yes"),types.InlineKeyboardButton("❌ Нет",callback_data="manage_close"))
 bot.send_message(q.message.chat.id,"⚠️ Удалить все незакрытые траты текущего периода?",reply_markup=k); bot.answer_callback_query(q.id)

@bot.callback_query_handler(func=lambda q:q.data=="clear_current_yes")
def clear_current_yes(q):
 if q.from_user.id not in PEOPLE: return
 ids=[r[0] for r in openrows()]
 with lock:
  if ids: c.executemany("DELETE FROM expenses WHERE id=?",[(i,) for i in ids]); c.commit()
 bot.answer_callback_query(q.id,"Снесено 💥"); bot.send_message(q.message.chat.id,f"🧹 Удалила {len(ids)} операций.\n\nФинансовый стол чист. С чистого листа, блять 😂",reply_markup=menu()); notify(other(q.from_user.id),f"🧹 {PEOPLE[q.from_user.id]} очистила текущий период. Удалено: {len(ids)}.")

@bot.callback_query_handler(func=lambda q:q.data=="clear_all")
def clear_all(q):
 if q.from_user.id not in PEOPLE: return
 k=types.InlineKeyboardMarkup(); k.row(types.InlineKeyboardButton("🚨 ДА, УДАЛИТЬ",callback_data="clear_all_yes"),types.InlineKeyboardButton("❌ Отмена",callback_data="manage_close"))
 bot.send_message(q.message.chat.id,"🚨 Это удалит ВСЕ незакрытые операции. Точно?",reply_markup=k); bot.answer_callback_query(q.id)

@bot.callback_query_handler(func=lambda q:q.data=="clear_all_yes")
def clear_all_yes(q):
 if q.from_user.id not in PEOPLE: return
 with lock: c.execute("DELETE FROM expenses WHERE id>?",(cutoff(),)); c.commit()
 bot.answer_callback_query(q.id,"Готово"); bot.send_message(q.message.chat.id,"🧹 Всё незакрытое удалено. Старые закрытые периоды не трогала.",reply_markup=menu()); notify(other(q.from_user.id),f"🧹 {PEOPLE[q.from_user.id]} удалила все незакрытые операции.")

@bot.callback_query_handler(func=lambda q:q.data=="manage_close")
def manage_close(q):
 bot.answer_callback_query(q.id,"Закрыли 😌")
 try: bot.delete_message(q.message.chat.id,q.message.message_id)
 except Exception: pass

@bot.message_handler(func=lambda m:m.from_user.id in PEOPLE and m.from_user.id in pending)
def process_pending(m):
 uid=m.from_user.id; s=pending[uid]; step=s.get("step"); text=m.text or ""
 if step=="amount":
  try: amount=float(text.replace(" ","").replace(",","."))
  except ValueError: return bot.send_message(m.chat.id,"🤨 Напиши сумму цифрами, например 3500.")
  if amount<=0: return bot.send_message(m.chat.id,"Ноль рублей — это уже не трата 😂 Напиши сумму больше нуля.")
  s.update(amount=amount,step="type"); k=types.ReplyKeyboardMarkup(resize_keyboard=True,one_time_keyboard=True); k.row("🤝 50/50","💸 Долг / заём")
  return bot.send_message(m.chat.id,"🧾 Как учитывать эту сумму?",reply_markup=k)
 if step=="type":
  if text.startswith("🤝"): s.update(expense_type="shared",step="payer"); return bot.send_message(m.chat.id,"👤 Кто заплатил?",reply_markup=people_kb())
  if text.startswith("💸"): s.update(expense_type="debt",step="creditor"); return bot.send_message(m.chat.id,"💸 Кто ДАЛ деньги?",reply_markup=people_kb())
  return bot.send_message(m.chat.id,"Выбери тип кнопкой 👆")
 if step=="payer":
  p=ALINA if "Алина" in text else NATASHA if "Наташа" in text else None
  if not p: return bot.send_message(m.chat.id,"Выбери Алину или Наташу 👆")
  s.update(payer_id=p,payer_name=PEOPLE[p],step="note"); return bot.send_message(m.chat.id,"🧾 Что это было?\n\nНапиши коротко или «пропустить».")
 if step=="creditor":
  p=ALINA if "Алина" in text else NATASHA if "Наташа" in text else None
  if not p: return bot.send_message(m.chat.id,"Выбери Алину или Наташу 👆")
  d=other(p); s.update(creditor_id=p,creditor_name=PEOPLE[p],debtor_id=d,debtor_name=PEOPLE[d],payer_id=p,payer_name=PEOPLE[p],step="note")
  return bot.send_message(m.chat.id,f"💸 {PEOPLE[p]} дала деньги → {PEOPLE[d]} взяла.\n\n🧾 Что это было?\nНапиши коротко или «пропустить».")
 if step=="note":
  note="" if text.strip().lower() in {"пропустить","-","нет"} else text.strip(); et=s.get("expense_type","shared")
  if et not in {"shared","debt"}: et="shared"
  payer=s.get("payer_id",uid); now=datetime.now().isoformat(timespec="seconds")
  with lock:
   c.execute("INSERT INTO expenses(month,payer_id,payer_name,amount,note,created_at,expense_type,debtor_id,debtor_name,creditor_id,creditor_name) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(month(),payer,PEOPLE[payer],s["amount"],note,now,et,s.get("debtor_id"),s.get("debtor_name"),s.get("creditor_id"),s.get("creditor_name"))); c.commit(); eid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
  r=rows("WHERE id=?",(eid,))[0]; pending.pop(uid,None)
  bot.send_message(m.chat.id,"✅ Записала!\n\n"+label(r)+"\n\n💰 "+balance_text(),reply_markup=menu())
  if et=="shared": notify(other(uid),f"🚨 {PEOPLE[uid].upper()} ЗАНЕСЛА ТРАТУ\n\n{label(r)}\n\nС тебя: {money(s['amount']/2)} 😂")
  else: notify(other(uid),f"💸 ФИНАНСОВАЯ ДРАМА\n\n{PEOPLE[s['creditor_id']]} дала → {PEOPLE[s['debtor_id']]} взяла\n\nСумма: {money(s['amount'])}\n🧾 {note or 'Без комментария'}\n\nТеперь это официально записано 😂")
  return
 pending.pop(uid,None); bot.send_message(m.chat.id,"Что-то пошло не так, начинаем заново 😅",reply_markup=menu())

@bot.message_handler(func=lambda m:m.text=="◀️ Назад")
def back(m):
 if ok(m): pending.pop(m.from_user.id,None); bot.send_message(m.chat.id,"Вернулись. Финансовый пиздец под контролем 😌",reply_markup=menu())

class HealthHandler(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
 def log_message(self,*args): pass

def run_health(): ThreadingHTTPServer(("0.0.0.0",int(os.getenv("PORT","10000"))),HealthHandler).serve_forever()

if __name__=="__main__":
 threading.Thread(target=run_health,daemon=True).start()
 print("Finance bot started",flush=True)
 bot.infinity_polling(skip_pending=True,timeout=30,long_polling_timeout=30)
