#!/usr/bin/env python3
"""
MiniPix V2 → Telegram Bot
Requires:
  pip install python-telegram-bot==21.6 requests groq
  export TELEGRAM_BOT_TOKEN="your_bot_token"
  export GROQ_API_KEY="your_groq_key"
"""

import os
import json
import time
import re
import logging
from datetime import date
from typing import Optional, Dict, Any

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
ACCOUNTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "minipix_accounts.json")

MAX_WATCHES_PER_EP = 4
REWARDS_BY_WATCH = {1: 15, 2: 8, 3: 5, 4: 3}
QUIZ_QUESTION_DELAY = 8

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = "openai/gpt-oss-120b"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

HEADERS_BASE = {
    "user-agent": "okhttp/4.12.0",
    "accept-encoding": "gzip",
}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Conversation states
(
    WAIT_PHONE,
    WAIT_OTP,
    WAIT_TOKEN,
    WAIT_LABEL,
    WAIT_QUIZ_SESSIONS,
    WAIT_QUIZ_DELAY,
) = range(6)


def _expected_reward(nth_watch):
    return REWARDS_BY_WATCH.get(int(nth_watch) if nth_watch else 1, 0)


# ───────────────────── MiniPix Core (same logic) ─────────────────────
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
        lbl = label or self.phone or self.current_account_label or f"acc_{self.user_id[-6:]}"
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
            "POST",
            "/login/generate-otp",
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
            "POST",
            "/login/verify-otp",
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

    def _collect_series_deep(self, obj, out_dict):
        if obj is None:
            return
        if isinstance(obj, dict):
            if (obj.get("_id") or obj.get("id") or obj.get("series_id")) and (
                obj.get("title")
                or obj.get("numberOfEpisodes") is not None
                or obj.get("totalEpisodes") is not None
                or obj.get("cardImage")
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

    def get_all_series(self, page_size=100, max_pages=15):
        found = {}
        home_urls = [("GET", "/short_search?page=home", False)]
        for method, tmpl, _ in home_urls:
            try:
                sc, data = self._req(method, tmpl)
            except Exception:
                sc, data = 0, None
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

        endpoints = [
            ("GET", "/webseries?page={p}&pageSize={ps}", True),
            ("GET", "/discover?type=webseries&page={p}&pageSize={ps}", True),
            ("GET", "/home?page={p}&pageSize={ps}", False),
            ("GET", "/discover/webseries?page={p}&pageSize={ps}", True),
            ("GET", "/series?page={p}&pageSize={ps}", True),
        ]
        for method, tmpl, is_series_list in endpoints:
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
                inner = None
                if isinstance(data.get("data"), dict):
                    inner = data["data"]
                elif isinstance(data.get("response"), dict):
                    inner = data["response"]
                if inner:
                    for k in ("webseries", "series", "items", "results", "contents", "list", "data"):
                        if isinstance(inner.get(k), list):
                            series_candidates.extend(inner[k])
                if not series_candidates:
                    if is_series_list and isinstance(data, dict) and (data.get("_id") or data.get("title")):
                        series_candidates = [data]
                if not series_candidates:
                    break
                for s in series_candidates:
                    if not isinstance(s, dict):
                        continue
                    sid = s.get("_id") or s.get("id") or s.get("series_id")
                    if not sid:
                        continue
                    if sid not in found:
                        found[sid] = s
                total_reported = data.get("total") or (inner or {}).get("total") or 0
                if total_reported and len(found) >= int(total_reported):
                    break
                if len(series_candidates) < int(page_size * 0.5):
                    break
        series_list = list(found.values())

        def _sort_key(s):
            try:
                return -int(s.get("numberOfEpisodes") or s.get("totalEpisodes") or 0)
            except Exception:
                return 0

        series_list.sort(key=_sort_key)
        return series_list

    def get_episodes(self, series_id, page=1, page_size=50):
        sc, data = self._req(
            "GET",
            f"/episodes?series_id={series_id}&page={page}&pageSize={page_size}",
        )
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
                cur_time = wh.get("time", 0) or 0
                if cur_pct >= (prev.get("watchedPct") or 0):
                    self.watch_history[key] = {"watchedPct": cur_pct, "time": cur_time}
            return profile
        return None

    def get_watch_counts_from_profile(self):
        counts = {}
        raw_history = []
        try:
            self.get_profile()
        except Exception:
            pass
        profile = getattr(self, "last_profile", None) or {}
        if isinstance(profile, dict):
            watched_list = profile.get("watched") or profile.get("watchHistory") or []
            if isinstance(watched_list, list):
                raw_history = watched_list
        if isinstance(getattr(self, "watch_history_raw", None), list):
            raw_history = raw_history + self.watch_history_raw
        for item in raw_history:
            if not isinstance(item, dict):
                continue
            sid = item.get("id") or item.get("series_id")
            ep = item.get("episodeNo") or item.get("episode_no")
            pct = int(item.get("watchedPct") or item.get("progress") or 0)
            if sid and ep and pct >= 80:
                k = (str(sid), str(ep))
                counts[k] = counts.get(k, 0) + 1
        if isinstance(self.watch_history, dict):
            for (sid, ep_no), info in self.watch_history.items():
                pct = int(info.get("watchedPct") or 0) if isinstance(info, dict) else 0
                if pct >= 80:
                    k = (str(sid), str(ep_no))
                    if counts.get(k, 0) < 1:
                        counts[k] = max(counts.get(k, 0), 1)
        runtime = getattr(self, "runtime_watch_counts", None)
        if isinstance(runtime, dict):
            for k, c in runtime.items():
                counts[k] = max(counts.get(k, 0), c)
        return counts

    def _update_watch_progress(
        self, series_id, series_title, hindi_title, episode_no,
        tc_in_ms, tc_out_ms, detail_image, watched_pct,
    ):
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
        if watched_pct >= 100:
            current_time_ms = tc_out_ms
        else:
            current_time_ms = int(tc_in_ms + (duration * watched_pct / 100))
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
            payload_patch = {"watched": watch_obj}
            sc1, d1 = self._req(
                "PATCH",
                f"/users/{self.user_id}/profiles/{self.profile_id}",
                headers={"content-type": "application/json; charset=utf-8"},
                data=json.dumps(payload_patch, ensure_ascii=False).encode("utf-8"),
            )
            ok1 = sc1 == 200 and isinstance(d1, dict) and d1.get("success")
        except Exception:
            pass

        ok2 = False
        try:
            for path in (
                f"/users/{self.user_id}/profiles/{self.profile_id}/watch-history/update",
                "/watch-history/update",
                f"/profiles/{self.profile_id}/watch-history/update",
            ):
                payload_wh = {"watched": watch_obj, "campaign": False}
                sc2, d2 = self._req(
                    "POST", path,
                    headers={"content-type": "application/json; charset=utf-8"},
                    data=json.dumps(payload_wh, ensure_ascii=False).encode("utf-8"),
                )
                if sc2 and sc2 < 500:
                    if isinstance(d2, dict) and d2.get("success"):
                        ok2 = True
                        break
                    if sc2 == 200:
                        ok2 = True
                        break
        except Exception:
            pass
        return ok1 or ok2

    def _report_watch_progress_to_coins(self, series_id, episode_no, watched_pct, series_title=""):
        if not (self.user_id and self.profile_id):
            return False
        try:
            watched_pct = int(watched_pct or 0)
        except Exception:
            watched_pct = 0
        ep_str = str(episode_no)
        bodies = [
            {
                "series_id": series_id,
                "episode_no": episode_no,
                "episodeNo": episode_no,
                "progress": watched_pct,
                "watchedPct": watched_pct,
                "campaign": False,
                "task_type": "watch_ladder",
            },
            {
                "type": "watch_ladder",
                "seriesId": series_id,
                "episode": ep_str,
                "watched": watched_pct,
                "campaign": False,
            },
            {
                "task_id": f"watch_ladder_{series_id}",
                "progress_delta": 1,
                "series_id": series_id,
                "episode_no": episode_no,
                "campaign": False,
            },
        ]
        endpoints = [
            ("POST", "/coins/progress-report", bodies[0]),
            ("POST", "/coins/tasks/progress", bodies[0]),
            ("POST", f"/coins/tasks/watch_ladder_{series_id}/progress", bodies[0]),
            ("POST", "/coins/watch-progress", bodies[1]),
            ("POST", "/coins/report-watched", bodies[1]),
            ("POST", "/coins/tasks/update", bodies[2]),
            ("POST", "/watch-ladder/progress", bodies[0]),
        ]
        any_ok = False
        for method, path, body in endpoints:
            try:
                sc, d = self._req(
                    method, path,
                    headers={"content-type": "application/json; charset=utf-8"},
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                )
                if sc and sc < 500 and isinstance(d, dict):
                    if d.get("success") is True or (sc == 200 and "success" not in d):
                        any_ok = True
                        break
            except Exception:
                continue
        return any_ok

    def _start_task_for_series(self, series_id):
        task_id = f"watch_ladder_{series_id}"
        candidates = [
            ("POST", f"/coins/tasks/{task_id}/start", {"series_id": series_id, "campaign": False}),
            ("POST", "/coins/tasks/start", {"task_id": task_id, "series_id": series_id, "campaign": False}),
            ("POST", "/watch-ladder/start", {"series_id": series_id, "campaign": False}),
            ("POST", "/coins/start-task", {"task_id": task_id, "campaign": False}),
            ("POST", f"/coins/tasks/watch_ladder_{series_id}/resume", {}),
        ]
        any_ok = False
        for method, path, body in candidates:
            try:
                sc, d = self._req(
                    method, path,
                    headers={"content-type": "application/json; charset=utf-8"},
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                )
                if sc and sc < 500:
                    if isinstance(d, dict) and d.get("success") is True:
                        any_ok = True
                        break
                    if sc == 200:
                        any_ok = True
                        break
            except Exception:
                pass
        return any_ok

    def claim_reward_task(self, task_id=None, series_id=None):
        if not task_id and series_id:
            task_id = f"watch_ladder_{series_id}"
        if series_id:
            try:
                self._report_watch_progress_to_coins(series_id, 0, 100)
            except Exception:
                pass
        candidates = []
        if task_id:
            candidates.extend([
                ("POST", f"/coins/tasks/{task_id}/claim", None),
                ("POST", "/coins/tasks/claim", {"task_id": task_id, "campaign": False}),
                ("POST", f"/coins/tasks/{task_id}/reward", None),
                ("POST", f"/coins/tasks/{task_id}/complete", {}),
                ("POST", "/coins/tasks/complete", {"task_id": task_id, "campaign": False}),
            ])
        if series_id:
            candidates.extend([
                ("POST", "/watch-ladder/claim", {"series_id": series_id, "campaign": False}),
                ("POST", f"/coins/watch-ladder/{series_id}/claim", None),
                ("POST", "/coins/claim", {"series_id": series_id, "type": "watch_ladder", "campaign": False}),
                ("POST", "/coins/redeem", {"series_id": series_id, "task": "watch_ladder", "campaign": False}),
                ("POST", f"/watch-ladder/{series_id}/complete", {"campaign": False}),
            ])
        any_ok = False
        for entry in candidates:
            method, path = entry[0], entry[1]
            body = entry[2] if len(entry) > 2 else None
            try:
                sc, data = self._req(
                    method, path,
                    headers={"content-type": "application/json; charset=utf-8"},
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None,
                )
                if sc and sc < 500 and isinstance(data, dict):
                    if data.get("success") is True:
                        any_ok = True
                        break
                    if sc == 200 and "success" not in data:
                        continue
            except Exception:
                continue
        return any_ok

    def watch_episode(
        self, episode, series_info, delay_multiplier=0.0,
        min_watch_pct=80, allow_repeat=False, nth_watch=None,
    ):
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
        if not allow_repeat and current_pct >= min_watch_pct:
            return True, "skip"

        progress_steps = [1, 50, 80, 99, 100, 100]
        any_fail = False
        reported_coin_progress = False
        for pct in progress_steps:
            if not allow_repeat and pct < current_pct:
                continue
            ok = self._update_watch_progress(
                series_id, series_title, hindi_title, ep_no,
                tc_in_ms, tc_out_ms, detail_image, pct,
            )
            if not ok:
                any_fail = True
            if pct >= 80 and not reported_coin_progress:
                try:
                    self._report_watch_progress_to_coins(series_id, ep_no, pct, series_title)
                    reported_coin_progress = True
                except Exception:
                    pass
            time.sleep(0.12)
        if not reported_coin_progress:
            try:
                self._report_watch_progress_to_coins(series_id, ep_no, 100, series_title)
            except Exception:
                pass
        try:
            self.claim_reward_task(series_id=series_id, task_id=None)
        except Exception:
            pass
        time.sleep(0.4)
        self.watch_history[history_key] = {"watchedPct": 100, "time": tc_out_ms}
        rk = (str(series_id), str(ep_no))
        self.runtime_watch_counts[rk] = self.runtime_watch_counts.get(rk, 0) + 1
        self.watch_history_raw.append({
            "id": series_id, "series_id": series_id,
            "episodeNo": ep_no, "episode_no": ep_no,
            "watchedPct": 100, "progress": 100, "time": tc_out_ms,
        })
        return True, "done"

    def browse_and_watch_all_smart_repeat(self, progress_callback=None, max_watches=300):
        """Returns summary dict. progress_callback(msg) is optional for Telegram updates."""
        def log(msg):
            if progress_callback:
                progress_callback(msg)

        log("Checking campaign status...")
        cap_status = self.get_campaign_status()
        log(f"Campaign: {'ON' if cap_status['enabled'] else 'OFF'} | Cap {cap_status['used']}/{cap_status['cap']}")

        log("Fetching all series...")
        all_series = self.get_all_series()
        if not all_series:
            return {"error": "No series found"}

        try:
            self.get_profile()
        except Exception:
            pass
        watch_counts = self.get_watch_counts_from_profile()
        total_watched_all = 0
        total_skipped = 0
        total_failed = 0
        balance_before = self.get_balance_silent()

        for si, s in enumerate(all_series, 1):
            sid = s.get("_id") or s.get("id") or s.get("series_id")
            if not sid:
                continue
            series_title = s.get("title") or "?"
            episodes, _ = self.get_episodes(sid, page=1, page_size=500)
            if not episodes:
                continue
            episodes_sorted = sorted(
                episodes,
                key=lambda e: int(e.get("episodeNo") or 0) if str(e.get("episodeNo") or "").isdigit() else 0,
            )
            try:
                self._start_task_for_series(sid)
            except Exception:
                pass

            log(f"[{si}/{len(all_series)}] {series_title} ({len(episodes_sorted)} eps)")
            done_this = 0
            for ep in episodes_sorted:
                if total_watched_all >= max_watches:
                    log("Soft limit reached, stopping.")
                    break
                ep_no = ep.get("episodeNo")
                kp = (str(sid), str(ep_no))
                cnt = watch_counts.get(kp, 0) + self.runtime_watch_counts.get(kp, 0)
                if cnt >= MAX_WATCHES_PER_EP:
                    continue
                nth = cnt + 1
                ok, st = self.watch_episode(
                    ep, s, delay_multiplier=0.0,
                    min_watch_pct=80, allow_repeat=True, nth_watch=nth,
                )
                if st == "skip":
                    total_skipped += 1
                elif ok:
                    done_this += 1
                    total_watched_all += 1
                else:
                    total_failed += 1
            try:
                self.claim_reward_task(series_id=sid)
            except Exception:
                pass
            if done_this:
                log(f"  → {done_this} watches done for this series")

        bal_end = self.get_balance_silent()
        delta = (bal_end or 0) - (balance_before or 0) if bal_end is not None and balance_before is not None else None
        return {
            "watched": total_watched_all,
            "skipped": total_skipped,
            "failed": total_failed,
            "balance_before": balance_before,
            "balance_after": bal_end,
            "delta": delta,
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
            "Solve this multiple-choice question. "
            "Return ONLY the integer index of the correct option (0, 1, 2, ...). "
            "No explanation, no extra words, just a single digit integer.\n\n"
            f"Question (both Hindi and English provided):\n{question}\n\nOptions:\n"
        )
        for i, opt in enumerate(options):
            prompt += f"  {i}: {opt}\n"
        prompt += "\nCorrect option index (integer only): "
        return prompt

    def _parse_quiz_answer(self, answer_text, options):
        if not answer_text:
            return None
        t = answer_text.strip()
        m = re.search(r"\b(\d+)\b", t)
        if m:
            idx = int(m.group(1))
            if 0 <= idx < len(options):
                return idx
        for i, opt in enumerate(options):
            opt_clean = str(opt).strip().lower()
            if opt_clean and opt_clean in t.lower():
                return i
        m2 = re.search(r"option\s*(\d+)", t, flags=re.IGNORECASE)
        if m2:
            idx = int(m2.group(1))
            if 1 <= idx <= len(options):
                return idx - 1
        return None

    def ask_groq(self, question, options):
        if not GROQ_API_KEY:
            return None
        prompt = self._build_quiz_prompt(question, options)
        try:
            from groq import Groq
            client = Groq(api_key=GROQ_API_KEY)
            completion = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_completion_tokens=64,
                top_p=1,
                stream=False,
            )
            answer_text = (completion.choices[0].message.content or "").strip()
            return self._parse_quiz_answer(answer_text, options)
        except Exception as e:
            logger.warning(f"Groq error: {e}")
            # fallback HTTP
            try:
                url = "https://api.groq.com/openai/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": GROQ_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "max_completion_tokens": 64,
                }
                r = requests.post(url, json=payload, headers=headers, timeout=40)
                if r.status_code == 200:
                    data = r.json()
                    answer_text = data["choices"][0]["message"]["content"].strip()
                    return self._parse_quiz_answer(answer_text, options)
            except Exception:
                pass
            return None

    def run_quiz_auto(self, max_sessions=3, question_delay=None, progress_callback=None):
        delay = question_delay if question_delay is not None else QUIZ_QUESTION_DELAY

        def log(msg):
            if progress_callback:
                progress_callback(msg)

        status = self.get_quiz_status()
        if not status:
            return {"error": "Could not fetch quiz status"}
        daily = status.get("dailyAttempts", {}) or {}
        if daily.get("exhausted"):
            return {"error": "Daily quiz attempts exhausted"}

        total_coins_run = 0
        sessions_done = 0

        for session_num in range(1, max_sessions + 1):
            log(f"--- Quiz Session {session_num}/{max_sessions} ---")
            session_id, question_obj, session_meta = self.quiz_start_session()
            if not session_id or not question_obj:
                log("Session start failed")
                break

            session_coins = 0
            q_count = 0
            hearts = session_meta.get("hearts", 3) if session_meta else 3
            ad_every = session_meta.get("adGateEvery", 5) if session_meta else 5

            while True:
                if hearts <= 0:
                    log("No hearts left")
                    break
                if not question_obj or not isinstance(question_obj, dict):
                    break

                q_id = question_obj.get("questionId")
                q_text_hi = question_obj.get("questionHi") or ""
                q_text_en = question_obj.get("questionEn") or ""
                options = question_obj.get("options", [])
                q_idx = question_obj.get("index", q_count)
                q_total = question_obj.get("total", "?")

                q_count += 1
                combined_q = q_text_hi
                if q_text_en and q_text_en != q_text_hi:
                    combined_q = f"{q_text_hi}\n[English: {q_text_en}]" if q_text_hi else q_text_en

                log(f"Q{q_idx + 1}/{q_total}: {q_text_hi or q_text_en}")

                if not q_id or not isinstance(options, list) or len(options) < 2:
                    break

                correct_index = self.ask_groq(combined_q, options)
                if correct_index is None:
                    rl = self.quiz_use_lifeline(session_id, q_id)
                    removed_set = set(rl or [])
                    remaining = [i for i in range(len(options)) if i not in removed_set]
                    correct_index = remaining[0] if remaining else 0

                correct_index = max(0, min(correct_index, len(options) - 1))
                log(f"  → Chose [{correct_index}] {options[correct_index]}")

                time.sleep(delay)

                result = self.quiz_submit_answer(session_id, q_id, correct_index)
                if not result:
                    break

                if result.get("success"):
                    correct_flag = result.get("correct", False)
                    coins_earned = int(result.get("coinsEarned") or 0)
                    coins_so_far = result.get("coinsSoFar", 0)
                    hearts = int(result.get("hearts", hearts))
                    session_coins = coins_so_far
                    total_coins_run += coins_earned
                    status_txt = "✅ CORRECT" if correct_flag else "❌ WRONG"
                    log(f"  {status_txt} | +{coins_earned} | hearts={hearts}")

                    next_info = result.get("next")
                    if not next_info:
                        log(f"Session complete! Coins: {coins_so_far}")
                        break

                    if isinstance(next_info, dict):
                        if "question" in next_info and isinstance(next_info.get("question"), dict):
                            question_obj = next_info["question"]
                            session_id = result.get("sessionId") or session_id
                            continue
                        if "result" in next_info:
                            rinfo = next_info["result"]
                            if isinstance(rinfo, dict):
                                log(f"Level result: {rinfo.get('scorePct')}% | Coins: {rinfo.get('coins')}")
                            break

                    if q_count > 0 and ad_every > 0 and (q_count % ad_every == 0):
                        nq = self.quiz_ad_ack(session_id)
                        if nq:
                            question_obj = nq
                            continue
                        break
                    break
                else:
                    break

            sessions_done += 1
            log(f"Session {session_num} coins: {session_coins}")
            if session_num < max_sessions:
                time.sleep(2)

        return {
            "sessions": sessions_done,
            "total_coins": total_coins_run,
            "balance": self.get_balance(),
        }


