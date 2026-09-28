import os,sqlite3,threading
from datetime import datetime,timedelta
from decimal import Decimal,InvalidOperation
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import telebot
from telebot import types
TOKEN=os.getenv('TOKEN')
if not TOKEN: raise RuntimeError('TOKEN environment variable is required')
bot=telebot.TeleBot(TOKEN); DB=os.getenv('DB_PATH','expenses.db')
PEOPLE={463620997:'Алина',831511518:'Наташа'}
ALLOWED_USERS=set(PEOPLE)|{int(x) for x in os.getenv('ALLOWED_USERS','').split(',') if x.strip().isdigit()}
c=sqlite3.connect(DB,check_same_thread=False); L=threading.Lock(); pending={}
c.execute('''CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY AUTOINCREMENT,month TEXT NOT NULL,payer_id INTEGER NOT NULL,payer_name TEXT NOT NULL,amount REAL NOT NULL,category TEXT NOT NULL DEFAULT '',note TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,expense_type TEXT NOT NULL DEFAULT 'shared',debtor_id INTEGER,debtor_name TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS settlements(id INTEGER PRIMARY KEY AUTOINCREMENT,closed_at TEXT NOT NULL,through_expense_id INTEGER NOT NULL,period TEXT NOT NULL,net_amount REAL NOT NULL,debtor_id INTEGER,creditor_id INTEGER,debtor_name TEXT,creditor_name TEXT)'''); c.commit()
def ok(m): return m.from_user.id in ALLOWED_USERS
def money(x): return f'{x:,.0f} ₽'.replace(',',' ')
def month(): return datetime.now().strftime('%Y-%m')
def menu():
 k=types.ReplyKeyboardMarkup(resize_keyboard=True); k.row('➕ Добавить трату','💰 Баланс'); k.row('📋 История','📊 Отчёты'); k.row('🔎 Поиск','📈 Статистика'); k.row('🛠 Управление','🔄 Взаиморасчёт'); k.row('📚 Архив','😂 Прикол'); k.row('❓ Помощь'); return k
def rows(where='',p=()): return c.execute('SELECT id,payer_id,payer_name,amount,expense_type,debtor_id,debtor_name,note,created_at FROM expenses '+where+' ORDER BY created_at DESC,id DESC',p).fetchall()
def cutoff():
 r=c.execute('SELECT through_expense_id FROM settlements ORDER BY id DESC LIMIT 1').fetchone(); return r[0] if r else 0
def openrows(): return rows('WHERE id>?',(cutoff(),))
def calc(rr=None):
 rr=rr if rr is not None else openrows(); n={463620997:0.,831511518:0.}
 for r in rr:
  _,payer,_,a,t,d,*_=r
  if payer not in n: continue
  if t=='shared': o=831511518 if payer==463620997 else 463620997; n[payer]+=a/2;n[o]-=a/2
  elif t=='debt' and d in n: n[d]+=a;n[payer]-=a
 return n
def bal():
 n=calc(); x=n[463620997]
 return '✨ Всё ровно. Никто никому не должен!' if abs(x)<.01 else (f'💸 Наташа должна Алине: {money(x)}' if x>0 else f'💸 Алина должна Наташе: {money(-x)}')
def label(r):
 _,_,p,a,t,_,d,note,created=r; kind={'shared':'🤝 50/50','personal':'🙋 Личная','debt':f'💸 Долг: {d} → {p}'}.get(t,t); return f"{datetime.fromisoformat(created):%d.%m %H:%M} · {p} · {money(a)} · {kind}\n   {note}"
@bot.message_handler(commands=['start','help'])
def start(m):
 if ok(m): bot.send_message(m.chat.id,'💅 Алина × Наташа — общий финансовый мозг.\n\nЯ считаю 50/50, личные траты и долги. История сохраняется.',reply_markup=menu())
 else: bot.reply_to(m,'⛔ Этот бот только для Алины и Наташи 💅')
