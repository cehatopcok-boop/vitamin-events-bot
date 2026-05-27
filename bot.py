import os
import logging
import json
import ssl
import urllib.request
import urllib.parse
import re
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import anthropic

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ["TELEGRAM_TOKEN"]
AKEY = os.environ["ANTHROPIC_API_KEY"]
OURL = os.environ.get("OBSIDIAN_URL", "").rstrip("/")
OTOK = os.environ.get("OBSIDIAN_TOKEN", "")

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

def oget(folder, fname):
    try:
        url = f"{OURL}/vault/{urllib.parse.quote(folder)}/{urllib.parse.quote(fname)}"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {OTOK}")
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as x:
            return x.read().decode("utf-8")
    except Exception as e:
        logger.error(f"oget error: {e}")
        return ""

def get_history(name):
    out = []
    words = [w for w in name.lower().split() if len(w) > 3]
    for yr in ["2025", "2026"]:
        folder = f"Мероприятия Витамин {yr}"
        try:
            req = urllib.request.Request(f"{OURL}/vault/{urllib.parse.quote(folder)}/")
            req.add_header("Authorization", f"Bearer {OTOK}")
            with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as x:
                files = json.loads(x.read()).get("files", [])
            for f in files:
                if any(w in f.lower() for w in words):
                    c = oget(folder, f)
                    if c:
                        out.append(f"=== {f} ({yr}) ===\n{c[:1000]}")
        except Exception as e:
            logger.error(f"history error {yr}: {e}")
    return "\n\n".join(out) or "История не найдена."

def get_docs():
    r = oget("Документы которые нужны", "Регламент согласования и оплаты мероприятий.md")
    d = oget("Документы которые нужны", "Отдел ивент-маркетинга Vitamin.tools.md")
    return f"РЕГЛАМЕНТ:\n{r[:1500]}\n\nЦА:\n{d[:1000]}"

def analyze_sync(text):
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    name = lines[0] if lines else "мероприятие"
    for l in lines:
        if re.match(r'^1[\)\.]', l):
            name = re.sub(r'^1[\)\.\s]+', '', l).strip()
            break
    client = anthropic.Anthropic(api_key=AKEY)
    prompt = f"""Ты ивент-аналитик Vitamin.tools. Прими решение по мероприятию.

РЕГЛАМЕНТ И ЦА:
{get_docs()}

ИСТОРИЯ:
{get_history(name)}

МЕРОПРИЯТИЕ:
{text}

Ответь кратко:
Мероприятие: [название]
Решение: УЧАСТВУЕМ / НЕ УЧАСТВУЕМ / НУЖНО УТОЧНИТЬ
Аргументы:
- [про ЦА]
- [про бюджет/охват]
- [из истории]
Расчёт:
- Стоимость участия: [сумма]
- Оценка лидов: [число]
- Стоимость касания: [сумма]
- Порог регламента x1.5: [сумма]
История: [что было раньше]
Что уточнить: [вопросы]"""
    msg = client.messages.create(
        model="claude-sonnet-4-5",
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
        loop = __import__('asyncio').get_event_loop()
        result = await loop.run_in_executor(None, analyze_sync, text)
        await msg.edit_text(result[:4000])
    except Exception as e:
        logger.error(f"handle error: {e}")
        await msg.edit_text(f"Ошибка: {str(e)[:300]}")

def main():
    logger.info("Starting bot...")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    logger.info("Running polling...")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
