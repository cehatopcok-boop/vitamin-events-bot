import os
import logging
import json
import ssl
import urllib.request
import urllib.parse
import re
import asyncio
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
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

# ── Встроенный регламент (резерв если Obsidian недоступен) ──────────────────
FALLBACK_REGLAMENT = """
РЕГЛАМЕНТ VITAMIN.TOOLS — ОТБОР МЕРОПРИЯТИЙ

ЦА ПРИОРИТЕТ 1: маркетологи и рекламные агентства с бюджетами от 300К+, владельцы бизнеса
ЦА ПРИОРИТЕТ 2: все бизнесы с рекламными бюджетами от 100К+
НЕ ИНТЕРЕСНЫ: только SEO/email без платного трафика, недвижимость и банки (оборот >9 млн)

МИНИМУМ АУДИТОРИИ:
- Офлайн: от 50 чел (от 30 для ЦА-1)
- Онлайн: от 150 чел (от 50-70 для ЦА-1)

ФОРМУЛА РАСЧЁТА:
Стоимость участия ÷ потенциальные лиды = стоимость касания
Средняя стоимость лида × 1.5 = максимально допустимая стоимость лида
Ориентир: средняя = 1 800 руб., порог = 2 700 руб.

ФОРМАТЫ УЧАСТИЯ: стенд, доклад, спонсорство, партнёрство, нетворкинг
СПИКЕРЫ: Егор Осипов (Саратов), Ален Багабо (СПб), Денис Кабалкин (только крупные)
"""

# ── Obsidian с коротким таймаутом ────────────────────────────────────────────
def oget(folder: str, fname: str, timeout: int = 4) -> str:
    if not OURL:
        return ""
    try:
        url = f"{OURL}/vault/{urllib.parse.quote(folder)}/{urllib.parse.quote(fname)}"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {OTOK}")
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=timeout) as x:
            return x.read().decode("utf-8")
    except Exception as e:
        logger.warning(f"oget [{fname}]: {e}")
        return ""

def olist(folder: str, timeout: int = 4) -> list:
    if not OURL:
        return []
    try:
        req = urllib.request.Request(f"{OURL}/vault/{urllib.parse.quote(folder)}/")
        req.add_header("Authorization", f"Bearer {OTOK}")
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=timeout) as x:
            return json.loads(x.read()).get("files", [])
    except Exception as e:
        logger.warning(f"olist [{folder}]: {e}")
        return []

def get_docs() -> str:
    r = oget("Документы которые нужны", "Регламент согласования и оплаты мероприятий.md")
    d = oget("Документы которые нужны", "Отдел ивент-маркетинга Vitamin.tools.md")
    if r or d:
        return f"РЕГЛАМЕНТ:\n{r[:2000]}\n\nЦА И ЗАДАЧИ:\n{d[:1000]}"
    return FALLBACK_REGLAMENT

def get_history(name: str) -> str:
    words = [w for w in name.lower().split() if len(w) > 3]
    if not words:
        return ""
    out = []
    for yr in ["2026", "2025"]:
        folder = f"Мероприятия Витамин {yr}"
        files = olist(folder)
        for f in files:
            if any(w in f.lower() for w in words):
                c = oget(folder, f)
                if c:
                    out.append(f"=== {f} ({yr}) ===\n{c[:800]}")
                if len(out) >= 3:
                    break
        if len(out) >= 3:
            break
    return "\n\n".join(out) if out else ""