# ───────────────────── Per-user bot state ─────────────────────
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
            [KeyboardButton("🤖 Run Quiz"), KeyboardButton("ℹ️ Help")],
        ],
        resize_keyboard=True,
    )


# ───────────────────── Handlers ─────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    bot = get_bot(user.id)
    text = (
        f"👋 Hi {user.first_name}!\n\n"
        "MiniPix V2 Bot ready.\n\n"
        "Use the buttons or commands:\n"
        "/balance  /campaign  /accounts\n"
        "/login  /watch  /quiz  /help"
    )
    if bot.access_token:
        text += f"\n\n✅ Logged in as: {bot.current_account_label or bot.phone}"
    else:
        text += "\n\n⚠️ Not logged in. Use /login"
    await update.message.reply_text(text, reply_markup=main_menu_keyboard())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 Commands\n\n"
        "/start – main menu\n"
        "/balance – coin balance\n"
        "/campaign – watch campaign status\n"
        "/accounts – list / switch / remove accounts\n"
        "/login – OTP or Token login\n"
        "/watch – smart 4x watch all series\n"
        "/quiz – quiz status + auto solver\n"
        "/logout – logout current account\n\n"
        "⚠️ Set GROQ_API_KEY env var for quiz solver.",
        reply_markup=main_menu_keyboard(),
    )


