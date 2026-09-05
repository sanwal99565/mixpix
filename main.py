#!/usr/bin/env python3
"""
MiniPix V2 Telegram Bot - Fixed Version
- Sessions 10-25
- Fixed 10s delay
- Model rotation on rate-limit
- Clean session summary logs
- Stronger quiz loop
"""

import os
import json
import time
import re
import logging
import asyncio
from datetime import date
from typing import Dict, Optional, List

import requests
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ───────────────────────── Config ─────────────────────────
API_BASE = "https://api.minipix.co/v4"
ACCOUNTS_FILE = "minipix_accounts.json"
USER_GROQ_FILE = "user_groq_keys.json"

MAX_WATCHES_PER_EP = 4
QUIZ_QUESTION_DELAY = 10          # Always 10 seconds
DEFAULT_SESSIONS = 15
MIN_SESSIONS = 10
MAX_SESSIONS = 25

GLOBAL_GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
LOG_CHANNEL_ID = os.environ.get("LOG_CHANNEL_ID", "")

# Models in priority order (your available ones)
GROQ_MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "qwen/qwen3.6-27b",
    "allam-2-7b",
]

HEADERS_BASE = {
    "user-agent": "okhttp/4.12.0",
    "accept-encoding": "gzip",
}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

(WAIT_PHONE, WAIT_OTP, WAIT_TOKEN, WAIT_QUIZ_SESSIONS) = range(4)


# ───────────────────── Log Helper ─────────────────────
def send_log_sync(text: str):
    if not LOG_CHANNEL_ID or not TELEGRAM_BOT_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": LOG_CHANNEL_ID,
                "text": text[:4090],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=12,
        )
    except Exception as e:
        logger.warning(f"Log error: {e}")