# ── Основной анализ ──────────────────────────────────────────────────────────
def analyze_sync(text: str) -> str:
    # Извлекаем название
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    name = lines[0] if lines else "мероприятие"
    for l in lines:
        m = re.match(r'^1[\).\s]+(.+)', l)
        if m:
            name = m.group(1).strip()
            break

    docs = get_docs()
    history = get_history(name)
    history_block = f"\n\nИСТОРИЯ УЧАСТИЯ:\n{history}" if history else "\n\nИСТОРИЯ: ранее не участвовали или данные недоступны."

    prompt = f"""Ты опытный ивент-маркетолог компании Vitamin.tools (рекламная экосистема: vitamin.tools, yagla.ru, lpgenerator.ru).
Твоя задача — принять решение об участии в мероприятии на основе регламента компании.

{docs}
{history_block}

МЕРОПРИЯТИЕ ДЛЯ АНАЛИЗА:
{text}

Дай чёткий структурированный ответ:

🎯 *Мероприятие:* [название]
📅 *Дата и формат:* [дата, город/онлайн]

✅/❌/⚠️ *Решение:* УЧАСТВУЕМ / НЕ УЧАСТВУЕМ / НУЖНО УТОЧНИТЬ

*Аргументы:*
• [соответствие ЦА — да/нет, почему]
• [охват и аудитория]
• [бюджетная эффективность]
• [история участия если есть]

*Расчёт:*
• Стоимость участия: [X руб.]
• Потенциальных лидов: [N чел.]
• Стоимость касания: [X руб.]
• Порог регламента (×1.5 от 1800₽): 2700₽
• Вывод по расчёту: [укладывается / не укладывается]

*Что уточнить перед финальным решением:*
• [конкретные вопросы организаторам]

*Рекомендация:* [одно чёткое предложение что делать дальше]"""

    client = anthropic.Anthropic(api_key=AKEY)
    msg = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1800,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text

# ── Telegram handlers ────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я Events Brain — помогаю решить, участвовать ли Vitamin.tools в мероприятии.\n\n"
        "Просто скинь название или ссылку на мероприятие — я сам найду всю информацию в интернете: даты, формат, аудиторию, стоимость, спикеров и всё остальное.\n\n"
        "Если чего-то не найду — спрошу тебя напрямую. Также могу спросить про наш прошлый опыт участия, если это поможет принять решение.\n\n"
        "📌 Можно скинуть:\n"
        "• Просто название: \"Сурового Питерского SMM\"\n"
        "• Ссылку на сайт мероприятия\n"
        "• Письмо от организатора целиком\n\n"
        "Погнали 🎯",
        parse_mode=None
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 *Как пользоваться ботом:*\n\n"
        "Отправь описание мероприятия — я проверю его по регламенту Vitamin.tools и базе прошлых ивентов.\n\n"
        "*Что анализирую:*\n"
        "• Соответствие целевой аудитории\n"
        "• Стоимость контакта vs регламент\n"
        "• Историю участия в прошлые годы\n"
        "• Потенциал лидогенерации\n\n"
        "*Формат:* можно кидать как угодно — хоть письмо от организатора целиком.",
        parse_mode="Markdown"
    )

async def handle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if len(text) < 15:
        await update.message.reply_text(
            "Кинь описание мероприятия — проверю по регламенту. Можно целое письмо от организатора."
        )
        return

    status_msg = await update.message.reply_text("🔍 Анализирую мероприятие...")
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, analyze_sync, text)
        # Разбиваем на части если длинный ответ
        if len(result) <= 4000:
            await status_msg.edit_text(result, parse_mode="Markdown")
        else:
            await status_msg.delete()
            chunks = [result[i:i+4000] for i in range(0, len(result), 4000)]
            for chunk in chunks:
                await update.message.reply_text(chunk, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"handle error: {e}", exc_info=True)
        err_text = str(e)
        if "model" in err_text.lower() and "not_found" in err_text.lower():
            await status_msg.edit_text("⚠️ Ошибка модели AI. Сообщи Илье.")
        else:
            await status_msg.edit_text(f"⚠️ Ошибка при анализе. Попробуй ещё раз или напиши Илье.\n\n{err_text[:200]}")

# ── Запуск ───────────────────────────────────────────────────────────────────
def main():
    logger.info("Starting Events Brain bot...")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    logger.info("Bot is running...")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