@bot.message_handler(func=lambda m:m.text=='❓ Помощь')
def help_(m):
 if ok(m): bot.send_message(m.chat.id,'🧠 ➕ Трата → сумма → кто заплатил → 50/50 / личная / долг.\n\n🔄 Взаиморасчёт считает чистую разницу. 🔒 Закрытие сохраняет историю и обнуляет текущий баланс.\n📚 Архив хранит закрытые периоды.',reply_markup=menu())
@bot.message_handler(func=lambda m:m.text=='➕ Добавить трату')
def add(m):
 if ok(m): pending[m.from_user.id]={'step':'amount'}; bot.send_message(m.chat.id,'💸 Сколько потратили? Например: 3500')
@bot.message_handler(func=lambda m:m.text=='💰 Баланс')
def balance(m):
 if ok(m): bot.send_message(m.chat.id,'💰 ТЕКУЩИЙ ВЗАИМОРАСЧЁТ\n\n'+bal(),reply_markup=menu())
@bot.message_handler(func=lambda m:m.text=='🔄 Взаиморасчёт')
def settlement(m):
 if not ok(m): return
 rr=openrows(); n=calc(rr); x=n[463620997]; text='🔄 ВЗАИМОРАСЧЁТ\n\n'+('✨ Сейчас всё ровно.' if not rr or abs(x)<.01 else (f'Наташа должна Алине: {money(x)}' if x>0 else f'Алина должна Наташе: {money(-x)}'))
 k=types.InlineKeyboardMarkup();
 if rr:k.add(types.InlineKeyboardButton('🔒 Закрыть взаиморасчёт',callback_data='settle_confirm'))
 bot.send_message(m.chat.id,text+'\n\nИстория не удаляется.',reply_markup=k if rr else menu())
@bot.callback_query_handler(func=lambda q:q.data=='settle_confirm')
def settle_confirm(q):
 if q.from_user.id not in ALLOWED_USERS:return
 rr=openrows();x=calc(rr)[463620997]; text='✨ Никто никому не должен.' if abs(x)<.01 else (f'Наташа → Алине: {money(x)}' if x>0 else f'Алина → Наташе: {money(-x)}')
 k=types.InlineKeyboardMarkup();k.add(types.InlineKeyboardButton('✅ Да, закрыть',callback_data='settle_yes'),types.InlineKeyboardButton('❌ Отмена',callback_data='settle_no'));bot.send_message(q.message.chat.id,f'🔒 Закрываем период?\n\n{text}\n\nВсе траты останутся в архиве, текущий баланс станет 0 ₽.',reply_markup=k);bot.answer_callback_query(q.id)
@bot.callback_query_handler(func=lambda q:q.data=='settle_yes')
def settle_yes(q):
 if q.from_user.id not in ALLOWED_USERS:return
 rr=openrows();n=calc(rr);x=n[463620997]
 if not rr:return bot.answer_callback_query(q.id,'Нечего закрывать',show_alert=True)
 through=max(r[0] for r in rr); names={**PEOPLE}; debtor=831511518 if x>0 else 463620997 if x<0 else None; creditor=463620997 if x>0 else 831511518 if x<0 else None
 c.execute('INSERT INTO settlements(closed_at,through_expense_id,period,net_amount,debtor_id,creditor_id,debtor_name,creditor_name) VALUES(?,?,?,?,?,?,?,?)',(datetime.now().isoformat(timespec='seconds'),through,month(),abs(x),debtor,creditor,names.get(debtor),names.get(creditor)));c.commit();bot.answer_callback_query(q.id,'Закрыто 💅');res='✨ Никто никому не должен.' if abs(x)<.01 else f'💸 {names[debtor]} → {names[creditor]}: {money(abs(x))}';bot.send_message(q.message.chat.id,f'🔒 ПЕРИОД ЗАКРЫТ\n\n{res}\n\n📚 История сохранена.\n🆕 Новый баланс: 0 ₽',reply_markup=menu())