async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot = get_bot(update.effective_user.id)
    if not bot.access_token:
        await update.message.reply_text("Not logged in. Use /login")
        return
    coins = bot.get_balance()
    if coins is None:
        await update.message.reply_text("Failed to fetch balance")
    else:
        await update.message.reply_text(f"💰 Coin Balance: *{coins}*", parse_mode="Markdown")


async def campaign_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot = get_bot(update.effective_user.id)
    if not bot.access_token:
        await update.message.reply_text("Not logged in. Use /login")
        return
    st = bot.get_campaign_status()
    text = (
        f"🎥 Campaign: {'ON' if st['enabled'] else 'OFF'}\n"
        f"Daily cap: {st['used']}/{st['cap']}\n"
        f"Reached: {st['reached']}\n"
        f"Block watching: {st['blockWatching']}"
    )
    await update.message.reply_text(text)


async def accounts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot = get_bot(update.effective_user.id)
    accs = bot.list_accounts()
    if not accs:
        await update.message.reply_text("No saved accounts.")
        return
    lines = [f"👥 Saved Accounts ({len(accs)}):\n"]
    keyboard = []
    for i, lbl in enumerate(accs, 1):
        acc = bot.accounts[lbl]
        ph = acc.get("phone") or "?"
        lines.append(f"{i}. {lbl}  |  {ph}")
        keyboard.append([
            InlineKeyboardButton(f"Switch → {lbl}", callback_data=f"sw:{lbl}"),
            InlineKeyboardButton("❌", callback_data=f"rm:{lbl}"),
        ])
    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    bot = get_bot(query.from_user.id)
    if data.startswith("sw:"):
        label = data[3:]
        ok, msg = bot.switch_account(label)
        if ok:
            bot.open_app()
            bal = bot.get_balance()
            await query.edit_message_text(f"✅ {msg}\n💰 Balance: {bal}")
        else:
            await query.edit_message_text(f"❌ {msg}")
    elif data.startswith("rm:"):
        label = data[3:]
        if bot.remove_account(label):
            await query.edit_message_text(f"Removed account: {label}")
        else:
            await query.edit_message_text("Remove failed")


