"""
Screen Time Tracker — Backend (app.py)
Tracks active window/app usage and serves data via REST API.

Install:
    pip install flask flask-cors psutil
    Windows: no extra deps
    macOS:   pip install pyobjc-framework-Quartz
    Linux:   sudo apt install xdotool  (or brew install xdotool)

Run:
    python app.py
"""

import time
import threading
import json
import platform
import os
from datetime import datetime, date, timedelta
from collections import defaultdict

from flask import Flask, jsonify, request
from flask_cors import CORS

# ── App setup ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

DATA_FILE   = "screentime_data.json"
DEVICE_FILE = "device_data.json"

tracker_data = {
    "sessions":      {},    # { "YYYY-MM-DD": { "AppName": seconds } }
    "current_app":   None,
    "current_start": None,
    "tracking":      False,
}

device_data = {
    "phone": {
        "name":      None,
        "battery":   None,
        "connected": False,
        "last_seen": None,
    },
    "watch": {
        "name":      None,
        "battery":   None,
        "connected": False,
        "last_seen": None,
        "health":    {},
    },
    "goals": {
        "daily_screen_minutes":  240,
        "productive_target_pct": 60,
    },
}

lock = threading.Lock()


# ── Persistence ───────────────────────────────────────────────────────────────
def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return default


def save_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        print(f"[warn] Could not save {path}: {e}")


def load_data():
    saved = load_json(DATA_FILE, {})
    tracker_data["sessions"] = saved.get("sessions", {})

    saved_device = load_json(DEVICE_FILE, {})
    if saved_device:
        device_data.update(saved_device)


def save_data():
    save_json(DATA_FILE,   {"sessions": tracker_data["sessions"]})
    save_json(DEVICE_FILE, device_data)


# ── Active window detection ───────────────────────────────────────────────────
def get_active_window() -> str:
    """Returns the name of the currently active application."""
    system = platform.system()
    try:
        if system == "Windows":
            import ctypes
            import psutil
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            try:
                proc = psutil.Process(pid.value)
                return proc.name().replace(".exe", "").replace(".EXE", "")
            except Exception:
                length = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                return buf.value or "Unknown"

        elif system == "Darwin":
            try:
                from AppKit import NSWorkspace
                info = NSWorkspace.sharedWorkspace().activeApplication()
                return info.get("NSApplicationName", "Unknown")
            except Exception:
                import subprocess
                script = 'tell application "System Events" to get name of first process whose frontmost is true'
                r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=2)
                return r.stdout.strip() or "Unknown"

        elif system == "Linux":
            import subprocess
            try:
                r = subprocess.run(
                    ["xdotool", "getactivewindow", "getwindowname"],
                    capture_output=True, text=True, timeout=1
                )
                if r.returncode == 0 and r.stdout.strip():
                    name = r.stdout.strip()
                    return name.rsplit(" - ", 1)[-1]
            except FileNotFoundError:
                pass  # xdotool not installed
            except Exception:
                pass
            return "Linux App"

    except Exception:
        pass
    return "Unknown"


# ── Tracking loop ─────────────────────────────────────────────────────────────
def tracking_loop():
    """Polls the active window every second and accumulates usage time."""
    POLL_INTERVAL = 1  # seconds

    while tracker_data["tracking"]:
        current_window = get_active_window()
        today = str(date.today())
        now   = time.time()

        with lock:
            tracker_data["sessions"].setdefault(today, {})

            if tracker_data["current_app"] != current_window:
                # Flush elapsed time for the previous app
                if tracker_data["current_app"] and tracker_data["current_start"]:
                    elapsed  = now - tracker_data["current_start"]
                    app_name = tracker_data["current_app"]
                    tracker_data["sessions"][today][app_name] = (
                        tracker_data["sessions"][today].get(app_name, 0) + elapsed
                    )
                tracker_data["current_app"]   = current_window
                tracker_data["current_start"] = now
            else:
                # Accumulate one poll interval for the current app
                if tracker_data["current_start"] is not None:
                    app_name = tracker_data["current_app"]
                    tracker_data["sessions"][today][app_name] = (
                        tracker_data["sessions"][today].get(app_name, 0) + POLL_INTERVAL
                    )
                    tracker_data["current_start"] = now

        time.sleep(POLL_INTERVAL)

    # Flush remaining time when tracking is stopped
    now   = time.time()
    today = str(date.today())
    with lock:
        if tracker_data["current_app"] and tracker_data["current_start"]:
            elapsed  = now - tracker_data["current_start"]
            app_name = tracker_data["current_app"]
            tracker_data["sessions"].setdefault(today, {})
            tracker_data["sessions"][today][app_name] = (
                tracker_data["sessions"][today].get(app_name, 0) + elapsed
            )
        tracker_data["current_app"]   = None
        tracker_data["current_start"] = None

    save_data()


