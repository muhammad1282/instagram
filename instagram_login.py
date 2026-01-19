# ================================ IMPORTS ====================================
import streamlit as st
from instagrapi import Client
import os, time, threading, schedule, re, sqlite3
from datetime import datetime
from PIL import Image
import pandas as pd

# ================================ SENTRY =====================================
import sentry_sdk
from sentry_sdk.integrations.threading import ThreadingIntegration

sentry_sdk.init(
     dsn="https://6edd88355cfe415ac9f7fad76af2faa9@o4510738158452736.ingest.de.sentry.io/4510738190106704",
    integrations=[ThreadingIntegration(propagate_hub=True)],
    traces_sample_rate=0.0,     # error monitoring only
    send_default_pii=False,     # protect credentials
    environment="production"
)

# ================================ PATHS ======================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "logs.db")
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)

# ================================ STREAMLIT ==================================
st.set_page_config(page_title="Instagram Story Scheduler", layout="wide")

# ================================ DATABASE ===================================
def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS story_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            day TEXT,
            file_path TEXT,
            status TEXT,
            msg TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def log(username, day, path, status, msg=""):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO story_logs (username, day, file_path, status, msg) VALUES (?,?,?,?,?)",
        (username, day, path, status, msg)
    )
    conn.commit()
    conn.close()

def get_logs(limit=100):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, username, day, file_path, status, msg, created_at "
        "FROM story_logs ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows

init_db()

# ================================ SESSION ====================================
if "accounts" not in st.session_state:
    st.session_state["accounts"] = []

# ================================ UI =========================================
st.title("📸 Instagram Multi-Account Story Scheduler")
st.caption("Auto weekly posting • Multi-account • Logs • Runs 24/7")

# ================================ SIDEBAR ====================================
with st.sidebar:
    st.header("➕ Add Instagram Account")
    u = st.text_input("Username")
    p = st.text_input("Password", type="password")

    if st.button("Add Account"):
        if u and p:
            st.session_state["accounts"].append({
                "username": u,
                "password": p
            })
            st.success("Account added")
        else:
            st.error("Username & password required")

    st.subheader("📂 Saved Accounts")
    for acc in st.session_state["accounts"]:
        st.write("•", acc["username"])

# ================================ HELPERS ====================================
def natural_sort_key(name):
    return [int(x) if x.isdigit() else x.lower()
            for x in re.split(r"(\d+)", name)]

def convert_to_24h(h, m, ap):
    h = int(h)
    if ap == "PM" and h != 12:
        h += 12
    if ap == "AM" and h == 12:
        h = 0
    return f"{h:02d}:{m:02d}"

def posted_today(day, user):
    return os.path.exists(f"posted_{day}_{user}.txt")

def mark_posted(day, user):
    with open(f"posted_{day}_{user}.txt", "w") as f:
        f.write(str(datetime.now()))

def login(username, password):
    cl = Client()
    session_file = os.path.join(SESSIONS_DIR, f"{username}.json")

    if os.path.exists(session_file):
        cl.load_settings(session_file)

    cl.login(username, password)
    cl.dump_settings(session_file)
    return cl

# ================================ SCHEDULE UI ================================
DAYS = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
CONFIG = {}

st.subheader("📅 Weekly Posting Schedule")
cols = st.columns(2)

for i, day in enumerate(DAYS):
    with cols[i % 2]:
        enable = st.checkbox(f"Enable {day}", True)
        folder = st.text_input(f"{day} Folder Path", key=f"{day}_folder")
        h = st.selectbox("Hour", list(range(1,13)), key=f"{day}_h")
        m = st.selectbox("Minute", list(range(0,60)), key=f"{day}_m")
        ap = st.selectbox("AM / PM", ["AM","PM"], key=f"{day}_ap")

        if enable:
            CONFIG[day.lower()] = {
                "folder": folder,
                "time": convert_to_24h(h, m, ap)
            }

# ================================ POST JOB ===================================
def post_job(day, accounts, config):
    for acc in accounts:
        user = acc["username"]
        pwd = acc["password"]

        sentry_sdk.set_tag("instagram_user", user)
        sentry_sdk.set_tag("day", day)

        if posted_today(day, user):
            continue

        folder = config[day]["folder"]
        if not os.path.exists(folder):
            continue

        files = [
            f for f in os.listdir(folder)
            if f.lower().endswith((".jpg",".jpeg",".png",".webp",".mp4"))
        ]
        files.sort(key=natural_sort_key)

        if not files:
            continue

        cl = login(user, pwd)

        for f in files:
            path = os.path.join(folder, f)
            ext = os.path.splitext(path)[1].lower()

            if ext == ".webp":
                jpg = path.replace(".webp", ".jpg")
                Image.open(path).convert("RGB").save(jpg, "JPEG")
                path = jpg
                ext = ".jpg"

            try:
                if ext in [".jpg",".jpeg",".png"]:
                    cl.photo_upload_to_story(path)
                elif ext == ".mp4":
                    cl.video_upload_to_story(path)

                log(user, day, path, "SUCCESS")
            except Exception as e:
                sentry_sdk.capture_exception(e)
                log(user, day, path, "FAIL", str(e))

            time.sleep(10)

        mark_posted(day, user)
        print("Posted:", user, day)

# ================================ SCHEDULER ==================================
def run_scheduler(accounts, config):
    for d, v in config.items():
        schedule.every().__getattribute__(d).at(v["time"]).do(
            post_job, d, accounts, config
        )

    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            sentry_sdk.capture_exception(e)
        time.sleep(20)

# ================================ CONTROLS ===================================
st.divider()
st.subheader("▶ Scheduler Controls")

if st.button("🚀 Start Scheduler", width="stretch"):
    if not st.session_state["accounts"]:
        st.error("Add at least one account")
    else:
        threading.Thread(
            target=run_scheduler,
            args=(list(st.session_state["accounts"]), dict(CONFIG)),
            daemon=True
        ).start()
        st.success("Scheduler running in background")

if st.button("⛔ Stop App", width="stretch"):
    os._exit(0)

# ================================ DASHBOARD ==================================
st.divider()
st.subheader("📊 Logs Dashboard (Latest 100)")

logs = get_logs(100)
df = pd.DataFrame(
    logs,
    columns=["ID","User","Day","File","Status","Msg","Created"]
)
st.dataframe(df, width="stretch")
