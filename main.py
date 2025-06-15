import asyncio
import logging
import sqlite3
import qrcode
import io
import math
import requests

from aiogram import Bot, Dispatcher, Router, F
from aiogram.enums import ParseMode
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, ReplyKeyboardRemove, InputFile
)
from aiogram.filters import Command
from aiogram import F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import BufferedInputFile
from aiogram.types import InlineKeyboardMarkup

from aiogram.utils.keyboard import InlineKeyboardBuilder
# --- CONFIG ---

ADMIN_ID = 5274097505
GROUP_ID = -1001234567890
SMM_API_KEY = "030721af5eaea75a86f77ebda0c74209"
SMM_API_URL = "https://easysmmpanel.com/api/v2"
UPI_ID = "kyakamhai@ybl"
SERVICES_PER_PAGE = 8

# Create bot and dispatcher
import os

API_TOKEN = "7542766614:AAEbytY4chkqjm_rMRLkXMHg8toFJEs2_ys"
bot = Bot(token=API_TOKEN, parse_mode=ParseMode.MARKDOWN)
dp = Dispatcher(storage=MemoryStorage())

# --- DB ---
conn = sqlite3.connect("db.sqlite3", check_same_thread=False)
cur = conn.cursor()
cur.executescript("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    name TEXT,
    phone TEXT,
    balance REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount REAL,
    txn_id TEXT UNIQUE,
    status TEXT DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    order_id TEXT,
    service_name TEXT,
    link TEXT,
    quantity INTEGER,
    price REAL,
    status TEXT
);
""")
conn.commit()

# --- STATES ---
class Register(StatesGroup):
    name = State()
    phone = State()

class AddBalance(StatesGroup):
    amount = State()
    txn_id = State()

class PlaceOrder(StatesGroup):
    svc_id = State()
    link = State()
    qty = State()





# --- KEYBOARDS ---
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def main_menu(balance=0):
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 My Wallet"), KeyboardButton(text="💰 Add Balance")],
            [KeyboardButton(text="📦 New Order"), KeyboardButton(text="📄 My Orders")],
            [KeyboardButton(text="📞 Contact Admin")]
        ],
        resize_keyboard=True
    )


def upi_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ I Paid", callback_data="paid_done")]
    ])
# --- ROUTER SETUP ---
router = Router()

@router.message(F.text == "/start")
async def cmd_start(m: Message, state: FSMContext):
    row = cur.execute("SELECT balance FROM users WHERE user_id=?", (m.from_user.id,)).fetchone()
    if row:
        bal = row[0]
        await m.answer(f"👋 Welcome back!\n💰 Balance: ₹{bal:.2f}", reply_markup=main_menu(bal))
    else:
        await m.answer("👋 Welcome! Please enter your full name:")
        await state.set_state(Register.name)

@router.message(Register.name)
async def reg_name(m: Message, state: FSMContext):
    await state.update_data(name=m.text.strip())
    await m.answer("📞 Enter your phone number:")
    await state.set_state(Register.phone)

@router.message(Register.phone)
async def reg_phone(m: Message, state: FSMContext):
    data = await state.get_data()
    name, phone = data["name"], m.text.strip()
    cur.execute("INSERT OR IGNORE INTO users(user_id, name, phone) VALUES (?, ?, ?)", (m.from_user.id, name, phone))
    conn.commit()
    await m.answer("✅ Registration complete!", reply_markup=main_menu())
    await state.clear()


# --- Cancel Command (Global)
@router.message(Command("cancel"))
async def cancel_any(message: Message, state: FSMContext):
    if await state.get_state() is None:
        return await message.answer("⚠️ Nothing to cancel.")
    await state.clear()
    await message.answer("❌ Operation cancelled.", reply_markup=main_menu())


# --- My Wallet Handler ---
@router.message(lambda m: m.text == "💰 My Wallet")
async def show_wallet(m: Message):
    bal = cur.execute("SELECT balance FROM users WHERE user_id=?", (m.from_user.id,)).fetchone()[0]
    await m.answer(f"💵 Current Balance: ₹{bal:.2f}")
@router.message(F.text == "💰 Add Balance")
async def prompt_amount(m: Message, state: FSMContext):
    bonus_msg = (
        "🎁 *Recharge Bonus Offers:*\n"
        "• ₹500 — _Get 2% Bonus_\n"
        "• ₹1000 — _Get 3% Bonus_\n"
        "• ₹2000+ — _Get 6% Bonus_\n\n"
        "💡 Bonus is applied automatically when your payment is approved."
    )
    await m.answer(bonus_msg, parse_mode="Markdown")
    await m.answer("💳 Enter the amount to add:")
    await state.set_state(AddBalance.amount)

@router.message(AddBalance.amount)
async def process_amount(m: Message, state: FSMContext):
    try:
        amt = round(float(m.text.strip()), 2)
        if amt <= 0:
            raise ValueError
    except ValueError:
        return await m.answer("❌ Invalid amount. Enter a number greater than 0.")

    await state.update_data(amount=amt)
    qr = f"upi://pay?pa={UPI_ID}&pn=SMMBot&am={amt}&cu=INR"
    img = qrcode.make(qr)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)

    await m.answer_photo(
        BufferedInputFile(buf.getvalue(), filename="qr.png"),
        caption=f"Scan & pay ₹{amt}, then click below.",
        reply_markup=upi_keyboard()
    )
    await state.set_state(AddBalance.txn_id)

@router.callback_query(F.data == "paid_done")
async def ask_txnid(c: CallbackQuery, state: FSMContext):
    await c.message.answer("📥 Enter your UPI Transaction ID:")
    await c.answer()

@router.message(AddBalance.txn_id)
async def save_txnid(m: Message, state: FSMContext):
    d = await state.get_data()
    amount, txn_id = d["amount"], m.text.strip()

    try:
        cur.execute("INSERT INTO payments(user_id, amount, txn_id) VALUES (?, ?, ?)", (m.from_user.id, amount, txn_id))
        conn.commit()
    except sqlite3.IntegrityError:
        return await m.answer("❗ This transaction ID is already used.")

    approve_btn = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Approve", callback_data=f"ap_{m.from_user.id}_{amount}")],
        [InlineKeyboardButton(text="❌ Decline", callback_data=f"de_{m.from_user.id}_{amount}")]
    ])

    await bot.send_message(ADMIN_ID, f"🧾 New Payment Request\nUser: {m.from_user.id}\nAmount: ₹{amount}\nTxn ID: {txn_id}", reply_markup=approve_btn)
    await m.answer("✅ Submitted for approval. You’ll be notified once processed.")
    await state.clear()

# --- Handle Admin Approval (Approve/Decline Payments) ---
@router.callback_query(F.data.startswith(("ap_", "de_")))
async def handle_payment_decision(c: CallbackQuery):
    action, uid, amt = c.data.split("_")
    uid = int(uid)
    amt = float(amt)
    status = "approved" if action == "ap" else "declined"

    cur.execute(
        "UPDATE payments SET status=? WHERE user_id=? AND ROUND(amount,2)=ROUND(?,2)",
        (status, uid, amt)
    )
    if action == "ap":
        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amt, uid))
        await bot.send_message(uid, f"✅ Your ₹{amt:.2f} payment has been approved!")
    else:
        await bot.send_message(uid, f"❌ Your ₹{amt:.2f} payment was declined.")
    conn.commit()
    await c.answer()



# 📦 Show available services

class PlaceOrder(StatesGroup):
    svc_id = State()
    svc_name = State()
    svc_rate = State()
    svc_link = State()
    svc_qty = State()

@router.message(F.text == "📦 New Order")
async def start_order(message: Message, state: FSMContext):
    response = requests.post(SMM_API_URL, data={"key": SMM_API_KEY, "action": "services"})
    if response.status_code != 200:
        return await message.answer("⚠️ Failed to fetch services.")

    services = response.json()
    await state.update_data(services=services)
    await show_services_page(message.chat.id, services, 0)

async def show_services_page(chat_id, services, page: int):
    per_page = 8
    start = page * per_page
    end = start + per_page
    buttons = []

    for svc in services[start:end]:
        buttons.append([InlineKeyboardButton(
            text=f"{svc['name']} ₹{svc['rate']}", callback_data=f"svc_{svc['service']}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"page_{page-1}"))
    if end < len(services):
        nav.append(InlineKeyboardButton(text="➡️ Next", callback_data=f"page_{page+1}"))
    if nav:
        buttons.append(nav)

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await bot.send_message(chat_id, f"📋 Choose a service (Page {page+1})", reply_markup=keyboard)

@router.callback_query(F.data.startswith("page_"))
async def paginate_services(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split("_")[1])
    data = await state.get_data()
    services = data.get("services", [])
    await callback.message.delete()
    await show_services_page(callback.message.chat.id, services, page)
    await callback.answer()

@router.callback_query(F.data.startswith("svc_"))
async def service_detail(callback: CallbackQuery, state: FSMContext):
    svc_id = callback.data.split("_")[1]
    data = await state.get_data()
    services = data.get("services", [])

    svc = next((s for s in services if str(s["service"]) == svc_id), None)
    if not svc:
        return await callback.answer("❌ Service not found", show_alert=True)

    rate_with_profit = round(float(svc['rate']) * 1.10, 2)
    await state.update_data(
        svc_id=svc_id,
        svc_name=svc["name"],
        svc_rate=rate_with_profit,
        svc_min=svc.get("min", "?"),
        svc_max=svc.get("max", "?")
    )

    text = (
        f"📌 *{svc['name']}*\n"
        f"{svc.get('description', 'No description available.')}\n"
        f"💰 Rate: ₹{rate_with_profit} per 1k units\n"
        f"🔢 Min: {svc.get('min', '?')} | Max: {svc.get('max', '?')}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Select", callback_data=f"select_{svc_id}")]
        ]
    )
    await callback.message.answer(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data.startswith("select_"))
async def input_link(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("🔗 Please send the link/username:")
    await state.set_state(PlaceOrder.svc_link)
    await callback.answer()

@router.message(PlaceOrder.svc_link)
async def input_quantity(message: Message, state: FSMContext):
    await state.update_data(svc_link=message.text.strip())
    await message.answer("📦 Enter quantity:")
    await state.set_state(PlaceOrder.svc_qty)

@router.message(PlaceOrder.svc_qty)
async def confirm_order(message: Message, state: FSMContext):
    try:
        qty = int(message.text.strip())
        assert qty > 0
    except:
        return await message.answer("❌ Invalid quantity.")

    data = await state.get_data()
    rate = float(data['svc_rate'])
    cost = round(qty * rate / 1000, 2)

    user_balance = cur.execute("SELECT balance FROM users WHERE user_id=?", (message.from_user.id,)).fetchone()
    if not user_balance or user_balance[0] < cost:
        return await message.answer("❌ Insufficient balance.")

    await state.update_data(svc_qty=qty, svc_cost=cost)

    text = (
        f"⚠️ Please confirm your order:\n\n"
        f"📦 *Service:* {data['svc_name']}\n"
        f"🔗 *Link:* {data['svc_link']}\n"
        f"🔢 *Qty:* {qty}\n"
        f"💰 *Cost:* ₹{cost:.2f}"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirm Order", callback_data="confirm_order")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_order")]
    ])
    await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

@router.callback_query(F.data == "confirm_order")
async def place_final_order(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_id = callback.from_user.id

    # SMM API Order Placement
    response = requests.post(SMM_API_URL, data={
        "key": SMM_API_KEY,
        "action": "add",
        "service": data['svc_id'],
        "link": data['svc_link'],
        "quantity": data['svc_qty']
    })

    resp_json = response.json()
    if 'order' not in resp_json:
        await callback.message.answer(f"❌ Failed: {resp_json.get('error', 'Unknown error')}")
        return await state.clear()

    order_id = str(resp_json['order'])
    cost = data['svc_cost']
    qty = data['svc_qty']

    # Deduct balance
    cur.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (cost, user_id))
    # Save order
    cur.execute("""
        INSERT INTO orders(user_id, order_id, service_name, link, quantity, price, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user_id, order_id, data['svc_name'], data['svc_link'], qty, cost, 'pending'))
    conn.commit()

    # Notify user
    await callback.message.answer(f"✅ Order placed!\n🆔 ID: {order_id}\n💰 Cost: ₹{cost:.2f}")

    # Notify admin & group
    user_row = cur.execute("SELECT name FROM users WHERE user_id=?", (user_id,)).fetchone()
    user_name = user_row[0] if user_row else "Unknown"
    notif_msg = (
        f"📥 *New Order*\n"
        f"👤 `{user_id}` ({user_name})\n"
        f"🆔 Order: `{order_id}`\n"
        f"📦 {data['svc_name']}\n"
        f"🔗 {data['svc_link']}\n"
        f"🔢 Qty: {qty}\n"
        f"💰 ₹{cost:.2f}\n"
        f"⏳ Status: pending"
    )
    try:
        await bot.send_message(ADMIN_ID, notif_msg, parse_mode="Markdown")
        await bot.send_message(GROUP_ID, notif_msg, parse_mode="Markdown")
    except Exception as e:
        print("❗ Notify failed:", e)

    await state.clear()

