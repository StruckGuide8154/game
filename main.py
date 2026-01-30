import os
import time
import json
import math
import random
import secrets
import sqlite3
from functools import wraps

from flask import (
    Flask, request, jsonify, session,
    render_template, g, abort
)
from werkzeug.security import generate_password_hash, check_password_hash

# ---------------------------------------------------------------------------
# App & config
# ---------------------------------------------------------------------------
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# Redis-backed server-side sessions via Flask-Session
redis_url = os.environ.get("REDIS_URL")
if redis_url:
    from flask_session import Session
    import redis as _redis
    app.config["SESSION_TYPE"] = "redis"
    app.config["SESSION_REDIS"] = _redis.from_url(redis_url)
    app.config["SESSION_USE_SIGNER"] = True
    app.config["SESSION_KEY_PREFIX"] = "fae:"
    Session(app)
else:
    # Fallback: signed cookie sessions (still HMAC-protected)
    app.config["SESSION_TYPE"] = "filesystem"

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SECURE_COOKIES", "0") == "1",
    PERMANENT_SESSION_LIFETIME=86400,
)

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

storage_uri = redis_url if redis_url else "memory://"
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["120 per minute"],
    storage_uri=storage_uri,
)

# ---------------------------------------------------------------------------
# Security headers  (flask-talisman)
# ---------------------------------------------------------------------------
from flask_talisman import Talisman

csp = {
    "default-src": "'self'",
    "script-src": ["'self'", "https://cdn.tailwindcss.com", "'unsafe-inline'"],
    "style-src": ["'self'", "'unsafe-inline'"],
    "img-src": "'self' data:",
    "connect-src": "'self'",
    "font-src": "'self'",
    "object-src": "'none'",
    "frame-ancestors": "'none'",
    "base-uri": "'self'",
    "form-action": "'self'",
}

force_https = os.environ.get("FORCE_HTTPS", "0") == "1"
Talisman(
    app,
    force_https=force_https,
    content_security_policy=csp,
    session_cookie_secure=os.environ.get("SECURE_COOKIES", "0") == "1",
    strict_transport_security=True,
    strict_transport_security_max_age=31536000,
    x_content_type_options=True,
    x_xss_protection=True,
    referrer_policy="strict-origin-when-cross-origin",
)

# ---------------------------------------------------------------------------
# CSRF token management
# ---------------------------------------------------------------------------
@app.before_request
def ensure_csrf():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)


def check_csrf():
    """Validate CSRF on every mutating request."""
    token = (
        request.headers.get("X-CSRF-Token")
        or request.form.get("csrf_token")
        or (request.get_json(silent=True) or {}).get("csrf_token")
    )
    if not token or not secrets.compare_digest(token, session.get("csrf_token", "")):
        abort(403, "CSRF validation failed")


@app.after_request
def add_extra_headers(resp):
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Pragma"] = "no-cache"
    return resp


# ---------------------------------------------------------------------------
# Database helpers  (SQLite – single file, zero config)
# ---------------------------------------------------------------------------
DATABASE = os.path.join(os.path.dirname(__file__), "game.db")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE)
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT    UNIQUE NOT NULL,
            pw_hash     TEXT    NOT NULL,
            created_at  REAL    NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS game_states (
            user_id     INTEGER PRIMARY KEY REFERENCES users(id),
            state_json  TEXT    NOT NULL,
            updated_at  REAL   NOT NULL
        )
    """)
    db.commit()
    db.close()


init_db()


# ---------------------------------------------------------------------------
# Auth decorator
# ---------------------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Not authenticated"}), 401
        return f(*args, **kwargs)
    return decorated


def mutating(f):
    """Decorator that checks CSRF + login for mutating endpoints."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        check_csrf()
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Game constants  (authoritative – never sent in raw form to client)
# ---------------------------------------------------------------------------
MARKETS = [
    {"name": "Youth Academy",     "multiplier": 1,  "minRep": 0,     "color": "text-gray-400"},
    {"name": "Domestic League",   "multiplier": 3,  "minRep": 500,   "color": "text-green-400"},
    {"name": "European Circuit",  "multiplier": 8,  "minRep": 2000,  "color": "text-blue-400"},
    {"name": "World Class",       "multiplier": 20, "minRep": 10000, "color": "text-yellow-400"},
    {"name": "Global Superstars", "multiplier": 50, "minRep": 50000, "color": "text-purple-400"},
]

