import sqlite3
from datetime import datetime, timedelta
from config import BOT_OWNER_USERNAME, BOT_OWNER_INITIAL_IDS

DB_PATH = "chatmanager.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS bot_owners (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        is_main INTEGER DEFAULT 0,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS chat_roles (
        chat_id INTEGER,
        user_id INTEGER,
        role_name TEXT,
        rank INTEGER,
        PRIMARY KEY (chat_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS warns (
        chat_id INTEGER,
        user_id INTEGER,
        count INTEGER DEFAULT 0,
        PRIMARY KEY (chat_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS features (
        user_id INTEGER,
        feature TEXT,
        PRIMARY KEY (user_id, feature)
    );
    CREATE TABLE IF NOT EXISTS free_grants (
        user_id INTEGER,
        feature TEXT,
        granted INTEGER DEFAULT 1,
        PRIMARY KEY (user_id, feature)
    );
    CREATE TABLE IF NOT EXISTS chat_settings (
        chat_id INTEGER,
        key TEXT,
        value TEXT,
        PRIMARY KEY (chat_id, key)
    );
    CREATE TABLE IF NOT EXISTS action_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        acting_id INTEGER,
        target_id INTEGER,
        action TEXT,
        detail TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS marriages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user1_id INTEGER,
        user2_id INTEGER,
        chat_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS marriage_proposals (
        proposer_id INTEGER,
        target_id INTEGER,
        chat_id INTEGER,
        msg_id INTEGER,
        PRIMARY KEY (proposer_id, target_id, chat_id)
    );
    CREATE TABLE IF NOT EXISTS disabled_chats (
        chat_id INTEGER PRIMARY KEY
    );
    CREATE TABLE IF NOT EXISTS activity (
        chat_id INTEGER,
        user_id INTEGER,
        score INTEGER DEFAULT 0,
        PRIMARY KEY (chat_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS achievements (
        user_id INTEGER,
        achievement TEXT,
        earned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, achievement)
    );
    CREATE TABLE IF NOT EXISTS duel_stats (
        user_id INTEGER PRIMARY KEY,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        feature TEXT,
        amount INTEGER,
        stars INTEGER,
        paid_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS bot_groups (
        chat_id INTEGER PRIMARY KEY,
        title TEXT,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS notes (
        chat_id INTEGER,
        name TEXT,
        content TEXT,
        PRIMARY KEY (chat_id, name)
    );
    CREATE TABLE IF NOT EXISTS filters (
        chat_id INTEGER,
        keyword TEXT,
        response TEXT,
        PRIMARY KEY (chat_id, keyword)
    );
    CREATE TABLE IF NOT EXISTS nicknames (
        chat_id INTEGER,
        user_id INTEGER,
        nick TEXT,
        PRIMARY KEY (chat_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS chat_members (
        chat_id INTEGER,
        user_id INTEGER,
        username TEXT,
        full_name TEXT,
        PRIMARY KEY (chat_id, user_id)
    );
    """)
    # Migrate: add is_main column if not exists
    try:
        conn.execute("ALTER TABLE bot_owners ADD COLUMN is_main INTEGER DEFAULT 0")
        conn.commit()
    except Exception:
        pass
    conn.commit()

    # Seed initial owners
    for uid in BOT_OWNER_INITIAL_IDS:
        conn.execute(
            "INSERT OR IGNORE INTO bot_owners (user_id, username, is_main) VALUES (?,?,1)",
            (uid, BOT_OWNER_USERNAME)
        )
    conn.commit()
    conn.close()

# ── USERS ──

def ensure_user(user_id, username):
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, username) VALUES (?,?)",
        (user_id, username)
    )
    conn.execute(
        "UPDATE users SET username=? WHERE user_id=?",
        (username, user_id)
    )
    conn.commit()
    conn.close()

def ensure_chat_member(chat_id, user_id, username, full_name=""):
    """Save a user seen in a specific chat — enables @username lookup per chat."""
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO chat_members (chat_id, user_id, username, full_name) VALUES (?,?,?,?)",
        (chat_id, user_id, (username or "").lower(), full_name or "")
    )
    # Also update global users table
    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, username) VALUES (?,?)",
        (user_id, username or "")
    )
    conn.execute(
        "UPDATE users SET username=? WHERE user_id=? AND (username IS NULL OR username='')",
        (username or "", user_id)
    )
    conn.commit()
    conn.close()

def find_in_chat_by_username(chat_id, username):
    """Find user_id by username within a specific chat."""
    conn = get_conn()
    row = conn.execute(
        "SELECT user_id FROM chat_members WHERE chat_id=? AND username=? COLLATE NOCASE",
        (chat_id, username)
    ).fetchone()
    conn.close()
    return row["user_id"] if row else None

def find_user_by_username(username):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None

# ── BOT OWNERS ──

def is_bot_owner(user_id):
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM bot_owners WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    if row:
        return True
    # Check by initial username
    u = conn if False else get_conn()
    row2 = u.execute(
        "SELECT user_id FROM users WHERE username=? COLLATE NOCASE AND user_id=?",
        (BOT_OWNER_USERNAME, user_id)
    ).fetchone()
    u.close()
    return bool(row2)

def is_main_owner(user_id):
    """Check if this is the primary (main) owner"""
    conn = get_conn()
    row = conn.execute("SELECT is_main FROM bot_owners WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    if row and row["is_main"]:
        return True
    # Fallback: check initial username match
    u = get_conn()
    row2 = u.execute("SELECT username FROM users WHERE user_id=?", (user_id,)).fetchone()
    u.close()
    if row2 and row2["username"] and row2["username"].lower() == BOT_OWNER_USERNAME.lower():
        return True
    return False

def add_bot_owner(user_id, username, is_main=False):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO bot_owners (user_id, username, is_main) VALUES (?,?,?)",
        (user_id, username, 1 if is_main else 0)
    )
    conn.commit()
    conn.close()

def remove_bot_owner(user_id):
    conn = get_conn()
    conn.execute("DELETE FROM bot_owners WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def get_bot_owners():
    conn = get_conn()
    rows = conn.execute("SELECT user_id, username, is_main FROM bot_owners ORDER BY is_main DESC, added_at ASC").fetchall()
    conn.close()
    result = []
    for r in rows:
        label = "👑 Владелец" if r["is_main"] else "🔑 Совладелец"
        uname = f"@{r['username']}" if r["username"] else f"<code>{r['user_id']}</code>"
        result.append(f"{label}: {uname} (<code>{r['user_id']}</code>)")
    return result

# ── ROLES ──

def get_role(chat_id, user_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM chat_roles WHERE chat_id=? AND user_id=?", (chat_id, user_id)
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def set_role(chat_id, user_id, role_name, rank):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO chat_roles (chat_id, user_id, role_name, rank) VALUES (?,?,?,?)",
        (chat_id, user_id, role_name, rank)
    )
    conn.commit()
    conn.close()

def remove_role(chat_id, user_id):
    conn = get_conn()
    conn.execute("DELETE FROM chat_roles WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    conn.commit()
    conn.close()

def get_chat_roles(chat_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT cr.*, u.username FROM chat_roles cr "
        "LEFT JOIN users u ON cr.user_id=u.user_id WHERE cr.chat_id=?", (chat_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── WARNS ──

def add_warn(chat_id, user_id):
    conn = get_conn()
    conn.execute(
        "INSERT INTO warns (chat_id, user_id, count) VALUES (?,?,1) "
        "ON CONFLICT(chat_id, user_id) DO UPDATE SET count=count+1",
        (chat_id, user_id)
    )
    conn.commit()
    row = conn.execute("SELECT count FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id)).fetchone()
    conn.close()
    return row["count"] if row else 1

def get_warns(chat_id, user_id):
    conn = get_conn()
    row = conn.execute("SELECT count FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id)).fetchone()
    conn.close()
    return row["count"] if row else 0

def reset_warns(chat_id, user_id):
    conn = get_conn()
    conn.execute("DELETE FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    conn.commit()
    conn.close()

# ── FEATURES ──

def has_feature(user_id, feature):
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM features WHERE user_id=? AND feature=?", (user_id, feature)).fetchone()
    conn.close()
    return bool(row)

def grant_feature(user_id, feature):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO features (user_id, feature) VALUES (?,?)", (user_id, feature))
    conn.commit()
    conn.close()

def revoke_feature(user_id, feature):
    conn = get_conn()
    conn.execute("DELETE FROM features WHERE user_id=? AND feature=?", (user_id, feature))
    conn.commit()
    conn.close()

def get_owned_features(user_id):
    conn = get_conn()
    rows = conn.execute("SELECT feature FROM features WHERE user_id=?", (user_id,)).fetchall()
    conn.close()
    return [r["feature"] for r in rows]

def has_free_grant(user_id, feature):
    conn = get_conn()
    row = conn.execute("SELECT granted FROM free_grants WHERE user_id=? AND feature=?", (user_id, feature)).fetchone()
    conn.close()
    return bool(row) and row["granted"] == 1

def set_free_grant(user_id, feature, granted):
    conn = get_conn()
    if granted:
        conn.execute("INSERT OR REPLACE INTO free_grants (user_id, feature, granted) VALUES (?,?,1)", (user_id, feature))
    else:
        conn.execute("DELETE FROM free_grants WHERE user_id=? AND feature=?", (user_id, feature))
    conn.commit()
    conn.close()

def grant_all_free(user_id, features_list):
    """Give free access to all listed features."""
    conn = get_conn()
    for fid in features_list:
        conn.execute("INSERT OR REPLACE INTO free_grants (user_id, feature, granted) VALUES (?,?,1)", (user_id, fid))
        conn.execute("INSERT OR IGNORE INTO features (user_id, feature) VALUES (?,?)", (user_id, fid))
    conn.commit()
    conn.close()

def revoke_all_free(user_id, features_list):
    """Remove free access and ownership of all listed features."""
    conn = get_conn()
    for fid in features_list:
        conn.execute("DELETE FROM free_grants WHERE user_id=? AND feature=?", (user_id, fid))
        conn.execute("DELETE FROM features WHERE user_id=? AND feature=?", (user_id, fid))
    conn.commit()
    conn.close()

# ── SETTINGS ──

def set_setting(chat_id, key, value):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO chat_settings (chat_id, key, value) VALUES (?,?,?)", (chat_id, key, value))
    conn.commit()
    conn.close()

def get_setting(chat_id, key):
    conn = get_conn()
    row = conn.execute("SELECT value FROM chat_settings WHERE chat_id=? AND key=?", (chat_id, key)).fetchone()
    conn.close()
    return row["value"] if row else None

# ── LOG ──

def log_action(chat_id, acting_id, target_id, action, detail):
    conn = get_conn()
    conn.execute("INSERT INTO action_log (chat_id, acting_id, target_id, action, detail) VALUES (?,?,?,?,?)",
                 (chat_id, acting_id, target_id, action, detail))
    conn.commit()
    conn.close()

def get_stats(chat_id=None):
    conn = get_conn()
    now = datetime.now()
    day_ago   = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    week_ago  = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    month_ago = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    year_ago  = (now - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")

    if chat_id:
        base = "WHERE chat_id=?"
        args = (chat_id,)
    else:
        base = "WHERE 1=1"
        args = ()

    def q(extra="", extra_args=()):
        a = args + extra_args
        return conn.execute(f"SELECT COUNT(*) as c FROM action_log {base} {extra}", a).fetchone()["c"]

    total_users    = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    total_groups   = conn.execute("SELECT COUNT(*) as c FROM bot_groups").fetchone()["c"]
    total_actions  = q()
    total_marriages = conn.execute("SELECT COUNT(*) as c FROM marriages").fetchone()["c"]

    # Payments
    total_earned   = conn.execute("SELECT COALESCE(SUM(stars),0) as s FROM payments").fetchone()["s"]
    day_earned     = conn.execute("SELECT COALESCE(SUM(stars),0) as s FROM payments WHERE paid_at>?", (day_ago,)).fetchone()["s"]
    week_earned    = conn.execute("SELECT COALESCE(SUM(stars),0) as s FROM payments WHERE paid_at>?", (week_ago,)).fetchone()["s"]
    month_earned   = conn.execute("SELECT COALESCE(SUM(stars),0) as s FROM payments WHERE paid_at>?", (month_ago,)).fetchone()["s"]
    year_earned    = conn.execute("SELECT COALESCE(SUM(stars),0) as s FROM payments WHERE paid_at>?", (year_ago,)).fetchone()["s"]

    actions_day    = q("AND created_at>?", (day_ago,))
    actions_week   = q("AND created_at>?", (week_ago,))
    actions_month  = q("AND created_at>?", (month_ago,))

    new_users_day   = conn.execute("SELECT COUNT(*) as c FROM users WHERE created_at>?", (day_ago,)).fetchone()["c"]
    new_users_week  = conn.execute("SELECT COUNT(*) as c FROM users WHERE created_at>?", (week_ago,)).fetchone()["c"]
    new_users_month = conn.execute("SELECT COUNT(*) as c FROM users WHERE created_at>?", (month_ago,)).fetchone()["c"]

    conn.close()
    return {
        "users": total_users,
        "groups": total_groups,
        "actions": total_actions,
        "marriages": total_marriages,
        "earned_total": total_earned,
        "earned_day": day_earned,
        "earned_week": week_earned,
        "earned_month": month_earned,
        "earned_year": year_earned,
        "actions_day": actions_day,
        "actions_week": actions_week,
        "actions_month": actions_month,
        "new_users_day": new_users_day,
        "new_users_week": new_users_week,
        "new_users_month": new_users_month,
    }

def record_payment(user_id, feature, stars):
    conn = get_conn()
    conn.execute(
        "INSERT INTO payments (user_id, feature, amount, stars) VALUES (?,?,?,?)",
        (user_id, feature, stars, stars)
    )
    conn.commit()
    conn.close()

# ── BOT GROUPS ──

def register_group(chat_id, title):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO bot_groups (chat_id, title) VALUES (?,?)",
        (chat_id, title)
    )
    conn.commit()
    conn.close()

def get_all_groups():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM bot_groups ORDER BY added_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── MARRIAGES ──

def is_married(user_id, chat_id=None):
    conn = get_conn()
    if chat_id:
        row = conn.execute("SELECT * FROM marriages WHERE (user1_id=? OR user2_id=?) AND chat_id=?",
                           (user_id, user_id, chat_id)).fetchone()
    else:
        row = conn.execute("SELECT * FROM marriages WHERE user1_id=? OR user2_id=?", (user_id, user_id)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_spouse_id(user_id, chat_id):
    m = is_married(user_id, chat_id)
    if not m:
        return None
    return m["user2_id"] if m["user1_id"] == user_id else m["user1_id"]

def create_marriage(user1_id, user2_id, chat_id):
    conn = get_conn()
    conn.execute("INSERT INTO marriages (user1_id, user2_id, chat_id) VALUES (?,?,?)", (user1_id, user2_id, chat_id))
    conn.commit()
    conn.close()

def divorce(user_id, chat_id):
    conn = get_conn()
    conn.execute("DELETE FROM marriages WHERE (user1_id=? OR user2_id=?) AND chat_id=?", (user_id, user_id, chat_id))
    conn.commit()
    conn.close()

def divorce_by_id(marriage_id):
    conn = get_conn()
    conn.execute("DELETE FROM marriages WHERE id=?", (marriage_id,))
    conn.commit()
    conn.close()

def get_all_marriages(chat_id):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM marriages WHERE chat_id=?", (chat_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_marriages_global():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM marriages").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def add_proposal(proposer_id, target_id, chat_id, msg_id):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO marriage_proposals VALUES (?,?,?,?)",
                 (proposer_id, target_id, chat_id, msg_id))
    conn.commit()
    conn.close()

def get_proposal(proposer_id, target_id, chat_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM marriage_proposals WHERE proposer_id=? AND target_id=? AND chat_id=?",
                       (proposer_id, target_id, chat_id)).fetchone()
    conn.close()
    return dict(row) if row else None

def remove_proposal(proposer_id, target_id, chat_id):
    conn = get_conn()
    conn.execute("DELETE FROM marriage_proposals WHERE proposer_id=? AND target_id=? AND chat_id=?",
                 (proposer_id, target_id, chat_id))
    conn.commit()
    conn.close()

# ── DISABLED CHATS ──

def is_chat_disabled(chat_id):
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM disabled_chats WHERE chat_id=?", (chat_id,)).fetchone()
    conn.close()
    return bool(row)

def disable_chat(chat_id):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO disabled_chats (chat_id) VALUES (?)", (chat_id,))
    conn.commit()
    conn.close()

def enable_chat(chat_id):
    conn = get_conn()
    conn.execute("DELETE FROM disabled_chats WHERE chat_id=?", (chat_id,))
    conn.commit()
    conn.close()

# ── ACTIVITY ──

def add_activity(chat_id, user_id, pts=1):
    conn = get_conn()
    conn.execute(
        "INSERT INTO activity (chat_id, user_id, score) VALUES (?,?,?) "
        "ON CONFLICT(chat_id, user_id) DO UPDATE SET score=score+?",
        (chat_id, user_id, pts, pts)
    )
    conn.commit()
    conn.close()

def get_top_activity(chat_id, limit=10):
    conn = get_conn()
    rows = conn.execute(
        "SELECT a.user_id, a.score, u.username FROM activity a "
        "LEFT JOIN users u ON a.user_id=u.user_id "
        "WHERE a.chat_id=? ORDER BY a.score DESC LIMIT ?", (chat_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── ACHIEVEMENTS ──

ACHIEVEMENTS_DEF = {
    "first_duel":    ("⚔️", "Первая дуэль",      "Принять участие в дуэли"),
    "duel_winner_5": ("🏆", "Ветеран дуэлей",    "Выиграть 5 дуэлей"),
    "married":       ("💍", "Женитьба",           "Вступить в брак"),
    "luck_100":      ("🎯", "Идеальный!",         "Выбросить 100 в рулетке"),
    "warn_free":     ("😇", "Образцовый",         "Ни одного предупреждения"),
    "casino_win":    ("🎲", "Счастливчик",        "Выиграть тройное совпадение в казино"),
    "top_1":         ("🥇", "Первый в топе",      "Занять 1 место в активности"),
}

def grant_achievement(user_id, code):
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO achievements (user_id, achievement) VALUES (?,?)", (user_id, code))
    conn.commit()
    conn.close()

def has_achievement(user_id, code):
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM achievements WHERE user_id=? AND achievement=?", (user_id, code)).fetchone()
    conn.close()
    return bool(row)

def get_achievements(user_id):
    conn = get_conn()
    rows = conn.execute("SELECT achievement, earned_at FROM achievements WHERE user_id=?", (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── DUEL STATS ──

def record_duel(winner_id, loser_id):
    conn = get_conn()
    for uid, col in [(winner_id, "wins"), (loser_id, "losses")]:
        conn.execute(
            f"INSERT INTO duel_stats (user_id, {col}) VALUES (?,1) "
            f"ON CONFLICT(user_id) DO UPDATE SET {col}={col}+1", (uid,)
        )
    conn.commit()
    conn.close()

def get_duel_stats(user_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM duel_stats WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else {"wins": 0, "losses": 0}

# ── NOTES ──

def set_note(chat_id, name, content):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO notes (chat_id, name, content) VALUES (?,?,?)", (chat_id, name, content))
    conn.commit()
    conn.close()

def get_note(chat_id, name):
    conn = get_conn()
    row = conn.execute("SELECT content FROM notes WHERE chat_id=? AND name=? COLLATE NOCASE", (chat_id, name)).fetchone()
    conn.close()
    return row["content"] if row else None

def del_note(chat_id, name):
    conn = get_conn()
    conn.execute("DELETE FROM notes WHERE chat_id=? AND name=? COLLATE NOCASE", (chat_id, name))
    conn.commit()
    conn.close()

def get_all_notes(chat_id):
    conn = get_conn()
    rows = conn.execute("SELECT name FROM notes WHERE chat_id=? ORDER BY name", (chat_id,)).fetchall()
    conn.close()
    return [r["name"] for r in rows]

# ── FILTERS ──

def set_filter(chat_id, keyword, response):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO filters (chat_id, keyword, response) VALUES (?,?,?)", (chat_id, keyword, response))
    conn.commit()
    conn.close()

def get_filters(chat_id):
    conn = get_conn()
    rows = conn.execute("SELECT keyword, response FROM filters WHERE chat_id=?", (chat_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def del_filter(chat_id, keyword):
    conn = get_conn()
    conn.execute("DELETE FROM filters WHERE chat_id=? AND keyword=? COLLATE NOCASE", (chat_id, keyword))
    conn.commit()
    conn.close()

# ── NICKNAMES ──

def set_nick(chat_id, user_id, nick):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO nicknames (chat_id, user_id, nick) VALUES (?,?,?)", (chat_id, user_id, nick))
    conn.commit()
    conn.close()

def get_nick(chat_id, user_id):
    conn = get_conn()
    row = conn.execute("SELECT nick FROM nicknames WHERE chat_id=? AND user_id=?", (chat_id, user_id)).fetchone()
    conn.close()
    return row["nick"] if row else None

def remove_nick(chat_id, user_id):
    conn = get_conn()
    conn.execute("DELETE FROM nicknames WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    conn.commit()
    conn.close()
