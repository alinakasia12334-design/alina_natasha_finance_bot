import os, sqlite3, threading, time
from datetime import datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import telebot
from telebot import types

TOKEN = os.getenv('TOKEN')
if not TOKEN:
    raise RuntimeError('TOKEN environment variable is required')

bot = telebot.TeleBot(TOKEN)
DB = os.getenv('DB_PATH', 'expenses.db')
PEOPLE = {463620997: 'Алина', 831511518: 'Наташа'}
ALLOWED_USERS = set(PEOPLE)
c = sqlite3.connect(DB, check_same_thread=False, timeout=30)
c.execute('''CREATE TABLE IF NOT EXISTS expenses(
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
)''')
c.execute('''CREATE TABLE IF NOT EXISTS settlements(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    closed_at TEXT NOT NULL,
    through_expense_id INTEGER NOT NULL,
    period TEXT NOT NULL,
    net_amount REAL NOT NULL,
    debtor_id INTEGER,
    creditor_id INTEGER,
    debtor_name TEXT,
    creditor_name TEXT
)''')
c.commit()
pending = {}


def ok(m):
    return m.from_user.id in ALLOWED_USERS


def money(x):
    return f'{x:,.0f} ₽'.replace(',', ' ')


def month():
    return datetime.now().strftime('%Y-%m')


def menu():
    k = types.ReplyKeyboardMarkup(resize_keyboard=True)
    k.row('➕ Добавить трату', '💰 Кто кому должен?')
    k.row('📋 История', '📊 Отчёты')
    k.row('📈 Статистика', '🔄 Взаиморасчёт')
    k.row('⚙️ Управление', '❓ Инструкция')
    return k


def rows(where='', p=()):
    return c.execute(
        'SELECT id,payer_id,payer_name,amount,expense_type,debtor_id,debtor_name,note,created_at,creditor_id,creditor_name '
        'FROM expenses ' + where + ' ORDER BY created_at DESC,id DESC', p
    ).fetchall()


def cutoff():
    r = c.execute('SELECT through_expense_id FROM settlements ORDER BY id DESC LIMIT 1').fetchone()
    return r[0] if r else 0


def openrows():
    return rows('WHERE id>?', (cutoff(),))


def calc(rr=None):
    rr = rr if rr is not None else openrows()
    n = {463620997: 0.0, 831511518: 0.0}
    for r in rr:
        _, payer, _, amount, kind, debtor, _, _, _, creditor, _ = r
        if kind == 'shared' and payer in n:
            other = 831511518 if payer == 463620997 else 463620997
            n[payer] += amount / 2
            n[other] -= amount / 2
        elif kind == 'debt' and debtor in n and creditor in n:
            n[debtor] += amount
            n[creditor] -= amount
    return n


def balance_text():
    x = calc()[463620997]
    if abs(x) < 0.01:
        return '✨ Всё ровно. Никто никому не должен!'
    if x > 0:
        return f'💸 Наташа должна Алине: {money(x)}'
    return f'💸 Алина должна Наташе: {money(-x)}'


def label(r):
    _, _, payer, amount, kind, debtor, debtor_name, note, created, creditor, creditor_name = r
    if kind == 'shared':
        kind_text = '🤝 50/50'
    else:
        kind_text = f'💸 Долг: {creditor_name} → {debtor_name}'
    return f'{datetime.fromisoformat(created):%d.%m %H:%M} · {payer} · {money(amount)} · {kind_text}\n   {note or "Без комментария"}'


def notify(uid, text):
    try:
        bot.send_message(uid, text)
    except Exception as e:
        print(f'Notification error: {e}', flush=True)


@bot.message_handler(commands=['start'])
def start(m):
    if not ok(m):
        return bot.reply_to(m, '⛔ Этот бот только для Алины и Наташи 💅')
    bot.send_message(m.chat.id, '''ЗДАРОВА, ЧЕЛИХА 👋🏻

Это ваш личный финансовый бот для АЛИНЫ И НАТАШИ.

Я создан для того, чтобы вы не запутались в деньгах, не вспоминали через месяц «а кто, блять, тогда платил?» и не сидели с калькулятором, выясняя, кто кому должен 😂

💸 ТРАТА — сумма → 50/50 → кто заплатил.
💰 ДОЛГ / ЗАЁМ — сумма → кто дал → дальше я сама понимаю, кто взял.
🧮 ВЗАИМОРАСЧЁТ — я сама считаю чистую разницу.
⚙️ УПРАВЛЕНИЕ — если где-то накосячили и надо что-то удалить.
📊 ОТЧЁТЫ — день, неделя, месяц.

Без бухгалтерской ебанины. Иногда с мемами.

ЛАЙФ КУ БИ ДРИМ 💅🏻''', reply_markup=menu())