@bot.callback_query_handler(func=lambda q:q.data=='settle_no')
def settle_no(q): bot.answer_callback_query(q.id,'Оставляем 😌')
@bot.message_handler(func=lambda m:m.text=='📚 Архив')
def archive(m):
 if not ok(m):return
 ss=c.execute('SELECT closed_at,period,net_amount,debtor_name,creditor_name FROM settlements ORDER BY id DESC LIMIT 12').fetchall()
 if not ss:return bot.send_message(m.chat.id,'📚 Архив пока пуст.',reply_markup=menu())
 out=['📚 АРХИВ ВЗАИМОРАСЧЁТОВ']
 for closed,p,a,d,cr in ss:out.append(f'🗓 {p} · закрыт {datetime.fromisoformat(closed):%d.%m.%Y}\n   '+('✨ 0 ₽' if a<.01 else f'{d} → {cr}: {money(a)}'))
 bot.send_message(m.chat.id,'\n\n'.join(out),reply_markup=menu())
@bot.message_handler(func=lambda m:m.text=='📋 История')
def history(m):
 if ok(m):
  rr=rows('WHERE month=?',(month(),))[:30];bot.send_message(m.chat.id,'📋 ИСТОРИЯ\n\n'+('\n\n'.join(label(r) for r in rr) if rr else 'Пусто 🫠'),reply_markup=menu())
@bot.message_handler(func=lambda m:m.text=='📊 Отчёты')
def reports(m):
 if ok(m):
  k=types.ReplyKeyboardMarkup(resize_keyboard=True,one_time_keyboard=True);k.row('📅 Сегодня','📆 Неделя');k.row('🗓 Месяц','◀️ Назад');bot.send_message(m.chat.id,'Какой отчёт?',reply_markup=k)
@bot.message_handler(func=lambda m:m.text in {'📅 Сегодня','📆 Неделя','🗓 Месяц'})
def period(m):
 if not ok(m):return
 now=datetime.now();start=now.replace(day=1,hour=0,minute=0,second=0,microsecond=0) if m.text=='🗓 Месяц' else (now-timedelta(days=6)).replace(hour=0,minute=0,second=0,microsecond=0) if m.text=='📆 Неделя' else now.replace(hour=0,minute=0,second=0,microsecond=0);rr=rows('WHERE created_at>=?',(start.isoformat(timespec='seconds'),)); total=sum(r[3] for r in rr);bot.send_message(m.chat.id,f'📊 ОТЧЁТ\n\n💸 Всего: {money(total)}\n🧾 Операций: {len(rr)}',reply_markup=menu())
@bot.message_handler(func=lambda m:m.text=='📈 Статистика')
def stats(m):
 if ok(m):
  rr=rows('WHERE month=?',(month(),));total=sum(r[3] for r in rr);bot.send_message(m.chat.id,f'📈 СТАТИСТИКА {month()}\n\n💸 Потрачено: {money(total)}\n🧾 Операций: {len(rr)}',reply_markup=menu())
@bot.message_handler(func=lambda m:m.text=='🛠 Управление')
def manage(m):
 if ok(m):
  k=types.InlineKeyboardMarkup();k.add(types.InlineKeyboardButton('↩️ Отменить последнюю',callback_data='undo'));k.add(types.InlineKeyboardButton('🗑 Удалить операцию',callback_data='delete_choose'));k.add(types.InlineKeyboardButton('💣 Очистить месяц',callback_data='clear_month'));k.add(types.InlineKeyboardButton('🚨 Удалить всё',callback_data='clear_all'));bot.send_message(m.chat.id,'🛠 УПРАВЛЕНИЕ\n\nБез паники — бухгалтерия не пострадает 😭',reply_markup=k)
