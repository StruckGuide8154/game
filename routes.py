"""
All Flask routes: auth, game state, game actions, admin.
"""
import math
import time
import random
import secrets

from flask import (
    Blueprint, request, jsonify, session,
    render_template,
)

from game_data import (
    PLAYER_TIERS, UPGRADE_TYPES, FORMATIONS,
    ALL_NATIONALITIES, generate_realistic_player_name,
    generate_player_stats, get_realistic_club,
)
from game_logic import (
    default_state, upgrade_cost, max_agents, max_players,
    commission_mult, rep_mult, available_tiers, unlocked_markets,
    add_notif, fmt, process_tick, sanitize, _generate_player_obj,
    check_club_ready, check_tier_progression, get_available_opponents,
    simulate_match, apply_match_result, apply_training,
    get_club_facility_cost,
)
from game_data import OPPONENT_CLUBS, TRAINING_TYPES

pages_bp = Blueprint("pages", __name__)
auth_bp = Blueprint("auth", __name__)
game_bp = Blueprint("game", __name__)
admin_bp = Blueprint("admin", __name__)


# ---------------------------------------------------------------------------
# Helpers - these get set by main.py during init
# ---------------------------------------------------------------------------
_redis_client = None
_check_csrf = None
_limiter = None


def init_routes(redis_client, check_csrf_fn, limiter):
    global _redis_client, _check_csrf, _limiter
    _redis_client = redis_client
    _check_csrf = check_csrf_fn
    _limiter = limiter


def _load(uid):
    from db import load_state
    return load_state(uid, _redis_client)


def _save(uid, st):
    from db import save_state
    save_state(uid, st, _redis_client)


def _login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Not authenticated"}), 401
        return f(*args, **kwargs)
    return decorated


def _mutating(f):
    from functools import wraps
    @wraps(f)
    @_login_required
    def decorated(*args, **kwargs):
        _check_csrf()
        return f(*args, **kwargs)
    return decorated


def _admin_required(f):
    from functools import wraps
    @wraps(f)
    @_login_required
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            return jsonify({"error": "Admin access required"}), 403
        _check_csrf()
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@pages_bp.route("/")
def index():
    return render_template("index.html")


@pages_bp.route("/login")
def login_page():
    return render_template("auth/login.html")


@pages_bp.route("/signup")
def signup_page():
    return render_template("auth/signup.html")


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------
@auth_bp.route("/api/register", methods=["POST"])
def register():
    from werkzeug.security import generate_password_hash
    from db import get_db, save_state

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
    session["is_admin"] = False

    st = default_state()
    save_state(user_id, st, _redis_client)

    return jsonify({"ok": True, "username": username, "csrf_token": session["csrf_token"]})


@auth_bp.route("/api/login", methods=["POST"])
def login():
    from werkzeug.security import check_password_hash
    from db import get_db

    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    db = get_db()
    user = db.execute("SELECT id, pw_hash, is_admin FROM users WHERE username=?", (username,)).fetchone()
    if not user or not check_password_hash(user["pw_hash"], password):
        return jsonify({"error": "Invalid credentials"}), 401

    session.permanent = True
    session["user_id"] = user["id"]
    session["username"] = username
    session["is_admin"] = bool(user["is_admin"])
    return jsonify({"ok": True, "username": username, "csrf_token": session["csrf_token"], "isAdmin": bool(user["is_admin"])})


@auth_bp.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@auth_bp.route("/api/csrf", methods=["GET"])
def get_csrf():
    return jsonify({"csrf_token": session["csrf_token"]})


@auth_bp.route("/api/me", methods=["GET"])
def me():
    if "user_id" in session:
        return jsonify({
            "username": session["username"],
            "csrf_token": session["csrf_token"],
            "isAdmin": session.get("is_admin", False),
        })
    return jsonify({"username": None})


# ---------------------------------------------------------------------------
# Game state
# ---------------------------------------------------------------------------
@game_bp.route("/api/state", methods=["GET"])
@_login_required
def get_state():
    uid = session["user_id"]
    st = _load(uid)
    now = time.time()
    elapsed = now - st.get("lastTickTime", now)
    process_tick(st, elapsed)
    _save(uid, st)
    return jsonify(sanitize(st))


