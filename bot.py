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
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn
import database as db

# تفعيل تسجيل الأخطاء والمراقبة
logging.basicConfig(level=logging.INFO)

# =================== الإعدادات الأساسية ===================
BOT_TOKEN = "ضع_توكن_البوت_هنا"
ADMIN_ID = 123456789  # ضع الآيدي الخاص بحسابك بالتليجرام هنا لتدخل كأدمن
SECRET_TOKEN = "MY_SUPER_SECRET_KEY_123" # مفتاح الأمان للربط مع جافاسكريبت

# ⚠️ ملاحظة: عند رفع السيرفر على Render واكتمال البناء بنجاح،
# قم بتغيير الرابط أدناه إلى رابط الـ Render الخاص بك الذي ستحصل عليه.
WEB_APP_URL = "https://onrender.com" 
# ========================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()

# تفعيل الـ CORS لتسمح لصفحة الويب بالاتصال بالسيرفر دون مشاكل أمنية
app.add_middleware(
    CORSMiddleware, 
    allow_origins=["*"], 
    allow_credentials=True, 
    allow_methods=["*"], 
    allow_headers=["*"]
)

# تشغيل وعرض ملفات الواجهة بتبويباتها (مجلد web) من داخل سيرفر بايثون مباشرة على Render
app.mount("/", StaticFiles(directory="web", html=True), name="web")

class RewardRequest(BaseModel):
    user_id: int
    amount: float
    secret_key: str

class WithdrawalStates(StatesGroup):
    waiting_for_wallet = State()