PLAYER_TIERS = [
    {"name": "Prospect",      "baseValue": 1,    "valueRange": [1, 5],       "multiplier": 1,  "color": "bg-gray-500",   "minRep": 0},
    {"name": "Rising Star",   "baseValue": 5,    "valueRange": [5, 15],      "multiplier": 2,  "color": "bg-green-500",  "minRep": 100},
    {"name": "Professional",  "baseValue": 25,   "valueRange": [20, 40],     "multiplier": 5,  "color": "bg-blue-500",   "minRep": 500},
    {"name": "International", "baseValue": 100,  "valueRange": [80, 150],    "multiplier": 10, "color": "bg-purple-500", "minRep": 2000},
    {"name": "World Class",   "baseValue": 500,  "valueRange": [400, 700],   "multiplier": 25, "color": "bg-yellow-500", "minRep": 10000},
    {"name": "Superstar",     "baseValue": 2500, "valueRange": [2000, 3500], "multiplier": 50, "color": "bg-red-500",    "minRep": 50000},
]

FIRST_NAMES = [
    "Tommy", "Alex", "Jordan", "Sam", "Chris", "Taylor", "Morgan", "Casey",
    "Riley", "Jamie", "Marco", "Luis", "Carlos", "Diego", "Andre", "Paulo",
    "Kevin", "Michael", "David", "Robert", "Luca", "Pierre", "Hans",
    "Giovanni", "Pablo", "Yuki", "Ahmed", "Viktor", "Mateo", "Lucas",
]

LAST_NAMES = [
    "James", "Martinez", "Silva", "Johnson", "O'Connor", "Mueller",
    "Nakamura", "Santos", "Williams", "Garcia", "Ibrahim", "Chen",
    "Rodriguez", "Smith", "Brown", "Davis", "Wilson", "Anderson",
    "Taylor", "Thomas", "Rossi", "Blanc", "Schmidt", "Petrov", "Costa",
]

CLUBS = [
    "Manchester FC", "Real Madrid", "Bayern Munich", "Barcelona", "PSG",
    "Juventus", "Liverpool", "Chelsea", "AC Milan", "Inter Milan",
    "Atletico", "Dortmund",
]

UPGRADE_TYPES = {
    "scoutingNetwork":   {"name": "Scouting Network",     "baseCost": 1000,  "icon": "scout",   "description": "Discover talent worldwide",        "effect": "Increases reputation gain by 25% per level"},
    "negotiationSkills": {"name": "Negotiation Skills",    "baseCost": 2000,  "icon": "negot",   "description": "Secure better deals",              "effect": "Increases commission by 20% per level"},
    "officeSpace":       {"name": "Office Expansion",      "baseCost": 5000,  "icon": "office",  "description": "Expand your agency capacity",      "effect": "+1 agent and +2 player slots per level"},
    "marketingTeam":     {"name": "Marketing Department",  "baseCost": 10000, "icon": "market",  "description": "Unlock premium markets",            "effect": "Access higher tier players"},
    "legalTeam":         {"name": "Legal Department",      "baseCost": 25000, "icon": "legal",   "description": "Handle complex transfers",         "effect": "Earn 10% bonus on transfers"},
    "mediaConnections":  {"name": "Media Network",         "baseCost": 50000, "icon": "media",   "description": "Attract sponsorships",             "effect": "Players earn sponsorship deals"},
}


