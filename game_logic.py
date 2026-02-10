"""
Server-side game logic: state management, tick processing, calculations.
"""
import math
import time
import random
import secrets

from game_data import (
    PLAYER_TIERS, UPGRADE_TYPES, MARKETS,
    ALL_NATIONALITIES, POSITIONS, FORMATIONS, OPPONENT_CLUBS, TRAINING_TYPES,
    RANDOM_EVENTS,
    generate_realistic_player_name, generate_player_stats, get_realistic_club,
)


def get_tier_for_value(value):
    """Find the appropriate tier based on player value."""
    # Go through tiers from highest to lowest to find the right one
    for tier in reversed(PLAYER_TIERS):
        if value >= tier["valueRange"][0]:
            return tier
    return PLAYER_TIERS[0]


def check_tier_progression(player):
    """Check and apply tier progression based on player value. Returns True if promoted."""
    current_tier_name = player.get("tier", "Prospect")
    current_value = player.get("value", 1)

    # Find current tier index
    current_idx = 0
    for i, t in enumerate(PLAYER_TIERS):
        if t["name"] == current_tier_name:
            current_idx = i
            break

    # Check if value exceeds current tier's max
    current_tier = PLAYER_TIERS[current_idx]
    if current_value <= current_tier["valueRange"][1]:
        return False  # Still within tier range

    # Find the appropriate higher tier
    new_tier = get_tier_for_value(current_value)
    new_idx = PLAYER_TIERS.index(new_tier)

    # Only promote, never demote through this mechanism
    if new_idx <= current_idx:
        return False

    # Apply tier promotion
    player["tier"] = new_tier["name"]
    player["multiplier"] = new_tier["multiplier"]
    player["color"] = new_tier["color"]

    # Boost stats to match new tier (stats grow with tier)
    if "stats" in player:
        stat_lo, stat_hi = new_tier.get("statRange", [30, 55])
        for stat_name in ["pace", "shooting", "passing", "dribbling", "defending", "physical"]:
            if stat_name in player["stats"]:
                # Boost stat towards new tier range
                current_stat = player["stats"][stat_name]
                if current_stat < stat_lo:
                    player["stats"][stat_name] = min(99, stat_lo + random.randint(0, 5))
        player["stats"]["overall"] = int(sum(v for k, v in player["stats"].items() if k != "overall") / 6)

    return True


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
        "nextTransferWindow": now + 600,
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
        # Club system
        "clubStats": {
            "matchesPlayed": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "goalsScored": 0,
            "goalsConceded": 0,
            "totalEarnings": 0,
        },
        "matchHistory": [],
        "nextMatchTime": now,
        "lastTrainingTime": now,
        "clubFacilities": {
            "stadium": 0,      # Increases match rewards
            "training": 0,     # Reduces training cooldown
            "youth": 0,        # Chance for bonus young players
        },
        "startingLineup": {},  # Maps position -> player ID for starting 11
        "autoScoutSettings": {
            "targetPositions": [],  # Empty means all positions
            "targetTiers": [],      # Empty means all tiers
            "minOverall": 0,        # Minimum overall rating
        },
        "lastEventTime": now,
        "activeEventBoost": None,  # Temporary boost from events
        "lastTrainedPlayers": [],   # IDs of last trained players for quick train
        "lastTrainingType": "",     # Last training type used for quick train
        # League system
        "league": None,             # Current league data
        "leagueSeason": 0,          # Current season number
        # Playtime tracking
        "totalPlaytime": 0,         # Total seconds played
        "sessionStart": now,        # When the current session started
        "lastActiveTime": now,      # Last time user was active (for idle detection)
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
    # Rep no longer blocks tiers - only marketingTeam upgrade determines available tiers
    # Start with first 2 tiers, +1 tier per marketing level
    num_tiers = 2 + marketing
    return PLAYER_TIERS[:num_tiers]


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

    # Check for active event boost
    event_boost_mult = 1.0
    if st.get("activeEventBoost"):
        boost = st["activeEventBoost"]
        if time.time() < boost.get("endTime", 0):
            event_boost_mult = boost.get("multiplier", 1.0)
        else:
            st["activeEventBoost"] = None

    for _ in range(ticks):
        total_earnings = 0.0
        for player in st["players"]:
            variation = 0.7 + random.random() * 0.6
            base = player["value"] * player["multiplier"] * commission_mult(st) * st["agents"]
            earning = base * variation * event_boost_mult
            total_earnings += earning
            player["earnings"] = player.get("earnings", 0) + earning

            if player.get("hasSponsorship"):
                sv = player.get("sponsorshipValue", 0) * (0.8 + random.random() * 0.4) * event_boost_mult
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
    grace_end = st.get("lastExpenseTime", now) + 600
    if now > grace_end:
        months_passed = int((now - grace_end) / 300)
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
            st["lastExpenseTime"] = now - 600

    # Player growth (every 600s) - more realistic
    if now - st.get("lastGrowthTime", now) > 600:
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
                # Check for tier promotion after growth
                if check_tier_progression(player):
                    add_notif(st, f"{player['name']} promoted to {player['tier']}!", "success")
            elif r < grow_chance + decline_chance:
                decline = math.floor(player["value"] * (decline_range[0] + random.random() * (decline_range[1] - decline_range[0])))
                player["value"] = max(1, player["value"] - decline)
                if "stats" in player:
                    stat_key = random.choice(["pace", "shooting", "passing", "dribbling", "defending", "physical"])
                    player["stats"][stat_key] = max(1, player["stats"][stat_key] - random.randint(1, 2))
                    player["stats"]["overall"] = int(sum(v for k, v in player["stats"].items() if k != "overall") / 6)
        st["lastGrowthTime"] = now

    # Auto-sign (increased from 0.05 to 0.15 for faster AI)
    if st.get("autoSignEnabled") and st["upgrades"].get("autoSign", 0) > 0:
        if len(st["players"]) < max_players(st) and st["money"] > 10000:
            if random.random() < 0.15 * ticks:
                tiers = available_tiers(st)
                if tiers:
                    # Apply auto scout filters
                    settings = st.get("autoScoutSettings", {})
                    target_tiers = settings.get("targetTiers", [])
                    target_positions = settings.get("targetPositions", [])
                    min_overall = settings.get("minOverall", 0)

                    # Filter tiers
                    if target_tiers:
                        tiers = [t for t in tiers if t["name"] in target_tiers]

                    if not tiers:
                        tiers = available_tiers(st)  # Fallback to all if filter too strict

                    # Generate player
                    tier = random.choice(tiers)
                    player = _generate_player_obj(tier)

                    # Check position filter
                    passes_position_filter = not target_positions or player["position"] in target_positions
                    # Check overall filter
                    passes_overall_filter = player.get("stats", {}).get("overall", 0) >= min_overall

                    # Only add player if passes all filters
                    if passes_position_filter and passes_overall_filter:
                        st["players"].append(player)
                        st["reputation"] += math.floor(tier["baseValue"] / 5 * rep_mult(st))

    # Random events every 5-10 minutes (only during active play)
    last_event = st.get("lastEventTime", now)
    if elapsed_seconds > 60:
        # Player was away — reset timer so events don't fire immediately on return
        st["lastEventTime"] = now
    elif now - last_event > random.randint(300, 600):  # 5-10 minutes
        # Trigger random event
        weights = [e["weight"] for e in RANDOM_EVENTS]
        total_weight = sum(weights)
        r = random.random() * total_weight
        selected_event = None
        for i, event in enumerate(RANDOM_EVENTS):
            r -= weights[i]
            if r <= 0:
                selected_event = event
                break

        if selected_event:
            effect = selected_event["effect"]
            event_popup = {
                "name": selected_event["name"],
                "description": selected_event.get("description", ""),
                "icon": selected_event.get("icon", "🎉"),
                "color": selected_event.get("color", "emerald"),
                "timestamp": now,
                "rewards": [],
            }

            if effect == "money":
                amount = random.randint(selected_event["value"][0], selected_event["value"][1])
                st["money"] += amount
                event_popup["rewards"].append({"type": "money", "value": amount, "label": f"+{fmt(amount)}"})
                add_notif(st, f"🎉 {selected_event['name']}: +{fmt(amount)}", "success")
            elif effect == "reputation":
                amount = random.randint(selected_event["value"][0], selected_event["value"][1])
                st["reputation"] += amount
                event_popup["rewards"].append({"type": "reputation", "value": amount, "label": f"+{amount} reputation"})
                add_notif(st, f"🎉 {selected_event['name']}: +{amount} reputation", "success")
            elif effect == "player_value":
                if st["players"]:
                    player = random.choice(st["players"])
                    mult = random.uniform(selected_event["value"][0], selected_event["value"][1])
                    old_val = player["value"]
                    player["value"] = int(player["value"] * mult)
                    increase_pct = int((mult - 1) * 100)
                    event_popup["rewards"].append({"type": "player_value", "value": increase_pct, "label": f"{player['name']} +{increase_pct}% value!", "player": player["name"]})
                    add_notif(st, f"🎉 {selected_event['name']}: {player['name']} value increased!", "success")
            elif effect == "free_scout":
                tiers = available_tiers(st)
                if tiers:
                    player = _generate_player_obj(random.choice(tiers))
                    st.setdefault("scoutedPlayers", []).append(player)
                    event_popup["rewards"].append({"type": "free_scout", "value": 1, "label": "Free player scouted!"})
                    add_notif(st, f"🎉 {selected_event['name']}: New player available!", "success")
            elif effect == "temp_earnings_boost":
                mult = random.uniform(selected_event["value"][0], selected_event["value"][1])
                st["activeEventBoost"] = {"multiplier": mult, "endTime": now + 300}
                boost_pct = int((mult - 1) * 100)
                event_popup["rewards"].append({"type": "earnings_boost", "value": boost_pct, "label": f"+{boost_pct}% earnings for 5 min!"})
                add_notif(st, f"🎉 {selected_event['name']}: {boost_pct}% earnings boost for 5 minutes!", "success")
            elif effect == "free_training":
                st["freeTrainingAvailable"] = True
                event_popup["rewards"].append({"type": "free_training", "value": 1, "label": "Free training session!"})
                add_notif(st, f"🎉 {selected_event['name']}: Free training session available!", "success")
            elif effect == "multi":
                money_amt = random.randint(selected_event["value"]["money"][0], selected_event["value"]["money"][1])
                rep_amt = random.randint(selected_event["value"]["reputation"][0], selected_event["value"]["reputation"][1])
                st["money"] += money_amt
                st["reputation"] += rep_amt
                event_popup["rewards"].append({"type": "money", "value": money_amt, "label": f"+{fmt(money_amt)}"})
                event_popup["rewards"].append({"type": "reputation", "value": rep_amt, "label": f"+{rep_amt} reputation"})
                add_notif(st, f"🎉 {selected_event['name']}: +{fmt(money_amt)} & +{rep_amt} reputation!", "success")

            # Store event for full-screen popup display
            st["pendingEventPopup"] = event_popup
            st["lastEventTime"] = now

    # Apply temporary event boost to earnings
    boost_mult = 1.0
    if st.get("activeEventBoost"):
        boost = st["activeEventBoost"]
        if now < boost.get("endTime", 0):
            boost_mult = boost.get("multiplier", 1.0)
        else:
            st["activeEventBoost"] = None

    st["lastTickTime"] = now

    # Track playtime (only count active time, cap at 5 min per tick)
    active_elapsed = min(elapsed_seconds, 300)
    st["totalPlaytime"] = st.get("totalPlaytime", 0) + active_elapsed
    st["lastActiveTime"] = now


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