# ───────────────────── User Groq Keys ─────────────────────
def load_user_groq_keys() -> dict:
    if os.path.exists(USER_GROQ_FILE):
        try:
            with open(USER_GROQ_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_user_groq_keys(data: dict):
    try:
        with open(USER_GROQ_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Save keys error: {e}")


user_groq_keys: dict = load_user_groq_keys()


def get_user_groq_key(user_id: int) -> Optional[str]:
    key = user_groq_keys.get(str(user_id))
    if key:
        return key
    return GLOBAL_GROQ_API_KEY or None


# ───────────────────── MiniPix Core ─────────────────────
class MiniPixV2:
    def __init__(self):
        self.access_token = None
        self.user_id = None
        self.profile_id = None
        self.phone = None
        self.session = requests.Session()
        self.session.headers.update(HEADERS_BASE)
        self.device_id = "65969f0b7041fabc"
        self.device_info = "Xiaomi"
        self.watch_history = {}
        self.watch_history_raw = []
        self.runtime_watch_counts = {}
        self.last_profile = {}
        self.current_account_label = None
        self.accounts = self._load_accounts()
        self.current_model_index = 0  # for rotation

    def _load_accounts(self):
        if os.path.exists(ACCOUNTS_FILE):
            try:
                with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data.get("accounts", {}) if isinstance(data.get("accounts"), dict) else data
                    return {}
            except Exception:
                return {}
        return {}

    def _save_accounts(self):
        payload = {"accounts": self.accounts, "saved_at": date.today().isoformat()}
        try:
            with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            return True
        except Exception:
            return False

    def _store_current_account(self, label=None):
        if not (self.access_token and self.user_id):
            return False
        lbl = label or self.phone or self.current_account_label or f"acc_{str(self.user_id)[-6:]}"
        self.current_account_label = lbl
        self.accounts[lbl] = {
            "access_token": self.access_token,
            "user_id": self.user_id,
            "profile_id": self.profile_id,
            "phone": self.phone,
            "added_on": date.today().isoformat(),
        }
        return self._save_accounts()

    def list_accounts(self):
        return list(self.accounts.keys())

    def switch_account(self, label):
        if label not in self.accounts:
            return False, f"Account '{label}' not found"
        acc = self.accounts[label]
        token = acc.get("access_token")
        if not token:
            return False, "No token"
        self._reset_state()
        self.access_token = token
        self.user_id = acc.get("user_id")
        self.profile_id = acc.get("profile_id")
        self.phone = acc.get("phone")
        self.session.headers["authorization"] = f"Bearer {self.access_token}"
        self.current_account_label = label
        if self.user_id:
            ok = self.get_user()
            if ok:
                self._store_current_account(label)
                return True, f"Switched to {label}"
            return False, "Token expired"
        return True, f"Switched to {label}"

    def remove_account(self, label):
        if label not in self.accounts:
            return False
        del self.accounts[label]
        self._save_accounts()
        if self.current_account_label == label:
            self._reset_state()
        return True

    def _reset_state(self):
        self.access_token = None
        self.user_id = None
        self.profile_id = None
        self.phone = None
        self.current_account_label = None
        self.watch_history = {}
        self.watch_history_raw = []
        self.runtime_watch_counts = {}
        self.last_profile = {}
        if "authorization" in self.session.headers:
            del self.session.headers["authorization"]

    def _req(self, method, path, **kwargs):
        url = f"{API_BASE}{path}"
        try:
            r = self.session.request(method, url, timeout=30, **kwargs)
            try:
                data = r.json()
            except Exception:
                data = r.text
            return r.status_code, data
        except Exception as e:
            return 0, str(e)

    def login_otp_generate(self, phone):
        self.phone = phone
        payload = {"phone_number": phone}
        sc, data = self._req(
            "POST", "/login/generate-otp",
            headers={"content-type": "application/json; charset=utf-8"},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        if sc == 200 and isinstance(data, dict) and (
            data.get("message") == "OTP sent" or data.get("success")
        ):
            return data.get("session_token") or data.get("sessionToken")
        return None

    def login_otp_verify(self, session_token, otp, save_label=None):
        payload = {
            "client_id": "android",
            "device_id": self.device_id,
            "device_info": self.device_info,
            "otp": otp,
            "phone_number": self.phone,
            "session_token": session_token,
        }
        sc, data = self._req(
            "POST", "/login/verify-otp",
            headers={"content-type": "application/json; charset=utf-8"},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        if sc == 200 and isinstance(data, dict) and data.get("access_token"):
            self.access_token = data["access_token"]
            self.user_id = data.get("id") or data.get("_id")
            self.session.headers["authorization"] = f"Bearer {self.access_token}"
            self.get_user()
            self._store_current_account(save_label)
            return True
        return False

    def login_with_token(self, token, user_id=None, profile_id=None, label=None):
        self.access_token = token
        self.user_id = user_id
        self.profile_id = profile_id
        self.session.headers["authorization"] = f"Bearer {self.access_token}"
        if not self.get_user():
            return False
        self._store_current_account(label)
        return True

    def get_user(self):
        if not self.user_id:
            return False
        sc, data = self._req("GET", f"/users/{self.user_id}")
        if sc == 200 and isinstance(data, dict):
            self.user_id = data.get("_id", self.user_id)
            self.profile_id = data.get("master_profile", self.profile_id)
            phone = data.get("mobile")
            if phone and not self.phone:
                self.phone = phone
            return True
        return False

    def open_app(self):
        if not (self.user_id and self.profile_id):
            return False
        payload = {"openApp": {"_id": self.user_id, "date": date.today().isoformat()}}
        sc, data = self._req(
            "PATCH",
            f"/users/{self.user_id}/profiles/{self.profile_id}/open_app",
            headers={"content-type": "application/json; charset=utf-8"},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        return sc == 200 and isinstance(data, dict) and data.get("success")

    def get_balance(self):
        sc, data = self._req("GET", "/coins/balance")
        if sc == 200 and isinstance(data, dict):
            coins = data.get("coins", 0)
            if isinstance(coins, dict):
                coins = coins.get("coins", 0)
            return coins
        if sc == 200 and isinstance(data, (int, float)):
            return int(data)
        return None

    def get_balance_silent(self):
        return self.get_balance()

    def get_campaign_status(self):
        sc, data = self._req("GET", "/watch-campaign/status")
        if sc == 200 and isinstance(data, dict) and data.get("success"):
            cap = data.get("dailyVideoCap", {}) or {}
            return {
                "enabled": data.get("enabled", False),
                "cap": cap.get("cap", 0),
                "used": cap.get("used", 0),
                "reached": cap.get("reached", False),
            }
        return {"enabled": False, "cap": 0, "used": 0, "reached": False}

    def _collect_series_deep(self, obj, out_dict):
        if obj is None:
            return
        if isinstance(obj, dict):
            if (obj.get("_id") or obj.get("id") or obj.get("series_id")) and (
                obj.get("title") or obj.get("numberOfEpisodes") is not None
                or obj.get("totalEpisodes") is not None or obj.get("cardImage")
            ):
                sid = obj.get("_id") or obj.get("id") or obj.get("series_id")
                if sid and sid not in out_dict:
                    out_dict[sid] = obj
            for v in obj.values():
                self._collect_series_deep(v, out_dict)
        elif isinstance(obj, list):
            for item in obj:
                self._collect_series_deep(item, out_dict)

    def get_all_series(self, page_size=100, max_pages=10):
        found = {}
        try:
            sc, data = self._req("GET", "/short_search?page=home")
            if sc == 200 and isinstance(data, (dict, list)):
                self._collect_series_deep(data, found)
        except Exception:
            pass

        endpoints = [
            "/webseries?page={p}&pageSize={ps}",
            "/discover?type=webseries&page={p}&pageSize={ps}",
            "/home?page={p}&pageSize={ps}",
        ]
        for tmpl in endpoints:
            for page in range(1, max_pages + 1):
                try:
                    sc, data = self._req("GET", tmpl.format(p=page, ps=page_size))
                    if sc == 200 and isinstance(data, dict):
                        self._collect_series_deep(data, found)
                except Exception:
                    continue
        series_list = list(found.values())
        series_list.sort(key=lambda s: -int(s.get("numberOfEpisodes") or s.get("totalEpisodes") or 0))
        return series_list

    def get_episodes(self, series_id, page=1, page_size=50):
        sc, data = self._req("GET", f"/episodes?series_id={series_id}&page={page}&pageSize={page_size}")
        if sc == 200 and isinstance(data, dict):
            return data.get("episodes", []), data.get("total", 0)
        return [], 0

    def get_profile(self):
        if not (self.user_id and self.profile_id):
            return None
        sc, data = self._req("GET", f"/users/{self.user_id}/profiles/{self.profile_id}")
        if sc == 200 and isinstance(data, dict):
            profile = data.get("profile", {}) or {}
            self.last_profile = profile
            history = profile.get("watchHistory", []) or profile.get("watched", []) or []
            if not isinstance(history, list):
                history = []
            self.watch_history_raw = list(history)
            self.watch_history = {}
            for wh in history:
                if isinstance(wh, dict):
                    key = (wh.get("id"), wh.get("episodeNo"))
                    self.watch_history[key] = {
                        "watchedPct": wh.get("watchedPct", 0) or 0,
                        "time": wh.get("time", 0) or 0,
                    }
            return profile
        return None

    def get_watch_counts_from_profile(self):
        counts = {}
        try:
            self.get_profile()
        except Exception:
            pass
        for item in getattr(self, "watch_history_raw", []):
            if isinstance(item, dict):
                sid = item.get("id") or item.get("series_id")
                ep = item.get("episodeNo") or item.get("episode_no")
                pct = int(item.get("watchedPct") or item.get("progress") or 0)
                if sid and ep and pct >= 80:
                    k = (str(sid), str(ep))
                    counts[k] = counts.get(k, 0) + 1
        for k, c in getattr(self, "runtime_watch_counts", {}).items():
            counts[k] = max(counts.get(k, 0), c)
        return counts

    def _update_watch_progress(self, series_id, series_title, hindi_title, episode_no,
                               tc_in_ms, tc_out_ms, detail_image, watched_pct):
        if not (self.user_id and self.profile_id):
            return False
        try:
            watched_pct = int(watched_pct or 0)
        except Exception:
            watched_pct = 0
        if not tc_out_ms or tc_out_ms <= tc_in_ms:
            tc_out_ms = tc_in_ms + 60000
        duration = tc_out_ms - tc_in_ms
        current_time_ms = tc_out_ms if watched_pct >= 100 else int(tc_in_ms + (duration * watched_pct / 100))
        stored_pct = 100 if watched_pct >= 100 else watched_pct

        watch_obj = {
            "id": series_id, "title": series_title, "hindiTitle": hindi_title,
            "episodeNo": episode_no, "tcInMs": tc_in_ms, "tcOutMs": tc_out_ms,
            "detailImage": detail_image, "type": "episode",
            "progress": stored_pct, "time": current_time_ms,
            "watchedPct": stored_pct, "campaign": False,
        }
        try:
            sc, d = self._req(
                "PATCH", f"/users/{self.user_id}/profiles/{self.profile_id}",
                headers={"content-type": "application/json; charset=utf-8"},
                data=json.dumps({"watched": watch_obj}, ensure_ascii=False).encode("utf-8"),
            )
            return sc == 200
        except Exception:
            return False

    def _report_watch_progress_to_coins(self, series_id, episode_no, watched_pct):
        body = {
            "series_id": series_id, "episode_no": episode_no, "episodeNo": episode_no,
            "progress": watched_pct, "watchedPct": watched_pct, "campaign": False,
            "task_type": "watch_ladder",
        }
        for path in ["/coins/progress-report", "/coins/tasks/progress", "/watch-ladder/progress"]:
            try:
                sc, d = self._req("POST", path,
                    headers={"content-type": "application/json; charset=utf-8"},
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8"))
                if sc and sc < 500:
                    return True
            except Exception:
                pass
        return False

    def claim_reward_task(self, series_id=None):
        if not series_id:
            return False
        for path in [
            f"/coins/tasks/watch_ladder_{series_id}/claim",
            "/watch-ladder/claim",
            f"/coins/watch-ladder/{series_id}/claim",
        ]:
            try:
                sc, d = self._req("POST", path,
                    headers={"content-type": "application/json; charset=utf-8"},
                    data=json.dumps({"series_id": series_id, "campaign": False}).encode("utf-8"))
                if sc == 200:
                    return True
            except Exception:
                pass
        return False

    def watch_episode(self, episode, series_info, allow_repeat=True):
        series_id = series_info.get("_id") or series_info.get("id")
        if not series_id:
            return False, "no_id"
        ep_no = episode.get("episodeNo") or episode.get("number") or 0
        series_title = series_info.get("title") or ""
        hindi_title = series_info.get("hindiTitle") or series_title
        detail_image = series_info.get("cardImage") or ""

        try:
            tc_in = int(episode.get("tcIn") or 0)
            tc_out = int(episode.get("tcOut") or (tc_in + 60))
        except Exception:
            tc_in, tc_out = 0, 60
        if tc_out <= tc_in:
            tc_out = tc_in + 60
        tc_in_ms, tc_out_ms = tc_in * 1000, tc_out * 1000

        for pct in [1, 50, 80, 99, 100]:
            self._update_watch_progress(series_id, series_title, hindi_title, ep_no,
                                        tc_in_ms, tc_out_ms, detail_image, pct)
            if pct >= 80:
                self._report_watch_progress_to_coins(series_id, ep_no, pct)
            time.sleep(0.1)

        self.claim_reward_task(series_id)
        rk = (str(series_id), str(ep_no))
        self.runtime_watch_counts[rk] = self.runtime_watch_counts.get(rk, 0) + 1
        return True, "done"

    def browse_and_watch_all_smart_repeat(self, progress_callback=None, max_watches=200, telegram_user_id=None):
        def log(msg):
            if progress_callback:
                progress_callback(msg)

        log("Checking campaign...")
        all_series = self.get_all_series()
        if not all_series:
            return {"error": "No series found"}

        watch_counts = self.get_watch_counts_from_profile()
        total_watched = total_skipped = total_failed = 0
        balance_before = self.get_balance_silent()

        for si, s in enumerate(all_series, 1):
            if total_watched >= max_watches:
                break
            sid = s.get("_id") or s.get("id")
            if not sid:
                continue
            title = s.get("title") or "?"
            episodes, _ = self.get_episodes(sid, page=1, page_size=300)
            if not episodes:
                continue
            episodes = sorted(episodes, key=lambda e: int(e.get("episodeNo") or 0) if str(e.get("episodeNo") or "").isdigit() else 0)

            log(f"[{si}/{len(all_series)}] {title}")
            done = 0
            for ep in episodes:
                if total_watched >= max_watches:
                    break
                ep_no = ep.get("episodeNo")
                kp = (str(sid), str(ep_no))
                cnt = watch_counts.get(kp, 0) + self.runtime_watch_counts.get(kp, 0)
                if cnt >= MAX_WATCHES_PER_EP:
                    total_skipped += 1
                    continue
                ok, st = self.watch_episode(ep, s)
                if ok:
                    done += 1
                    total_watched += 1
                else:
                    total_failed += 1
            if done:
                log(f"  → {done} watches")

        bal_end = self.get_balance_silent()
        delta = (bal_end - balance_before) if (bal_end is not None and balance_before is not None) else None
        return {
            "watched": total_watched, "skipped": total_skipped, "failed": total_failed,
            "balance_before": balance_before, "balance_after": bal_end, "delta": delta,
        }

    # ── Quiz ──
    def get_quiz_status(self):
        sc, data = self._req("GET", "/quiz/status")
        if sc == 200 and isinstance(data, dict) and data.get("success"):
            return data
        return None

    def quiz_start_session(self):
        sc, data = self._req(
            "POST", "/quiz/session/start",
            headers={"content-type": "application/json; charset=utf-8"},
            data=json.dumps({}).encode("utf-8"),
        )
        if sc == 200 and isinstance(data, dict) and data.get("success"):
            session_obj = data.get("session") or {}
            question_obj = data.get("question")
            sid = session_obj.get("sessionId") or data.get("sessionId") or data.get("_id")
            if sid and question_obj:
                return sid, question_obj, session_obj
        return None, None, None

    def quiz_submit_answer(self, session_id, question_id, chosen_index):
        payload = {"sessionId": session_id, "questionId": question_id, "chosenIndex": chosen_index}
        sc, data = self._req(
            "POST", "/quiz/session/answer",
            headers={"content-type": "application/json; charset=utf-8"},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        if sc == 200 and isinstance(data, dict):
            return data
        return None

    def quiz_use_lifeline(self, session_id, question_id):
        payload = {"sessionId": session_id, "questionId": question_id}
        sc, data = self._req(
            "POST", "/quiz/session/lifeline",
            headers={"content-type": "application/json; charset=utf-8"},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        if sc == 200 and isinstance(data, dict) and data.get("success"):
            return data.get("removedOptions", [])
        return None

    def quiz_ad_ack(self, session_id):
        payload = {"sessionId": session_id}
        sc, data = self._req(
            "POST", "/quiz/session/ad-ack",
            headers={"content-type": "application/json; charset=utf-8"},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        if sc == 200 and isinstance(data, dict) and data.get("success"):
            return data.get("question")
        return None

    def _build_quiz_prompt(self, question, options):
        prompt = (
            "Multiple choice question. Choose the correct option.\n"
            "Reply with ONLY the option number (0, 1, 2 or 3).\n\n"
            f"Question:\n{question}\n\nOptions:\n"
        )
        for i, opt in enumerate(options):
            prompt += f"{i}. {opt}\n"
        prompt += "\nCorrect option number:"
        return prompt

    def _parse_quiz_answer(self, answer_text, options):
        if not answer_text:
            return None
        m = re.search(r"\b(\d+)\b", answer_text.strip())
        if m:
            idx = int(m.group(1))
            if 0 <= idx < len(options):
                return idx
        return None

    def ask_groq(self, question, options, telegram_user_id: int = None):
        """Try models one by one. On rate-limit / error → next model."""
        api_key = get_user_groq_key(telegram_user_id) if telegram_user_id else GLOBAL_GROQ_API_KEY
        if not api_key:
            return None

        prompt = self._build_quiz_prompt(question, options)
        start_idx = self.current_model_index

        for i in range(len(GROQ_MODELS)):
            model_idx = (start_idx + i) % len(GROQ_MODELS)
            model = GROQ_MODELS[model_idx]

            try:
                from groq import Groq
                client = Groq(api_key=api_key)
                completion = client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a quiz solver. Reply with ONLY a single integer (0, 1, 2 or 3). No other text."
                        },
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.0,
                    max_tokens=10,
                )
                answer_text = (completion.choices[0].message.content or "").strip()
                idx = self._parse_quiz_answer(answer_text, options)

                if idx is not None:
                    self.current_model_index = model_idx  # stick with working model
                    return idx

            except Exception as e:
                err = str(e).lower()
                # Rate limit / daily limit → try next model
                if any(x in err for x in ["rate", "limit", "quota", "429", "too many"]):
                    logger.warning(f"Model {model} hit limit, switching...")
                    self.current_model_index = (model_idx + 1) % len(GROQ_MODELS)
                    continue
                logger.warning(f"Model {model} error: {e}")
                continue

        return None

    def run_quiz_auto(self, max_sessions=15, question_delay=10, progress_callback=None, telegram_user_id=None):
        def log(msg):
            if progress_callback:
                progress_callback(msg)

        status = self.get_quiz_status()
        if not status:
            return {"error": "Could not fetch quiz status"}
        daily = status.get("dailyAttempts", {}) or {}
        if daily.get("exhausted"):
            return {"error": "Daily quiz attempts exhausted"}

        total_coins = 0
        sessions_done = 0
        grand_correct = 0
        grand_total_q = 0

        send_log_sync(
            f"<b>🧠 QUIZ STARTED</b>\n"
            f"User: <code>{telegram_user_id}</code>\n"
            f"Sessions: {max_sessions} | Delay: {question_delay}s"
        )

        for session_num in range(1, max_sessions + 1):
            log(f"Session {session_num}/{max_sessions} starting...")
            session_id, question_obj, session_meta = self.quiz_start_session()
            if not session_id or not question_obj:
                log(f"Session {session_num} failed to start")
                time.sleep(3)
                continue

            hearts = session_meta.get("hearts", 3) if session_meta else 3
            ad_every = session_meta.get("adGateEvery", 5) if session_meta else 5
            q_count = 0
            session_correct = 0
            session_coins = 0
            max_questions_per_session = 20  # safety

            while hearts > 0 and q_count < max_questions_per_session:
                if not question_obj or not isinstance(question_obj, dict):
                    break

                q_id = question_obj.get("questionId")
                q_text_hi = question_obj.get("questionHi") or ""
                q_text_en = question_obj.get("questionEn") or ""
                options = question_obj.get("options", [])
                q_idx = question_obj.get("index", q_count)
                q_total = question_obj.get("total", "?")

                if not q_id or not isinstance(options, list) or len(options) < 2:
                    break

                q_count += 1
                grand_total_q += 1
                combined = q_text_hi or q_text_en
                if q_text_en and q_text_hi and q_text_en != q_text_hi:
                    combined = f"{q_text_hi}\n[EN: {q_text_en}]"

                log(f"S{session_num} Q{q_count}: {(q_text_hi or q_text_en)[:60]}...")

                correct_index = self.ask_groq(combined, options, telegram_user_id=telegram_user_id)
                if correct_index is None:
                    # fallback guess
                    correct_index = 0

                correct_index = max(0, min(correct_index, len(options) - 1))

                time.sleep(question_delay)

                result = self.quiz_submit_answer(session_id, q_id, correct_index)
                if not result or not result.get("success"):
                    log("Submit failed, ending session")
                    break

                correct_flag = result.get("correct", False)
                coins_earned = int(result.get("coinsEarned") or 0)
                session_coins = result.get("coinsSoFar", session_coins)
                hearts = int(result.get("hearts", hearts))
                total_coins += coins_earned

                if correct_flag:
                    session_correct += 1
                    grand_correct += 1

                # Get next question
                next_info = result.get("next")
                if not next_info:
                    break

                if isinstance(next_info, dict):
                    if "question" in next_info and isinstance(next_info.get("question"), dict):
                        question_obj = next_info["question"]
                        session_id = result.get("sessionId") or session_id
                        continue
                    if "result" in next_info:
                        break

                # Ad gate
                if ad_every > 0 and (q_count % ad_every == 0):
                    nq = self.quiz_ad_ack(session_id)
                    if nq:
                        question_obj = nq
                        continue
                    break

                # If no clear next question, stop this session
                break

            sessions_done += 1
            # Clean session summary
            summary = (
                f"<b>📊 Session {session_num} Summary</b>\n"
                f"User: <code>{telegram_user_id}</code>\n"
                f"Questions: {q_count}\n"
                f"Correct: {session_correct}\n"
                f"Coins this session: {session_coins}\n"
                f"Hearts left: {hearts}"
            )
            send_log_sync(summary)
            log(f"Session {session_num} done → {session_correct}/{q_count} correct")

            if session_num < max_sessions:
                time.sleep(2)

        final = (
            f"<b>🏁 QUIZ FINISHED</b>\n"
            f"User: <code>{telegram_user_id}</code>\n"
            f"Sessions completed: {sessions_done}\n"
            f"Total Questions: {grand_total_q}\n"
            f"Total Correct: {grand_correct}\n"
            f"Coins earned: ~{total_coins}\n"
            f"Balance now: {self.get_balance()}"
        )
        send_log_sync(final)

        return {
            "sessions": sessions_done,
            "total_coins": total_coins,
            "total_questions": grand_total_q,
            "total_correct": grand_correct,
            "balance": self.get_balance(),
        }


# ───────────────────── Bot Instances ─────────────────────
user_bots: Dict[int, MiniPixV2] = {}


def get_bot(user_id: int) -> MiniPixV2:
    if user_id not in user_bots:
        user_bots[user_id] = MiniPixV2()
    return user_bots[user_id]


def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("💰 Balance"), KeyboardButton("📊 Campaign")],
            [KeyboardButton("👥 Accounts"), KeyboardButton("➕ Login")],
            [KeyboardButton("🎬 Watch All (4x)"), KeyboardButton("🧠 Quiz Status")],
            [KeyboardButton("🤖 Run Quiz"), KeyboardButton("🔑 Set Groq Key")],
            [KeyboardButton("ℹ️ Help")],
        ],
        resize_keyboard=True,
    )


# ───────────────────── Handlers ─────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    bot = get_bot(user.id)
    text = f"👋 Hi {user.first_name}!\n\nMiniPix Bot ready."
    if bot.access_token:
        text += f"\n✅ Logged in: {bot.current_account_label or bot.phone}"
    else:
        text += "\n⚠️ Not logged in → /login"
    await update.message.reply_text(text, reply_markup=main_menu_keyboard())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 Commands\n\n"
        "/setgroq gsk_xxx – set your Groq key\n"
        "/login – login account\n"
        "/watch – 4x watch\n"
        "/quiz – status\n"
        "/logout – logout",
        reply_markup=main_menu_keyboard(),
    )


async def set_groq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/setgroq gsk_your_key`", parse_mode="Markdown")
        return
    key = context.args[0].strip()
    if not key.startswith("gsk_"):
        await update.message.reply_text("❌ Key must start with gsk_")
        return
    user_groq_keys[str(update.effective_user.id)] = key
    save_user_groq_keys(user_groq_keys)
    await update.message.reply_text("✅ Groq key saved!")


async def my_groq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = get_user_groq_key(update.effective_user.id)
    if key:
        await update.message.reply_text(f"✅ Key set: `{key[:10]}...{key[-4:]}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ No key set. Use /setgroq")


async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot = get_bot(update.effective_user.id)
    if not bot.access_token:
        await update.message.reply_text("Not logged in")
        return
    coins = bot.get_balance()
    await update.message.reply_text(f"💰 Balance: *{coins}*", parse_mode="Markdown")


async def campaign_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot = get_bot(update.effective_user.id)
    if not bot.access_token:
        await update.message.reply_text("Not logged in")
        return
    st = bot.get_campaign_status()
    await update.message.reply_text(
        f"🎥 Campaign: {'ON' if st['enabled'] else 'OFF'}\n"
        f"Cap: {st['used']}/{st['cap']}"
    )


async def accounts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot = get_bot(update.effective_user.id)
    accs = bot.list_accounts()
    if not accs:
        await update.message.reply_text("No accounts")
        return
    lines = [f"👥 Accounts ({len(accs)})"]
    kb = []
    for lbl in accs:
        lines.append(f"• {lbl}")
        kb.append([
            InlineKeyboardButton(f"Switch {lbl}", callback_data=f"sw:{lbl}"),
            InlineKeyboardButton("❌", callback_data=f"rm:{lbl}"),
        ])
    await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))


async def account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    bot = get_bot(query.from_user.id)
    data = query.data
    if data.startswith("sw:"):
        ok, msg = bot.switch_account(data[3:])
        if ok:
            bot.open_app()
            await query.edit_message_text(f"✅ {msg}\nBalance: {bot.get_balance()}")
        else:
            await query.edit_message_text(f"❌ {msg}")
    elif data.startswith("rm:"):
        if bot.remove_account(data[3:]):
            await query.edit_message_text("Removed")
        else:
            await query.edit_message_text("Failed")


async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📱 Phone + OTP", callback_data="login:otp")],
        [InlineKeyboardButton("🔑 Token", callback_data="login:token")],
    ]
    await update.message.reply_text("Login method:", reply_markup=InlineKeyboardMarkup(kb))


