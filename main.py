import os
import requests
from docx import Document
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, CallbackContext

# Load API keys
def load_keys(path="key.txt"):
    with open(path, "r") as f:
        lines = f.readlines()
        return lines[0].strip(), lines[1].strip()

OPENROUTER_API_KEY, TELEGRAM_TOKEN = load_keys()
MODEL_NAME = "mistralai/mixtral-8x7b-instruct"
DATA_FOLDER = "data"

# Ensure tracking files
def ensure_files():
    for f in ["response.txt", "report.txt", "user.txt"]:
        if not os.path.exists(f):
            with open(f, "w", encoding="utf-8") as file:
                file.write("0\n0" if f == "response.txt" else "")

ensure_files()

# Track total responses and satisfaction
def increment_response():
    with open("response.txt", "r+") as f:
        lines = f.readlines()
        total = int(lines[0]) + 1
        satisfied = int(lines[1])
        f.seek(0)
        f.write(f"{total}\n{satisfied}")
        f.truncate()

def increment_satisfied():
    with open("response.txt", "r+") as f:
        lines = f.readlines()
        total = int(lines[0])
        satisfied = int(lines[1]) + 1
        f.seek(0)
        f.write(f"{total}\n{satisfied}")
        f.truncate()

# Log user data
def update_user_log(user):
    uid = str(user.id)
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    uname = f"@{user.username}" if user.username else "N/A"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    data = {}
    if os.path.exists("user.txt"):
        with open("user.txt", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split(" | ")
                if len(parts) >= 6:
                    data[parts[0]] = {
                        "name": parts[1], "username": parts[2],
                        "first_seen": parts[3].replace("First: ", ""),
                        "last_seen": parts[4].replace("Last: ", ""),
                        "queries": int(parts[5].split()[0])
                    }

    if uid in data:
        data[uid]["last_seen"] = now
        data[uid]["queries"] += 1
    else:
        data[uid] = {"name": name, "username": uname, "first_seen": now, "last_seen": now, "queries": 1}

    with open("user.txt", "w", encoding="utf-8") as f:
        for uid, d in data.items():
            f.write(f"{uid} | {d['name']} | {d['username']} | First: {d['first_seen']} | Last: {d['last_seen']} | {d['queries']} queries\n")

# Chat logs per user
def log_user_chat(user_id, role, message):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(f"{user_id}.txt", "a", encoding="utf-8") as f:
        f.write(f"[{now}] {role}: {message}\n")

# Report log
def log_report(query, answer, user):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open("report.txt", "a", encoding="utf-8") as f:
        f.write(f"[{now}] ID: {user.id} | {user.first_name} | @{user.username or 'N/A'}\n")
        f.write(f"Query: {query}\nResponse: {answer}\n---\n")

# Extract text from .docx
def extract_docx_text(path):
    doc = Document(path)
    return "\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())[:6000]

# AI check: is question related to document?
def query_is_related_to_doc(question, doc_text):
    prompt = f"""
Answer with 'yes' or 'no' only. Is the following question related to this document?

DOCUMENT:
{doc_text[:3000]}

QUESTION:
{question}
"""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "Answer strictly with yes or no."},
            {"role": "user", "content": prompt}
        ]
    }
    try:
        res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
        print(res.json()['choices'][0]['message']['content'].strip().lower())
        return "yes" in res.json()['choices'][0]['message']['content'].strip().lower()
    except:
        return False

# AI: Answer using doc
def answer_with_doc_kb(question, kb_text):
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    system_msg = f"""
You are a TCS onboarding assistant. Answer ONLY using the document below.
If unsure, reply:
❗ Sorry, based on the current document, I don't know the answer.

DOCUMENT:
{kb_text}
"""
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": question}
        ]
    }
    res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
    return res.json()["choices"][0]["message"]["content"].strip()

# AI fallback
def answer_freely(question):
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": question}
        ]
    }
    res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
    return res.json()["choices"][0]["message"]["content"].strip()

# Main chat handler
last_qas = {}

def handle_message(update, context):
    user = update.effective_user
    question = update.message.text
    user_id = user.id

    update_user_log(user)
    increment_response()
    log_user_chat(user_id, "User", question)

    selected_text = None

    # Check each document until one matches
    for file in os.listdir(DATA_FOLDER):
        if file.endswith(".docx"):
            path = os.path.join(DATA_FOLDER, file)
            try:
                doc_text = extract_docx_text(path)
                if query_is_related_to_doc(question, doc_text):
                    selected_text = doc_text
                    break  # stop at first related document
            except Exception as e:
                print(f"Error checking {file}: {e}")

    if selected_text:
        answer = answer_with_doc_kb(question, selected_text)
    else:
        answer = answer_freely(question)

    log_user_chat(user_id, "Bot", answer)
    last_qas[user_id] = (question, answer)

    buttons = [[
        InlineKeyboardButton("✅ Satisfied", callback_data='satisfied'),
        InlineKeyboardButton("🚩 Report", callback_data='report')
    ]]
    context.bot.send_message(chat_id=update.effective_chat.id, text=answer, reply_markup=InlineKeyboardMarkup(buttons))

# Button handler
def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    user = query.from_user
    user_id = user.id
    query.answer()

    if query.data == "satisfied":
        increment_satisfied()
        query.edit_message_reply_markup(reply_markup=None)
        query.message.reply_text("✅ Thanks! Glad it helped.")
    elif query.data == "report" and user_id in last_qas:
        q, a = last_qas[user_id]
        log_report(q, a, user)
        query.edit_message_reply_markup(reply_markup=None)
        query.message.reply_text("🚩 Report noted.")

def start(update, context):
    context.bot.send_message(chat_id=update.effective_chat.id,
        text="👋 Welcome to the TCS FAQ Bot! Ask any onboarding, BGC, IPA, or joining related question.")

def main():
    updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    dp.add_handler(CallbackQueryHandler(button_handler))
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