# ---------------------------------------------------------------------------
# Server-side game logic helpers
# ---------------------------------------------------------------------------
def default_state():
    now = time.time()
    return {
        "money": 100,
        "reputation": 0,
        "agents": 1,
        "players": [],
        "upgrades": {
            "scoutingNetwork": 0,
            "negotiationSkills": 0,
            "officeSpace": 0,
            "marketingTeam": 0,
            "legalTeam": 0,
            "mediaConnections": 0,
        },
        "transfersCompleted": 0,
        "totalCommission": 0,
        "activeSponsorships": 0,
        "nextTransferWindow": now + 20,
        "autoSignEnabled": False,
        "lastTickTime": now,
        "lastExpenseTime": now,
        "lastGrowthTime": now,
        "notifications": [],
        # Ephemeral (per-session scouting/deals)
        "scoutedPlayers": [],
        "availableDeals": [],
    }


def _upgrade_cost(key, level):
    base = UPGRADE_TYPES[key]["baseCost"]
    return math.floor(base * (1.5 ** level))


def _max_agents(st):
    return 1 + st["upgrades"]["officeSpace"]


def _max_players(st):
    return 3 + st["upgrades"]["officeSpace"] * 2


def _commission_mult(st):
    return (1 + st["upgrades"]["negotiationSkills"] * 0.2) * (1 + st["upgrades"]["legalTeam"] * 0.1)


def _rep_mult(st):
    return 1 + st["upgrades"]["scoutingNetwork"] * 0.25


def _sponsor_chance(st):
    return min(0.5, st["upgrades"]["mediaConnections"] * 0.1)


def _available_tiers(st):
    marketing = st["upgrades"]["marketingTeam"]
    eligible = [t for t in PLAYER_TIERS if t["minRep"] <= st["reputation"]]
    return eligible[: 2 + marketing]


def _unlocked_markets(st):
    return [m for m in MARKETS if m["minRep"] <= st["reputation"]]


def _earnings_per_second(st):
    total = 0.0
    for p in st["players"]:
        total += p["value"] * p["multiplier"] * _commission_mult(st) * st["agents"]
        if p.get("hasSponsorship"):
            total += p.get("sponsorshipValue", 0)
    return total


def _add_notif(st, message, ntype="success"):
    st["notifications"].append({
        "id": random.random(),
        "message": message,
        "type": ntype,
        "timestamp": time.time(),
    })
    # Prune old
    cutoff = time.time() - 10
    st["notifications"] = [n for n in st["notifications"] if n["timestamp"] > cutoff]


def _process_tick(st, elapsed_seconds):
    """Compute server-authoritative game tick for the elapsed time."""
    if elapsed_seconds <= 0:
        return

    # Cap elapsed to 1 hour of offline progress max
    elapsed_seconds = min(elapsed_seconds, 3600)

    ticks = int(elapsed_seconds)
    if ticks < 1:
        ticks = 1

    for _ in range(ticks):
        total_earnings = 0.0
        for player in st["players"]:
            variation = 0.7 + random.random() * 0.6
            base = player["value"] * player["multiplier"] * _commission_mult(st) * st["agents"]
            earning = base * variation
            total_earnings += earning
            player["earnings"] = player.get("earnings", 0) + earning

            if player.get("hasSponsorship"):
                sv = player.get("sponsorshipValue", 0) * (0.8 + random.random() * 0.4)
                total_earnings += sv
                player["earnings"] += sv

            # Sponsorship chance
            if not player.get("hasSponsorship"):
                if random.random() < _sponsor_chance(st) / 100:
                    player["hasSponsorship"] = True
                    player["sponsorshipValue"] = player["value"] * player["multiplier"] * 2
                    st["activeSponsorships"] = st.get("activeSponsorships", 0) + 1

        st["money"] += total_earnings

    # Expenses (every 120s after 300s grace)
    now = time.time()
    grace_end = st["lastExpenseTime"] + 300
    if now > grace_end:
        months_passed = int((now - grace_end) / 120)
        if months_passed > 0:
            office_expense = st["upgrades"]["officeSpace"] * 50
            agent_salaries = st["agents"] * 200
            per_player = len(st["players"]) * 10
            total_expense = (office_expense + agent_salaries + per_player) * months_passed
            st["money"] -= total_expense
            st["lastExpenseTime"] = now - 300  # keep offset
            if total_expense > 0:
                _add_notif(st, f"Monthly expenses: -{_fmt(total_expense)}", "error")

    # Player growth (every 300s)
    if now - st["lastGrowthTime"] > 300:
        for player in st["players"]:
            if random.random() < 0.3:
                growth = math.floor(player["value"] * (0.05 + random.random() * 0.15))
                player["value"] += growth
            elif random.random() < 0.1:
                decline = math.floor(player["value"] * (0.05 + random.random() * 0.10))
                player["value"] = max(1, player["value"] - decline)
        st["lastGrowthTime"] = now

    # Auto-sign
    if st.get("autoSignEnabled") and len(st["players"]) < _max_players(st) and st["money"] > 10000:
        if random.random() < 0.05 * ticks:
            tiers = _available_tiers(st)
            if tiers:
                tier = tiers[0]
                mn, mx = tier["valueRange"]
                pv = random.randint(mn, mx)
                st["players"].append({
                    "id": random.random(),
                    "name": f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}",
                    "tier": tier["name"],
                    "value": pv,
                    "multiplier": tier["multiplier"],
                    "color": tier["color"],
                    "earnings": 0,
                    "hasSponsorship": False,
                    "sponsorshipValue": 0,
                })
                st["reputation"] += math.floor(tier["baseValue"] / 5 * _rep_mult(st))

    st["lastTickTime"] = now