@bot.callback_query_handler(func=lambda q:q.data=='undo')
def undo(q):
 if q.from_user.id not in ALLOWED_USERS:return
 rr=openrows()[:1]
 if not rr:return bot.answer_callback_query(q.id,'Удалять нечего 😭',show_alert=True)
 c.execute('DELETE FROM expenses WHERE id=?',(rr[0][0],));c.commit();bot.answer_callback_query(q.id,'Удалено 💅');bot.send_message(q.message.chat.id,'↩️ Отменила:\n\n'+label(rr[0])+'\n\nБаланс пересчитан.',reply_markup=menu())
@bot.callback_query_handler(func=lambda q:q.data=='delete_choose')
def delete_choose(q):
 if q.from_user.id not in ALLOWED_USERS:return
 rr=openrows()[:10]
 if not rr:return bot.answer_callback_query(q.id,'Новых операций нет',show_alert=True)
 k=types.InlineKeyboardMarkup();[k.add(types.InlineKeyboardButton(f'{r[2]} · {money(r[3])} · {r[7][:20]}',callback_data=f'del:{r[0]}')) for r in rr];bot.send_message(q.message.chat.id,'🗑 Выбери операцию:',reply_markup=k);bot.answer_callback_query(q.id)
@bot.callback_query_handler(func=lambda q:q.data.startswith('del:'))
def delete_one(q):
 if q.from_user.id not in ALLOWED_USERS:return
 eid=int(q.data.split(':')[1]);rr=rows('WHERE id=?',(eid,))
 if not rr or eid<=cutoff():return bot.answer_callback_query(q.id,'Эта операция уже в закрытом архиве 🔒',show_alert=True)
 c.execute('DELETE FROM expenses WHERE id=?',(eid,));c.commit();bot.answer_callback_query(q.id,'Удалено 💅');bot.send_message(q.message.chat.id,'🗑 Удалила:\n\n'+label(rr[0])+'\n\nБаланс пересчитан.',reply_markup=menu())
@bot.callback_query_handler(func=lambda q:q.data in {'clear_month','clear_all'})
def clear(q):
 if q.from_user.id not in ALLOWED_USERS:return
 k=types.InlineKeyboardMarkup();k.add(types.InlineKeyboardButton('ДА, УДАЛИТЬ 😭',callback_data='confirm_'+q.data),types.InlineKeyboardButton('Нет',callback_data='cancel_clear'));bot.send_message(q.message.chat.id,'⚠️ Точно удалить? Закрытый архив не трогаю 🔒',reply_markup=k);bot.answer_callback_query(q.id)
@bot.callback_query_handler(func=lambda q:q.data.startswith('confirm_clear_'))
def clear_yes(q):
 if q.from_user.id not in ALLOWED_USERS:return
 a=q.data.replace('confirm_',''); c.execute('DELETE FROM expenses WHERE id>? AND '+("month=?" if a=='clear_month' else '1=1'),((cutoff(),month()) if a=='clear_month' else (cutoff(),)));c.commit();bot.answer_callback_query(q.id,'Готово');bot.send_message(q.message.chat.id,'💣 Незакрытая история очищена. Архив сохранён 🔒',reply_markup=menu())
@bot.callback_query_handler(func=lambda q:q.data=='cancel_clear')
def cancel_clear(q):
 bot.answer_callback_query(q.id,'Фух 😮‍💨');bot.send_message(q.message.chat.id,'Ничего не удаляю 😌',reply_markup=menu())
@bot.message_handler(func=lambda m:m.text=='🔎 Поиск')
def search(m):
 if ok(m):pending[m.from_user.id]={'step':'search'};bot.send_message(m.chat.id,'🔎 Что ищем?')
@bot.message_handler(func=lambda m:m.text=='😂 Прикол')
def joke(m):
 if ok(m):bot.send_message(m.chat.id,'😂 Финансовая аналитика: если кажется, что денег мало — это не кажется. Но теперь мы знаем, куда они исчезли 💸',reply_markup=menu())