# ── Login conversation ──
async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📱 Phone + OTP", callback_data="login:otp")],
        [InlineKeyboardButton("🔑 Bearer Token", callback_data="login:token")],
    ]
    await update.message.reply_text(
        "Choose login method:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ConversationHandler.END  # we use callback instead


async def login_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "login:otp":
        await query.edit_message_text("Send phone number (e.g. +9198xxxxxxxx or 98xxxxxxxx):")
        return WAIT_PHONE
    elif query.data == "login:token":
        await query.edit_message_text("Send the Bearer token:")
        return WAIT_TOKEN
    return ConversationHandler.END


async def login_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not phone.startswith("+"):
        phone = "+91" + phone.lstrip("0")
    context.user_data["phone"] = phone
    bot = get_bot(update.effective_user.id)
    st = bot.login_otp_generate(phone)
    if not st:
        await update.message.reply_text("Failed to send OTP. Try again.")
        return ConversationHandler.END
    context.user_data["session_token"] = st
    await update.message.reply_text(f"OTP sent to {phone}.\nNow send the OTP:")
    return WAIT_OTP


async def login_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    otp = update.message.text.strip()
    bot = get_bot(update.effective_user.id)
    st = context.user_data.get("session_token")
    if not st:
        await update.message.reply_text("Session lost. Start /login again.")
        return ConversationHandler.END
    ok = bot.login_otp_verify(st, otp)
    if ok:
        bot.open_app()
        bal = bot.get_balance()
        await update.message.reply_text(
            f"✅ Login success!\nUser: {bot.user_id}\n💰 Balance: {bal}",
            reply_markup=main_menu_keyboard(),
        )
    else:
        await update.message.reply_text("❌ OTP verify failed.")
    return ConversationHandler.END


async def login_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = update.message.text.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    bot = get_bot(update.effective_user.id)
    ok = bot.login_with_token(token)
    if ok:
        bot.open_app()
        bal = bot.get_balance()
        await update.message.reply_text(
            f"✅ Token login success!\n💰 Balance: {bal}",
            reply_markup=main_menu_keyboard(),
        )
    else:
        await update.message.reply_text("❌ Invalid / expired token.")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


# ── Watch ──
async def watch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot = get_bot(update.effective_user.id)
    if not bot.access_token:
        await update.message.reply_text("Not logged in. Use /login")
        return

    msg = await update.message.reply_text("🚀 Starting smart 4x watch...\nThis can take a while.")

    def progress(text):
        # fire-and-forget update (Telegram rate limits apply)
        try:
            context.application.create_task(
                msg.edit_text(f"🚀 Watching...\n\n{text[-800:]}")
            )
        except Exception:
            pass

    # run in thread to not block
    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: bot.browse_and_watch_all_smart_repeat(progress_callback=progress, max_watches=250),
    )

    if "error" in result:
        await msg.edit_text(f"❌ {result['error']}")
        return

    text = (
        f"🏁 Watch finished\n\n"
        f"Watched: {result['watched']}\n"
        f"Skipped: {result['skipped']}\n"
        f"Failed: {result['failed']}\n"
    )
    if result.get("delta") is not None:
        text += f"💰 {result['balance_before']} → {result['balance_after']} ({result['delta']:+d})"
    await msg.edit_text(text)