def _fmt(amount):
    if amount >= 1e9:
        return f"${amount/1e9:.2f}B"
    if amount >= 1e6:
        return f"${amount/1e6:.2f}M"
    if amount >= 1e3:
        return f"${amount/1e3:.1f}K"
    return f"${int(amount)}"


# ---------------------------------------------------------------------------
# DB helpers for game state
# ---------------------------------------------------------------------------
def load_state(user_id):
    db = get_db()
    row = db.execute("SELECT state_json FROM game_states WHERE user_id=?", (user_id,)).fetchone()
    if row:
        return json.loads(row["state_json"])
    return default_state()


def save_state(user_id, st):
    db = get_db()
    now = time.time()
    db.execute(
        """INSERT INTO game_states (user_id, state_json, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at""",
        (user_id, json.dumps(st), now),
    )
    db.commit()


def tick_and_save(user_id, st):
    """Process elapsed time, save, and return state."""
    now = time.time()
    elapsed = now - st.get("lastTickTime", now)
    _process_tick(st, elapsed)
    save_state(user_id, st)
    return st


# ---------------------------------------------------------------------------
# Routes – pages
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("game.html")


# ---------------------------------------------------------------------------
# Routes – auth
# ---------------------------------------------------------------------------
@app.route("/api/register", methods=["POST"])
@limiter.limit("5 per minute")
def register():
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or len(username) < 3 or len(username) > 20:
        return jsonify({"error": "Username must be 3-20 characters"}), 400
    if not username.isalnum():
        return jsonify({"error": "Username must be alphanumeric"}), 400
    if len(password) < 6 or len(password) > 128:
        return jsonify({"error": "Password must be 6-128 characters"}), 400

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if existing:
        return jsonify({"error": "Username already taken"}), 409

    pw_hash = generate_password_hash(password, method="scrypt")
    cur = db.execute(
        "INSERT INTO users (username, pw_hash, created_at) VALUES (?, ?, ?)",
        (username, pw_hash, time.time()),
    )
    db.commit()

    user_id = cur.lastrowid
    session.permanent = True
    session["user_id"] = user_id
    session["username"] = username

    # Initialize game state
    st = default_state()
    save_state(user_id, st)

    return jsonify({"ok": True, "username": username, "csrf_token": session["csrf_token"]})


