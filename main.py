#!/usr/bin/env python3
"""
MiniPix V2 Telegram Bot - Full Version
- Full Watch 4x Ladder
- Quiz with model rotation + heavy debug
- Sessions 10-25, Delay 10s fixed
- Per-user Groq key
- Log Channel
"""

import os
import json
import time
import re
import logging
import asyncio
from datetime import date
from typing import Dict, Optional, List, Any

import requests
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

# ───────────────────────── Config ─────────────────────────
API_BASE = "https://api.minipix.co/v4"
ACCOUNTS_FILE = "minipix_accounts.json"
USER_GROQ_FILE = "user_groq_keys.json"

MAX_WATCHES_PER_EP = 4
REWARDS_BY_WATCH = {1: 15, 2: 8, 3: 5, 4: 3}
QUIZ_QUESTION_DELAY = 10
DEFAULT_SESSIONS = 15
MIN_SESSIONS = 10
MAX_SESSIONS = 25

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
LOG_CHANNEL_ID = os.environ.get("LOG_CHANNEL_ID", "")
GLOBAL_GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

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
    level=logging.INFO
)
logger = logging.getLogger(__name__)

(WAIT_PHONE, WAIT_OTP, WAIT_TOKEN, WAIT_QUIZ_SESSIONS) = range(4)


# ───────────────────── Log Helper ─────────────────────
def send_log(text: str):
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
        logger.warning(f"Log send failed: {e}")