# ── Quiz ──
async def quiz_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot = get_bot(update.effective_user.id)
    if not bot.access_token:
        await update.message.reply_text("Not logged in.")
        return
    data = bot.get_quiz_status()
    if not data:
        await update.message.reply_text("Failed to get quiz status")
        return
    lvl = data.get("currentLevel", "?")
    cfg = data.get("levelConfig", {}) or {}
    hearts = data.get("hearts", {}) or {}
    daily = data.get("dailyAttempts", {}) or {}
    totals = data.get("totals", {}) or {}
    text = (
        f"🧠 Quiz Status\n\n"
        f"Level: {lvl}\n"
        f"Qs: {cfg.get('questionsCount')} | +{cfg.get('coinsPerCorrect')}/correct | Pass {cfg.get('passPct')}%\n"
        f"Hearts: {hearts.get('freePerLevel')}/level (cap {hearts.get('cap')})\n"
        f"Daily: {daily.get('used')}/{daily.get('limit')} {'[EXHAUSTED]' if daily.get('exhausted') else ''}\n"
        f"Lifetime: {totals.get('answers')} answers | {totals.get('correct')} correct | {totals.get('coins')} coins"
    )
    await update.message.reply_text(text)


async def quiz_run_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot = get_bot(update.effective_user.id)
    if not bot.access_token:
        await update.message.reply_text("Not logged in.")
        return ConversationHandler.END
    if not GROQ_API_KEY:
        await update.message.reply_text("GROQ_API_KEY not set. Quiz solver disabled.")
        return ConversationHandler.END
    await update.message.reply_text("How many quiz sessions? (1-5, default 3):")
    return WAIT_QUIZ_SESSIONS


