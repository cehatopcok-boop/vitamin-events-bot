import os, asyncio, json, ssl, urllib.request, urllib.parse, re
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import anthropic

TOKEN = os.getenv("TELEGRAM_TOKEN")
AKEY = os.getenv("ANTHROPIC_API_KEY")
OURL = os.getenv("OBSIDIAN_URL", "").rstrip("/")
OTOK = os.getenv("OBSIDIAN_TOKEN")

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

def oget(folder, fname):
    url = f"{OURL}/vault/{urllib.parse.quote(folder)}/{urllib.parse.quote(fname)}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {OTOK}")
    try:
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as x:
            return x.read().decode("utf-8")
    except:
        return ""

def get_history(name):
    out = []
    words = [w for w in name.lower().split() if len(w) > 3]
    for yr in ["2025", "2026"]:
        folder = f"Мероприятия Витамин {yr}"
        req = urllib.request.Request(f"{OURL}/vault/{urllib.parse.quote(folder)}/")
        req.add_header("Authorization", f"Bearer {OTOK}")
        try:
            with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as x:
                files = json.loads(x.read()).get("files", [])
            for f in files:
                if any(w in f.lower() for w in words):
                    c = oget(folder, f)
                    if c:
                        out.append(f"=== {f} ({yr}) ===\n{c[:1200]}")
        except:
            pass
    return "\n\n".join(out) or "История не найдена."

def get_docs():
    r = oget("Документы которые нужны", "Регламент согласования и оплаты мероприятий.md")
    d = oget("Документы которые нужны", "Отдел ивент-маркетинга Vitamin.tools.md")
    return f"РЕГЛАМЕНТ:\n{r[:2000]}\n\nЦА:\n{d[:1500]}"

def analyze_sync(text):
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    name = next((re.sub(r'^1[\)\.:\s]+', '', l) for l in lines if re.match(r'^1[\)\.]', l)), lines[0] if lines else "мероприятие")
    client = anthropic.Anthropic(api_key=AKEY)
    prompt = f"""Ты ивент-аналитик Vitamin.tools. Прими решение по мероприятию.

РЕГЛАМЕНТ И ЦА:
{get_docs()}

ИСТОРИЯ:
{get_history(name)}

МЕРОПРИЯТИЕ:
{text}

Ответь строго в формате без markdown звёздочек:
Мероприятие: [название]
Решение: УЧАСТВУЕМ / НЕ УЧАСТВУЕМ / НУЖНО УТОЧНИТЬ
Аргументы:
- [аргумент про ЦА]
- [аргумент про бюджет]
- [аргумент из истории]
Расчёт:
- Стоимость: [сумма]
- Оценка лидов: [число]
- Стоимость касания: [сумма]
- Порог (x1.5): [сумма]
История: [из базы]
Что уточнить: [вопросы]"""
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text

async def handle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if len(text) < 20:
        await update.message.reply_text("Кидай описание мероприятия — проверю по регламенту и базе.")
        return
    msg = await update.message.reply_text("Анализирую...")
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, analyze_sync, text)
        await msg.edit_text(result[:4000])
    except Exception as e:
        await msg.edit_text(f"Ошибка: {str(e)[:200]}")

async def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    print("Bot started!")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    print("Polling started, waiting...")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