# ───────────────────── Groq Keys ─────────────────────
def load_user_keys() -> dict:
    if os.path.exists(USER_GROQ_FILE):
        try:
            with open(USER_GROQ_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_user_keys(data: dict):
    try:
        with open(USER_GROQ_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Save keys error: {e}")


user_groq_keys = load_user_keys()


def get_user_key(uid: int) -> Optional[str]:
    return user_groq_keys.get(str(uid)) or GLOBAL_GROQ_API_KEY or None


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
        self.current_model_index = 0

    # ---------- Account Management ----------
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
        if self.user_id and self.get_user():
            self._store_current_account(label)
            return True, f"Switched to {label}"
        return False, "Token expired"

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

    # ---------- HTTP ----------
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

    # ---------- Login ----------
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
                "blockWatching": cap.get("blockWatching", False),
            }
        return {"enabled": False, "cap": 0, "used": 0, "reached": False, "blockWatching": False}

    # ---------- Series Discovery ----------
    def _collect_series_deep(self, obj, out_dict):
        if obj is None:
            return
        if isinstance(obj, dict):
            if (obj.get("_id") or obj.get("id") or obj.get("series_id")) and (
                obj.get("title") or obj.get("numberOfEpisodes") is not None
                or obj.get("totalEpisodes") is not None or obj.get("cardImage")
                or obj.get("hindiTitle")
            ):
                sid = obj.get("_id") or obj.get("id") or obj.get("series_id")
                if sid and sid not in out_dict:
                    out_dict[sid] = obj
            for v in obj.values():
                self._collect_series_deep(v, out_dict)
        elif isinstance(obj, list):
            for item in obj:
                self._collect_series_deep(item, out_dict)

    def get_all_series(self, page_size=100, max_pages=12):
        found = {}
        try:
            sc, data = self._req("GET", "/short_search?page=home")
            if sc == 200 and isinstance(data, (dict, list)):
                self._collect_series_deep(data, found)
                if isinstance(data, dict) and data.get("playlists"):
                    for pl in data["playlists"]:
                        if isinstance(pl, dict) and isinstance(pl.get("webseries_details"), list):
                            for ws in pl["webseries_details"]:
                                if isinstance(ws, dict):
                                    sid = ws.get("_id") or ws.get("id")
                                    if sid and sid not in found:
                                        found[sid] = ws
        except Exception:
            pass

        endpoints = [
            ("GET", "/webseries?page={p}&pageSize={ps}"),
            ("GET", "/discover?type=webseries&page={p}&pageSize={ps}"),
            ("GET", "/home?page={p}&pageSize={ps}"),
            ("GET", "/discover/webseries?page={p}&pageSize={ps}"),
            ("GET", "/series?page={p}&pageSize={ps}"),
        ]
        for method, tmpl in endpoints:
            for page in range(1, max_pages + 1):
                url = tmpl.format(p=page, ps=page_size)
                try:
                    sc, data = self._req(method, url)
                except Exception:
                    continue
                if not (sc == 200 and isinstance(data, dict)):
                    continue
                self._collect_series_deep(data, found)
                series_candidates = []
                for k in ("webseries", "series", "data", "items", "results", "contents", "list"):
                    if isinstance(data.get(k), list):
                        series_candidates.extend(data[k])
                inner = data.get("data") if isinstance(data.get("data"), dict) else None
                if inner:
                    for k in ("webseries", "series", "items", "results", "contents", "list"):
                        if isinstance(inner.get(k), list):
                            series_candidates.extend(inner[k])
                for s in series_candidates:
                    if isinstance(s, dict):
                        sid = s.get("_id") or s.get("id") or s.get("series_id")
                        if sid and sid not in found:
                            found[sid] = s
                if len(series_candidates) < int(page_size * 0.4):
                    break

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
                if not isinstance(wh, dict):
                    continue
                key = (wh.get("id"), wh.get("episodeNo"))
                prev = self.watch_history.get(key) or {"watchedPct": 0, "time": 0}
                cur_pct = wh.get("watchedPct", 0) or 0
                if cur_pct >= (prev.get("watchedPct") or 0):
                    self.watch_history[key] = {"watchedPct": cur_pct, "time": wh.get("time", 0) or 0}
            return profile
        return None

    def get_watch_counts_from_profile(self):
        counts = {}
        try:
            self.get_profile()
        except Exception:
            pass
        raw = list(getattr(self, "watch_history_raw", []) or [])
        profile = getattr(self, "last_profile", {}) or {}
        if isinstance(profile, dict):
            extra = profile.get("watched") or profile.get("watchHistory") or []
            if isinstance(extra, list):
                raw.extend(extra)
        for item in raw:
            if not isinstance(item, dict):
                continue
            sid = item.get("id") or item.get("series_id")
            ep = item.get("episodeNo") or item.get("episode_no")
            pct = int(item.get("watchedPct") or item.get("progress") or 0)
            if sid and ep and pct >= 80:
                k = (str(sid), str(ep))
                counts[k] = counts.get(k, 0) + 1
        for k, c in getattr(self, "runtime_watch_counts", {}).items():
            counts[k] = max(counts.get(k, 0), c)
        return counts

    # ---------- Watch Core ----------
    def _update_watch_progress(self, series_id, series_title, hindi_title, episode_no,
                               tc_in_ms, tc_out_ms, detail_image, watched_pct):
        if not (self.user_id and self.profile_id):
            return False
        try:
            watched_pct = int(watched_pct or 0)
        except Exception:
            watched_pct = 0
        if not tc_in_ms:
            tc_in_ms = 0
        if not tc_out_ms or tc_out_ms <= tc_in_ms:
            tc_out_ms = tc_in_ms + 60000
        duration = tc_out_ms - tc_in_ms
        current_time_ms = tc_out_ms if watched_pct >= 100 else int(tc_in_ms + (duration * watched_pct / 100))
        stored_pct = 99 if watched_pct == 99 else (100 if watched_pct >= 100 else watched_pct)

        watch_obj = {
            "id": series_id,
            "title": series_title,
            "hindiTitle": hindi_title,
            "episodeNo": episode_no,
            "tcInMs": tc_in_ms,
            "tcOutMs": tc_out_ms,
            "detailImage": detail_image,
            "type": "episode",
            "progress": 100 if watched_pct >= 100 else watched_pct,
            "time": current_time_ms,
            "watchedPct": stored_pct,
            "campaign": False,
        }

        ok1 = False
        try:
            sc1, d1 = self._req(
                "PATCH", f"/users/{self.user_id}/profiles/{self.profile_id}",
                headers={"content-type": "application/json; charset=utf-8"},
                data=json.dumps({"watched": watch_obj}, ensure_ascii=False).encode("utf-8"),
            )
            ok1 = sc1 == 200 and isinstance(d1, dict) and d1.get("success")
        except Exception:
            pass

        ok2 = False
        for path in (
            f"/users/{self.user_id}/profiles/{self.profile_id}/watch-history/update",
            "/watch-history/update",
        ):
            try:
                sc2, d2 = self._req(
                    "POST", path,
                    headers={"content-type": "application/json; charset=utf-8"},
                    data=json.dumps({"watched": watch_obj, "campaign": False}, ensure_ascii=False).encode("utf-8"),
                )
                if sc2 and sc2 < 500 and (isinstance(d2, dict) and d2.get("success") or sc2 == 200):
                    ok2 = True
                    break
            except Exception:
                pass
        return ok1 or ok2

    def _report_watch_progress_to_coins(self, series_id, episode_no, watched_pct, series_title=""):
        bodies = [
            {
                "series_id": series_id, "episode_no": episode_no, "episodeNo": episode_no,
                "progress": watched_pct, "watchedPct": watched_pct, "campaign": False,
                "task_type": "watch_ladder",
            },
            {
                "type": "watch_ladder", "seriesId": series_id, "episode": str(episode_no),
                "watched": watched_pct, "campaign": False,
            },
        ]
        endpoints = [
            ("POST", "/coins/progress-report", bodies[0]),
            ("POST", "/coins/tasks/progress", bodies[0]),
            ("POST", "/coins/watch-progress", bodies[1]),
            ("POST", "/watch-ladder/progress", bodies[0]),
        ]
        for method, path, body in endpoints:
            try:
                sc, d = self._req(
                    method, path,
                    headers={"content-type": "application/json; charset=utf-8"},
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                )
                if sc and sc < 500 and isinstance(d, dict) and (d.get("success") is True or sc == 200):
                    return True
            except Exception:
                continue
        return False

    def _start_task_for_series(self, series_id):
        task_id = f"watch_ladder_{series_id}"
        candidates = [
            ("POST", f"/coins/tasks/{task_id}/start", {"series_id": series_id, "campaign": False}),
            ("POST", "/coins/tasks/start", {"task_id": task_id, "series_id": series_id, "campaign": False}),
            ("POST", "/watch-ladder/start", {"series_id": series_id, "campaign": False}),
        ]
        for method, path, body in candidates:
            try:
                sc, d = self._req(
                    method, path,
                    headers={"content-type": "application/json; charset=utf-8"},
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                )
                if sc and sc < 500:
                    return True
            except Exception:
                pass
        return False

    def claim_reward_task(self, task_id=None, series_id=None):
        if not task_id and series_id:
            task_id = f"watch_ladder_{series_id}"
        candidates = []
        if task_id:
            candidates.extend([
                ("POST", f"/coins/tasks/{task_id}/claim", None),
                ("POST", "/coins/tasks/claim", {"task_id": task_id, "campaign": False}),
            ])
        if series_id:
            candidates.extend([
                ("POST", "/watch-ladder/claim", {"series_id": series_id, "campaign": False}),
                ("POST", f"/coins/watch-ladder/{series_id}/claim", None),
            ])
        for entry in candidates:
            method, path = entry[0], entry[1]
            body = entry[2] if len(entry) > 2 else None
            try:
                sc, data = self._req(
                    method, path,
                    headers={"content-type": "application/json; charset=utf-8"},
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None,
                )
                if sc and sc < 500 and isinstance(data, dict) and data.get("success") is True:
                    return True
            except Exception:
                continue
        return False

    def watch_episode(self, episode, series_info, allow_repeat=True, nth_watch=None):
        if not isinstance(series_info, dict) or not isinstance(episode, dict):
            return False, "invalid"
        series_id = series_info.get("_id") or series_info.get("id") or series_info.get("series_id")
        if not series_id:
            return False, "no_series_id"
        ep_no = episode.get("episodeNo") or episode.get("episode_no") or episode.get("number") or 0
        series_title = series_info.get("title") or ""
        hindi_title = series_info.get("hindiTitle") or series_title
        detail_image = series_info.get("cardImage") or series_info.get("longVerticalImage") or ""

        try:
            tc_in = int(episode.get("tcIn") or 0)
        except Exception:
            tc_in = 0
        try:
            tc_out = int(episode.get("tcOut") or (tc_in + 60))
        except Exception:
            tc_out = tc_in + 60
        if tc_out <= tc_in:
            tc_out = tc_in + 60
        tc_in_ms = tc_in * 1000
        tc_out_ms = tc_out * 1000

        history_key = (series_id, ep_no)
        current_pct = int(self.watch_history.get(history_key, {}).get("watchedPct", 0) or 0)
        if not allow_repeat and current_pct >= 80:
            return True, "skip"

        for pct in [1, 50, 80, 99, 100]:
            self._update_watch_progress(
                series_id, series_title, hindi_title, ep_no,
                tc_in_ms, tc_out_ms, detail_image, pct,
            )
            if pct >= 80:
                self._report_watch_progress_to_coins(series_id, ep_no, pct, series_title)
            time.sleep(0.12)

        try:
            self.claim_reward_task(series_id=series_id)
        except Exception:
            pass

        self.watch_history[history_key] = {"watchedPct": 100, "time": tc_out_ms}
        rk = (str(series_id), str(ep_no))
        self.runtime_watch_counts[rk] = self.runtime_watch_counts.get(rk, 0) + 1
        return True, "done"

    def browse_and_watch_all_smart_repeat(self, progress_callback=None, max_watches=250, telegram_user_id=None):
        def log(msg):
            if progress_callback:
                progress_callback(msg)
            if any(x in msg for x in ["→", "Campaign", "Fetching", "Soft limit", "finished"]):
                send_log(f"<b>🎬 WATCH</b> | User <code>{telegram_user_id}</code>\n{msg}")

        log("Checking campaign...")
        cap = self.get_campaign_status()
        log(f"Campaign: {'ON' if cap['enabled'] else 'OFF'} | {cap['used']}/{cap['cap']}")

        log("Fetching series...")
        all_series = self.get_all_series()
        if not all_series:
            return {"error": "No series found"}

        try:
            self.get_profile()
        except Exception:
            pass
        watch_counts = self.get_watch_counts_from_profile()

        total_watched = total_skipped = total_failed = 0
        balance_before = self.get_balance_silent()

        for si, s in enumerate(all_series, 1):
            if total_watched >= max_watches:
                log("Soft limit reached")
                break
            sid = s.get("_id") or s.get("id") or s.get("series_id")
            if not sid:
                continue
            title = s.get("title") or "?"
            episodes, _ = self.get_episodes(sid, page=1, page_size=500)
            if not episodes:
                continue
            episodes = sorted(
                episodes,
                key=lambda e: int(e.get("episodeNo") or 0) if str(e.get("episodeNo") or "").isdigit() else 0,
            )
            try:
                self._start_task_for_series(sid)
            except Exception:
                pass

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
                ok, st = self.watch_episode(ep, s, allow_repeat=True, nth_watch=cnt + 1)
                if st == "skip":
                    total_skipped += 1
                elif ok:
                    done += 1
                    total_watched += 1
                else:
                    total_failed += 1
            try:
                self.claim_reward_task(series_id=sid)
            except Exception:
                pass
            if done:
                log(f"  → {done} watches")

        bal_end = self.get_balance_silent()
        delta = None
        if balance_before is not None and bal_end is not None:
            delta = bal_end - balance_before

        summary = (
            f"<b>🏁 WATCH FINISHED</b>\n"
            f"User: <code>{telegram_user_id}</code>\n"
            f"Watched: {total_watched} | Skipped: {total_skipped} | Failed: {total_failed}\n"
        )
        if delta is not None:
            summary += f"Balance: {balance_before} → {bal_end} ({delta:+d})"
        send_log(summary)

        return {
            "watched": total_watched,
            "skipped": total_skipped,
            "failed": total_failed,
            "balance_before": balance_before,
            "balance_after": bal_end,
            "delta": delta,
        }

    # ---------- Quiz ----------
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
        send_log(f"<b>DEBUG start_session</b>\nHTTP {sc}\n<code>{str(data)[:550]}</code>")
        if sc == 200 and isinstance(data, dict) and data.get("success"):
            session_obj = data.get("session") or {}
            question_obj = data.get("question")
            sid = session_obj.get("sessionId") or data.get("sessionId") or data.get("_id")
            if sid and question_obj:
                return sid, question_obj, session_obj
        return None, None, None

    def quiz_submit_answer(self, session_id, question_id, chosen_index):
        payload = {
            "sessionId": session_id,
            "questionId": question_id,
            "chosenIndex": chosen_index,
        }
        sc, data = self._req(
            "POST", "/quiz/session/answer",
            headers={"content-type": "application/json; charset=utf-8"},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        send_log(f"<b>DEBUG submit</b> chosen={chosen_index}\nHTTP {sc}\n<code>{str(data)[:650]}</code>")
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

    def ask_groq(self, question, options, telegram_user_id=None):
        api_key = get_user_key(telegram_user_id) if telegram_user_id else GLOBAL_GROQ_API_KEY
        if not api_key:
            send_log(f"❌ No Groq key for user <code>{telegram_user_id}</code>")
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
                            "content": "You are a quiz solver. Reply with ONLY a single integer (0, 1, 2 or 3). No explanation."
                        },
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.0,
                    max_tokens=10,
                )
                answer_text = (completion.choices[0].message.content or "").strip()
                idx = self._parse_quiz_answer(answer_text, options)

                send_log(
                    f"<b>🤖 Model:</b> <code>{model}</code>\n"
                    f"<b>Raw:</b> <code>{answer_text}</code>\n"
                    f"<b>Parsed:</b> {idx}"
                )

                if idx is not None:
                    self.current_model_index = model_idx
                    return idx

            except Exception as e:
                err = str(e)
                send_log(f"⚠️ <code>{model}</code> failed:\n<code>{err[:280]}</code>")
                if any(x in err.lower() for x in ["rate", "limit", "quota", "429", "too many"]):
                    self.current_model_index = (model_idx + 1) % len(GROQ_MODELS)
                continue

        send_log("❌ All models failed for this question")
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

        send_log(
            f"<b>🧠 QUIZ STARTED</b>\n"
            f"User: <code>{telegram_user_id}</code>\n"
            f"Sessions: {max_sessions} | Delay: {question_delay}s"
        )

        for session_num in range(1, max_sessions + 1):
            log(f"Session {session_num}/{max_sessions} starting...")
            session_id, question_obj, session_meta = self.quiz_start_session()
            if not session_id or not question_obj:
                log("Session start failed")
                time.sleep(2)
                continue

            hearts = session_meta.get("hearts", 3) if session_meta else 3
            ad_every = session_meta.get("adGateEvery", 5) if session_meta else 5
            q_count = 0
            session_correct = 0
            session_coins = 0

            while hearts > 0 and q_count < 30:
                if not question_obj or not isinstance(question_obj, dict):
                    break

                q_id = question_obj.get("questionId")
                q_text_hi = question_obj.get("questionHi") or ""
                q_text_en = question_obj.get("questionEn") or ""
                options = question_obj.get("options", [])
                q_idx = question_obj.get("index", q_count)
                q_total = question_obj.get("total", "?")

                if not q_id or not isinstance(options, list) or len(options) < 2:
                    send_log("⚠️ Invalid question data")
                    break

                q_count += 1
                grand_total_q += 1
                combined = q_text_hi or q_text_en
                if q_text_en and q_text_hi and q_text_en != q_text_hi:
                    combined = f"{q_text_hi}\n[EN: {q_text_en}]"

                log(f"S{session_num} Q{q_count}: {(q_text_hi or q_text_en)[:55]}...")

                correct_index = self.ask_groq(combined, options, telegram_user_id=telegram_user_id)
                if correct_index is None:
                    correct_index = 0
                    send_log("⚠️ Model failed → using 0")

                correct_index = max(0, min(correct_index, len(options) - 1))
                chosen_text = options[correct_index]

                time.sleep(question_delay)

                result = self.quiz_submit_answer(session_id, q_id, correct_index)
                if not result or not result.get("success"):
                    send_log("❌ Submit failed, ending session")
                    break

                correct_flag = result.get("correct", False)
                coins_earned = int(result.get("coinsEarned") or 0)
                session_coins = result.get("coinsSoFar", session_coins)
                hearts = int(result.get("hearts", hearts))
                total_coins += coins_earned
                server_correct = result.get("correctIndex")

                if correct_flag:
                    session_correct += 1
                    grand_correct += 1

                # Detailed question log
                opts_txt = "\n".join(f"{i}. {opt}" for i, opt in enumerate(options))
                log_msg = (
                    f"<b>Q{q_count}</b> | Session {session_num}\n"
                    f"<b>Question:</b>\n{q_text_hi or q_text_en}\n\n"
                    f"<b>Options:</b>\n{opts_txt}\n\n"
                    f"<b>Model chose:</b> [{correct_index}] {chosen_text}\n"
                    f"<b>Result:</b> {'✅ CORRECT' if correct_flag else '❌ WRONG'}\n"
                )
                if not correct_flag and server_correct is not None:
                    try:
                        log_msg += f"<b>Correct was:</b> [{server_correct}] {options[server_correct]}\n"
                    except Exception:
                        pass
                log_msg += f"Coins +{coins_earned} | Hearts {hearts}"
                send_log(log_msg)

                # Next
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

                if ad_every > 0 and (q_count % ad_every == 0):
                    nq = self.quiz_ad_ack(session_id)
                    if nq:
                        question_obj = nq
                        continue
                    break
                break

            sessions_done += 1
            send_log(
                f"<b>📊 Session {session_num} Summary</b>\n"
                f"User: <code>{telegram_user_id}</code>\n"
                f"Questions: {q_count}\n"
                f"Correct: {session_correct}\n"
                f"Coins: {session_coins}\n"
                f"Hearts left: {hearts}"
            )
            log(f"Session {session_num} → {session_correct}/{q_count} correct")
            if session_num < max_sessions:
                time.sleep(2)

        final = (
            f"<b>🏁 QUIZ FINISHED</b>\n"
            f"User: <code>{telegram_user_id}</code>\n"
            f"Sessions: {sessions_done}\n"
            f"Total Questions: {grand_total_q}\n"
            f"Total Correct: {grand_correct}\n"
            f"Coins earned: ~{total_coins}\n"
            f"Balance now: {self.get_balance()}"
        )
        send_log(final)

        return {
            "sessions": sessions_done,
            "total_questions": grand_total_q,
            "total_correct": grand_correct,
            "total_coins": total_coins,
            "balance": self.get_balance(),
        }