# ── API Routes ────────────────────────────────────────────────────────────────
@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "message": "Screen Time Tracker Backend is running 🚀"
    })


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})

@app.route("/api/status")
def status():
    with lock:
        is_tracking     = tracker_data["tracking"]
        current         = tracker_data["current_app"]
        started         = tracker_data["current_start"]
        current_seconds = (time.time() - started) if started else 0

    return jsonify({
        "tracking":        is_tracking,
        "current_app":     current,
        "current_seconds": round(current_seconds),
        "platform":        platform.system(),
        "devices":         device_data,
    })


@app.route("/api/start", methods=["POST"])
def start_tracking():
    if not tracker_data["tracking"]:
        tracker_data["tracking"]      = True
        tracker_data["current_start"] = time.time()
        t = threading.Thread(target=tracking_loop, daemon=True)
        t.start()
    return jsonify({"ok": True, "message": "Tracking started"})


@app.route("/api/stop", methods=["POST"])
def stop_tracking():
    tracker_data["tracking"] = False
    return jsonify({"ok": True, "message": "Tracking stopped"})


@app.route("/api/today")
def get_today():
    today = str(date.today())
    with lock:
        data = dict(tracker_data["sessions"].get(today, {}))
        # Merge in live (unsaved) current session time
        if tracker_data["current_app"] and tracker_data["current_start"]:
            current = tracker_data["current_app"]
            elapsed = time.time() - tracker_data["current_start"]
            data[current] = data.get(current, 0) + elapsed

    sorted_apps = sorted(data.items(), key=lambda x: x[1], reverse=True)
    total = sum(v for _, v in sorted_apps)

    return jsonify({
        "date":         today,
        "total_seconds": round(total),
        "apps": [
            {
                "name":    name,
                "seconds": round(secs),
                "percent": round((secs / total * 100) if total > 0 else 0, 1),
            }
            for name, secs in sorted_apps
        ],
    })


@app.route("/api/history")
def get_history():
    days = int(request.args.get("days", 7))
    with lock:
        sessions = dict(tracker_data["sessions"])

    result = []
    today  = date.today()
    for i in range(days):
        day     = today - timedelta(days=i)
        day_str = str(day)
        day_data = sessions.get(day_str, {})
        total    = sum(day_data.values())
        result.append({
            "date":          day_str,
            "label":         day.strftime("%a %d"),
            "total_seconds": round(total),
            "apps": sorted(
                [{"name": k, "seconds": round(v)} for k, v in day_data.items()],
                key=lambda x: x["seconds"],
                reverse=True,
            )[:5],
        })

    result.reverse()  # chronological order
    return jsonify({"days": result})


@app.route("/api/device", methods=["POST"])
def update_device():
    payload = request.json or {}
    kind    = payload.get("kind")  # "phone" or "watch"

    if kind not in ["phone", "watch"]:
        return jsonify({"ok": False, "error": "kind must be 'phone' or 'watch'"}), 400

    with lock:
        device_data[kind].update({
            "name":      payload.get("name",      device_data[kind].get("name")),
            "battery":   payload.get("battery",   device_data[kind].get("battery")),
            "connected": payload.get("connected", True),
            "last_seen": datetime.utcnow().isoformat() + "Z",
        })
        if kind == "watch" and isinstance(payload.get("health"), dict):
            device_data[kind]["health"] = payload["health"]
        save_data()

    return jsonify({"ok": True})


@app.route("/api/devices")
def get_devices():
    with lock:
        return jsonify(device_data)


@app.route("/api/goals", methods=["GET", "POST"])
def goals():
    if request.method == "POST":
        payload = request.json or {}
        with lock:
            device_data["goals"] = {
                "daily_screen_minutes":  int(payload.get("daily_screen_minutes", 240)),
                "productive_target_pct": int(payload.get("productive_target_pct", 60)),
            }
            save_data()
        return jsonify({"ok": True})

    with lock:
        return jsonify(device_data.get("goals", {}))