async def login_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "login:otp":
        await query.edit_message_text("Phone number bhejo:")
        return WAIT_PHONE
    elif query.data == "login:token":
        await query.edit_message_text("Bearer token bhejo:")
        return WAIT_TOKEN
    return ConversationHandler.END


async def login_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith("+"):
        phone = "+91" + phone.lstrip("0")
    bot = get_bot(update.effective_user.id)
    st = bot.login_otp_generate(phone)
    if not st:
        await update.message.reply_text("OTP fail")
        return ConversationHandler.END
    context.user_data["session_token"] = st
    await update.message.reply_text("OTP bhejo:")
    return WAIT_OTP


async def login_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    otp = update.message.text.strip()
    bot = get_bot(update.effective_user.id)
    st = context.user_data.get("session_token")
    if not st:
        await update.message.reply_text("Session lost")
        return ConversationHandler.END
    if bot.login_otp_verify(st, otp):
        bot.open_app()
        await update.message.reply_text(f"✅ Login OK\nBalance: {bot.get_balance()}", reply_markup=main_menu_keyboard())
    else:
        await update.message.reply_text("❌ OTP wrong")
    return ConversationHandler.END


async def login_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = update.message.text.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    bot = get_bot(update.effective_user.id)
    if bot.login_with_token(token):
        bot.open_app()
        await update.message.reply_text(f"✅ Login OK\nBalance: {bot.get_balance()}", reply_markup=main_menu_keyboard())
    else:
        await update.message.reply_text("❌ Invalid token")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


