import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask
from threading import Thread
import urllib.parse

# --- KEEP ALIVE SERVER ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is running and healthy!"

def run_web():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    Thread(target=run_web).start()

# --- CONFIG ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))
UPI_ID = os.getenv('UPI_ID')
CONTACT_USERNAME = os.getenv('CONTACT_USERNAME')

bot = telebot.TeleBot(BOT_TOKEN)

client = MongoClient(MONGO_URI)
db = client['sub_management']
channels_col = db['channels']
users_col = db['users']

BOT_USERNAME = bot.get_me().username


# --- START ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.from_user.id
    text = message.text.split()

    if len(text) > 1:
        try:
            ch_id = int(text[1])
            ch_data = channels_col.find_one({"channel_id": ch_id})

            if ch_data:
                markup = InlineKeyboardMarkup()

                for p_time, p_price in ch_data['plans'].items():
                    label = f"{p_time} Min" if int(p_time) < 60 else f"{int(p_time)//1440} Days"
                    markup.add(
                        InlineKeyboardButton(
                            f"💳 {label} - ₹{p_price}",
                            callback_data=f"select_{ch_id}_{p_time}"
                        )
                    )

                markup.add(
                    InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{CONTACT_USERNAME}")
                )

                bot.send_message(
                    message.chat.id,
                    f"Welcome!\n\nYou are joining: *{ch_data['name']}*.\n\nSelect plan:",
                    reply_markup=markup,
                    parse_mode="Markdown"
                )
                return
        except:
            pass

    if user_id == ADMIN_ID:
        bot.send_message(message.chat.id,
                         "✅ Admin Panel\n/add - Add Channel\n/channels - Manage Channels")
    else:
        bot.send_message(message.chat.id,
                         "Welcome! Use admin link to join channel.")


# --- LIST CHANNELS ---
@bot.message_handler(commands=['channels'], func=lambda m: m.from_user.id == ADMIN_ID)
def list_channels(message):
    markup = InlineKeyboardMarkup()
    cursor = channels_col.find({"admin_id": ADMIN_ID})

    count = 0
    for ch in cursor:
        markup.add(InlineKeyboardButton(
            f"Channel: {ch['name']}",
            callback_data=f"manage_{ch['channel_id']}"
        ))
        count += 1

    markup.add(InlineKeyboardButton("➕ Add New Channel", callback_data="add_new"))

    bot.send_message(
        ADMIN_ID,
        "Your Channels:" if count else "No channels found.",
        reply_markup=markup
    )


# --- ADD CHANNEL ---
@bot.message_handler(commands=['add'], func=lambda m: m.from_user.id == ADMIN_ID)
def add_channel(message):
    msg = bot.send_message(
        ADMIN_ID,
        "Forward any message from your channel here."
    )
    bot.register_next_step_handler(msg, get_plans)


@bot.callback_query_handler(func=lambda call: call.data == "add_new")
def cb_add(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(ADMIN_ID, "Forward channel message here.")
    bot.register_next_step_handler(msg, get_plans)


def get_plans(message):
    if message.forward_from_chat:
        ch_id = message.forward_from_chat.id
        ch_name = message.forward_from_chat.title

        msg = bot.send_message(
            ADMIN_ID,
            f"Channel: *{ch_name}*\n\nEnter plans:\n`1440:99, 43200:199`",
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(msg, finalize_channel, ch_id, ch_name)
    else:
        bot.send_message(ADMIN_ID, "❌ Forward message required.")

def finalize_channel(message, ch_id, ch_name):
    try:
        text = message.text.strip()

        plans = {}

        for item in text.split(","):
            item = item.strip()

            if ":" not in item:
                bot.send_message(
                    ADMIN_ID,
                    "❌ Invalid format.\n\nExample:\n1440:99,43200:199"
                )
                return

            time_str, price_str = item.split(":", 1)

            time_str = time_str.strip()
            price_str = price_str.strip()

            if not time_str.isdigit():
                bot.send_message(
                    ADMIN_ID,
                    "❌ Time must be a number.\nExample:\n1440:99"
                )
                return

            try:
                price = int(price_str)
            except ValueError:
                bot.send_message(
                    ADMIN_ID,
                    "❌ Price must be a number.\nExample:\n1440:99"
                )
                return

            plans[time_str] = price

        channels_col.update_one(
            {"channel_id": ch_id},
            {
                "$set": {
                    "name": ch_name,
                    "plans": plans,
                    "admin_id": ADMIN_ID
                }
            },
            upsert=True
        )

        bot.send_message(
            ADMIN_ID,
            f"✅ Channel Added Successfully!\n\nJoin Link:\nhttps://t.me/{BOT_USERNAME}?start={ch_id}"
        )

    except Exception as e:
        bot.send_message(
            ADMIN_ID,
            f"❌ Error:\n{str(e)}"
        )


# --- PAYMENT ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('select_'))
def user_pay(call):
    _, ch_id, mins = call.data.split('_')

    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][mins]

    upi_link = f"upi://pay?pa={UPI_ID}&am={price}&cu=INR"
    qr_url = "https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=" + urllib.parse.quote(upi_link)

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Paid", callback_data=f"paid_{ch_id}_{mins}"))

    bot.send_photo(
        call.message.chat.id,
        qr_url,
        caption=f"Pay ₹{price}",
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('paid_'))
def paid(call):
    _, ch_id, mins = call.data.split('_')

    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][mins]

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("Approve", callback_data=f"app_{call.from_user.id}_{ch_id}_{mins}"))

    bot.send_message(
        ADMIN_ID,
        f"Payment request\nUser: {call.from_user.id}\nPlan: {mins} min\n₹{price}",
        reply_markup=markup
    )

    bot.send_message(call.message.chat.id, "Waiting for approval...")


# --- APPROVE ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('app_'))
def approve(call):
    _, u_id, ch_id, mins = call.data.split('_')
    u_id, ch_id, mins = int(u_id), int(ch_id), int(mins)

    expiry_datetime = datetime.now() + timedelta(minutes=mins)

    link = bot.create_chat_invite_link(
        ch_id,
        member_limit=1,
        expire_date=expiry_datetime
    )

    users_col.update_one(
        {"user_id": u_id, "channel_id": ch_id},
        {"$set": {"expiry": expiry_datetime.timestamp()}},
        upsert=True
    )

    bot.send_message(
        u_id,
        f"Approved!\nJoin: {link.invite_link}"
    )

    bot.edit_message_text("Approved", call.message.chat.id, call.message.message_id)


# --- EXPIRE CHECK ---
def kick_expired():
    now = datetime.now().timestamp()
    expired = users_col.find({"expiry": {"$lte": now}})

    for u in expired:
        try:
            bot.ban_chat_member(u['channel_id'], u['user_id'])
            bot.unban_chat_member(u['channel_id'], u['user_id'])
            users_col.delete_one({"_id": u["_id"]})
        except:
            pass


# --- START ---
if __name__ == '__main__':
    keep_alive()

    scheduler = BackgroundScheduler()
    scheduler.add_job(kick_expired, 'interval', minutes=1)
    scheduler.start()

    bot.remove_webhook()
    print("Bot running...")
    bot.infinity_polling()