# ───────────────────── Bot Layer ─────────────────────
user_bots: Dict[int, MiniPixV2] = {}


def get_bot(uid: int) -> MiniPixV2:
    if uid not in user_bots:
        user_bots[uid] = MiniPixV2()
    return user_bots[uid]


def main_menu():
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


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    b = get_bot(update.effective_user.id)
    txt = f"👋 Hi {update.effective_user.first_name}!\n\nMiniPix Bot ready."
    if b.access_token:
        txt += f"\n✅ {b.current_account_label or b.phone}"
    else:
        txt += "\n⚠️ Not logged in → /login"
    await update.message.reply_text(txt, reply_markup=main_menu())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 /setgroq gsk_xxx\n/login\n/watch\n/quiz\n/logout",
        reply_markup=main_menu(),
    )


async def cmd_setgroq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/setgroq gsk_your_key`", parse_mode="Markdown")
        return
    key = context.args[0].strip()
    if not key.startswith("gsk_"):
        await update.message.reply_text("Key must start with gsk_")
        return
    user_groq_keys[str(update.effective_user.id)] = key
    save_user_keys(user_groq_keys)
    await update.message.reply_text("✅ Groq key saved")


async def cmd_mygroq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    k = get_user_key(update.effective_user.id)
    if k:
        await update.message.reply_text(f"✅ `{k[:12]}...{k[-4:]}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ No key set")


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    b = get_bot(update.effective_user.id)
    if not b.access_token:
        await update.message.reply_text("Not logged in")
        return
    await update.message.reply_text(f"💰 Balance: *{b.get_balance()}*", parse_mode="Markdown")


