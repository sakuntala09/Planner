"""
My Planner - Study, Meal, Self-care and Finance planners (built with Streamlit)

Run it with:   streamlit run app.py
"""

import calendar
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

# ------------------------------------------------------------------
# 1. SETTINGS (change these easily)
# ------------------------------------------------------------------
TIMEZONE = "Asia/Kolkata"  # so "today" is correct for you, not the server
CURRENCY = "₹"
WATER_GOAL = 8  # glasses per day
DATA_FILE = Path(os.environ.get("PLANNER_DATA", "planner_data.json"))
DB_TABLE = "planner_users"  # the Supabase table name
COOKIE_NAME = "planner_login"  # the 'remember me' cookie kept in your browser
REMEMBER_DAYS = 30  # how long "Keep me logged in" lasts
MAX_DEVICES = 5  # how many phones/computers can stay logged in at once
IMAGE_DIR = Path("images")  # put character.png in this folder

# Calendar colors (keep them close to .streamlit/config.toml)
COLOR_PRIMARY = "#F48FB1"
COLOR_SOFT = "#FCE4EC"
COLOR_DOT = "#D81B60"

QUOTES = [
    "Small steps every day add up to big results.",
    "Start where you are. Use what you have. Do what you can.",
    "A little progress each day is still progress.",
    "Focus for now, rest after. You've got this!",
    "Your future self will thank you for studying today.",
    "Done is better than perfect.",
    "One page, one task, one step at a time.",
    "Be proud of how far you have come.",
]

# Hours shown in the timetable, grouped by part of the day
SLOTS = {
    "Morning": ["06:00", "07:00", "08:00", "09:00", "10:00", "11:00"],
    "Afternoon": ["12:00", "13:00", "14:00", "15:00", "16:00"],
    "Evening": ["17:00", "18:00", "19:00", "20:00"],
    "Night": ["21:00", "22:00", "23:00"],
}

MEALS = ["Breakfast", "Lunch", "Snacks", "Dinner"]
MOODS = ["😞 Very low", "😕 Low", "😐 Okay", "🙂 Good", "😄 Great"]
SLEEP_QUALITY = ["Poor", "Okay", "Good"]
SPEND_CATEGORIES = ["Food", "Travel", "Study", "Shopping", "Fun", "Other"]

st.set_page_config(page_title="My Planner", page_icon="🌸", layout="centered")

# Soft, rounded look. Theme colors come from .streamlit/config.toml
CSS = """
<style>
.stButton > button, .stFormSubmitButton > button {
    width: 100%;
    border-radius: 16px;
    padding: 0.6rem 1rem;
}
.big-timer { font-size: 4rem; font-weight: 700; text-align: center; margin: 0.5rem 0; }

/* ---- monthly calendar ---- */
.cal { background: __SOFT__; border-radius: 16px; padding: 10px 8px; margin-bottom: 4px; }
.cal-title { text-align: center; font-weight: 700; font-size: 1.1rem; margin-bottom: 6px; }
.cal table { width: 100%; border-collapse: collapse; table-layout: fixed; margin: 0; }
.cal th, .cal td { border: none !important; background: transparent !important;
                   padding: 2px 0 !important; text-align: center; }
.cal th { font-size: 0.75rem; opacity: 0.7; }
.cal td { height: 2.7rem; vertical-align: top; }
.cal .num { width: 1.8rem; height: 1.8rem; line-height: 1.8rem; margin: 0 auto;
            font-size: 0.9rem; border-radius: 50%; }
.cal .other { opacity: 0.35; }
.cal .today { background: __PRIMARY__; color: white; font-weight: 700; }
.cal .sel { outline: 2px solid __PRIMARY__; }
.cal .dotline { font-size: 0.6rem; line-height: 0.9rem; color: __DOT__; }
</style>
""".replace("__SOFT__", COLOR_SOFT).replace("__PRIMARY__", COLOR_PRIMARY).replace("__DOT__", COLOR_DOT)
st.markdown(CSS, unsafe_allow_html=True)


# ------------------------------------------------------------------
# 2. SMALL HELPERS (date, saving data, passwords, money)
# ------------------------------------------------------------------
def today():
    """Today's date in YOUR timezone."""
    try:
        return datetime.now(ZoneInfo(TIMEZONE)).date()
    except Exception:
        return date.today()


# ---- Storage: Supabase when it is set up, otherwise a local JSON file ----
class DatabaseError(Exception):
    """Shown on screen when the database cannot be reached."""


def supabase_settings():
    """Return (url, key) if Supabase is set up in Secrets, otherwise None."""
    try:
        cfg = st.secrets["supabase"]
        return str(cfg["url"]).strip().rstrip("/"), str(cfg["key"]).strip()
    except Exception:
        return None


def api_headers(key, extra=None):
    """Headers Supabase needs. New 'sb_secret_' keys go ONLY in `apikey`;
    old service_role keys (they start with 'eyJ') also go in Authorization."""
    headers = {"apikey": key, "Content-Type": "application/json"}
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    headers.update(extra or {})
    return headers


def check_response(response):
    if response.status_code >= 400:
        hint = ""
        if response.status_code in (401, 403):
            hint = " Check the key in your Streamlit Secrets."
        elif response.status_code == 404:
            hint = " Check the project URL and that the table exists (run the SQL setup)."
        raise DatabaseError(f"Database error ({response.status_code}).{hint}")


