# handlers/coinex_withdraw.py
import logging
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, MessageHandler, ConversationHandler, ContextTypes, filters
import store, config
from coinex_adapter import CoinExClient

logger = logging.getLogger(__name__)

# Conversation states
AMOUNT, CHAIN, ADDRESS, CONFIRM = range(4)

# Configuration
ADMIN_IDS = getattr(config, "ADMIN_IDS", [])
MIN_WITHDRAW_NSP = getattr(config, "COINEX_MIN_WITHDRAW_NSP", 10000)
FEE_PERCENT = getattr(config, "COINEX_FEE_PERCENT", 0.0)  # optional extra platform fee

def _client():
    return CoinExClient(api_key=config.COINEX_API_KEY, api_secret=config.COINEX_API_SECRET)

# ========== USER FLOW ==========

async def start_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.effective_chat.send_message(
        f"💸 سحب عبر CoinEx\n"
        f"الحد الأدنى للسحب: {MIN_WITHDRAW_NSP} NSP\n"
        f"الرجاء إدخال المبلغ بالـ NSP:"
    )
    return AMOUNT


async def ask_chain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask user to choose withdrawal chain (BEP20 / TRC20)"""
    try:
        amount = int(update.message.text.strip().replace(",", ""))
    except:
        await update.message.reply_text("❌ الرجاء إدخال رقم صالح.")
        return AMOUNT

    if amount < MIN_WITHDRAW_NSP:
        await update.message.reply_text(f"⚠️ الحد الأدنى للسحب هو {MIN_WITHDRAW_NSP} NSP.")
        return AMOUNT

    user = store.getUserByTelegramId(str(update.effective_user.id))
    if not user:
        await update.message.reply_text("⚠️ حسابك غير مسجل.")
        return ConversationHandler.END

    balance = store.get_user_balance(user["id"])
    if amount > balance:
        await update.message.reply_text(f"🚫 لا يوجد رصيد كافٍ. رصيدك الحالي: {balance} NSP.")
        return ConversationHandler.END

    context.user_data["amount_nsp"] = amount
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 BEP20", callback_data="chain_bep20"),
         InlineKeyboardButton("🔵 TRC20", callback_data="chain_trc20")]
    ])
    await update.message.reply_text("🌐 اختر السلسلة المراد السحب عليها:", reply_markup=kb)
    return CHAIN


async def ask_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chain = "BEP20" if "bep20" in q.data else "TRC20"
    context.user_data["chain"] = chain
    await q.edit_message_text("📩 أدخل عنوان محفظة USDT المراد السحب إليها:")
    return ADDRESS


async def confirm_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User enters withdrawal address and confirms"""
    address = update.message.text.strip()
    context.user_data["address"] = address
    amount_nsp = context.user_data["amount_nsp"]

    # ✅ تحقق من أن العنوان موجود في الـ whitelist
    if not store.is_coinex_address_whitelisted(address):
        await update.message.reply_text(
            "⚠️ هذا العنوان غير مسجل في قائمة العناوين الموثوقة.\n"
            "يرجى التواصل مع الإدارة لإضافته قبل طلب السحب."
        )
        return ConversationHandler.END

    # تحويل NSP → USDT
    rate = store.get_usd_to_nsp_rate()
    if not rate or rate <= 0:
        await update.message.reply_text("⚠️ سعر التحويل غير متوفر حالياً. يرجى المحاولة لاحقاً.")
        return ConversationHandler.END

    usdt_amount = float("{:.6f}".format(amount_nsp / rate))
    chain = context.user_data["chain"]

    summary = (
        f"📋 **ملخص طلب السحب:**\n\n"
        f"💰 المبلغ (NSP): {amount_nsp}\n"
        f"💵 ما يعادله (USDT): {usdt_amount}\n"
        f"🔗 الشبكة: {chain}\n"
        f"🏦 العنوان: `{address}`\n\n"
        f"هل ترغب في إرسال الطلب للإدارة؟"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد", callback_data="withdraw_send")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="withdraw_cancel")]
    ])
    await update.message.reply_text(summary, reply_markup=kb, parse_mode="Markdown")
    return CONFIRM