async def cmd_campaign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    b = get_bot(update.effective_user.id)
    if not b.access_token:
        await update.message.reply_text("Not logged in")
        return
    s = b.get_campaign_status()
    await update.message.reply_text(
        f"🎥 {'ON' if s['enabled'] else 'OFF'}\nCap: {s['used']}/{s['cap']}"
    )


async def cmd_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    b = get_bot(update.effective_user.id)
    accs = b.list_accounts()
    if not accs:
        await update.message.reply_text("No saved accounts")
        return
    lines = [f"👥 Accounts ({len(accs)})"]
    kb = []
    for a in accs:
        lines.append(f"• {a}")
        kb.append([
            InlineKeyboardButton(f"Switch {a}", callback_data=f"sw:{a}"),
            InlineKeyboardButton("❌", callback_data=f"rm:{a}"),
        ])
    await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))


async def account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    b = get_bot(q.from_user.id)
    data = q.data
    if data.startswith("sw:"):
        ok, msg = b.switch_account(data[3:])
        if ok:
            b.open_app()
            await q.edit_message_text(f"✅ {msg}\nBalance: {b.get_balance()}")
        else:
            await q.edit_message_text(f"❌ {msg}")
    elif data.startswith("rm:"):
        if b.remove_account(data[3:]):
            await q.edit_message_text("Removed")
        else:
            await q.edit_message_text("Failed")