# رابط استقبال طلبات تحديث الرصيد عند إكمال المهام أو مشاهدة الإعلانات بالتبويبات
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
    
    welcome = (
        "مرحباً بك في بوت عملة **KAK** الرسمية! 🚀\n\n"
        "🔹 **الباقة المجانية:** تمنحك 10 KAK يومياً تلقائياً.\n"
        "🔹 **مكافأة الإحالة:** 50 KAK لكل صديق يدخل عبر رابطك.\n"
        "🔹 **السحب:** كل 20,000 KAK تعادل 1\\$ بعملة TON يتم إرسالها إلى محفظة Tonkeeper يدوياً من قِبل الإدارة."
    )
    await message.reply(welcome, reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(lambda c: c.data == "my_balance")
async def show_balance(callback: types.CallbackQuery):
    balance = db.get_user(callback.from_user.id)
    if balance is None: 
        return await callback.answer("أرسل /start أولاً لتفعيل حسابك")
    usdt_value = balance / 20000
    
    text = (
        f"📊 **تفاصيل رصيدك المالي:**\n\n"
        f"🪙 رصيدك الحالي: `{balance:.2f} KAK`\n"
        f"💵 القيمة بالدولار: `{usdt_value:.4f} $`"
    )
    builder = InlineKeyboardBuilder()
    if balance >= 20000:
        builder.row(types.InlineKeyboardButton(text="💳 سحب الأرباح إلى Tonkeeper", callback_data="request_withdraw"))
    builder.row(types.InlineKeyboardButton(text="🔄 تحديث الرصيد", callback_data="my_balance"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(lambda c: c.data == "invite_link")
async def show_invite(callback: types.CallbackQuery):
    bot_info = await bot.get_me()
    await callback.message.reply(
        f"👥 **رابط الإحالة الخاص بك:**\n"
        f"`https://t.me{bot_info.username}?start={callback.from_user.id}`\n\n"
        f"لكل صديق يسجل من خلالك، ستحصل فوراً على **50 KAK** مكافأة في حسابك!", 
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "request_withdraw")
async def start_withdrawal(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.reply("📍 يرجى إرسال عنوان محفظة TON الخاصة بك من تطبيق **Tonkeeper** الآن:")
    await state.set_state(WithdrawalStates.waiting_for_wallet)
    await callback.answer()

@dp.message(WithdrawalStates.waiting_for_wallet)
async def process_wallet(message: types.Message, state: FSMContext):
    wallet = message.text.strip()
    if not (wallet.startswith("EQ") or wallet.startswith("UQ")) or len(wallet) < 40:
        return await message.reply("❌ عنوان المحفظة غير صحيح. تأكد من نسخه بدقة وأرسله مجدداً.")
    
    balance = db.get_user(message.from_user.id)
    if balance < 20000:
        await state.clear()
        return await message.reply("❌ رصيدك أقل من الحد الأدنى للسحب وهو (20,000 KAK).")
    
    db.create_withdrawal(message.from_user.id, wallet, balance, balance / 20000)
    await state.clear()
    await message.reply("✅ **تم تسجيل طلب السحب بنجاح!**\n\nستقوم الإدارة بمراجعة الطلب يدويًا وإرسال الـ TON المقابل لمحفظتك بـ Tonkeeper خلال 24 ساعة.")

# --- لوحة التحكم للأدمن (الإدارة اليدوية لطلبات السحب) ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: 
        return
    conn = sqlite3.connect("kak_mining.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_id, ton_wallet, kak_amount, usdt_value FROM withdrawals WHERE status = 'PENDING'")
    requests = cursor.fetchall()
    conn.close()
    
    if not requests: 
        return await message.reply("📥 لا توجد طلبات سحب معلقة حالياً.")
    
    for req in requests:
        req_id, u_id, wallet, kak, usdt = req
        text = (
            f"🆔 **طلب رقم:** #{req_id}\n"
            f"👤 **المستخدم:** `{u_id}`\n"
            f"🪙 **المبلغ المطلوب:** {kak} KAK\n"
            f"💵 **القيمة المستحقة:** {usdt:.2f} \\$\n"
            f"👛 **المحفظة:**\n`{wallet}`"
        )
        builder = InlineKeyboardBuilder()
        builder.row(
            types.InlineKeyboardButton(text="✅ تم الدفع وتأكيد السحب", callback_data=f"confirm_{req_id}"), 
            types.InlineKeyboardButton(text="❌ إلغاء وإعادة الرصيد", callback_data=f"reject_{req_id}")
        )
        await message.reply(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(lambda c: c.data.startswith("confirm_") or c.data.startswith("reject_"))
async def handle_admin_action(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: 
        return
    action, req_id = callback.data.split("_")
    req_id = int(req_id)
    
    conn = sqlite3.connect("kak_mining.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, kak_amount FROM withdrawals WHERE id = ?", (req_id,))
    req_data = cursor.fetchone()
    if not req_data: 
        conn.close()
        return
    user_id, kak_amount = req_data
    
    if action == "confirm":
        cursor.execute("UPDATE withdrawals SET status = 'COMPLETED' WHERE id = ?", (req_id,))
        await bot.send_message(user_id, f"🎉 **تمت معالجة سحبك بنجاح!**\nتمت مراجعة حسابك وإرسال الأرباح المقابلة لـ `{kak_amount} KAK` لـ Tonkeeper.")
    elif action == "reject":
        cursor.execute("UPDATE withdrawals SET status = 'REJECTED' WHERE id = ?", (req_id,))
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (kak_amount, user_id))
        await bot.send_message(user_id, f"⚠️ **تم رفض طلب السحب.**\nتم إعادة رصيد وقدره `{kak_amount} KAK` لحسابك في البوت للاطمئنان والتأكد من بيانات محفظتك ثانية.")
    conn.commit()
    conn.close()
    await callback.message.edit_text(f"✅ تم معالجة الإجراء بنجاح للطلب #{req_id}.")

# تشغيل البوت وخادم الـ API معاً
async def main():
    # ستقوم مكتبة uvicorn بقراءة المتغيرات وتخصيص المنفذ تلقائياً لتوافق خادم Render
    import os
    port = int(os.environ.get("PORT", 8000))
    asyncio.create_task(uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port)).serve())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