async def watch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot = get_bot(update.effective_user.id)
    if not bot.access_token:
        await update.message.reply_text("Not logged in")
        return
    uid = update.effective_user.id
    msg = await update.message.reply_text("🚀 Watch starting...")

    def progress(text):
        try:
            asyncio.get_event_loop().create_task(msg.edit_text(f"🚀 {text[-800:]}"))
        except Exception:
            pass

    result = await asyncio.get_event_loop().run_in_executor(
        None, lambda: bot.browse_and_watch_all_smart_repeat(progress_callback=progress, telegram_user_id=uid)
    )
    if "error" in result:
        await msg.edit_text(f"❌ {result['error']}")
        return
    text = f"🏁 Done\nWatched: {result['watched']}\nSkipped: {result['skipped']}"
    if result.get("delta") is not None:
        text += f"\n💰 {result['balance_before']} → {result['balance_after']} ({result['delta']:+d})"
    await msg.edit_text(text)


async def quiz_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot = get_bot(update.effective_user.id)
    if not bot.access_token:
        await update.message.reply_text("Not logged in")
        return
    data = bot.get_quiz_status()
    if not data:
        await update.message.reply_text("Failed")
        return
    daily = data.get("dailyAttempts", {}) or {}
    await update.message.reply_text(
        f"🧠 Level: {data.get('currentLevel')}\n"
        f"Daily: {daily.get('used')}/{daily.get('limit')}"
    )