async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📱 Phone + OTP", callback_data="login:otp")],
        [InlineKeyboardButton("🔑 Bearer Token", callback_data="login:token")],
    ]
    await update.message.reply_text("Choose login:", reply_markup=InlineKeyboardMarkup(kb))


async def login_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "login:otp":
        await q.edit_message_text("Phone number bhejo:")
        return WAIT_PHONE
    if q.data == "login:token":
        await q.edit_message_text("Bearer token bhejo:")
        return WAIT_TOKEN
    return ConversationHandler.END


async def login_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith("+"):
        phone = "+91" + phone.lstrip("0")
    b = get_bot(update.effective_user.id)
    st = b.login_otp_generate(phone)
    if not st:
        await update.message.reply_text("OTP generate fail")
        return ConversationHandler.END
    context.user_data["session_token"] = st
    await update.message.reply_text("OTP bhejo:")
    return WAIT_OTP


async def login_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    otp = update.message.text.strip()
    b = get_bot(update.effective_user.id)
    st = context.user_data.get("session_token")
    if not st:
        await update.message.reply_text("Session lost")
        return ConversationHandler.END
    if b.login_otp_verify(st, otp):
        b.open_app()
        await update.message.reply_text(
            f"✅ Login success\nBalance: {b.get_balance()}",
            reply_markup=main_menu(),
        )
        send_log(f"✅ Login | User <code>{update.effective_user.id}</code>")
    else:
        await update.message.reply_text("❌ OTP wrong")
    return ConversationHandler.END


