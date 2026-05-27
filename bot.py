import os
import logging
import json
import ssl
import urllib.request
import urllib.parse
import re
import asyncio
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes, ConversationHandler
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

# Состояния диалога
CHOOSING, MODE_HISTORY, MODE_DECISION, MODE_ADVICE = range(4)

FALLBACK_REGLAMENT = """
ЦА ПРИОРИТЕТ 1: маркетологи и рекламные агентства с бюджетами от 300К+, владельцы бизнеса
ЦА ПРИОРИТЕТ 2: все бизнесы с рекламными бюджетами от 100К+
НЕ ИНТЕРЕСНЫ: только SEO/email без платного трафика, недвижимость и банки (оборот >9 млн)
МИНИМУМ: офлайн от 50 чел (от 30 для ЦА-1), онлайн от 150 чел (от 50 для ЦА-1)
РАСЧЁТ: стоимость касания = стоимость участия / лиды. Порог = 2 700 руб.
"""

# ── Obsidian ─────────────────────────────────────────────────────────────────
def oget(folder, fname, timeout=4):
    if not OURL:
        return ""
    try:
        url = f"{OURL}/vault/{urllib.parse.quote(folder)}/{urllib.parse.quote(fname)}"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {OTOK}")
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=timeout) as x:
            return x.read().decode("utf-8")
    except Exception as e:
        logger.warning(f"oget: {e}")
        return ""

def olist(folder, timeout=4):
    if not OURL:
        return []
    try:
        req = urllib.request.Request(f"{OURL}/vault/{urllib.parse.quote(folder)}/")
        req.add_header("Authorization", f"Bearer {OTOK}")
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=timeout) as x:
            return json.loads(x.read()).get("files", [])
    except Exception as e:
        logger.warning(f"olist: {e}")
        return []

def get_docs():
    r = oget("Документы которые нужны", "Регламент согласования и оплаты мероприятий.md")
    d = oget("Документы которые нужны", "Отдел ивент-маркетинга Vitamin.tools.md")
    if r or d:
        return f"РЕГЛАМЕНТ:\n{r[:2000]}\n\nЦА:\n{d[:1000]}"
    return FALLBACK_REGLAMENT

def get_history(name):
    words = [w for w in name.lower().split() if len(w) > 3]
    if not words:
        return ""
    out = []
    for yr in ["2026", "2025"]:
        folder = f"Мероприятия Витамин {yr}"
        for f in olist(folder):
            if any(w in f.lower() for w in words):
                c = oget(folder, f)
                if c:
                    out.append(f"=== {f} ({yr}) ===\n{c[:800]}")
                if len(out) >= 3:
                    break
    return "\n\n".join(out) if out else ""

# ── Клод-аналитика ────────────────────────────────────────────────────────────
def claude_ask(prompt):
    client = anthropic.Anthropic(api_key=AKEY)
    msg = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text

def analyze_history_sync(query):
    history = get_history(query)
    if not history:
        return f"🔍 В базе Obsidian ничего не нашёл по запросу *{query}*\n\nВозможно мероприятие ещё не добавлено в базу или название отличается. Попробуй другое название."
    prompt = f"""Ты ивент-аналитик Vitamin.tools. Пользователь хочет узнать всё про мероприятие из нашей базы.

ДАННЫЕ ИЗ БАЗЫ:
{history}

Структурируй информацию понятно:
📌 Название и формат
📅 Когда участвовали
📊 Результаты и лиды
💡 Выводы и стоит ли идти снова"""
    return claude_ask(prompt)

def analyze_decision_sync(text):
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

    prompt = f"""Ты опытный ивент-маркетолог Vitamin.tools. Прими решение об участии в мероприятии.

{docs}
{history_block}

МЕРОПРИЯТИЕ:
{text}

Если каких-то данных нет — сделай обоснованное предположение на основе типа мероприятия.

Ответ:
🎯 *Мероприятие:* [название]
📅 *Дата и формат:* [дата, город/онлайн]

✅/❌/⚠️ *Решение:* УЧАСТВУЕМ / НЕ УЧАСТВУЕМ / НУЖНО УТОЧНИТЬ

*Аргументы:*
• [соответствие ЦА]
• [охват и аудитория]
• [бюджетная эффективность]
• [история если есть]

*Расчёт:*
• Стоимость участия: [X руб.]
• Потенциальных лидов: [N чел.]
• Стоимость касания: [X руб.]
• Порог (×1.5 от 1800₽): 2700₽
• Вывод: [укладывается / нет]

*Что уточнить:*
• [вопросы если нужны]

*Рекомендация:* [одно чёткое действие]"""
    return claude_ask(prompt)

