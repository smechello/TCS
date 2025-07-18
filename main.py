import os
import pdfplumber
import requests
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, CallbackContext
import time

# === Load API keys ===
def load_keys(path="key.txt"):
    with open(path, "r") as f:
        lines = f.readlines()
        return lines[0].strip(), lines[1].strip()

OPENROUTER_API_KEY, TELEGRAM_TOKEN = load_keys()
MODEL_NAME = "mistralai/mixtral-8x7b-instruct"
PDF_PATH = "data.pdf"

# === Ensure support files exist ===
def ensure_files():
    for f in ["response.txt", "report.txt", "user.txt"]:
        if not os.path.exists(f):
            open(f, "w").write("0\n0" if f == "response.txt" else "")
ensure_files()

# === PDF Text Extractor using pdfplumber ===
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

# === Log and Track ===
def increment_response():
    with open("response.txt", "r+") as f:
        lines = f.readlines()
        total = int(lines[0].strip()) + 1
        satisfied = int(lines[1].strip())
        f.seek(0)
        f.write(f"{total}\n{satisfied}")
        f.truncate()

def increment_satisfied():
    with open("response.txt", "r+") as f:
        lines = f.readlines()
        total = int(lines[0].strip())
        satisfied = int(lines[1].strip()) + 1
        f.seek(0)
        f.write(f"{total}\n{satisfied}")
        f.truncate()

def update_user_log(user):
    user_id = str(user.id)
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    username = f"@{user.username}" if user.username else "N/A"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    data = {}
    if os.path.exists("user.txt"):
        with open("user.txt", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split(" | ")
                if len(parts) >= 6:
                    uid = parts[0]
                    data[uid] = {
                        "name": parts[1], "username": parts[2],
                        "first_seen": parts[3].replace("First: ", ""),
                        "last_seen": parts[4].replace("Last: ", ""),
                        "queries": int(parts[5].split()[0])
                    }

    if user_id in data:
        data[user_id]["last_seen"] = now
        data[user_id]["queries"] += 1
    else:
        data[user_id] = {
            "name": name, "username": username,
            "first_seen": now, "last_seen": now, "queries": 1
        }

    with open("user.txt", "w", encoding="utf-8") as f:
        for uid, info in data.items():
            f.write(f"{uid} | {info['name']} | {info['username']} | First: {info['first_seen']} | Last: {info['last_seen']} | {info['queries']} queries\n")

def log_report(question, answer, user):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open("report.txt", "a", encoding="utf-8") as f:
        f.write(f"[{now}] ID: {user.id} | {user.first_name} | @{user.username or 'N/A'}\n")
        f.write(f"Query: {question}\nResponse: {answer}\n---\n")

def log_user_chat(user_id, role, message):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(f"{user_id}.txt", "a", encoding="utf-8") as f:
        f.write(f"[{now}] {role}: {message}\n")

# === OpenRouter API ===
def answer_with_context(question, kb_chunks):
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    context = "\n---\n".join(kb_chunks)
    system_msg = f"""
You are a TCS onboarding assistant. Answer ONLY using the data below and do not give full data as reply and answer only for the given question.
If the answer is not present, reply:
"❗ Sorry, based on the current data, I don't know the answer. Please contact the admin or mail: xplore.support@tcs.com"
DATA:
{context}
"""
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": question}
        ]
    }
    res = requests.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers)
    return res.json()["choices"][0]["message"]["content"].strip()

# === Telegram Handlers ===
kb_chunks = extract_pdf_text(PDF_PATH)
last_qas = {}

def handle_message(update, context):
    user = update.effective_user
    question = update.message.text
    user_id = user.id

    update_user_log(user)
    increment_response()
    log_user_chat(user_id, "User", question)

    answer = answer_with_context(question, kb_chunks)
    log_user_chat(user_id, "Bot", answer)
    last_qas[user_id] = (question, answer)

    buttons = [[
        InlineKeyboardButton("✅ Satisfied", callback_data='satisfied'),
        InlineKeyboardButton("🚩 Report", callback_data='report')
    ]]
    context.bot.send_message(chat_id=update.effective_chat.id, text=answer, reply_markup=InlineKeyboardMarkup(buttons))

def button_handler(update: Update, context: CallbackContext):
    query_obj = update.callback_query
    query_obj.answer()
    user = query_obj.from_user
    user_id = user.id
    action = query_obj.data

    if action == "satisfied":
        increment_satisfied()
        query_obj.edit_message_reply_markup(reply_markup=None)
        query_obj.message.reply_text("✅ Thanks! Glad it helped.")
    elif action == "report" and user_id in last_qas:
        q, a = last_qas[user_id]
        log_report(q, a, user)
        query_obj.edit_message_reply_markup(reply_markup=None)
        query_obj.message.reply_text("🚩 Report noted.")

def start(update, context):
    context.bot.send_message(chat_id=update.effective_chat.id,
                             text="👋 Welcome to the TCS FAQ Bot! Ask any onboarding or IPA-related question.")

def main():
    updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    dp.add_handler(CallbackQueryHandler(button_handler))
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    while True:
        try:
            print("🚀 Starting bot...")
            main()
        except Exception as e:
            print(f"❌ Bot crashed with error: {e}")
            time.sleep(5)
from flask import Flask
import threading

app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run_web_server():
    app.run(host='0.0.0.0', port=8080)

# Start Flask server in parallel
threading.Thread(target=run_web_server).start()