async def login_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = update.message.text.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    b = get_bot(update.effective_user.id)
    if b.login_with_token(token):
        b.open_app()
        await update.message.reply_text(
            f"✅ Login success\nBalance: {b.get_balance()}",
            reply_markup=main_menu(),
        )
    else:
        await update.message.reply_text("❌ Invalid token")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled", reply_markup=main_menu())
    return ConversationHandler.END


async def watch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    b = get_bot(update.effective_user.id)
    if not b.access_token:
        await update.message.reply_text("Not logged in")
        return
    uid = update.effective_user.id
    msg = await update.message.reply_text("🚀 Starting 4x watch...")

    def progress(text):
        try:
            asyncio.get_event_loop().create_task(msg.edit_text(f"🚀 {text[-850:]}"))
        except Exception:
            pass

    result = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: b.browse_and_watch_all_smart_repeat(
            progress_callback=progress, max_watches=250, telegram_user_id=uid
        ),
    )
    if "error" in result:
        await msg.edit_text(f"❌ {result['error']}")
        return
    text = (
        f"🏁 Watch Done\n"
        f"Watched: {result['watched']}\n"
        f"Skipped: {result['skipped']}\n"
        f"Failed: {result['failed']}"
    )
    if result.get("delta") is not None:
        text += f"\n💰 {result['balance_before']} → {result['balance_after']} ({result['delta']:+d})"
    await msg.edit_text(text)


