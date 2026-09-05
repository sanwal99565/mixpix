import requests
import json
import time
import sys
import os
from datetime import date

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

API_BASE = "https://api.minipix.co/v4"
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "minipix_config.json")

MAX_WATCHES_PER_EP = 4
REWARDS_BY_WATCH = {1: 15, 2: 8, 3: 5, 4: 3}
def _expected_reward(nth_watch):
    return REWARDS_BY_WATCH.get(int(nth_watch) if nth_watch else 1, 0)

HEADERS_BASE = {
    "user-agent": "okhttp/4.12.0",
    "accept-encoding": "gzip",
}


class MiniPixAuto:
    def __init__(self):
        self.access_token = None
        self.user_id = None
        self.profile_id = None
        self.session = requests.Session()
        self.session.headers.update(HEADERS_BASE)
        self.phone = None
        self.device_id = "65969f0b7041fabc"
        self.device_info = "Xiaomi"
        self.watch_history = {}
        self.watch_history_raw = []
        self.runtime_watch_counts = {}
        self.config = self.load_config()
        if self.config:
            self.access_token = self.config.get("access_token")
            self.user_id = self.config.get("user_id")
            self.profile_id = self.config.get("profile_id")
            self.phone = self.config.get("phone")
            if self.access_token:
                self.session.headers["authorization"] = f"Bearer {self.access_token}"

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_config(self):
        cfg = {
            "access_token": self.access_token,
            "user_id": self.user_id,
            "profile_id": self.profile_id,
            "phone": self.phone,
        }
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(cfg, f, indent=2)
            print(f"[i] Config save ho gaya: {CONFIG_FILE}")
        except Exception as e:
            print(f"[!] Config save error: {e}")

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
            print(f"[!] Network error: {e}")
            return 0, None

    def login_otp_generate(self, phone):
        self.phone = phone
        payload = {"phone_number": phone}
        sc, data = self._req(
            "POST",
            "/login/generate-otp",
            headers={"content-type": "application/json; charset=utf-8"},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        if sc == 200 and data and data.get("message") == "OTP sent":
            print(f"[+] OTP bhej diya gaya: {phone}")
            return data.get("session_token")
        print(f"[-] OTP generate fail: {sc} {data}")
        return None

    def login_otp_verify(self, session_token, otp):
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
        if sc == 200 and data and data.get("access_token"):
            self.access_token = data["access_token"]
            self.user_id = data["id"]
            self.session.headers["authorization"] = f"Bearer {self.access_token}"
            print(f"[+] Login success! User ID: {self.user_id}")
            self.get_user()
            self.save_config()
            return True
        print(f"[-] OTP verify fail: {sc} {data}")
        return False

    def login_with_token(self, token, user_id=None, profile_id=None):
        self.access_token = token
        self.user_id = user_id
        self.profile_id = profile_id
        self.session.headers["authorization"] = f"Bearer {self.access_token}"
        print("[+] Token set. User info fetch kar raha hun...")
        if not self.get_user():
            print("[-] Token invalid ya expired!")
            return False
        self.save_config()
        return True

    def get_user(self):
        if not self.user_id:
            print("[-] User ID nahi pata!")
            return False
        sc, data = self._req("GET", f"/users/{self.user_id}")
        if sc == 200 and data:
            self.user_id = data.get("_id", self.user_id)
            self.profile_id = data.get("master_profile", self.profile_id)
            phone = data.get("mobile")
            if phone and not self.phone:
                self.phone = phone
            print(f"[i] Phone: {data.get('mobile')} | Profile: {self.profile_id}")
            return True
        print(f"[-] Get user fail: {sc} {data}")
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
        if sc == 200 and data and data.get("success"):
            print("[+] App open mark (10 daily coins claim ho gaye!)")
            return True
        print(f"[-] Open app fail: {sc} {data}")
        return False

    def get_balance(self):
        sc, data = self._req("GET", "/coins/balance")
        if sc == 200 and isinstance(data, dict):
            coins = data.get("coins", 0)
            if isinstance(coins, dict):
                coins = coins.get("coins", 0)
            print(f"[💰] Coin Balance: {coins}")
            return coins
        if sc == 200 and isinstance(data, (int, float)):
            print(f"[💰] Coin Balance: {data}")
            return int(data)
        print(f"[-] Balance fetch fail: {sc} {str(data)[:120] if data else data}")
        return None

    def get_balance_silent(self):
        try:
            sc, data = self._req("GET", "/coins/balance")
            if sc == 200 and isinstance(data, dict):
                coins = data.get("coins")
                if isinstance(coins, dict):
                    coins = coins.get("coins")
                return coins
            if sc == 200 and isinstance(data, (int, float)):
                return int(data)
        except Exception:
            pass
        return None

    def get_campaign_status(self):
        sc, data = self._req("GET", "/watch-campaign/status")
        if sc == 200 and isinstance(data, dict) and data.get("success"):
            cap = data.get("dailyVideoCap", {}) or {}
            status = {
                "enabled": data.get("enabled", False),
                "cap": cap.get("cap", 0),
                "used": cap.get("used", 0),
                "reached": cap.get("reached", False),
                "blockWatching": cap.get("blockWatching", False),
            }
            print(
                f"[🎥] Campaign: {'ON' if status['enabled'] else 'OFF'} | "
                f"Daily cap: {status['used']}/{status['cap']} "
                f"{'[REACHED]' if status['reached'] else ''}"
            )
            return status
        return {"enabled": False, "cap": 0, "used": 0, "reached": False, "blockWatching": False}

    def _collect_series_deep(self, obj, out_dict):
        if obj is None:
            return
        if isinstance(obj, dict):
            if (obj.get("_id") or obj.get("id") or obj.get("series_id")) and (
                obj.get("title")
                or obj.get("numberOfEpisodes") is not None
                or obj.get("totalEpisodes") is not None
                or obj.get("watchLadderEnabled") is not None
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

    def get_all_series(self, page_size=100, max_pages=20):
        found = {}
        tried = 0
        home_urls = [
            ("GET", "/short_search?page=home", False),
        ]
        for method, tmpl, _p in home_urls:
            tried += 1
            try:
                sc, data = self._req(method, tmpl)
            except Exception:
                sc, data = 0, None
            if sc == 200 and isinstance(data, (dict, list)):
                self._collect_series_deep(data, found)
                print(f"[📚] short_search/home se series mili: {len(found)}")
        endpoints = [
            ("GET", "/webseries?page={p}&pageSize={ps}", True),
            ("GET", "/discover?type=webseries&page={p}&pageSize={ps}", True),
            ("GET", "/home?page={p}&pageSize={ps}", False),
            ("GET", "/discover/webseries?page={p}&pageSize={ps}", True),
            ("GET", "/series?page={p}&pageSize={ps}", True),
            ("GET", "/content?type=webseries&page={p}&pageSize={ps}", True),
        ]
        for method, tmpl, is_series_list in endpoints:
            for page in range(1, max_pages + 1):
                url = tmpl.format(p=page, ps=page_size)
                tried += 1
                try:
                    sc, data = self._req(method, url)
                except Exception:
                    continue
                if not (sc == 200 and isinstance(data, dict)):
                    continue
                self._collect_series_deep(data, found)
                series_candidates = []
                if isinstance(data.get("webseries"), list):
                    series_candidates = data["webseries"]
                elif isinstance(data.get("series"), list):
                    series_candidates = data["series"]
                elif isinstance(data.get("data"), list):
                    series_candidates = data["data"]
                elif isinstance(data.get("items"), list):
                    series_candidates = data["items"]
                elif isinstance(data.get("results"), list):
                    series_candidates = data["results"]
                elif isinstance(data.get("contents"), list):
                    series_candidates = data["contents"]
                elif isinstance(data.get("list"), list):
                    series_candidates = data["list"]
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
        print(f"[📚] Total Series found: {len(series_list)} (tried {tried} endpoint hits)")
        return series_list

    def get_watched_set_from_profile(self):
        watched = set()
        counts = self.get_watch_counts_from_profile()
        for k, c in counts.items():
            if c >= 1:
                watched.add(k)
        return watched

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

    def get_profile(self):
        if not (self.user_id and self.profile_id):
            return None
        sc, data = self._req(
            "GET", f"/users/{self.user_id}/profiles/{self.profile_id}"
        )
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
                    self.watch_history[key] = {
                        "watchedPct": cur_pct,
                        "time": cur_time,
                    }
            print(f"[i] Watch history: {len(history)} raw entries")
            return profile
        return None

    def get_watch_tasks(self):
        sc, data = self._req("GET", "/coins/tasks?all=1")
        if sc == 200 and data:
            tasks = data.get("tasks", [])
            ladder_tasks = [t for t in tasks if t.get("task_type") == "watch_ladder"]
            total_series = len(ladder_tasks)
            total_pending = 0
            total_coins = 0
            print(f"\n[📋] Available Watch Ladder Tasks ({total_series} series):")
            print("-" * 120)
            print(f"  {'#':>2s}  {'Series ID':<26s}  {'Series Name':<32s}  {'Eps':>4s}  {'Pending':>7s}  {'Reward':>6s}  {'State':>10s}")
            print("-" * 120)
            for i, t in enumerate(ladder_tasks, 1):
                prog = t.get("progress", {})
                watched = prog.get("watched", 0)
                required = prog.get("required", 10)
                pending = max(0, required - watched)
                total_pending += pending
                total = t.get("reward_coins", 0)
                total_coins += total
                sid = t.get("series_id", "")
                state = t.get("state", "?")
                w_r = f"{watched}/{required}"
                print(
                    f"  {i:2d}. {sid:<26s}  {t.get('title','?'):<32s}  "
                    f"{w_r:>6s}  {pending:>5d}   "
                    f"{total:>5d}  {state:>10s}"
                )
            print("-" * 120)
            print(f"  TOTAL: {total_pending} pending episodes across {total_series} series | Total reward: {total_coins} coins")
            return ladder_tasks
        print(f"[-] Tasks fail: {sc} {data}")
        return []

    def show_all_series_detail(self):
        self.get_profile()
        watch_counts = self.get_watch_counts_from_profile()
        print("[i] Sab series load ho rahi hain (NEW 4x REWARD SYSTEM: 15→8→5→3 coins per episode)...")
        all_series = self.get_all_series()
        tasks = self.get_watch_tasks()
        task_sids = {t.get("series_id") for t in tasks if t.get("series_id")}
        series_pool = []
        for s in all_series:
            sid = s.get("_id") or s.get("id") or s.get("series_id")
            if not sid:
                continue
            series_pool.append({
                "series_id": sid,
                "title": s.get("title") or s.get("name") or "?",
                "_raw": s,
            })
        if not series_pool:
            print("[-] Koi series nahi mili!")
            return
        print()
        print("=" * 148)
        print("[🔍] DETAILED PER-SERIES REPORT (4x REWARDS | Epi-wise watch counts & remaining slots)")
        print("=" * 148)
        grand_total_eps = 0
        grand_total_watches = 0
        grand_total_maxed = 0
        grand_total_remaining_slots = 0
        grand_total_partial = 0
        limit_preview = min(len(series_pool), 25)
        print(f"[i] Showing first {limit_preview} of {len(series_pool)} series (sab ke liye Option 11 use karo).")
        print(f"[💡] Rewards: 1st watch +15 | 2nd +8 | 3rd +5 | 4th +3 coins (max {MAX_WATCHES_PER_EP}x per episode)")
        for i, t in enumerate(series_pool[:limit_preview], 1):
            sid = t["series_id"]
            series_name = t.get("title", "?")
            episodes, total_eps = self.get_episodes(sid, page=1, page_size=200)
            if not episodes:
                print(f"\n  [{i:2d}] {series_name} (ID: {sid}) → Episodes list nahi mila!")
                continue
            episodes_sorted = sorted(episodes, key=lambda e: e.get("episodeNo", 0))
            counts_bucket = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
            partial_eps = []
            series_watches_total = 0
            series_maxed_eps = 0
            series_remaining_slots = 0
            for ep in episodes_sorted:
                ep_no = ep.get("episodeNo", 0)
                key_pair = (str(sid), str(ep_no))
                cnt = watch_counts.get(key_pair, 0)
                cnt_clamped = min(cnt, MAX_WATCHES_PER_EP)
                counts_bucket[cnt_clamped] = counts_bucket.get(cnt_clamped, 0) + 1
                series_watches_total += cnt
                if cnt_clamped >= MAX_WATCHES_PER_EP:
                    series_maxed_eps += 1
                else:
                    series_remaining_slots += (MAX_WATCHES_PER_EP - cnt_clamped)
                if 0 < cnt_clamped < MAX_WATCHES_PER_EP:
                    pass
                wh = self.watch_history.get((sid, ep_no), {})
                pct = wh.get("watchedPct", 0)
                if 0 < pct < 80 and cnt_clamped == 0:
                    partial_eps.append((ep_no, pct))
            grand_total_eps += len(episodes_sorted)
            grand_total_watches += series_watches_total
            grand_total_maxed += series_maxed_eps
            grand_total_remaining_slots += series_remaining_slots
            grand_total_partial += len(partial_eps)
            def _ranges_from_numbers(nums):
                if not nums:
                    return "-"
                nums_sorted = sorted(nums)
                chunks = []
                start = prev = nums_sorted[0]
                for e in nums_sorted[1:]:
                    if e == prev + 1:
                        prev = e
                    else:
                        chunks.append(f"{start}" if start == prev else f"{start}-{prev}")
                        start = prev = e
                chunks.append(f"{start}" if start == prev else f"{start}-{prev}")
                return ", ".join(chunks)
            partial_str = ", ".join(f"E{n}({p}%)" for n, p in partial_eps) if partial_eps else "-"
            ladder_flag = " 🪜" if sid in task_sids else ""
            eps_0x = counts_bucket.get(0, 0)
            eps_1x = counts_bucket.get(1, 0)
            eps_2x = counts_bucket.get(2, 0)
            eps_3x = counts_bucket.get(3, 0)
            eps_4x = counts_bucket.get(4, 0)
            eps_0to3 = eps_0x + eps_1x + eps_2x + eps_3x
            est_min_coins = (eps_1x * 15) + (eps_2x * (15 + 8)) + (eps_3x * (15 + 8 + 5)) + (eps_4x * (15 + 8 + 5 + 3))
            est_max_possible = est_min_coins + (series_remaining_slots * 8)
            print(f"\n  [{i:2d}] 📺 {series_name}{ladder_flag}")
            print(f"       Series ID : {sid}")
            print(f"       Total Eps : {len(episodes_sorted)} | 🔁 Total watches done: {series_watches_total}")
            print(f"       Per-count : ❌ 0x={eps_0x:<4d} 🔵 1x={eps_1x:<4d} 🟢 2x={eps_2x:<4d} 🟠 3x={eps_3x:<4d} ✅ 4x(MAX)={eps_4x:<4d}")
            print(f"       Rewards   : ⏳ Remaining slots={series_remaining_slots}  💰 Earned ~{est_min_coins} coins | Max possible ~{est_max_possible} coins")
            if eps_0x:
                list_0x = []
                for ep in episodes_sorted:
                    ep_no = ep.get("episodeNo", 0)
                    k = (str(sid), str(ep_no))
                    if watch_counts.get(k, 0) == 0:
                        list_0x.append(int(ep_no) if str(ep_no).isdigit() else ep_no)
                r0 = _ranges_from_numbers(list_0x[:100])
                print(f"       ❌ 0x (new)  : {r0}")
            if eps_1x + eps_2x + eps_3x:
                list_in_progress = []
                for ep in episodes_sorted:
                    ep_no = ep.get("episodeNo", 0)
                    k = (str(sid), str(ep_no))
                    c = watch_counts.get(k, 0)
                    if 1 <= c < MAX_WATCHES_PER_EP:
                        try:
                            list_in_progress.append(f"{int(ep_no)}(x{c})")
                        except Exception:
                            list_in_progress.append(f"{ep_no}(x{c})")
                lip_str = ", ".join(list_in_progress[:40]) + (".." if len(list_in_progress) > 40 else "") if list_in_progress else "-"
                print(f"       🔁 Repeat eligible: {lip_str}")
            if partial_eps:
                print(f"       ⏳ Partial <80% : {partial_str}")
        print()
        print("=" * 148)
        print(f"  📊 OVERALL (preview {limit_preview}/{len(series_pool)} series):")
        print(f"     • Total Episodes            : {grand_total_eps}")
        print(f"     • Total watches completed   : {grand_total_watches}")
        print(f"     • Fully maxed (4x done)     : {grand_total_maxed} episodes")
        print(f"     • Partials <80%             : {grand_total_partial} episodes")
        print(f"     • Remaining reward SLOTS    : {grand_total_remaining_slots} (avg ~8 coins each → ~{grand_total_remaining_slots*8} coins pending)")
        print("=" * 148)

    def get_series(self, series_id):
        sc, data = self._req("GET", f"/webseries/{series_id}")
        if sc == 200 and data and data.get("success"):
            return data
        print(f"[-] Series fail: {sc}")
        return None

    def get_episodes(self, series_id, page=1, page_size=50):
        sc, data = self._req(
            "GET",
            f"/episodes?series_id={series_id}&page={page}&pageSize={page_size}",
        )
        if sc == 200 and data:
            return data.get("episodes", []), data.get("total", 0)
        print(f"[-] Episodes fail: {sc}")
        return [], 0

    def _update_watch_progress(
        self,
        series_id,
        series_title,
        hindi_title,
        episode_no,
        tc_in_ms,
        tc_out_ms,
        detail_image,
        watched_pct,
        campaign=False,
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
        if watched_pct == 99:
            stored_pct = 99
        elif watched_pct >= 100:
            stored_pct = 100
        else:
            stored_pct = watched_pct
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
            if not ok1 and sc1 and sc1 >= 400:
                msg = ""
                if isinstance(d1, dict):
                    msg = d1.get("message") or d1.get("error") or str(d1)[:100]
                print(f"    [!] profile PATCH {watched_pct}% HTTP {sc1}: {msg}")
        except Exception as e:
            print(f"    [!] profile PATCH err: {e}")

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
                    if d.get("success") is True:
                        any_ok = True
                        break
                    if sc == 200 and "success" not in d:
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

    def watch_campaign_select_series(self, series_id):
        payload = {"seriesId": series_id, "series_id": series_id, "campaign": False}
        candidates = [
            ("POST", "/watch-campaign/session/start", payload),
            ("POST", "/watch-campaign/start", payload),
            ("POST", "/watch-campaign/select-series", payload),
            ("POST", "/watch-campaign/select", payload),
            ("PUT", "/watch-campaign/select", payload),
            ("PATCH", "/watch-campaign/select", payload),
            ("POST", "/watch-ladder/start-session", payload),
            ("POST", "/campaigns/watch/start", {"series_id": series_id}),
        ]
        confirmed = False
        for method, path, body in candidates:
            try:
                sc, data = self._req(
                    method, path,
                    headers={"content-type": "application/json; charset=utf-8"},
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                )
                if sc and sc < 500 and isinstance(data, dict):
                    if data.get("success"):
                        confirmed = True
                        break
                    if sc == 200:
                        confirmed = True
                        break
            except Exception:
                pass
        if confirmed:
            try:
                self._start_task_for_series(series_id)
            except Exception:
                pass
        else:
            try:
                self._start_task_for_series(series_id)
            except Exception:
                pass
        return True

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
            candidates.append(("POST", f"/coins/tasks/{task_id}/claim", None))
            candidates.append(("POST", "/coins/tasks/claim", {"task_id": task_id, "campaign": False}))
            candidates.append(("POST", f"/coins/tasks/{task_id}/reward", None))
            candidates.append(("POST", f"/coins/tasks/{task_id}/complete", {}))
            candidates.append(("POST", "/coins/tasks/complete", {"task_id": task_id, "campaign": False}))
        if series_id:
            candidates.append(("POST", f"/watch-ladder/claim", {"series_id": series_id, "campaign": False}))
            candidates.append(("POST", f"/coins/watch-ladder/{series_id}/claim", None))
            candidates.append(("POST", f"/coins/claim", {"series_id": series_id, "type": "watch_ladder", "campaign": False}))
            candidates.append(("POST", "/coins/redeem", {"series_id": series_id, "task": "watch_ladder", "campaign": False}))
            candidates.append(("POST", f"/watch-ladder/{series_id}/complete", {"campaign": False}))
        any_ok = False
        last_msg = ""
        for entry in candidates:
            method, path, body = entry[0], entry[1], entry[2] if len(entry) > 2 else None
            try:
                sc, data = self._req(
                    method, path,
                    headers={"content-type": "application/json; charset=utf-8"},
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None,
                )
                if sc and sc < 500 and isinstance(data, dict):
                    if data.get("success") is True:
                        coins = data.get("coins") or data.get("reward_coins") or data.get("reward") or data.get("amount")
                        print(f"[💰] CLAIM OK! {task_id or series_id}: {coins or 'OK'} coins ({method} {path})")
                        any_ok = True
                        break
                    if data.get("success") is False:
                        last_msg = data.get("message") or data.get("error") or str(data)[:80]
                    if sc == 200 and "success" not in data:
                        continue
                elif sc and sc < 500 and isinstance(data, str):
                    if sc == 200 and len(data or "") > 0:
                        any_ok = True
                        break
            except Exception:
                continue
        if not any_ok:
            extra = f" msg={last_msg}" if last_msg else ""
            print(f"[i] Claim endpoints tried for {task_id or series_id} (none ok){extra}")
        return any_ok

    def refresh_tasks_and_balance(self):
        try:
            self.get_balance()
        except Exception:
            pass
        sc, data = self._req("GET", "/coins/tasks?all=1")
        updated = []
        if sc == 200 and isinstance(data, dict):
            tasks = data.get("tasks") or data.get("data") or []
            if isinstance(tasks, dict):
                tasks = tasks.get("tasks") or []
            for t in tasks if isinstance(tasks, list) else []:
                if isinstance(t, dict) and t.get("task_type") == "watch_ladder":
                    updated.append(t)
        return updated

    def watch_episode(
        self,
        episode,
        series_info,
        delay_multiplier=0.0,
        campaign=False,
        min_watch_pct=80,
        allow_repeat=False,
        nth_watch=None,
    ):
        if not isinstance(series_info, dict):
            print("  [!] series_info invalid")
            return False, "invalid_series"
        if not isinstance(episode, dict):
            print("  [!] episode invalid")
            return False, "invalid_episode"
        series_id = (
            series_info.get("_id")
            or series_info.get("id")
            or series_info.get("series_id")
        )
        if not series_id:
            print("  [!] missing series_id in series_info")
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
        dur_sec = tc_out - tc_in
        history_key = (series_id, ep_no)
        current_pct = int(self.watch_history.get(history_key, {}).get("watchedPct", 0) or 0)
        try:
            ep_no_fmt = int(ep_no)
            ep_fmt = f"{ep_no_fmt:2d}"
        except Exception:
            ep_fmt = f"{str(ep_no):>2s}"
        if not allow_repeat and current_pct >= min_watch_pct:
            print(f"  ⏭️  Ep {ep_fmt} already watched {current_pct}% - skip")
            return True, "skip"
        expected_coin = None
        if nth_watch is not None:
            try:
                nw = int(nth_watch)
                expected_coin = _expected_reward(nw)
            except Exception:
                pass

        if not episode.get("coinUnlocked", True):
            ep_id = episode.get("_id") or episode.get("id")
            url = episode.get("playbackUrl")
            print(f"  🔒 Ep {ep_fmt} LOCKED — trying unlock/cdn-bypass & client bypass...")
            try:
                self.unlock_episode(series_id, ep_id, ep_no, playback_url=url)
            except Exception:
                pass

        progress_steps = [1, 50, 80, 99, 100, 100]
        watch_tag = ""
        if nth_watch is not None and expected_coin is not None:
            try:
                nw = int(nth_watch)
                ord_txt = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}.get(nw, f"{nw}th")
                watch_tag = f" [{ord_txt} watch | expect +{expected_coin}]"
            except Exception:
                pass
        repeat_tag = " [REPEAT]" if allow_repeat else ""
        print(f"  🎬 Ep {ep_fmt} | {dur_sec:4d}s{watch_tag}{repeat_tag} | ", end="", flush=True)
        any_fail = False
        reported_coin_progress = False
        for pct in progress_steps:
            if not allow_repeat and pct < current_pct:
                continue
            ok = self._update_watch_progress(
                series_id, series_title, hindi_title, ep_no,
                tc_in_ms, tc_out_ms, detail_image, pct,
                campaign=False,
            )
            if not ok:
                print(f"\n  ❌ progress {pct}% not ok (continuing to coin endpoints)...")
                any_fail = True
            print(f"{pct}% ", end="", flush=True)
            if pct >= 80 and not reported_coin_progress:
                try:
                    self._report_watch_progress_to_coins(series_id, ep_no, pct, series_title)
                    reported_coin_progress = True
                except Exception:
                    pass
            if delay_multiplier > 0:
                try:
                    idx_cur = progress_steps.index(pct)
                    prev_pct = progress_steps[idx_cur - 1] if idx_cur > 0 else 0
                    delta = pct - prev_pct
                    delay = (dur_sec * delay_multiplier * delta / 100)
                    if delay > 0:
                        time.sleep(min(delay, 2))
                except Exception:
                    time.sleep(0.15)
            else:
                time.sleep(0.15)
        if not reported_coin_progress:
            try:
                self._report_watch_progress_to_coins(series_id, ep_no, 100, series_title)
            except Exception:
                pass
        bal_before = self.get_balance_silent()
        try:
            self.claim_reward_task(series_id=series_id, task_id=None)
        except Exception:
            pass
        time.sleep(0.6)
        bal_after = self.get_balance_silent()
        gained = 0
        if bal_before is not None and bal_after is not None:
            gained = (bal_after or 0) - (bal_before or 0)
            if gained != 0:
                match = ""
                if expected_coin is not None:
                    match = " ✅ EXPECTED" if gained == expected_coin else f" (expected +{expected_coin})"
                print(f"    💸 Ep coins: {bal_before} → {bal_after} ({gained:+d}){match}")
            else:
                exp_note = ""
                if expected_coin is not None:
                    exp_note = f" (expected +{expected_coin})"
                print(f"    💸 Balance unchanged: {bal_after} — no credit yet?{exp_note}")
        elif bal_after is not None:
            print(f"    💸 Current balance: {bal_after}")
        if any_fail:
            print(" ⚠️ DONE (partial)")
        else:
            print("✅ DONE")
        self.watch_history[history_key] = {"watchedPct": 100, "time": tc_out_ms}
        rk = (str(series_id), str(ep_no))
        self.runtime_watch_counts[rk] = self.runtime_watch_counts.get(rk, 0) + 1
        self.watch_history_raw.append({
            "id": series_id,
            "series_id": series_id,
            "episodeNo": ep_no,
            "episode_no": ep_no,
            "watchedPct": 100,
            "progress": 100,
            "time": tc_out_ms,
        })
        return True, "done"

    def watch_series(self, series_id, max_episodes=None, delay_multiplier=0.0):
        series_info = self.get_series(series_id)
        if not series_info:
            print(f"[-] Series nahi mili: {series_id}")
            return 0
        actual_series_id = (
            series_info.get("_id") or series_info.get("id") or series_info.get("series_id") or series_id
        )
        if not (self.user_id and self.profile_id):
            try:
                self.get_user()
            except Exception:
                pass
            if not (self.user_id and self.profile_id):
                print("[-] User/Profile ID missing. Refresh login/token!")
                return 0
        try:
            self.get_profile()
        except Exception:
            pass
        total_eps = (
            series_info.get("numberOfEpisodes")
            or series_info.get("episodes_count")
            or series_info.get("totalEpisodes")
            or 0
        )
        try:
            total_eps = int(total_eps)
        except Exception:
            total_eps = 0
        print(f"\n[🎬] Watching: {series_info.get('title')} ({series_info.get('hindiTitle')})")
        print(f"    Total eps info: {total_eps}")
        print(f"    [+] Start campaign/task session...")
        try:
            self.watch_campaign_select_series(actual_series_id)
        except Exception as e:
            print(f"    [!] Campaign/start err (continue): {e}")

        episodes, reported_total = self.get_episodes(actual_series_id, page=1, page_size=max(50, total_eps or 500))
        if not episodes:
            print("[-] Koi episode nahi mila!")
            return 0
        if not total_eps:
            total_eps = reported_total or len(episodes)

        def _ep_key(e):
            n = e.get("episodeNo")
            try:
                return int(n)
            except Exception:
                try:
                    return float(n)
                except Exception:
                    return 0

        episodes_sorted = sorted(episodes, key=_ep_key)
        done = 0
        failed = 0
        skipped = 0
        limit = max_episodes if max_episodes else len(episodes_sorted)
        balance_before = None
        try:
            sc, bal_data = self._req("GET", "/coins/balance")
            if sc == 200 and isinstance(bal_data, dict):
                balance_before = bal_data.get("coins")
        except Exception:
            pass

        for idx, ep in enumerate(episodes_sorted[:limit], 1):
            try:
                ok, status = self.watch_episode(
                    ep, series_info, delay_multiplier=delay_multiplier
                )
            except Exception as e:
                import traceback
                print(f"\n  ❌ watch_ep exception: {e}")
                traceback.print_exc()
                ok, status = False, "exception"
            if status == "skip":
                skipped += 1
            elif ok:
                done += 1
            else:
                failed += 1
            if idx % 2 == 0 and idx != limit:
                try:
                    self._report_watch_progress_to_coins(actual_series_id, ep.get("episodeNo") or 0, 100)
                except Exception:
                    pass
            if idx % 3 == 0 and idx != limit:
                try:
                    self.claim_reward_task(series_id=actual_series_id)
                except Exception as e:
                    print(f"    [!] Claim error: {e}")
            if idx % 5 == 0 and idx != limit:
                try:
                    tasks_state = self.refresh_tasks_and_balance()
                    for t in tasks_state:
                        if t.get("series_id") == actual_series_id:
                            prog = t.get("progress", {})
                            print(f"    [Task state] watched={prog.get('watched')}/{prog.get('required')} state={t.get('state')}")
                except Exception:
                    pass
                print(f"    [Progress] Done: {done} | Skip: {skipped} | Fail: {failed}")
        print(f"\n[🏁] Series complete! Watched: {done} | Skip: {skipped} | Fail: {failed}")
        print(f"    [+] Final coin progress report...")
        try:
            last_ep = episodes_sorted[min(limit, len(episodes_sorted)) - 1]
            self._report_watch_progress_to_coins(actual_series_id, last_ep.get("episodeNo") or 0, 100)
        except Exception:
            pass
        time.sleep(0.4)
        bal_before_final = self.get_balance_silent()
        print(f"    [💰] Final reward claim (best-effort)...")
        try:
            self.claim_reward_task(series_id=actual_series_id)
        except Exception as e:
            print(f"    [!] Final claim error: {e}")
        time.sleep(0.5)
        bal_after_final = self.get_balance_silent()
        if bal_before_final is not None and bal_after_final is not None:
            delta = (bal_after_final or 0) - (bal_before_final or 0)
            print(f"    💸 Finalize delta: {bal_before_final} → {bal_after_final} ({delta:+d})")
        elif bal_after_final is not None:
            print(f"    💸 Final balance: {bal_after_final}")
        try:
            self.get_profile()
        except Exception:
            pass
        if balance_before is not None:
            cur = self.get_balance_silent() or 0
            print(f"    📊 Run totals: Coins gained = {(cur - balance_before):+d} (Before: {balance_before} After: {cur})  Watched: {done} Skip: {skipped} Fail: {failed}")
        return done

    def browse_and_watch_all_smart_repeat(self):
        print("\n" + "=" * 78)
        print(f" 🌐 BROWSE + SMART WATCH (MAX {MAX_WATCHES_PER_EP}x/episode REWARDS: 15→8→5→3)")
        print("=" * 78)
        print(f"[i] Rewards per episode: 1st=+15 | 2nd=+8 | 3rd=+5 | 4th=+3 coins (max {MAX_WATCHES_PER_EP}x)")
        print("[i] Campaign status + daily cap check...")
        cap_status = self.get_campaign_status()
        daily_used = cap_status.get("used", 0)
        daily_cap = cap_status.get("cap", 0)
        print("[i] Step 1: Saari available series list ho rahi hain...")
        all_series = self.get_all_series()
        if not all_series:
            print("[-] Koi series nahi mili! Try /webseries endpoints manually.")
            return
        try:
            self.get_profile()
        except Exception:
            pass
        watch_counts = self.get_watch_counts_from_profile()
        total_profile_watches = sum(watch_counts.values())
        maxed_out_eps = sum(1 for c in watch_counts.values() if c >= MAX_WATCHES_PER_EP)
        print(f"[i] Profile stats: {len(watch_counts)} unique (series,ep) pairs")
        print(f"[i]   Total completed watches (server): {total_profile_watches}")
        print(f"[i]   Fully maxed-out episodes (4x done): {maxed_out_eps}")
        remaining_slots = sum(
            max(0, MAX_WATCHES_PER_EP - c) for c in watch_counts.values()
        )
        print(f"[i]   Remaining reward slots for known eps: {remaining_slots}")

        def _ep_key(e):
            n = e.get("episodeNo")
            try:
                return int(n)
            except Exception:
                try:
                    return float(n)
                except Exception:
                    return 0

        def _series_watchable_slots(sid, n_total):
            if not n_total:
                return 0
            total = 0
            for eno in range(1, n_total + 1):
                cur = watch_counts.get((str(sid), str(eno)), 0)
                total += max(0, MAX_WATCHES_PER_EP - cur)
            return total

        print("\n" + "-" * 108)
        print(f"  {'#':>3s}  {'Series ID':<26s}  {'Title':<38s}  {'Eps':>4s}  {'Slots':>6s}  {'Hindi?'}")
        print("-" * 108)
        summaries = []
        for idx, s in enumerate(all_series, 1):
            sid = s.get("_id") or s.get("id") or s.get("series_id")
            if not sid:
                continue
            n_total = int(s.get("numberOfEpisodes") or s.get("totalEpisodes") or 0 or 0)
            slots = _series_watchable_slots(sid, n_total)
            title = s.get("title") or s.get("name") or "(no title)"
            title_trunc = title[:36] + (".." if len(title) > 36 else "")
            hindi = bool(s.get("hindiTitle"))
            print(f"  {idx:3d}  {str(sid):<26s}  {title_trunc:<38s}  {n_total:4d}  {slots:6d}  {'Yes' if hindi else 'No'}")
            summaries.append((idx, sid, s, n_total, slots, title))
        if not summaries:
            print("[-] Koi valid series summary nahi bana!")
            return

        print("\n Choose action:")
        print(f"   a) Auto-watch ALL series slots (har episode max {MAX_WATCHES_PER_EP} baar, rewards 15/8/5/3)")
        print(f"   b) Ek series choose karo — uske sab available reward slots dekho")
        print("   c) Bas browse karo — kuch nahi dekhna")
        choice = input("\nAction [a/b/c]: ").strip().lower()
        if choice == "c":
            print("[i] Cancelled.")
            return
        bal_start = self.get_balance_silent()
        total_watched_all = 0
        total_skip_all = 0
        total_fail_all = 0

        def _check_daily_cap(local_watched):
            if daily_cap and daily_cap > 0:
                total_today = daily_used + local_watched
                if total_today >= daily_cap or cap_status.get("reached") or cap_status.get("blockWatching"):
                    print(f"\n[🛑] DAILY CAP REACHED! ({daily_used}+{local_watched} >= cap {daily_cap}) Stop kar rahe hain.")
                    return True
            return False

        def _do_series(series_entry):
            nonlocal total_watched_all, total_skip_all, total_fail_all
            _, sid, sinfo, n_total, slots, title = series_entry
            print(f"\n▶️  [{title}] (id={sid}) available-slots~{slots}")
            actual_info = self.get_series(sid) or sinfo
            real_sid = (actual_info or {}).get("_id") or (actual_info or {}).get("id") or sid
            episodes, reported = self.get_episodes(real_sid, page=1, page_size=max(200, n_total or 500))
            episodes_sorted = sorted(episodes, key=_ep_key) if episodes else []
            if not episodes_sorted:
                print("  [!] Koi episode nahi — skip series.")
                return
            try:
                self.watch_campaign_select_series(real_sid)
            except Exception:
                pass
            series_done = series_skip = series_fail = 0
            any_progress_in_series = True
            while any_progress_in_series:
                any_progress_in_series = False
                for ep in episodes_sorted:
                    if _check_daily_cap(total_watched_all):
                        return
                    ep_no = ep.get("episodeNo") or ep.get("episode_no") or 0
                    key_pair = (str(real_sid), str(ep_no))
                    cur_count = watch_counts.get(key_pair, 0)
                    if cur_count >= MAX_WATCHES_PER_EP:
                        continue
                    nth = cur_count + 1
                    try:
                        ok, status = self.watch_episode(
                            ep, actual_info or sinfo,
                            allow_repeat=True,
                            nth_watch=nth,
                        )
                    except Exception as e:
                        import traceback
                        print(f"\n  ❌ watch_ep exception: {e}")
                        traceback.print_exc()
                        ok, status = False, "exception"
                    if status == "skip":
                        series_skip += 1
                    elif ok:
                        series_done += 1
                        watch_counts[key_pair] = nth
                        any_progress_in_series = True
                    else:
                        series_fail += 1
            total_watched_all += series_done
            total_skip_all += series_skip
            total_fail_all += series_fail
            print(f"  [Series summary] Watched: {series_done} Skip: {series_skip} Fail: {series_fail}")
            try:
                self.get_profile()
            except Exception:
                pass
            try:
                refreshed_counts = self.get_watch_counts_from_profile()
                for k, c in refreshed_counts.items():
                    if c > watch_counts.get(k, 0):
                        watch_counts[k] = c
            except Exception:
                pass

        if choice == "b":
            pick = input("Series number (from # column): ").strip()
            try:
                ipick = int(pick)
            except Exception:
                print("[-] Invalid number.")
                return
            entry = None
            for s in summaries:
                if s[0] == ipick:
                    entry = s
                    break
            if not entry:
                print("[-] Number list me nahi hai.")
                return
            _do_series(entry)
        elif choice == "a":
            print(f"\n[🚀] SMART REPEAT MODE START! (max {MAX_WATCHES_PER_EP} watches/ep) | Total series: {len(summaries)}")
            max_eps_per_series_raw = input("Max WATCHES per series? (empty = no limit per series): ").strip()
            max_eps_per_series = None
            if max_eps_per_series_raw:
                try:
                    max_eps_per_series = int(max_eps_per_series_raw)
                except Exception:
                    print("[!] Invalid number — no limit applied.")
            stop_after_raw = input("Rukne se pehle kitne TOTAL watches karne hain? (empty = daily cap tak): ").strip()
            stop_after = None
            if stop_after_raw:
                try:
                    stop_after = int(stop_after_raw)
                except Exception:
                    print("[!] Invalid number — no limit applied.")
            for idx, s in enumerate(summaries, 1):
                if stop_after is not None and total_watched_all >= stop_after:
                    print(f"\n[🛑] Stop limit reached ({stop_after} watched total).")
                    break
                print(f"\n=== Series {idx}/{len(summaries)} ===")
                try:
                    _, sid, sinfo, n_total, slots, title = s
                    real_info = self.get_series(sid) or sinfo
                    real_sid = (real_info or {}).get("_id") or (real_info or {}).get("id") or sid
                    episodes, _ = self.get_episodes(real_sid, page=1, page_size=max(200, n_total or 500))
                    episodes_sorted = sorted(episodes, key=_ep_key) if episodes else []
                    if episodes_sorted:
                        watched_here = 0
                        skip_here = 0
                        fail_here = 0
                        try:
                            self.watch_campaign_select_series(real_sid)
                        except Exception:
                            pass
                        cap_txt = f"up to {max_eps_per_series} watches " if max_eps_per_series else f"{MAX_WATCHES_PER_EP}x per episode "
                        print(f"▶️  [{title}] (id={real_sid}) — limiting {cap_txt}(slots~{slots})")
                        any_series_progress = True
                        while any_series_progress:
                            any_series_progress = False
                            for ep in episodes_sorted:
                                if _check_daily_cap(total_watched_all):
                                    break
                                if max_eps_per_series and watched_here >= max_eps_per_series:
                                    break
                                if stop_after is not None and total_watched_all >= stop_after:
                                    break
                                ep_no = ep.get("episodeNo") or ep.get("episode_no") or 0
                                key_pair = (str(real_sid), str(ep_no))
                                cur_count = watch_counts.get(key_pair, 0)
                                if cur_count >= MAX_WATCHES_PER_EP:
                                    continue
                                nth = cur_count + 1
                                try:
                                    ok, status = self.watch_episode(
                                        ep, real_info or sinfo,
                                        allow_repeat=True, nth_watch=nth,
                                    )
                                except Exception as e:
                                    import traceback
                                    print(f"\n  ❌ watch_ep exception: {e}")
                                    traceback.print_exc()
                                    ok, status = False, "exception"
                                if status == "skip":
                                    skip_here += 1
                                elif ok:
                                    watched_here += 1
                                    total_watched_all += 1
                                    watch_counts[key_pair] = nth
                                    any_series_progress = True
                                else:
                                    fail_here += 1
                        total_skip_all += skip_here
                        total_fail_all += fail_here
                        print(f"  [Series summary] Watched: {watched_here} Skip: {skip_here} Fail: {fail_here}")
                    else:
                        _do_series(s)
                except Exception as e:
                    print(f"\n❌ Series loop exception: {e}")
                    import traceback
                    traceback.print_exc()
                    continue
        else:
            print("[-] Unknown choice.")
            return

        try:
            self.get_profile()
        except Exception:
            pass
        bal_end = self.get_balance_silent()
        if bal_start is not None and bal_end is not None:
            print(f"\n🏁 FINAL SMART-REPEAT RUN COMPLETE (max {MAX_WATCHES_PER_EP}x per episode)")
            print(f"   Watched total: {total_watched_all}   Skip total: {total_skip_all}   Fail total: {total_fail_all}")
            print(f"   Balance: {bal_start} → {bal_end}   Delta: {(bal_end - bal_start):+d} coins")
            avg = (bal_end - bal_start) / total_watched_all if total_watched_all else 0
            if avg:
                print(f"   Avg coins/watch: {avg:.1f}  (ideal max = {_expected_reward(1)+_expected_reward(2)+_expected_reward(3)+_expected_reward(4)} coins/episode for 4x)")
        else:
            print(f"\n🏁 RUN DONE: Watched={total_watched_all} Skip={total_skip_all} Fail={total_fail_all}")

    browse_and_watch_all_non_repeat = browse_and_watch_all_smart_repeat

    def unlock_episode(self, series_id, ep_id, ep_no, playback_url=None):
        if not (series_id and ep_id):
            return False
        ok = False
        unlock_candidates = [
            ("POST", f"/episodes/{ep_id}/unlock", {"series_id": series_id, "episodeNo": ep_no, "campaign": False}),
            ("POST", "/episodes/unlock", {"series_id": series_id, "episode_id": ep_id, "episodeNo": ep_no, "campaign": False}),
            ("POST", f"/webseries/{series_id}/episodes/{ep_no}/unlock", {"campaign": False}),
            ("POST", "/coins/unlock-episode", {"series_id": series_id, "episode_id": ep_id, "episodeNo": ep_no}),
            ("POST", "/watch-campaign/unlock", {"seriesId": series_id, "episode_id": ep_id, "campaign": False}),
        ]
        for method, path, body in unlock_candidates:
            try:
                sc, d = self._req(
                    method, path,
                    headers={"content-type": "application/json; charset=utf-8"},
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                )
                if sc and sc < 500 and isinstance(d, dict):
                    if d.get("success") is True:
                        ok = True
                        break
                    if sc == 200 and d.get("unlocked"):
                        ok = True
                        break
                    if sc == 200 and "success" not in d:
                        ok = True
                        break
            except Exception:
                continue
        if playback_url:
            try:
                self.verify_cdn_bypass(playback_url, silent=True)
            except Exception:
                pass
        return ok

    def list_locked_episodes(self, series_id):
        episodes, _ = self.get_episodes(series_id, page=1, page_size=500)
        if not episodes:
            print("[-] Episodes list nahi mili!")
            return
        locked = []
        unlocked = []
        for e in episodes:
            ep_no = e.get("episodeNo") or e.get("episode_no") or "?"
            unlocked_flag = e.get("coinUnlocked", None)
            if unlocked_flag is False or e.get("locked") is True:
                locked.append((ep_no, e.get("_id") or e.get("id"), e))
            else:
                unlocked.append(ep_no)
        print(f"\n[🔒] Series: {series_id}")
        print(f"    Total Episodes: {len(episodes)}  |  ✅ Unlocked: {len(unlocked)}  |  🔒 Locked: {len(locked)}")
        if locked:
            print(f"\n    LOCKED Episodes:")
            for i, (eno, eid, raw) in enumerate(locked, 1):
                price = raw.get("coinPrice") or raw.get("price") or "?"
                pu = raw.get("playbackUrl") or ""
                print(f"      {i:2d}. Ep {eno:<4s}  (id={eid})  Price: {price}  URL={pu[:70]}{'..' if len(pu) > 70 else ''}")
        else:
            print("    🎉 Sab episodes unlocked hain!")
        return locked

    def unlock_series_locked(self, series_id):
        locked = self.list_locked_episodes(series_id)
        if not locked:
            return 0
        print(f"\n[🔓] Sab locked episodes unlock karne ki koshish kar raha hun ({len(locked)} total)...")
        done = 0
        for eno, eid, raw in locked:
            pu = raw.get("playbackUrl")
            try:
                ok = self.unlock_episode(series_id, eid, eno, playback_url=pu)
                if ok:
                    print(f"    ✅ Ep {eno} unlock ho gaya!")
                    done += 1
                else:
                    print(f"    ❌ Ep {eno} unlock NAHI hua.")
            except Exception as e:
                print(f"    ❌ Ep {eno} error: {e}")
            time.sleep(0.3)
        print(f"\n[🏁] Unlock complete: {done}/{len(locked)}")
        return done

    def verify_cdn_bypass(self, url, silent=False):
        if not url:
            if not silent:
                print("[-] URL nahi di!")
            return False
        if not silent:
            print(f"[🛡️] CDN Bypass Check kar raha hun: {url[:90]}{'..' if len(url) > 90 else ''}")
        test_urls = [url]
        import re as _re
        def _add_variants(u):
            out = [u]
            q = "?" in u
            sep = "&" if q else "?"
            for suffix in ["ignore_sign=1", "nosig=1", "bypass=1", "unsigned=1", "s=0", "sig=0", "token=bypass", "X-Amz-SignedHeaders=host"]:
                out.append(u + sep + suffix)
            m = _re.search(r"(https?://[^/]+/.+\.)(m3u8|mpd)", u)
            if m:
                base, ext = m.group(1), m.group(2)
                out.append(f"{base}master.{ext}")
                out.append(f"{base}list.{ext}")
                out.append(f"{base}index.{ext}")
            if "?" in u:
                clean = u.split("?")[0]
                out.insert(1, clean)
            return out
        variants = []
        for u in test_urls:
            variants.extend(_add_variants(u))
        seen = set()
        variants = [v for v in variants if not (v in seen or seen.add(v))]
        ok = False
        first_ok_url = None
        first_code = None
        for i, vu in enumerate(variants, 1):
            try:
                hdrs = {
                    "user-agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
                    "accept": "*/*",
                    "range": "bytes=0-8191",
                }
                r = self.session.get(vu, headers=hdrs, timeout=10, allow_redirects=True)
                code = r.status_code
                ct = (r.headers.get("content-type") or "").lower()
                length = len(r.content or b"")
                if not silent:
                    snippet = (r.content or b"")[:200].decode("utf-8", errors="replace").replace("\n", " ")
                    print(f"    [{i:2d}] HTTP {code}  len={length:<6d}  ct={ct[:30]:<30s}  {vu[:85]}{'..' if len(vu) > 85 else ''}")
                    if length and code < 400:
                        print(f"         snippet: {snippet[:120]}")
                if code < 400 and length > 0 and (
                    ".m3u8" in ct or ".mpd" in ct or "mpegurl" in ct or "dash+xml" in ct or length > 500 or ("#ext" in (r.text[:200].lower() if r.text else "") or "<mpd" in (r.text[:200].lower() if r.text else ""))
                ):
                    ok = True
                    if not first_ok_url:
                        first_ok_url = vu
                        first_code = code
                    if not silent:
                        print(f"    ✅ BYPASS OK! URL accessible without restrictions.")
                    break
            except Exception as e:
                if not silent:
                    print(f"    [{i:2d}] ❌ Network err: {type(e).__name__}: {str(e)[:80]}")
        if ok and not silent:
            print(f"\n  🎉 Final bypass URL (HTTP {first_code}):\n     {first_ok_url}")
        elif not ok and not silent:
            print(f"\n  ⚠️ Bypass NAHI mila — URL restricted hai.")
        return ok

    def interactive_login(self):
        opts = [str(x) for x in (opts or [])]
        if not opts:
            return -1, None
        try:
            api_key = self._gemini_key()
        except Exception:
            api_key = self._GEMINI_API_KEY_DEFAULT
        if not api_key or not str(api_key).strip():
            return -1, None
        cache = getattr(self, "gemini_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "gemini_cache", cache)
        qid = question.get("questionId") or ""
        qhi = str(question.get("questionHi") or "")
        qen = str(question.get("questionEn") or "")
        topic = str(question.get("topic") or "")
        cache_key = (qid, tuple(opts), qhi, qen, topic)
        if not force and cache_key in cache:
            return cache[cache_key]
        prompt_json = {
            "question_type": qtype or (question.get("type") or "unknown"),
            "question_hindi": qhi,
            "question_english": qen,
            "topic": topic,
            "options_indexed": [{"index": i, "text": str(o)} for i, o in enumerate(opts)],
            "instructions": [
                "This is a KIDS/LEVEL-1 multiple choice English question for a mini-quiz inside an Indian short-video app.",
                "Pick the SINGLE best correct option index. Typical question types:",
                "   - antonym / opposite / vilom shabd: pick the option that is the opposite of the target word in the question.",
                "   - synonym / paryay / similar / meaning: pick the option closest in meaning to the target word.",
                "   - grammar / sentence / correct / shudh karo: detect the underlined or capitalized error in the question sentence and pick the corrected form from options.",
                "   - fill blank / choose / multiple: read carefully and pick the correct word to insert or the correct choice.",
                "Return ONLY a strict JSON object, exactly one line, no markdown, no extra text, in this shape:",
                '{"chosenIndex": N, "reasoning": "short 6-15 word reasoning"}',
                "WHERE N MUST be an integer strictly between 0 and " + str(len(opts) - 1) + " inclusive.",
                "Never explain after the JSON. Never add ```json fences. Strict JSON only.",
            ],
        }
        prompt_lines = []
        prompt_lines.append("# MINI-QUIZ QUESTION (LEVEL-1 KIDS)")
        prompt_lines.append("")
        prompt_lines.append(f"**Type**: {prompt_json['question_type']}")
        prompt_lines.append(f"**Question (Hi)**: {prompt_json['question_hindi']}")
        prompt_lines.append(f"**Question (En)**: {prompt_json['question_english']}")
        if prompt_json["topic"]:
            prompt_lines.append(f"**Topic**: {prompt_json['topic']}")
        prompt_lines.append("")
        prompt_lines.append("**Options (index = N from 0)**:")
        for it in prompt_json["options_indexed"]:
            prompt_lines.append(f"  N={it['index']}  →  {it['text']}")
        prompt_lines.append("")
        prompt_lines.append("## INSTRUCTIONS")
        for inst in prompt_json["instructions"]:
            prompt_lines.append("- " + inst)
        prompt_lines.append("")
        prompt_lines.append("## OUTPUT (strict JSON only)")
        prompt = "\n".join(prompt_lines)
        chosen_index = -1
        reasoning = None
        used_mode = None
        try:
            import importlib as _imp
            try:
                _google_mod = _imp.import_module("google.genai")
            except Exception:
                _google_mod = None
            if _google_mod is not None:
                try:
                    client = _google_mod.Client(api_key=api_key)
                    interaction = client.interactions.create(
                        model="gemini-3.8-flash",
                        input=prompt,
                    )
                    raw_txt = getattr(interaction, "output_text", None) or ""
                    if raw_txt:
                        chosen_index, reasoning = self._parse_gemini_quiz_json(str(raw_txt), len(opts))
                        used_mode = "sdk:interactions.create"
                except Exception:
                    pass
        except Exception:
            pass
        if chosen_index < 0:
            try:
                import json as _json2
                rest_url = (
                    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key="
                    + api_key
                )
                body = {
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": prompt}],
                        }
                    ],
                    "generationConfig": {
                        "temperature": 0.05,
                        "maxOutputTokens": 256,
                        "responseMimeType": "application/json",
                        "responseSchema": {
                            "type": "object",
                            "properties": {
                                "chosenIndex": {"type": "integer", "minimum": 0, "maximum": max(0, len(opts) - 1)},
                                "reasoning": {"type": "string"},
                            },
                            "required": ["chosenIndex"],
                        },
                    },
                }
                resp = self.session.post(
                    rest_url,
                    headers={"content-type": "application/json; charset=utf-8"},
                    data=_json2.dumps(body, ensure_ascii=False).encode("utf-8"),
                    timeout=30,
                )
                if resp.status_code == 200:
                    rd = resp.json()
                    cands = rd.get("candidates") or []
                    if cands:
                        first = cands[0]
                        parts = (((first.get("content") or {}).get("parts") or [{}]) or [{}])
                        raw_txt = ""
                        for p in parts:
                            raw_txt += str(p.get("text") or "")
                        if raw_txt:
                            chosen_index, reasoning = self._parse_gemini_quiz_json(raw_txt, len(opts))
                            used_mode = "rest:generateContent"
            except Exception:
                pass
        if chosen_index < 0:
            import json as _json3
            try:
                rest_url2 = (
                    "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent?key="
                    + api_key
                )
                body2 = {
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.02, "maxOutputTokens": 256},
                }
                resp2 = self.session.post(
                    rest_url2,
                    headers={"content-type": "application/json; charset=utf-8"},
                    data=_json3.dumps(body2, ensure_ascii=False).encode("utf-8"),
                    timeout=30,
                )
                if resp2.status_code == 200:
                    rd2 = resp2.json()
                    cands2 = rd2.get("candidates") or []
                    if cands2:
                        parts2 = (cands2[0].get("content") or {}).get("parts") or [{}]
                        raw_txt2 = ""
                        for p in parts2:
                            raw_txt2 += str(p.get("text") or "")
                        if raw_txt2:
                            chosen_index, reasoning = self._parse_gemini_quiz_json(raw_txt2, len(opts))
                            used_mode = "rest:generateContent:3.8-flash"
            except Exception:
                pass
        if chosen_index is not None and 0 <= int(chosen_index) < len(opts):
            chosen_index = int(chosen_index)
            cache[cache_key] = (chosen_index, reasoning or used_mode)
            if not silent:
                why = reasoning or used_mode or ""
                print(f"           🤖 Gemini [{used_mode or 'gemini'}]: N={chosen_index} {('→ ' + why[:70]) if why else ''}")
            return chosen_index, reasoning or used_mode
        return -1, None

    def _parse_gemini_quiz_json(self, raw_text, n_options):
        import json as _jsonp
        import re as _rep
        if not raw_text:
            return -1, None
        chosen_index = -1
        reasoning = None
        s = str(raw_text).strip()
        s2 = s.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
        try:
            d = _jsonp.loads(s2)
            if isinstance(d, dict):
                if "chosenIndex" in d:
                    try:
                        chosen_index = int(d["chosenIndex"])
                    except Exception:
                        chosen_index = -1
                if "reasoning" in d:
                    reasoning = str(d["reasoning"])
                if isinstance(d.get("data"), dict):
                    if "chosenIndex" in d["data"]:
                        try:
                            chosen_index = int(d["data"]["chosenIndex"])
                        except Exception:
                            pass
        except Exception:
            pass
        if chosen_index is None or chosen_index < 0:
            m = _rep.search(r'"chosenIndex"\s*:\s*(-?\d+)', s2)
            if m:
                try:
                    chosen_index = int(m.group(1))
                except Exception:
                    pass
        if chosen_index is None or chosen_index < 0:
            mm = _rep.search(r"chosenIndex\s*[=:]\s*(\d+)", s2, flags=_rep.IGNORECASE)
            if mm:
                try:
                    chosen_index = int(mm.group(1))
                except Exception:
                    pass
        if chosen_index is None or chosen_index < 0:
            m2 = _rep.search(
                r"option\s*(?:index|number|choice)?(?:\s*(?:no|num|#))?\s*[:=]?\s*(?:is\s+)?(\d)",
                s2,
                flags=_rep.IGNORECASE,
            )
            if m2:
                try:
                    chosen_index = int(m2.group(1))
                except Exception:
                    pass
        if chosen_index is None or chosen_index < 0:
            m3 = _rep.search(r"\bN\s*=\s*(\d+)\b", s2)
            if m3:
                try:
                    chosen_index = int(m3.group(1))
                except Exception:
                    pass
        if reasoning is None:
            m_reason = _rep.search(r'"reasoning"\s*:\s*"([^"]{0,120})"', s2)
            if m_reason:
                reasoning = m_reason.group(1)
        if chosen_index is not None and 0 <= int(chosen_index) < n_options:
            return int(chosen_index), reasoning
        return -1, reasoning

    def quiz_get_status(self):
        sc, data = self._req("GET", "/quiz/status")
        if sc == 200 and isinstance(data, dict) and data.get("success"):
            return data
        print(f"[-] Quiz status fail: {sc} {str(data)[:120]}")
        return None

    def quiz_start_session(self):
        sc, data = self._req("POST", "/quiz/session/start",
                             headers={"content-type": "application/json; charset=utf-8"},
                             data=json.dumps({"campaign": False}).encode("utf-8"))
        if sc == 200 and isinstance(data, dict) and data.get("success"):
            return data
        print(f"[-] Quiz session start fail: {sc} {str(data)[:150]}")
        return None

    def quiz_submit_answer(self, session_id, question_id, chosen_index):
        body = {
            "sessionId": session_id,
            "questionId": question_id,
            "chosenIndex": int(chosen_index),
        }
        sc, data = self._req(
            "POST", "/quiz/session/answer",
            headers={"content-type": "application/json; charset=utf-8"},
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        )
        if sc == 200 and isinstance(data, dict):
            return data
        print(f"    [-] Quiz answer fail: HTTP {sc}  {str(data)[:150] if data else ''}")
        return None

    def quiz_claim_final(self, session_id):
        if not session_id:
            return False
        candidates = [
            ("POST", "/quiz/session/claim", {"sessionId": session_id, "campaign": False}),
            ("POST", "/quiz/session/complete", {"sessionId": session_id, "campaign": False}),
            ("POST", "/quiz/claim", {"sessionId": session_id, "campaign": False}),
            ("POST", f"/quiz/session/{session_id}/claim", None),
            ("POST", f"/quiz/session/{session_id}/finish", None),
            ("POST", "/coins/quiz-claim", {"sessionId": session_id, "type": "quiz", "campaign": False}),
        ]
        any_ok = False
        last_coins = None
        for method, path, body in candidates:
            try:
                payload_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
                sc, d = self._req(
                    method, path,
                    headers={"content-type": "application/json; charset=utf-8"} if body else {},
                    data=payload_bytes,
                )
                if sc and sc < 500 and isinstance(d, dict):
                    if d.get("success") is True:
                        last_coins = d.get("coins") or d.get("reward_coins") or d.get("coinsEarned") or d.get("reward") or 0
                        any_ok = True
                        break
                    if sc == 200 and "success" not in d:
                        any_ok = True
                        break
            except Exception:
                continue
        if any_ok and last_coins is not None:
            print(f"    [💰] Quiz claim response: +{last_coins} coins (best-effort endpoint)")
        return any_ok

    def _smart_quiz_pick_index(self, question, qbank=None):
        qbank = qbank or getattr(self, "quiz_qbank", {})
        qid = question.get("questionId") or ""
        opts = question.get("options") or []
        if not opts:
            return 0
        if qid and qid in qbank:
            memorized = qbank[qid]
            if 0 <= int(memorized) < len(opts):
                return int(memorized)
        qtype = (question.get("type") or "").lower()
        text_all = ""
        for field in ("questionHi", "questionEn", "topic"):
            v = question.get(field) or ""
            text_all += " " + str(v).lower()
        opt_lower = [str(o).lower() for o in opts]
        def _find(tokens_list):
            best = -1
            best_score = -1
            for i, ol in enumerate(opt_lower):
                s = 0
                for token_group in tokens_list:
                    if all(t in ol for t in token_group):
                        s += 3
                    for t in token_group:
                        if t in ol:
                            s += 1
                if s > best_score:
                    best_score = s
                    best = i
            return best if best_score > 0 else -1
        ANTONYM_PAIRS = [
            ({"up"}, {"down"}), ({"above", "over"}, {"below", "under"}), ({"on"}, {"off", "out"}),
            ({"fast", "quick"}, {"slow"}), ({"big", "large"}, {"small", "little"}),
            ({"good"}, {"bad"}), ({"hot"}, {"cold"}), ({"happy", "glad"}, {"sad", "unhappy"}),
            ({"light"}, {"heavy", "dark"}), ({"easy"}, {"hard", "difficult"}),
            ({"right"}, {"left", "wrong", "incorrect"}), ({"open"}, {"close", "shut"}),
            ({"new"}, {"old"}), ({"rich"}, {"poor"}), ({"tall"}, {"short"}),
            ({"clean"}, {"dirty"}), ({"loud"}, {"quiet", "soft"}), ({"strong"}, {"weak"}),
            ({"true"}, {"false"}), ({"high"}, {"low"}), ({"wide"}, {"narrow"}),
            ({"long"}, {"short"}), ({"thick"}, {"thin"}), ({"deep"}, {"shallow"}),
            ({"early"}, {"late"}), ({"young"}, {"old"}), ({"warm"}, {"cool"}),
            ({"bright"}, {"dull", "dim"}), ({"empty"}, {"full"}), ({"fat"}, {"thin", "slim"}),
            ({"beautiful", "pretty"}, {"ugly"}), ({"safe"}, {"dangerous"}),
            ({"smooth"}, {"rough"}), ({"sharp"}, {"blunt"}), ({"soft"}, {"hard", "firm"}),
            ({"poor"}, {"rich"}), ({"wet"}, {"dry"}), ({"begin", "start"}, {"end", "finish", "stop"}),
            ({"love"}, {"hate"}), ({"give"}, {"take"}), ({"push"}, {"pull"}),
            ({"come"}, {"go"}), ({"buy"}, {"sell"}), ({"win"}, {"lose"}),
            ({"day"}, {"night"}), ({"inside", "inner"}, {"outside", "outer"}),
            ({"front"}, {"back"}), ({"male", "he", "boy"}, {"female", "she", "girl"}),
            ({"many"}, {"few"}), ({"always"}, {"never"}), ({"often"}, {"rarely"}),
            ({"same"}, {"different"}), ({"simple"}, {"complex", "complicated"}),
            ({"expensive"}, {"cheap"}), ({"friendly"}, {"unfriendly", "hostile"}),
            ({"brave"}, {"cowardly", "afraid"}), ({"calm"}, {"anxious", "nervous"}),
            ({"polite"}, {"rude", "impolite"}), ({"honest"}, {"dishonest"}),
            ({"proud"}, {"humble", "modest"}), ({"lazy"}, {"hardworking", "active"}),
            ({"stupid", "foolish"}, {"clever", "intelligent", "smart"}),
            ({"weak"}, {"strong"}), ({"hungry"}, {"full", "satisfied"}),
            ({"thirsty"}, {"quenched", "full"}), ({"tired"}, {"energetic", "fresh"}),
            ({"ill", "sick"}, {"healthy", "well"}),
        ]
        SYNONYM_PAIRS = [
            ({"fast"}, {"quick", "rapid", "swift", "speedy"}),
            ({"big"}, {"large", "huge", "giant", "enormous", "massive"}),
            ({"small"}, {"little", "tiny", "mini", "short"}),
            ({"happy"}, {"glad", "joyful", "pleased", "cheerful"}),
            ({"sad"}, {"unhappy", "sorrowful", "gloomy"}),
            ({"beautiful"}, {"pretty", "lovely", "gorgeous", "stunning"}),
            ({"begin"}, {"start", "commence", "initiate"}),
            ({"end"}, {"finish", "complete", "conclude", "terminate", "stop"}),
            ({"help"}, {"aid", "assist", "support"}),
            ({"angry"}, {"furious", "annoyed", "irritated", "mad"}),
            ({"clever"}, {"smart", "intelligent", "bright", "sharp", "wise"}),
            ({"stupid"}, {"foolish", "dumb", "idiotic", "silly"}),
            ({"afraid"}, {"scared", "fearful", "terrified", "frightened"}),
            ({"tired"}, {"exhausted", "fatigued", "weary", "drained"}),
            ({"hungry"}, {"starving", "famished", "ravenous"}),
            ({"strong"}, {"powerful", "mighty", "robust", "sturdy"}),
            ({"rich"}, {"wealthy", "affluent", "prosperous"}),
            ({"poor"}, {"needy", "destitute", "impoverished"}),
            ({"quickly"}, {"rapidly", "swiftly", "speedily", "promptly"}),
            ({"slowly"}, {"gradually", "steadily", "leisurely"}),
            ({"important"}, {"significant", "crucial", "vital", "essential"}),
            ({"easy"}, {"simple", "effortless", "straightforward"}),
            ({"hard"}, {"difficult", "tough", "challenging", "demanding"}),
            ({"old"}, {"ancient", "aged", "elderly"}),
            ({"new"}, {"novel", "fresh", "modern", "recent"}),
            ({"hot"}, {"warm", "burning", "scorching"}),
            ({"cold"}, {"cool", "chilly", "freezing", "icy"}),
            ({"loud"}, {"noisy", "deafening", "thundering"}),
            ({"quiet"}, {"silent", "calm", "peaceful", "noiseless", "soft"}),
            ({"fast, quick rapid speedy"}, {"prompt", "instant"}),
            ({"big large huge"}, {"vast", "immense"}),
            ({"small little tiny"}, {"minute", "microscopic"}),
            ({"happy glad joyful"}, {"elated", "delighted", "content"}),
            ({"many"}, {"numerous", "plenty", "abundant", "several", "multiple"}),
            ({"few"}, {"several", "scanty", "limited", "sparse"}),
            ({"strange"}, {"odd", "weird", "peculiar", "unusual", "bizarre"}),
            ({"normal"}, {"ordinary", "common", "regular", "usual", "typical"}),
            ({"famous"}, {"well-known", "renowned", "popular", "celebrated"}),
            ({"honest"}, {"truthful", "sincere", "trustworthy", "genuine"}),
            ({"dishonest"}, {"deceitful", "fraudulent", "untrustworthy"}),
            ({"rude"}, {"impolite", "insolent", "ill-mannered", "disrespectful"}),
            ({"polite"}, {"courteous", "respectful", "well-mannered", "gentle"}),
            ({"brave"}, {"courageous", "fearless", "bold", "valiant"}),
            ({"cowardly"}, {"fearful", "timid", "shy", "scared"}),
            ({"calm"}, {"peaceful", "serene", "composed", "tranquil"}),
            ({"nervous anxious"}, {"tense", "worried", "restless", "uneasy"}),
            ({"friendly"}, {"kind", "pleasant", "warm", "affable", "sociable"}),
            ({"enemy"}, {"foe", "opponent", "rival", "adversary"}),
            ({"friend"}, {"companion", "pal", "buddy", "ally", "acquaintance"}),
            ({"enormous gigantic colossal"}, {"huge", "giant", "massive"}),
            ({"speak"}, {"say", "tell", "talk", "utter", "communicate"}),
            ({"walk"}, {"stroll", "stride", "march", "wander", "roam"}),
            ({"run"}, {"sprint", "dash", "race", "jog", "rush"}),
            ({"jump"}, {"leap", "hop", "bounce", "spring"}),
            ({"build"}, {"construct", "create", "make", "assemble", "erect"}),
            ({"destroy"}, {"ruin", "demolish", "break", "wreck", "smash"}),
            ({"beautiful pretty lovely"}, {"attractive", "handsome", "charming", "appealing"}),
            ({"ugly"}, {"unattractive", "hideous", "unsightly"}),
            ({"intelligent smart clever"}, {"brilliant", "gifted", "talented", "wise"}),
            ({"silly"}, {"foolish", "absurd", "ridiculous", "childish"}),
        ]
        GRAMMAR_HINTS = [
            ("doesn't", [r"don't.*she\b", r"don't.*he\b", r"don't.*it\b", r"don't.*name", r"does not.*she\b", r"does not.*he\b"]),
            ("doesn't like", [r"don't like.*she\b", r"don't like.*he\b", r"don't like.*it\b"]),
            ("doesn't", [r"doesn't"]),
            ("has", [r"have.*she\b", r"have.*he\b", r"have.*it\b"]),
            ("is", [r"are.*she\b", r"are.*he\b", r"are.*it\b"]),
            ("was", [r"were.*she\b", r"were.*he\b", r"were.*it\b"]),
            ("goes", [r"go.*she\b", r"go.*he\b", r"go.*it\b"]),
            ("plays", [r"play.*she\b", r"play.*he\b", r"play.*it\b"]),
            ("likes", [r"like.*she\b", r"like.*he\b", r"like.*it\b"]),
            ("reads", [r"read.*she\b", r"read.*he\b", r"read.*it\b"]),
            ("writes", [r"write.*she\b", r"write.*he\b", r"write.*it\b"]),
            ("watches", [r"watch.*she\b", r"watch.*he\b", r"watch.*it\b"]),
            ("teaches", [r"teach.*she\b", r"teach.*he\b", r"teach.*it\b"]),
            ("a, an: an before vowels", [r"[ a]an? +[aeiou]", r"[ a]an? +honest", r"[ a]an? +hour", r"[ a]an? +heir"]),
            ("your vs you're", [r"you're", r"your\b"]),
            ("their vs there vs they're", [r"they're", r"their", r"there"]),
            ("its vs it's", [r"it's", r"its\b"]),
            ("than vs then", [r"\bthan\b.*compar", r"\bthen\b.*sequen", r"better.*then", r"better.*than"]),
            ("to too two", [r"\bto\b", r"\btoo\b", r"\btwo\b"]),
            ("i before e except after c", [r"believe", r"receive", r"ceiling", r"friend", r"neighbor", r"weight", r"either", r"neither"]),
            ("double consonants ed/ing for short vowel + single consonant", [r"stop.*stoped", r"stop.*stopped", r"run.*runing", r"run.*running", r"beg.*beging", r"beg.*beginning", r"swim.*swiming", r"swim.*swimming", r"prefer.*prefered", r"prefer.*preferred"]),
            ("plural: -es after s/sh/ch/x/z", [r"box.*boxs", r"box.*boxes", r"bus.*buses", r"bus.*buss", r"church.*churches", r"church.*churchs", r"dish.*dishes", r"dish.*dishs", r"kiss.*kisses", r"kiss.*kisss"]),
            ("past tense: -ed regular; irregular known forms", [r"goed", r"went", r"seed", r"saw", r"goed", r"did\b", r"done\b", r"breaked", r"broke\b", r"broken\b", r"eated", r"ate\b", r"eaten\b", r"drinked", r"drank\b", r"drunk\b", r"runned", r"ran\b", r"runned", r"swimmed", r"swam\b", r"beginned", r"began\b", r"begun\b", r"buyed", r"bought\b", r"catched", r"caught\b", r"teached", r"taught\b", r"bringed", r"brought\b", r"thinked", r"thought\b", r"sitted", r"sat\b", r"standed", r"stood\b", r"sleeped", r"slept\b", r"keeped", r"kept\b", r"feeled", r"felt\b", r"leaved", r"left\b", r"losed", r"lost\b", r"finded", r"found\b", r"grinded", r"ground\b", r"growed", r"grew\b", r"grown\b", r"knowed", r"knew\b", r"known\b", r"throwed", r"threw\b", r"thrown\b", r"drawed", r"drew\b", r"drawn\b", r"flyed", r"flew\b", r"flown\b", r"hided", r"hid\b", r"hidden\b", r"leaded", r"led\b", r"readed", r"read\b", r"rided", r"rode\b", r"ridden\b", r"rised", r"rose\b", r"risen\b", r"send", r"sent\b", r"shaked", r"shook\b", r"shaken\b", r"singed", r"sang\b", r"sung\b", r"speaked", r"spoke\b", r"spoken\b", r"stealed", r"stole\b", r"stolen\b", r"swinged", r"swung\b", r"telled", r"told\b", r"taked", r"took\b", r"taken\b", r"waked", r"woke\b", r"woken\b", r"wear", r"wore\b", r"worn\b", r"winned", r"won\b", r"writed", r"wrote\b", r"written\b"]),
        ]
        idx = -1
        def _match_any(text, patterns):
            import re as _re2
            for p in patterns:
                if _re2.search(p, text):
                    return True
            return False
        if qtype in ("antonym", "opposite", "vilom"):
            target_word = None
            import re as _re3
            m = _re3.search(r"['\"\(]?\s*([a-z]{2,25})\s*['\"\)]?\s*(का|ke|के|का|की|ki|ka|ne|ने)?\s*(विलोम|vilom|opposite|उल्टा)", text_all)
            if not m:
                m2 = _re3.search(r"([a-z]{2,25})", (question.get("questionEn") or question.get("questionHi") or "").lower())
                if m2:
                    target_word = m2.group(1)
            else:
                target_word = m.group(1)
            if target_word:
                tw = {target_word}
                for src_set, ant_set in ANTONYM_PAIRS:
                    if any(w in tw or any(w in t for t in [s for s in src_set]) for w in src_set) or any(s == target_word for s in src_set):
                        candidates_list = []
                        for ant in ant_set:
                            best_in_opts = -1
                            best_len = -1
                            for i, ol in enumerate(opt_lower):
                                if ant in ol or ol in ant:
                                    ln = max(len(ant), len(ol))
                                    if ln > best_len:
                                        best_len = ln
                                        best_in_opts = i
                            if best_in_opts >= 0:
                                candidates_list.append((best_len, best_in_opts, ant))
                        if candidates_list:
                            candidates_list.sort(reverse=True)
                            return candidates_list[0][1]
                neg_hit = -1
                for i, ol in enumerate(opt_lower):
                    for _, ant_set in ANTONYM_PAIRS:
                        for a in ant_set:
                            if ("not " + target_word) in ol or ("non-" + target_word) in ol or ("im" + target_word) in ol or ("un" + target_word) in ol or ("in" + target_word) in ol or ("dis" + target_word) in ol or ("ir" + target_word) in ol or ("il" + target_word) in ol:
                                neg_hit = i
                                break
                if neg_hit >= 0:
                    return neg_hit
        if qtype in ("synonym", "paryay", "similar", "meaning", "samarth", "paribhasha"):
            target_word = None
            import re as _re4
            mm = _re4.search(r"['\"\(]?\s*([a-z]{2,25})\s*['\"\)]?", (question.get("questionEn") or question.get("questionHi") or "").lower())
            if mm:
                target_word = mm.group(1)
            else:
                m2 = _re4.search(r"([a-z]{2,25})", text_all)
                if m2:
                    target_word = m2.group(1)
            if target_word:
                tw = {target_word}
                for src_set, syn_set in SYNONYM_PAIRS:
                    src_match = False
                    for s in src_set:
                        if s == target_word or s in text_all or target_word in s:
                            src_match = True
                            break
                    if src_match:
                        candidates_list = []
                        for sy in syn_set:
                            best_in_opts = -1
                            best_len = -1
                            for i, ol in enumerate(opt_lower):
                                if sy in ol or ol in sy or (ol.strip() == sy):
                                    ln = max(len(sy), len(ol))
                                    if ln > best_len:
                                        best_len = ln
                                        best_in_opts = i
                        if best_in_opts >= 0:
                            candidates_list.append((best_len, best_in_opts, sy))
                        if candidates_list:
                            candidates_list.sort(reverse=True)
                            return candidates_list[0][1]
        if qtype in ("grammar", "grammarfix", "sentence", "correct", "shudh", "sahi"):
            sentence = (question.get("questionHi") or question.get("questionEn") or "").lower()
            for correct_phrase, pattern_list in GRAMMAR_HINTS:
                if _match_any(sentence, pattern_list):
                    for i, ol in enumerate(opt_lower):
                        if correct_phrase.lower() in ol or correct_phrase.lower().replace(" ", "") in ol.replace(" ", ""):
                            return i
            for i, ol in enumerate(opt_lower):
                good_sigs = [
                    ("doesn't", ["don't"]), ("has", ["have"]), ("is", ["are"]), ("was", ["were"]),
                    ("likes", ["like "]), ("goes", ["go "]), ("plays", ["play "]), ("reads", ["read "]),
                    ("writes", ["write "]), ("watches", ["watch "]), ("teaches", ["teach "]),
                    ("an honest", ["a honest"]), ("an hour", ["a hour"]), ("an heir", ["a heir"]),
                    ("an apple", ["a apple"]), ("an egg", ["a egg"]), ("an idea", ["a idea"]),
                    ("an orange", ["a orange"]), ("an umbrella", ["a umbrella"]),
                    ("your book", ["you're book"]), ("they're", ["their ", "there "]),
                    ("its tail", ["it's tail"]), ("better than", ["better then"]),
                    ("went", ["goed"]), ("saw", ["seed"]), ("did", ["doed"]),
                    ("broke", ["breaked"]), ("broken", ["breaked"]), ("ate", ["eated"]),
                    ("drank", ["drinked"]), ("ran", ["runned"]), ("began", ["beginned"]),
                    ("bought", ["buyed"]), ("caught", ["catched"]), ("taught", ["teached"]),
                    ("brought", ["bringed"]), ("thought", ["thinked"]), ("sat", ["sitted"]),
                    ("stood", ["standed"]), ("slept", ["sleeped"]), ("kept", ["keeped"]),
                    ("felt", ["feeled"]), ("left", ["leaved"]), ("lost", ["losed"]),
                    ("found", ["finded"]), ("knew", ["knowed"]), ("rode", ["rided"]),
                    ("taken", ["taked"]), ("took", ["taked"]), ("wrote", ["writed"]),
                    ("boxes", ["boxs"]), ("buses", ["buss"]), ("churches", ["churchs"]),
                    ("dishes", ["dishs"]), ("kisses", ["kisss"]),
                    ("stopped", ["stoped"]), ("running", ["runing"]), ("beginning", ["begining"]),
                    ("swimming", ["swiming"]), ("preferred", ["prefered"]),
                ]
                for good, bad_list in good_sigs:
                    if good in ol:
                        bad_in_sent = any(b in sentence for b in bad_list)
                        if bad_in_sent:
                            return i
        if qtype in ("fill", "blank", "choose", "multiple"):
            for src_set, syn_set in SYNONYM_PAIRS:
                if any(s in text_all for s in src_set):
                    for sy in syn_set:
                        for i, ol in enumerate(opt_lower):
                            if sy in ol or ol in sy:
                                return i
        local_idx = idx
        if idx < 0:
            fallback = _find(
                [
                    list({opt}) for opt in opt_lower
                ]
            )
            if fallback >= 0:
                idx = fallback
        try:
            gem_idx, _gem_reason = self._ask_gemini_quiz(
                question, opts, qtype=qtype, silent=False, force=False
            )
            if 0 <= int(gem_idx) < len(opts):
                if local_idx < 0:
                    idx = int(gem_idx)
                elif local_idx != int(gem_idx):
                    print(f"           ⚠️ Gemini says N={gem_idx}; local rules say N={local_idx}. Trusting GEMINI (higher accuracy).")
                    idx = int(gem_idx)
        except Exception:
            pass
        if idx < 0:
            idx = 0
        if idx >= len(opts):
            idx = 0
        return int(idx)

    def quiz_auto_complete_all_available(self):
        print("\n" + "=" * 78)
        print("  🧠 MINI QUIZ AUTO-COMPLETE (100% CORRECT ANSWERS = MAX COINS)")
        print("=" * 78)
        if not getattr(self, "quiz_qbank", None):
            self.quiz_qbank = {}
        bal_before = self.get_balance_silent()
        status = self.quiz_get_status()
        if not status:
            print("[-] Quiz status nahi mila — feature disabled ya network error.")
            return False
        enabled = status.get("enabled")
        daily = status.get("dailyAttempts") or {}
        limit = int(daily.get("limit") or 0)
        used = int(daily.get("used") or 0)
        exhausted = bool(daily.get("exhausted"))
        current_level = status.get("currentLevel")
        level_cfg = status.get("levelConfig") or {}
        per_correct = int(level_cfg.get("coinsPerCorrect") or 5)
        qcount = int(level_cfg.get("questionsCount") or 20)
        pass_pct = int(level_cfg.get("passPct") or 40)
        print(f"[🎯] Quiz enabled: {'YES' if enabled else 'NO'}  |  Level: {current_level}")
        print(f"[📆] Daily attempts: {used}/{limit}  Exhausted: {'YES' if exhausted else 'NO'}")
        print(f"[💰] {qcount} questions × {per_correct} coins = {qcount * per_correct} coins/session  (pass: {pass_pct}%)")
        totals = status.get("totals") or {}
        print(f"[📊] Lifetime — answered: {totals.get('answers',0)}  correct: {totals.get('correct',0)}  coins: {totals.get('coins',0)}")
        levels_done = status.get("levelsCompleted") or []
        if levels_done:
            print(f"[🏆] Levels completed: {levels_done}")
        if not enabled:
            print("[-] Quiz abhi enabled NAHI hai app side se. Skip.")
            return False
        if exhausted or used >= limit:
            print(f"[🛑] Aaj ke saare {limit} attempts khatam ho gaye. Kal phir aaiye!")
            return False
        max_sessions = limit - used
        print(f"[🚀] Aaj {max_sessions} session(s) baaki hain. Har session 100% correct answers ke saath play karunga...\n")
        sessions_played = 0
        all_sessions_total_coins = 0
        for sess_num in range(1, max_sessions + 1):
            print(f"  ━━━━━━━━━━━━━━━━━━━━ SESSION {sess_num}/{max_sessions} ━━━━━━━━━━━━━━━━━━━━")
            start = self.quiz_start_session()
            if not start:
                print("  [-] Session start nahi ho paya. Next try.")
                continue
            sess = start.get("session") or {}
            session_id = sess.get("sessionId")
            hearts = int(sess.get("hearts") or 3)
            question = start.get("question") or {}
            q_idx = 0
            correct_count = 0
            coins_accrued = 0
            total_answered = 0
            already_earned_total = 0
            while question and isinstance(question, dict):
                q_idx += 1
                qid = question.get("questionId") or ""
                q_num = question.get("index", q_idx)
                q_total = question.get("total", qcount)
                qtype = question.get("type", "?")
                opts = question.get("options") or []
                choice = self._smart_quiz_pick_index(question, self.quiz_qbank)
                choice = max(0, min(int(choice), len(opts) - 1 if opts else 0))
                choice_txt = opts[choice] if 0 <= choice < len(opts) else "?"
                qhi = (question.get("questionHi") or "")[:80]
                qhi = qhi + (".." if len((question.get("questionHi") or "")) > 80 else "")
                print(f"  [Q{q_num:>2d}/{q_total}] {qtype:>8s} | {qhi}")
                print(f"           Pick: [{choice}] {str(choice_txt)[:90]}")
                ans = self.quiz_submit_answer(session_id, qid, choice)
                if not isinstance(ans, dict):
                    print(f"             ❌ No response. Break session.")
                    break
                if not ans.get("success"):
                    msg = ans.get("message") or str(ans)[:100]
                    print(f"             ❌ Failed: {msg}")
                    break
                correct_flag = bool(ans.get("correct"))
                correct_idx = ans.get("correctIndex")
                if correct_idx is not None:
                    try:
                        correct_idx = int(correct_idx)
                        if qid:
                            self.quiz_qbank[qid] = correct_idx
                    except Exception:
                        pass
                ce = int(ans.get("coinsEarned") or 0)
                csf = int(ans.get("coinsSoFar") or 0)
                ae = bool(ans.get("alreadyEarned"))
                hearts = int(ans.get("hearts") or hearts)
                total_answered += 1
                coins_accrued = csf if csf else coins_accrued + ce
                if correct_flag:
                    correct_count += 1
                    all_sessions_total_coins += ce
                    already_earned_total += int(ans.get("coinsEarned") or 0) if ae else 0
                    note = " (ALREADY EARNED — credited previously)" if ae else f" +{ce} coins  [so far +{coins_accrued}]"
                    explain = ""
                    if ans.get("explanationHi"):
                        explain = " | " + str(ans.get("explanationHi"))[:60]
                    print(f"             ✅ CORRECT!{note}{explain}  (hearts: {hearts})")
                else:
                    correct_opt = opts[correct_idx] if (correct_idx is not None and 0 <= correct_idx < len(opts)) else "?"
                    our_pick_status = ""
                    if (correct_idx is not None) and choice != correct_idx:
                        our_pick_status = f"  👉 We picked [{choice}] = {str(opts[choice])[:40]}; actually CORRECT = [{correct_idx}] = {str(correct_opt)[:50]}"
                    print(f"             ❌ WRONG! +0 coins  (hearts: {hearts}){our_pick_status}")
                ad_gate = ans.get("adGatePending")
                if ad_gate:
                    print(f"             📺 Ad-gate detected. Attempting bypass/report-watched variants...")
                    try:
                        ad_candidates = [
                            ("POST", "/quiz/session/ad-watched", {"sessionId": session_id, "campaign": False}),
                            ("POST", "/quiz/ad-watched", {"sessionId": session_id, "campaign": False}),
                            ("POST", f"/quiz/session/{session_id}/ad-gate/skip", {}),
                        ]
                        for m, p, b in ad_candidates:
                            sc_a, d_a = self._req(
                                m, p,
                                headers={"content-type": "application/json; charset=utf-8"},
                                data=json.dumps(b, ensure_ascii=False).encode("utf-8"),
                            )
                            if sc_a == 200 and isinstance(d_a, dict) and d_a.get("success"):
                                print(f"             📺 Ad-gate handled ({m} {p})")
                                ans = d_a
                                break
                    except Exception:
                        pass
                next_wrap = ans.get("next") or {}
                if isinstance(next_wrap, dict) and "question" in next_wrap and isinstance(next_wrap.get("question"), dict):
                    question = next_wrap["question"]
                else:
                    question = None
            sess_summary_prefix = "  ┗━━━ "
            pass_flag = ""
            pct = int((correct_count / total_answered) * 100) if total_answered else 0
            if total_answered >= qcount:
                if pct >= pass_pct:
                    pass_flag = "  🏆 PASSED!"
                else:
                    pass_flag = f"  ❌ FAILED ({pct}% < {pass_pct}% required)"
            print(f"{sess_summary_prefix}Session done: {correct_count}/{total_answered} correct ({pct}%)  Coins this session: +{coins_accrued}  {pass_flag}")
            if coins_accrued or total_answered:
                print(f"{sess_summary_prefix}Claiming final reward / session wrap-up...")
                self.quiz_claim_final(session_id)
            sessions_played += 1
            time.sleep(0.7)
        print("\n" + "-" * 78)
        print(f"  📊 QUIZ RUN SUMMARY: {sessions_played}/{max_sessions} session(s) completed.")
        bal_after = self.get_balance_silent()
        if bal_before is not None and bal_after is not None:
            delta = (bal_after or 0) - (bal_before or 0)
            est = all_sessions_total_coins
            print(f"     Balance: {bal_before} → {bal_after}   Actual delta: {delta:+d} coins   Expected from quiz: ~+{est}")
        elif bal_after is not None:
            print(f"     Balance: {bal_after}")
        print("=" * 78)
        return sessions_played > 0

    def interactive_login(self):
        print("\n=== MiniPix LOGIN ===")
        print("1) Phone + OTP se login")
        print("2) Direct Token se login")
        ch = input("Choose option [1/2]: ").strip()
        if ch == "1":
            phone = input("Phone (+91...): ").strip()
            if not phone.startswith("+"):
                phone = "+91" + phone.lstrip("0")
            st = self.login_otp_generate(phone)
            if not st:
                return False
            otp = input("OTP daalo: ").strip()
            return self.login_otp_verify(st, otp)
        elif ch == "2":
            token = input("Bearer Token (paste): ").strip()
            if token.lower().startswith("bearer "):
                token = token[7:].strip()
            user_id = input("User ID (empty = auto fetch): ").strip() or None
            profile_id = input("Profile ID (empty = auto fetch): ").strip() or None
            return self.login_with_token(token, user_id, profile_id)
        print("[-] Invalid option!")
        return False


