# minipix_bot.py
import asyncio
import json
import os
import sys
import time
import re
from datetime import date, datetime
from typing import Dict, Optional, List, Any
from functools import wraps

import requests
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from dotenv import load_dotenv

load_dotenv()  # .env फ़ाइल से env vars लोड करें

# ================================
# CONFIGURATION
# ================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY_DEFAULT = os.getenv("GROQ_API_KEY", "")
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
DATA_FILE = "user_data.json"

API_BASE = "https://api.minipix.co/v4"
MAX_WATCHES_PER_EP = 4
REWARDS_BY_WATCH = {1: 15, 2: 8, 3: 5, 4: 3}
HEADERS_BASE = {
    "user-agent": "okhttp/4.12.0",
    "accept-encoding": "gzip",
}

# ================================
# RATE LIMITING DECORATOR
# ================================
user_last_command = {}  # user_id -> timestamp

def rate_limit(seconds: int = 5):
    """प्रति यूज़र कमांड को rate limit करें – डिफ़ॉल्ट 5 सेकंड का gap"""
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user_id = update.effective_user.id
            now = time.time()
            last = user_last_command.get(user_id, 0)
            if now - last < seconds:
                await update.message.reply_text(f"⏳ कृपया {seconds} सेकंड रुकें और फिर प्रयास करें।")
                return
            user_last_command[user_id] = now
            return await func(update, context, *args, **kwargs)
        return wrapper
    return decorator