@router.callback_query(F.data == "cancel_order")
async def cancel_order_callback(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("❌ Order cancelled.")
    await state.clear()

# 📄 My Orders
@router.message(F.text == "📄 My Orders")
async def view_orders(message: Message):
    rows = cur.execute(
        "SELECT order_id, service_name, quantity, price, status FROM orders WHERE user_id=?",
        (message.from_user.id,)
    ).fetchall()

    if not rows:
        return await message.answer("❌ You haven't placed any orders yet.")

    msg = "📦 *Your Orders:*\n\n"
    for r in rows:
        msg += f"🆔 Order #{r[0]}\n📦 {r[1]}\n🔢 Qty: {r[2]}\n💰 ₹{r[3]:.2f}\n📊 Status: {r[4]}\n\n"

    await message.answer(msg, parse_mode="Markdown")


# --- Contact Admin Handler ---
@router.message(F.text == "📞 Contact Admin")
async def contact_admin(m: Message):
    await m.answer("📩 Contact support: @sastasmmhelper_bot", parse_mode=None)


# --- /addbalance command ---
@router.message(Command("addbalance"))
async def add_balance_cmd(m: Message):
    if m.from_user.id != ADMIN_ID:
        return await m.answer("❌ Unauthorized.")
    parts = m.text.split()
    if len(parts) != 3:
        return await m.answer("Usage: /addbalance <user_id> <amount>")
    try:
        uid = int(parts[1])
        amt = float(parts[2])
    except ValueError:
        return await m.answer("❌ Invalid format.")

    user = cur.execute("SELECT balance FROM users WHERE user_id = ?", (uid,)).fetchone()
    if not user:
        return await m.answer("❌ User not found.")

    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amt, uid))
    conn.commit()

    await m.answer(f"✅ ₹{amt:.2f} added to user {uid}")
    await bot.send_message(uid, f"✅ ₹{amt:.2f} has been added to your wallet by the admin.")

