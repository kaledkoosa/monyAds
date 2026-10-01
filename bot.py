import os
import logging
import sqlite3
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn
import database as db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))  
SECRET_TOKEN = os.environ.get("SECRET_TOKEN", "MY_SUPER_SECRET_KEY_123")
WEB_APP_URL = os.environ.get("WEB_APP_URL", "https://onrender.com")
DB_NAME = "kak_mining.db"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI(title="TON Ads Platform Backend")

# تقييد الـ CORS لضمان عدم استغلال بروتوكول الـ API من نطاقات خارجية
app.add_middleware(
    CORSMiddleware, 
    allow_origins=[WEB_APP_URL, "https://html.tg"], 
    allow_credentials=True, 
    allow_methods=["*"], 
    allow_headers=["*"]
)

# فتح وإغلاق آمن لقواعد البيانات لمنع حدوث Database Is Locked
def execute_db_query(query, params=(), fetchall=False, commit=False):
    conn = sqlite3.connect(DB_NAME, timeout=10.0)
    cursor = conn.cursor()
    try:
        cursor.execute(query, params)
        if commit:
            conn.commit()
        if fetchall:
            return cursor.fetchall()
        return cursor.fetchone()
    except Exception as e:
        logger.error(f"Database error: {e}")
        raise e
    finally:
        conn.close()

class RewardRequest(BaseModel):
    user_id: int
    amount: float

class AppWithdrawRequest(BaseModel):
    user_id: int
    wallet: str

class CreateAdRequest(BaseModel):
    user_id: int
    platform: str
    url: str
    clicks: int
    cost: float

def verify_secret_auth(x_secret_token: str = Header(None)):
    if x_secret_token != SECRET_TOKEN:
        raise HTTPException(status_code=403, detail="غير مصرح به - مفتاح الحماية خاطئ")

@app.get("/api/get_balance")
async def get_user_balance(user_id: int):
    balance = db.get_user(user_id)
    if balance is None: 
        raise HTTPException(status_code=404, detail="المستخدم غير موجود")
    return {"status": "success", "balance": balance}

@app.post("/api/create_ad")
async def create_user_ad(data: CreateAdRequest, x_secret_token: str = Header(None)):
    verify_secret_auth(x_secret_token)
    balance = db.get_user(data.user_id)
    
    if data.clicks < 100:
        raise HTTPException(status_code=400, detail="الحد الأدنى للترويج هو 100 زيارة")
    
    calculated_cost = data.clicks * 0.003
    if balance is None or balance < calculated_cost:
        raise HTTPException(status_code=400, detail="رصيدك من عملة TON غير كافٍ لإطلاق الحملة.")
    
    db.update_balance(data.user_id, -calculated_cost)
    
    try:
        await bot.send_message(
            ADMIN_ID, 
            f"📢 **حملة إعلانية ممولة بـ TON قيد المراجعة:**\n\n👤 المعلن: `{data.user_id}`\n🌐 المنصة: {data.platform.upper()}\n🔗 الرابط:\n{data.url}\n🎯 العدد: {data.clicks} زيارة\n💰 التكلفة المخصومة: {calculated_cost:.3f} TON"
        )
    except Exception: 
        pass
    return {"status": "success"}

@app.post("/api/app_withdraw")
async def process_app_withdrawal(data: AppWithdrawRequest, x_secret_token: str = Header(None)):
    verify_secret_auth(x_secret_token)
    balance = db.get_user(data.user_id)
    
    if balance is None or balance < 0.20: 
        raise HTTPException(status_code=400, detail="الحد الأدنى للسحب هو 0.20 TON")
        
    db.create_withdrawal(data.user_id, data.wallet, balance, balance)
    
    try:
        await bot.send_message(
            data.user_id, 
            f"📥 **تم تسجيل طلب السحب الفوري بنجاح!**\n\n💰 المبلغ: `{balance:.3f} TON`\n👛 المحفظة: `{data.wallet}`\n⏱ جاري مراجعة طلبك وإرسال عملات TON لمحفظتك بـ Tonkeeper يدوياً."
        )
    except Exception: 
        pass
    return {"status": "success"}

@app.post("/api/reward")
async def give_reward(data: RewardRequest, x_secret_token: str = Header(None)):
    verify_secret_auth(x_secret_token)
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
    builder.row(types.InlineKeyboardButton(text="📢 افتح منصة TON الإعلانية", web_app=types.WebAppInfo(url=f"{WEB_APP_URL}?user={user_id}")))
    
    welcome = "مرحباً بك في **منصة TON الإعلانية**! 🚀\n\nاضغط على الزر أدناه لفتح لوحة التحكم وبدء الترويج، كسب TON الحقيقي، أو الإيداع والسحب الفوري لـ Tonkeeper."
    await message.reply(welcome, reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    requests = execute_db_query("SELECT id, user_id, ton_wallet, kak_amount FROM withdrawals WHERE status = 'PENDING'", fetchall=True)
    if not requests: 
        return await message.reply("📥 لا توجد طلبات سحب معلقة.")
    for req in requests:
        req_id, u_id, wallet, ton_amount = req
        text = f"🆔 **طلب رقم:** #{req_id}\n👤 **المستخدم:** `{u_id}`\n💰 **المبلغ المستحق:** {ton_amount:.3f} TON\n👛 **المحفظة:**\n`{wallet}`"
        builder = InlineKeyboardBuilder()
        builder.row(
            types.InlineKeyboardButton(text="✅ تم الدفع وتأكيد السحب", callback_data=f"confirm_{req_id}"), 
            types.InlineKeyboardButton(text="❌ إلغاء وإعادة الرصيد", callback_data=f"reject_{req_id}")
        )
        await message.reply(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(lambda c: c.data.startswith("confirm_") or c.data.startswith("reject_"))
async def handle_admin_action(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    action, req_id = callback.data.split("_")
    req_id = int(req_id)
    
    req_data = execute_db_query("SELECT user_id, kak_amount FROM withdrawals WHERE id = ?", (req_id,))
    if not req_data: return
    user_id, ton_amount = req_data
    
    if action == "confirm":
        execute_db_query("UPDATE withdrawals SET status = 'COMPLETED' WHERE id = ?", (req_id,), commit=True)
        try:
            await bot.send_message(user_id, f"🎉 **تم تأكيد سحبك يدويّاً!**\nتم إرسال `{ton_amount:.3f} TON` إلى محفظتك بـ Tonkeeper.")
        except Exception: pass
    elif action == "reject":
        conn = sqlite3.connect(DB_NAME, timeout=10.0)
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE withdrawals SET status = 'REJECTED' WHERE id = ?", (req_id,))
            cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (ton_amount, user_id))
            conn.commit()
        finally:
            conn.close()
        try:
            await bot.send_message(user_id, f"⚠️ **تم رفض السحب وإعادة الأرصدة.**\nتم إعادة رصيد `{ton_amount:.3f} TON` لحسابك.")
        except Exception: pass
        
    await callback.message.edit_text(f"✅ تم معالجة الطلب #{req_id}.")

# --- السطر المعدل والمصحح لربط الواجهة الأمامية بالجذر الرئيسي ومسح خطأ الـ Not Found ---
app.mount("/", StaticFiles(directory="web", html=True), name="web")

polling_task = None

@app.on_event("startup")
async def on_startup():
    global polling_task
    polling_task = asyncio.create_task(dp.start_polling(bot))

@app.on_event("shutdown")
async def on_shutdown():
    if polling_task:
        polling_task.cancel()
    await bot.session.close()

async def main():
    port = int(os.environ.get("PORT", 8000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