def analyze_advice_sync(question):
    docs = get_docs()
    prompt = f"""Ты опытный ивент-маркетолог Vitamin.tools. Коллега просит совета.

КОНТЕКСТ КОМПАНИИ:
{docs}

ВОПРОС:
{question}

Дай конкретный практичный совет, основанный на опыте ивент-маркетинга."""
    return claude_ask(prompt)

# ── Handlers ─────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    keyboard = [["📂 Инфо о прошлом мероприятии"], ["🤔 Принять решение по новому"], ["💡 Просто совет"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(
        "👋 Привет! Чем займёмся?",
        reply_markup=reply_markup
    )
    return CHOOSING

async def choosing(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if "прошлом" in text or "📂" in text:
        await update.message.reply_text(
            "📂 *Инфо о прошлом мероприятии*\n\n"
            "Напиши название мероприятия или скинь ссылку — найду всё в нашей базе и структурирую для тебя.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        return MODE_HISTORY

    elif "решение" in text or "новому" in text or "🤔" in text:
        await update.message.reply_text(
            "🤔 *Принять решение по новому мероприятию*\n\n"
            "Скинь что есть — хоть письмо от организатора целиком. Если чего-то не хватит, я сам найду в сети.\n\n"
            "Идеально если есть:\n"
            "1) Название\n"
            "2) Дата\n"
            "3) Формат, город\n"
            "4) Тематика/треки\n"
            "5) ЦА, кол-во участников\n"
            "6) Стоимость участия\n"
            "7) Сайт\n\n"
            "Но не парься если чего-то нет — разберёмся 👌",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        return MODE_DECISION

    elif "совет" in text or "💡" in text:
        await update.message.reply_text(
            "💡 *Совет*\n\n"
            "Что случилось? Расскажи — помогу разобраться.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        return MODE_ADVICE

    else:
        # Если написал что-то другое — пробуем угадать режим
        keyboard = [["📂 Инфо о прошлом мероприятии"], ["🤔 Принять решение по новому"], ["💡 Просто совет"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text(
            "Выбери один из вариантов 👇",
            reply_markup=reply_markup
        )
        return CHOOSING

async def mode_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    msg = await update.message.reply_text("🔍 Ищу в базе...")
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, analyze_history_sync, query)
        await send_long(update, msg, result)
    except Exception as e:
        logger.error(f"history error: {e}", exc_info=True)
        await msg.edit_text("⚠️ Что-то пошло не так. Попробуй ещё раз.")
    return await ask_continue(update, ctx)

async def mode_decision(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    msg = await update.message.reply_text("🔍 Анализирую мероприятие...")
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, analyze_decision_sync, text)
        await send_long(update, msg, result)
    except Exception as e:
        logger.error(f"decision error: {e}", exc_info=True)
        await msg.edit_text("⚠️ Что-то пошло не так. Попробуй ещё раз.")
    return await ask_continue(update, ctx)

async def mode_advice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    question = update.message.text.strip()
    msg = await update.message.reply_text("💭 Думаю...")
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, analyze_advice_sync, question)
        await send_long(update, msg, result)
    except Exception as e:
        logger.error(f"advice error: {e}", exc_info=True)
        await msg.edit_text("⚠️ Что-то пошло не так. Попробуй ещё раз.")
    return await ask_continue(update, ctx)

async def send_long(update, msg, text):
    if len(text) <= 4000:
        try:
            await msg.edit_text(text, parse_mode="Markdown")
        except Exception:
            await msg.edit_text(text)
    else:
        await msg.delete()
        for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
            try:
                await update.message.reply_text(chunk, parse_mode="Markdown")
            except Exception:
                await update.message.reply_text(chunk)

async def ask_continue(update, ctx):
    keyboard = [["📂 Инфо о прошлом мероприятии"], ["🤔 Принять решение по новому"], ["💡 Просто совет"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("Чем ещё помочь?", reply_markup=reply_markup)
    return CHOOSING

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)
    return CHOOSING

# ── Запуск ────────────────────────────────────────────────────────────────────
def main():
    logger.info("Starting Events Brain bot...")
    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            CHOOSING:      [MessageHandler(filters.TEXT & ~filters.COMMAND, choosing)],
            MODE_HISTORY:  [MessageHandler(filters.TEXT & ~filters.COMMAND, mode_history)],
            MODE_DECISION: [MessageHandler(filters.TEXT & ~filters.COMMAND, mode_decision)],
            MODE_ADVICE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, mode_advice)],
        },
        fallbacks=[CommandHandler("start", cmd_start), CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(conv)
    logger.info("Bot is running...")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