# --- /deduct command ---
@router.message(Command("deduct"))
async def deduct_balance_cmd(m: Message):
    if m.from_user.id != ADMIN_ID:
        return await m.answer("❌ Unauthorized.")
    parts = m.text.split()
    if len(parts) != 3:
        return await m.answer("Usage: /deduct <user_id> <amount>")
    try:
        uid = int(parts[1])
        amt = float(parts[2])
    except ValueError:
        return await m.answer("❌ Invalid format.")
    bal = cur.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
    if not bal:
        return await m.answer("❌ User not found.")
    if bal[0] < amt:
        return await m.answer("❌ Insufficient balance.")
    cur.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amt, uid))
    conn.commit()
    await m.answer(f"✅ ₹{amt:.2f} deducted from user {uid}")
    await bot.send_message(uid, f"⚠️ ₹{amt:.2f} was deducted from your wallet by the admin.")

# --- /bonusadd command ---
@router.message(Command("bonusadd"))
async def add_bonus_command(m: Message):
    if m.from_user.id != ADMIN_ID:
        return await m.answer("❌ Unauthorized.")
    try:
        parts = m.text.split()
        if len(parts) != 3:
            return await m.answer("❌ Usage: /bonusadd <user_id> <amount>")
        user_id = int(parts[1])
        bonus = float(parts[2])
        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (bonus, user_id))
        conn.commit()
        await m.answer(f"✅ ₹{bonus} bonus added to user `{user_id}`", parse_mode="Markdown")
        await bot.send_message(
            user_id,
            f"🤝 A bonus of ₹{bonus} was granted to your account by support. Thanks for using our panel."
        )
    except Exception as e:
        await m.answer(f"⚠️ Error: {e}")