def supabase_get(cfg, username):
    url, key = cfg
    try:
        response = requests.get(
            f"{url}/rest/v1/{DB_TABLE}",
            params={"username": f"eq.{username}", "select": "data"},
            headers=api_headers(key),
            timeout=15,
        )
    except requests.RequestException:
        raise DatabaseError("Could not reach the database. Check your internet and try again.") from None
    check_response(response)
    rows = response.json()
    return rows[0]["data"] if rows else None


def supabase_save(cfg, users):
    url, key = cfg
    rows = [{"username": name, "data": record} for name, record in users.items()]
    try:
        response = requests.post(
            f"{url}/rest/v1/{DB_TABLE}",
            params={"on_conflict": "username"},
            headers=api_headers(key, {"Prefer": "resolution=merge-duplicates,return=minimal"}),
            json=rows,
            timeout=15,
        )
    except requests.RequestException:
        raise DatabaseError("Could not reach the database. Check your internet and try again.") from None
    check_response(response)


def read_json_file():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"users": {}}


def write_json_file(db):
    """Safe write: write a temp file first, then swap it in."""
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, indent=2), encoding="utf-8")
    tmp.replace(DATA_FILE)


def valid_username(name):
    return len(name) >= 3 and name.replace("_", "").isalnum()


def user_cache():
    return st.session_state.setdefault("_user_cache", {})


def clear_user_cache():
    st.session_state["_user_cache"] = {}


def fetch_user(username):
    """Get ONE user's record (or None). Asked once per click, then reused."""
    if not username or not valid_username(username):
        return None
    cache = user_cache()
    if username in cache:
        return cache[username]
    cfg = supabase_settings()
    record = supabase_get(cfg, username) if cfg else read_json_file()["users"].get(username)
    if record is not None:
        cache[username] = record
    return record


def save_users(users):
    """Save one or more users: {username: record}."""
    cfg = supabase_settings()
    if cfg:
        supabase_save(cfg, users)
    else:
        db = read_json_file()
        db["users"].update(users)
        write_json_file(db)
    user_cache().update(users)


def save_db(db):
    """Save what current_user() gave you, after you changed it."""
    save_users(db["users"])


# ---- "Keep me logged in" (remember me) ----
def token_hash(token):
    """Scrambled copy of a token. The database keeps only this, never the token."""
    return hashlib.sha256(token.encode()).hexdigest()


def cookie_at_start():
    """The remember-me cookie the browser sent when the app was opened (or None)."""
    try:
        value = st.context.cookies.get(COOKIE_NAME)
    except Exception:
        return None
    return value if isinstance(value, str) else None


def start_remember_session(username, user):
    """Make a secret token for this device. Call save_users() after this."""
    token = secrets.token_urlsafe(32)
    now = time.time()
    alive = [s for s in user.get("sessions", []) if s["expires"] > now]
    alive.append({"hash": token_hash(token), "expires": now + REMEMBER_DAYS * 86400})
    user["sessions"] = alive[-MAX_DEVICES:]  # oldest devices fall off the list
    st.session_state.session_token = token
    st.session_state.cookie_target = f"{username}.{token}"  # to be written to the browser


def try_auto_login():
    """New visit with a valid remember-me cookie? Log in without a password."""
    if st.session_state.username or st.session_state.get("auto_login_checked"):
        return
    st.session_state.auto_login_checked = True  # only check once per visit
    raw = cookie_at_start()
    if not raw:
        return
    username, _, token = raw.partition(".")
    user = fetch_user(username)
    if user and token:
        now = time.time()
        wanted = token_hash(token)
        for s in user.get("sessions", []):
            if s["expires"] > now and hmac.compare_digest(s["hash"], wanted):
                st.session_state.username = username
                st.session_state.session_token = token
                st.session_state.page = "home"
                return
    st.session_state.cookie_target = ""  # cookie is old or wrong: delete it


def sync_cookie():
    """Write (or delete) the remember-me cookie in the browser with a tiny script."""
    target = st.session_state.get("cookie_target")
    if target is None:
        return
    max_age = REMEMBER_DAYS * 86400 if target else 0
    script = (
        "<script>(function(){"
        "var d; try { d = window.parent.document; d.cookie; } catch (e) { d = document; }"
        "var s = (window.location.protocol === 'https:') ? '; Secure' : '';"
        f"d.cookie = {json.dumps(COOKIE_NAME + '=' + target)}"
        f" + '; Max-Age={max_age}; path=/; SameSite=Lax' + s;"
        "})();</script>"
    )
    if hasattr(st, "iframe"):  # newer Streamlit
        st.iframe(script, height="content")
    else:  # older Streamlit
        import streamlit.components.v1 as components

        components.html(script, height=0)
        
        
def hash_password(password, salt_hex):
    """Turn a password into a scrambled string. We never store the real one."""
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), 100_000
    ).hex()


def new_user_data():
    """The empty notebook every new user starts with."""
    return {
        "visits": {},  # planner name -> list of dates you opened it (the calendar dots)
        "study": {"timetable": {}, "todos": [], "minutes": {}, "notes": {}},
        "meal": {"days": {}, "week_notes": {}, "grocery": []},
        "selfcare": {"checkins": {}},
        "finance": {"months": {}, "fixed": [], "daily": [], "debts": []},
    }