@bot.message_handler(func=lambda m:True)
def flow(m):
 if not ok(m):return
 s=pending.get(m.from_user.id)
 if not s:return
 if s['step']=='search':
  q=m.text.lower();rr=rows('WHERE lower(note) LIKE ? OR CAST(amount AS TEXT) LIKE ?',(f'%{q}%',f'%{q}%'))[:15];pending.pop(m.from_user.id,None);return bot.send_message(m.chat.id,'🔎 Результаты:\n\n'+('\n\n'.join(label(r) for r in rr) if rr else 'Ничего не нашла 🫠'),reply_markup=menu())
 if s['step']=='amount':
  try:v=float(Decimal(m.text.replace(' ','').replace(',','.')));assert v>0
  except: return bot.send_message(m.chat.id,'Напиши сумму цифрами, например 2500')
  s.update(amount=v,step='payer');k=types.ReplyKeyboardMarkup(resize_keyboard=True,one_time_keyboard=True);k.row('👩🏻 Алина','👩🏻 Наташа');return bot.send_message(m.chat.id,'Кто заплатил?',reply_markup=k)
 if s['step']=='payer':
  p=463620997 if 'Алина' in m.text else 831511518 if 'Наташа' in m.text else None
  if not p:return bot.send_message(m.chat.id,'Выбери Алину или Наташу 👆')
  s.update(payer_id=p,payer_name=PEOPLE[p],step='type');k=types.ReplyKeyboardMarkup(resize_keyboard=True,one_time_keyboard=True);k.row('🤝 50/50','🙋 Личная');k.row('💸 Долг / заём');return bot.send_message(m.chat.id,'Как учитывать сумму?',reply_markup=k)
 if s['step']=='type':
  if m.text.startswith('🤝') or m.text.startswith('🙋'):s.update(expense_type='shared' if m.text.startswith('🤝') else 'personal',step='note');return bot.send_message(m.chat.id,'Что это было? Можно коротко или «пропустить»')
  if m.text.startswith('💸'):
   other=831511518 if s['payer_id']==463620997 else 463620997;s.update(expense_type='debt',debtor_id=other,debtor_name=PEOPLE[other],step='debt_confirm');return bot.send_message(m.chat.id,f"Кто должен {s['payer_name']}? Это {PEOPLE[other]}. Напиши «да»")
  return bot.send_message(m.chat.id,'Выбери тип кнопкой 👆')
 if s['step']=='debt_confirm':
  if m.text.strip().lower() not in {'да','дa','yes'}:return bot.send_message(m.chat.id,'Подтверди кнопкой текстом «да» 👆')
  s['step']='note';return bot.send_message(m.chat.id,'Что это было? Можно коротко или «пропустить»')
 if s['step']=='note':
  note='' if m.text.lower() in {'пропустить','-','нет'} else m.text;now=datetime.now().isoformat(timespec='seconds');c.execute('INSERT INTO expenses(month,payer_id,payer_name,amount,note,created_at,expense_type,debtor_id,debtor_name) VALUES(?,?,?,?,?,?,?,?,?)',(month(),s['payer_id'],s['payer_name'],s['amount'],note,now,s['expense_type'],s.get('debtor_id'),s.get('debtor_name')));c.commit();pending.pop(m.from_user.id,None);r=rows()[:1];k=types.InlineKeyboardMarkup();k.add(types.InlineKeyboardButton('↩️ Ой, отменить',callback_data=f'del:{r[0][0]}'));bot.send_message(m.chat.id,'✅ Записала!\n\n'+label(r[0])+'\n\n💰 '+bal(),reply_markup=k)
class H(BaseHTTPRequestHandler):
 def do_GET(self):self.send_response(200);self.end_headers();self.wfile.write(b'OK')
 def log_message(self,*a):pass
def serve():ThreadingHTTPServer(('0.0.0.0',int(os.getenv('PORT','10000'))),H).serve_forever()
if __name__=='__main__':threading.Thread(target=serve,daemon=True).start();bot.infinity_polling(skip_pending=True)