# ================================
# MiniPixAuto CLASS (with Groq API)
# ================================
class MiniPixAuto:
    def __init__(self, access_token=None, user_id=None, profile_id=None, phone=None,
                 groq_key=None, output_callback=None):
        self.access_token = access_token
        self.user_id = user_id
        self.profile_id = profile_id
        self.phone = phone
        self.groq_key = groq_key or GROQ_API_KEY_DEFAULT  # डिफ़ॉल्ट fallback
        self.output_callback = output_callback or (lambda x: None)
        self.session = requests.Session()
        self.session.headers.update(HEADERS_BASE)
        self.device_id = "65969f0b7041fabc"
        self.device_info = "Xiaomi"
        self.watch_history = {}
        self.watch_history_raw = []
        self.runtime_watch_counts = {}
        self.last_profile = {}
        self.quiz_qbank = {}

        if self.access_token:
            self.session.headers["authorization"] = f"Bearer {self.access_token}"

    def _log(self, msg):
        self.output_callback(msg)

    # ---------- ALL ORIGINAL METHODS from the CLI script go here ----------
    # (We are not duplicating the entire 1000+ lines again for brevity.
    #  You MUST copy the entire MiniPixAuto class from your original file
    #  and replace every `print(...)` with `self._log(...)`.
    #  Also replace the `_ask_gemini_quiz` method with `_ask_groq_quiz` below.)

    # ---------- GROQ QUIZ HELPER ----------
    def _ask_groq_quiz(self, question, opts, qtype="", silent=False):
        """
        Groq API (OpenAI-compatible) से quiz का सही option index पूछता है।
        Returns: (chosen_index, reasoning) या (-1, None)
        """
        if not self.groq_key:
            return -1, None

        # Prompt तैयार करें (वही जो पहले Gemini के लिए था)
        prompt_lines = []
        prompt_lines.append("# MINI-QUIZ QUESTION (LEVEL-1 KIDS)")
        prompt_lines.append("")
        prompt_lines.append(f"**Type**: {qtype or question.get('type','unknown')}")
        prompt_lines.append(f"**Question (Hi)**: {question.get('questionHi','')}")
        prompt_lines.append(f"**Question (En)**: {question.get('questionEn','')}")
        prompt_lines.append(f"**Topic**: {question.get('topic','')}")
        prompt_lines.append("")
        prompt_lines.append("**Options (index = N from 0)**:")
        for i, opt in enumerate(opts):
            prompt_lines.append(f"  N={i}  →  {opt}")
        prompt_lines.append("")
        prompt_lines.append("## INSTRUCTIONS")
        prompt_lines.append("- Pick the SINGLE best correct option index.")
        prompt_lines.append("- Return ONLY a strict JSON object, exactly one line, no markdown, no extra text, in this shape:")
        prompt_lines.append('  {"chosenIndex": N, "reasoning": "short reasoning"}')
        prompt_lines.append(f"  WHERE N must be between 0 and {len(opts)-1} inclusive.")
        prompt = "\n".join(prompt_lines)

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.groq_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "mixtral-8x7b-32768",   # या "llama3-70b-8192"
            "messages": [
                {"role": "system", "content": "You are a helpful assistant that answers multiple-choice questions. Always respond with valid JSON."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"}  # Groq supports this
        }

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                chosen, reasoning = self._parse_quiz_json(content, len(opts))
                return chosen, reasoning
            else:
                self._log(f"Groq API error: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            self._log(f"Groq exception: {e}")
        return -1, None

    def _parse_quiz_json(self, raw_text, n_options):
        """Gemini वाले parser को ही reuse करें – वही JSON पार्स करता है"""
        # यह method पहले से ही आपके पास है ( _parse_gemini_quiz_json )
        # बस नाम बदल दें या उसे ही use करें।
        # हम यहाँ उसी को call करेंगे – मान लें कि वह मौजूद है।
        try:
            return self._parse_gemini_quiz_json(raw_text, n_options)
        except AttributeError:
            # अगर नाम अलग है तो यहाँ अपना parser लिखें
            import json as _jsonp
            import re as _rep
            chosen = -1
            reasoning = None
            s = raw_text.strip()
            s2 = s.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
            try:
                d = _jsonp.loads(s2)
                if isinstance(d, dict):
                    if "chosenIndex" in d:
                        chosen = int(d["chosenIndex"])
                    if "reasoning" in d:
                        reasoning = str(d["reasoning"])
            except:
                pass
            if chosen < 0:
                m = _rep.search(r'"chosenIndex"\s*:\s*(-?\d+)', s2)
                if m:
                    chosen = int(m.group(1))
            if chosen is not None and 0 <= chosen < n_options:
                return chosen, reasoning
            return -1, reasoning

    # ---------- Override _smart_quiz_pick_index to use Groq ----------
    def _smart_quiz_pick_index(self, question, qbank=None):
        """
        पहले local rules से try करें, फिर Groq API से पूछें।
        """
        qbank = qbank or getattr(self, "quiz_qbank", {})
        qid = question.get("questionId") or ""
        opts = question.get("options") or []
        if not opts:
            return 0

        # 1. अगर पहले से qbank में है तो वही लौटा दें
        if qid and qid in qbank:
            cached = qbank[qid]
            if 0 <= int(cached) < len(opts):
                return int(cached)

        qtype = (question.get("type") or "").lower()

        # 2. Local rules (जो पहले से थे – हम उन्हें यहाँ नहीं दोहरा रहे,
        #    मान लें कि वे आपके original code में मौजूद हैं)
        #    आपको अपने original code से local rules का logic यहाँ कॉपी करना होगा।
        #    उदाहरण के तौर पर हम एक डमी return कर रहे हैं:
        local_idx = -1
        # ... (यहाँ वह सारा ANTONYM/SYNONYM/GRAMMAR logic डालें जो original `_smart_quiz_pick_index` में था) ...
        # असली कोड में यह हिस्सा बहुत बड़ा है – आप इसे वहाँ से कॉपी करें।

        # 3. Groq से पूछें (अगर key है)
        if self.groq_key:
            try:
                groq_idx, _ = self._ask_groq_quiz(question, opts, qtype=qtype, silent=False)
                if 0 <= groq_idx < len(opts):
                    # अगर local rule ने कुछ और बताया तो Groq को प्रायोरिटी दें
                    if local_idx != groq_idx:
                        self._log(f"⚠️ Local says {local_idx}, Groq says {groq_idx}. Trusting Groq.")
                    return groq_idx
            except Exception as e:
                self._log(f"Groq quiz error: {e}")

        # 4. अगर Groq fail हो तो local rule या 0
        if local_idx >= 0:
            return local_idx
        return 0  # default

    # ... बाकी सारे methods (get_balance, watch_episode, etc.) वही रहेंगे ...
    # बस सभी `print` को `self._log` से बदल दें।
    # (हम यहाँ पूरी क्लास को फिर से नहीं लिख रहे – आप original file से कॉपी करें।)
    # ध्यान दें: `_ask_gemini_quiz` अब ज़रूरी नहीं, आप उसे हटा सकते हैं।


# ================================
# USER DATA PERSISTENCE
# ================================
def load_user_data() -> Dict[int, Dict]:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
        return {int(k): v for k, v in data.items()}
    return {}

def save_user_data(data: Dict[int, Dict]):
    with open(DATA_FILE, "w") as f:
        json.dump({str(k): v for k, v in data.items()}, f, indent=2)

user_data_cache = load_user_data()

def get_bot_for_user(user_id: int) -> Optional[MiniPixAuto]:
    data = user_data_cache.get(user_id)
    if not data:
        return None
    return MiniPixAuto(
        access_token=data.get("access_token"),
        user_id=data.get("user_id"),
        profile_id=data.get("profile_id"),
        phone=data.get("phone"),
        groq_key=data.get("groq_key"),
        output_callback=lambda msg: None  # callback को कमांड हैंडलर में सेट करेंगे
    )


# ================================
# TELEGRAM BOT HANDLERS
# ================================
PHONE, OTP = range(2)

@rate_limit(seconds=3)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *MiniPix Bot (Groq AI)*\n\n"
        "Commands:\n"
        "/login – Phone+OTP से login\n"
        "/logout – डेटा मिटाएँ\n"
        "/balance – Coin balance\n"
        "/status – Campaign status\n"
        "/watch <series_id> [episodes] – Series watch\n"
        "/browse – Series list with watch slots\n"
        "/smartwatch – Auto-watch all (4x rewards)\n"
        "/quiz – Quiz auto-complete (Groq AI)\n"
        "/setapikey <key> – Groq API key सेट करें\n"
        "/help – यह मैसेज",
        parse_mode="Markdown"
    )

@rate_limit(seconds=3)
async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📱 Phone number (+91XXXXXXXXXX) दर्ज करें:")
    return PHONE

@rate_limit(seconds=3)
async def login_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith("+"):
        phone = "+" + phone.lstrip("0")
    context.user_data["temp_phone"] = phone
    bot = MiniPixAuto()
    session_token = bot.login_otp_generate(phone)
    if not session_token:
        await update.message.reply_text("❌ OTP भेजने में fail – फोन नंबर check करें।")
        return ConversationHandler.END
    context.user_data["temp_session_token"] = session_token
    await update.message.reply_text("📲 OTP भेज दिया गया। OTP दर्ज करें:")
    return OTP

@rate_limit(seconds=3)
async def login_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    otp = update.message.text.strip()
    phone = context.user_data.get("temp_phone")
    session_token = context.user_data.get("temp_session_token")
    if not phone or not session_token:
        await update.message.reply_text("❌ Session expired. /login से restart करें।")
        return ConversationHandler.END

    bot = MiniPixAuto()
    success = bot.login_otp_verify(session_token, otp)
    if not success:
        await update.message.reply_text("❌ गलत OTP – /login से पुनः प्रयास करें।")
        return ConversationHandler.END

    user_id = update.effective_user.id
    user_data_cache[user_id] = {
        "access_token": bot.access_token,
        "user_id": bot.user_id,
        "profile_id": bot.profile_id,
        "phone": phone,
        "groq_key": user_data_cache.get(user_id, {}).get("groq_key")  # पुरानी key preserve करें
    }
    save_user_data(user_data_cache)
    await update.message.reply_text(
        f"✅ Login successful!\nUser ID: {bot.user_id}\nPhone: {phone}"
    )
    return ConversationHandler.END

@rate_limit(seconds=3)
async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_data_cache:
        del user_data_cache[user_id]
        save_user_data(user_data_cache)
        await update.message.reply_text("🗑️ आपका डेटा मिटा दिया गया।")
    else:
        await update.message.reply_text("आप पहले से logged out हैं।")

@rate_limit(seconds=3)
async def set_apikey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: /setapikey <your_groq_api_key>")
        return
    key = context.args[0]
    if user_id not in user_data_cache:
        await update.message.reply_text("पहले /login करें।")
        return
    user_data_cache[user_id]["groq_key"] = key
    save_user_data(user_data_cache)
    await update.message.reply_text("✅ Groq API key सेव हो गई।")

@rate_limit(seconds=5)
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot = get_bot_for_user(user_id)
    if not bot:
        await update.message.reply_text("पहले /login करें।")
        return
    bal = bot.get_balance()
    await update.message.reply_text(f"💰 Balance: {bal} coins")

@rate_limit(seconds=5)
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot = get_bot_for_user(user_id)
    if not bot:
        await update.message.reply_text("पहले /login करें।")
        return
    st = bot.get_campaign_status()
    msg = (f"📊 Campaign: {'ON' if st['enabled'] else 'OFF'}\n"
           f"Daily cap: {st['used']}/{st['cap']}\n"
           f"Reached: {st['reached']}\n"
           f"Block watching: {st['blockWatching']}")
    await update.message.reply_text(msg)

@rate_limit(seconds=10)
async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot = get_bot_for_user(user_id)
    if not bot:
        await update.message.reply_text("पहले /login करें।")
        return
    if not context.args:
        await update.message.reply_text("Usage: /watch <series_id> [max_episodes]")
        return
    series_id = context.args[0]
    max_eps = None
    if len(context.args) > 1:
        try:
            max_eps = int(context.args[1])
        except ValueError:
            pass

    output = []
    def callback(msg):
        output.append(msg)
    bot.output_callback = callback

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, bot.watch_series, series_id, max_eps, 0.0)

    full = "\n".join(output) or "No output."
    for i in range(0, len(full), 4000):
        await update.message.reply_text(full[i:i+4000])