@app.route("/api/login", methods=["POST"])
@limiter.limit("10 per minute")
def login():
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    db = get_db()
    user = db.execute("SELECT id, pw_hash FROM users WHERE username=?", (username,)).fetchone()
    if not user or not check_password_hash(user["pw_hash"], password):
        return jsonify({"error": "Invalid credentials"}), 401

    session.permanent = True
    session["user_id"] = user["id"]
    session["username"] = username
    return jsonify({"ok": True, "username": username, "csrf_token": session["csrf_token"]})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/csrf", methods=["GET"])
def get_csrf():
    """Return a fresh CSRF token (needed on first page load)."""
    return jsonify({"csrf_token": session["csrf_token"]})


@app.route("/api/me", methods=["GET"])
def me():
    if "user_id" in session:
        return jsonify({"username": session["username"], "csrf_token": session["csrf_token"]})
    return jsonify({"username": None})


# ---------------------------------------------------------------------------
# Routes – game state
# ---------------------------------------------------------------------------
@app.route("/api/state", methods=["GET"])
@login_required
def get_state():
    uid = session["user_id"]
    st = load_state(uid)
    st = tick_and_save(uid, st)
    return jsonify(_sanitize(st))


def _sanitize(st):
    """Return client-safe view of state (no internal fields)."""
    return {
        "money": st["money"],
        "reputation": st["reputation"],
        "agents": st["agents"],
        "players": st["players"],
        "upgrades": st["upgrades"],
        "transfersCompleted": st["transfersCompleted"],
        "totalCommission": st["totalCommission"],
        "activeSponsorships": st["activeSponsorships"],
        "nextTransferWindow": st["nextTransferWindow"],
        "autoSignEnabled": st.get("autoSignEnabled", False),
        "notifications": st.get("notifications", []),
        "scoutedPlayers": st.get("scoutedPlayers", []),
        "availableDeals": st.get("availableDeals", []),
        # Derived
        "maxAgents": _max_agents(st),
        "maxPlayers": _max_players(st),
        "earningsPerSecond": _earnings_per_second(st),
        "commissionMultiplier": _commission_mult(st),
        "reputationMultiplier": _rep_mult(st),
        "unlockedMarkets": _unlocked_markets(st),
        "availableTiers": _available_tiers(st),
        "upgradeCosts": {k: _upgrade_cost(k, st["upgrades"][k]) for k in UPGRADE_TYPES},
        "hireAgentCost": 10000 * (2 ** (st["agents"] - 1)),
        "upgradeInfo": UPGRADE_TYPES,
        "serverTime": time.time(),
    }


# ---------------------------------------------------------------------------
# Routes – game actions (all server-authoritative)
# ---------------------------------------------------------------------------
@app.route("/api/scout", methods=["POST"])
@mutating
def scout_players():
    uid = session["user_id"]
    st = load_state(uid)
    _process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    tiers = _available_tiers(st)
    if not tiers:
        return jsonify({"error": "No tiers available"}), 400

    num = 4 + st["upgrades"]["scoutingNetwork"] // 2
    scouted = []
    for _ in range(num):
        weights = [2 ** (len(tiers) - i - 1) for i in range(len(tiers))]
        total_w = sum(weights)
        r = random.random() * total_w
        sel = 0
        for j, w in enumerate(weights):
            r -= w
            if r <= 0:
                sel = j
                break
        tier = tiers[sel]
        mn, mx = tier["valueRange"]
        pv = random.randint(mn, mx)
        rep_req = tier["minRep"] + random.randint(0, tier["baseValue"] * 5)
        if st["reputation"] >= rep_req:
            acc = 100
        else:
            acc = min(95, max(10, int(100 - ((rep_req - st["reputation"]) / max(rep_req, 1)) * 100)))
        scouted.append({
            "id": secrets.token_hex(8),
            "name": f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}",
            "tier": tier["name"],
            "value": pv,
            "multiplier": tier["multiplier"],
            "color": tier["color"],
            "repRequired": rep_req,
            "acceptanceChance": acc,
            "demanded": st["reputation"] < rep_req,
        })

    scouted.sort(key=lambda p: p["repRequired"])
    st["scoutedPlayers"] = scouted
    save_state(uid, st)
    return jsonify({"players": scouted})