async def quiz_run_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot = get_bot(update.effective_user.id)
    if not bot.access_token:
        await update.message.reply_text("Not logged in")
        return ConversationHandler.END
    if not get_user_groq_key(update.effective_user.id):
        await update.message.reply_text("❌ Pehle /setgroq se key set karo")
        return ConversationHandler.END

    await update.message.reply_text(
        f"Kitne sessions? ({MIN_SESSIONS}-{MAX_SESSIONS}, default {DEFAULT_SESSIONS}):"
    )
    return WAIT_QUIZ_SESSIONS


async def quiz_sessions_and_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        n = int(update.message.text.strip() or str(DEFAULT_SESSIONS))
        n = max(MIN_SESSIONS, min(MAX_SESSIONS, n))
    except Exception:
        n = DEFAULT_SESSIONS

    bot = get_bot(update.effective_user.id)
    uid = update.effective_user.id
    msg = await update.message.reply_text(f"🤖 Running {n} sessions (delay 10s)...")

    def progress(text):
        try:
            asyncio.get_event_loop().create_task(msg.edit_text(f"🤖 {text[-900:]}"))
        except Exception:
            pass

    result = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: bot.run_quiz_auto(
            max_sessions=n,
            question_delay=10,
            progress_callback=progress,
            telegram_user_id=uid,
        ),
    )

    if "error" in result:
        await msg.edit_text(f"❌ {result['error']}")
    else:
        await msg.edit_text(
            f"🏁 Finished\n"
            f"Sessions: {result.get('sessions')}\n"
            f"Questions: {result.get('total_questions')}\n"
            f"Correct: {result.get('total_correct')}\n"
            f"Coins: ~{result.get('total_coins')}\n"
            f"Balance: {result.get('balance')}"
        )
    return ConversationHandler.END