# ---------------------------------------------------------------------------
# Game actions
# ---------------------------------------------------------------------------
@game_bp.route("/api/scout", methods=["POST"])
@_mutating
def scout_players():
    uid = session["user_id"]
    st = _load(uid)
    process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    tiers = available_tiers(st)
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

        first, last, nat = generate_realistic_player_name()
        position = random.choice(["GK", "CB", "LB", "RB", "CDM", "CM", "CAM", "LW", "RW", "ST"])
        age = random.randint(16, 35)
        if tier["name"] in ("Prospect", "Rising Star"):
            age = random.randint(16, 22)
        elif tier["name"] in ("World Class", "Superstar"):
            age = random.randint(24, 33)
        foot = random.choice(["Right", "Right", "Right", "Left"])
        stats = generate_player_stats(position, tier["name"])

        scouted.append({
            "id": secrets.token_hex(8),
            "name": f"{first} {last}",
            "nationality": nat,
            "tier": tier["name"],
            "value": pv,
            "multiplier": tier["multiplier"],
            "color": tier["color"],
            "repRequired": rep_req,
            "acceptanceChance": acc,
            "demanded": st["reputation"] < rep_req,
            "position": position,
            "age": age,
            "preferredFoot": foot,
            "stats": stats,
        })

    scouted.sort(key=lambda p: p["repRequired"])
    st["scoutedPlayers"] = scouted
    _save(uid, st)
    return jsonify({"players": scouted})


@game_bp.route("/api/sign", methods=["POST"])
@_mutating
def sign_player():
    uid = session["user_id"]
    st = _load(uid)
    process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    data = request.get_json(force=True)
    pid = data.get("playerId")
    if not pid:
        return jsonify({"error": "Missing playerId"}), 400

    player = None
    for p in st.get("scoutedPlayers", []):
        if p["id"] == pid:
            player = p
            break
    if not player:
        return jsonify({"error": "Player not found in scouted list"}), 404

    if len(st["players"]) >= max_players(st):
        return jsonify({"error": "Roster full"}), 400

    roll = random.random() * 100
    if roll <= player["acceptanceChance"]:
        new_player = {
            "id": secrets.token_hex(8),
            "name": player["name"],
            "nationality": player.get("nationality", random.choice(ALL_NATIONALITIES)),
            "tier": player["tier"],
            "value": player["value"],
            "multiplier": player["multiplier"],
            "color": player["color"],
            "earnings": 0,
            "hasSponsorship": False,
            "sponsorshipValue": 0,
            "position": player.get("position", "CM"),
            "age": player.get("age", 22),
            "preferredFoot": player.get("preferredFoot", "Right"),
            "stats": player.get("stats", generate_player_stats("CM", player["tier"])),
        }
        st["players"].append(new_player)
        st["reputation"] += math.floor(player["value"] / 5 * rep_mult(st))
        st["scoutedPlayers"] = []
        add_notif(st, f"Signed {player['name']}!", "success")
        _save(uid, st)
        return jsonify({"ok": True, "signed": True, "player": new_player, "state": sanitize(st)})
    else:
        st["scoutedPlayers"] = [p for p in st["scoutedPlayers"] if p["id"] != pid]
        # Penalty for failed signing (only if you have at least 1 player - not for beginners)
        if st["players"]:
            rep_penalty = max(5, int(player["value"] * player["multiplier"] * 0.5))
            st["reputation"] = max(0, st["reputation"] - rep_penalty)
            add_notif(st, f"{player['name']} declined! -{rep_penalty} reputation", "error")
        else:
            add_notif(st, f"{player['name']} declined", "error")
        _save(uid, st)
        return jsonify({"ok": True, "signed": False, "state": sanitize(st)})


@game_bp.route("/api/upgrade", methods=["POST"])
@_mutating
def purchase_upgrade():
    uid = session["user_id"]
    st = _load(uid)
    process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    data = request.get_json(force=True)
    key = data.get("upgrade")
    if key not in UPGRADE_TYPES:
        return jsonify({"error": "Invalid upgrade"}), 400

    cost = upgrade_cost(key, st["upgrades"].get(key, 0))
    if st["money"] < cost:
        return jsonify({"error": "Not enough money"}), 400

    st["money"] -= cost
    st["upgrades"][key] = st["upgrades"].get(key, 0) + 1
    add_notif(st, f"Upgraded {UPGRADE_TYPES[key]['name']}", "success")
    _save(uid, st)
    return jsonify({"ok": True, "state": sanitize(st)})


@game_bp.route("/api/hire-agent", methods=["POST"])
@_mutating
def hire_agent():
    uid = session["user_id"]
    st = _load(uid)
    process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    cost = 10000 * (2 ** (st["agents"] - 1))
    if st["money"] < cost:
        return jsonify({"error": "Not enough money"}), 400
    if st["agents"] >= max_agents(st):
        return jsonify({"error": "Max agents reached"}), 400

    st["money"] -= cost
    st["agents"] += 1
    add_notif(st, "Hired new agent!", "success")
    _save(uid, st)
    return jsonify({"ok": True, "state": sanitize(st)})