# --- /checkbalance command ---
@router.message(Command("checkbalance"))
async def check_balance_cmd(m: Message):
    if m.from_user.id != ADMIN_ID:
        return await m.answer("❌ Unauthorized.")
    parts = m.text.split()
    if len(parts) != 2:
        return await m.answer("Usage: /checkbalance <user_id>")
    try:
        uid = int(parts[1])
    except ValueError:
        return await m.answer("❌ Invalid user ID.")
    row = cur.execute("SELECT balance FROM users WHERE user_id = ?", (uid,)).fetchone()
    if not row:
        return await m.answer("❌ User not found.")
    bal = row[0]
    await m.answer(f"👤 User ID: {uid}\n💰 Balance: ₹{bal:.2f}")


# --- /userorders command ---
@router.message(Command("userorders"))
async def user_orders_cmd(m: Message):
    if m.from_user.id != ADMIN_ID:
        return await m.answer("❌ Unauthorized.")
    parts = m.text.split()
    if len(parts) != 2:
        return await m.answer("Usage: /userorders <user_id>")
    try:
        uid = int(parts[1])
    except ValueError:
        return await m.answer("❌ Invalid user ID.")
    rows = cur.execute(
        "SELECT order_id, service_name, quantity, price, status FROM orders WHERE user_id=?",
        (uid,)
    ).fetchall()
    if not rows:
        return await m.answer("No orders found.")
    msg = f"📦 Order history for user {uid}:\n\n" + "\n\n".join(
        [f"#{r[0]} • {r[1]} x{r[2]} • ₹{r[3]:.2f} • {r[4]}" for r in rows])
    await m.answer(msg)