def calculate_club_overall(st):
    """Calculate the club's overall rating based on players in formation."""
    if not st["players"]:
        return 0

    # If starting lineup is set, use those players
    lineup = st.get("startingLineup", {})
    if lineup and len(lineup) >= 11:
        selected_players = []
        # lineup maps position_idx (as string) -> player_id
        for pos_idx_str, player_id in lineup.items():
            for p in st["players"]:
                if p["id"] == player_id:
                    selected_players.append(p)
                    break
        if len(selected_players) >= 11:
            total_ovr = sum(p.get("stats", {}).get("overall", 50) for p in selected_players[:11])
            return int(total_ovr / min(len(selected_players), 11))

    # Otherwise, auto-assign best players by position to formation
    formation_key = st.get("formation", "4-3-3")
    formation = FORMATIONS.get(formation_key)
    if not formation:
        # Fallback: just use best 11 players
        players_by_ovr = sorted(st["players"], key=lambda p: p.get("stats", {}).get("overall", 50), reverse=True)
        top_11 = players_by_ovr[:11]
        if not top_11:
            return 0
        total_ovr = sum(p.get("stats", {}).get("overall", 50) for p in top_11)
        return int(total_ovr / len(top_11))

    # Assign best available player for each position
    positions = formation["positions"]
    assigned = []
    used_ids = set()
    for pos in positions:
        # Find best available player for this position
        best_player = None
        best_ovr = 0
        for p in st["players"]:
            if p["id"] not in used_ids and p.get("position") == pos:
                p_ovr = p.get("stats", {}).get("overall", 0)
                if p_ovr > best_ovr:
                    best_player = p
                    best_ovr = p_ovr
        if best_player:
            assigned.append(best_player)
            used_ids.add(best_player["id"])

    if not assigned:
        return 0

    total_ovr = sum(p.get("stats", {}).get("overall", 50) for p in assigned)
    return int(total_ovr / len(assigned))


