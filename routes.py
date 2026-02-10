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
    PLAYER_TIERS, UPGRADE_TYPES, FORMATIONS, POSITIONS,
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
trade_bp = Blueprint("trade", __name__)


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

    # Rate limit only the admin add-money endpoint to prevent abuse
    if limiter:
        limiter.limit("5 per minute")(admin_add_money)


def _load(uid):
    from db import load_state
    return load_state(uid, _redis_client)


def _save(uid, st):
    from db import save_state
    # Strip one-shot popup before persisting so it isn't re-sent on every poll
    popup = st.pop("pendingEventPopup", None)
    save_state(uid, st, _redis_client)
    if popup is not None:
        st["pendingEventPopup"] = popup  # restore for current response


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
        # William always has admin access
        if not session.get("is_admin") and session.get("username") != "William":
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
    from db import get_db, save_state, save_user_to_redis, check_username_exists

    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or len(username) < 3 or len(username) > 20:
        return jsonify({"error": "Username must be 3-20 characters"}), 400
    if not username.isalnum():
        return jsonify({"error": "Username must be alphanumeric"}), 400
    if len(password) < 6 or len(password) > 128:
        return jsonify({"error": "Password must be 6-128 characters"}), 400

    # Check if username exists in Redis or SQLite
    if check_username_exists(_redis_client, username):
        return jsonify({"error": "Username already taken"}), 409

    pw_hash = generate_password_hash(password, method="scrypt")
    created_at = time.time()

    # Save to SQLite
    db = get_db()
    cur = db.execute(
        "INSERT INTO users (username, pw_hash, created_at) VALUES (?, ?, ?)",
        (username, pw_hash, created_at),
    )
    db.commit()
    user_id = cur.lastrowid

    # Save to Redis for persistence across deploys
    save_user_to_redis(_redis_client, user_id, username, pw_hash, False, created_at)

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
    from db import load_user_by_username

    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    # Load user from Redis first, fallback to SQLite
    user = load_user_by_username(_redis_client, username)
    if not user or not check_password_hash(user["pw_hash"], password):
        return jsonify({"error": "Invalid credentials"}), 401

    session.permanent = True
    session["user_id"] = user["id"]
    session["username"] = username
    # William always gets admin access
    is_admin = bool(user.get("is_admin", False)) or username == "William"
    session["is_admin"] = is_admin
    return jsonify({"ok": True, "username": username, "csrf_token": session["csrf_token"], "isAdmin": is_admin})


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
        is_admin = session.get("is_admin", False) or session.get("username") == "William"
        return jsonify({
            "username": session["username"],
            "csrf_token": session["csrf_token"],
            "isAdmin": is_admin,
        })
    return jsonify({"username": None})


# ---------------------------------------------------------------------------
# Game state
# ---------------------------------------------------------------------------
@game_bp.route("/api/state", methods=["GET"])
@_login_required
def get_state():
    from game_logic import calculate_offline_earnings
    uid = session["user_id"]
    st = _load(uid)
    now = time.time()
    elapsed = now - st.get("lastTickTime", now)

    # Check for offline earnings before processing tick
    offline = calculate_offline_earnings(st)
    if offline:
        st["money"] += offline["earned"]
        st["pendingOfflineEarnings"] = offline
        add_notif(st, f"Welcome back! Earned {fmt(offline['earned'])} while away ({offline['awayHours']}h)", "success")

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
        # Gain reputation from signing (increased from value/5 to value/2 for better progression)
        rep_gain = math.floor(player["value"] / 2 * rep_mult(st))
        st["reputation"] += rep_gain
        st["scoutedPlayers"] = []
        add_notif(st, f"Signed {player['name']}! +{rep_gain} reputation", "success")
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
    st["nextTransferWindow"] = time.time() + 600
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


@game_bp.route("/api/set-autoscout-settings", methods=["POST"])
@_mutating
def set_autoscout_settings():
    uid = session["user_id"]
    st = _load(uid)
    if st["upgrades"].get("autoSign", 0) < 1:
        return jsonify({"error": "Purchase Auto-Scout AI upgrade first"}), 400

    data = request.get_json(force=True)
    settings = st.setdefault("autoScoutSettings", {})

    if "targetPositions" in data:
        settings["targetPositions"] = data["targetPositions"]
    if "targetTiers" in data:
        settings["targetTiers"] = data["targetTiers"]
    if "minOverall" in data:
        settings["minOverall"] = max(0, min(99, int(data["minOverall"])))

    _save(uid, st)
    return jsonify({"ok": True, "settings": settings, "state": sanitize(st)})


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


@game_bp.route("/api/set-lineup", methods=["POST"])
@_mutating
def set_lineup():
    """Set the starting 11 lineup for matches."""
    uid = session["user_id"]
    st = _load(uid)
    data = request.get_json(force=True)
    lineup = data.get("lineup", {})  # Maps position -> player ID

    if not lineup:
        return jsonify({"error": "Lineup required"}), 400

    # Get formation positions
    formation = st.get("formation", "4-3-3")
    required_positions = FORMATIONS[formation]["positions"]

    # Validate lineup has all required positions
    if len(lineup) != len(required_positions):
        return jsonify({"error": f"Lineup must have {len(required_positions)} players"}), 400

    # Validate all positions are filled
    for pos in required_positions:
        if pos not in lineup:
            return jsonify({"error": f"Missing player for position {pos}"}), 400

    # Validate all player IDs exist
    player_ids = set(p["id"] for p in st["players"])
    for player_id in lineup.values():
        if player_id not in player_ids:
            return jsonify({"error": "Invalid player ID in lineup"}), 400

    st["startingLineup"] = lineup
    add_notif(st, "Starting lineup updated!", "success")
    _save(uid, st)
    return jsonify({"ok": True, "state": sanitize(st)})