# --- /listusers command ---
@router.message(Command("listusers"))
async def list_users_cmd(m: Message):
    if m.from_user.id != ADMIN_ID:
        return await m.answer("❌ Unauthorized.")
    rows = cur.execute("SELECT user_id, name, phone, balance FROM users").fetchall()
    if not rows:
        return await m.answer("No users found.")
    msg = "👥 Registered Users:\n\n" + "\n".join(
        [f"{r[0]} • {r[1]} • {r[2]} • ₹{r[3]:.2f}" for r in rows])
    await m.answer(msg)


# --- /stats command ---
@router.message(Command("stats"))
async def stats_cmd(m: Message):
    if m.from_user.id != ADMIN_ID:
        return await m.answer("❌ Unauthorized.")
    total_users = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_orders = cur.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    revenue = cur.execute("SELECT SUM(price) FROM orders").fetchone()[0] or 0.0
    revenue = round(float(revenue), 2)

    msg = (
        "📊 Bot Statistics:\n"
        f"• Total Users: {total_users}\n"
        f"• Total Orders: {total_orders}\n"
        f"• Total Revenue: ₹{revenue:.2f}"
    )
    await m.answer(msg)

#orderupdate
from aiogram.filters import Command
from aiogram import Router
from aiogram.types import Message

admin_router = Router()

@admin_router.message(Command("update_orders"))
async def update_all_orders(message: Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ You are not authorized.")

    pending_orders = cur.execute(
        "SELECT order_id, user_id FROM orders WHERE status = 'pending'"
    ).fetchall()

    if not pending_orders:
        return await message.answer("✅ No pending orders to update.")

    updated_count = 0
    for order_id, user_id in pending_orders:
        try:
            resp = requests.post(SMM_API_URL, data={
                "key": SMM_API_KEY,
                "action": "status",
                "order": order_id
            }).json()

            new_status = resp.get("status")
            if new_status and new_status != "pending":
                cur.execute("UPDATE orders SET status=? WHERE order_id=?",
                            (new_status, order_id))
                conn.commit()
                updated_count += 1

                # Optional: notify user
                try:
                    await bot.send_message(
                        user_id,
                        f"📦 Your order `{order_id}` status has been updated to *{new_status}*.",
                        parse_mode="Markdown"
                    )
                except:
                    pass  # User may have blocked the bot

        except Exception as e:
            print(f"Error updating order {order_id}:", e)

    await message.answer(f"✅ Updated {updated_count} orders.")



# --- Bot Startup ---
async def main():
    dp.include_router(router)
    dp.include_router(admin_router)
    dp.services_cache = []  # Used in service pagination
    logging.info("Starting bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

