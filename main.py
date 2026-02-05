"""
Netflix Profile Sales Bot - Broadcast Fixed Version
Features:
- Fixed Broadcast (Safe Mode - No Formatting Errors)
- New User Notification to Admin
- Auto Admin Detection
- Admin Panel & Manual Verification
"""

import os
import sqlite3
import logging
import asyncio
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
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
WAITING_SCREENSHOT = 1
WAITING_TRX_ID = 2
WAITING_LAST_4 = 3
ADMIN_WAITING_REASON = 4
ADMIN_WAITING_BULK = 5
WAITING_SUPPORT_MESSAGE = 6
ADMIN_WAITING_BROADCAST = 7

# --- DATABASE INIT ---
def init_database():
    if '/' in DATABASE_PATH:
        os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # Profiles table
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
    
    # Sales table
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

    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

# --- USER FLOWS ---

class NetflixBot:
    
    @staticmethod
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
        # --- NEW USER CHECK LOGIC ---
        try:
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            
            # Check if user already exists
            cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user.id,))
            existing_user = cursor.fetchone()
            
            if not existing_user:
                # Insert New User
                cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user.id,))
                conn.commit()
                
                # Notify Admin
                join_date = datetime.now().strftime("%Y-%m-%d %I:%M %p")
                notification_msg = (
                    f"🔔 *New User Joined*\n"
                    f"────────────────\n"
                    f"📛 *User Name:* {user.full_name}\n"
                    f"🆔 *User ID:* `{user.id}`\n"
                    f"🔗 *Username:* @{user.username if user.username else 'None'}\n"
                    f"📅 *Join Date:* {join_date}"
                )
                try:
                    await context.bot.send_message(
                        chat_id=ADMIN_USER_ID, 
                        text=notification_msg, 
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as e:
                    logger.error(f"Failed to notify admin: {e}")

            conn.close()
        except Exception as e:
            logger.error(f"User DB Error: {e}")
        # ----------------------------

        # Check if user is Admin
        is_admin = (user.id == ADMIN_USER_ID)

        welcome_text = (
            f"👋 *Welcome, {user.first_name}!*\n"
            f"──────────────────\n"
            f"🤖 *Automated Digital Shop*\n\n"
            f"Choose an option below to get started:\n"
            f"──────────────────"
        )
        
        # Standard Buttons
        keyboard = [
            [InlineKeyboardButton("🛒 Order Netflix (50 TK)", callback_data='buy_netflix')],
            [InlineKeyboardButton("📝 Request Product", callback_data='contact_support_req')],
            [InlineKeyboardButton("📞 Contact Owner", callback_data='contact_owner_info')]
        ]

        # Add Admin Button ONLY if user is Admin
        if is_admin:
            keyboard.append([InlineKeyboardButton("🔐 Admin Panel", callback_data='open_admin_panel')])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text=welcome_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        else:
            await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

    # --- OWNER INFO ---
    @staticmethod
    async def contact_owner_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        info_text = (
            "👨‍💻 *Owner Contact*\n"
            "────────────────\n"
            "📞 Phone: +8801784346353\n"
            "✈️ Telegram: @xenlize\n"
            "────────────────\n"
            "Click below to send a message directly via Bot."
        )
        keyboard = [
            [InlineKeyboardButton("💬 Contact Admin For Netflix", callback_data='contact_support_help')],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data='back_to_start')]
        ]
        await query.edit_message_text(info_text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))

    @staticmethod
    async def back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await NetflixBot.start(update, context)

    # --- SUPPORT MESSAGE FLOW ---
    @staticmethod
    async def start_support_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        msg = "📬 *Contact Admin / Request Product*\n────────────────\nSend your message (Text or Photo) now."
        await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN)
        return WAITING_SUPPORT_MESSAGE

    @staticmethod
    async def handle_support_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        admin_header = f"🔔 *Message from* {user.full_name} (`{user.id}`)\n@{user.username}"

        if update.message.photo:
            photo_id = update.message.photo[-1].file_id
            caption = f"{admin_header}\n\n{update.message.caption or ''}"
            try: await context.bot.send_photo(ADMIN_USER_ID, photo=photo_id, caption=caption, parse_mode=ParseMode.MARKDOWN)
            except: pass
        elif update.message.text:
            text = f"{admin_header}\n\n{update.message.text}"
            try: await context.bot.send_message(ADMIN_USER_ID, text=text, parse_mode=ParseMode.MARKDOWN)
            except: pass
        else:
            await update.message.reply_text("❌ Text or Photo only.")
            return WAITING_SUPPORT_MESSAGE
        
        await update.message.reply_text("✅ Message Sent! Admin will contact you.")
        await NetflixBot.start(update, context)
        return ConversationHandler.END

    # --- BUY FLOW ---
    @staticmethod
    async def buy_netflix(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM profiles WHERE status = 'unsold'")
        stock = cursor.fetchone()[0]
        conn.close()
        
        if stock == 0:
            keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data='back_to_start')]]
            await query.edit_message_text("🚫 *Out of Stock*", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
            return ConversationHandler.END
        
        msg = (
            f"💳 *Payment Gateway*\n──────────────────\n"
            f"💸 Amount: `{PRODUCT_PRICE} TK`\n"
            f"🚀 bKash: `{BKASH_NUMBER}`\n"
            f"🚀 Nagad: `{NAGAD_NUMBER}`\n\n"
            f"📸 *Step 1/3:* Upload Payment Screenshot."
        )
        keyboard = [[InlineKeyboardButton("🔙 Cancel", callback_data='cancel_flow')]]
        await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        return WAITING_SCREENSHOT

    @staticmethod
    async def handle_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message.photo:
            await update.message.reply_text("⚠️ Send a Screenshot Photo.")
            return WAITING_SCREENSHOT
        context.user_data['payment_photo'] = update.message.photo[-1].file_id
        await update.message.reply_text("🆔 *Step 2/3:* Enter Transaction ID (TrxID).", parse_mode=ParseMode.MARKDOWN)
        return WAITING_TRX_ID

    @staticmethod
    async def receive_trx_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['payment_trx'] = update.message.text.strip().upper()
        await update.message.reply_text("📱 *Step 3/3:* Enter Last 4 Digits.", parse_mode=ParseMode.MARKDOWN)
        return WAITING_LAST_4

    @staticmethod
    async def receive_last_4(update: Update, context: ContextTypes.DEFAULT_TYPE):
        last_4 = update.message.text.strip()
        user = update.effective_user
        
        await update.message.reply_text("⏳ *Verifying...* Wait for Admin approval.", parse_mode=ParseMode.MARKDOWN)

        caption = (
            f"🧾 *New Order*\n"
            f"👤: {user.full_name} (`{user.id}`)\n"
            f"💰: {PRODUCT_PRICE} TK\n"
            f"🆔 TrxID: `{context.user_data['payment_trx']}`\n"
            f"📱 Last 4: `{last_4}`"
        )
        keyboard = [[InlineKeyboardButton("✅ Approve", callback_data=f'approve_{user.id}'), InlineKeyboardButton("❌ Reject", callback_data=f'pre_reject_{user.id}')]]
        await context.bot.send_photo(ADMIN_USER_ID, context.user_data['payment_photo'], caption=caption, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        return ConversationHandler.END

    @staticmethod
    async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("❌ Cancelled.")
        await NetflixBot.start(update, context)
        return ConversationHandler.END

# --- ADMIN LOGIC ---

class AdminActions:
    @staticmethod
    async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
        
        if data == 'cancel_flow':
            await query.edit_message_text("❌ Cancelled.")
            return ConversationHandler.END

        # APPROVE
        if data.startswith('approve_'):
            user_id = int(data.split('_')[1])
            conn = sqlite3.connect(DATABASE_PATH)
            c = conn.cursor()
            c.execute("SELECT id,email,password,profile_pin,profile_name FROM profiles WHERE status='unsold' LIMIT 1")
            row = c.fetchone()
            
            if not row:
                conn.close()
                await query.answer("⚠️ Stock Empty!", show_alert=True)
                return
            
            pid, em, pw, pin, nm = row
            c.execute("UPDATE profiles SET status='sold', sold_at=?, sold_to_user_id=? WHERE id=?", (datetime.now(), user_id, pid))
            c.execute("INSERT INTO sales (user_id,trxid,amount,profile_id) VALUES (?,?,?,?)", (user_id, "MANUAL", PRODUCT_PRICE, pid))
            conn.commit()
            conn.close()
            
            msg = f"🎉 *Order Completed!*\n\n📧 `{em}`\n🔑 `{pw}`\n👤 `{nm}`\n📌 `{pin}`"
            try:
                await context.bot.send_message(user_id, msg, parse_mode=ParseMode.MARKDOWN)
                await query.edit_message_caption(query.message.caption + "\n\n✅ *DELIVERED*")
            except: pass

        # REJECT FLOWS
        elif data.startswith('pre_reject_'):
            user_id = data.split('_')[2]
            keyb = [
                [InlineKeyboardButton("📝 Reason", callback_data=f'reject_reason_{user_id}')],
                [InlineKeyboardButton("🚫 Quick Reject", callback_data=f'reject_skip_{user_id}')],
                [InlineKeyboardButton("🔙 Back", callback_data=f'back_to_main_{user_id}')]
            ]
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyb))

        elif data.startswith('reject_skip_'):
            user_id = int(data.split('_')[2])
            kb = [[InlineKeyboardButton("💬 Contact Admin For Netflix", callback_data='contact_support_help')]]
            try:
                await context.bot.send_message(user_id, "❌ *Payment Rejected*\nContact admin for help.", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))
                await query.edit_message_caption(query.message.caption + "\n\n❌ *REJECTED*")
            except: pass
            await query.edit_message_reply_markup(reply_markup=None)

        elif data.startswith('back_to_main_'):
            user_id = data.split('_')[3]
            keyb = [[InlineKeyboardButton("✅ Approve", callback_data=f'approve_{user.id}'), InlineKeyboardButton("❌ Reject", callback_data=f'pre_reject_{user.id}')]]
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyb))

    # REJECT REASON
    @staticmethod
    async def start_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        context.user_data['reject_target_id'] = query.data.split('_')[2]
        await query.message.reply_text("📝 *Write Reason:*", parse_mode=ParseMode.MARKDOWN)
        return ADMIN_WAITING_REASON

    @staticmethod
    async def send_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
        reason = update.message.text
        target_id = int(context.user_data.get('reject_target_id'))
        kb = [[InlineKeyboardButton("💬 Contact Admin For Netflix", callback_data='contact_support_help')]]
        try:
            await context.bot.send_message(target_id, f"❌ *Payment Rejected*\nReason: {reason}", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))
            await update.message.reply_text("✅ Reason sent.")
        except: pass
        return ConversationHandler.END

    # ADMIN PANEL
    @staticmethod
    async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_USER_ID: return
        
        kb = [
            [InlineKeyboardButton("➕ Add Bulk", callback_data='adm_add'), InlineKeyboardButton("📊 Stats", callback_data='adm_stats')],
            [InlineKeyboardButton("📢 Broadcast Message", callback_data='adm_broadcast')],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data='back_to_start')]
        ]
        text = "🛠 *Admin Panel*\nSelect an action:"

        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))

    @staticmethod
    async def admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if query.data == 'adm_add':
            await query.edit_message_text("📤 `email:pass:pin:name`", parse_mode=ParseMode.MARKDOWN)
            return ADMIN_WAITING_BULK
        elif query.data == 'adm_broadcast':
            await query.edit_message_text("📢 *Broadcast Mode*\n\nSend the message (Text or Photo) you want to broadcast to all users.", parse_mode=ParseMode.MARKDOWN)
            return ADMIN_WAITING_BROADCAST
        elif query.data == 'adm_stats':
            conn = sqlite3.connect(DATABASE_PATH)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM profiles WHERE status='unsold'")
            stock = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM sales")
            sales = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM users")
            users = c.fetchone()[0] or 0
            conn.close()
            
            kb = [[InlineKeyboardButton("🔙 Back to Panel", callback_data='open_admin_panel')]]
            await query.edit_message_text(f"📊 Stock: {stock} | Sales: {sales} | Users: {users}", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))
            return ConversationHandler.END

    # BULK SAVE
    @staticmethod
    async def save_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        count = 0
        for line in text.split('\n'):
            p = line.strip().split(':')
            if len(p) == 4:
                c.execute("INSERT INTO profiles (email,password,profile_pin,profile_name) VALUES (?,?,?,?)", p)
                count += 1
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ Added {count} profiles.")
        return ConversationHandler.END

    # BROADCAST SENDER (FIXED & SAFE MODE)
    @staticmethod
    async def send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_USER_ID: return ConversationHandler.END
        
        await update.message.reply_text("⏳ *Broadcasting started...*", parse_mode=ParseMode.MARKDOWN)
        
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        users = cursor.fetchall()
        conn.close()
        
        success = 0
        blocked = 0
        
        for user_row in users:
            user_id = user_row[0]
            try:
                # REMOVED ParseMode.MARKDOWN for safety - sends plain text/photo
                if update.message.photo:
                    await context.bot.send_photo(chat_id=user_id, photo=update.message.photo[-1].file_id, caption=update.message.caption)
                else:
                    await context.bot.send_message(chat_id=user_id, text=update.message.text)
                success += 1
            except Exception as e:
                logger.error(f"Broadcast Error for {user_id}: {e}")
                blocked += 1
            await asyncio.sleep(0.05)
            
        await update.message.reply_text(f"📢 *Broadcast Completed*\n\n✅ Sent: {success}\n❌ Blocked: {blocked}", parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

def main():
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN missing")
        return
    
    init_database()
    app = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler('start', NetflixBot.start))
    app.add_handler(CommandHandler('admin', AdminActions.admin_panel))
    
    # 1. Buy Flow
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(NetflixBot.buy_netflix, pattern='^buy_netflix$')],
        states={
            WAITING_SCREENSHOT: [MessageHandler(filters.PHOTO, NetflixBot.handle_screenshot)],
            WAITING_TRX_ID: [MessageHandler(filters.TEXT, NetflixBot.receive_trx_id)],
            WAITING_LAST_4: [MessageHandler(filters.TEXT, NetflixBot.receive_last_4)],
        },
        fallbacks=[CommandHandler('cancel', NetflixBot.cancel), CallbackQueryHandler(AdminActions.handle_callback, pattern='^cancel_flow$')]
    ))

    # 2. Support Flow
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(NetflixBot.start_support_flow, pattern='^contact_support_')],
        states={WAITING_SUPPORT_MESSAGE: [MessageHandler(filters.TEXT | filters.PHOTO | filters.CAPTION, NetflixBot.handle_support_message)]},
        fallbacks=[CommandHandler('cancel', NetflixBot.cancel)]
    ))
    
    # 3. Admin Reason
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(AdminActions.start_reject_reason, pattern='^reject_reason_')],
        states={ADMIN_WAITING_REASON: [MessageHandler(filters.TEXT, AdminActions.send_reject_reason)]},
        fallbacks=[CommandHandler('cancel', NetflixBot.cancel)]
    ))

    # 4. Admin Bulk
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(AdminActions.admin_buttons, pattern='^adm_add$')],
        states={ADMIN_WAITING_BULK: [MessageHandler(filters.TEXT, AdminActions.save_bulk)]},
        fallbacks=[CommandHandler('cancel', NetflixBot.cancel)]
    ))

    # 5. Broadcast
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(AdminActions.admin_buttons, pattern='^adm_broadcast$')],
        states={ADMIN_WAITING_BROADCAST: [MessageHandler(filters.TEXT | filters.PHOTO | filters.CAPTION, AdminActions.send_broadcast)]},
        fallbacks=[CommandHandler('cancel', NetflixBot.cancel)]
    ))

    # Callbacks
    app.add_handler(CallbackQueryHandler(NetflixBot.contact_owner_info, pattern='^contact_owner_info$'))
    app.add_handler(CallbackQueryHandler(NetflixBot.back_to_start, pattern='^back_to_start$'))
    app.add_handler(CallbackQueryHandler(AdminActions.admin_panel, pattern='^open_admin_panel$'))
    app.add_handler(CallbackQueryHandler(AdminActions.handle_callback, pattern='^(approve|pre_reject|reject_skip|back_to_main)_'))
    app.add_handler(CallbackQueryHandler(AdminActions.admin_buttons, pattern='^adm_stats$'))

    print("Bot Started...")
    app.run_polling()

if __name__ == '__main__':
    main()