async def quiz_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        n = int(update.message.text.strip() or "3")
        n = max(1, min(5, n))
    except Exception:
        n = 3
    context.user_data["quiz_sessions"] = n
    await update.message.reply_text(f"Delay between questions in seconds? (default {QUIZ_QUESTION_DELAY}):")
    return WAIT_QUIZ_DELAY


async def quiz_delay_and_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        delay = int(update.message.text.strip() or str(QUIZ_QUESTION_DELAY))
        delay = max(3, min(30, delay))
    except Exception:
        delay = QUIZ_QUESTION_DELAY

    bot = get_bot(update.effective_user.id)
    sessions = context.user_data.get("quiz_sessions", 3)
    msg = await update.message.reply_text(f"🤖 Running {sessions} quiz sessions (delay {delay}s)...")

    def progress(text):
        try:
            context.application.create_task(
                msg.edit_text(f"🤖 Quiz running...\n\n{text[-900:]}")
            )
        except Exception:
            pass

    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: bot.run_quiz_auto(max_sessions=sessions, question_delay=delay, progress_callback=progress),
    )

    if "error" in result:
        await msg.edit_text(f"❌ {result['error']}")
    else:
        await msg.edit_text(
            f"🏁 Quiz done\nSessions: {result.get('sessions')}\n"
            f"Coins this run: ~{result.get('total_coins')}\n"
            f"Current balance: {result.get('balance')}"
        )
    return ConversationHandler.END


