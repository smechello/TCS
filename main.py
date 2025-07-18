from flask import Flask, Response
from threading import Thread
import os
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, CallbackQueryHandler
import requests
import pdfplumber
import time

app = Flask(__name__)
bot_logs = []  # will show in Flask page

# === Load API keys ===
def load_keys(path="key.txt"):
    with open(path, "r") as f:
        lines = f.readlines()
        return lines[0].strip(), lines[1].strip()

OPENROUTER_API_KEY, TELEGRAM_TOKEN = load_keys()
MODEL_NAME = "mistralai/mixtral-8x7b-instruct"
PDF_PATH = "data.pdf"

# === PDF Text Extractor ===
def extract_pdf_text(path, chunk_size=500):
    chunks = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            words = text.split()
            for i in range(0, len(words), chunk_size):
                chunks.append(" ".join(words[i:i+chunk_size]))
    return chunks

kb_chunks = extract_pdf_text(PDF_PATH)
last_qas = {}

# === Generate answer ===
def answer_with_context(question, kb_chunks):
    context = "\n---\n".join(kb_chunks)
    system_msg = f"""
You are a TCS onboarding assistant. Answer ONLY using the document below.
If the answer is not present, reply:
"❗ Sorry, based on the current document, I don't know the answer. Please contact the admin or mail: xplore.support@tcs.com"
DOCUMENT:
{context}
"""
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": question}
        ]
    }
    res = requests.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers)
    return res.json()["choices"][0]["message"]["content"].strip()

# === Telegram handlers ===
def handle_message(update: Update, context: CallbackContext):
    user = update.effective_user
    user_id = user.id
    msg = update.message.text

    time_now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    answer = answer_with_context(msg, kb_chunks)

    # Log chat for /chatlog
    bot_logs.append(f"[{time_now}] {user_id}: {msg}\n➡️ {answer}\n")

    # Save individual chat logs
    with open(f"{user_id}.txt", "a", encoding="utf-8") as f:
        f.write(f"[{time_now}] User: {msg}\nBot: {answer}\n")

    # Store last QA
    last_qas[user_id] = (msg, answer)

    # Send to Telegram
    buttons = [[
        InlineKeyboardButton("✅ Satisfied", callback_data='satisfied'),
        InlineKeyboardButton("🚩 Report", callback_data='report')
    ]]
    update.message.reply_text(answer, reply_markup=InlineKeyboardMarkup(buttons))

def button_handler(update: Update, context: CallbackContext):
    query_obj = update.callback_query
    user = query_obj.from_user
    user_id = user.id
    action = query_obj.data

    if action == "satisfied":
        query_obj.edit_message_reply_markup(reply_markup=None)
        query_obj.message.reply_text("✅ Thanks!")
    elif action == "report" and user_id in last_qas:
        q, a = last_qas[user_id]
        with open("report.txt", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now()}] {user_id}\nQuery: {q}\nAnswer: {a}\n---\n")
        query_obj.edit_message_reply_markup(reply_markup=None)
        query_obj.message.reply_text("🚩 Report noted.")

def start(update, context):
    update.message.reply_text("Welcome to the TCS FAQ Bot!")

# === Flask routes ===
@app.route("/")
def home():
    if bot_logs:
        return "<pre>" + bot_logs[-1] + "</pre>"
    return "🤖 Bot is running!"

@app.route("/chatlog")
def chatlog():
    return "<pre>" + "\n".join(bot_logs[-100:]) + "</pre>"

# === Start Telegram Bot ===
def run_bot():
    updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    dp.add_handler(CallbackQueryHandler(button_handler))
    updater.start_polling()
    updater.idle()

# === Start everything ===
if __name__ == "__main__":
    bot_thread = Thread(target=run_bot)
    bot_thread.start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