def get_available_opponents(st):
    """Get list of opponents the club can face based on their overall."""
    club_ovr = calculate_club_overall(st)
    opponents = []
    for opp in OPPONENT_CLUBS:
        # Can challenge clubs within reasonable range
        opp_min, opp_max = opp["ovrRange"]
        # Allow challenging if within 15 OVR above or any below
        if opp_min <= club_ovr + 15:
            difficulty = "easy" if club_ovr > opp_max else "medium" if club_ovr >= opp_min else "hard"
            opponents.append({
                **opp,
                "difficulty": difficulty,
                "avgOvr": (opp_min + opp_max) // 2,
            })
    return opponents


def simulate_match(st, opponent):
    """Simulate a match and return the result."""
    club_ovr = calculate_club_overall(st)
    opp_ovr = random.randint(opponent["ovrRange"][0], opponent["ovrRange"][1])

    # Calculate win probability based on OVR difference
    ovr_diff = club_ovr - opp_ovr
    # Base 50% win chance, +/- 2% per OVR difference, clamped
    win_chance = max(0.1, min(0.9, 0.5 + (ovr_diff * 0.02)))
    draw_chance = 0.2  # 20% draw chance

    # Stadium bonus: +5% win chance per level
    stadium_lvl = st.get("clubFacilities", {}).get("stadium", 0)
    win_chance = min(0.95, win_chance + stadium_lvl * 0.05)

    roll = random.random()
    if roll < win_chance:
        result = "win"
        # Goals based on OVR advantage
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

    # Calculate rewards
    base_reward = opponent["reward"]
    rep_reward = opponent["repReward"]

    # Stadium bonus: +25% rewards per level
    reward_mult = 1 + stadium_lvl * 0.25

    if result == "win":
        money_earned = int(base_reward * reward_mult)
        rep_earned = rep_reward
    elif result == "draw":
        money_earned = int(base_reward * 0.3 * reward_mult)
        rep_earned = int(rep_reward * 0.3)
    else:
        money_earned = int(base_reward * 0.1 * reward_mult)
        rep_earned = int(rep_reward * 0.1)

    return {
        "result": result,
        "ourGoals": our_goals,
        "theirGoals": their_goals,
        "opponent": opponent["name"],
        "opponentOvr": opp_ovr,
        "clubOvr": club_ovr,
        "moneyEarned": money_earned,
        "repEarned": rep_earned,
        "timestamp": time.time(),
    }


