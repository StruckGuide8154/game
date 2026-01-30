"""
Server-side game logic: state management, tick processing, calculations.
"""
import math
import time
import random
import secrets

from game_data import (
    PLAYER_TIERS, UPGRADE_TYPES, MARKETS,
    ALL_NATIONALITIES, POSITIONS, FORMATIONS,
    generate_realistic_player_name, generate_player_stats, get_realistic_club,
)


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
            "autoSign": 0,
        },
        "transfersCompleted": 0,
        "totalCommission": 0,
        "activeSponsorships": 0,
        "nextTransferWindow": now + 300,
        "autoSignEnabled": False,
        "autoPayEnabled": False,
        "lastTickTime": now,
        "lastExpenseTime": now,
        "lastGrowthTime": now,
        "notifications": [],
        "expenseLog": [],
        "pendingExpenses": [],
        "scoutedPlayers": [],
        "availableDeals": [],
        "formation": "4-3-3",
        "clubName": "",
        "clubActive": False,
    }


def upgrade_cost(key, level):
    base = UPGRADE_TYPES[key]["baseCost"]
    return math.floor(base * (1.5 ** level))


def max_agents(st):
    return 1 + st["upgrades"]["officeSpace"]


def max_players(st):
    return 3 + st["upgrades"]["officeSpace"] * 2


def commission_mult(st):
    return (1 + st["upgrades"]["negotiationSkills"] * 0.2) * (1 + st["upgrades"]["legalTeam"] * 0.1)


def rep_mult(st):
    return 1 + st["upgrades"]["scoutingNetwork"] * 0.25


def sponsor_chance(st):
    return min(0.5, st["upgrades"]["mediaConnections"] * 0.1)


def available_tiers(st):
    marketing = st["upgrades"]["marketingTeam"]
    eligible = [t for t in PLAYER_TIERS if t["minRep"] <= st["reputation"]]
    return eligible[: 2 + marketing]


def unlocked_markets(st):
    return [m for m in MARKETS if m["minRep"] <= st["reputation"]]


def earnings_per_second(st):
    total = 0.0
    for p in st["players"]:
        total += p["value"] * p["multiplier"] * commission_mult(st) * st["agents"]
        if p.get("hasSponsorship"):
            total += p.get("sponsorshipValue", 0)
    return total


def calculate_expenses(st):
    """Calculate itemized expenses for transparency."""
    items = []
    office_lvl = st["upgrades"]["officeSpace"]
    if office_lvl > 0:
        items.append({"name": "Office Rent", "amount": office_lvl * 50, "detail": f"Level {office_lvl}"})
    if st["agents"] > 0:
        items.append({"name": "Agent Salaries", "amount": st["agents"] * 200, "detail": f"{st['agents']} agent(s)"})
    num_players = len(st["players"])
    if num_players > 0:
        items.append({"name": "Player Management", "amount": num_players * 10, "detail": f"{num_players} player(s)"})
    legal_lvl = st["upgrades"]["legalTeam"]
    if legal_lvl > 0:
        items.append({"name": "Legal Retainer", "amount": legal_lvl * 100, "detail": f"Level {legal_lvl}"})
    mkt_lvl = st["upgrades"]["marketingTeam"]
    if mkt_lvl > 0:
        items.append({"name": "Marketing Spend", "amount": mkt_lvl * 75, "detail": f"Level {mkt_lvl}"})
    media_lvl = st["upgrades"]["mediaConnections"]
    if media_lvl > 0:
        items.append({"name": "Media Fees", "amount": media_lvl * 150, "detail": f"Level {media_lvl}"})

    total = sum(i["amount"] for i in items)
    return items, total


def add_notif(st, message, ntype="success"):
    st["notifications"].append({
        "id": secrets.token_hex(4),
        "message": message,
        "type": ntype,
        "timestamp": time.time(),
    })
    cutoff = time.time() - 8
    st["notifications"] = [n for n in st["notifications"] if n["timestamp"] > cutoff][-5:]


def fmt(amount):
    if amount >= 1e9:
        return f"${amount/1e9:.2f}B"
    if amount >= 1e6:
        return f"${amount/1e6:.2f}M"
    if amount >= 1e3:
        return f"${amount/1e3:.1f}K"
    return f"${int(amount)}"