async def submit_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User confirms and request is stored pending admin review"""
    q = update.callback_query
    await q.answer()

    if q.data == "withdraw_cancel":
        await q.edit_message_text("❎ تم إلغاء العملية.")
        return ConversationHandler.END

    user = store.getUserByTelegramId(str(q.from_user.id))
    amount_nsp = context.user_data["amount_nsp"]
    chain = context.user_data["chain"]
    address = context.user_data["address"]

    # خصم الرصيد (تجميد مؤقت حتى المراجعة)
    store.deduct_balance(user["id"], amount_nsp)

    # تحويل القيمة إلى USDT
    rate = store.get_usd_to_nsp_rate()
    usdt_amount = float("{:.6f}".format(amount_nsp / rate))

    # حفظ الطلب في قاعدة البيانات
    db = store.getDatabaseConnection()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO coinex_withdrawals (user_id, nsp_amount, usdt_amount, chain, address, status, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (user["id"], amount_nsp, usdt_amount, chain, address, "pending", datetime.now()))
    wid = cur.lastrowid
    cur.execute("""
        INSERT INTO transactions (user_id, provider_id, provider_type, value, action_type)
        VALUES (%s,%s,%s,%s,%s)
    """, (user["id"], wid, "coinex", amount_nsp, "withdraw"))
    db.commit()
    db.close()

    store.add_audit_log("coinex_withdrawals", wid, "pending", "User submitted withdrawal request")

    await q.edit_message_text("✅ تم تسجيل طلب السحب بنجاح، بانتظار موافقة الإدارة.")
    context.user_data.clear()

    # إشعار الأدمن
    msg = (
        f"🔔 **طلب سحب جديد عبر CoinEx**\n\n"
        f"👤 المستخدم: @{q.from_user.username or q.from_user.full_name}\n"
        f"💰 NSP: {amount_nsp} → USDT: {usdt_amount}\n"
        f"🔗 الشبكة: {chain}\n"
        f"🏦 العنوان: `{address}`\n"
        f"🆔 رقم العملية: {wid}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ موافقة وتنفيذ آلي", callback_data=f"admin_coinex_approve:{wid}")],
        [InlineKeyboardButton("❌ رفض", callback_data=f"admin_coinex_reject:{wid}")]
    ])
    for admin in ADMIN_IDS:
        try:
            await context.bot.send_message(admin, msg, reply_markup=kb, parse_mode="Markdown")
        except:
            pass

    return ConversationHandler.END

# ========== ADMIN FLOW ==========

async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin approves and triggers automatic CoinEx withdrawal"""
    q = update.callback_query
    await q.answer()
    if int(q.from_user.id) not in ADMIN_IDS:
        return await q.answer("❌ غير مصرح.")

    wid = int(q.data.split(":")[1])
    tx = store.get_transaction("coinex_withdrawals", wid)
    if not tx:
        return await q.answer("⚠️ العملية غير موجودة.")

    client = _client()
    res = client.withdraw(
        coin="USDT",
        to_address=tx["address"],
        amount=float(tx["usdt_amount"]),
        network=tx["chain"]
    )

    if res.get("code") == 0 and res.get("data"):
        txid = res["data"].get("id") or res["data"].get("withdraw_id") or res["data"]
        store.update_transaction_status("coinex_withdrawals", wid, "approved", txid=txid)
        store.add_audit_log("coinex_withdrawals", wid, "approved", f"Executed via API, txid={txid}")

        tg = store.get_user_telegram_by_id(tx["user_id"])
        if tg:
            await context.bot.send_message(tg, f"✅ تمت معالجة سحبك #{wid}.\n🆔 TxID: `{txid}`", parse_mode="Markdown")

        await q.edit_message_text(f"✅ تم تنفيذ السحب آليًا.\nTxID: `{txid}`", parse_mode="Markdown")
    else:
        store.update_transaction_status("coinex_withdrawals", wid, "error")
        store.add_audit_log("coinex_withdrawals", wid, "error", f"API error: {res}")
        await q.edit_message_text(f"❌ فشل تنفيذ السحب عبر CoinEx API.\nResponse: {res}")


async def admin_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if int(q.from_user.id) not in ADMIN_IDS:
        return await q.answer("❌ غير مصرح.")
    wid = int(q.data.split(":")[1])
    context.user_data["reject_wid"] = wid
    await q.edit_message_text("✏️ الرجاء إدخال سبب الرفض:")
    return "WAIT_REASON"


async def receive_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip()
    wid = context.user_data.pop("reject_wid", None)
    if not wid:
        return await update.message.reply_text("⚠️ لا يوجد طلب معلق.")
    store.update_transaction_status("coinex_withdrawals", wid, "rejected", reason=reason)
    store.add_audit_log("coinex_withdrawals", wid, "rejected", f"Admin rejected: {reason}")

    tx = store.get_transaction("coinex_withdrawals", wid)
    tg = store.get_user_telegram_by_id(tx["user_id"])
    if tg:
        await context.bot.send_message(tg, f"🚫 تم رفض عملية السحب #{wid}.\n📝 السبب: {reason}")
    await update.message.reply_text(f"✅ تم رفض الطلب #{wid}.")
    return ConversationHandler.END


# ========== REGISTER ==========

def register_handlers(dp):
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_withdraw, pattern="^coinex_withdraw$")],
        states={
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_chain)],
            CHAIN: [CallbackQueryHandler(ask_address, pattern="^chain_")],
            ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_request)],
            CONFIRM: [CallbackQueryHandler(submit_request, pattern="^withdraw_")],
            "WAIT_REASON": [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reject_reason)]
        },
        fallbacks=[]
    )
    dp.add_handler(conv)
    dp.add_handler(CallbackQueryHandler(admin_approve, pattern="^admin_coinex_approve"))
    dp.add_handler(CallbackQueryHandler(admin_reject, pattern="^admin_coinex_reject"))