@game_bp.route("/api/transfer-window", methods=["POST"])
@_mutating
def open_transfer_window():
    uid = session["user_id"]
    st = _load(uid)
    process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    if not st["players"]:
        return jsonify({"error": "No players signed"}), 400

    now = time.time()
    if now < st.get("nextTransferWindow", 0):
        remaining = st["nextTransferWindow"] - now
        return jsonify({"error": "Transfer window not open", "remaining": remaining}), 400

    markets = unlocked_markets(st)
    max_market_tier = max(m["leagueTier"] for m in markets) if markets else 0
    num_deals = 3 + st["upgrades"]["negotiationSkills"] // 2
    deals = []

    for player in st["players"]:
        if random.random() < 0.3:
            market = random.choice(markets)
            club, league = get_realistic_club(max_market_tier, player.get("nationality"))
            fee = player["value"] * player["multiplier"] * market["multiplier"] * (5 + random.random() * 10)
            commission = fee * 0.1 * commission_mult(st)
            deals.append({
                "id": secrets.token_hex(8),
                "playerId": player["id"],
                "playerName": player["name"],
                "playerTier": player["tier"],
                "playerNationality": player.get("nationality", "unknown"),
                "club": club,
                "league": league,
                "transferFee": fee,
                "commission": commission,
                "type": "transfer",
            })

    for player in st["players"]:
        if random.random() < 0.2:
            club, league = get_realistic_club(max_market_tier, player.get("nationality"))
            bonus = player["value"] * player["multiplier"] * (2 + random.random() * 3) * commission_mult(st)
            deals.append({
                "id": secrets.token_hex(8),
                "playerId": player["id"],
                "playerName": player["name"],
                "playerTier": player["tier"],
                "playerNationality": player.get("nationality", "unknown"),
                "club": club,
                "league": league,
                "transferFee": bonus * 5,
                "commission": bonus,
                "type": "renewal",
            })

    deals = deals[:num_deals]
    st["availableDeals"] = deals
    _save(uid, st)
    return jsonify({"deals": deals, "state": sanitize(st)})


@game_bp.route("/api/complete-deal", methods=["POST"])
@_mutating
def complete_deal():
    uid = session["user_id"]
    st = _load(uid)
    process_tick(st, time.time() - st.get("lastTickTime", time.time()))

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
                # Check for tier promotion after value increase
                if check_tier_progression(p):
                    add_notif(st, f"{p['name']} promoted to {p['tier']}!", "success")
                break
        add_notif(st, f"Transfer! {deal['playerName']} to {deal['club']}. Earned {fmt(deal['commission'])}", "success")
    else:
        add_notif(st, f"Contract renewed at {deal['club']}! Earned {fmt(deal['commission'])}", "success")

    st["availableDeals"] = [d for d in st["availableDeals"] if d["id"] != deal_id]
    st["nextTransferWindow"] = time.time() + 300
    _save(uid, st)
    return jsonify({"ok": True, "state": sanitize(st)})


@game_bp.route("/api/toggle-autosign", methods=["POST"])
@_mutating
def toggle_autosign():
    uid = session["user_id"]
    st = _load(uid)
    if st["upgrades"].get("autoSign", 0) < 1:
        return jsonify({"error": "Purchase Auto-Scout AI upgrade first"}), 400
    st["autoSignEnabled"] = not st.get("autoSignEnabled", False)
    _save(uid, st)
    return jsonify({"ok": True, "autoSignEnabled": st["autoSignEnabled"], "state": sanitize(st)})


@game_bp.route("/api/toggle-autopay", methods=["POST"])
@_mutating
def toggle_autopay():
    uid = session["user_id"]
    st = _load(uid)
    st["autoPayEnabled"] = not st.get("autoPayEnabled", False)
    _save(uid, st)
    return jsonify({"ok": True, "autoPayEnabled": st["autoPayEnabled"], "state": sanitize(st)})


