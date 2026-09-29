import logging
import sqlite3
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import database as db

logging.basicConfig(level=logging.INFO)

# --- الإعدادات (قم بتغييرها بما يناسبك) ---
BOT_TOKEN = "ضع_توكن_البوت_هنا"
ADMIN_ID = 123456789  # ضع الآيدي الخاص بحسابك بالتليجرام هنا لتدخل كأدمن
WEB_APP_URL = "https://yourdomain.com" # رابط واجهة التعدين المستضافة لاحقاً
SECRET_TOKEN = "MY_SUPER_SECRET_KEY_123" # مفتاح الأمان للربط مع جافاسكريبت

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

class RewardRequest(BaseModel):
    user_id: int
    amount: float
    secret_key: str

class WithdrawalStates(StatesGroup):
    waiting_for_wallet = State()

@app.post("/api/reward")
async def give_reward(data: RewardRequest):
    if data.secret_key != SECRET_TOKEN:
        raise HTTPException(status_code=403, detail="غير مصرح به")
    user = db.get_user(data.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="المستخدم غير موجود")
    db.update_balance(data.user_id, data.amount)
    return {"status": "success"}

@dp.message(Command("start"))
async def start_command(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    username = message.from_user.username or "User"
    referrer_id = int(command.args) if command.args and command.args.isdigit() and int(command.args) != user_id else None
    
    db.register_user(user_id, username, referrer_id)
    
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="⛏️ افتح تطبيق KAK Mining", web_app=types.WebAppInfo(url=f"{WEB_APP_URL}?user={user_id}")))
    builder.row(types.InlineKeyboardButton(text="💰 حسابي وتحويل العملات", callback_data="my_balance"))
    builder.row(types.InlineKeyboardButton(text="👥 رابط الإحالة", callback_data="invite_link"))
    
    welcome = "مرحباً بك في بوت عملة **KAK**! 🚀\n\n الباقة المجانية تمنحك 10 KAK يومياً.\n مكافأة الإحالة 50 KAK.\n السحب: كل 20,000 KAK تعادل 1\$ بعملة TON إلى Tonkeeper يدوياً."
    await message.reply(welcome, reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(lambda c: c.data == "my_balance")
async def show_balance(callback: types.CallbackQuery):
    balance = db.get_user(callback.from_user.id)
    if balance is None: return await callback.answer("أرسل /start أولاً")
    usdt_value = balance / 20000
    
    text = f"📊 **تفاصيل رصيدك المالي:**\n\n🪙 رصيدك الحالي: `{balance:.2f} KAK`\n💵 القيمة بالدولار: `{usdt_value:.4f} $`"
    builder = InlineKeyboardBuilder()
    if balance >= 20000:
        builder.row(types.InlineKeyboardButton(text="💳 سحب الأرباح إلى Tonkeeper", callback_data="request_withdraw"))
    builder.row(types.InlineKeyboardButton(text="🔄 تحديث", callback_data="my_balance"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(lambda c: c.data == "invite_link")
async def show_invite(callback: types.CallbackQuery):
    bot_info = await bot.get_me()
    await callback.message.reply(f"👥 **رابط الإحالة الخاص بك:**\n`https://t.me{bot_info.username}?start={callback.from_user.id}`\n\nلكل صديق يدخل ستحصل على 50 KAK!", parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "request_withdraw")
async def start_withdrawal(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.reply("📍 يرجى إرسال عنوان محفظة TON الخاصة بك من تطبيق **Tonkeeper**:")
    await state.set_state(WithdrawalStates.waiting_for_wallet)
    await callback.answer()

@dp.message(WithdrawalStates.waiting_for_wallet)
async def process_wallet(message: types.Message, state: FSMContext):
    wallet = message.text.strip()
    if not (wallet.startswith("EQ") or wallet.startswith("UQ")) or len(wallet) < 40:
        return await message.reply("❌ عنوان المحفظة غير صحيح. تأكد منه وأرسله مجدداً.")
    
    balance = db.get_user(message.from_user.id)
    if balance < 20000:
        await state.clear()
        return await message.reply("❌ رصيدك أقل من الحد الأدنى (20,000 KAK).")
    
    db.create_withdrawal(message.from_user.id, wallet, balance, balance / 20000)
    await state.clear()
    await message.reply("✅ تم تسجيل طلب السحب بنجاح! سيتم مراجعته وإرسال الـ TON لمحفظتك يدوياً خلال 24 ساعة.")

# --- لوحة التحكم للأدمن (الإدارة اليدوية) ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect("kak_mining.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_id, ton_wallet, kak_amount, usdt_value FROM withdrawals WHERE status = 'PENDING'")
    requests = cursor.fetchall()
    conn.close()
    
    if not requests: return await message.reply("📥 لا توجد طلبات سحب معلقة.")
    
    for req in requests:
        req_id, u_id, wallet, kak, usdt = req
        text = f"🆔 **طلب رقم:** #{req_id}\n👤 **المستخدم:** `{u_id}`\n🪙 **المبلغ:** {kak} KAK\n💵 **القيمة:** {usdt:.2f} \$\n👛 **المحفظة:**\n`{wallet}`"
        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text="✅ تم الدفع وتأكيد السحب", callback_data=f"confirm_{req_id}"), types.InlineKeyboardButton(text="❌ إلغاء وإعادة الرصيد", callback_data=f"reject_{req_id}"))
        await message.reply(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(lambda c: c.data.startswith("confirm_") or c.data.startswith("reject_"))
async def handle_admin_action(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    action, req_id = callback.data.split("_")
    req_id = int(req_id)
    
    conn = sqlite3.connect("kak_mining.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, kak_amount FROM withdrawals WHERE id = ?", (req_id,))
    req_data = cursor.fetchone()
    if not req_data: return conn.close()
    user_id, kak_amount = req_data
    
    if action == "confirm":
        cursor.execute("UPDATE withdrawals SET status = 'COMPLETED' WHERE id = ?", (req_id,))
        await bot.send_message(user_id, f"🎉 تمت معالجة سحبك بنجاح! تم إرسال الأرباح المقابلة لـ `{kak_amount} KAK` لـ Tonkeeper.")
    elif action == "reject":
        cursor.execute("UPDATE withdrawals SET status = 'REJECTED' WHERE id = ?", (req_id,))
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (kak_amount, user_id))
        await bot.send_message(user_id, f"⚠️ تم رفض طلب سحبك وإعادة رصيد `{kak_amount} KAK` لحسابك.")
    conn.commit()
    conn.close()
    await callback.message.edit_text(f"✅ تم معالجة الإجراء بنجاح للطلب #{req_id}.")

async def main():
    asyncio.create_task(uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8000)).serve())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