def current_user():
    """Return (whole database, the logged-in user's record).
    Missing parts are filled in, so older saved data keeps working."""
    username = st.session_state.username
    user = fetch_user(username)
    if user is None:
        raise DatabaseError("Your account was not found. Please log in again.")
    db = {"users": {username: user}}
    for key, empty in new_user_data().items():
        part = user.setdefault(key, empty)
        if isinstance(empty, dict):
            for sub_key, sub_empty in empty.items():
                part.setdefault(sub_key, sub_empty)
    return db, user


def add_minutes(day, minutes):
    """Add studied minutes to a day."""
    if minutes <= 0:
        return
    db, user = current_user()
    log = user["study"]["minutes"]
    log[day.isoformat()] = log.get(day.isoformat(), 0) + minutes
    save_db(db)


def fmt_minutes(minutes):
    hours, mins = divmod(int(minutes), 60)
    return f"{hours} h {mins} min" if hours else f"{mins} min"


def money(amount):
    if abs(amount - round(amount)) < 0.005:
        return f"{CURRENCY}{amount:,.0f}"
    return f"{CURRENCY}{amount:,.2f}"


def flash(message):
    """Remember a message to show after the next page refresh."""
    st.session_state.flash = message
    
# ------------------------------------------------------------------
# 3. NAVIGATION + SHARED PIECES
# ------------------------------------------------------------------
def init_state():
    st.session_state.setdefault("page", "login")
    st.session_state.setdefault("username", None)
    st.session_state.setdefault("selected_date", today())
    st.session_state.setdefault("timer", None)


def go(page):
    """Used as a button callback: switch to another page."""
    st.session_state.page = page


def logout():
    username = st.session_state.get("username")
    token = st.session_state.get("session_token")
    if username and token:  # forget this device on the server too
        try:
            user = fetch_user(username)
            if user:
                gone = token_hash(token)
                user["sessions"] = [s for s in user.get("sessions", []) if s["hash"] != gone]
                save_users({username: user})
        except DatabaseError:
            pass  # still log out on screen even if the database is unreachable
    for key in ("username", "timer", "timer_message", "flash", "session_token"):
        st.session_state[key] = None
    st.session_state.page = "login"
    st.session_state.auto_login_checked = True  # don't log in again from the old cookie
    st.session_state.cookie_target = ""  # delete the cookie from the browser


def logout_everywhere():
    """Forget ALL devices (use this if you lose your phone)."""
    username = st.session_state.get("username")
    user = fetch_user(username) if username else None
    if user:
        user["sessions"] = []
        save_users({username: user})
    st.session_state.session_token = None
    logout()


def page_header(title, back_to, back_label="⬅ Back"):
    """Back button + title + any 'Saved!' message."""
    st.button(back_label, key=f"back_{back_to}", on_click=go, args=(back_to,))
    st.title(title)
    message = st.session_state.pop("flash", None)
    if message:
        st.success(message)


def show_character(width=150):
    """Show your character picture. Put it at images/character.png"""
    for name in ("character.png", "character.jpg", "character.jpeg", "character.gif"):
        path = IMAGE_DIR / name
        if path.exists():
            _, middle, _ = st.columns([1, 2, 1])
            with middle:
                st.image(str(path), width=width)
            return
    st.markdown("<div style='text-align:center; font-size:4rem'>🐰</div>", unsafe_allow_html=True)


def sync_date(key):
    """When the date box changes, remember the new date for all pages."""
    st.session_state.selected_date = st.session_state[key]


def date_picker(label, key):
    st.date_input(
        label,
        value=st.session_state.selected_date,
        key=key,
        on_change=sync_date,
        args=(key,),
        format="DD/MM/YYYY",
    )


def mark_visit(planner):
    """Remember that you opened this planner today (this makes the calendar dot)."""
    db, user = current_user()
    days = user["visits"].setdefault(planner, [])
    key = today().isoformat()
    if key not in days:
        days.append(key)
        save_db(db)