@game_bp.route("/api/club/swap-player", methods=["POST"])
@_mutating
def swap_player():
    """Swap a player in a specific position slot."""
    uid = session["user_id"]
    st = _load(uid)
    data = request.get_json(force=True)
    position_idx = data.get("positionIdx")
    player_id = data.get("playerId")

    if position_idx is None:
        return jsonify({"error": "Missing positionIdx"}), 400

    # Get formation positions
    formation = st.get("formation", "4-3-3")
    positions = FORMATIONS[formation]["positions"]

    if position_idx < 0 or position_idx >= len(positions):
        return jsonify({"error": "Invalid position index"}), 400

    # Validate player exists
    player = None
    for p in st["players"]:
        if p["id"] == player_id:
            player = p
            break

    if not player:
        return jsonify({"error": "Player not found"}), 404

    # Validate player has the correct position
    required_position = positions[position_idx]
    if player.get("position") != required_position:
        return jsonify({"error": f"Player must be a {required_position}"}), 400

    # Update lineup
    lineup = st.get("startingLineup", {})
    lineup[str(position_idx)] = player_id
    st["startingLineup"] = lineup

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

    # Set next match cooldown (4 minutes)
    st["nextMatchTime"] = now + 240

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


@game_bp.route("/api/club/train-batch", methods=["POST"])
@_mutating
def train_batch():
    """Train multiple players at once (up to 3/11 of roster)."""
    uid = session["user_id"]
    st = _load(uid)
    process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    data = request.get_json(force=True)
    player_ids = data.get("playerIds", [])
    training_type = data.get("trainingType")

    if not player_ids or not training_type:
        return jsonify({"error": "Missing playerIds or trainingType"}), 400

    # Calculate max trainable players (3/11 of roster)
    max_trainable = max(1, int(len(st["players"]) * 3 / 11))
    if len(player_ids) > max_trainable:
        return jsonify({"error": f"Can only train {max_trainable} players at once"}), 400

    results = []
    errors = []

    for player_id in player_ids:
        result, error = apply_training(st, training_type, player_id)
        if error:
            errors.append(error)
        else:
            results.append(result)

    if results:
        names = [r["player"] for r in results]
        add_notif(st, f"Trained {len(results)} players: {', '.join(names[:3])}{'...' if len(names) > 3 else ''}", "success")

    _save(uid, st)
    return jsonify({"ok": True, "trained": len(results), "results": results, "errors": errors, "state": sanitize(st)})


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
# Friend match (play against another user's team)
# ---------------------------------------------------------------------------
@game_bp.route("/api/club/friend-match", methods=["POST"])
@_mutating
def friend_match():
    uid = session["user_id"]
    st = _load(uid)
    process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    if not st.get("clubActive"):
        return jsonify({"error": "Activate your club first"}), 400

    data = request.get_json(force=True)
    friend_username = (data.get("username") or "").strip()
    if not friend_username:
        return jsonify({"error": "Enter a friend's username"}), 400

    # Check match cooldown
    now = time.time()
    if now < st.get("nextMatchTime", 0):
        remaining = int(st["nextMatchTime"] - now)
        return jsonify({"error": f"Match cooldown: {remaining}s remaining"}), 400

    # Find the friend's user
    from db import load_user_by_username, load_state
    friend_user = load_user_by_username(_redis_client, friend_username)
    if not friend_user:
        return jsonify({"error": "Player not found"}), 404

    if friend_user["id"] == uid:
        return jsonify({"error": "You can't play against yourself"}), 400

    # Load friend's game state
    friend_st = load_state(friend_user["id"], _redis_client)
    if not friend_st.get("clubActive"):
        return jsonify({"error": f"{friend_username}'s club is not active"}), 400

    # Calculate friend's club overall
    from game_logic import calculate_club_overall, simulate_match, apply_match_result
    friend_ovr = calculate_club_overall(friend_st)
    club_ovr = calculate_club_overall(st)

    # Create a virtual opponent from friend's team
    friend_club_name = friend_st.get("clubName", f"{friend_username}'s Team")

    # Simulate match using friend's stats
    ovr_diff = club_ovr - friend_ovr
    win_chance = max(0.1, min(0.9, 0.5 + (ovr_diff * 0.02)))
    draw_chance = 0.2
    stadium_lvl = st.get("clubFacilities", {}).get("stadium", 0)
    win_chance = min(0.95, win_chance + stadium_lvl * 0.03)

    roll = random.random()
    if roll < win_chance:
        result = "win"
        our_goals = random.randint(1, 3) + max(0, ovr_diff // 20)
        their_goals = random.randint(0, max(0, our_goals - 1))
    elif roll < win_chance + draw_chance:
        result = "draw"
        goals = random.randint(0, 3)
        our_goals = goals
        their_goals = goals
    else:
        result = "loss"
        their_goals = random.randint(1, 3) + max(0, -ovr_diff // 20)
        our_goals = random.randint(0, max(0, their_goals - 1))

    # Friend matches give better rewards
    base_reward = max(5000, abs(friend_ovr - club_ovr) * 500 + 10000)
    base_rep = max(100, abs(friend_ovr - club_ovr) * 20 + 200)
    reward_mult = 1 + stadium_lvl * 0.25

    if result == "win":
        money_earned = int(base_reward * reward_mult * 1.5)
        rep_earned = int(base_rep * 1.5)
    elif result == "draw":
        money_earned = int(base_reward * 0.4 * reward_mult)
        rep_earned = int(base_rep * 0.4)
    else:
        money_earned = int(base_reward * 0.15 * reward_mult)
        rep_earned = int(base_rep * 0.15)

    match_result = {
        "result": result,
        "ourGoals": our_goals,
        "theirGoals": their_goals,
        "opponent": f"{friend_club_name} ({friend_username})",
        "opponentOvr": friend_ovr,
        "clubOvr": club_ovr,
        "moneyEarned": money_earned,
        "repEarned": rep_earned,
        "timestamp": now,
        "isFriendMatch": True,
    }

    apply_match_result(st, match_result)
    st["nextMatchTime"] = now + 240

    from game_logic import add_notif, fmt
    result_text = f"{'Won' if result == 'win' else 'Drew' if result == 'draw' else 'Lost'} {our_goals}-{their_goals} vs {friend_club_name}"
    add_notif(st, f"{result_text}! Earned {fmt(money_earned)}", "success" if result == "win" else "info")

    _save(uid, st)
    return jsonify({
        "ok": True,
        "match": match_result,
        "friendClub": friend_club_name,
        "friendOvr": friend_ovr,
        "state": sanitize(st),
    })


# ---------------------------------------------------------------------------
# League system
# ---------------------------------------------------------------------------
@game_bp.route("/api/club/start-league", methods=["POST"])
@_mutating
def start_league():
    uid = session["user_id"]
    st = _load(uid)
    process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    if not st.get("clubActive"):
        return jsonify({"error": "Activate your club first"}), 400

    if st.get("league") and not st["league"].get("completed"):
        return jsonify({"error": "You already have an active league season"}), 400

    from game_logic import calculate_club_overall
    club_ovr = calculate_club_overall(st)

    # Generate league teams based on club level
    league_teams = _generate_league_teams(st, club_ovr)
    season = st.get("leagueSeason", 0) + 1

    # Generate fixtures (round-robin)
    team_names = [st.get("clubName", "My Club")] + [t["name"] for t in league_teams]
    fixtures = _generate_fixtures(team_names)

    # Initialize standings
    standings = {}
    for name in team_names:
        standings[name] = {"played": 0, "wins": 0, "draws": 0, "losses": 0, "gf": 0, "ga": 0, "points": 0}

    league = {
        "season": season,
        "teams": league_teams,
        "fixtures": fixtures,
        "standings": standings,
        "currentMatchday": 0,
        "totalMatchdays": len(fixtures),
        "completed": False,
        "playerTeam": st.get("clubName", "My Club"),
    }

    st["league"] = league
    st["leagueSeason"] = season
    from game_logic import add_notif
    add_notif(st, f"Season {season} started! {len(fixtures)} matchdays await.", "success")

    _save(uid, st)
    return jsonify({"ok": True, "league": league, "state": sanitize(st)})


@game_bp.route("/api/club/play-league-match", methods=["POST"])
@_mutating
def play_league_match():
    uid = session["user_id"]
    st = _load(uid)
    process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    if not st.get("clubActive"):
        return jsonify({"error": "Activate your club first"}), 400

    league = st.get("league")
    if not league or league.get("completed"):
        return jsonify({"error": "No active league. Start a new season!"}), 400

    # Check match cooldown
    now = time.time()
    if now < st.get("nextMatchTime", 0):
        remaining = int(st["nextMatchTime"] - now)
        return jsonify({"error": f"Match cooldown: {remaining}s remaining"}), 400

    matchday = league["currentMatchday"]
    if matchday >= league["totalMatchdays"]:
        league["completed"] = True
        _save(uid, st)
        return jsonify({"error": "League season complete!"}), 400

    fixture = league["fixtures"][matchday]
    player_team = league["playerTeam"]

    from game_logic import calculate_club_overall, add_notif, fmt

    club_ovr = calculate_club_overall(st)
    all_results = []

    # Simulate all matches in this matchday
    for match in fixture:
        home, away = match["home"], match["away"]
        is_player_match = (home == player_team or away == player_team)

        if is_player_match:
            # Player's match - find opponent
            opponent_name = away if home == player_team else home
            opponent_team = None
            for t in league["teams"]:
                if t["name"] == opponent_name:
                    opponent_team = t
                    break

            if not opponent_team:
                continue

            opp_ovr = opponent_team["ovr"]
            ovr_diff = club_ovr - opp_ovr
            win_chance = max(0.1, min(0.9, 0.5 + (ovr_diff * 0.02)))
            # Home advantage
            if home == player_team:
                win_chance = min(0.95, win_chance + 0.05)
            stadium_lvl = st.get("clubFacilities", {}).get("stadium", 0)
            win_chance = min(0.95, win_chance + stadium_lvl * 0.03)

            roll = random.random()
            if roll < win_chance:
                result = "win"
                h_goals = random.randint(1, 4) + max(0, ovr_diff // 15)
                a_goals = random.randint(0, max(0, h_goals - 1))
            elif roll < win_chance + 0.2:
                result = "draw"
                g = random.randint(0, 3)
                h_goals, a_goals = g, g
            else:
                result = "loss"
                a_goals = random.randint(1, 3) + max(0, -ovr_diff // 15)
                h_goals = random.randint(0, max(0, a_goals - 1))

            if home != player_team:
                h_goals, a_goals = a_goals, h_goals
                result = "win" if result == "loss" else "loss" if result == "win" else "draw"

            # Rewards
            reward = opponent_team.get("reward", 5000)
            rep_reward = opponent_team.get("repReward", 100)
            reward_mult = 1 + stadium_lvl * 0.25
            if result == "win":
                money_earned = int(reward * reward_mult)
                rep_earned = rep_reward
            elif result == "draw":
                money_earned = int(reward * 0.3 * reward_mult)
                rep_earned = int(rep_reward * 0.3)
            else:
                money_earned = int(reward * 0.1 * reward_mult)
                rep_earned = int(rep_reward * 0.1)

            st["money"] += money_earned
            st["reputation"] += rep_earned

            # Update club stats
            stats = st.setdefault("clubStats", {"matchesPlayed": 0, "wins": 0, "draws": 0, "losses": 0, "goalsScored": 0, "goalsConceded": 0, "totalEarnings": 0})
            stats["matchesPlayed"] += 1
            our_goals = h_goals if home == player_team else a_goals
            their_goals = a_goals if home == player_team else h_goals
            stats["goalsScored"] += our_goals
            stats["goalsConceded"] += their_goals
            stats["totalEarnings"] += money_earned
            if result == "win":
                stats["wins"] += 1
            elif result == "draw":
                stats["draws"] += 1
            else:
                stats["losses"] += 1

            # Match history
            history_entry = {
                "result": result,
                "ourGoals": our_goals,
                "theirGoals": their_goals,
                "opponent": opponent_name,
                "moneyEarned": money_earned,
                "repEarned": rep_earned,
                "timestamp": now,
                "isLeague": True,
            }
            st.setdefault("matchHistory", []).append(history_entry)
            st["matchHistory"] = st["matchHistory"][-20:]

            player_result = {
                "home": home, "away": away, "homeGoals": h_goals, "awayGoals": a_goals,
                "isPlayerMatch": True, "result": result, "moneyEarned": money_earned,
                "repEarned": rep_earned,
            }
            all_results.append(player_result)
        else:
            # Simulate AI vs AI
            home_team = None
            away_team = None
            for t in league["teams"]:
                if t["name"] == home:
                    home_team = t
                if t["name"] == away:
                    away_team = t

            if home_team and away_team:
                diff = home_team["ovr"] - away_team["ovr"]
                wc = max(0.15, min(0.85, 0.5 + diff * 0.02 + 0.05))
                r = random.random()
                if r < wc:
                    h_goals = random.randint(1, 3)
                    a_goals = random.randint(0, max(0, h_goals - 1))
                elif r < wc + 0.25:
                    g = random.randint(0, 2)
                    h_goals, a_goals = g, g
                else:
                    a_goals = random.randint(1, 3)
                    h_goals = random.randint(0, max(0, a_goals - 1))
            else:
                h_goals, a_goals = 1, 1

            all_results.append({
                "home": home, "away": away, "homeGoals": h_goals, "awayGoals": a_goals,
                "isPlayerMatch": False,
            })

        # Update standings
        standings = league["standings"]
        if home in standings:
            standings[home]["played"] += 1
            standings[home]["gf"] += h_goals
            standings[home]["ga"] += a_goals
        if away in standings:
            standings[away]["played"] += 1
            standings[away]["gf"] += a_goals
            standings[away]["ga"] += h_goals

        if h_goals > a_goals:
            if home in standings:
                standings[home]["wins"] += 1
                standings[home]["points"] += 3
            if away in standings:
                standings[away]["losses"] += 1
        elif h_goals < a_goals:
            if away in standings:
                standings[away]["wins"] += 1
                standings[away]["points"] += 3
            if home in standings:
                standings[home]["losses"] += 1
        else:
            if home in standings:
                standings[home]["draws"] += 1
                standings[home]["points"] += 1
            if away in standings:
                standings[away]["draws"] += 1
                standings[away]["points"] += 1

    league["currentMatchday"] = matchday + 1
    st["nextMatchTime"] = now + 240

    # Check if season is complete
    season_rewards = None
    if league["currentMatchday"] >= league["totalMatchdays"]:
        league["completed"] = True
        # Calculate final position
        sorted_standings = sorted(league["standings"].items(), key=lambda x: (-x[1]["points"], -(x[1]["gf"] - x[1]["ga"]), -x[1]["gf"]))
        position = 1
        for i, (team_name, _) in enumerate(sorted_standings):
            if team_name == player_team:
                position = i + 1
                break

        # Season rewards based on position
        total_teams = len(sorted_standings)
        if position == 1:
            reward_money = 200000
            reward_rep = 5000
            title = "LEAGUE CHAMPION"
        elif position == 2:
            reward_money = 100000
            reward_rep = 2500
            title = "Runner-up"
        elif position == 3:
            reward_money = 50000
            reward_rep = 1000
            title = "Third Place"
        elif position <= total_teams // 2:
            reward_money = 20000
            reward_rep = 500
            title = "Upper Half"
        else:
            reward_money = 5000
            reward_rep = 100
            title = "Lower Half"

        st["money"] += reward_money
        st["reputation"] += reward_rep
        season_rewards = {"position": position, "title": title, "money": reward_money, "rep": reward_rep, "totalTeams": total_teams}
        add_notif(st, f"Season {league['season']} complete! Finished {position}/{total_teams} - {title}!", "success")

    _save(uid, st)
    return jsonify({
        "ok": True,
        "matchday": matchday + 1,
        "results": all_results,
        "standings": league["standings"],
        "completed": league["completed"],
        "seasonRewards": season_rewards,
        "state": sanitize(st),
    })


def _generate_league_teams(st, club_ovr):
    """Generate 7 AI teams for an 8-team league."""
    teams = []
    # Spread teams around the player's level
    offsets = [-15, -10, -5, 0, 5, 10, 15]
    random.shuffle(offsets)

    team_names = [
        "Red Lions FC", "Blue Eagles", "Golden Bears", "Silver Wolves",
        "Iron City United", "Thunder Hawks", "Phoenix Rising", "Storm Rangers",
        "Galaxy Stars", "Dynamo FC", "Atlas United", "Valor FC",
    ]
    random.shuffle(team_names)

    for i, offset in enumerate(offsets):
        ovr = max(30, min(95, club_ovr + offset + random.randint(-3, 3)))
        reward = max(2000, int((ovr / 60) * 15000))
        rep_reward = max(50, int((ovr / 60) * 300))
        teams.append({
            "name": team_names[i],
            "ovr": ovr,
            "reward": reward,
            "repReward": rep_reward,
        })

    return teams


def _generate_fixtures(team_names):
    """Generate round-robin fixtures for the league."""
    n = len(team_names)
    if n % 2 != 0:
        team_names = team_names + ["BYE"]
        n += 1

    fixtures = []
    teams = list(range(n))

    for round_num in range(n - 1):
        matchday = []
        for i in range(n // 2):
            home_idx = teams[i]
            away_idx = teams[n - 1 - i]
            if home_idx < len(team_names) and away_idx < len(team_names):
                if team_names[home_idx] != "BYE" and team_names[away_idx] != "BYE":
                    matchday.append({"home": team_names[home_idx], "away": team_names[away_idx]})
        if matchday:
            fixtures.append(matchday)
        # Rotate teams (keep first team fixed)
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]

    return fixtures


# ---------------------------------------------------------------------------
# Quick train - retrain last trained players
# ---------------------------------------------------------------------------
@game_bp.route("/api/club/quick-train", methods=["POST"])
@_mutating
def quick_train():
    """Quick train the last trained players with the same training type."""
    uid = session["user_id"]
    st = _load(uid)
    process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    last_trained = st.get("lastTrainedPlayers", [])
    last_type = st.get("lastTrainingType", "")

    if not last_trained or not last_type:
        return jsonify({"error": "No previous training session found. Train players first!"}), 400

    # Filter to only players still in roster and not on cooldown
    now = time.time()
    cooldowns = st.get("playerTrainingCooldowns", {})
    valid_ids = []
    for pid in last_trained:
        player_exists = any(p["id"] == pid for p in st["players"])
        on_cooldown = cooldowns.get(pid, 0) > now
        if player_exists and not on_cooldown:
            valid_ids.append(pid)

    if not valid_ids:
        # Clear stale training memory so UI falls back to batch training
        remaining = [pid for pid in last_trained if any(p["id"] == pid for p in st["players"])]
        if not remaining:
            st["lastTrainedPlayers"] = []
            st["lastTrainingType"] = ""
            _save(uid, st)
        return jsonify({"error": "All previously trained players are on cooldown or no longer in roster", "state": sanitize(st)}), 400

    results = []
    errors = []
    for pid in valid_ids:
        result, error = apply_training(st, last_type, pid)
        if error:
            errors.append(error)
        else:
            results.append(result)

    if results:
        names = [r["player"] for r in results]
        from game_logic import add_notif
        add_notif(st, f"Quick trained {len(results)} players: {', '.join(names[:3])}{'...' if len(names) > 3 else ''}", "success")

    _save(uid, st)
    return jsonify({"ok": True, "trained": len(results), "results": results, "errors": errors, "state": sanitize(st)})


# ---------------------------------------------------------------------------
# VIP bonus route (for William)
# ---------------------------------------------------------------------------
@game_bp.route("/api/vip-bonus", methods=["POST"])
@_mutating
def claim_vip_bonus():
    uid = session["user_id"]
    username = session.get("username", "")

    # Only William can claim this
    if username != "William":
        return jsonify({"error": "VIP access only"}), 403

    st = _load(uid)
    process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    now = time.time()
    last_claim = st.get("lastVipClaim", 0)
    hours_passed = (now - last_claim) / 3600

    if hours_passed < 1:
        minutes_left = int((1 - hours_passed) * 60)
        return jsonify({"error": f"Bonus available in {minutes_left} minutes"}), 400

    # Grant bonus
    st["money"] += 5000
    st["lastVipClaim"] = now
    add_notif(st, "VIP Bonus: +$5,000!", "success")

    _save(uid, st)
    return jsonify({"ok": True, "state": sanitize(st)})


# ---------------------------------------------------------------------------
# Player Trading routes
# ---------------------------------------------------------------------------
@trade_bp.route("/api/trade/list-player", methods=["POST"])
@_mutating
def trade_list_player():
    """List a player for sale on the trade market."""
    from db import get_db
    uid = session["user_id"]
    username = session.get("username", "")
    data = request.get_json(force=True)
    player_id = data.get("playerId")
    price = data.get("price", 0)

    if not player_id:
        return jsonify({"error": "Missing playerId"}), 400
    if not isinstance(price, (int, float)) or price < 100:
        return jsonify({"error": "Price must be at least $100"}), 400
    if price > 1e12:
        return jsonify({"error": "Price too high"}), 400

    st = _load(uid)
    process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    # Find and remove the player
    player = None
    for p in st["players"]:
        if p["id"] == player_id:
            player = p
            break
    if not player:
        return jsonify({"error": "Player not found"}), 404

    # Remove from roster
    if player.get("hasSponsorship"):
        st["activeSponsorships"] = max(0, st.get("activeSponsorships", 0) - 1)
    st["players"] = [p for p in st["players"] if p["id"] != player_id]

    # Remove from lineup if present
    lineup = st.get("startingLineup", {})
    st["startingLineup"] = {k: v for k, v in lineup.items() if v != player_id}

    # Insert into trade_listings table
    import json
    db = get_db()
    db.execute(
        "INSERT INTO trade_listings (seller_id, seller_name, player_json, price, listed_at, status) VALUES (?, ?, ?, ?, ?, ?)",
        (uid, username, json.dumps(player), price, time.time(), "active")
    )
    db.commit()

    add_notif(st, f"Listed {player['name']} for {fmt(price)}", "info")
    _save(uid, st)
    return jsonify({"ok": True, "state": sanitize(st)})


@trade_bp.route("/api/trade/listings", methods=["GET"])
@_login_required
def trade_listings():
    """Get all active trade listings."""
    from db import get_db
    db = get_db()
    import json

    rows = db.execute(
        "SELECT id, seller_id, seller_name, player_json, price, listed_at FROM trade_listings WHERE status='active' ORDER BY listed_at DESC"
    ).fetchall()

    listings = []
    for row in rows:
        player = json.loads(row["player_json"])
        listings.append({
            "listingId": row["id"],
            "sellerId": row["seller_id"],
            "sellerName": row["seller_name"],
            "price": row["price"],
            "listedAt": row["listed_at"],
            "player": {
                "id": player.get("id"),
                "name": player.get("name"),
                "tier": player.get("tier"),
                "position": player.get("position", "CM"),
                "age": player.get("age", 22),
                "nationality": player.get("nationality", "english"),
                "value": player.get("value", 1),
                "multiplier": player.get("multiplier", 1),
                "stats": player.get("stats", {}),
                "hasSponsorship": player.get("hasSponsorship", False),
                "sponsorshipValue": player.get("sponsorshipValue", 0),
                "preferredFoot": player.get("preferredFoot", "Right"),
            },
        })

    return jsonify({"listings": listings})


@trade_bp.route("/api/trade/buy", methods=["POST"])
@_mutating
def trade_buy():
    """Buy a player from the trade market."""
    from db import get_db
    import json
    uid = session["user_id"]
    data = request.get_json(force=True)
    listing_id = data.get("listingId")

    if not listing_id:
        return jsonify({"error": "Missing listingId"}), 400

    db = get_db()
    row = db.execute(
        "SELECT id, seller_id, seller_name, player_json, price FROM trade_listings WHERE id=? AND status='active'",
        (listing_id,)
    ).fetchone()

    if not row:
        return jsonify({"error": "Listing not found or already sold"}), 404

    if row["seller_id"] == uid:
        return jsonify({"error": "Cannot buy your own listing"}), 400

    price = row["price"]
    player_data = json.loads(row["player_json"])

    # Load buyer state
    st = _load(uid)
    process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    if st["money"] < price:
        return jsonify({"error": f"Not enough money. Need {fmt(price)}"}), 400

    if len(st["players"]) >= max_players(st):
        return jsonify({"error": "Roster full"}), 400

    # Deduct money from buyer
    st["money"] -= price

    # Give new ID to prevent conflicts
    player_data["id"] = secrets.token_hex(8)
    player_data["earnings"] = 0
    st["players"].append(player_data)

    add_notif(st, f"Bought {player_data['name']} for {fmt(price)} from {row['seller_name']}", "success")
    _save(uid, st)

    # Credit seller
    seller_st = _load(row["seller_id"])
    seller_st["money"] = seller_st.get("money", 0) + price
    add_notif(seller_st, f"{player_data['name']} sold for {fmt(price)}!", "success")
    _save(row["seller_id"], seller_st)

    # Mark listing as sold
    db.execute("UPDATE trade_listings SET status='sold' WHERE id=?", (listing_id,))
    db.commit()

    return jsonify({"ok": True, "state": sanitize(st)})


@trade_bp.route("/api/trade/cancel", methods=["POST"])
@_mutating
def trade_cancel():
    """Cancel your own trade listing and get the player back."""
    from db import get_db
    import json
    uid = session["user_id"]
    data = request.get_json(force=True)
    listing_id = data.get("listingId")

    if not listing_id:
        return jsonify({"error": "Missing listingId"}), 400

    db = get_db()
    row = db.execute(
        "SELECT id, seller_id, player_json FROM trade_listings WHERE id=? AND status='active'",
        (listing_id,)
    ).fetchone()

    if not row:
        return jsonify({"error": "Listing not found or already sold"}), 404
    if row["seller_id"] != uid:
        return jsonify({"error": "Not your listing"}), 403

    player_data = json.loads(row["player_json"])

    st = _load(uid)
    process_tick(st, time.time() - st.get("lastTickTime", time.time()))

    if len(st["players"]) >= max_players(st):
        return jsonify({"error": "Roster full, can't take player back"}), 400

    st["players"].append(player_data)
    add_notif(st, f"Cancelled listing for {player_data['name']}", "info")
    _save(uid, st)

    db.execute("UPDATE trade_listings SET status='cancelled' WHERE id=?", (listing_id,))
    db.commit()

    return jsonify({"ok": True, "state": sanitize(st)})


@trade_bp.route("/api/trade/my-listings", methods=["GET"])
@_login_required
def trade_my_listings():
    """Get the current user's active listings."""
    from db import get_db
    import json
    uid = session["user_id"]
    db = get_db()

    rows = db.execute(
        "SELECT id, player_json, price, listed_at FROM trade_listings WHERE seller_id=? AND status='active' ORDER BY listed_at DESC",
        (uid,)
    ).fetchall()

    listings = []
    for row in rows:
        player = json.loads(row["player_json"])
        listings.append({
            "listingId": row["id"],
            "price": row["price"],
            "listedAt": row["listed_at"],
            "player": {
                "name": player.get("name"),
                "tier": player.get("tier"),
                "position": player.get("position", "CM"),
                "stats": player.get("stats", {}),
            },
        })

    return jsonify({"listings": listings})


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
    from db import get_db, load_user_by_username, save_user_to_redis
    db = get_db()
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (session["user_id"],))
    db.commit()
    session["is_admin"] = True

    # Also update in Redis
    user = load_user_by_username(_redis_client, session["username"])
    if user:
        save_user_to_redis(_redis_client, user["id"], user["username"],
                           user["pw_hash"], True, user.get("created_at", time.time()))

    return jsonify({"ok": True})


@admin_bp.route("/api/admin/search-users", methods=["GET"])
@_admin_required
def admin_search_users():
    """Search users by username with live suggestions."""
    from db import get_db
    query = request.args.get("q", "").strip().lower()
    if len(query) < 1:
        return jsonify({"users": []})

    db = get_db()
    # Search for users matching the query (case insensitive)
    rows = db.execute(
        "SELECT id, username, is_admin, created_at FROM users WHERE LOWER(username) LIKE ? LIMIT 10",
        (f"%{query}%",)
    ).fetchall()

    users = []
    for row in rows:
        users.append({
            "id": row["id"],
            "username": row["username"],
            "isAdmin": bool(row["is_admin"]),
            "createdAt": row["created_at"],
        })

    return jsonify({"users": users})


@admin_bp.route("/api/admin/delete-user", methods=["POST"])
@_admin_required
def admin_delete_user():
    """Delete a user account."""
    from db import get_db
    _check_csrf()

    data = request.get_json(force=True)
    user_id = data.get("userId")

    if not user_id:
        return jsonify({"error": "Missing userId"}), 400

    # Prevent self-deletion
    if user_id == session["user_id"]:
        return jsonify({"error": "Cannot delete your own account"}), 400

    db = get_db()

    # Check if user exists
    user_row = db.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
    if not user_row:
        return jsonify({"error": "User not found"}), 404

    username = user_row["username"]

    # Delete from SQLite
    db.execute("DELETE FROM game_states WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()

    # Delete from Redis
    if _redis_client:
        try:
            _redis_client.delete(f"user:name:{username}")
            _redis_client.delete(f"user:id:{user_id}")
            _redis_client.delete(f"game:state:{user_id}")
            _redis_client.srem("users:all", username)
        except Exception:
            pass

    return jsonify({"ok": True, "deleted": username})


@admin_bp.route("/api/admin/leaderboard", methods=["GET"])
@_admin_required
def admin_leaderboard():
    """Get leaderboard data for all users."""
    from db import get_db, load_state
    db = get_db()
    rows = db.execute("SELECT id, username, is_admin, created_at FROM users ORDER BY id").fetchall()

    leaderboard = []
    for row in rows:
        st = load_state(row["id"], _redis_client)
        players = st.get("players", [])
        club_stats = st.get("clubStats", {})
        best_player = None
        if players:
            best_player = max(players, key=lambda p: p.get("stats", {}).get("overall", 0))
        leaderboard.append({
            "id": row["id"],
            "username": row["username"],
            "isAdmin": bool(row["is_admin"]),
            "money": st.get("money", 0),
            "reputation": st.get("reputation", 0),
            "playerCount": len(players),
            "transfersCompleted": st.get("transfersCompleted", 0),
            "totalCommission": st.get("totalCommission", 0),
            "clubName": st.get("clubName", ""),
            "clubActive": st.get("clubActive", False),
            "matchesPlayed": club_stats.get("matchesPlayed", 0),
            "wins": club_stats.get("wins", 0),
            "bestPlayerName": best_player["name"] if best_player else "",
            "bestPlayerOvr": best_player.get("stats", {}).get("overall", 0) if best_player else 0,
            "totalPlaytime": st.get("totalPlaytime", 0),
            "createdAt": row["created_at"],
        })

    return jsonify({"leaderboard": leaderboard})


@admin_bp.route("/api/admin/playtime", methods=["GET"])
@_admin_required
def admin_playtime():
    """Get playtime data for all users."""
    from db import get_db, load_state
    db = get_db()
    rows = db.execute("SELECT id, username, created_at FROM users ORDER BY id").fetchall()

    playtime_data = []
    for row in rows:
        st = load_state(row["id"], _redis_client)
        playtime_data.append({
            "id": row["id"],
            "username": row["username"],
            "totalPlaytime": st.get("totalPlaytime", 0),
            "lastActiveTime": st.get("lastActiveTime", 0),
            "sessionStart": st.get("sessionStart", 0),
            "createdAt": row["created_at"],
        })

    # Sort by playtime descending
    playtime_data.sort(key=lambda x: x["totalPlaytime"], reverse=True)
    return jsonify({"playtime": playtime_data})


@admin_bp.route("/api/admin/user-stats/<int:user_id>", methods=["GET"])
@_admin_required
def admin_user_stats(user_id):
    """Get detailed stats for a specific user."""
    from db import get_db, load_state

    db = get_db()
    user_row = db.execute(
        "SELECT id, username, is_admin, created_at FROM users WHERE id=?",
        (user_id,)
    ).fetchone()

    if not user_row:
        return jsonify({"error": "User not found"}), 404

    # Load game state
    st = load_state(user_id, _redis_client)

    return jsonify({
        "user": {
            "id": user_row["id"],
            "username": user_row["username"],
            "isAdmin": bool(user_row["is_admin"]),
            "createdAt": user_row["created_at"],
        },
        "stats": {
            "money": st.get("money", 0),
            "reputation": st.get("reputation", 0),
            "agents": st.get("agents", 1),
            "playerCount": len(st.get("players", [])),
            "transfersCompleted": st.get("transfersCompleted", 0),
            "totalCommission": st.get("totalCommission", 0),
            "clubName": st.get("clubName", ""),
            "clubActive": st.get("clubActive", False),
            "clubStats": st.get("clubStats", {}),
            "activeSponsorships": st.get("activeSponsorships", 0),
            "totalPlaytime": st.get("totalPlaytime", 0),
            "lastActiveTime": st.get("lastActiveTime", 0),
            "formation": st.get("formation", "4-3-3"),
            "clubFacilities": st.get("clubFacilities", {}),
            "upgrades": st.get("upgrades", {}),
            "earningsPerSecond": sum(
                p["value"] * p["multiplier"] for p in st.get("players", [])
            ),
        },
        "players": [{
            "id": p["id"],
            "name": p["name"],
            "tier": p["tier"],
            "position": p.get("position", "CM"),
            "age": p.get("age", 22),
            "nationality": p.get("nationality", "english"),
            "value": p["value"],
            "multiplier": p.get("multiplier", 1),
            "overall": p.get("stats", {}).get("overall", 0),
            "stats": p.get("stats", {}),
            "hasSponsorship": p.get("hasSponsorship", False),
            "sponsorshipValue": p.get("sponsorshipValue", 0),
            "preferredFoot": p.get("preferredFoot", "Right"),
            "earnings": p.get("earnings", 0),
        } for p in st.get("players", [])],
    })


@admin_bp.route("/api/admin/set-balance", methods=["POST"])
@_admin_required
def admin_set_balance():
    """Set a specific user's money balance."""
    data = request.get_json(force=True)
    target_user_id = data.get("userId")
    amount = data.get("amount", 0)

    if not target_user_id:
        return jsonify({"error": "Missing userId"}), 400
    if not isinstance(amount, (int, float)) or amount < 0:
        return jsonify({"error": "Invalid amount"}), 400

    st = _load(target_user_id)
    old_balance = st.get("money", 0)
    st["money"] = amount
    add_notif(st, f"Admin set balance to {fmt(amount)}", "info")
    _save(target_user_id, st)
    return jsonify({"ok": True, "oldBalance": old_balance, "newBalance": amount})


@admin_bp.route("/api/admin/set-user-reputation", methods=["POST"])
@_admin_required
def admin_set_user_reputation():
    """Set a specific user's reputation."""
    data = request.get_json(force=True)
    target_user_id = data.get("userId")
    rep = data.get("reputation", 0)

    if not target_user_id:
        return jsonify({"error": "Missing userId"}), 400
    if not isinstance(rep, (int, float)) or rep < 0:
        return jsonify({"error": "Invalid reputation"}), 400

    st = _load(target_user_id)
    st["reputation"] = rep
    add_notif(st, f"Admin set reputation to {int(rep)}", "info")
    _save(target_user_id, st)
    return jsonify({"ok": True})


@admin_bp.route("/api/admin/add-player", methods=["POST"])
@_admin_required
def admin_add_player():
    """Add a new player to a user's roster."""
    data = request.get_json(force=True)
    target_user_id = data.get("userId")
    tier_name = data.get("tier", "Prospect")

    if not target_user_id:
        return jsonify({"error": "Missing userId"}), 400

    tier = None
    for t in PLAYER_TIERS:
        if t["name"] == tier_name:
            tier = t
            break
    if not tier:
        return jsonify({"error": "Invalid tier"}), 400

    st = _load(target_user_id)
    player = _generate_player_obj(tier)
    st["players"].append(player)
    add_notif(st, f"Admin added {player['name']} ({tier_name})", "info")
    _save(target_user_id, st)
    return jsonify({"ok": True, "player": player})


@admin_bp.route("/api/admin/remove-player", methods=["POST"])
@_admin_required
def admin_remove_player():
    """Remove a player from a user's roster."""
    data = request.get_json(force=True)
    target_user_id = data.get("userId")
    player_id = data.get("playerId")

    if not target_user_id or not player_id:
        return jsonify({"error": "Missing userId or playerId"}), 400

    st = _load(target_user_id)
    player = None
    for p in st["players"]:
        if p["id"] == player_id:
            player = p
            break
    if not player:
        return jsonify({"error": "Player not found"}), 404

    player_name = player["name"]
    if player.get("hasSponsorship"):
        st["activeSponsorships"] = max(0, st.get("activeSponsorships", 0) - 1)
    st["players"] = [p for p in st["players"] if p["id"] != player_id]
    add_notif(st, f"Admin removed {player_name}", "info")
    _save(target_user_id, st)
    return jsonify({"ok": True, "removed": player_name})


@admin_bp.route("/api/admin/edit-player", methods=["POST"])
@_admin_required
def admin_edit_player():
    """Edit a player's stats, tier, value, age, position, etc."""
    data = request.get_json(force=True)
    target_user_id = data.get("userId")
    player_id = data.get("playerId")
    changes = data.get("changes", {})

    if not target_user_id or not player_id:
        return jsonify({"error": "Missing userId or playerId"}), 400

    st = _load(target_user_id)
    player = None
    for p in st["players"]:
        if p["id"] == player_id:
            player = p
            break
    if not player:
        return jsonify({"error": "Player not found"}), 404

    # Apply changes
    if "value" in changes:
        player["value"] = max(1, int(changes["value"]))
    if "age" in changes:
        player["age"] = max(16, min(45, int(changes["age"])))
    if "position" in changes and changes["position"] in POSITIONS:
        player["position"] = changes["position"]
    if "tier" in changes:
        for t in PLAYER_TIERS:
            if t["name"] == changes["tier"]:
                player["tier"] = t["name"]
                player["multiplier"] = t["multiplier"]
                player["color"] = t["color"]
                break
    if "stats" in changes:
        for stat_name, stat_val in changes["stats"].items():
            if stat_name in player.get("stats", {}) and stat_name != "overall":
                player["stats"][stat_name] = max(1, min(99, int(stat_val)))
        # Recalculate overall
        player["stats"]["overall"] = int(
            sum(v for k, v in player["stats"].items() if k != "overall") / 6
        )
    if "name" in changes:
        player["name"] = str(changes["name"])[:40]

    add_notif(st, f"Admin edited {player['name']}", "info")
    _save(target_user_id, st)
    return jsonify({"ok": True, "player": player})


@admin_bp.route("/api/admin/toggle-sponsorship", methods=["POST"])
@_admin_required
def admin_toggle_sponsorship():
    """Toggle sponsorship on/off for a player."""
    data = request.get_json(force=True)
    target_user_id = data.get("userId")
    player_id = data.get("playerId")

    if not target_user_id or not player_id:
        return jsonify({"error": "Missing userId or playerId"}), 400

    st = _load(target_user_id)
    player = None
    for p in st["players"]:
        if p["id"] == player_id:
            player = p
            break
    if not player:
        return jsonify({"error": "Player not found"}), 404

    if player.get("hasSponsorship"):
        player["hasSponsorship"] = False
        player["sponsorshipValue"] = 0
        st["activeSponsorships"] = max(0, st.get("activeSponsorships", 0) - 1)
        add_notif(st, f"Admin removed sponsorship from {player['name']}", "info")
    else:
        player["hasSponsorship"] = True
        player["sponsorshipValue"] = player["value"] * player["multiplier"] * 2
        st["activeSponsorships"] = st.get("activeSponsorships", 0) + 1
        add_notif(st, f"Admin granted sponsorship to {player['name']}", "info")

    _save(target_user_id, st)
    return jsonify({"ok": True, "hasSponsorship": player["hasSponsorship"], "sponsorshipValue": player["sponsorshipValue"]})


@admin_bp.route("/api/admin/toggle-user-admin", methods=["POST"])
@_admin_required
def admin_toggle_user_admin():
    """Toggle admin status for a user."""
    from db import get_db, load_user_by_username, save_user_to_redis
    data = request.get_json(force=True)
    target_user_id = data.get("userId")

    if not target_user_id:
        return jsonify({"error": "Missing userId"}), 400

    db = get_db()
    user_row = db.execute("SELECT id, username, is_admin FROM users WHERE id=?", (target_user_id,)).fetchone()
    if not user_row:
        return jsonify({"error": "User not found"}), 404

    new_admin = 0 if user_row["is_admin"] else 1
    db.execute("UPDATE users SET is_admin=? WHERE id=?", (new_admin, target_user_id))
    db.commit()

    # Update Redis
    user = load_user_by_username(_redis_client, user_row["username"])
    if user:
        save_user_to_redis(_redis_client, user["id"], user["username"],
                           user["pw_hash"], bool(new_admin), user.get("created_at", time.time()))

    return jsonify({"ok": True, "isAdmin": bool(new_admin), "username": user_row["username"]})
