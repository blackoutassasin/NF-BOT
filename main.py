"""
Netflix Profile Telegram Sales Bot - NEXT LEVEL VERSION
Features: Auto Admin Detection, Channel Verification, VPN Detection, Advanced Payment Management
"""

import os
import re
import sqlite3
import logging
import hashlib
import asyncio
from datetime import datetime
from io import BytesIO
from typing import Optional, Tuple, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler
)
from telegram.error import TelegramError

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
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_USERNAME = os.getenv('CHANNEL_USERNAME', '@Unknowns_Zx')  # Channel for referrals
CHANNEL_LINK = os.getenv('CHANNEL_LINK', 'https://t.me/Unknowns_Zx')
OWNER_USERNAME = os.getenv('OWNER_USERNAME', '@xenlize')
BKASH_NUMBER = os.getenv('BKASH_NUMBER', '01XXXXXXXXX')
NAGAD_NUMBER = os.getenv('NAGAD_NUMBER', '01XXXXXXXXX')
PRODUCT_PRICE = 50
REFERRAL_THRESHOLD = 20
DATABASE_PATH = 'netflix_bot.db'

# Auto-detected admins list
ADMIN_LIST = []

# Conversation states
WAITING_PAYMENT_SCREENSHOT = 1
WAITING_BULK_PROFILES = 2
WAITING_BROADCAST_MESSAGE = 3
WAITING_USER_ID_TO_MESSAGE = 4
WAITING_MESSAGE_TO_USER = 5
WAITING_REJECTION_APPEAL = 6

# Known VPN/Proxy IP ranges (simplified detection)
VPN_INDICATORS = [
    'vpn', 'proxy', 'anonymous', 'hide', 'tunnel', 'secure',
    'private', 'shield', 'guard', 'protect'
]

