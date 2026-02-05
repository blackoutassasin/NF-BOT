# Netflix Profile Sales Bot 🎬

A professional Telegram bot for selling Netflix profiles with automated OCR payment verification.

## Features ✨

- **Automated Payment Verification**: Uses OCR to read Transaction ID and Amount from bKash/Nagad screenshots
- **Instant Delivery**: Profiles delivered immediately after successful verification
- **Admin Panel**: Bulk profile management and sales statistics
- **SQLite Database**: Stores profiles and sales records
- **Anti-Fraud**: Duplicate transaction detection
- **Professional UI**: Clean inline keyboards and formatted messages

## Tech Stack 🛠️

- Python 3.11+
- python-telegram-bot v20.7
- Pillow (Image processing)
- pytesseract (OCR)
- SQLite3 (Database)
- Tesseract OCR Engine

## Setup Instructions 📋

### 1. Get Your Bot Token

1. Open Telegram and search for [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the instructions
3. Copy the bot token you receive

### 2. Get Your User ID

1. Search for [@userinfobot](https://t.me/userinfobot) on Telegram
2. Send `/start` to get your user ID
3. Save this number for the ADMIN_USER_ID

### 3. Local Development Setup

```bash
# Clone or download the project
cd netflix-bot

# Install Python dependencies
pip install -r requirements.txt

# Install Tesseract OCR
# Ubuntu/Debian:
sudo apt-get install tesseract-ocr tesseract-ocr-eng

# macOS:
brew install tesseract

# Windows:
# Download installer from: https://github.com/UB-Mannheim/tesseract/wiki

# Create .env file
cp .env.example .env

# Edit .env file with your credentials
nano .env

# Run the bot
python main.py
```

### 4. Railway.app Deployment 🚀

#### Step 1: Prepare Your Repository

1. Create a new GitHub repository
2. Upload these files:
   - `main.py`
   - `requirements.txt`
   - `Dockerfile`
   - `.env.example`

#### Step 2: Deploy to Railway

1. Go to [Railway.app](https://railway.app/)
2. Sign up/Login with GitHub
3. Click "New Project" → "Deploy from GitHub repo"
4. Select your repository
5. Railway will auto-detect the Dockerfile

#### Step 3: Configure Environment Variables

In Railway Dashboard → Your Project → Variables, add:

```
BOT_TOKEN=your_actual_bot_token
ADMIN_USER_ID=your_telegram_user_id
BKASH_NUMBER=01XXXXXXXXX
NAGAD_NUMBER=01XXXXXXXXX
```

#### Step 4: Deploy

Railway will automatically build and deploy your bot!

## Usage Guide 📱

### For Customers:

1. Start the bot: `/start`
2. Click "Buy Netflix Profile (50 TK)"
3. Send 50 BDT to the provided bKash/Nagad number
4. Take a clear screenshot showing Transaction ID and Amount
5. Upload the screenshot
6. Receive your Netflix profile instantly!

### For Admin:

1. Send `/admin` to access the admin panel
2. **Add Profiles**: Bulk upload in format `email:password:pin`
3. **View Stats**: Check total sales and revenue
4. **Check Stock**: See available and sold profiles

## Admin Commands 🔐

- `/start` - Start the bot
- `/admin` - Access admin panel (restricted to ADMIN_USER_ID)
- `/cancel` - Cancel current operation

## Adding Profiles in Bulk 📦

Format (one per line):
```
email1@example.com:password1:1234
email2@example.com:password2:5678
email3@example.com:password3:9012
```

Example:
```
netflix.user1@gmail.com:SecurePass123:1111
netflix.user2@gmail.com:MyPass456:2222
netflix.user3@gmail.com:SafeWord789:3333
```

## Database Schema 🗄️

### Profiles Table
```sql
- id: INTEGER PRIMARY KEY
- email: TEXT
- password: TEXT
- profile_pin: TEXT
- status: TEXT (unsold/sold)
- sold_at: TIMESTAMP
- sold_to_user_id: INTEGER
```

### Sales Table
```sql
- id: INTEGER PRIMARY KEY
- user_id: INTEGER
- username: TEXT
- trxid: TEXT
- amount: INTEGER
- timestamp: TIMESTAMP
- profile_id: INTEGER (Foreign Key)
```

## OCR Requirements 📸

For best OCR results, screenshots should:
- Be clear and high resolution
- Show Transaction ID clearly
- Display the amount (50 BDT)
- Not be overly cropped
- Have good lighting/contrast

## Troubleshooting 🔧

### OCR Not Working
- Ensure Tesseract is installed: `tesseract --version`
- Check if pytesseract can find Tesseract
- On Windows, set: `pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'`

### Bot Not Responding
- Check if BOT_TOKEN is correct
- Ensure all environment variables are set
- Check Railway logs for errors

### Database Errors
- Ensure write permissions for database file
- Check if database is initialized properly

## Security Notes 🔒

- Never commit your `.env` file to Git
- Add `.env` and `*.db` to `.gitignore`
- Keep your BOT_TOKEN secret
- Only share ADMIN_USER_ID with trusted admins
- Regularly backup your database

## File Structure 📁

```
netflix-bot/
├── main.py              # Main bot application
├── requirements.txt     # Python dependencies
├── Dockerfile          # Docker configuration
├── .env.example        # Environment variables template
├── README.md           # This file
└── netflix_bot.db      # SQLite database (created automatically)
```

## Environment Variables 🌍

| Variable | Description | Example |
|----------|-------------|---------|
| BOT_TOKEN | Telegram bot token from BotFather | 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11 |
| ADMIN_USER_ID | Your Telegram user ID | 123456789 |
| BKASH_NUMBER | bKash mobile number | 01712345678 |
| NAGAD_NUMBER | Nagad mobile number | 01812345678 |

## Support 💬

For issues or questions:
- Check the logs in Railway dashboard
- Review the Troubleshooting section
- Ensure all dependencies are installed correctly

## License 📄

This project is for educational purposes. Ensure you comply with Netflix's terms of service.

## Credits 👨‍💻

Built with ❤️ using:
- python-telegram-bot
- Tesseract OCR
- Pillow

---

**Note**: This bot is designed for legitimate business use. Always comply with local laws and service terms.
