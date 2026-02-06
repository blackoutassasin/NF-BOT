"""
Netflix Profile Telegram Sales Bot - ENHANCED VERSION v3.1
Features: Pre-Start Menu, Free vs Paid Flow, Auto Admin from Environment
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
CHANNEL_USERNAME = os.getenv('CHANNEL_USERNAME', '@Unknowns_Zx')
CHANNEL_LINK = os.getenv('CHANNEL_LINK', 'https://t.me/Unknowns_Zx')
OWNER_USERNAME = os.getenv('OWNER_USERNAME', '@xenlize')
BKASH_NUMBER = os.getenv('BKASH_NUMBER', '01XXXXXXXXX')
NAGAD_NUMBER = os.getenv('NAGAD_NUMBER', '01XXXXXXXXX')
PRODUCT_PRICE = 50
REFERRAL_THRESHOLD = 20
DATABASE_PATH = 'netflix_bot.db'

# Admin configuration from environment
ADMIN_USER_IDS = os.getenv('ADMIN_USER_IDS', '')
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
            user_type TEXT DEFAULT 'unknown',
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
            added_by TEXT DEFAULT 'environment'
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")


async def load_admins_from_env():
    """Load admins from environment variable and database"""
    global ADMIN_LIST
    
    # Load from environment variable
    if ADMIN_USER_IDS:
        env_admins = [int(x.strip()) for x in ADMIN_USER_IDS.split(',') if x.strip().isdigit()]
        
        # Add to database
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        for admin_id in env_admins:
            try:
                cursor.execute(
                    "INSERT OR REPLACE INTO admins (user_id, added_by) VALUES (?, 'environment')",
                    (admin_id,)
                )
            except Exception as e:
                logger.error(f"Error adding admin {admin_id}: {e}")
        
        conn.commit()
        conn.close()
        
        ADMIN_LIST.extend(env_admins)
    
    # Load from database
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM admins")
    db_admins = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    # Merge and deduplicate
    ADMIN_LIST = list(set(ADMIN_LIST + db_admins))
    
    if ADMIN_LIST:
        logger.info(f"✅ Loaded {len(ADMIN_LIST)} admin(s): {ADMIN_LIST}")
    else:
        logger.warning("⚠️ No admins configured. Set ADMIN_USER_IDS environment variable.")


def is_admin(user_id: int) -> bool:
    """Check if user is admin"""
    return user_id in ADMIN_LIST


async def check_channel_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user has joined the required channel"""
    try:
        channel = CHANNEL_USERNAME
        if not channel.startswith('@'):
            channel = '@' + channel
        
        member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
        
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
        data = f"{user.id}_{user.username}_{user.first_name}_{user.language_code}"
        return hashlib.sha256(data.encode()).hexdigest()
    except:
        return None


def detect_vpn(update: Update, user_ip_hash: str) -> bool:
    """Detect potential VPN usage (simplified)"""
    try:
        user = update.effective_user
        suspicious_indicators = 0
        
        if user.username:
            username_lower = user.username.lower()
            for indicator in VPN_INDICATORS:
                if indicator in username_lower:
                    suspicious_indicators += 1
        
        if user.first_name:
            name_lower = user.first_name.lower()
            for indicator in VPN_INDICATORS:
                if indicator in name_lower:
                    suspicious_indicators += 1
        
        return suspicious_indicators >= 2
        
    except:
        return False


def generate_referral_code(user_id: int) -> str:
    """Generate unique referral code for user"""
    return f"REF{user_id}"


def register_user(user_id: int, username: str, first_name: str, user_type: str = 'unknown',
                 referred_by: Optional[int] = None, ip_hash: Optional[str] = None, is_vpn: bool = False):
    """Register or update user in database"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # Check if user exists
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    exists = cursor.fetchone()
    
    if not exists:
        referral_code = generate_referral_code(user_id)
        
        # Validate referral
        valid_referral = False
        if referred_by:
            cursor.execute("SELECT user_id, ip_hash, is_vpn_user FROM users WHERE user_id = ?", (referred_by,))
            referrer = cursor.fetchone()
            
            if referrer and ip_hash:
                referrer_ip = referrer[1]
                
                cursor.execute(
                    "SELECT COUNT(*) FROM users WHERE referred_by = ? AND ip_hash = ?",
                    (referred_by, ip_hash)
                )
                same_ip_count = cursor.fetchone()[0]
                
                if same_ip_count == 0 and ip_hash != referrer_ip and not is_vpn:
                    valid_referral = True
        
        cursor.execute(
            """INSERT INTO users (user_id, username, first_name, referral_code, referred_by, 
               ip_hash, is_vpn_user, user_type) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, username, first_name, referral_code, 
             referred_by if valid_referral else None, ip_hash, is_vpn, user_type)
        )
        
        # Update referrer's count if valid
        if valid_referral:
            cursor.execute(
                "UPDATE users SET referral_count = referral_count + 1 WHERE user_id = ?",
                (referred_by,)
            )
            
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
                    return True, new_free_profiles
    
    conn.commit()
    conn.close()
    return False, 0