def banner():
    print("=" * 60)
    print("  MiniPix Auto Watch - Episode Automation Bot")
    print("=" * 60)


def main():
    banner()
    bot = MiniPixAuto()

    if not bot.access_token:
        if not bot.interactive_login():
            print("[-] Login nahi ho paya! Exit.")
            sys.exit(1)
    else:
        print(f"[+] Saved token se login: User={bot.user_id}")
        if not bot.get_user():
            if not bot.interactive_login():
                sys.exit(1)

    print()
    bot.open_app()
    bot.get_balance()
    bot.get_campaign_status()

    while True:
        print("\n" + "-" * 55)
        print("MENU (App Update ke baad: Watch Tasks REMOVED)")
        print("-" * 55)
        print("  1) 💰 Refresh Balance")
        print("  2) 📋 [OLD] Watch Ladder tasks (AB KAM NAHI KARTA)")
        print("  3) 📊 Detailed Series-episodes Pending/Watched Report")
        print("  4) 🎯 Direct Series ID se episodes dekho")
        print("  7) 🕒 Watch History + Refresh Profile")
        print("  8) 🔒 Locked Episodes list of a Series")
        print("  9) 🔓 Unlock all locked episodes of a Series")
        print(" 10) 🛡️  Verify CDN Bypass on a Playback URL")
        print(" 11) ⭐ 🌐 Browse ALL Series + SMART 4x REPEAT Auto-Watch")
        print("           (YE OPTION USE KARO — 15→8→5→3 coins per episode! MAX REWARD!)")
        print(" 12) 🧠 Mini Quiz AUTO-COMPLETE (100% Correct Answers → Max Coins)")
        print("  0) 🚪 Exit")
        ch = input("\nOption [11 recommended]: ").strip()

        if ch == "0":
            print("[i] Bye bye!")
            break
        elif ch == "1":
            bot.get_balance()
        elif ch == "2":
            tasks = bot.get_watch_tasks()
            if not tasks:
                print("[i] Task API se koi tasks nahi aaye (app update ke baad task system hat gaya hai?). Try Option 11.")
                continue
            pick = input("\nKonse series no. ko watch karna hai? (0=back): ").strip()
            try:
                idx = int(pick) - 1
                if idx < 0:
                    continue
                task = tasks[idx]
            except (ValueError, IndexError):
                print("[-] Invalid number!")
                continue
            print(f"  [Series ID copied: {task['series_id']}]")
            n = input(f"Kitne episodes? (Enter=all {task.get('progress',{}).get('required','all')}): ").strip()
            n = int(n) if n.isdigit() else None
            dm = input("Realistic delay? (y/N): ").strip().lower()
            dmul = 0.05 if dm == "y" else 0.0
            bot.watch_series(task["series_id"], max_episodes=n, delay_multiplier=dmul)
        elif ch == "3":
            bot.show_all_series_detail()
        elif ch == "4":
            sid = input("Series ID paste: ").strip()
            if not sid:
                continue
            n = input("Kitne episodes? (Enter=all): ").strip()
            n = int(n) if n.isdigit() else None
            bot.watch_series(sid, max_episodes=n)
        elif ch == "5":
            tasks = bot.get_watch_tasks()
            if not tasks:
                print("[i] Tasks nahi mile. Option 11 try karo — Browse ALL Series.")
                continue
            pick = input("\nKonse series no. ke PENDING episodes dekhe? (0=back): ").strip()
            try:
                idx = int(pick) - 1
                if idx < 0:
                    continue
                task = tasks[idx]
            except (ValueError, IndexError):
                print("[-] Invalid number!")
                continue
            bot.watch_series(task["series_id"])
        elif ch == "6":
            tasks = bot.get_watch_tasks()
            if not tasks:
                print("[i] Tasks nahi mile. Option 11 (Browse All) try karo.")
                continue
            n_per = input("Har series me kitne episodes? (Enter=10): ").strip()
            n_per = int(n_per) if n_per.isdigit() else 10
            dm = input("Realistic delay? (y/N): ").strip().lower()
            dmul = 0.05 if dm == "y" else 0.0
            total = 0
            for task in tasks:
                total += bot.watch_series(
                    task["series_id"], max_episodes=n_per, delay_multiplier=dmul
                )
            print(f"\n🏆 Total {total} episodes auto-watched!")
            bot.get_balance()
        elif ch == "7":
            bot.get_profile()
            ws = bot.get_watched_set_from_profile()
            print(f"[i] Global watched pairs (>=80%): {len(ws)}")
        elif ch == "8":
            sid = input("Series ID paste (Enter = pick from task list): ").strip()
            if not sid:
                tasks = bot.get_watch_tasks() or []
                for i, t in enumerate(tasks, 1):
                    print(f"  {i}. {t.get('series_id','?')}")
                p = input("Pick no: ").strip()
                try:
                    ip = int(p) - 1
                    if 0 <= ip < len(tasks):
                        sid = tasks[ip].get("series_id")
                except Exception:
                    pass
            if sid:
                bot.list_locked_episodes(sid)
        elif ch == "9":
            sid = input("Series ID paste: ").strip()
            if not sid:
                continue
            bot.unlock_series_locked(sid)
        elif ch == "10":
            url = input("Playback URL (.mpd/.m3u8) paste: ").strip()
            if not url:
                continue
            bot.verify_cdn_bypass(url)
        elif ch == "11":
            bot.browse_and_watch_all_smart_repeat()
        elif ch == "12":
            bot.quiz_auto_complete_all_available()
        else:
            print("[-] Invalid option!")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[i] User ne stop kiya. Bye!")