def apply_match_result(st, match_result):
    """Apply match result to club stats."""
    stats = st.setdefault("clubStats", {
        "matchesPlayed": 0, "wins": 0, "draws": 0, "losses": 0,
        "goalsScored": 0, "goalsConceded": 0, "totalEarnings": 0,
    })

    stats["matchesPlayed"] += 1
    stats["goalsScored"] += match_result["ourGoals"]
    stats["goalsConceded"] += match_result["theirGoals"]
    stats["totalEarnings"] += match_result["moneyEarned"]

    if match_result["result"] == "win":
        stats["wins"] += 1
    elif match_result["result"] == "draw":
        stats["draws"] += 1
    else:
        stats["losses"] += 1

    st["money"] += match_result["moneyEarned"]
    st["reputation"] += match_result["repEarned"]

    # Add to match history (keep last 20)
    history = st.setdefault("matchHistory", [])
    history.append(match_result)
    st["matchHistory"] = history[-20:]

    # Player value boost for wins
    if match_result["result"] == "win":
        for player in st["players"][:11]:
            player["value"] = int(player["value"] * 1.01)  # 1% boost

    return match_result


def apply_training(st, training_type, player_id):
    """Apply training to a specific player."""
    training = TRAINING_TYPES.get(training_type)
    if not training:
        return None, "Invalid training type"

    player = None
    for p in st["players"]:
        if p["id"] == player_id:
            player = p
            break
    if not player:
        return None, "Player not found"

    if st["money"] < training["cost"]:
        return None, "Not enough money"

    # Check training cooldown for this player
    player_cooldowns = st.setdefault("playerTrainingCooldowns", {})
    now = time.time()
    cooldown_reduction = st.get("clubFacilities", {}).get("training", 0) * 30  # -30s per level
    effective_cooldown = max(60, training["cooldown"] - cooldown_reduction)

    if player_id in player_cooldowns:
        if now < player_cooldowns[player_id]:
            remaining = int(player_cooldowns[player_id] - now)
            return None, f"Training cooldown: {remaining}s remaining"

    # Apply training
    st["money"] -= training["cost"]
    player_cooldowns[player_id] = now + effective_cooldown

    # Boost stats
    boost_lo, boost_hi = training["boost"]
    boosted_stats = []
    for stat in training["stats"]:
        if stat in player.get("stats", {}):
            boost = random.randint(boost_lo, boost_hi)
            player["stats"][stat] = min(99, player["stats"][stat] + boost)
            boosted_stats.append(f"{stat} +{boost}")

    # Recalculate overall
    if "stats" in player:
        player["stats"]["overall"] = int(sum(v for k, v in player["stats"].items() if k != "overall") / 6)

    # Chance to boost player value
    if random.random() < 0.3:
        player["value"] = int(player["value"] * 1.05)

    # Track last trained player for quick train
    last_trained = st.setdefault("lastTrainedPlayers", [])
    if player_id not in last_trained:
        last_trained.append(player_id)
    # Keep only the most recent batch (up to 5)
    st["lastTrainedPlayers"] = last_trained[-5:]
    st["lastTrainingType"] = training_type

    return {
        "player": player["name"],
        "training": training["name"],
        "boosts": boosted_stats,
        "newOverall": player["stats"]["overall"],
    }, None