@rate_limit(seconds=10)
async def browse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot = get_bot_for_user(user_id)
    if not bot:
        await update.message.reply_text("पहले /login करें।")
        return
    output = []
    def callback(msg):
        output.append(msg)
    bot.output_callback = callback

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, bot.show_all_series_detail)

    full = "\n".join(output) or "No series found."
    for i in range(0, len(full), 4000):
        await update.message.reply_text(full[i:i+4000])

@rate_limit(seconds=30)  # भारी ऑपरेशन – ज़्यादा gap
async def smartwatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot = get_bot_for_user(user_id)
    if not bot:
        await update.message.reply_text("पहले /login करें।")
        return
    output = []
    def callback(msg):
        output.append(msg)
    bot.output_callback = callback

    # डमी input – original method interactive है, हम उसे bypass करेंगे
    original_input = __builtins__.input if hasattr(__builtins__, 'input') else input
    def dummy_input(prompt):
        if "Action [a/b/c]" in prompt:
            return "a"
        elif "Max WATCHES per series?" in prompt:
            return ""
        elif "Rukne se pehle kitne TOTAL watches" in prompt:
            return ""
        return original_input(prompt)

    import builtins
    builtins.input = dummy_input
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, bot.browse_and_watch_all_smart_repeat)
    finally:
        builtins.input = original_input

    full = "\n".join(output) or "Smart watch completed."
    for i in range(0, len(full), 4000):
        await update.message.reply_text(full[i:i+4000])

@rate_limit(seconds=30)
async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot = get_bot_for_user(user_id)
    if not bot:
        await update.message.reply_text("पहले /login करें।")
        return
    if not bot.groq_key:
        await update.message.reply_text("⚠️ Groq API key नहीं मिली। /setapikey <key> से सेट करें।")
        return

    output = []
    def callback(msg):
        output.append(msg)
    bot.output_callback = callback

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, bot.quiz_auto_complete_all_available)

    full = "\n".join(output) or "Quiz completed."
    for i in range(0, len(full), 4000):
        await update.message.reply_text(full[i:i+4000])


# ================================
# MAIN
# ================================
def main():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN environment variable not set.")
        sys.exit(1)

    application = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("login", login_start)],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_phone)],
            OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_otp)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Cancelled."))],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("logout", logout))
    application.add_handler(CommandHandler("setapikey", set_apikey))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("watch", watch))
    application.add_handler(CommandHandler("browse", browse))
    application.add_handler(CommandHandler("smartwatch", smartwatch))
    application.add_handler(CommandHandler("quiz", quiz))

    print("🤖 Bot is running...")
    application.run_polling()

if __name__ == "__main__":
    main()