@bot.message_handler(func=lambda m: m.text in ('❓ Инструкция', '❓ Помощь'))
def help_(m):
    if ok(m):
        bot.send_message(m.chat.id, '''🧠 КАК ЭТО РАБОТАЕТ

➕ Добавить трату → сумма → 50/50 или долг.
🤝 50/50 → кто заплатил → что это было.
💸 Долг → кто дал деньги → второй человек автоматически считается тем, кто взял → комментарий.
💰 Кто кому должен? → текущий баланс.
🔄 Взаиморасчёт → чистая разница и новый период с нуля.
⚙️ Управление → отмена/удаление/очистка.

Если где-то финансовый пиздец — спокойно, я разберусь 😂

ЛАЙФ КУ БИ ДРИМ.''', reply_markup=menu())


@bot.message_handler(func=lambda m: m.text == '➕ Добавить трату')
def add(m):
    if ok(m):
        pending[m.from_user.id] = {'step': 'amount'}
        bot.send_message(m.chat.id, '💸 Сколько потратили?\n\nНапиши сумму цифрами, например: 3500')


@bot.message_handler(func=lambda m: m.text == '💰 Кто кому должен?')
def balance(m):
    if ok(m):
        bot.send_message(m.chat.id, '💰 ТЕКУЩИЙ ВЗАИМОРАСЧЁТ\n\n' + balance_text(), reply_markup=menu())


@bot.message_handler(func=lambda m: m.text == '🔄 Взаиморасчёт')
def settlement(m):
    if not ok(m):
        return
    rr = openrows()
    x = calc(rr)[463620997]
    text = '✨ Сейчас всё ровно.' if not rr or abs(x) < .01 else (f'Наташа должна Алине: {money(x)}' if x > 0 else f'Алина должна Наташе: {money(-x)}')
    k = types.InlineKeyboardMarkup()
    if rr:
        k.add(types.InlineKeyboardButton('🔒 Закрыть взаиморасчёт', callback_data='settle_confirm'))
    bot.send_message(m.chat.id, '🔄 ВЗАИМОРАСЧЁТ\n\n' + text + '\n\nИстория не удаляется.', reply_markup=k if rr else menu())


@bot.callback_query_handler(func=lambda q: q.data == 'settle_confirm')
def settle_confirm(q):
    if q.from_user.id not in ALLOWED_USERS:
        return
    x = calc(openrows())[463620997]
    text = '✨ Никто никому не должен.' if abs(x) < .01 else (f'Наташа → Алине: {money(x)}' if x > 0 else f'Алина → Наташе: {money(-x)}')
    k = types.InlineKeyboardMarkup()
    k.add(types.InlineKeyboardButton('✅ Да, закрыть', callback_data='settle_yes'), types.InlineKeyboardButton('❌ Отмена', callback_data='settle_no'))
    bot.send_message(q.message.chat.id, '🔒 Закрываем взаиморасчёт?\n\n' + text + '\n\nВсе траты останутся в истории. Текущий баланс станет 0 ₽.', reply_markup=k)
    bot.answer_callback_query(q.id)


@bot.callback_query_handler(func=lambda q: q.data == 'settle_yes')
def settle_yes(q):
    if q.from_user.id not in ALLOWED_USERS:
        return
    rr = openrows()
    x = calc(rr)[463620997]
    if not rr:
        return bot.answer_callback_query(q.id, 'Нечего закрывать', show_alert=True)
    debtor = 831511518 if x > 0 else 463620997 if x < 0 else None
    creditor = 463620997 if x > 0 else 831511518 if x < 0 else None
    through = max(r[0] for r in rr)
    c.execute('INSERT INTO settlements(closed_at,through_expense_id,period,net_amount,debtor_id,creditor_id,debtor_name,creditor_name) VALUES(?,?,?,?,?,?,?,?)',
              (datetime.now().isoformat(timespec='seconds'), through, month(), abs(x), debtor, creditor, PEOPLE.get(debtor), PEOPLE.get(creditor)))
    c.commit()
    result = '✨ Никто никому не должен.' if abs(x) < .01 else f'💸 {PEOPLE[debtor]} → {PEOPLE[creditor]}: {money(abs(x))}'
    bot.answer_callback_query(q.id, 'Закрыто 💅')
    bot.send_message(q.message.chat.id, '🧹 ФИНАНСОВЫЙ СТОЛ ОЧИЩЕН\n\n' + result + '\n\nСтарые траты сохранены. Новый период начался с нуля.\n\nЛАЙФ КУ БИ ДРИМ 💅🏻', reply_markup=menu())
    other = creditor if q.from_user.id == debtor else debtor
    if other:
        notify(other, '🧹 АЛИНА И НАТАША ЗАКРЫЛИ ВЗАИМОРАСЧЁТ\n\n' + result + '\n\nСтарые траты сохранены, новый период — с нуля.\n\nДо следующего финансового пиздеца 😂')