@game_bp.route("/api/pay-expense", methods=["POST"])
@_mutating
def pay_expense():
    uid = session["user_id"]
    st = _load(uid)
    process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    data = request.get_json(force=True)
    expense_id = data.get("expenseId")

    if expense_id == "all":
        total = sum(e["total"] for e in st.get("pendingExpenses", []))
        if st["money"] < total:
            return jsonify({"error": "Not enough money"}), 400
        st["money"] -= total
        for e in st.get("pendingExpenses", []):
            e["status"] = "paid"
            st.setdefault("expenseLog", []).append(e)
        st["pendingExpenses"] = []
        add_notif(st, f"Paid all expenses: -{fmt(total)}", "success")
    else:
        expense = None
        for e in st.get("pendingExpenses", []):
            if e["id"] == expense_id:
                expense = e
                break
        if not expense:
            return jsonify({"error": "Expense not found"}), 404
        if st["money"] < expense["total"]:
            return jsonify({"error": "Not enough money"}), 400
        st["money"] -= expense["total"]
        expense["status"] = "paid"
        st.setdefault("expenseLog", []).append(expense)
        st["pendingExpenses"] = [e for e in st["pendingExpenses"] if e["id"] != expense_id]
        add_notif(st, f"Paid expense: -{fmt(expense['total'])}", "success")

    _save(uid, st)
    return jsonify({"ok": True, "state": sanitize(st)})


@game_bp.route("/api/fire-player", methods=["POST"])
@_mutating
def fire_player():
    uid = session["user_id"]
    st = _load(uid)
    process_tick(st, time.time() - st.get("lastTickTime", time.time()))

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
    add_notif(st, f"Fired {player['name']}", "error")
    _save(uid, st)
    return jsonify({"ok": True, "state": sanitize(st)})


@game_bp.route("/api/reset", methods=["POST"])
@_mutating
def reset_game():
    uid = session["user_id"]
    st = default_state()
    _save(uid, st)
    return jsonify({"ok": True, "state": sanitize(st)})


@game_bp.route("/api/save", methods=["POST"])
@_mutating
def save_game():
    uid = session["user_id"]
    st = _load(uid)
    process_tick(st, time.time() - st.get("lastTickTime", time.time()))
    _save(uid, st)
    return jsonify({"ok": True})


@game_bp.route("/api/set-formation", methods=["POST"])
@_mutating
def set_formation():
    uid = session["user_id"]
    st = _load(uid)
    data = request.get_json(force=True)
    formation = data.get("formation")
    if formation not in FORMATIONS:
        return jsonify({"error": "Invalid formation"}), 400
    st["formation"] = formation
    _save(uid, st)
    return jsonify({"ok": True, "state": sanitize(st)})


@game_bp.route("/api/set-club-name", methods=["POST"])
@_mutating
def set_club_name():
    uid = session["user_id"]
    st = _load(uid)
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()[:30]
    if not name:
        return jsonify({"error": "Name required"}), 400
    st["clubName"] = name
    _save(uid, st)
    return jsonify({"ok": True, "state": sanitize(st)})


@game_bp.route("/api/activate-club", methods=["POST"])
@_mutating
def activate_club():
    uid = session["user_id"]
    st = _load(uid)
    if not check_club_ready(st):
        return jsonify({"error": "Fill all formation positions first"}), 400
    if not st.get("clubName"):
        return jsonify({"error": "Set a club name first"}), 400
    st["clubActive"] = True
    add_notif(st, f"{st['clubName']} is now active!", "success")
    _save(uid, st)
    return jsonify({"ok": True, "state": sanitize(st)})


# ---------------------------------------------------------------------------
# Club action routes
# ---------------------------------------------------------------------------
@game_bp.route("/api/club/play-match", methods=["POST"])
@_mutating
def play_match():
    uid = session["user_id"]
    st = _load(uid)
    process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    if not st.get("clubActive"):
        return jsonify({"error": "Activate your club first"}), 400

    data = request.get_json(force=True)
    opponent_name = data.get("opponent")
    if not opponent_name:
        return jsonify({"error": "Select an opponent"}), 400

    # Check match cooldown (2 minutes between matches)
    now = time.time()
    if now < st.get("nextMatchTime", 0):
        remaining = int(st["nextMatchTime"] - now)
        return jsonify({"error": f"Match cooldown: {remaining}s remaining"}), 400

    # Find opponent
    opponent = None
    for opp in OPPONENT_CLUBS:
        if opp["name"] == opponent_name:
            opponent = opp
            break
    if not opponent:
        return jsonify({"error": "Invalid opponent"}), 400

    # Simulate and apply match
    result = simulate_match(st, opponent)
    apply_match_result(st, result)

    # Set next match cooldown (2 minutes)
    st["nextMatchTime"] = now + 120

    result_text = f"{'Won' if result['result'] == 'win' else 'Drew' if result['result'] == 'draw' else 'Lost'} {result['ourGoals']}-{result['theirGoals']} vs {opponent_name}"
    add_notif(st, f"{result_text}! Earned {fmt(result['moneyEarned'])}", "success" if result["result"] == "win" else "info")

    _save(uid, st)
    return jsonify({"ok": True, "match": result, "state": sanitize(st)})


