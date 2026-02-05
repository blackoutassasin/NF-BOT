"""
Netflix Profile Telegram Sales Bot
A professional bot for selling Netflix profiles with OCR payment verification
"""

import os
import re
import sqlite3
import logging
from datetime import datetime
from io import BytesIO
from typing import Optional, Tuple

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

try:
    from PIL import Image
    import pytesseract
except ImportError:
    print("PIL and pytesseract required. Install via requirements.txt")
    exit(1)

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
# Configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID', '0'))
BKASH_NUMBER = os.getenv('BKASH_NUMBER')
NAGAD_NUMBER = os.getenv('NAGAD_NUMBER')
PRODUCT_PRICE = 50
DATABASE_PATH = 'netflix_bot.db'

# Conversation states
WAITING_PAYMENT_SCREENSHOT = 1
WAITING_BULK_PROFILES = 2

# Database initialization
def init_database():
    """Initialize SQLite database with required tables"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # Profiles table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            password TEXT NOT NULL,
            profile_pin TEXT NOT NULL,
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
    
    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")


class NetflixBot:
    """Main bot class handling all operations"""
    
    @staticmethod
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command - Show welcome message and buy button"""
        user = update.effective_user
        
        keyboard = [
            [InlineKeyboardButton("🎬 Buy Netflix Profile (50 TK)", callback_data='buy_netflix')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_message = (
            f"👋 Welcome {user.first_name}!\n\n"
            f"🎬 *Netflix Profile Sales Bot*\n\n"
            f"📦 *Product:* Netflix Profile (1 Month Access)\n"
            f"💰 *Price:* {PRODUCT_PRICE} BDT\n"
            f"💳 *Payment:* bKash/Nagad\n\n"
            f"✅ *How it works:*\n"
            f"1️⃣ Click the button below\n"
            f"2️⃣ Send money via bKash/Nagad\n"
            f"3️⃣ Upload payment screenshot\n"
            f"4️⃣ Get your Netflix profile instantly!\n\n"
            f"🔒 100% Automated & Secure"
        )
        
        await update.message.reply_text(
            welcome_message,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    
    @staticmethod
    async def buy_netflix(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle buy button click - Show payment instructions"""
        query = update.callback_query
        await query.answer()
        
        # Check if profiles are available
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM profiles WHERE status = 'unsold'")
        available_count = cursor.fetchone()[0]
        conn.close()
        
        if available_count == 0:
            await query.edit_message_text(
                "❌ *Sorry! No profiles available right now.*\n\n"
                "Please contact the admin or try again later.",
                parse_mode='Markdown'
            )
            return ConversationHandler.END
        
        payment_message = (
            f"💳 *Payment Instructions*\n\n"
            f"💰 Amount: *{PRODUCT_PRICE} BDT*\n\n"
            f"📱 *bKash Number:* `{BKASH_NUMBER}`\n"
            f"📱 *Nagad Number:* `{NAGAD_NUMBER}`\n\n"
            f"⚠️ *Important:*\n"
            f"• Send exactly {PRODUCT_PRICE} TK\n"
            f"• Use Send Money (NOT Cash Out)\n"
            f"• Take a clear screenshot of the transaction\n"
            f"• Screenshot must show Transaction ID and Amount\n\n"
            f"📸 *Next Step:*\n"
            f"Send your payment screenshot now ⬇️"
        )
        
        await query.edit_message_text(payment_message, parse_mode='Markdown')
        return WAITING_PAYMENT_SCREENSHOT
    
    @staticmethod
    def extract_transaction_info(image: Image.Image) -> Tuple[Optional[str], Optional[int]]:
        """
        Extract Transaction ID and Amount from payment screenshot using OCR
        
        Returns:
            Tuple of (transaction_id, amount) or (None, None) if extraction fails
        """
        try:
            # Preprocess image for better OCR
            # Convert to grayscale
            image = image.convert('L')
            
            # Apply OCR
            text = pytesseract.image_to_string(image)
            logger.info(f"OCR extracted text: {text}")
            
            # Extract Transaction ID (10 alphanumeric characters)
            # Common patterns: TrxID, Transaction ID, TXN ID, etc.
            trx_patterns = [
                r'(?:TrxID|Transaction ID|TXN ID|TXNID|TRX)\s*:?\s*([A-Z0-9]{10})',
                r'\b([A-Z0-9]{10})\b',  # Fallback: any 10-char alphanumeric
            ]
            
            transaction_id = None
            for pattern in trx_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    transaction_id = match.group(1).upper()
                    # Validate it looks like a transaction ID (has both letters and numbers)
                    if re.search(r'[A-Z]', transaction_id) and re.search(r'[0-9]', transaction_id):
                        break
            
            # Extract Amount (looking for 50 or 50.00)
            amount_patterns = [
                r'(?:Amount|Total|Tk|BDT|৳)\s*:?\s*(\d+(?:\.\d{2})?)',
                r'(\d+(?:\.\d{2})?)\s*(?:Tk|BDT|৳|Taka)',
                r'\b(50(?:\.00)?)\b',  # Direct match for 50
            ]
            
            amount = None
            for pattern in amount_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    try:
                        amount = int(float(match.group(1)))
                        if amount == PRODUCT_PRICE:
                            break
                    except ValueError:
                        continue
            
            return transaction_id, amount
            
        except Exception as e:
            logger.error(f"OCR extraction error: {e}")
            return None, None
    
    @staticmethod
    async def handle_payment_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Process uploaded payment screenshot"""
        user = update.effective_user
        
        # Check if message has photo
        if not update.message.photo:
            await update.message.reply_text(
                "❌ Please send a screenshot image (photo), not a file."
            )
            return WAITING_PAYMENT_SCREENSHOT
        
        await update.message.reply_text("🔍 Processing your screenshot... Please wait.")
        
        try:
            # Download photo
            photo_file = await update.message.photo[-1].get_file()
            photo_bytes = await photo_file.download_as_bytearray()
            image = Image.open(BytesIO(photo_bytes))
            
            # Extract transaction info
            trx_id, amount = NetflixBot.extract_transaction_info(image)
            
            # Validate extracted data
            if not trx_id:
                await update.message.reply_text(
                    "❌ *Verification Failed*\n\n"
                    "Could not find Transaction ID in your screenshot.\n\n"
                    "Please ensure:\n"
                    "✓ Screenshot is clear and readable\n"
                    "✓ Transaction ID is visible\n"
                    "✓ Image is not cropped too much\n\n"
                    "Please send a new screenshot.",
                    parse_mode='Markdown'
                )
                return WAITING_PAYMENT_SCREENSHOT
            
            if amount != PRODUCT_PRICE:
                await update.message.reply_text(
                    f"❌ *Verification Failed*\n\n"
                    f"Amount mismatch!\n"
                    f"Expected: {PRODUCT_PRICE} BDT\n"
                    f"Found: {amount if amount else 'Not detected'} BDT\n\n"
                    f"Please send exactly {PRODUCT_PRICE} TK and upload a new screenshot.",
                    parse_mode='Markdown'
                )
                return WAITING_PAYMENT_SCREENSHOT
            
            # Check for duplicate transaction
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM sales WHERE trxid = ?", (trx_id,))
            
            if cursor.fetchone():
                conn.close()
                await update.message.reply_text(
                    "❌ *Duplicate Transaction*\n\n"
                    "This transaction has already been used.\n"
                    "Please make a new payment or contact admin.",
                    parse_mode='Markdown'
                )
                return ConversationHandler.END
            
            # Get an unsold profile
            cursor.execute(
                "SELECT id, email, password, profile_pin FROM profiles WHERE status = 'unsold' LIMIT 1"
            )
            profile = cursor.fetchone()
            
            if not profile:
                conn.close()
                await update.message.reply_text(
                    "❌ *Out of Stock*\n\n"
                    "Sorry, no profiles available right now.\n"
                    "Please contact admin for a refund.",
                    parse_mode='Markdown'
                )
                return ConversationHandler.END
            
            profile_id, email, password, pin = profile
            
            # Record the sale
            cursor.execute(
                """INSERT INTO sales (user_id, username, trxid, amount, profile_id) 
                   VALUES (?, ?, ?, ?, ?)""",
                (user.id, user.username, trx_id, amount, profile_id)
            )
            
            # Mark profile as sold
            cursor.execute(
                """UPDATE profiles 
                   SET status = 'sold', sold_at = ?, sold_to_user_id = ? 
                   WHERE id = ?""",
                (datetime.now(), user.id, profile_id)
            )
            
            conn.commit()
            conn.close()
            
            # Send profile details
            success_message = (
                "✅ *Payment Verified Successfully!*\n\n"
                "🎬 *Your Netflix Profile:*\n\n"
                f"📧 *Email:* `{email}`\n"
                f"🔑 *Password:* `{password}`\n"
                f"📍 *Profile PIN:* `{pin}`\n\n"
                f"⏱ *Valid for:* 1 Month\n"
                f"💳 *Transaction ID:* `{trx_id}`\n\n"
                "⚠️ *Important Notes:*\n"
                "• Do NOT change the password\n"
                "• Use only your assigned profile\n"
                "• Save these credentials securely\n\n"
                "✨ Enjoy your Netflix! 🍿"
            )
            
            await update.message.reply_text(success_message, parse_mode='Markdown')
            
            logger.info(f"Sale completed: User {user.id}, TrxID {trx_id}, Profile {profile_id}")
            return ConversationHandler.END
            
        except Exception as e:
            logger.error(f"Error processing screenshot: {e}")
            await update.message.reply_text(
                "❌ *Processing Error*\n\n"
                "An error occurred while processing your screenshot.\n"
                "Please try again or contact admin.",
                parse_mode='Markdown'
            )
            return ConversationHandler.END
    
    @staticmethod
    async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel the current operation"""
        await update.message.reply_text(
            "❌ Operation cancelled.\n\nUse /start to begin again."
        )
        return ConversationHandler.END


class AdminPanel:
    """Admin commands for bot management"""
    
    @staticmethod
    def is_admin(user_id: int) -> bool:
        """Check if user is admin"""
        return user_id == ADMIN_USER_ID
    
    @staticmethod
    async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show admin panel"""
        if not AdminPanel.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Unauthorized access.")
            return
        
        keyboard = [
            [InlineKeyboardButton("➕ Add Profiles", callback_data='admin_add_profiles')],
            [InlineKeyboardButton("📊 View Stats", callback_data='admin_stats')],
            [InlineKeyboardButton("📦 Check Stock", callback_data='admin_stock')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🔐 *Admin Panel*\n\nSelect an option:",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    
    @staticmethod
    async def admin_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle admin panel button clicks"""
        query = update.callback_query
        await query.answer()
        
        if not AdminPanel.is_admin(query.from_user.id):
            await query.edit_message_text("❌ Unauthorized access.")
            return
        
        if query.data == 'admin_add_profiles':
            await query.edit_message_text(
                "➕ *Add Profiles in Bulk*\n\n"
                "Send profiles in this format (one per line):\n"
                "`email:password:pin`\n\n"
                "*Example:*\n"
                "`user1@gmail.com:pass123:1234`\n"
                "`user2@gmail.com:pass456:5678`\n\n"
                "Send /cancel to abort.",
                parse_mode='Markdown'
            )
            return WAITING_BULK_PROFILES
        
        elif query.data == 'admin_stats':
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*), SUM(amount) FROM sales")
            total_sales, total_revenue = cursor.fetchone()
            total_revenue = total_revenue or 0
            
            cursor.execute(
                "SELECT COUNT(*) FROM sales WHERE DATE(timestamp) = DATE('now')"
            )
            today_sales = cursor.fetchone()[0]
            
            conn.close()
            
            stats_message = (
                f"📊 *Sales Statistics*\n\n"
                f"💰 Total Revenue: *{total_revenue} BDT*\n"
                f"📈 Total Sales: *{total_sales}*\n"
                f"📅 Today's Sales: *{today_sales}*\n"
            )
            
            await query.edit_message_text(stats_message, parse_mode='Markdown')
        
        elif query.data == 'admin_stock':
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM profiles WHERE status = 'unsold'")
            unsold = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM profiles WHERE status = 'sold'")
            sold = cursor.fetchone()[0]
            
            conn.close()
            
            stock_message = (
                f"📦 *Stock Status*\n\n"
                f"✅ Available: *{unsold}* profiles\n"
                f"❌ Sold: *{sold}* profiles\n"
                f"📊 Total: *{unsold + sold}* profiles\n"
            )
            
            await query.edit_message_text(stock_message, parse_mode='Markdown')
        
        return ConversationHandler.END
    
    @staticmethod
    async def receive_bulk_profiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Process bulk profile addition"""
        if not AdminPanel.is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Unauthorized access.")
            return ConversationHandler.END
        
        text = update.message.text.strip()
        lines = text.split('\n')
        
        added = 0
        errors = []
        
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue
            
            parts = line.split(':')
            if len(parts) != 3:
                errors.append(f"Line {line_num}: Invalid format")
                continue
            
            email, password, pin = parts
            
            try:
                cursor.execute(
                    "INSERT INTO profiles (email, password, profile_pin) VALUES (?, ?, ?)",
                    (email.strip(), password.strip(), pin.strip())
                )
                added += 1
            except Exception as e:
                errors.append(f"Line {line_num}: {str(e)}")
        
        conn.commit()
        conn.close()
        
        result_message = f"✅ *Added {added} profiles successfully!*\n\n"
        
        if errors:
            result_message += "⚠️ *Errors:*\n" + "\n".join(errors[:10])
            if len(errors) > 10:
                result_message += f"\n... and {len(errors) - 10} more errors"
        
        await update.message.reply_text(result_message, parse_mode='Markdown')
        return ConversationHandler.END


def main():
    """Start the bot"""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable not set!")
        return
    
    if ADMIN_USER_ID == 0:
        logger.warning("ADMIN_USER_ID not set! Admin panel will not work.")
    
    # Initialize database
    init_database()
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Conversation handler for buying Netflix
    buy_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(NetflixBot.buy_netflix, pattern='^buy_netflix$')],
        states={
            WAITING_PAYMENT_SCREENSHOT: [
                MessageHandler(filters.PHOTO, NetflixBot.handle_payment_screenshot)
            ],
        },
        fallbacks=[CommandHandler('cancel', NetflixBot.cancel)],
        allow_reentry=True
    )
    
    # Conversation handler for admin bulk add
    admin_add_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(AdminPanel.admin_button_handler, pattern='^admin_add_profiles$')
        ],
        states={
            WAITING_BULK_PROFILES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, AdminPanel.receive_bulk_profiles)
            ],
        },
        fallbacks=[CommandHandler('cancel', NetflixBot.cancel)],
        allow_reentry=True
    )
    
    # Add handlers
    application.add_handler(CommandHandler('start', NetflixBot.start))
    application.add_handler(CommandHandler('admin', AdminPanel.admin))
    application.add_handler(buy_conv_handler)
    application.add_handler(admin_add_conv_handler)
    application.add_handler(
        CallbackQueryHandler(AdminPanel.admin_button_handler, pattern='^admin_(stats|stock)$')
    )
    
    # Start bot
    logger.info("Bot started successfully!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
