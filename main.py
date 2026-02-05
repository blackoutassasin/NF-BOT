"""
Netflix Profile Sales Bot - Advanced Manual Verification
Features: Screenshot -> TrxID -> Last 4 Digits -> Admin Approval (Reason/Skip)
"""

import os
import sqlite3
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler
)

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID', '0'))
BKASH_NUMBER = os.getenv('BKASH_NUMBER', '01XXXXXXXXX')
NAGAD_NUMBER = os.getenv('NAGAD_NUMBER', '01XXXXXXXXX')
PRODUCT_PRICE = 50
DATABASE_PATH = os.getenv('DATABASE_PATH', 'netflix_bot.db')

# --- STATES ---
# User Flow States
WAITING_SCREENSHOT = 1
WAITING_TRX_ID = 2
WAITING_LAST_4 = 3

# Admin Flow States (For writing reject reason)
ADMIN_WAITING_REASON = 4
ADMIN_WAITING_BULK = 5

# --- DATABASE INIT ---
def init_database():
    if '/' in DATABASE_PATH:
        os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            password TEXT NOT NULL,
            profile_pin TEXT NOT NULL,
            profile_name TEXT DEFAULT 'Default',
            status TEXT DEFAULT 'unsold',
            sold_at TIMESTAMP,
            sold_to_user_id INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            trxid TEXT NOT NULL,
            amount INTEGER NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            profile_id INTEGER,
            FOREIGN KEY (profile_id) REFERENCES profiles(id)
        )
    ''')
    conn.commit()
    conn.close()

# --- USER BUYING FLOW ---

class NetflixBot:
    
    @staticmethod
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        keyboard = [[InlineKeyboardButton("🎬 Buy Netflix Profile (50 TK)", callback_data='buy_netflix')]]
        welcome_text = (
            f"👋 Hello {user.first_name}!\n\n"
            f"🎬 *Netflix Premium Profile*\n"
            f"💰 Price: *{PRODUCT_PRICE} BDT*\n"
            f"📅 Validity: 1 Month\n"
            f"🛡️ Protection: PIN Protected\n\n"
            f"Click 'Buy' to purchase 👇"
        )
        await update.message.reply_text(welcome_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    
    @staticmethod
    async def buy_netflix(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        # Check Stock
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM profiles WHERE status = 'unsold'")
        stock = cursor.fetchone()[0]
        conn.close()
        
        if stock == 0:
            await query.edit_message_text("❌ *Out of Stock!* Please come back later.", parse_mode='Markdown')
            return ConversationHandler.END
        
        msg = (
            f"💳 *Payment Instructions*\n\n"
            f"Send *{PRODUCT_PRICE} TK* to:\n"
            f"🚀 *bKash:* `{BKASH_NUMBER}` (Send Money)\n"
            f"🚀 *Nagad:* `{NAGAD_NUMBER}` (Send Money)\n\n"
            f"👉 *Step 1:* After payment, send the **Screenshot** here."
        )
        await query.edit_message_text(msg, parse_mode='Markdown')
        return WAITING_SCREENSHOT

    @staticmethod
    async def handle_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message.photo:
            await update.message.reply_text("❌ Please send a PHOTO (Screenshot).")
            return WAITING_SCREENSHOT
        
        # Save photo file_id to context
        context.user_data['payment_photo'] = update.message.photo[-1].file_id
        
        await update.message.reply_text(
            "✅ Screenshot Received.\n\n"
            "👉 *Step 2:* Please type the **Transaction ID** (TrxID) text now.\n"
            "(Example: 9G45H6J7K8)",
            parse_mode='Markdown'
        )
        return WAITING_TRX_ID

    @staticmethod
    async def receive_trx_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
        trx_id = update.message.text.strip().upper()
        context.user_data['payment_trx'] = trx_id
        
        await update.message.reply_text(
            "✅ TrxID Saved.\n\n"
            "👉 *Step 3:* Enter the **Last 4 Digits** of the number you sent money from.\n"
            "(Example: 4635)",
            parse_mode='Markdown'
        )
        return WAITING_LAST_4

    @staticmethod
    async def receive_last_4(update: Update, context: ContextTypes.DEFAULT_TYPE):
        last_4 = update.message.text.strip()
        context.user_data['payment_last4'] = last_4
        user = update.effective_user
        
        # Notify User
        await update.message.reply_text(
            "✅ *Order Submitted!* \n\n"
            "Admin is checking your details manually.\n"
            "Please wait...",
            parse_mode='Markdown'
        )

        # Prepare Admin Report
        photo_id = context.user_data['payment_photo']
        trx_id = context.user_data['payment_trx']
        
        caption = (
            f"🛒 *New Order Request*\n\n"
            f"👤 *User ID:* `{user.id}`\n"
            f"📛 *User Name:* @{user.username if user.username else 'None'}\n"
            f"📦 *Quantity:* 1 Profile\n\n"
            f"🆔 *User TrxID:* `{trx_id}`\n"
            f"📱 *User Paid Last 4:* `{last_4}`"
        )

        keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f'approve_{user.id}'),
                InlineKeyboardButton("❌ Reject", callback_data=f'pre_reject_{user.id}')
            ]
        ]

        # Send to Admin
        await context.bot.send_photo(
            chat_id=ADMIN_USER_ID,
            photo=photo_id,
            caption=caption,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        return ConversationHandler.END

    @staticmethod
    async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("❌ Operation Cancelled.")
        return ConversationHandler.END


# --- ADMIN LOGIC ---

class AdminActions:
    
    @staticmethod
    async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
        
        # --- APPROVE FLOW ---
        if data.startswith('approve_'):
            user_id = int(data.split('_')[1])
            
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT id, email, password, profile_pin, profile_name FROM profiles WHERE status = 'unsold' LIMIT 1")
            profile = cursor.fetchone()
            
            if not profile:
                conn.close()
                await query.answer("❌ Stock Empty! Add profiles first.", show_alert=True)
                return
            
            pid, email, pwd, pin, name = profile
            
            # Update DB
            cursor.execute("UPDATE profiles SET status = 'sold', sold_at = ?, sold_to_user_id = ? WHERE id = ?", 
                           (datetime.now(), user_id, pid))
            cursor.execute("INSERT INTO sales (user_id, trxid, amount, profile_id) VALUES (?, ?, ?, ?)", 
                           (user_id, "MANUAL", PRODUCT_PRICE, pid))
            conn.commit()
            conn.close()
            
            # Send to User
            msg = (
                f"✅ *Order Approved!*\n\n"
                f"📧 Email: `{email}`\n"
                f"🔑 Pass: `{pwd}`\n"
                f"👤 Profile: `{name}`\n"
                f"📌 PIN: `{pin}`\n\n"
                f"⚠️ Do NOT change info."
            )
            try:
                await context.bot.send_message(user_id, msg, parse_mode='Markdown')
                await query.edit_message_caption(caption=query.message.caption + "\n\n✅ *APPROVED & DELIVERED*")
            except Exception:
                await query.edit_message_caption(caption=query.message.caption + "\n\n⚠️ *Approved but User Blocked Bot*")

        # --- PRE-REJECT (SHOW OPTIONS) ---
        elif data.startswith('pre_reject_'):
            user_id = data.split('_')[2] # string
            keyboard = [
                [InlineKeyboardButton("📝 Write Reason", callback_data=f'reject_reason_{user_id}')],
                [InlineKeyboardButton("⏭ Skip (Default Msg)", callback_data=f'reject_skip_{user_id}')],
                [InlineKeyboardButton("🔙 Back", callback_data=f'back_to_main_{user_id}')] # Optional safety
            ]
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))

        # --- REJECT SKIP (DEFAULT) ---
        elif data.startswith('reject_skip_'):
            user_id = int(data.split('_')[2])
            try:
                await context.bot.send_message(user_id, "❌ *Payment Failed.*\nInformation did not match. Contact Admin.", parse_mode='Markdown')
                await query.edit_message_caption(caption=query.message.caption + "\n\n❌ *REJECTED (Skipped Reason)*")
            except:
                pass
            await query.edit_message_reply_markup(reply_markup=None) # Remove buttons

        # --- RESTORE BUTTONS (BACK) ---
        elif data.startswith('back_to_main_'):
            user_id = data.split('_')[3]
            keyboard = [
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f'approve_{user_id}'),
                    InlineKeyboardButton("❌ Reject", callback_data=f'pre_reject_{user_id}')
                ]
            ]
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))

    # --- REJECT REASON FLOW (Conversation) ---
    @staticmethod
    async def start_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = query.data.split('_')[2]
        context.user_data['reject_target_id'] = user_id
        context.user_data['admin_msg_id'] = query.message.message_id
        
        await query.message.reply_text(f"📝 *Write rejection reason for User {user_id}:*", parse_mode='Markdown')
        return ADMIN_WAITING_REASON

    @staticmethod
    async def send_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
        reason = update.message.text
        target_id = int(context.user_data.get('reject_target_id'))
        
        # Send to User
        try:
            await context.bot.send_message(
                target_id, 
                f"❌ *Payment Rejected*\n\nReason: {reason}\n\nContact Admin for help.", 
                parse_mode='Markdown'
            )
            await update.message.reply_text("✅ Reason sent to user.")
        except:
            await update.message.reply_text("⚠️ User blocked bot, could not send reason.")

        return ConversationHandler.END

    # --- ADMIN PANEL & BULK ADD ---
    @staticmethod
    async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_USER_ID: return
        keyb = [[InlineKeyboardButton("➕ Add Bulk Profiles", callback_data='adm_add')],
                [InlineKeyboardButton("📊 Stats", callback_data='adm_stats')]]
        await update.message.reply_text("🛠 Admin Panel:", reply_markup=InlineKeyboardMarkup(keyb))

    @staticmethod
    async def admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if query.data == 'adm_add':
            await query.edit_message_text("📤 Send profiles:\n`email:pass:pin:name`\n(One per line)")
            return ADMIN_WAITING_BULK
        elif query.data == 'adm_stats':
            conn = sqlite3.connect(DATABASE_PATH)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM profiles WHERE status='unsold'")
            stock = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM sales")
            sales = c.fetchone()[0]
            conn.close()
            await query.edit_message_text(f"📊 Stock: {stock}\n💰 Total Sales: {sales}")
            return ConversationHandler.END

    @staticmethod
    async def save_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        count = 0
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        for line in text.split('\n'):
            p = line.strip().split(':')
            if len(p) == 4:
                c.execute("INSERT INTO profiles (email,password,profile_pin,profile_name) VALUES (?,?,?,?)", p)
                count += 1
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ Added {count} profiles.")
        return ConversationHandler.END

def main():
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN missing")
        return
    
    init_database()
    app = Application.builder().token(BOT_TOKEN).build()

    # 1. User Buy Conversation
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(NetflixBot.buy_netflix, pattern='^buy_netflix$')],
        states={
            WAITING_SCREENSHOT: [MessageHandler(filters.PHOTO, NetflixBot.handle_screenshot)],
            WAITING_TRX_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, NetflixBot.receive_trx_id)],
            WAITING_LAST_4: [MessageHandler(filters.TEXT & ~filters.COMMAND, NetflixBot.receive_last_4)],
        },
        fallbacks=[CommandHandler('cancel', NetflixBot.cancel)]
    ))

    # 2. Admin Rejection Reason Conversation
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(AdminActions.start_reject_reason, pattern='^reject_reason_')],
        states={ADMIN_WAITING_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, AdminActions.send_reject_reason)]},
        fallbacks=[CommandHandler('cancel', NetflixBot.cancel)]
    ))

    # 3. Admin Bulk Add Conversation
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(AdminActions.admin_buttons, pattern='^adm_add$')],
        states={ADMIN_WAITING_BULK: [MessageHandler(filters.TEXT, AdminActions.save_bulk)]},
        fallbacks=[CommandHandler('cancel', NetflixBot.cancel)]
    ))

    # 4. General Handlers
    app.add_handler(CommandHandler('start', NetflixBot.start))
    app.add_handler(CommandHandler('admin', AdminActions.admin_panel))
    app.add_handler(CallbackQueryHandler(AdminActions.handle_callback, pattern='^(approve|pre_reject|reject_skip|back_to_main)_'))
    app.add_handler(CallbackQueryHandler(AdminActions.admin_buttons, pattern='^adm_stats$'))

    print("Bot Started...")
    app.run_polling()

if __name__ == '__main__':
    main()