@app.route("/api/plan")
def get_plan():
    with lock:
        sessions = dict(tracker_data["sessions"])

    today     = date.today()
    totals    = defaultdict(float)
    day_totals = []

    for i in range(7):
        day      = today - timedelta(days=i)
        day_data = sessions.get(str(day), {})
        day_totals.append(sum(day_data.values()))
        for app_name, secs in day_data.items():
            totals[app_name] += secs

    avg_daily = sum(day_totals) / max(len(day_totals), 1)
    top_apps  = sorted(totals.items(), key=lambda x: x[1], reverse=True)[:5]
    total_secs = sum(totals.values())

    productive_kw = ["code", "vscode", "intellij", "pycharm", "terminal", "word",
                     "excel", "notion", "obsidian", "figma", "slack", "zoom",
                     "meet", "docs", "sheets"]
    distract_kw   = ["youtube", "netflix", "instagram", "twitter", "tiktok",
                     "reddit", "facebook", "discord", "twitch", "prime", "hbo"]

    prod_secs = sum(s for a, s in totals.items() if any(p in a.lower() for p in productive_kw))
    dist_secs = sum(s for a, s in totals.items() if any(d in a.lower() for d in distract_kw))

    prod_pct = round(prod_secs / total_secs * 100) if total_secs > 0 else 0
    dist_pct = round(dist_secs / total_secs * 100) if total_secs > 0 else 0

    def fmt(secs):
        h = int(secs // 3600)
        m = int((secs % 3600) // 60)
        return f"{h}h {m}m" if h > 0 else f"{m}m"

    recommendations = []

    if avg_daily > 10 * 3600:
        recommendations.append({
            "type": "warning", "icon": "ti-alert-triangle",
            "title": "High screen time detected",
            "desc":  f"You averaged {fmt(avg_daily)}/day this week. Take a 20-min break every 2 hours.",
        })
    elif avg_daily < 2 * 3600:
        recommendations.append({
            "type": "success", "icon": "ti-circle-check",
            "title": "Great screen balance",
            "desc":  f"You averaged only {fmt(avg_daily)}/day — a healthy screen relationship.",
        })

    if dist_pct > 30:
        recommendations.append({
            "type": "warning", "icon": "ti-device-tv",
            "title": "High distraction usage",
            "desc":  f"{dist_pct}% of your time is entertainment/social. Consider blocking after 9 PM.",
        })

    if prod_pct > 50:
        recommendations.append({
            "type": "success", "icon": "ti-rocket",
            "title": "Highly productive week",
            "desc":  f"{prod_pct}% of screen time on productive apps. Keep it up!",
        })

    if top_apps:
        top_name, top_secs = top_apps[0]
        recommendations.append({
            "type": "info", "icon": "ti-star",
            "title": f"Most used: {top_name}",
            "desc":  f"You spent {fmt(top_secs)} on {top_name} this week. Intentional?",
        })

    schedule = [
        {"time": "6:00 – 9:00 AM",  "activity": "Morning routine — no screens",          "type": "break"},
        {"time": "9:00 – 12:00 PM", "activity": "Deep work block (productive apps)",      "type": "work"},
        {"time": "12:00 – 1:00 PM", "activity": "Lunch break — step outside",             "type": "break"},
        {"time": "1:00 – 3:30 PM",  "activity": "Focus work / meetings",                  "type": "work"},
        {"time": "3:30 – 4:00 PM",  "activity": "Short break — stretch",                  "type": "break"},
        {"time": "4:00 – 6:00 PM",  "activity": "Wrap up + review / learning",            "type": "work"},
        {"time": "6:00 – 9:00 PM",  "activity": "Personal time — limited screens",        "type": "leisure"},
        {"time": "9:00 PM+",        "activity": "Wind down — no blue light",              "type": "break"},
    ]

    return jsonify({
        "summary": {
            "avg_daily_hours":   round(avg_daily / 3600, 1),
            "productive_pct":    prod_pct,
            "distraction_pct":   dist_pct,
            "top_app":           top_apps[0][0] if top_apps else "N/A",
        },
        "recommendations":  recommendations,
        "suggested_schedule": schedule,
    })


@app.route("/api/reset", methods=["POST"])
def reset_today():
    today = str(date.today())
    with lock:
        tracker_data["sessions"][today] = {}
    save_data()
    return jsonify({"ok": True, "message": f"Reset data for {today}"})


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    load_data()
    print("Screen Time Tracker backend running on http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