@app.route("/api/sign", methods=["POST"])
@mutating
def sign_player():
    uid = session["user_id"]
    st = load_state(uid)
    _process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    data = request.get_json(force=True)
    pid = data.get("playerId")
    if not pid:
        return jsonify({"error": "Missing playerId"}), 400

    # Find in scouted list (server-authoritative)
    player = None
    for p in st.get("scoutedPlayers", []):
        if p["id"] == pid:
            player = p
            break
    if not player:
        return jsonify({"error": "Player not found in scouted list"}), 404

    if len(st["players"]) >= _max_players(st):
        return jsonify({"error": "Roster full"}), 400

    # Roll acceptance server-side
    roll = random.random() * 100
    if roll <= player["acceptanceChance"]:
        new_player = {
            "id": secrets.token_hex(8),
            "name": player["name"],
            "tier": player["tier"],
            "value": player["value"],
            "multiplier": player["multiplier"],
            "color": player["color"],
            "earnings": 0,
            "hasSponsorship": False,
            "sponsorshipValue": 0,
        }
        st["players"].append(new_player)
        st["reputation"] += math.floor(player["value"] / 5 * _rep_mult(st))
        st["scoutedPlayers"] = []  # close scouting after sign
        _add_notif(st, f"Signed {player['name']}!", "success")
        save_state(uid, st)
        return jsonify({"ok": True, "signed": True, "player": new_player, "state": _sanitize(st)})
    else:
        st["scoutedPlayers"] = [p for p in st["scoutedPlayers"] if p["id"] != pid]
        _add_notif(st, f"{player['name']} declined", "error")
        save_state(uid, st)
        return jsonify({"ok": True, "signed": False, "state": _sanitize(st)})


@app.route("/api/upgrade", methods=["POST"])
@mutating
def purchase_upgrade():
    uid = session["user_id"]
    st = load_state(uid)
    _process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    data = request.get_json(force=True)
    key = data.get("upgrade")
    if key not in UPGRADE_TYPES:
        return jsonify({"error": "Invalid upgrade"}), 400

    cost = _upgrade_cost(key, st["upgrades"][key])
    if st["money"] < cost:
        return jsonify({"error": "Not enough money"}), 400

    st["money"] -= cost
    st["upgrades"][key] += 1
    _add_notif(st, f"Upgraded {UPGRADE_TYPES[key]['name']}", "success")
    save_state(uid, st)
    return jsonify({"ok": True, "state": _sanitize(st)})


@app.route("/api/hire-agent", methods=["POST"])
@mutating
def hire_agent():
    uid = session["user_id"]
    st = load_state(uid)
    _process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    cost = 10000 * (2 ** (st["agents"] - 1))
    if st["money"] < cost:
        return jsonify({"error": "Not enough money"}), 400
    if st["agents"] >= _max_agents(st):
        return jsonify({"error": "Max agents reached"}), 400

    st["money"] -= cost
    st["agents"] += 1
    _add_notif(st, "Hired new agent!", "success")
    save_state(uid, st)
    return jsonify({"ok": True, "state": _sanitize(st)})


