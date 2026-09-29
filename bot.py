import os
import logging
import sqlite3
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn
import database as db

# تفعيل تسجيل الأخطاء والمراقبة
logging.basicConfig(level=logging.INFO)

# =================== سحب الإعدادات من المتغيرات البيئية ===================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))  
SECRET_TOKEN = os.environ.get("SECRET_TOKEN", "MY_SUPER_SECRET_KEY_123")
WEB_APP_URL = os.environ.get("WEB_APP_URL", "https://onrender.com")

# التحقق من وجود التوكن لمنع تشغيل السيرفر بأخطاء
if not BOT_TOKEN:
    raise ValueError("⚠️ خطأ أمني: لم يتم العثور على متغير البيئة BOT_TOKEN في سيرفر ريندر!")
# =========================================================================

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

class AppWithdrawRequest(BaseModel):
    user_id: int
    wallet: str
    secret_key: str

# 1. رابط جلب الرصيد الفعلي لعرضه بداخل التطبيق المصغر
@app.get("/api/get_balance")
async def get_user_balance(user_id: int):
    balance = db.get_user(user_id)
    if balance is None:
        raise HTTPException(status_code=404, detail="المستخدم غير موجود")
    return {"status": "success", "balance": balance}

# 2. رابط استقبال طلبات السحب من داخل التطبيق المصغر مباشرة
@app.post("/api/app_withdraw")
async def process_app_withdrawal(data: AppWithdrawRequest):
    if data.secret_key != SECRET_TOKEN:
        raise HTTPException(status_code=403, detail="غير مصرح به")
    
    balance = db.get_user(data.user_id)
    if balance is None:
        raise HTTPException(status_code=404, detail="المستخدم غير موجود")
    
    if balance < 20000:
        raise HTTPException(status_code=400, detail="رصيدك أقل من الحد الأدنى للسحب وهو 20,000 KAK")
        
    # تسجيل طلب السحب وخصم الرصيد يدوياً
    db.create_withdrawal(data.user_id, data.wallet, balance, balance / 20000)
    
    # إرسال إشعار فوري للمستخدم في الشات الخارجي لتأكيد استلام الطلب من التطبيق
    try:
        await bot.send_message(data.user_id, f"📥 **تم استلام طلب السحب من التطبيق المصغر بنجاح!**\n\n💰 المبلغ: `{balance} KAK`\n👛 المحفظة: `{data.wallet}`\n⏱ جاري المراجعة من قِبل الإدارة والدفع لـ Tonkeeper.")
    except Exception:
        pass
        
    return {"status": "success"}

# 3. رابط استقبال طلبات تحديث الرصيد عند إكمال المهام أو مشاهدة الإعلانات
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
    
    welcome = "مرحباً بك في بوت عملة **KAK** الرسمية! 🚀\n\nاضغط على الزر أدناه لفتح لوحة التعدين والمهام والمحفظة بشكل مدمج واحترافي تماماً بداخل تليجرام."
    await message.reply(welcome, reply_markup=builder.as_markup(), parse_mode="Markdown")

# --- لوحة التحكم للأدمن (الإدارة اليدوية لطلبات السحب) ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    conn = sqlite3.connect("kak_mining.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_id, ton_wallet, kak_amount, usdt_value FROM withdrawals WHERE status = 'PENDING'")
    requests = cursor.fetchall()
    conn.close()
    
    if not requests: return await message.reply("📥 لا توجد طلبات سحب معلقة حالياً.")
    
    for req in requests:
        req_id, u_id, wallet, kak, usdt = req
        text = f"🆔 **طلب رقم:** #{req_id}\n👤 **المستخدم:** `{u_id}`\n🪙 **المبلغ:** {kak} KAK\n💵 **القيمة:** {usdt:.2f} \\$\n👛 **المحفظة:**\n`{wallet}`"
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
        await bot.send_message(user_id, f"🎉 **تمت معالجة سحبك بنجاح!**\nوصلت الأرباح المقابلة لـ `{kak_amount} KAK` لمحفظتك بـ Tonkeeper.")
    elif action == "reject":
        cursor.execute("UPDATE withdrawals SET status = 'REJECTED' WHERE id = ?", (req_id,))
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (kak_amount, user_id))
        await bot.send_message(user_id, f"⚠️ **تم رفض طلب السحب.**\nتم إعادة رصيد وقدره `{kak_amount} KAK` لحسابك بالتطبيق.")
    conn.commit()
    conn.close()
    await callback.message.edit_text(f"✅ تم معالجة الإجراء بنجاح للطلب #{req_id}.")

# --- إقلاع خادم uvicorn الرئيسي وتشغيل البوت بالخلفية تلقائياً على Render ---
@app.on_event("startup")
async def on_startup():
    logging.info("🚀 جاري تشغيل استقصاء البوت (Polling) في الخلفية...")
    asyncio.create_task(dp.start_polling(bot))

async def main():
    import os
    port = int(os.environ.get("PORT", 8000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