def calendar_html(visited, selected, today_date):
    """Build the monthly calendar as a small HTML table."""
    month = selected.month
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(selected.year, month)
    head = "".join(f"<th>{d}</th>" for d in ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"])
    rows = ""
    for week in weeks:
        cells = ""
        for d in week:
            classes = ["num"]
            if d.month != month:
                classes.append("other")
            if d == today_date:
                classes.append("today")
            if d == selected:
                classes.append("sel")
            dot = "●" if d.isoformat() in visited else "&nbsp;"
            cells += (
                f"<td><div class='{' '.join(classes)}'>{d.day}</div>"
                f"<div class='dotline'>{dot}</div></td>"
            )
        rows += f"<tr>{cells}</tr>"
    title = selected.strftime("%B %Y")
    return (
        f"<div class='cal'><div class='cal-title'>{title}</div>"
        f"<table><tr>{head}</tr>{rows}</table></div>"
    )


def calendar_block(planner):
    """Monthly calendar for one planner. A dot = a day you opened that planner."""
    mark_visit(planner)
    _, user = current_user()
    visited = set(user["visits"].get(planner, []))
    st.markdown(
        calendar_html(visited, st.session_state.selected_date, today()),
        unsafe_allow_html=True,
    )
    st.caption("● = a day you opened this planner")
    date_picker("📅 Pick a day (the calendar jumps to its month)", f"date_{planner}_home")


def toggle_item(section, list_name, item_id):
    """Called when you tick or untick a checklist item."""
    db, user = current_user()
    for item in user[section][list_name]:
        if item["id"] == item_id:
            item["done"] = st.session_state[f"{list_name}_{item_id}"]
    save_db(db)


def clear_done(section, list_name):
    db, user = current_user()
    user[section][list_name] = [i for i in user[section][list_name] if not i["done"]]
    save_db(db)


def checklist(section, list_name, input_label, placeholder, clear_label):
    """A reusable tick list: add items, tick them, delete the ticked ones."""
    _, user = current_user()
    items = user[section][list_name]
    with st.form(f"form_{list_name}", clear_on_submit=True):
        text = st.text_input(input_label, placeholder=placeholder)
        add_clicked = st.form_submit_button("Add")
    if add_clicked and text.strip():
        db, user = current_user()
        user[section][list_name].append(
            {"id": secrets.token_hex(4), "text": text.strip(), "done": False}
        )
        save_db(db)
        st.rerun()

    if not items:
        st.caption("Nothing here yet. Add your first item above!")
    for item in items:
        st.checkbox(
            item["text"],
            value=item["done"],
            key=f"{list_name}_{item['id']}",
            on_change=toggle_item,
            args=(section, list_name, item["id"]),
        )
    if any(i["done"] for i in items):
        st.button(clear_label, key=f"clear_{list_name}", on_click=clear_done, args=(section, list_name))


def remove_picker(items, describe, key):
    """A dropdown + button to remove one item. Returns the id to remove, or None."""
    if not items:
        return None
    with st.expander("🗑 Remove an item"):
        labels = {item["id"]: describe(item) for item in items}
        chosen = st.selectbox("Pick one", list(labels), format_func=labels.get, key=key)
        if st.button("Remove selected", key=f"{key}_button"):
            return chosen
    return None


# ------------------------------------------------------------------
# 4. PAGE: LOGIN / SIGN UP
# ------------------------------------------------------------------
def page_login():
    show_character()
    st.title("🌸 My Planner")
    st.caption("Study, meals, self-care and money - all in one place.")

    tab_login, tab_signup = st.tabs(["Log in", "Sign up"])

    with tab_login:
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            remember = st.checkbox(f"Keep me logged in on this device ({REMEMBER_DAYS} days)", value=True)
            submitted = st.form_submit_button("Log in")
        if submitted:
            uname = username.strip().lower()
            user = fetch_user(uname)
            ok = user is not None and hmac.compare_digest(
                hash_password(password, user["salt"]), user["hash"]
            )
            if ok:
                st.session_state.username = uname
                st.session_state.page = "home"
                if remember:
                    start_remember_session(uname, user)
                    save_users({uname: user})
                st.rerun()
            else:
                st.error("Wrong username or password.")

    with tab_signup:
        with st.form("signup_form"):
            name = st.text_input("Your name")
            new_username = st.text_input("Choose a username")
            new_password = st.text_input("Choose a password (min 6 characters)", type="password")
            confirm = st.text_input("Confirm password", type="password")
            remember_new = st.checkbox(
                f"Keep me logged in on this device ({REMEMBER_DAYS} days)", value=True, key="remember_new"
            )
            created = st.form_submit_button("Create account")
        if created:
            uname = new_username.strip().lower()
            if not name.strip():
                st.error("Please enter your name.")
            elif not valid_username(uname):
                st.error("Username: at least 3 letters/numbers (underscore _ is allowed).")
            elif fetch_user(uname) is not None:
                st.error("That username is already taken.")
            elif len(new_password) < 6:
                st.error("Password must be at least 6 characters.")
            elif new_password != confirm:
                st.error("Passwords do not match.")
            else:
                salt = secrets.token_hex(16)
                record = {
                    "name": name.strip(),
                    "salt": salt,
                    "hash": hash_password(new_password, salt),
                    **new_user_data(),
                }
                if remember_new:
                    start_remember_session(uname, record)
                save_users({uname: record})
                st.session_state.username = uname
                st.session_state.page = "home"
                st.rerun()

# ------------------------------------------------------------------
# 5. PAGE: HOME (main menu)
# ------------------------------------------------------------------
def page_home():
    _, user = current_user()
    show_character()
    st.title(f"Hi, {user['name']}! 🌷")
    st.write("What would you like to plan today?")

    st.button("📚 Study Planner", on_click=go, args=("study_home",))
    st.button("🍱 Meal Planner", on_click=go, args=("meal_home",))
    st.button("💖 Self-care Planner", on_click=go, args=("selfcare_home",))
    st.button("💰 Finance Tracker", on_click=go, args=("finance_home",))
    st.button("⚙ Settings", on_click=go, args=("settings",))
    st.divider()
    st.button("Log out", on_click=logout)


# ------------------------------------------------------------------
# 6. STUDY PLANNER
# ------------------------------------------------------------------
def page_study_home():
    page_header("📚 Study Planner", "home", "⬅ Main menu")
    calendar_block("study")
    st.button("Go to Dashboard ➜", on_click=go, args=("dashboard",))
    st.divider()
    quote = QUOTES[today().toordinal() % len(QUOTES)]  # a new quote each day
    st.info(f"💬 {quote}")


def page_dashboard():
    page_header("🗂 Dashboard", "study_home", "⬅ Calendar")
    _, user = current_user()
    st.metric("Studied today", fmt_minutes(user["study"]["minutes"].get(today().isoformat(), 0)))
    st.button("📝 Timetable", on_click=go, args=("timetable",))
    st.button("⏱ Study Timer", on_click=go, args=("timer",))
    st.button("⚙ Settings", on_click=go, args=("settings",))


def page_timetable():
    page_header("📝 Timetable", "dashboard", "⬅ Dashboard")
    date_picker("Day", "date_timetable")
    day_key = st.session_state.selected_date.isoformat()
    day = st.session_state.selected_date

    _, user = current_user()
    study = user["study"]

    # ---- Section 1: tasks from morning to night ----
    st.subheader("1. Daily tasks")
    saved_slots = study["timetable"].get(day_key, {})
    with st.form(f"timetable_form_{day_key}"):
        values = {}
        for part, hours in SLOTS.items():
            st.markdown(f"**{part}**")
            for hour in hours:
                values[hour] = st.text_input(
                    hour,
                    value=saved_slots.get(hour, ""),
                    key=f"slot_{day_key}_{hour}",
                    placeholder="What will you study?",
                )
        saved_clicked = st.form_submit_button("Save timetable")
    if saved_clicked:
        db, user = current_user()
        user["study"]["timetable"][day_key] = {h: v.strip() for h, v in values.items() if v.strip()}
        save_db(db)
        st.success("Timetable saved!")

    # ---- Section 2: important to-do list ----
    st.subheader("2. Important to-do")
    checklist("study", "todos", "New important task", "e.g. Finish assignment", "🧹 Clear completed tasks")

    # ---- Section 3: study hour tracker ----
    st.subheader("3. Study hour tracker")
    log = study["minutes"]
    st.metric("Studied on this day", fmt_minutes(log.get(day_key, 0)))
    last7 = [day - timedelta(days=i) for i in range(6, -1, -1)]
    chart = pd.DataFrame(
        {"Minutes": [log.get(d.isoformat(), 0) for d in last7]},
        index=pd.to_datetime(last7),
    )
    st.caption("Minutes studied in the 7 days up to this day")
    st.bar_chart(chart)

    # ---- Section 4: notes ----
    st.subheader("4. Notes")
    note = st.text_area(
        "Notes for this day",
        value=study["notes"].get(day_key, ""),
        key=f"note_{day_key}",
        height=150,
    )
    if st.button("Save notes"):
        db, user = current_user()
        user["study"]["notes"][day_key] = note
        save_db(db)
        st.success("Notes saved!")


# ---- Pomodoro timer ----
def start_timer(mode, minutes):
    now = time.time()
    st.session_state.timer = {
        "mode": mode,
        "minutes": minutes,
        "start": now,
        "end": now + minutes * 60,  # a real clock time, so it stays correct
    }


def finish_timer(completed):
    """Stop the timer. If it was a focus session, save the studied minutes."""
    timer = st.session_state.timer
    if not timer:
        return
    if timer["mode"] == "Focus":
        minutes = timer["minutes"] if completed else int((time.time() - timer["start"]) // 60)
        add_minutes(today(), minutes)
        if completed:
            st.session_state.timer_message = f"🎉 Great job! {minutes} minutes added. Time for a break."
        else:
            st.session_state.timer_message = f"Stopped. {minutes} minutes saved."
    else:
        st.session_state.timer_message = "☕ Break is over!" if completed else "Break stopped."
    st.session_state.timer = None


def show_countdown():
    """Shows the ticking clock. It refreshes itself every second while running."""
    running = st.session_state.timer is not None

    @st.fragment(run_every=1 if running else None)
    def tick():
        timer = st.session_state.timer
        if timer is None:
            st.markdown("<div class='big-timer'>--:--</div>", unsafe_allow_html=True)
            return
        left = int(timer["end"] - time.time())
        if left <= 0:
            finish_timer(completed=True)
            st.rerun()  # refresh the whole page
        mins, secs = divmod(left, 60)
        st.markdown(f"**{timer['mode']} time**")
        st.markdown(f"<div class='big-timer'>{mins:02d}:{secs:02d}</div>", unsafe_allow_html=True)
        total = timer["minutes"] * 60
        st.progress(min(max(1 - left / total, 0.0), 1.0))

    tick()


def page_timer():
    page_header("⏱ Study Timer", "dashboard", "⬅ Dashboard")

    message = st.session_state.pop("timer_message", None)
    if message:
        st.success(message)

    show_countdown()

    if st.session_state.timer is None:
        focus = st.number_input("Focus minutes", min_value=1, max_value=120, value=25)
        rest = st.number_input("Break minutes", min_value=1, max_value=60, value=5)
        st.button("▶ Start focus", on_click=start_timer, args=("Focus", int(focus)))
        st.button("☕ Start break", on_click=start_timer, args=("Break", int(rest)))
    else:
        st.button("⏹ Stop", on_click=finish_timer, args=(False,))

    st.divider()
    _, user = current_user()
    st.metric("Total studied today", fmt_minutes(user["study"]["minutes"].get(today().isoformat(), 0)))

    with st.expander("Studied without the timer? Add minutes"):
        extra = st.number_input("Minutes", min_value=0, max_value=600, value=0, key="extra_minutes")
        if st.button("Add to today"):
            add_minutes(today(), int(extra))
            st.rerun()





# ------------------------------------------------------------------
# 7. MEAL PLANNER
# ------------------------------------------------------------------
def page_meal_home():
    page_header("🍱 Meal Planner", "home", "⬅ Main menu")
    calendar_block("meal")
    st.button("🍽 Meal Planner", on_click=go, args=("meal_plan",))
    st.button("🛒 Grocery List", on_click=go, args=("grocery",))


def page_meal_plan():
    page_header("🍽 Weekly Meals", "meal_home", "⬅ Calendar")
    date_picker("Pick any day (shows its whole week)", "date_meal_plan")
    selected = st.session_state.selected_date
    week_start = selected - timedelta(days=selected.weekday())  # Monday
    week_days = [week_start + timedelta(days=i) for i in range(7)]
    week_key = week_start.isoformat()
    st.caption(f"Week: {week_days[0]:%d %b} - {week_days[-1]:%d %b %Y}")

    _, user = current_user()
    meal = user["meal"]

    with st.form(f"meal_form_{week_key}"):
        week_note = st.text_area(
            "📝 Weekly meal notes",
            value=meal["week_notes"].get(week_key, ""),
            key=f"week_note_{week_key}",
            placeholder="Ideas, things to cook, reminders...",
        )
        entries = {}
        for d in week_days:
            iso = d.isoformat()
            saved = meal["days"].get(iso, {"plan": {}, "eaten": {}})
            eaten_count = sum(1 for m in MEALS if saved["eaten"].get(m))
            label = f"{d:%a %d %b} - {eaten_count}/{len(MEALS)} meals eaten"
            with st.expander(label, expanded=(d == selected)):
                plan, eaten = {}, {}
                for m in MEALS:
                    plan[m] = st.text_input(
                        m, value=saved["plan"].get(m, ""), key=f"plan_{iso}_{m}",
                        placeholder=f"What for {m.lower()}?",
                    )
                    eaten[m] = st.checkbox(
                        f"✅ {m} eaten", value=bool(saved["eaten"].get(m)), key=f"eaten_{iso}_{m}"
                    )
            entries[iso] = {"plan": plan, "eaten": eaten}
        saved_clicked = st.form_submit_button("Save this week")

    if saved_clicked:
        db, user = current_user()
        user["meal"]["week_notes"][week_key] = week_note
        for iso, entry in entries.items():
            user["meal"]["days"][iso] = {
                "plan": {m: t.strip() for m, t in entry["plan"].items() if t.strip()},
                "eaten": {m: v for m, v in entry["eaten"].items() if v},
            }
        save_db(db)
        flash("Week saved!")
        st.rerun()


def page_grocery():
    page_header("🛒 Grocery List", "meal_home", "⬅ Calendar")
    checklist("meal", "grocery", "Add an item", "e.g. Milk", "🗑 Delete ticked items")


# ------------------------------------------------------------------
# 8. SELF-CARE PLANNER
# ------------------------------------------------------------------
def page_selfcare_home():
    page_header("💖 Self-care Planner", "home", "⬅ Main menu")
    calendar_block("selfcare")
    st.button("📝 Check-in", on_click=go, args=("checkin",))


def page_checkin():
    page_header("📝 Check-in", "selfcare_home", "⬅ Calendar")
    date_picker("Day", "date_checkin")
    day = st.session_state.selected_date
    day_key = day.isoformat()

    _, user = current_user()
    checkins = user["selfcare"]["checkins"]
    saved = checkins.get(day_key, {})

    water_saved = int(saved.get("water", 0))
    st.progress(min(water_saved / WATER_GOAL, 1.0), text=f"💧 Water: {water_saved} / {WATER_GOAL} glasses")

    with st.form(f"checkin_form_{day_key}"):
        mood_label = st.select_slider(
            "1. Mood",
            options=MOODS,
            value=MOODS[saved.get("mood", 3) - 1],
            key=f"mood_{day_key}",
        )
        stress = st.slider(
            "2. Stress level (1 = calm, 10 = very stressed)",
            1, 10, int(saved.get("stress", 3)), key=f"stress_{day_key}",
        )
        st.markdown("**3. Sleep**")
        sleep_hours = st.number_input(
            "Hours slept", 0.0, 16.0, float(saved.get("sleep_hours", 7.0)),
            step=0.5, key=f"sleep_{day_key}",
        )
        sleep_quality = st.radio(
            "Sleep quality", SLEEP_QUALITY,
            index=SLEEP_QUALITY.index(saved.get("sleep_quality", "Okay")),
            horizontal=True, key=f"quality_{day_key}",
        )
        good = st.text_area("4. What went well today? 🌟", value=saved.get("good", ""), key=f"good_{day_key}")
        bad = st.text_area("What did not go well? 🌧", value=saved.get("bad", ""), key=f"bad_{day_key}")
        water = st.number_input(
            f"5. Water intake (glasses, goal {WATER_GOAL})", 0, 30, water_saved, key=f"water_{day_key}",
        )
        saved_clicked = st.form_submit_button("Save check-in")

    if saved_clicked:
        db, user = current_user()
        user["selfcare"]["checkins"][day_key] = {
            "mood": MOODS.index(mood_label) + 1,
            "stress": int(stress),
            "sleep_hours": float(sleep_hours),
            "sleep_quality": sleep_quality,
            "good": good.strip(),
            "bad": bad.strip(),
            "water": int(water),
        }
        save_db(db)
        flash("Check-in saved! 💕")
        st.rerun()

    # ---- last 7 days ----
    st.subheader("Last 7 days")
    rows = []
    for i in range(6, -1, -1):
        d = day - timedelta(days=i)
        c = checkins.get(d.isoformat())
        rows.append(
            {
                "Day": d.strftime("%a %d %b"),
                "Mood": MOODS[c["mood"] - 1].split()[0] if c else "-",
                "Stress": c["stress"] if c else "-",
                "Sleep (h)": c["sleep_hours"] if c else "-",
                "Water": c["water"] if c else "-",
            }
        )
    st.dataframe(pd.DataFrame(rows).astype(str), hide_index=True)


# ------------------------------------------------------------------
# 9. FINANCE TRACKER
# ------------------------------------------------------------------
def get_month(finance, ym):
    return finance["months"].setdefault(ym, {"income": 0.0, "saved": 0.0, "goal": 0.0})


def page_finance_home():
    page_header("💰 Finance Tracker", "home", "⬅ Main menu")
    calendar_block("finance")
    st.button("💰 Go to Finance page", on_click=go, args=("finance",))


def page_finance():
    page_header("💰 Finance", "finance_home", "⬅ Calendar")
    date_picker("Day / month", "date_finance")
    day = st.session_state.selected_date
    day_key = day.isoformat()
    ym = day.strftime("%Y-%m")
    st.caption(f"Showing: {day:%B %Y}")

    _, user = current_user()
    fin = user["finance"]
    month = get_month(fin, ym)
    fixed_total = sum(e["amount"] for e in fin["fixed"])
    month_daily = [e for e in fin["daily"] if e["date"].startswith(ym)]
    daily_total = sum(e["amount"] for e in month_daily)
    spent = fixed_total + daily_total

    # ---- 1. Monthly income ----
    st.subheader("1. Monthly income")
    with st.form(f"income_form_{ym}"):
        income = st.number_input(
            f"Income this month ({CURRENCY})", min_value=0.0, value=float(month["income"]),
            step=100.0, key=f"income_{ym}",
        )
        clicked = st.form_submit_button("Save income")
    if clicked:
        db, user = current_user()
        get_month(user["finance"], ym)["income"] = float(income)
        save_db(db)
        flash("Income saved!")
        st.rerun()

    # ---- 2. Monthly spending ----
    st.subheader("2. Monthly spending")
    st.metric("Spent this month", money(spent))
    st.caption(f"Fixed {money(fixed_total)} + daily {money(daily_total)}")
    st.metric("Money left (income - spent)", money(month["income"] - spent))

    # ---- 3. Fixed expenses ----
    st.subheader("3. Fixed expenses")
    with st.form("fixed_form", clear_on_submit=True):
        fixed_name = st.text_input("Name", placeholder="e.g. Rent, Wi-Fi, Subscription")
        fixed_amount = st.number_input(f"Amount per month ({CURRENCY})", min_value=0.0, step=50.0)
        clicked = st.form_submit_button("Add fixed expense")
    if clicked and fixed_name.strip() and fixed_amount > 0:
        db, user = current_user()
        user["finance"]["fixed"].append(
            {"id": secrets.token_hex(4), "name": fixed_name.strip(), "amount": float(fixed_amount)}
        )
        save_db(db)
        st.rerun()
    for e in fin["fixed"]:
        st.markdown(f"- **{e['name']}** - {money(e['amount'])} / month")
    if not fin["fixed"]:
        st.caption("No fixed expenses yet.")
    gone = remove_picker(fin["fixed"], lambda e: f"{e['name']} - {money(e['amount'])}", "rm_fixed")
    if gone:
        db, user = current_user()
        user["finance"]["fixed"] = [e for e in user["finance"]["fixed"] if e["id"] != gone]
        save_db(db)
        st.rerun()

    # ---- 4. Daily spending ----
    st.subheader("4. Daily spending")
    st.caption(f"Adding for: {day:%d %b %Y}")
    with st.form("daily_form", clear_on_submit=True):
        d_amount = st.number_input(f"Amount ({CURRENCY})", min_value=0.0, step=10.0)
        d_category = st.selectbox("Category", SPEND_CATEGORIES)
        d_note = st.text_input("Note (optional)")
        clicked = st.form_submit_button("Add spending")
    if clicked and d_amount > 0:
        db, user = current_user()
        user["finance"]["daily"].append(
            {"id": secrets.token_hex(4), "date": day_key, "amount": float(d_amount),
             "category": d_category, "note": d_note.strip()}
        )
        save_db(db)
        st.rerun()
    day_items = [e for e in fin["daily"] if e["date"] == day_key]
    st.metric("Spent on this day", money(sum(e["amount"] for e in day_items)))
    for e in day_items:
        note = f" - {e['note']}" if e["note"] else ""
        st.markdown(f"- **{e['category']}** {money(e['amount'])}{note}")
    gone = remove_picker(
        day_items, lambda e: f"{e['category']} {money(e['amount'])} {e['note']}".strip(), "rm_daily"
    )
    if gone:
        db, user = current_user()
        user["finance"]["daily"] = [e for e in user["finance"]["daily"] if e["id"] != gone]
        save_db(db)
        st.rerun()
    if month_daily:
        with st.expander("See all spending this month"):
            table = pd.DataFrame(
                [
                    {"Date": e["date"], "Category": e["category"], "Note": e["note"], "Amount": e["amount"]}
                    for e in sorted(month_daily, key=lambda e: e["date"])
                ]
            )
            st.dataframe(table, hide_index=True)

    # ---- 5. Debts ----
    st.subheader("5. Debts")
    with st.form("debt_form", clear_on_submit=True):
        who = st.text_input("Who do I owe?")
        debt_amount = st.number_input(f"Amount ({CURRENCY})", min_value=0.0, step=100.0, key="debt_amount")
        due = st.date_input("Due date", value=today(), format="DD/MM/YYYY", key="debt_due")
        clicked = st.form_submit_button("Add debt")
    if clicked and who.strip() and debt_amount > 0:
        db, user = current_user()
        user["finance"]["debts"].append(
            {"id": secrets.token_hex(4), "who": who.strip(), "amount": float(debt_amount), "due": due.isoformat()}
        )
        save_db(db)
        st.rerun()
    debts = sorted(fin["debts"], key=lambda e: e["due"])
    st.metric("Total I owe", money(sum(e["amount"] for e in debts)))
    for e in debts:
        overdue = " ⚠ overdue" if e["due"] < today().isoformat() else ""
        due_text = date.fromisoformat(e["due"]).strftime("%d %b %Y")
        st.markdown(f"- **{e['who']}** - {money(e['amount'])}, due {due_text}{overdue}")
    gone = remove_picker(debts, lambda e: f"{e['who']} - {money(e['amount'])}", "rm_debt")
    if gone:
        db, user = current_user()
        user["finance"]["debts"] = [e for e in user["finance"]["debts"] if e["id"] != gone]
        save_db(db)
        st.rerun()

    # ---- 6. Monthly savings ----
    st.subheader("6. Monthly savings")
    with st.form(f"savings_form_{ym}"):
        saved_amount = st.number_input(
            f"Saved this month ({CURRENCY})", min_value=0.0, value=float(month["saved"]),
            step=100.0, key=f"saved_{ym}",
        )
        goal = st.number_input(
            f"Savings goal ({CURRENCY})", min_value=0.0, value=float(month["goal"]),
            step=100.0, key=f"goal_{ym}",
        )
        clicked = st.form_submit_button("Save savings")
    if clicked:
        db, user = current_user()
        m = get_month(user["finance"], ym)
        m["saved"], m["goal"] = float(saved_amount), float(goal)
        save_db(db)
        flash("Savings saved!")
        st.rerun()
    if month["goal"] > 0:
        st.progress(min(month["saved"] / month["goal"], 1.0),
                    text=f"{money(month['saved'])} of {money(month['goal'])}")
    else:
        st.caption("Set a goal to see your progress bar.")

# ------------------------------------------------------------------
# Setting
# ------------------------------------------------------------------

def page_settings():
    page_header("⚙ Settings", "dashboard", "⬅ Dashboard")
    _, user = current_user()

    st.subheader("Change name")
    with st.form("name_form"):
        new_name = st.text_input("Your name", value=user["name"])
        name_clicked = st.form_submit_button("Save name")
    if name_clicked:
        if new_name.strip():
            db, user = current_user()
            user["name"] = new_name.strip()
            save_db(db)
            st.success("Name updated!")
        else:
            st.error("Name cannot be empty.")

    st.subheader("Change password")
    with st.form("password_form", clear_on_submit=True):
        old = st.text_input("Current password", type="password")
        new = st.text_input("New password (min 6 characters)", type="password")
        again = st.text_input("Confirm new password", type="password")
        pw_clicked = st.form_submit_button("Change password")
    if pw_clicked:
        db, user = current_user()
        if not hmac.compare_digest(hash_password(old, user["salt"]), user["hash"]):
            st.error("Current password is wrong.")
        elif len(new) < 6:
            st.error("New password must be at least 6 characters.")
        elif new != again:
            st.error("New passwords do not match.")
        else:
            user["salt"] = secrets.token_hex(16)
            user["hash"] = hash_password(new, user["salt"])
            # other devices must log in again with the new password
            token = st.session_state.get("session_token")
            keep = token_hash(token) if token else None
            user["sessions"] = [s for s in user.get("sessions", []) if s["hash"] == keep]
            save_db(db)
            st.success("Password changed!")

    st.subheader("Stay logged in")
    _, user = current_user()
    devices = sum(1 for s in user.get("sessions", []) if s["expires"] > time.time())
    if st.session_state.get("session_token"):
        st.caption("✅ This device stays logged in.")
    else:
        st.caption("This device will ask for your password next time. Log out and log in again with the tick box on.")
    st.caption(f"Devices that stay logged in: {devices}")
    st.caption("Cookie found when the app opened: " + ("yes" if cookie_at_start() else "no"))
    st.button("Log out on all devices", on_click=logout_everywhere)

    st.divider()
    st.button("Log out", on_click=logout)

# ------------------------------------------------------------------
# 10. MAIN - decide which page to show
# ------------------------------------------------------------------
PAGES = {
    "login": page_login,
    "home": page_home,
    "study_home": page_study_home,
    "dashboard": page_dashboard,
    "timetable": page_timetable,
    "timer": page_timer,
    "settings": page_settings,
    "meal_home": page_meal_home,
    "meal_plan": page_meal_plan,
    "grocery": page_grocery,
    "selfcare_home": page_selfcare_home,
    "checkin": page_checkin,
    "finance_home": page_finance_home,
    "finance": page_finance,
}

init_state()
clear_user_cache()  # every click starts with fresh data from the database
try_auto_login()  # remembered device? log in without a password

# If someone is not logged in (or the account vanished), send them to login
if st.session_state.page != "login":
    if not st.session_state.username or fetch_user(st.session_state.username) is None:
        logout()

PAGES[st.session_state.page]()
sync_cookie()  # write/delete the remember-me cookie in the browser
clear_user_cache()  # so the next click also starts fresh