@app.route("/api/transfer-window", methods=["POST"])
@mutating
def open_transfer_window():
    uid = session["user_id"]
    st = load_state(uid)
    _process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    if not st["players"]:
        return jsonify({"error": "No players signed"}), 400

    now = time.time()
    if now < st.get("nextTransferWindow", 0):
        remaining = st["nextTransferWindow"] - now
        return jsonify({"error": "Transfer window not open", "remaining": remaining}), 400

    markets = _unlocked_markets(st)
    num_deals = 3 + st["upgrades"]["negotiationSkills"] // 2
    deals = []

    for player in st["players"]:
        if random.random() < 0.3:
            market = random.choice(markets)
            club = random.choice(CLUBS)
            fee = player["value"] * player["multiplier"] * market["multiplier"] * (5 + random.random() * 10)
            commission = fee * 0.1 * _commission_mult(st)
            deals.append({
                "id": secrets.token_hex(8),
                "playerId": player["id"],
                "playerName": player["name"],
                "playerTier": player["tier"],
                "club": club,
                "transferFee": fee,
                "commission": commission,
                "type": "transfer",
            })

    for player in st["players"]:
        if random.random() < 0.2:
            bonus = player["value"] * player["multiplier"] * (2 + random.random() * 3) * _commission_mult(st)
            deals.append({
                "id": secrets.token_hex(8),
                "playerId": player["id"],
                "playerName": player["name"],
                "playerTier": player["tier"],
                "club": "Current Club",
                "transferFee": bonus * 5,
                "commission": bonus,
                "type": "renewal",
            })

    deals = deals[:num_deals]
    st["availableDeals"] = deals
    save_state(uid, st)
    return jsonify({"deals": deals, "state": _sanitize(st)})


@app.route("/api/complete-deal", methods=["POST"])
@mutating
def complete_deal():
    uid = session["user_id"]
    st = load_state(uid)
    _process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    data = request.get_json(force=True)
    deal_id = data.get("dealId")
    if not deal_id:
        return jsonify({"error": "Missing dealId"}), 400

    deal = None
    for d in st.get("availableDeals", []):
        if d["id"] == deal_id:
            deal = d
            break
    if not deal:
        return jsonify({"error": "Deal not found"}), 404

    st["money"] += deal["commission"]
    st["totalCommission"] += deal["commission"]
    st["transfersCompleted"] += 1
    st["reputation"] += math.floor(deal["commission"] / 100)

    if deal["type"] == "transfer":
        for p in st["players"]:
            if p["id"] == deal["playerId"]:
                p["value"] = math.floor(p["value"] * (1.1 + random.random() * 0.1))
                break
        _add_notif(st, f"Transfer complete! {deal['playerName']} to {deal['club']}. Earned {_fmt(deal['commission'])}", "success")
    else:
        _add_notif(st, f"Contract renewed! Earned {_fmt(deal['commission'])}", "success")

    st["availableDeals"] = [d for d in st["availableDeals"] if d["id"] != deal_id]
    st["nextTransferWindow"] = time.time() + 20 + random.random() * 10
    save_state(uid, st)
    return jsonify({"ok": True, "state": _sanitize(st)})


@app.route("/api/toggle-autosign", methods=["POST"])
@mutating
def toggle_autosign():
    uid = session["user_id"]
    st = load_state(uid)
    st["autoSignEnabled"] = not st.get("autoSignEnabled", False)
    save_state(uid, st)
    return jsonify({"ok": True, "autoSignEnabled": st["autoSignEnabled"]})


@app.route("/api/fire-player", methods=["POST"])
@mutating
def fire_player():
    uid = session["user_id"]
    st = load_state(uid)
    _process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    data = request.get_json(force=True)
    pid = data.get("playerId")
    if not pid:
        return jsonify({"error": "Missing playerId"}), 400

    player = None
    for p in st["players"]:
        if p["id"] == pid:
            player = p
            break
    if not player:
        return jsonify({"error": "Player not found"}), 404

    if player.get("hasSponsorship"):
        st["activeSponsorships"] = max(0, st.get("activeSponsorships", 0) - 1)

    st["players"] = [p for p in st["players"] if p["id"] != pid]
    _add_notif(st, f"Fired {player['name']}", "error")
    save_state(uid, st)
    return jsonify({"ok": True, "state": _sanitize(st)})


@app.route("/api/reset", methods=["POST"])
@mutating
def reset_game():
    uid = session["user_id"]
    st = default_state()
    save_state(uid, st)
    return jsonify({"ok": True, "state": _sanitize(st)})


@app.route("/api/save", methods=["POST"])
@mutating
def save_game():
    uid = session["user_id"]
    st = load_state(uid)
    _process_tick(st, time.time() - st.get("lastTickTime", time.time()))
    save_state(uid, st)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
