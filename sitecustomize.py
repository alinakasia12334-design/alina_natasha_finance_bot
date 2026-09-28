from pathlib import Path

# Render starts the service with `python bot.py`. Fix the one malformed
# decorator introduced in the latest commit before Python parses bot.py.
p = Path(__file__).with_name('bot.py')
if p.exists():
    s = p.read_text(encoding='utf-8')
    bad = "@bot.callback_query_handler(func=lambda q:q.data=='cancel_clear'): \n"
    good = "@bot.callback_query_handler(func=lambda q:q.data=='cancel_clear')\n"
    if bad in s:
        p.write_text(s.replace(bad, good, 1), encoding='utf-8')