@bot.callback_query_handler(func=lambda q: q.data == 'settle_no')
def settle_no(q):
    bot.answer_callback_query(q.id, 'Оставляем 😌')


@bot.message_handler(func=lambda m: m.text == '📋 История')
def history(m):
    if ok(m):
        rr = rows('WHERE month=?', (month(),))[:30]
        bot.send_message(m.chat.id, '📋 ИСТОРИЯ\n\n' + ('\n\n'.join(label(r) for r in rr) if rr else 'Пока пусто. Бухгалтерия девственно чиста 😭'), reply_markup=menu())


@bot.message_handler(func=lambda m: m.text == '📊 Отчёты')
def reports(m):
    if ok(m):
        k = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        k.row('📅 Сегодня', '📆 Неделя')
        k.row('🗓 Месяц', '◀️ Назад')
        bot.send_message(m.chat.id, '📊 Какой отчёт показать?', reply_markup=k)


@bot.message_handler(func=lambda m: m.text in {'📅 Сегодня', '📆 Неделя', '🗓 Месяц'})
def period(m):
    if not ok(m):
        return
    now = datetime.now()
    if m.text == '🗓 Месяц':
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif m.text == '📆 Неделя':
        start = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rr = rows('WHERE created_at>=?', (start.isoformat(timespec='seconds'),))
    bot.send_message(m.chat.id, f'📊 ОТЧЁТ\n\n💸 Всего потрачено: {money(sum(r[3] for r in rr))}\n🧾 Операций: {len(rr)}', reply_markup=menu())


@bot.message_handler(func=lambda m: m.text == '📈 Статистика')
def stats(m):
    if ok(m):
        rr = rows('WHERE month=?', (month(),))
        bot.send_message(m.chat.id, f'📈 СТАТИСТИКА {month()}\n\n💸 Потрачено: {money(sum(r[3] for r in rr))}\n🧾 Операций: {len(rr)}', reply_markup=menu())


@bot.message_handler(func=lambda m: m.text == '⚙️ Управление')
def manage(m):
    if ok(m):
        k = types.InlineKeyboardMarkup()
        k.add(types.InlineKeyboardButton('↩️ Отменить последнюю', callback_data='undo'))
        k.add(types.InlineKeyboardButton('🗑 Удалить операцию', callback_data='delete_choose'))
        k.add(types.InlineKeyboardButton('🧹 Очистить текущий период', callback_data='clear_current'))
        k.add(types.InlineKeyboardButton('🚨 Удалить всё незакрытое', callback_data='clear_all'))
        bot.send_message(m.chat.id, '⚙️ УПРАВЛЕНИЕ\n\nЗдесь можно исправить косяк. Не переживай, бухгалтерия не обидится 😂', reply_markup=k)


@bot.callback_query_handler(func=lambda q: q.data == 'undo')
def undo(q):
    if q.from_user.id not in ALLOWED_USERS:
        return
    rr = openrows()[:1]
    if not rr:
        return bot.answer_callback_query(q.id, 'Удалять нечего 😭', show_alert=True)
    c.execute('DELETE FROM expenses WHERE id=?', (rr[0][0],))
    c.commit()
    bot.answer_callback_query(q.id, 'Удалено 💅')
    bot.send_message(q.message.chat.id, '↩️ Последнюю операцию отменила.\n\n' + label(rr[0]) + '\n\nБаланс пересчитан.', reply_markup=menu())
    notify(831511518 if q.from_user.id == 463620997 else 463620997, f'🗑 {PEOPLE[q.from_user.id].upper()} ОТМЕНИЛА ПОСЛЕДНЮЮ ТРАТУ\n\n{label(rr[0])}\n\nФинансовое преступление отменено 😂')