async def logout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_bot(update.effective_user.id)._reset_state()
    await update.message.reply_text("Logged out", reply_markup=main_menu_keyboard())


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    mapping = {
        "💰 Balance": balance_cmd,
        "📊 Campaign": campaign_cmd,
        "👥 Accounts": accounts_cmd,
        "➕ Login": login_start,
        "🎬 Watch All (4x)": watch_cmd,
        "🧠 Quiz Status": quiz_status_cmd,
        "🤖 Run Quiz": quiz_run_start,
        "🔑 Set Groq Key": lambda u, c: u.message.reply_text("Use: /setgroq gsk_xxx"),
        "ℹ️ Help": help_cmd,
    }
    handler = mapping.get(text)
    if handler:
        return await handler(update, context)
    await update.message.reply_text("Use menu buttons")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception:", exc_info=context.error)
    if "Conflict" in str(context.error):
        logger.error("CONFLICT: Multiple bot instances running. Keep only ONE instance on Railway.")


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN missing")
        return

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    login_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(login_callback, pattern=r"^login:")],
        states={
            WAIT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_phone)],
            WAIT_OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_otp)],
            WAIT_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_token)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    quiz_conv = ConversationHandler(
        entry_points=[
            CommandHandler("quizrun", quiz_run_start),
            MessageHandler(filters.Regex("^🤖 Run Quiz$"), quiz_run_start),
        ],
        states={
            WAIT_QUIZ_SESSIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_sessions_and_run)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("campaign", campaign_cmd))
    app.add_handler(CommandHandler("accounts", accounts_cmd))
    app.add_handler(CommandHandler("login", login_start))
    app.add_handler(CommandHandler("watch", watch_cmd))
    app.add_handler(CommandHandler("quiz", quiz_status_cmd))
    app.add_handler(CommandHandler("setgroq", set_groq))
    app.add_handler(CommandHandler("mygroq", my_groq))
    app.add_handler(CommandHandler("logout", logout_cmd))
    app.add_handler(CallbackQueryHandler(account_callback, pattern=r"^(sw|rm):"))
    app.add_handler(login_conv)
    app.add_handler(quiz_conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_error_handler(error_handler)

    print("Bot starting...")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