async def logout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot = get_bot(update.effective_user.id)
    bot.logout_current() if hasattr(bot, "logout_current") else bot._reset_state()
    await update.message.reply_text("Logged out.", reply_markup=main_menu_keyboard())


# text button router
async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text == "💰 Balance":
        await balance_cmd(update, context)
    elif text == "📊 Campaign":
        await campaign_cmd(update, context)
    elif text == "👥 Accounts":
        await accounts_cmd(update, context)
    elif text == "➕ Login":
        await login_start(update, context)
    elif text == "🎬 Watch All (4x)":
        await watch_cmd(update, context)
    elif text == "🧠 Quiz Status":
        await quiz_status_cmd(update, context)
    elif text == "🤖 Run Quiz":
        return await quiz_run_start(update, context)
    elif text == "ℹ️ Help":
        await help_cmd(update, context)
    else:
        await update.message.reply_text("Unknown. Use /help or the buttons.")


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: Set TELEGRAM_BOT_TOKEN environment variable")
        return
    if not GROQ_API_KEY:
        print("WARNING: GROQ_API_KEY not set – quiz solver will not work")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Login conversation
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

    # Quiz conversation
    quiz_conv = ConversationHandler(
        entry_points=[
            CommandHandler("quizrun", quiz_run_start),
            MessageHandler(filters.Regex("^🤖 Run Quiz$"), quiz_run_start),
        ],
        states={
            WAIT_QUIZ_SESSIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_sessions)],
            WAIT_QUIZ_DELAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_delay_and_run)],
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
    app.add_handler(CommandHandler("logout", logout_cmd))
    app.add_handler(CallbackQueryHandler(account_callback, pattern=r"^(sw|rm):"))
    app.add_handler(login_conv)
    app.add_handler(quiz_conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    print("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