class NetflixBot:
    """Main bot class handling all operations"""
    
    @staticmethod
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command - Show pre-start menu"""
        user = update.effective_user
        
        # Check if user came via referral link
        referred_by = None
        if context.args and len(context.args) > 0:
            ref_code = context.args[0]
            if ref_code.startswith('REF'):
                try:
                    referred_by = int(ref_code[3:])
                except:
                    pass
        
        # If user already registered, show main menu
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT user_type FROM users WHERE user_id = ?", (user.id,))
        existing = cursor.fetchone()
        conn.close()
        
        if existing and existing[0] != 'unknown':
            # User already chose path, show main menu
            await NetflixBot.show_main_menu(update, context)
            return
        
        # Show pre-start menu (choice between free and paid)
        keyboard = [
            [InlineKeyboardButton("🎁 Get Netflix for FREE", callback_data='choose_free')],
            [InlineKeyboardButton("💳 Buy Netflix (50 BDT)", callback_data='choose_paid')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_message = (
            f"👋 Welcome *{user.first_name}*!\n\n"
            f"🎬 *Netflix Profile Sales Bot*\n\n"
            f"Choose how you want to get Netflix:\n\n"
            f"🎁 *Get FREE Netflix*\n"
            f"• Join our channel\n"
            f"• Share referral link with friends\n"
            f"• 20 referrals = 1 FREE Netflix profile!\n\n"
            f"💳 *Buy Netflix Instantly*\n"
            f"• Pay only {PRODUCT_PRICE} BDT\n"
            f"• No channel join required\n"
            f"• Get profile within 24 hours\n\n"
            f"📋 *Choose your option:*"
        )
        
        # Store referral info in user data for later
        if referred_by:
            context.user_data['referred_by'] = referred_by
        
        await update.message.reply_text(
            welcome_message,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    
    @staticmethod
    async def choose_free_path(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """User chose free path - must join channel and get referral link"""
        query = update.callback_query
        await query.answer()
        
        user = update.effective_user
        ip_hash = get_user_ip_hash(update)
        is_vpn = detect_vpn(update, ip_hash)
        
        # Get referred_by from user_data if exists
        referred_by = context.user_data.get('referred_by')
        
        # Register user as free path
        register_user(user.id, user.username, user.first_name, 'free', referred_by, ip_hash, is_vpn)
        
        # VPN warning
        if is_vpn:
            await query.edit_message_text(
                "⚠️ *VPN/Proxy Detected*\n\n"
                "We detected you're using a VPN or proxy.\n\n"
                "⚠️ *Important:*\n"
                "• You can still get FREE Netflix via referrals\n"
                "• However, your referrals from VPN won't count\n"
                "• Consider buying directly (50 BDT) instead\n\n"
                "Proceeding to free path...",
                parse_mode='Markdown'
            )
            await asyncio.sleep(3)
        
        # Check channel membership
        channel_joined = await check_channel_membership(user.id, context)
        
        if not channel_joined:
            # Show channel join requirement
            keyboard = [
                [InlineKeyboardButton("📢 Join Our Channel", url=CHANNEL_LINK)],
                [InlineKeyboardButton("✅ I Joined - Get Referral Link", callback_data='verify_and_get_link')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"🎁 *Get Netflix for FREE!*\n\n"
                f"📋 *Steps to get FREE Netflix:*\n\n"
                f"1️⃣ Join our channel (required)\n"
                f"2️⃣ Get your unique referral link\n"
                f"3️⃣ Share with 20 friends\n"
                f"4️⃣ Get 1 FREE Netflix profile!\n\n"
                f"⚠️ *Important:*\n"
                f"• You MUST join our channel first\n"
                f"• Each of your referrals must also join\n"
                f"• Only unique, non-VPN users count\n\n"
                f"📢 *Channel:* {CHANNEL_LINK}\n\n"
                f"👇 *First, join the channel, then click below:*",
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        else:
            # Already joined, give referral link
            await NetflixBot.show_referral_link(update, context)
    
    @staticmethod
    async def verify_and_get_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Verify channel membership and show referral link"""
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
            
            # Show referral link
            await NetflixBot.show_referral_link(update, context)
        else:
            # Not joined yet
            keyboard = [
                [InlineKeyboardButton("📢 Join Our Channel", url=CHANNEL_LINK)],
                [InlineKeyboardButton("✅ I Joined - Get Referral Link", callback_data='verify_and_get_link')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"❌ *Not Joined Yet*\n\n"
                f"You haven't joined our channel.\n\n"
                f"Please join the channel first, then click 'I Joined'.\n\n"
                f"📢 *Channel:* {CHANNEL_LINK}",
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
    
    @staticmethod
    async def show_referral_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show user their referral link and stats"""
        query = update.callback_query
        if query:
            await query.answer()
            user_id = query.from_user.id
        else:
            user_id = update.effective_user.id
        
        # Get user stats
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """SELECT referral_code, referral_count, free_profiles_earned 
               FROM users WHERE user_id = ?""",
            (user_id,)
        )
        user_data = cursor.fetchone()
        conn.close()
        
        if user_data:
            ref_code, ref_count, free_earned = user_data
            remaining = REFERRAL_THRESHOLD - (ref_count % REFERRAL_THRESHOLD)
            
            # Create referral link
            bot_username = (await context.bot.get_me()).username
            ref_link = f"https://t.me/{bot_username}?start={ref_code}"
            
            keyboard = [
                [InlineKeyboardButton("🔗 Share Referral Link", 
                 url=f"https://t.me/share/url?url={ref_link}&text=Get FREE Netflix! Join via my link and help me earn a free profile! 🎬")],
                [InlineKeyboardButton("🔄 Refresh Stats", callback_data='verify_and_get_link')],
                [InlineKeyboardButton("💳 Buy Instead (50 BDT)", callback_data='choose_paid')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            message = (
                f"✅ *Channel Verified!*\n\n"
                f"🎁 *Your FREE Netflix Path*\n\n"
                f"👥 Your Referrals: *{ref_count}* / {REFERRAL_THRESHOLD}\n"
                f"🎉 Free Profiles Earned: *{free_earned}*\n"
                f"⏳ Next Free Profile in: *{remaining}* referrals\n\n"
                f"🔗 *Your Referral Link:*\n"
                f"`{ref_link}`\n\n"
                f"📋 *How to get FREE Netflix:*\n"
                f"1️⃣ Copy your referral link above\n"
                f"2️⃣ Share with friends via social media\n"
                f"3️⃣ Each friend MUST join our channel\n"
                f"4️⃣ When you reach {REFERRAL_THRESHOLD} referrals = FREE profile!\n\n"
                f"⚠️ *Rules:*\n"
                f"• All referrals must join: {CHANNEL_LINK}\n"
                f"• Only unique users count (no VPN/proxy)\n"
                f"• No multiple accounts from same device\n\n"
                f"💡 *Tip:* Want instant access? Buy for 50 BDT!"
            )
            
            if query:
                await query.edit_message_text(
                    message,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
            else:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
    
    @staticmethod
    async def choose_paid_path(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """User chose paid path - show payment instructions"""
        query = update.callback_query
        await query.answer()
        
        user = query.from_user
        ip_hash = get_user_ip_hash(update)
        is_vpn = detect_vpn(update, ip_hash)
        
        # Register user as paid path (even before payment)
        register_user(user.id, user.username, user.first_name, 'paid', None, ip_hash, is_vpn)
        
        # Check if profiles are available
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM profiles WHERE status = 'unsold'")
        available_count = cursor.fetchone()[0]
        conn.close()
        
        if available_count == 0:
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='back_to_start')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "❌ *Sorry! No profiles available right now.*\n\n"
                "Please try again later or contact admin.",
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
            return ConversationHandler.END
        
        payment_message = (
            f"💳 *Buy Netflix - Payment Instructions*\n\n"
            f"💰 Amount: *{PRODUCT_PRICE} BDT Only*\n\n"
            f"📱 *bKash Number:* `{BKASH_NUMBER}`\n"
            f"📱 *Nagad Number:* `{NAGAD_NUMBER}`\n\n"
            f"⚠️ *Payment Instructions:*\n"
            f"1️⃣ Send exactly {PRODUCT_PRICE} TK via Send Money\n"
            f"2️⃣ Take a CLEAR screenshot of transaction\n"
            f"3️⃣ Screenshot MUST show:\n"
            f"   • Transaction ID\n"
            f"   • Amount ({PRODUCT_PRICE} BDT)\n"
            f"   • Date & Time\n\n"
            f"✅ *Benefits of Paid Path:*\n"
            f"• NO channel join required!\n"
            f"• Get profile within 24 hours\n"
            f"• Direct admin support\n"
            f"• Can still earn via referrals\n\n"
            f"📸 *Next Step:*\n"
            f"Send your payment screenshot now ⬇️"
        )
        
        await query.edit_message_text(payment_message, parse_mode='Markdown')
        return WAITING_PAYMENT_SCREENSHOT
    
    @staticmethod
    async def back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Return to start menu"""
        query = update.callback_query
        await query.answer()
        
        keyboard = [
            [InlineKeyboardButton("🎁 Get Netflix for FREE", callback_data='choose_free')],
            [InlineKeyboardButton("💳 Buy Netflix (50 BDT)", callback_data='choose_paid')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "📋 *Choose your option:*\n\n"
            "🎁 Get FREE via referrals\n"
            "💳 Buy instantly for 50 BDT",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    
    @staticmethod
    async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show main menu for existing users"""
        user = update.effective_user
        
        keyboard = [
            [InlineKeyboardButton("🎁 My Referral Link", callback_data='verify_and_get_link')],
            [InlineKeyboardButton("💳 Buy Netflix", callback_data='choose_paid')],
            [InlineKeyboardButton("👨‍💼 Contact Admin", url=f'https://t.me/{OWNER_USERNAME[1:]}')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_message = (
            f"👋 Welcome back *{user.first_name}*!\n\n"
            f"🎬 *Netflix Profile Sales Bot*\n\n"
            f"📋 *Quick Access:*"
        )
        
        await update.message.reply_text(
            welcome_message,
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
                f"✅ *Benefit:* As a paid user, you can use all features without channel join!\n\n"
                f"Thank you for your patience! 🙏",
                parse_mode='Markdown'
            )
            
            # Notify all admins
            keyboard = [
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f'approve_payment_{payment_id}'),
                    InlineKeyboardButton("❌ Reject", callback_data=f'reject_payment_{payment_id}')
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            admin_message = (
                f"🔔 *New Payment Submission*\n\n"
                f"👤 User: {user.first_name} (@{user.username if user.username else 'N/A'})\n"
                f"🆔 User ID: `{user.id}`\n"
                f"📝 Payment ID: `{payment_id}`\n"
                f"💳 TrxID: `{trx_id if trx_id else 'Not detected'}`\n"
                f"💰 Amount: {amount if amount else 'Not detected'} BDT\n"
                f"💼 User Type: Paid User\n\n"
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
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='back_to_start')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "❌ Operation cancelled.",
            reply_markup=reply_markup
        )
        return ConversationHandler.END


class AdminPanel:
    """Admin commands for bot management"""
    
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
            "💡 *Bonus:* Share your referral link to earn more FREE profiles!"
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
                [InlineKeyboardButton("💳 Submit New Payment", callback_data='choose_paid')],
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
                         f"1️⃣ Appeal this decision (if you think it's a mistake)\n"
                         f"2️⃣ Submit a new payment with correct screenshot\n"
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
                        f"User can appeal or resubmit.",
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
            f"Please explain why this payment should be approved.\n\n"
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
            f"Thank you! 🙏",
            parse_mode='Markdown'
        )
        
        # Notify all admins
        admin_message = (
            f"📮 *Payment Appeal Received*\n\n"
            f"👤 User: {user.first_name} (@{user.username or 'N/A'})\n"
            f"🆔 User ID: `{user.id}`\n"
            f"📝 Payment ID: `{payment_id}`\n\n"
            f"💬 *Appeal:*\n{appeal_text}\n\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
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
        """Initiate broadcast"""
        query = update.callback_query
        await query.answer()
        
        if not is_admin(query.from_user.id):
            return
        
        await query.edit_message_text(
            "📢 *Broadcast Message*\n\n"
            "Send the message to broadcast to all users.\n\n"
            "Send /cancel to abort.",
            parse_mode='Markdown'
        )
        return WAITING_BROADCAST_MESSAGE
    
    @staticmethod
    async def receive_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Broadcast message to all users"""
        if not is_admin(update.effective_user.id):
            return ConversationHandler.END
        
        await update.message.reply_text("📤 Broadcasting...")
        
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
                logger.error(f"Broadcast failed for {user_id}: {e}")
        
        await update.message.reply_text(
            f"✅ *Broadcast Complete!*\n\n"
            f"✅ Sent: {success}\n"
            f"❌ Failed: {failed}",
            parse_mode='Markdown'
        )
        return ConversationHandler.END
    
    @staticmethod
    async def admin_message_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Message specific user"""
        query = update.callback_query
        await query.answer()
        
        if not is_admin(query.from_user.id):
            return
        
        await query.edit_message_text(
            "💬 *Message User*\n\nSend User ID.\n\nSend /cancel to abort.",
            parse_mode='Markdown'
        )
        return WAITING_USER_ID_TO_MESSAGE
    
    @staticmethod
    async def receive_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Receive user ID"""
        if not is_admin(update.effective_user.id):
            return ConversationHandler.END
        
        try:
            user_id = int(update.message.text.strip())
            context.user_data['message_target_user'] = user_id
            
            await update.message.reply_text(
                f"📝 Send message for User ID: `{user_id}`\n\nSend /cancel to abort.",
                parse_mode='Markdown'
            )
            return WAITING_MESSAGE_TO_USER
        except ValueError:
            await update.message.reply_text("❌ Invalid User ID.")
            return WAITING_USER_ID_TO_MESSAGE
    
    @staticmethod
    async def send_message_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send message to user"""
        if not is_admin(update.effective_user.id):
            return ConversationHandler.END
        
        target_user = context.user_data.get('message_target_user')
        if not target_user:
            await update.message.reply_text("❌ Error.")
            return ConversationHandler.END
        
        try:
            await update.message.copy(chat_id=target_user)
            await context.bot.send_message(
                chat_id=target_user,
                text=f"_Message from Admin {OWNER_USERNAME}_",
                parse_mode='Markdown'
            )
            
            await update.message.reply_text(f"✅ Sent to User ID: `{target_user}`", parse_mode='Markdown')
        except Exception as e:
            await update.message.reply_text(f"❌ Failed: {str(e)}")
        
        return ConversationHandler.END
    
    @staticmethod
    async def admin_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle admin buttons"""
        query = update.callback_query
        await query.answer()
        
        if not is_admin(query.from_user.id):
            return
        
        if query.data == 'admin_pending':
            await AdminPanel.admin_pending_payments(update, context)
        elif query.data == 'admin_add_profiles':
            await query.edit_message_text(
                "➕ *Add Profiles*\n\nFormat:\n`email:password:pin`\n\nSend /cancel to abort.",
                parse_mode='Markdown'
            )
            return WAITING_BULK_PROFILES
        elif query.data == 'admin_stats':
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*), SUM(amount) FROM sales WHERE status = 'completed'")
            total_sales, total_revenue = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM users")
            total_users = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM users WHERE is_paid_user = 1")
            paid_users = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM pending_payments WHERE status = 'pending'")
            pending = cursor.fetchone()[0]
            
            conn.close()
            
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='back_to_admin')]]
            
            await query.edit_message_text(
                f"📊 *Statistics*\n\n"
                f"💰 Revenue: *{total_revenue or 0} BDT*\n"
                f"📈 Sales: *{total_sales}*\n"
                f"👥 Users: *{total_users}*\n"
                f"💳 Paid: *{paid_users}*\n"
                f"⏳ Pending: *{pending}*",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
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
            
            await query.edit_message_text(
                f"📦 *Stock*\n\n✅ Available: *{unsold}*\n❌ Sold: *{sold}*",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        elif query.data == 'admin_list':
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='back_to_admin')]]
            await query.edit_message_text(
                f"👥 *Admins*\n\n{', '.join(map(str, ADMIN_LIST))}",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        elif query.data == 'admin_referrals':
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            cursor.execute(
                """SELECT first_name, username, referral_count, free_profiles_earned 
                   FROM users WHERE referral_count > 0 ORDER BY referral_count DESC LIMIT 10"""
            )
            top = cursor.fetchall()
            conn.close()
            
            message = "🎁 *Top Referrers*\n\n" if top else "No referrals yet."
            for ref in top:
                message += f"{ref[0]} (@{ref[1] or 'N/A'}): {ref[2]} refs | {ref[3]} free\n"
            
            keyboard = [[InlineKeyboardButton("🔙 Back", callback_data='back_to_admin')]]
            await query.edit_message_text(message, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
        elif query.data == 'back_to_admin':
            await AdminPanel.admin(update, context)
    
    @staticmethod
    async def receive_bulk_profiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Add profiles in bulk"""
        if not is_admin(update.effective_user.id):
            return ConversationHandler.END
        
        lines = update.message.text.strip().split('\n')
        added = 0
        
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        for line in lines:
            parts = line.split(':')
            if len(parts) == 3:
                try:
                    cursor.execute(
                        "INSERT INTO profiles (email, password, profile_pin) VALUES (?, ?, ?)",
                        tuple(p.strip() for p in parts)
                    )
                    added += 1
                except:
                    pass
        
        conn.commit()
        conn.close()
        
        await update.message.reply_text(f"✅ Added {added} profiles!", parse_mode='Markdown')
        return ConversationHandler.END


def main():
    """Start the bot"""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!")
        return
    
    init_database()
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Load admins
    async def post_init(app: Application):
        await load_admins_from_env()
    
    application.post_init = post_init
    
    # Handlers
    buy_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(NetflixBot.choose_paid_path, pattern='^choose_paid$')],
        states={WAITING_PAYMENT_SCREENSHOT: [MessageHandler(filters.PHOTO, NetflixBot.handle_payment_screenshot)]},
        fallbacks=[CommandHandler('cancel', NetflixBot.cancel)],
        allow_reentry=True
    )
    
    admin_add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(AdminPanel.admin_button_handler, pattern='^admin_add_profiles$')],
        states={WAITING_BULK_PROFILES: [MessageHandler(filters.TEXT & ~filters.COMMAND, AdminPanel.receive_bulk_profiles)]},
        fallbacks=[CommandHandler('cancel', NetflixBot.cancel)],
        allow_reentry=True
    )
    
    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(AdminPanel.admin_broadcast, pattern='^admin_broadcast$')],
        states={WAITING_BROADCAST_MESSAGE: [MessageHandler(filters.ALL & ~filters.COMMAND, AdminPanel.receive_broadcast_message)]},
        fallbacks=[CommandHandler('cancel', NetflixBot.cancel)],
        allow_reentry=True
    )
    
    message_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(AdminPanel.admin_message_user, pattern='^admin_message_user$')],
        states={
            WAITING_USER_ID_TO_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, AdminPanel.receive_user_id)],
            WAITING_MESSAGE_TO_USER: [MessageHandler(filters.ALL & ~filters.COMMAND, AdminPanel.send_message_to_user)]
        },
        fallbacks=[CommandHandler('cancel', NetflixBot.cancel)],
        allow_reentry=True
    )
    
    appeal_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(AdminPanel.start_appeal, pattern='^appeal_rejection_')],
        states={WAITING_REJECTION_APPEAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, AdminPanel.receive_appeal)]},
        fallbacks=[CommandHandler('cancel', NetflixBot.cancel)],
        allow_reentry=True
    )
    
    application.add_handler(CommandHandler('start', NetflixBot.start))
    application.add_handler(CommandHandler('admin', AdminPanel.admin))
    application.add_handler(buy_conv)
    application.add_handler(admin_add_conv)
    application.add_handler(broadcast_conv)
    application.add_handler(message_conv)
    application.add_handler(appeal_conv)
    
    application.add_handler(CallbackQueryHandler(NetflixBot.choose_free_path, pattern='^choose_free$'))
    application.add_handler(CallbackQueryHandler(NetflixBot.verify_and_get_link, pattern='^verify_and_get_link$'))
    application.add_handler(CallbackQueryHandler(NetflixBot.back_to_start, pattern='^back_to_start$'))
    application.add_handler(CallbackQueryHandler(AdminPanel.approve_payment, pattern='^approve_payment_'))
    application.add_handler(CallbackQueryHandler(AdminPanel.reject_payment, pattern='^reject_payment_'))
    application.add_handler(CallbackQueryHandler(AdminPanel.reject_with_reason, pattern='^reject_reason_'))
    application.add_handler(CallbackQueryHandler(AdminPanel.admin_button_handler, pattern='^admin_'))
    application.add_handler(CallbackQueryHandler(AdminPanel.admin_button_handler, pattern='^back_to_admin$'))
    
    logger.info("🚀 Bot started!")
    logger.info(f"📢 Channel: {CHANNEL_LINK}")
    logger.info(f"👥 Admins: {len(ADMIN_LIST)}")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