# Database initialization
def init_database():
    """Initialize SQLite database with all required tables"""
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
            status TEXT DEFAULT 'pending',
            FOREIGN KEY (profile_id) REFERENCES profiles(id)
        )
    ''')
    
    # Users table for tracking and referrals
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            referral_code TEXT UNIQUE,
            referred_by INTEGER,
            referral_count INTEGER DEFAULT 0,
            free_profiles_earned INTEGER DEFAULT 0,
            ip_hash TEXT,
            is_vpn_user BOOLEAN DEFAULT 0,
            channel_joined BOOLEAN DEFAULT 0,
            is_paid_user BOOLEAN DEFAULT 0,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (referred_by) REFERENCES users(user_id)
        )
    ''')
    
    # Pending payments table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pending_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            screenshot_file_id TEXT,
            trxid TEXT,
            amount INTEGER,
            submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'pending',
            rejection_reason TEXT,
            appeal_message TEXT,
            appeal_submitted_at TIMESTAMP
        )
    ''')
    
    # Admins table (auto-detected)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            added_by INTEGER
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")


async def detect_admins(application: Application):
    """Auto-detect bot admins by checking who can delete messages"""
    global ADMIN_LIST
    
    try:
        # Get bot info
        bot = application.bot
        me = await bot.get_me()
        
        # Try to get admins from a test approach
        # Since we can't directly query "who can manage bot", we'll use database
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM admins")
        admins = cursor.fetchall()
        conn.close()
        
        ADMIN_LIST = [admin[0] for admin in admins]
        
        if ADMIN_LIST:
            logger.info(f"✅ Auto-detected admins: {ADMIN_LIST}")
        else:
            logger.warning("⚠️ No admins in database. Use /makeadmin command to add admins.")
        
    except Exception as e:
        logger.error(f"Admin detection error: {e}")


def add_admin_to_db(user_id: int, username: str = None, first_name: str = None, added_by: int = None):
    """Add admin to database"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            "INSERT OR REPLACE INTO admins (user_id, username, first_name, added_by) VALUES (?, ?, ?, ?)",
            (user_id, username, first_name, added_by)
        )
        conn.commit()
        
        # Refresh admin list
        global ADMIN_LIST
        if user_id not in ADMIN_LIST:
            ADMIN_LIST.append(user_id)
        
        return True
    except Exception as e:
        logger.error(f"Error adding admin: {e}")
        return False
    finally:
        conn.close()


def is_admin(user_id: int) -> bool:
    """Check if user is admin"""
    return user_id in ADMIN_LIST


async def check_channel_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user has joined the required channel"""
    try:
        # Extract channel username from link
        channel = CHANNEL_USERNAME
        if not channel.startswith('@'):
            channel = '@' + channel
        
        member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
        
        # Check if user is member, admin, or creator
        if member.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
            return True
        return False
    except Exception as e:
        logger.error(f"Channel check error for user {user_id}: {e}")
        return False


def get_user_ip_hash(update: Update) -> Optional[str]:
    """Generate a hash from user info to track unique devices"""
    try:
        user = update.effective_user
        # Create fingerprint from user data
        data = f"{user.id}_{user.username}_{user.first_name}_{user.language_code}"
        return hashlib.sha256(data.encode()).hexdigest()
    except:
        return None


def detect_vpn(update: Update, user_ip_hash: str) -> bool:
    """
    Detect potential VPN usage (simplified)
    In production, integrate with VPN detection API like:
    - IPHub.info
    - IPQualityScore
    - VPN Blocker API
    """
    try:
        user = update.effective_user
        
        # Basic heuristics (for demonstration)
        # In real implementation, use actual VPN detection API
        
        # Check if user has suspicious patterns
        suspicious_indicators = 0
        
        # Check username for VPN keywords
        if user.username:
            username_lower = user.username.lower()
            for indicator in VPN_INDICATORS:
                if indicator in username_lower:
                    suspicious_indicators += 1
        
        # Check if first name contains VPN indicators
        if user.first_name:
            name_lower = user.first_name.lower()
            for indicator in VPN_INDICATORS:
                if indicator in name_lower:
                    suspicious_indicators += 1
        
        # If multiple indicators, likely VPN
        # In production, this should be replaced with actual VPN API check
        return suspicious_indicators >= 2
        
    except:
        return False


def generate_referral_code(user_id: int) -> str:
    """Generate unique referral code for user"""
    return f"REF{user_id}"


def register_user(user_id: int, username: str, first_name: str, referred_by: Optional[int] = None, 
                 ip_hash: Optional[str] = None, is_vpn: bool = False):
    """Register or update user in database"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # Check if user exists
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    exists = cursor.fetchone()
    
    if not exists:
        referral_code = generate_referral_code(user_id)
        
        # Validate referral - check if referred_by user exists and IP is unique
        valid_referral = False
        if referred_by:
            cursor.execute("SELECT user_id, ip_hash, is_vpn_user FROM users WHERE user_id = ?", (referred_by,))
            referrer = cursor.fetchone()
            
            if referrer and ip_hash:
                referrer_ip = referrer[1]
                
                # Check if this IP already referred someone
                cursor.execute(
                    "SELECT COUNT(*) FROM users WHERE referred_by = ? AND ip_hash = ?",
                    (referred_by, ip_hash)
                )
                same_ip_count = cursor.fetchone()[0]
                
                # Only count referral if different IP, not VPN, and referrer not VPN
                if same_ip_count == 0 and ip_hash != referrer_ip and not is_vpn:
                    valid_referral = True
        
        cursor.execute(
            """INSERT INTO users (user_id, username, first_name, referral_code, referred_by, 
               ip_hash, is_vpn_user) 
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, username, first_name, referral_code, 
             referred_by if valid_referral else None, ip_hash, is_vpn)
        )
        
        # Update referrer's count if valid
        if valid_referral:
            cursor.execute(
                "UPDATE users SET referral_count = referral_count + 1 WHERE user_id = ?",
                (referred_by,)
            )
            
            # Check if referrer earned a free profile
            cursor.execute("SELECT referral_count, free_profiles_earned FROM users WHERE user_id = ?", (referred_by,))
            ref_data = cursor.fetchone()
            if ref_data:
                referrals, free_earned = ref_data
                new_free_profiles = referrals // REFERRAL_THRESHOLD - free_earned
                
                if new_free_profiles > 0:
                    cursor.execute(
                        "UPDATE users SET free_profiles_earned = free_profiles_earned + ? WHERE user_id = ?",
                        (new_free_profiles, referred_by)
                    )
                    conn.commit()
                    conn.close()
                    return True, new_free_profiles  # Signal to send notification
    
    conn.commit()
    conn.close()
    return False, 0


class NetflixBot:
    """Main bot class handling all operations"""
    
    @staticmethod
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command with referral support"""
        user = update.effective_user
        ip_hash = get_user_ip_hash(update)
        is_vpn = detect_vpn(update, ip_hash)
        
        # Check for referral code
        referred_by = None
        is_referral_user = False
        if context.args and len(context.args) > 0:
            ref_code = context.args[0]
            if ref_code.startswith('REF'):
                try:
                    referred_by = int(ref_code[3:])
                    is_referral_user = True
                except:
                    pass
        
        # Register user
        earned_profile, count = register_user(
            user.id, 
            user.username, 
            user.first_name, 
            referred_by,
            ip_hash,
            is_vpn
        )
        
        # Notify referrer if they earned a free profile
        if earned_profile and referred_by:
            try:
                await context.bot.send_message(
                    chat_id=referred_by,
                    text=f"🎉 *Congratulations!*\n\n"
                         f"You've earned {count} free Netflix profile(s)!\n"
                         f"You now have {count * REFERRAL_THRESHOLD} successful referrals!\n\n"
                         f"Contact admin to claim your free profile(s)!",
                    parse_mode='Markdown'
                )
            except:
                pass
        
        # If VPN detected, show warning for referral users
        if is_vpn and is_referral_user:
            await update.message.reply_text(
                "⚠️ *VPN/Proxy Detected*\n\n"
                "We detected you're using a VPN or proxy.\n"
                "Referrals from VPN users are not counted.\n\n"
                "You can still buy Netflix profiles directly!",
                parse_mode='Markdown'
            )
        
        # Check if referral user needs to join channel
        if is_referral_user:
            channel_joined = await check_channel_membership(user.id, context)
            
            if not channel_joined:
                # Update database
                conn = sqlite3.connect(DATABASE_PATH)
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET channel_joined = 0 WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
                
                # Show channel join requirement
                keyboard = [
                    [InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)],
                    [InlineKeyboardButton("✅ I Joined", callback_data='verify_channel')]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(
                    f"👋 Welcome *{user.first_name}*!\n\n"
                    f"🎁 You joined via referral link!\n\n"
                    f"⚠️ *Important:* To access the bot, you must:\n"
                    f"1️⃣ Join our channel\n"
                    f"2️⃣ Click 'I Joined' button below\n\n"
                    f"📢 *Channel:* {CHANNEL_LINK}",
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
                return
            else:
                # Update database - user already joined
                conn = sqlite3.connect(DATABASE_PATH)
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET channel_joined = 1 WHERE user_id = ?", (user.id,))
                conn.commit()
                conn.close()
        
        # Main menu keyboard
        keyboard = [
            [InlineKeyboardButton("🎁 Request Product", callback_data='request_product')],
            [InlineKeyboardButton("🛒 Buy Netflix", callback_data='buy_netflix')],
            [InlineKeyboardButton("👨‍💼 Contact Admin", url=f'https://t.me/{OWNER_USERNAME[1:]}')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_message = (
            f"👋 Welcome *{user.first_name}*!\n\n"
            f"🎬 *Netflix Profile Sales Bot*\n\n"
            f"📦 *Product:* Netflix Profile (1 Month)\n"
            f"💰 *Price:* {PRODUCT_PRICE} BDT\n"
            f"💳 *Payment:* bKash/Nagad\n\n"
            f"🎁 *Referral Program:*\n"
            f"Refer {REFERRAL_THRESHOLD} friends = 1 FREE Netflix Profile!\n"
            f"_(Referral users must join our channel)_\n\n"
            f"📋 *Choose an option below:*"
        )
        
        await update.message.reply_text(
            welcome_message,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    
    @staticmethod
    async def verify_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Verify channel membership"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        
        # Check channel membership
        is_member = await check_channel_membership(user_id, context)
        
        if is_member:
            # Update database
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET channel_joined = 1 WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            
            # Show main menu
            keyboard = [
                [InlineKeyboardButton("🎁 Request Product", callback_data='request_product')],
                [InlineKeyboardButton("🛒 Buy Netflix", callback_data='buy_netflix')],
                [InlineKeyboardButton("👨‍💼 Contact Admin", url=f'https://t.me/{OWNER_USERNAME[1:]}')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"✅ *Channel Verified!*\n\n"
                f"Thank you for joining our channel!\n\n"
                f"🎬 *Netflix Profile Sales Bot*\n\n"
                f"📦 *Product:* Netflix Profile (1 Month)\n"
                f"💰 *Price:* {PRODUCT_PRICE} BDT\n\n"
                f"🎁 *Referral Program:*\n"
                f"Refer {REFERRAL_THRESHOLD} friends = 1 FREE Profile!\n\n"
                f"📋 *Choose an option below:*",
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        else:
            # Still not joined
            keyboard = [
                [InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK)],
                [InlineKeyboardButton("✅ I Joined", callback_data='verify_channel')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"❌ *Not Joined Yet*\n\n"
                f"You haven't joined our channel yet.\n\n"
                f"Please join the channel and click 'I Joined' again.\n\n"
                f"📢 *Channel:* {CHANNEL_LINK}",
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
    
    @staticmethod
    async def request_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle Request Product button - Available for both paid and referral users"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        
        # Get user stats
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """SELECT referral_code, referral_count, free_profiles_earned, is_paid_user, 
               channel_joined FROM users WHERE user_id = ?""",
            (user_id,)
        )
        user_data = cursor.fetchone()
        conn.close()
        
        if user_data:
            ref_code, ref_count, free_earned, is_paid, channel_joined = user_data
            remaining = REFERRAL_THRESHOLD - (ref_count % REFERRAL_THRESHOLD)
            
            # Create referral link
            bot_username = (await context.bot.get_me()).username
            ref_link = f"https://t.me/{bot_username}?start={ref_code}"
            
            keyboard = [
                [InlineKeyboardButton("🔗 Share Referral Link", 
                 url=f"https://t.me/share/url?url={ref_link}&text=Join this amazing Netflix bot! Get free profiles by referring friends!")],
                [InlineKeyboardButton("🔙 Back to Menu", callback_data='back_to_menu')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Different message for paid vs referral users
            user_type = "💳 Paid User" if is_paid else "🎁 Referral User"
            
            message = (
                f"🎁 *Referral Program*\n\n"
                f"👤 Status: {user_type}\n"
                f"👥 Your Referrals: *{ref_count}*\n"
                f"🎉 Free Profiles Earned: *{free_earned}*\n"
                f"⏳ Next Free Profile in: *{remaining}* referrals\n\n"
                f"🔗 *Your Referral Link:*\n"
                f"`{ref_link}`\n\n"
                f"📋 *How it works:*\n"
                f"1️⃣ Share your referral link\n"
                f"2️⃣ Friends must join our channel: {CHANNEL_LINK}\n"
                f"3️⃣ Every {REFERRAL_THRESHOLD} valid referrals = 1 FREE profile!\n\n"
                f"⚠️ *Important Rules:*\n"
                f"• Referral users MUST join channel\n"
                f"• VPN/Proxy users not counted\n"
                f"• Only unique users count\n"
                f"• No multiple accounts from same device\n\n"
                f"💡 *Tip:* Paid users (50 BDT) don't need channel join!"
            )
            
            await query.edit_message_text(
                message,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        else:
            await query.edit_message_text(
                "❌ Error loading your data. Please try /start again."
            )
    
    @staticmethod
    async def buy_netflix(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle buy button click - Show payment instructions (no channel requirement for paid users)"""
        query = update.callback_query
        await query.answer()
        
        # Check if profiles are available
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM profiles WHERE status = 'unsold'")
        available_count = cursor.fetchone()[0]
        conn.close()
        
        if available_count == 0:
            keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data='back_to_menu')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "❌ *Sorry! No profiles available right now.*\n\n"
                "Please contact admin or try again later.",
                parse_mode='Markdown',
                reply_markup=reply_markup
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
            f"• Take a clear screenshot of transaction\n"
            f"• Screenshot must show Transaction ID and Amount\n\n"
            f"✅ *Benefit:* Paid users (50 BDT) don't need to join channel!\n"
            f"You get direct access to all features!\n\n"
            f"📸 *Next Step:*\n"
            f"Send your payment screenshot now ⬇️\n\n"
            f"⏱️ Admin will verify and send profile within 24 hours"
        )
        
        await query.edit_message_text(payment_message, parse_mode='Markdown')
        return WAITING_PAYMENT_SCREENSHOT
    
    @staticmethod
    async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Return to main menu"""
        query = update.callback_query
        await query.answer()
        
        keyboard = [
            [InlineKeyboardButton("🎁 Request Product", callback_data='request_product')],
            [InlineKeyboardButton("🛒 Buy Netflix", callback_data='buy_netflix')],
            [InlineKeyboardButton("👨‍💼 Contact Admin", url=f'https://t.me/{OWNER_USERNAME[1:]}')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "📋 *Main Menu*\n\nChoose an option:",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    
    @staticmethod
    def extract_transaction_info(image: Image.Image) -> Tuple[Optional[str], Optional[int]]:
        """Extract Transaction ID and Amount from payment screenshot using OCR"""
        try:
            image = image.convert('L')
            text = pytesseract.image_to_string(image)
            logger.info(f"OCR extracted text: {text}")
            
            # Extract Transaction ID
            trx_patterns = [
                r'(?:TrxID|Transaction ID|TXN ID|TXNID|TRX)\s*:?\s*([A-Z0-9]{10})',
                r'\b([A-Z0-9]{10})\b',
            ]
            
            transaction_id = None
            for pattern in trx_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    transaction_id = match.group(1).upper()
                    if re.search(r'[A-Z]', transaction_id) and re.search(r'[0-9]', transaction_id):
                        break
            
            # Extract Amount
            amount_patterns = [
                r'(?:Amount|Total|Tk|BDT|৳)\s*:?\s*(\d+(?:\.\d{2})?)',
                r'(\d+(?:\.\d{2})?)\s*(?:Tk|BDT|৳|Taka)',
                r'\b(50(?:\.00)?)\b',
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
        """Process uploaded payment screenshot - ADMIN APPROVAL REQUIRED"""
        user = update.effective_user
        
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
            file_id = update.message.photo[-1].file_id
            
            # Extract transaction info
            trx_id, amount = NetflixBot.extract_transaction_info(image)
            
            # Save to pending payments
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            
            cursor.execute(
                """INSERT INTO pending_payments (user_id, username, screenshot_file_id, trxid, amount) 
                   VALUES (?, ?, ?, ?, ?)""",
                (user.id, user.username, file_id, trx_id, amount)
            )
            payment_id = cursor.lastrowid
            
            # Mark user as paid user
            cursor.execute("UPDATE users SET is_paid_user = 1 WHERE user_id = ?", (user.id,))
            
            conn.commit()
            conn.close()
            
            # Notify user
            await update.message.reply_text(
                f"✅ *Payment Submitted Successfully!*\n\n"
                f"📝 Payment ID: `{payment_id}`\n"
                f"💳 Transaction ID: `{trx_id if trx_id else 'Auto-detected'}`\n"
                f"💰 Amount: {amount if amount else 'Auto-detected'} BDT\n\n"
                f"⏳ *Status:* Pending Admin Approval\n\n"
                f"Your payment is under review. You'll receive your Netflix profile "
                f"within 24 hours after approval.\n\n"
                f"✅ *Note:* As a paid user, you now have full access without channel join requirement!\n\n"
                f"Thank you for your patience! 🙏",
                parse_mode='Markdown'
            )
            
            # Notify all admins
            keyboard = [
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f'approve_payment_{payment_id}'),
                    InlineKeyboardButton("❌ Reject", callback_data=f'reject_payment_{payment_id}')
                ],
                [InlineKeyboardButton("👤 View User", callback_data=f'view_user_{user.id}')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            admin_message = (
                f"🔔 *New Payment Submission*\n\n"
                f"👤 User: {user.first_name} (@{user.username if user.username else 'N/A'})\n"
                f"🆔 User ID: `{user.id}`\n"
                f"📝 Payment ID: `{payment_id}`\n"
                f"💳 TrxID: `{trx_id if trx_id else 'Not detected'}`\n"
                f"💰 Amount: {amount if amount else 'Not detected'} BDT\n"
                f"💼 User Type: Paid User (No channel requirement)\n\n"
                f"⏰ Submitted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            
            for admin_id in ADMIN_LIST:
                try:
                    await context.bot.send_photo(
                        chat_id=admin_id,
                        photo=file_id,
                        caption=admin_message,
                        parse_mode='Markdown',
                        reply_markup=reply_markup
                    )
                except Exception as e:
                    logger.error(f"Failed to notify admin {admin_id}: {e}")
            
            return ConversationHandler.END
            
        except Exception as e:
            logger.error(f"Error processing screenshot: {e}")
            await update.message.reply_text(
                "❌ *Processing Error*\n\n"
                "An error occurred. Please try again or contact admin.",
                parse_mode='Markdown'
            )
            return ConversationHandler.END
    
    @staticmethod
    async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel the current operation"""
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data='back_to_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "❌ Operation cancelled.",
            reply_markup=reply_markup
        )
        return ConversationHandler.END


class AdminPanel:
    """Admin commands for bot management"""
    
    @staticmethod
    async def makeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Make someone admin (only existing admins can do this, or first user becomes admin)"""
        user = update.effective_user
        
        # Check if any admins exist
        if not ADMIN_LIST:
            # First user becomes admin
            if add_admin_to_db(user.id, user.username, user.first_name):
                await update.message.reply_text(
                    f"✅ *You are now the first admin!*\n\n"
                    f"Use /admin to access admin panel.",
                    parse_mode='Markdown'
                )
            return
        
        # Check if user is admin
        if not is_admin(user.id):
            await update.message.reply_text("❌ Only admins can add other admins.")
            return
        
        # Check if replying to someone
        if update.message.reply_to_message:
            target_user = update.message.reply_to_message.from_user
            if add_admin_to_db(target_user.id, target_user.username, target_user.first_name, user.id):
                await update.message.reply_text(
                    f"✅ *{target_user.first_name}* is now an admin!\n\n"
                    f"They can access /admin panel.",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text("❌ Failed to add admin.")
        else:
            await update.message.reply_text(
                "❌ Reply to a user's message with /makeadmin to make them admin."
            )
    
    @staticmethod
    async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show admin panel"""
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Unauthorized access.")
            return
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Pending Payments", callback_data='admin_pending'),
                InlineKeyboardButton("📊 Stats", callback_data='admin_stats')
            ],
            [
                InlineKeyboardButton("➕ Add Profiles", callback_data='admin_add_profiles'),
                InlineKeyboardButton("📦 Stock", callback_data='admin_stock')
            ],
            [
                InlineKeyboardButton("📢 Broadcast", callback_data='admin_broadcast'),
                InlineKeyboardButton("💬 Message User", callback_data='admin_message_user')
            ],
            [
                InlineKeyboardButton("👥 Admins List", callback_data='admin_list'),
                InlineKeyboardButton("🎁 Referrals", callback_data='admin_referrals')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🔐 *Admin Panel*\n\nSelect an option:",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    
    @staticmethod
    async def admin_pending_payments(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show pending payments"""
        query = update.callback_query
        await query.answer()
        
        if not is_admin(query.from_user.id):
            return
        
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """SELECT id, user_id, username, trxid, amount, submitted_at 
               FROM pending_payments WHERE status = 'pending' 
               ORDER BY submitted_at DESC LIMIT 10"""
        )
        pending = cursor.fetchall()
        conn.close()
        
        if not pending:
            await query.edit_message_text(
                "✅ No pending payments!\n\nAll caught up! 🎉"
            )
            return
        
        message = "⏳ *Pending Payments*\n\n"
        for pay in pending:
            pay_id, user_id, username, trxid, amount, submitted = pay
            message += (
                f"📝 ID: `{pay_id}` | User: @{username or 'N/A'}\n"
                f"💳 TrxID: `{trxid or 'N/A'}` | 💰 {amount or '?'} BDT\n"
                f"⏰ {submitted}\n\n"
            )
        
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='back_to_admin')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            message,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    
    @staticmethod
    async def approve_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Approve a payment and deliver profile"""
        query = update.callback_query
        await query.answer()
        
        if not is_admin(query.from_user.id):
            return
        
        payment_id = int(query.data.split('_')[-1])
        
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        # Get payment details
        cursor.execute(
            "SELECT user_id, username, trxid, amount FROM pending_payments WHERE id = ?",
            (payment_id,)
        )
        payment = cursor.fetchone()
        
        if not payment:
            await query.edit_message_caption(
                caption="❌ Payment not found or already processed."
            )
            conn.close()
            return
        
        user_id, username, trxid, amount = payment
        
        # Check for available profile
        cursor.execute(
            "SELECT id, email, password, profile_pin FROM profiles WHERE status = 'unsold' LIMIT 1"
        )
        profile = cursor.fetchone()
        
        if not profile:
            await query.edit_message_caption(
                caption="❌ No profiles available! Add profiles first."
            )
            conn.close()
            return
        
        profile_id, email, password, pin = profile
        
        # Mark payment as approved
        cursor.execute(
            "UPDATE pending_payments SET status = 'approved' WHERE id = ?",
            (payment_id,)
        )
        
        # Record sale
        cursor.execute(
            """INSERT INTO sales (user_id, username, trxid, amount, profile_id, status) 
               VALUES (?, ?, ?, ?, ?, 'completed')""",
            (user_id, username, trxid or f'PAY{payment_id}', amount or PRODUCT_PRICE, profile_id)
        )
        
        # Mark profile as sold
        cursor.execute(
            """UPDATE profiles 
               SET status = 'sold', sold_at = ?, sold_to_user_id = ? 
               WHERE id = ?""",
            (datetime.now(), user_id, profile_id)
        )
        
        # Ensure user is marked as paid user
        cursor.execute("UPDATE users SET is_paid_user = 1 WHERE user_id = ?", (user_id,))
        
        conn.commit()
        conn.close()
        
        # Send profile to user
        success_message = (
            "✅ *Payment Approved!*\n\n"
            "🎬 *Your Netflix Profile:*\n\n"
            f"📧 *Email:* `{email}`\n"
            f"🔑 *Password:* `{password}`\n"
            f"📍 *Profile PIN:* `{pin}`\n\n"
            f"⏱ *Valid for:* 1 Month\n"
            f"💳 *Payment ID:* `{payment_id}`\n\n"
            "⚠️ *Important Notes:*\n"
            "• Do NOT change the password\n"
            "• Use only your assigned profile\n"
            "• Save these credentials securely\n\n"
            "✨ Enjoy your Netflix! 🍿\n\n"
            "💡 *Bonus:* You can still use referral system to earn FREE profiles!"
        )
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=success_message,
                parse_mode='Markdown'
            )
            
            await query.edit_message_caption(
                caption=f"✅ *Payment Approved & Profile Delivered!*\n\n"
                        f"User ID: `{user_id}`\n"
                        f"Payment ID: `{payment_id}`\n"
                        f"Profile sent successfully!",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Failed to send profile to user {user_id}: {e}")
            await query.edit_message_caption(
                caption=f"⚠️ Profile assigned but failed to send message.\n"
                        f"User ID: `{user_id}` - Contact manually.",
                parse_mode='Markdown'
            )
    
    @staticmethod
    async def reject_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Reject a payment with reason"""
        query = update.callback_query
        await query.answer()
        
        if not is_admin(query.from_user.id):
            return
        
        payment_id = int(query.data.split('_')[-1])
        
        # Store payment_id for appeal
        context.user_data['rejecting_payment_id'] = payment_id
        
        # Ask for rejection reason
        keyboard = [
            [InlineKeyboardButton("Invalid Screenshot", callback_data=f'reject_reason_invalid_{payment_id}')],
            [InlineKeyboardButton("Wrong Amount", callback_data=f'reject_reason_amount_{payment_id}')],
            [InlineKeyboardButton("Duplicate Transaction", callback_data=f'reject_reason_duplicate_{payment_id}')],
            [InlineKeyboardButton("Unclear Screenshot", callback_data=f'reject_reason_unclear_{payment_id}')],
            [InlineKeyboardButton("🔙 Cancel", callback_data='back_to_admin')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_caption(
            caption="⚠️ *Select Rejection Reason:*",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    
    @staticmethod
    async def reject_with_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Process rejection with specific reason"""
        query = update.callback_query
        await query.answer()
        
        if not is_admin(query.from_user.id):
            return
        
        # Parse callback data
        parts = query.data.split('_')
        reason_key = parts[2]
        payment_id = int(parts[3])
        
        reason_map = {
            'invalid': 'Invalid or fake screenshot',
            'amount': 'Wrong amount paid',
            'duplicate': 'Duplicate transaction',
            'unclear': 'Screenshot is unclear/unreadable'
        }
        
        reason = reason_map.get(reason_key, 'Payment could not be verified')
        
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT user_id FROM pending_payments WHERE id = ?",
            (payment_id,)
        )
        payment = cursor.fetchone()
        
        if payment:
            user_id = payment[0]
            cursor.execute(
                "UPDATE pending_payments SET status = 'rejected', rejection_reason = ? WHERE id = ?",
                (reason, payment_id)
            )
            conn.commit()
            
            # Notify user with appeal option
            keyboard = [
                [InlineKeyboardButton("📝 Appeal Rejection", callback_data=f'appeal_rejection_{payment_id}')],
                [InlineKeyboardButton("👨‍💼 Contact Admin", url=f'https://t.me/{OWNER_USERNAME[1:]}')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"❌ *Payment Rejected*\n\n"
                         f"Payment ID: `{payment_id}`\n"
                         f"Reason: {reason}\n\n"
                         f"⚠️ *What you can do:*\n"
                         f"1️⃣ Appeal this decision (explain below)\n"
                         f"2️⃣ Submit new payment with correct details\n"
                         f"3️⃣ Contact admin for clarification\n\n"
                         f"We're here to help! 🙏",
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
            except:
                pass
            
            await query.edit_message_caption(
                caption=f"❌ Payment {payment_id} rejected.\n"
                        f"Reason: {reason}\n"
                        f"User can appeal or contact admin.",
                parse_mode='Markdown'
            )
        
        conn.close()
    
    @staticmethod
    async def start_appeal(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """User starts appeal process"""
        query = update.callback_query
        await query.answer()
        
        payment_id = int(query.data.split('_')[-1])
        context.user_data['appealing_payment_id'] = payment_id
        
        await query.edit_message_text(
            f"📝 *Appeal Payment Rejection*\n\n"
            f"Payment ID: `{payment_id}`\n\n"
            f"Please send a message explaining why this payment should be approved.\n\n"
            f"You can:\n"
            f"• Explain the transaction\n"
            f"• Upload additional proof\n"
            f"• Clarify any confusion\n\n"
            f"Send your appeal message now ⬇️\n\n"
            f"Send /cancel to abort.",
            parse_mode='Markdown'
        )
        
        return WAITING_REJECTION_APPEAL
    
    @staticmethod
    async def receive_appeal(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive user's appeal message"""
        user = update.effective_user
        payment_id = context.user_data.get('appealing_payment_id')
        
        if not payment_id:
            await update.message.reply_text("❌ Error: No appeal in progress.")
            return ConversationHandler.END
        
        appeal_text = update.message.text
        
        # Update database
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE pending_payments SET appeal_message = ?, appeal_submitted_at = ? WHERE id = ?",
            (appeal_text, datetime.now(), payment_id)
        )
        conn.commit()
        conn.close()
        
        # Notify user
        await update.message.reply_text(
            f"✅ *Appeal Submitted!*\n\n"
            f"Payment ID: `{payment_id}`\n\n"
            f"Your appeal has been forwarded to admins.\n"
            f"You'll receive a response within 24 hours.\n\n"
            f"Thank you for your patience! 🙏",
            parse_mode='Markdown'
        )
        
        # Notify all admins
        admin_message = (
            f"📮 *Payment Appeal Received*\n\n"
            f"👤 User: {user.first_name} (@{user.username or 'N/A'})\n"
            f"🆔 User ID: `{user.id}`\n"
            f"📝 Payment ID: `{payment_id}`\n\n"
            f"💬 *Appeal Message:*\n{appeal_text}\n\n"
            f"⏰ Submitted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"Review and take action accordingly."
        )
        
        for admin_id in ADMIN_LIST:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=admin_message,
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
        
        return ConversationHandler.END
    
    @staticmethod
    async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Initiate broadcast to all users"""
        query = update.callback_query
        await query.answer()
        
        if not is_admin(query.from_user.id):
            return
        
        await query.edit_message_text(
            "📢 *Broadcast Message*\n\n"
            "Send the message you want to broadcast to all users.\n\n"
            "Supports: Text, Photos, Videos, Documents\n\n"
            "Send /cancel to abort.",
            parse_mode='Markdown'
        )
        return WAITING_BROADCAST_MESSAGE
    
    @staticmethod
    async def receive_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive and broadcast message to all users"""
        if not is_admin(update.effective_user.id):
            return ConversationHandler.END
        
        await update.message.reply_text("📤 Broadcasting... Please wait.")
        
        # Get all users
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        users = cursor.fetchall()
        conn.close()
        
        success = 0
        failed = 0
        
        for user in users:
            user_id = user[0]
            try:
                await update.message.copy(chat_id=user_id)
                success += 1
            except Exception as e:
                failed += 1
                logger.error(f"Broadcast failed for user {user_id}: {e}")
        
        await update.message.reply_text(
            f"✅ *Broadcast Complete!*\n\n"
            f"✅ Sent: {success}\n"
            f"❌ Failed: {failed}\n"
            f"📊 Total: {success + failed}",
            parse_mode='Markdown'
        )
        return ConversationHandler.END
    
    @staticmethod
    async def admin_message_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Initiate messaging a specific user"""
        query = update.callback_query
        await query.answer()
        
        if not is_admin(query.from_user.id):
            return
        
        await query.edit_message_text(
            "💬 *Message to User*\n\n"
            "Send the User ID you want to message.\n\n"
            "Send /cancel to abort.",
            parse_mode='Markdown'
        )
        return WAITING_USER_ID_TO_MESSAGE
    
    @staticmethod
    async def receive_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive user ID to message"""
        if not is_admin(update.effective_user.id):
            return ConversationHandler.END
        
        try:
            user_id = int(update.message.text.strip())
            context.user_data['message_target_user'] = user_id
            
            await update.message.reply_text(
                f"📝 Now send the message for User ID: `{user_id}`\n\n"
                f"Supports: Text, Photos, Videos, Documents\n\n"
                f"Send /cancel to abort.",
                parse_mode='Markdown'
            )
            return WAITING_MESSAGE_TO_USER
        except ValueError:
            await update.message.reply_text(
                "❌ Invalid User ID. Please send a numeric ID."
            )
            return WAITING_USER_ID_TO_MESSAGE
    
    @staticmethod
    async def send_message_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send message to specific user"""
        if not is_admin(update.effective_user.id):
            return ConversationHandler.END
        
        target_user = context.user_data.get('message_target_user')
        if not target_user:
            await update.message.reply_text("❌ Error: Target user not set.")
            return ConversationHandler.END
        
        try:
            await update.message.copy(chat_id=target_user)
            await context.bot.send_message(
                chat_id=target_user,
                text=f"_Message from Admin {OWNER_USERNAME}_",
                parse_mode='Markdown'
            )
            
            await update.message.reply_text(
                f"✅ Message sent to User ID: `{target_user}`",
                parse_mode='Markdown'
            )
        except Exception as e:
            await update.message.reply_text(
                f"❌ Failed to send message: {str(e)}"
            )
        
        return ConversationHandler.END
    
    @staticmethod
    async def admin_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle admin panel button clicks"""
        query = update.callback_query
        await query.answer()
        
        if not is_admin(query.from_user.id):
            await query.edit_message_text("❌ Unauthorized access.")
            return
        
        if query.data == 'admin_pending':
            await AdminPanel.admin_pending_payments(update, context)
            return
        
        elif query.data == 'admin_add_profiles':
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
            
            cursor.execute("SELECT COUNT(*), SUM(amount) FROM sales WHERE status = 'completed'")
            total_sales, total_revenue = cursor.fetchone()
            total_revenue = total_revenue or 0
            
            cursor.execute(
                "SELECT COUNT(*) FROM sales WHERE DATE(timestamp) = DATE('now') AND status = 'completed'"
            )
            today_sales = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM users")
            total_users = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM users WHERE is_paid_user = 1")
            paid_users = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM pending_payments WHERE status = 'pending'")
            pending_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM users WHERE is_vpn_user = 1")
            vpn_users = cursor.fetchone()[0]
            
            conn.close()
            
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='back_to_admin')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            stats_message = (
                f"📊 *Bot Statistics*\n\n"
                f"💰 Total Revenue: *{total_revenue} BDT*\n"
                f"📈 Total Sales: *{total_sales}*\n"
                f"📅 Today's Sales: *{today_sales}*\n"
                f"👥 Total Users: *{total_users}*\n"
                f"💳 Paid Users: *{paid_users}*\n"
                f"🔒 VPN Users Detected: *{vpn_users}*\n"
                f"⏳ Pending Approvals: *{pending_count}*"
            )
            
            await query.edit_message_text(
                stats_message,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        
        elif query.data == 'admin_stock':
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM profiles WHERE status = 'unsold'")
            unsold = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM profiles WHERE status = 'sold'")
            sold = cursor.fetchone()[0]
            
            conn.close()
            
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='back_to_admin')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            stock_message = (
                f"📦 *Stock Status*\n\n"
                f"✅ Available: *{unsold}* profiles\n"
                f"❌ Sold: *{sold}* profiles\n"
                f"📊 Total: *{unsold + sold}* profiles\n"
            )
            
            await query.edit_message_text(
                stock_message,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        
        elif query.data == 'admin_list':
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, username, first_name, added_at FROM admins ORDER BY added_at")
            admins = cursor.fetchall()
            conn.close()
            
            if admins:
                message = "👥 *Admin List*\n\n"
                for admin in admins:
                    user_id, username, first_name, added_at = admin
                    message += f"👤 {first_name} (@{username or 'N/A'})\n"
                    message += f"   🆔 `{user_id}`\n"
                    message += f"   📅 Added: {added_at[:10]}\n\n"
            else:
                message = "No admins found."
            
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='back_to_admin')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                message,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        
        elif query.data == 'admin_referrals':
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            
            cursor.execute(
                """SELECT user_id, first_name, username, referral_count, free_profiles_earned, is_paid_user 
                   FROM users WHERE referral_count > 0 
                   ORDER BY referral_count DESC LIMIT 10"""
            )
            top_referrers = cursor.fetchall()
            conn.close()
            
            if top_referrers:
                message = "🎁 *Top Referrers*\n\n"
                for ref in top_referrers:
                    user_id, name, username, count, earned, is_paid = ref
                    user_type = "💳" if is_paid else "🎁"
                    message += f"{user_type} {name} (@{username or 'N/A'})\n"
                    message += f"   📊 {count} referrals | 🎉 {earned} free profiles\n\n"
            else:
                message = "No referrals yet."
            
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='back_to_admin')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                message,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        
        elif query.data == 'back_to_admin':
            keyboard = [
                [
                    InlineKeyboardButton("✅ Pending", callback_data='admin_pending'),
                    InlineKeyboardButton("📊 Stats", callback_data='admin_stats')
                ],
                [
                    InlineKeyboardButton("➕ Add Profiles", callback_data='admin_add_profiles'),
                    InlineKeyboardButton("📦 Stock", callback_data='admin_stock')
                ],
                [
                    InlineKeyboardButton("📢 Broadcast", callback_data='admin_broadcast'),
                    InlineKeyboardButton("💬 Message User", callback_data='admin_message_user')
                ],
                [
                    InlineKeyboardButton("👥 Admins", callback_data='admin_list'),
                    InlineKeyboardButton("🎁 Referrals", callback_data='admin_referrals')
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "🔐 *Admin Panel*\n\nSelect an option:",
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        
        return ConversationHandler.END
    
    @staticmethod
    async def receive_bulk_profiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Process bulk profile addition"""
        if not is_admin(update.effective_user.id):
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
    
    # Initialize database
    init_database()
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Detect admins on startup
    async def post_init(application: Application):
        await detect_admins(application)
    
    application.post_init = post_init
    
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
    
    # Conversation handler for broadcast
    broadcast_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(AdminPanel.admin_broadcast, pattern='^admin_broadcast$')
        ],
        states={
            WAITING_BROADCAST_MESSAGE: [
                MessageHandler(filters.ALL & ~filters.COMMAND, AdminPanel.receive_broadcast_message)
            ],
        },
        fallbacks=[CommandHandler('cancel', NetflixBot.cancel)],
        allow_reentry=True
    )
    
    # Conversation handler for messaging user
    message_user_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(AdminPanel.admin_message_user, pattern='^admin_message_user$')
        ],
        states={
            WAITING_USER_ID_TO_MESSAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, AdminPanel.receive_user_id)
            ],
            WAITING_MESSAGE_TO_USER: [
                MessageHandler(filters.ALL & ~filters.COMMAND, AdminPanel.send_message_to_user)
            ],
        },
        fallbacks=[CommandHandler('cancel', NetflixBot.cancel)],
        allow_reentry=True
    )
    
    # Conversation handler for payment appeal
    appeal_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(AdminPanel.start_appeal, pattern='^appeal_rejection_')
        ],
        states={
            WAITING_REJECTION_APPEAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, AdminPanel.receive_appeal)
            ],
        },
        fallbacks=[CommandHandler('cancel', NetflixBot.cancel)],
        allow_reentry=True
    )
    
    # Add handlers
    application.add_handler(CommandHandler('start', NetflixBot.start))
    application.add_handler(CommandHandler('makeadmin', AdminPanel.makeadmin))
    application.add_handler(CommandHandler('admin', AdminPanel.admin))
    application.add_handler(buy_conv_handler)
    application.add_handler(admin_add_conv_handler)
    application.add_handler(broadcast_conv_handler)
    application.add_handler(message_user_conv_handler)
    application.add_handler(appeal_conv_handler)
    
    # Callback query handlers
    application.add_handler(CallbackQueryHandler(NetflixBot.verify_channel, pattern='^verify_channel$'))
    application.add_handler(CallbackQueryHandler(NetflixBot.request_product, pattern='^request_product$'))
    application.add_handler(CallbackQueryHandler(NetflixBot.back_to_menu, pattern='^back_to_menu$'))
    application.add_handler(CallbackQueryHandler(AdminPanel.approve_payment, pattern='^approve_payment_'))
    application.add_handler(CallbackQueryHandler(AdminPanel.reject_payment, pattern='^reject_payment_'))
    application.add_handler(CallbackQueryHandler(AdminPanel.reject_with_reason, pattern='^reject_reason_'))
    application.add_handler(
        CallbackQueryHandler(AdminPanel.admin_button_handler, 
                            pattern='^admin_(stats|stock|pending|referrals|list)$')
    )
    application.add_handler(CallbackQueryHandler(AdminPanel.admin_button_handler, pattern='^back_to_admin$'))
    
    # Start bot
    logger.info("🚀 Bot started successfully!")
    logger.info(f"📢 Channel: {CHANNEL_LINK}")
    logger.info(f"👨‍💼 Owner: {OWNER_USERNAME}")
    logger.info(f"👥 Admins will be auto-detected. Use /makeadmin to add first admin.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()            status TEXT DEFAULT 'unsold',
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