def get_club_facility_cost(facility, level):
    """Calculate cost of upgrading a club facility."""
    base_costs = {
        "stadium": 50000,
        "training": 25000,
        "youth": 75000,
    }
    base = base_costs.get(facility, 50000)
    return int(base * (2 ** level))


def migrate_state(st):
    """Ensure old save files have all new fields."""
    defaults = default_state()
    for key in defaults:
        if key not in st:
            st[key] = defaults[key]
    if "autoSign" not in st.get("upgrades", {}):
        st["upgrades"]["autoSign"] = 0
    # Migrate club stats
    if "clubStats" not in st:
        st["clubStats"] = defaults["clubStats"]
    if "matchHistory" not in st:
        st["matchHistory"] = []
    if "nextMatchTime" not in st:
        st["nextMatchTime"] = time.time()
    if "lastTrainingTime" not in st:
        st["lastTrainingTime"] = time.time()
    if "clubFacilities" not in st:
        st["clubFacilities"] = defaults["clubFacilities"]
    if "playerTrainingCooldowns" not in st:
        st["playerTrainingCooldowns"] = {}
    if "startingLineup" not in st:
        st["startingLineup"] = {}
    if "totalPlaytime" not in st:
        st["totalPlaytime"] = 0
    if "sessionStart" not in st:
        st["sessionStart"] = time.time()
    if "lastActiveTime" not in st:
        st["lastActiveTime"] = time.time()
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
    now = time.time()

    # Auto-activate club if ready and not yet active
    if not st.get("clubActive") and check_club_ready(st):
        if not st.get("clubName"):
            st["clubName"] = f"FC {st.get('players', [{}])[0].get('name', 'United').split()[-1]}" if st.get("players") else "FC United"
        st["clubActive"] = True

    # Clean up expired training cooldowns
    cooldowns = st.get("playerTrainingCooldowns", {})
    active_cooldowns = {pid: cd for pid, cd in cooldowns.items() if cd > now}

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
        # Club system
        "clubStats": st.get("clubStats", {}),
        "matchHistory": st.get("matchHistory", [])[-10:],
        "nextMatchTime": st.get("nextMatchTime", now),
        "clubFacilities": st.get("clubFacilities", {}),
        "clubOverall": calculate_club_overall(st),
        "availableOpponents": get_available_opponents(st) if st.get("clubActive") else [],
        "trainingTypes": TRAINING_TYPES,
        "playerTrainingCooldowns": active_cooldowns,
        "startingLineup": st.get("startingLineup", {}),
        "autoScoutSettings": st.get("autoScoutSettings", {"targetPositions": [], "targetTiers": [], "minOverall": 0}),
        "lastVipClaim": st.get("lastVipClaim", 0),
        "facilityUpgradeCosts": {
            "stadium": get_club_facility_cost("stadium", st.get("clubFacilities", {}).get("stadium", 0)),
            "training": get_club_facility_cost("training", st.get("clubFacilities", {}).get("training", 0)),
            "youth": get_club_facility_cost("youth", st.get("clubFacilities", {}).get("youth", 0)),
        },
        "matchCooldown": max(0, st.get("nextMatchTime", now) - now),
        "lastTrainedPlayers": st.get("lastTrainedPlayers", []),
        "lastTrainingType": st.get("lastTrainingType", ""),
        "league": st.get("league"),
        "leagueSeason": st.get("leagueSeason", 0),
        "pendingEventPopup": st.pop("pendingEventPopup", None),
        # Playtime
        "totalPlaytime": st.get("totalPlaytime", 0),
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