@game_bp.route("/api/club/train", methods=["POST"])
@_mutating
def train_player():
    uid = session["user_id"]
    st = _load(uid)
    process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    data = request.get_json(force=True)
    player_id = data.get("playerId")
    training_type = data.get("trainingType")

    if not player_id or not training_type:
        return jsonify({"error": "Missing playerId or trainingType"}), 400

    result, error = apply_training(st, training_type, player_id)
    if error:
        return jsonify({"error": error}), 400

    add_notif(st, f"{result['player']}: {', '.join(result['boosts'])}", "success")
    _save(uid, st)
    return jsonify({"ok": True, "training": result, "state": sanitize(st)})


@game_bp.route("/api/club/upgrade-facility", methods=["POST"])
@_mutating
def upgrade_facility():
    uid = session["user_id"]
    st = _load(uid)
    process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    data = request.get_json(force=True)
    facility = data.get("facility")

    if facility not in ["stadium", "training", "youth"]:
        return jsonify({"error": "Invalid facility"}), 400

    facilities = st.setdefault("clubFacilities", {"stadium": 0, "training": 0, "youth": 0})
    current_level = facilities.get(facility, 0)

    if current_level >= 5:
        return jsonify({"error": "Facility already at max level"}), 400

    cost = get_club_facility_cost(facility, current_level)
    if st["money"] < cost:
        return jsonify({"error": "Not enough money"}), 400

    st["money"] -= cost
    facilities[facility] = current_level + 1

    facility_names = {"stadium": "Stadium", "training": "Training Ground", "youth": "Youth Academy"}
    add_notif(st, f"Upgraded {facility_names[facility]} to Level {current_level + 1}!", "success")

    _save(uid, st)
    return jsonify({"ok": True, "state": sanitize(st)})


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------
@admin_bp.route("/api/admin/set-tier", methods=["POST"])
@_admin_required
def admin_set_tier():
    uid = session["user_id"]
    st = _load(uid)
    data = request.get_json(force=True)
    player_id = data.get("playerId")
    tier_name = data.get("tier")

    tier = None
    for t in PLAYER_TIERS:
        if t["name"] == tier_name:
            tier = t
            break
    if not tier:
        return jsonify({"error": "Invalid tier"}), 400

    player = None
    for p in st["players"]:
        if p["id"] == player_id:
            player = p
            break
    if not player:
        return jsonify({"error": "Player not found"}), 404

    mn, mx = tier["valueRange"]
    player["tier"] = tier["name"]
    player["multiplier"] = tier["multiplier"]
    player["color"] = tier["color"]
    player["value"] = random.randint(mn, mx)
    player["stats"] = generate_player_stats(player.get("position", "CM"), tier["name"])
    add_notif(st, f"Admin: {player['name']} set to {tier['name']}", "info")
    _save(uid, st)
    return jsonify({"ok": True, "state": sanitize(st)})


@admin_bp.route("/api/admin/add-money", methods=["POST"])
@_admin_required
def admin_add_money():
    uid = session["user_id"]
    st = _load(uid)
    data = request.get_json(force=True)
    amount = data.get("amount", 0)
    if not isinstance(amount, (int, float)) or amount <= 0:
        return jsonify({"error": "Invalid amount"}), 400
    st["money"] += amount
    add_notif(st, f"Admin: Added {fmt(amount)}", "info")
    _save(uid, st)
    return jsonify({"ok": True, "state": sanitize(st)})


@admin_bp.route("/api/admin/set-reputation", methods=["POST"])
@_admin_required
def admin_set_reputation():
    uid = session["user_id"]
    st = _load(uid)
    data = request.get_json(force=True)
    rep = data.get("reputation", 0)
    if not isinstance(rep, (int, float)) or rep < 0:
        return jsonify({"error": "Invalid reputation"}), 400
    st["reputation"] = rep
    add_notif(st, f"Admin: Reputation set to {int(rep)}", "info")
    _save(uid, st)
    return jsonify({"ok": True, "state": sanitize(st)})


@admin_bp.route("/api/admin/make-admin", methods=["POST"])
@_admin_required
def admin_make_admin():
    """Make the current user an admin (for initial setup)."""
    from db import get_db
    db = get_db()
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (session["user_id"],))
    db.commit()
    session["is_admin"] = True
    return jsonify({"ok": True})
