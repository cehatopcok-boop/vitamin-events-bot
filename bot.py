import os
import asyncio
import json
import re
import ssl
import urllib.request
import urllib.parse
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import anthropic

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OBSIDIAN_URL = os.getenv("OBSIDIAN_URL")
OBSIDIAN_TOKEN = os.getenv("OBSIDIAN_TOKEN")

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

def obsidian_request(path):
    url = f"{OBSIDIAN_URL.rstrip('/')}{path}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {OBSIDIAN_TOKEN}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
            return resp.read().decode("utf-8")
    except Exception as e:
        return f"ERROR: {e}"

def get_obsidian_file(folder, filename):
    path = f"/vault/{urllib.parse.quote(folder)}/{urllib.parse.quote(filename)}"
    req = urllib.request.Request(f"{OBSIDIAN_URL.rstrip('/')}{path}")
    req.add_header("Authorization", f"Bearer {OBSIDIAN_TOKEN}")
    req.add_header("Accept", "text/markdown")
    try:
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
            return resp.read().decode("utf-8")
    except:
        return ""

def search_obsidian_history(event_name):
    results = []
    name_lower = event_name.lower().strip()
    for year in ["2025", "2026"]:
        folder = f"Мероприятия Витамин {year}"
        list_data = obsidian_request(f"/vault/{urllib.parse.quote(folder)}/")
        if "ERROR" in list_data:
            continue
        try:
            files = json.loads(list_data).get("files", [])
        except:
            continue
        for f in files:
            fname = f.replace(".md", "")
            if any(w in fname.lower() for w in name_lower.split() if len(w) > 3):
                content = get_obsidian_file(folder, f)
                if content:
                    results.append(f"=== {fname} ({year}) ===\n{content[:1500]}")
    return "\n\n".join(results) if results else "История не найдена."

def get_regulations():
    reg = get_obsidian_file("Документы которые нужны", "Регламент согласования и оплаты мероприятий.md")
    dept = get_obsidian_file("Документы которые нужны", "Отдел ивент-маркетинга Vitamin.tools.md")
    return f"РЕГЛАМЕНТ:\n{reg[:2000]}\n\nЦА И КРИТЕРИИ:\n{dept[:1500]}"

def extract_event_name(text):
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for line in lines:
        if re.match(r'^1[\)\.:]', line):
            name = re.sub(r'^1[\)\.:\s]+', '', line).strip()
            if name:
                return name
    for line in lines:
        if len(line) > 5 and line[0].isupper() and not line.startswith("Всем"):
            return line
    return lines[0] if lines else "мероприятие"

def analyze_event(event_text):
    event_name = extract_event_name(event_text)
    history = search_obsidian_history(event_name)
    regulations = get_regulations()
    prompt = f"""Ты — ивент-аналитик компании Vitamin.tools. Прими решение по мероприятию.

РЕГЛАМЕНТ И КРИТЕРИИ:
{regulations}

ИСТОРИЯ:
{history}

МЕРОПРИЯТИЕ:
{event_text}

Ответь строго в формате:

**Мероприятие:** [название]

**Решение:** ✅ УЧАСТВУЕМ / ❌ НЕ УЧАСТВУЕМ / ⚠️ НУЖНО УТОЧНИТЬ

**Аргументы:**
- [аргумент про ЦА]
- [аргумент про бюджет/охват]
- [аргумент из истории]

**Расчёт:**
- Стоимость участия: [сумма]
- Оценка лидов: [число]
- Стоимость касания: [сумма]
- Порог (×1.5): [сумма]
- Вывод: [укладываемся/нет/нет данных]

**История участия:**
[из базы прошлых лет]

**Что уточнить:**
[вопросы перед финальным решением]"""
    message = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if len(text) < 20:
        await update.message.reply_text("👋 Кидай описание мероприятия — проверю по регламенту и базе прошлых ивентов.")
        return
    thinking_msg = await update.message.reply_text("🔍 Анализирую мероприятие...")
    try:
        result = await asyncio.get_event_loop().run_in_executor(None, analyze_event, text)
        await thinking_msg.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await thinking_msg.edit_text(f"❌ Ошибка: {e}")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