async def quiz_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    b = get_bot(update.effective_user.id)
    if not b.access_token:
        await update.message.reply_text("Not logged in")
        return
    data = b.get_quiz_status()
    if not data:
        await update.message.reply_text("Failed to get status")
        return
    daily = data.get("dailyAttempts", {}) or {}
    await update.message.reply_text(
        f"🧠 Level: {data.get('currentLevel')}\n"
        f"Daily: {daily.get('used')}/{daily.get('limit')}"
    )


async def quiz_run_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    b = get_bot(update.effective_user.id)
    if not b.access_token:
        await update.message.reply_text("Not logged in")
        return ConversationHandler.END
    if not get_user_key(update.effective_user.id):
        await update.message.reply_text("❌ Pehle /setgroq se key set karo")
        return ConversationHandler.END
    await update.message.reply_text(
        f"Kitne sessions? ({MIN_SESSIONS}-{MAX_SESSIONS}, default {DEFAULT_SESSIONS}):"
    )
    return WAIT_QUIZ_SESSIONS


async def quiz_sessions_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        n = int(update.message.text.strip() or str(DEFAULT_SESSIONS))
        n = max(MIN_SESSIONS, min(MAX_SESSIONS, n))
    except Exception:
        n = DEFAULT_SESSIONS

    b = get_bot(update.effective_user.id)
    uid = update.effective_user.id
    msg = await update.message.reply_text(f"🤖 Running {n} sessions (delay 10s)...")

    def progress(text):
        try:
            asyncio.get_event_loop().create_task(msg.edit_text(f"🤖 {text[-900:]}"))
        except Exception:
            pass

    result = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: b.run_quiz_auto(
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
    await update.message.reply_text("Logged out", reply_markup=main_menu())


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    mapping = {
        "💰 Balance": cmd_balance,
        "📊 Campaign": cmd_campaign,
        "👥 Accounts": cmd_accounts,
        "➕ Login": login_start,
        "🎬 Watch All (4x)": watch_cmd,
        "🧠 Quiz Status": quiz_status_cmd,
        "🤖 Run Quiz": quiz_run_start,
        "🔑 Set Groq Key": lambda u, c: u.message.reply_text("Use: /setgroq gsk_xxx"),
        "ℹ️ Help": cmd_help,
    }
    handler = mapping.get(text)
    if handler:
        return await handler(update, context)
    await update.message.reply_text("Use the menu buttons")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception:", exc_info=context.error)
    if "Conflict" in str(context.error):
        logger.error("CONFLICT: Multiple bot instances. Keep only ONE on Railway.")


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set")
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
            WAIT_QUIZ_SESSIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_sessions_run)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("campaign", cmd_campaign))
    app.add_handler(CommandHandler("accounts", cmd_accounts))
    app.add_handler(CommandHandler("login", login_start))
    app.add_handler(CommandHandler("watch", watch_cmd))
    app.add_handler(CommandHandler("quiz", quiz_status_cmd))
    app.add_handler(CommandHandler("setgroq", cmd_setgroq))
    app.add_handler(CommandHandler("mygroq", cmd_mygroq))
    app.add_handler(CommandHandler("logout", logout_cmd))
    app.add_handler(CallbackQueryHandler(account_callback, pattern=r"^(sw|rm):"))
    app.add_handler(login_conv)
    app.add_handler(quiz_conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_error_handler(error_handler)

    print("Bot starting...")
    if LOG_CHANNEL_ID:
        print(f"Log channel: {LOG_CHANNEL_ID}")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