def _generate_player_obj(tier, nationality=None):
    """Create a full player object with position, age, stats, foot."""
    mn, mx = tier["valueRange"]
    pv = random.randint(mn, mx)
    first, last, nat = generate_realistic_player_name(nationality)
    position = random.choice(POSITIONS)
    age = random.randint(16, 35)
    # Younger players tend to be lower tier, older players more experienced
    if tier["name"] in ("Prospect", "Rising Star"):
        age = random.randint(16, 22)
    elif tier["name"] in ("World Class", "Superstar"):
        age = random.randint(24, 33)
    foot = random.choice(["Right", "Right", "Right", "Left"])  # 75% right-footed
    stats = generate_player_stats(position, tier["name"])

    return {
        "id": secrets.token_hex(8),
        "name": f"{first} {last}",
        "nationality": nat,
        "tier": tier["name"],
        "value": pv,
        "multiplier": tier["multiplier"],
        "color": tier["color"],
        "earnings": 0,
        "hasSponsorship": False,
        "sponsorshipValue": 0,
        "position": position,
        "age": age,
        "preferredFoot": foot,
        "stats": stats,
    }


def process_tick(st, elapsed_seconds):
    if elapsed_seconds <= 0:
        return

    elapsed_seconds = min(elapsed_seconds, 3600)
    ticks = max(1, int(elapsed_seconds))

    for _ in range(ticks):
        total_earnings = 0.0
        for player in st["players"]:
            variation = 0.7 + random.random() * 0.6
            base = player["value"] * player["multiplier"] * commission_mult(st) * st["agents"]
            earning = base * variation
            total_earnings += earning
            player["earnings"] = player.get("earnings", 0) + earning

            if player.get("hasSponsorship"):
                sv = player.get("sponsorshipValue", 0) * (0.8 + random.random() * 0.4)
                total_earnings += sv
                player["earnings"] += sv

            # Sponsorship chance
            if not player.get("hasSponsorship"):
                if random.random() < sponsor_chance(st) / 100:
                    player["hasSponsorship"] = True
                    player["sponsorshipValue"] = player["value"] * player["multiplier"] * 2
                    st["activeSponsorships"] = st.get("activeSponsorships", 0) + 1

        st["money"] += total_earnings

    # Expenses
    now = time.time()
    grace_end = st.get("lastExpenseTime", now) + 300
    if now > grace_end:
        months_passed = int((now - grace_end) / 120)
        if months_passed > 0:
            items, total = calculate_expenses(st)
            if total > 0:
                bill = {
                    "id": secrets.token_hex(8),
                    "items": items,
                    "total": total * months_passed,
                    "periods": months_passed,
                    "timestamp": now,
                    "status": "pending",
                }
                if st.get("autoPayEnabled", False):
                    if st["money"] >= bill["total"]:
                        st["money"] -= bill["total"]
                        bill["status"] = "paid"
                        st.setdefault("expenseLog", []).append(bill)
                        add_notif(st, f"Auto-paid expenses: -{fmt(bill['total'])}", "info")
                    else:
                        st.setdefault("pendingExpenses", []).append(bill)
                        add_notif(st, f"Insufficient funds for auto-pay! Bill: {fmt(bill['total'])}", "error")
                else:
                    st.setdefault("pendingExpenses", []).append(bill)
                    add_notif(st, f"New bill: {fmt(bill['total'])} - pay in Payments", "warning")
            st["lastExpenseTime"] = now - 300

    # Player growth (every 300s) - more realistic
    if now - st.get("lastGrowthTime", now) > 300:
        for player in st["players"]:
            age = player.get("age", 25)
            # Younger players grow faster, older decline more
            if age <= 24:
                grow_chance, grow_range = 0.4, (0.05, 0.20)
                decline_chance, decline_range = 0.05, (0.02, 0.08)
            elif age <= 30:
                grow_chance, grow_range = 0.25, (0.03, 0.12)
                decline_chance, decline_range = 0.1, (0.03, 0.10)
            else:
                grow_chance, grow_range = 0.1, (0.01, 0.05)
                decline_chance, decline_range = 0.25, (0.05, 0.15)

            r = random.random()
            if r < grow_chance:
                growth = math.floor(player["value"] * (grow_range[0] + random.random() * (grow_range[1] - grow_range[0])))
                player["value"] += max(1, growth)
                # Stats can grow too
                if "stats" in player:
                    stat_key = random.choice(["pace", "shooting", "passing", "dribbling", "defending", "physical"])
                    player["stats"][stat_key] = min(99, player["stats"][stat_key] + random.randint(1, 3))
                    player["stats"]["overall"] = int(sum(v for k, v in player["stats"].items() if k != "overall") / 6)
            elif r < grow_chance + decline_chance:
                decline = math.floor(player["value"] * (decline_range[0] + random.random() * (decline_range[1] - decline_range[0])))
                player["value"] = max(1, player["value"] - decline)
                if "stats" in player:
                    stat_key = random.choice(["pace", "shooting", "passing", "dribbling", "defending", "physical"])
                    player["stats"][stat_key] = max(1, player["stats"][stat_key] - random.randint(1, 2))
                    player["stats"]["overall"] = int(sum(v for k, v in player["stats"].items() if k != "overall") / 6)
        st["lastGrowthTime"] = now

    # Auto-sign
    if st.get("autoSignEnabled") and st["upgrades"].get("autoSign", 0) > 0:
        if len(st["players"]) < max_players(st) and st["money"] > 10000:
            if random.random() < 0.05 * ticks:
                tiers = available_tiers(st)
                if tiers:
                    tier = tiers[0]
                    player = _generate_player_obj(tier)
                    st["players"].append(player)
                    st["reputation"] += math.floor(tier["baseValue"] / 5 * rep_mult(st))

    st["lastTickTime"] = now


