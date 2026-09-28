"""Notification layer for the Alina/Natasha finance bot.

Python loads sitecustomize automatically before bot.py, so important financial
changes can be mirrored to the other participant without changing the bot's
existing logic.
"""
try:
    import telebot
except Exception:
    telebot = None

if telebot is not None:
    PEOPLE = {463620997: "Алина", 831511518: "Наташа"}
    _original_send_message = telebot.TeleBot.send_message
    _notify_guard = False

    def _other(uid):
        return 831511518 if uid == 463620997 else 463620997

    def _notification_text(uid, text):
        name = PEOPLE.get(uid, "Кто-то")
        if text.startswith("✅ Записала!"):
            return (
                f"🚨 {name.upper()} ОПЯТЬ ЧТО-ТО ЗАНЕСЛА В БУХГАЛТЕРИЮ\n\n"
                f"{text}\n\n"
                "Я всё вижу. Финансовый пиздец под контролем 😂"
            )
        if text.startswith("↩️ Отменила:") or text.startswith("🗑 Удалила:"):
            return (
                f"🗑 {name.upper()} УДАЛИЛА ОПЕРАЦИЮ\n\n"
                f"{text}\n\n"
                "Было — и сплыло. Финансовое преступление заметено под ковёр. 😂"
            )
        if text.startswith("💣 Незакрытая история очищена"):
            return (
                f"🚨 {name.upper()} НАЖАЛА КНОПКУ УДАЛИТЬ\n\n"
                "Незакрытые финансовые записи очищены.\n"
                "Если ты сейчас охуела — я тоже. 😂"
            )
        if text.startswith("🔒 ПЕРИОД ЗАКРЫТ"):
            return (
                "🔄 ВЗАИМОРАСЧЁТ ЗАКРЫТ\n\n"
                f"{text}\n\n"
                "С чистого листа, девочки. До следующей финансовой драмы 💅🏻"
            )
        return None

    def send_message_with_notifications(self, chat_id, text, *args, **kwargs):
        global _notify_guard
        result = _original_send_message(self, chat_id, text, *args, **kwargs)
        if _notify_guard or chat_id not in PEOPLE or not isinstance(text, str):
            return result
        notification = _notification_text(chat_id, text)
        if notification:
            _notify_guard = True
            try:
                _original_send_message(self, _other(chat_id), notification)
            except Exception as exc:
                print(f"Notification error: {exc}", flush=True)
            finally:
                _notify_guard = False
        return result

    telebot.TeleBot.send_message = send_message_with_notifications