@bot.callback_query_handler(func=lambda q: q.data == 'delete_choose')
def delete_choose(q):
    if q.from_user.id not in ALLOWED_USERS:
        return
    rr = openrows()[:10]
    if not rr:
        return bot.answer_callback_query(q.id, 'Незакрытых операций нет', show_alert=True)
    k = types.InlineKeyboardMarkup()
    for r in rr:
        k.add(types.InlineKeyboardButton(f'{r[2]} · {money(r[3])} · {(r[7] or "без названия")[:18]}', callback_data=f'del:{r[0]}'))
    bot.send_message(q.message.chat.id, '🗑 Выбери операцию для удаления:', reply_markup=k)
    bot.answer_callback_query(q.id)


@bot.callback_query_handler(func=lambda q: q.data.startswith('del:'))
def delete_one(q):
    if q.from_user.id not in ALLOWED_USERS:
        return
    eid = int(q.data.split(':')[1])
    rr = rows('WHERE id=?', (eid,))
    if not rr or eid <= cutoff():
        return bot.answer_callback_query(q.id, 'Эта операция уже в закрытом периоде 🔒', show_alert=True)
    c.execute('DELETE FROM expenses WHERE id=?', (eid,))
    c.commit()
    bot.answer_callback_query(q.id, 'Удалено 💅')
    bot.send_message(q.message.chat.id, '🗑 Удалила:\n\n' + label(rr[0]) + '\n\nБаланс пересчитан.', reply_markup=menu())
    notify(831511518 if q.from_user.id == 463620997 else 463620997, f'🗑 {PEOPLE[q.from_user.id].upper()} УДАЛИЛА ОПЕРАЦИЮ\n\n{label(rr[0])}\n\nБыло — и сплыло 😂')


@bot.callback_query_handler(func=lambda q: q.data in {'clear_current', 'clear_all'})
def clear(q):
    if q.from_user.id not in ALLOWED_USERS:
        return
    k = types.InlineKeyboardMarkup()
    k.add(types.InlineKeyboardButton('ДА, УДАЛИТЬ 😭', callback_data='confirm_' + q.data), types.InlineKeyboardButton('Нет', callback_data='cancel_clear'))
    bot.send_message(q.message.chat.id, '⚠️ Точно удалить незакрытые операции?\n\nЗакрытые взаиморасчёты не трогаю 🔒', reply_markup=k)
    bot.answer_callback_query(q.id)


@bot.callback_query_handler(func=lambda q: q.data.startswith('confirm_'))
def clear_yes(q):
    if q.from_user.id not in ALLOWED_USERS:
        return
    action = q.data.replace('confirm_', '')
    rr = openrows()
    count = len(rr)
    if action == 'clear_current':
        c.execute('DELETE FROM expenses WHERE id>? AND month=?', (cutoff(), month()))
    elif action == 'clear_all':
        c.execute('DELETE FROM expenses WHERE id>?', (cutoff(),))
    else:
        return
    c.commit()
    bot.answer_callback_query(q.id, 'Готово')
    bot.send_message(q.message.chat.id, f'🧹 Удалила {count} незакрытых операций.\n\nЗакрытые периоды не трогала 🔒', reply_markup=menu())
    notify(831511518 if q.from_user.id == 463620997 else 463620997, f'🚨 {PEOPLE[q.from_user.id].upper()} ПОЧИСТИЛА БУХГАЛТЕРИЮ\n\nУдалено операций: {count}.\n\nФинансовый апокалипсис локализован 😂')


@bot.callback_query_handler(func=lambda q: q.data == 'cancel_clear')
def cancel_clear(q):
    bot.answer_callback_query(q.id, 'Фух 😮‍💨')
    bot.send_message(q.message.chat.id, 'Ничего не удаляю 😌', reply_markup=menu())