def check_club_ready(st):
    """Check if all formation positions are filled."""
    formation_key = st.get("formation", "4-3-3")
    formation = FORMATIONS.get(formation_key)
    if not formation:
        return False
    needed = {}
    for pos in formation["positions"]:
        needed[pos] = needed.get(pos, 0) + 1
    have = {}
    for p in st["players"]:
        pos = p.get("position", "CM")
        have[pos] = have.get(pos, 0) + 1
    for pos, count in needed.items():
        if have.get(pos, 0) < count:
            return False
    return True


def migrate_state(st):
    """Ensure old save files have all new fields."""
    defaults = default_state()
    for key in defaults:
        if key not in st:
            st[key] = defaults[key]
    if "autoSign" not in st.get("upgrades", {}):
        st["upgrades"]["autoSign"] = 0
    for p in st.get("players", []):
        if "nationality" not in p:
            p["nationality"] = random.choice(ALL_NATIONALITIES)
        if "position" not in p:
            p["position"] = random.choice(POSITIONS)
        if "age" not in p:
            p["age"] = random.randint(18, 30)
        if "preferredFoot" not in p:
            p["preferredFoot"] = random.choice(["Right", "Right", "Right", "Left"])
        if "stats" not in p:
            p["stats"] = generate_player_stats(p["position"], p.get("tier", "Prospect"))


def tick_and_save(user_id, st, save_fn):
    now = time.time()
    elapsed = now - st.get("lastTickTime", now)
    process_tick(st, elapsed)
    save_fn(user_id, st)
    return st


def sanitize(st):
    """Prepare state for sending to client."""
    cutoff = time.time() - 8
    st["notifications"] = [n for n in st.get("notifications", []) if n["timestamp"] > cutoff][-5:]
    expense_items, expense_total = calculate_expenses(st)

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
        "autoPayEnabled": st.get("autoPayEnabled", False),
        "notifications": st.get("notifications", []),
        "scoutedPlayers": st.get("scoutedPlayers", []),
        "availableDeals": st.get("availableDeals", []),
        "pendingExpenses": st.get("pendingExpenses", []),
        "expenseLog": (st.get("expenseLog", []) or [])[-10:],
        "currentExpenses": expense_items,
        "currentExpenseTotal": expense_total,
        "formation": st.get("formation", "4-3-3"),
        "clubName": st.get("clubName", ""),
        "clubActive": st.get("clubActive", False),
        "clubReady": check_club_ready(st),
        # Derived
        "maxAgents": max_agents(st),
        "maxPlayers": max_players(st),
        "earningsPerSecond": earnings_per_second(st),
        "commissionMultiplier": commission_mult(st),
        "reputationMultiplier": rep_mult(st),
        "unlockedMarkets": unlocked_markets(st),
        "availableTiers": available_tiers(st),
        "upgradeCosts": {k: upgrade_cost(k, st["upgrades"].get(k, 0)) for k in UPGRADE_TYPES},
        "hireAgentCost": 10000 * (2 ** (st["agents"] - 1)),
        "upgradeInfo": UPGRADE_TYPES,
        "formations": {k: v["label"] for k, v in FORMATIONS.items()},
        "formationPositions": FORMATIONS.get(st.get("formation", "4-3-3"), FORMATIONS["4-3-3"])["positions"],
        "serverTime": time.time(),
    }