@bot.message_handler(func=lambda m: True)
def flow(m):
    if not ok(m):
        return
    s = pending.get(m.from_user.id)
    if not s:
        if m.text == '◀️ Назад':
            bot.send_message(m.chat.id, 'Главное меню 👇', reply_markup=menu())
        return
    try:
        if s['step'] == 'amount':
            try:
                value = float(Decimal(m.text.replace(' ', '').replace(',', '.')))
                if value <= 0:
                    raise ValueError
            except Exception:
                return bot.send_message(m.chat.id, 'Напиши сумму цифрами, например 2500 💸')
            s.update(amount=value, step='type')
            k = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            k.row('🤝 50/50', '💸 Долг / заём')
            return bot.send_message(m.chat.id, '🧾 Как учитывать эту сумму?', reply_markup=k)

        if s['step'] == 'type':
            if m.text.startswith('🤝'):
                s.update(expense_type='shared', step='payer')
                k = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
                k.row('👩🏻 Алина', '👩🏻 Наташа')
                return bot.send_message(m.chat.id, '👤 Кто заплатил?', reply_markup=k)
            if m.text.startswith('💸'):
                s['expense_type'] = 'debt'
                s['step'] = 'creditor'
                k = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
                k.row('👩🏻 Алина', '👩🏻 Наташа')
                return bot.send_message(m.chat.id, '💸 Кто ДАЛ деньги?', reply_markup=k)
            return bot.send_message(m.chat.id, 'Выбери тип кнопкой 👆')

        if s['step'] == 'payer':
            p = 463620997 if 'Алина' in m.text else 831511518 if 'Наташа' in m.text else None
            if not p:
                return bot.send_message(m.chat.id, 'Выбери Алину или Наташу 👆')
            s.update(payer_id=p, payer_name=PEOPLE[p], step='note')
            return bot.send_message(m.chat.id, '🧾 Что это было? Можно коротко или «пропустить»')

        if s['step'] == 'creditor':
            p = 463620997 if 'Алина' in m.text else 831511518 if 'Наташа' in m.text else None
            if not p:
                return bot.send_message(m.chat.id, 'Выбери Алину или Наташу 👆')
            debtor = 831511518 if p == 463620997 else 463620997
            s.update(creditor_id=p, creditor_name=PEOPLE[p], debtor_id=debtor, debtor_name=PEOPLE[debtor], payer_id=p, payer_name=PEOPLE[p], step='note')
            return bot.send_message(m.chat.id, f'👤 Значит {PEOPLE[debtor]} взяла деньги. Всё верно?\n\n🧾 Что это было? Можно коротко или «пропустить»')

        if s['step'] == 'note':
            note = '' if m.text.lower().strip() in {'пропустить', '-', 'нет'} else m.text.strip()
            now = datetime.now().isoformat(timespec='seconds')
            c.execute('INSERT INTO expenses(month,payer_id,payer_name,amount,note,created_at,expense_type,debtor_id,debtor_name,creditor_id,creditor_name) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                      (month(), s['payer_id'], s['payer_name'], s['amount'], note, now, s['expense_type'], s.get('debtor_id'), s.get('debtor_name'), s.get('creditor_id'), s.get('creditor_name')))
            c.commit()
            eid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
            r = rows('WHERE id=?', (eid,))[0]
            pending.pop(m.from_user.id, None)
            bot.send_message(m.chat.id, '✅ Записала!\n\n' + label(r) + '\n\n💰 ' + balance_text(), reply_markup=menu())
            other = 831511518 if m.from_user.id == 463620997 else 463620997
            if s['expense_type'] == 'shared':
                notify(other, f'🚨 {PEOPLE[m.from_user.id].upper()} ЧТО-ТО ЗАНЕСЛА В БУХГАЛТЕРИЮ\n\n{label(r)}\n\nС тебя: {money(s["amount"]/2)}\n\nНе благодари. Я просто разношу финансовый пиздец по полочкам 😂')
            else:
                notify(other, f'💸 ФИНАНСОВАЯ ДРАМА\n\n{PEOPLE[s["creditor_id"]]} дала → {PEOPLE[s["debtor_id"]]} взяла\n\nСумма: {money(s["amount"])}\n🧾 {note or "Без комментария"}\n\nТеперь это официально записано. Отмазка «ой, я забыла» не принимается 😂')
            return
    except Exception as e:
        print(f'FLOW ERROR: {e}', flush=True)
        pending.pop(m.from_user.id, None)
        bot.send_message(m.chat.id, '💥 Я сейчас чуть не сдохла от финансовой нагрузки 😂\n\nОперацию не записала. Нажми «➕ Добавить трату» и попробуем ещё раз.', reply_markup=menu())


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')
    def log_message(self, *a):
        pass


def serve():
    ThreadingHTTPServer(('0.0.0.0', int(os.getenv('PORT', '10000'))), H).serve_forever()


if __name__ == '__main__':
    threading.Thread(target=serve, daemon=True).start()
    while True:
        try:
            bot.infinity_polling(skip_pending=True)
        except Exception as e:
            print(f'Polling error, retrying: {e}', flush=True)
            time.sleep(5)
