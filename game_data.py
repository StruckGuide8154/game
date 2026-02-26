"""
Game constants: player names, clubs, leagues, markets, tiers, upgrades, expenses, positions, formations.
"""
import random

# ---------------------------------------------------------------------------
# Player positions and formations
# ---------------------------------------------------------------------------
POSITIONS = ["GK", "CB", "LB", "RB", "CDM", "CM", "CAM", "LW", "RW", "ST"]

POSITION_LABELS = {
    "GK": "Goalkeeper", "CB": "Centre Back", "LB": "Left Back", "RB": "Right Back",
    "CDM": "Defensive Mid", "CM": "Central Mid", "CAM": "Attacking Mid",
    "LW": "Left Wing", "RW": "Right Wing", "ST": "Striker",
}

# Stat weights per position (pace, shooting, passing, dribbling, defending, physical)
POSITION_STAT_WEIGHTS = {
    "GK":  (0.3, 0.1, 0.4, 0.2, 0.6, 0.7),
    "CB":  (0.5, 0.2, 0.4, 0.3, 0.9, 0.8),
    "LB":  (0.7, 0.3, 0.6, 0.5, 0.7, 0.6),
    "RB":  (0.7, 0.3, 0.6, 0.5, 0.7, 0.6),
    "CDM": (0.5, 0.3, 0.6, 0.5, 0.8, 0.7),
    "CM":  (0.6, 0.5, 0.8, 0.7, 0.5, 0.6),
    "CAM": (0.6, 0.7, 0.8, 0.8, 0.3, 0.4),
    "LW":  (0.9, 0.7, 0.6, 0.9, 0.2, 0.4),
    "RW":  (0.9, 0.7, 0.6, 0.9, 0.2, 0.4),
    "ST":  (0.8, 0.9, 0.5, 0.7, 0.2, 0.6),
}

FORMATIONS = {
    "4-3-3":   {"positions": ["GK", "LB", "CB", "CB", "RB", "CM", "CM", "CM", "LW", "RW", "ST"], "label": "4-3-3"},
    "4-4-2":   {"positions": ["GK", "LB", "CB", "CB", "RB", "LW", "CM", "CM", "RW", "ST", "ST"], "label": "4-4-2"},
    "4-2-3-1": {"positions": ["GK", "LB", "CB", "CB", "RB", "CDM", "CDM", "LW", "CAM", "RW", "ST"], "label": "4-2-3-1"},
    "3-5-2":   {"positions": ["GK", "CB", "CB", "CB", "LW", "CM", "CDM", "CM", "RW", "ST", "ST"], "label": "3-5-2"},
    "4-1-4-1": {"positions": ["GK", "LB", "CB", "CB", "RB", "CDM", "LW", "CM", "CM", "RW", "ST"], "label": "4-1-4-1"},
}

# ---------------------------------------------------------------------------
# Real player names by nationality
# ---------------------------------------------------------------------------
PLAYER_NAMES = {
    "brazilian": [
        ("Raphael", "Santos"), ("Lucas", "Oliveira"), ("Gabriel", "Silva"),
        ("Matheus", "Costa"), ("Felipe", "Ferreira"), ("Vinicius", "Almeida"),
        ("Bruno", "Souza"), ("Thiago", "Pereira"), ("Caio", "Ribeiro"),
        ("Pedro", "Gomes"), ("Enzo", "Barbosa"), ("Kaio", "Mendes"),
    ],
    "spanish": [
        ("Alejandro", "Garcia"), ("Pablo", "Martinez"), ("Carlos", "Hernandez"),
        ("Diego", "Lopez"), ("Alvaro", "Fernandez"), ("Marco", "Ruiz"),
        ("Adrian", "Moreno"), ("Hugo", "Jimenez"), ("Sergio", "Romero"),
        ("Iker", "Navarro"), ("Dani", "Iglesias"), ("Jordi", "Torres"),
    ],
    "english": [
        ("Jack", "Williams"), ("Harry", "Jones"), ("Oliver", "Taylor"),
        ("George", "Brown"), ("James", "Wilson"), ("Charlie", "Davies"),
        ("Thomas", "Evans"), ("Oscar", "Roberts"), ("Freddie", "Walker"),
        ("Archie", "Wright"), ("Leo", "Thompson"), ("Alfie", "Hughes"),
    ],
    "french": [
        ("Antoine", "Dupont"), ("Kylian", "Moreau"), ("Ousmane", "Laurent"),
        ("Adrien", "Bernard"), ("Theo", "Leroy"), ("Jules", "Roux"),
        ("Rayan", "Girard"), ("Mathis", "Bonnet"), ("Enzo", "Lambert"),
        ("Nolan", "Dubois"), ("Hugo", "Mercier"), ("Lucas", "Fontaine"),
    ],
    "german": [
        ("Florian", "Mueller"), ("Leon", "Schmidt"), ("Kai", "Schneider"),
        ("Jonas", "Fischer"), ("Felix", "Weber"), ("Niklas", "Wagner"),
        ("Maximilian", "Becker"), ("Luca", "Hoffmann"), ("Tim", "Schulz"),
        ("Lukas", "Richter"), ("Finn", "Koch"), ("Julian", "Wolf"),
    ],
    "italian": [
        ("Alessandro", "Rossi"), ("Lorenzo", "Russo"), ("Marco", "Ferrari"),
        ("Luca", "Esposito"), ("Matteo", "Bianchi"), ("Andrea", "Romano"),
        ("Federico", "Colombo"), ("Giacomo", "Ricci"), ("Davide", "Marino"),
        ("Tommaso", "Greco"), ("Nicolo", "Conti"), ("Simone", "Gallo"),
    ],
    "portuguese": [
        ("Diogo", "Fernandes"), ("Bernardo", "Rodrigues"), ("Joao", "Goncalves"),
        ("Rui", "Martins"), ("Andre", "Sousa"), ("Tiago", "Lopes"),
        ("Nuno", "Marques"), ("Pedro", "Alves"), ("Rafael", "Pereira"),
        ("Goncalo", "Pinto"), ("Francisco", "Correia"), ("Miguel", "Carvalho"),
    ],
    "dutch": [
        ("Daan", "de Jong"), ("Sem", "de Boer"), ("Luuk", "Bakker"),
        ("Thijs", "Visser"), ("Bram", "Smit"), ("Finn", "Mulder"),
        ("Jesse", "de Groot"), ("Lars", "Bos"), ("Sven", "Vos"),
        ("Niek", "Peters"), ("Ruben", "Hendriks"), ("Joep", "van Dijk"),
    ],
    "argentinian": [
        ("Nicolas", "Gonzalez"), ("Lautaro", "Martinez"), ("Julian", "Alvarez"),
        ("Enzo", "Fernandez"), ("Thiago", "Almada"), ("Matias", "Sosa"),
        ("Valentin", "Barco"), ("Facundo", "Medina"), ("Alejandro", "Garnacho"),
        ("Santiago", "Gimenez"), ("Franco", "Colapinto"), ("Tomas", "Belmonte"),
    ],
    "african": [
        ("Amadou", "Diallo"), ("Ibrahim", "Konate"), ("Moussa", "Dembele"),
        ("Ismail", "Sarr"), ("Naby", "Keita"), ("Sadio", "Traore"),
        ("Edmond", "Tapsoba"), ("Kalidou", "Koulibaly"), ("Mohamed", "Salah"),
        ("Victor", "Osimhen"), ("Samuel", "Chukwueze"), ("Andre", "Onana"),
    ],
}

ALL_NATIONALITIES = list(PLAYER_NAMES.keys())

# ---------------------------------------------------------------------------
# Clubs grouped by league
# ---------------------------------------------------------------------------
CLUB_LEAGUES = {
    "Premier League": {
        "clubs": [
            "Manchester City", "Arsenal", "Liverpool", "Manchester United",
            "Chelsea", "Tottenham", "Newcastle United", "Aston Villa",
            "Brighton", "West Ham",
        ],
        "nationalities": ["english", "brazilian", "french", "spanish", "portuguese", "dutch", "african"],
        "tier": 3,
    },
    "La Liga": {
        "clubs": [
            "Real Madrid", "Barcelona", "Atletico Madrid", "Real Sociedad",
            "Villarreal", "Athletic Bilbao", "Sevilla", "Real Betis",
        ],
        "nationalities": ["spanish", "brazilian", "french", "argentinian", "portuguese", "african"],
        "tier": 3,
    },
    "Bundesliga": {
        "clubs": [
            "Bayern Munich", "Borussia Dortmund", "RB Leipzig", "Bayer Leverkusen",
            "Eintracht Frankfurt", "Wolfsburg", "Freiburg", "Union Berlin",
        ],
        "nationalities": ["german", "french", "dutch", "african", "brazilian", "argentinian"],
        "tier": 2,
    },
    "Serie A": {
        "clubs": [
            "Inter Milan", "AC Milan", "Juventus", "Napoli",
            "Roma", "Lazio", "Atalanta", "Fiorentina",
        ],
        "nationalities": ["italian", "argentinian", "brazilian", "portuguese", "french", "african"],
        "tier": 2,
    },
    "Ligue 1": {
        "clubs": [
            "PSG", "Marseille", "Monaco", "Lyon",
            "Lille", "Nice", "Lens", "Rennes",
        ],
        "nationalities": ["french", "brazilian", "african", "portuguese", "argentinian", "spanish"],
        "tier": 2,
    },
    "Eredivisie": {
        "clubs": [
            "Ajax", "PSV", "Feyenoord", "AZ Alkmaar",
            "FC Twente", "Utrecht",
        ],
        "nationalities": ["dutch", "brazilian", "african", "argentinian"],
        "tier": 1,
    },
    "Liga Portugal": {
        "clubs": [
            "Benfica", "Porto", "Sporting CP", "Braga",
            "Vitoria Guimaraes",
        ],
        "nationalities": ["portuguese", "brazilian", "african", "argentinian"],
        "tier": 1,
    },
    "Youth Academies": {
        "clubs": [
            "La Masia Academy", "Clairefontaine", "Ajax Youth",
            "Sporting Academy", "Santos Academy", "Dortmund Youth",
        ],
        "nationalities": ALL_NATIONALITIES,
        "tier": 0,
    },
}

# ---------------------------------------------------------------------------
# Markets, tiers, upgrades, expenses
# ---------------------------------------------------------------------------
MARKETS = [
    {"name": "Youth Academy",     "multiplier": 1,  "minRep": 0,     "color": "text-slate-400",  "leagueTier": 0},
    {"name": "Domestic League",   "multiplier": 2,  "minRep": 80,    "color": "text-emerald-400","leagueTier": 1},
    {"name": "European Circuit",  "multiplier": 5,  "minRep": 250,   "color": "text-blue-400",   "leagueTier": 2},
    {"name": "World Class",       "multiplier": 12, "minRep": 600,   "color": "text-amber-400",  "leagueTier": 3},
    {"name": "Global Superstars", "multiplier": 30, "minRep": 1500,  "color": "text-purple-400", "leagueTier": 3},
]

PLAYER_TIERS = [
    {"name": "Prospect",      "baseValue": 1,    "valueRange": [1, 3],       "multiplier": 0.5, "color": "bg-slate-500",  "minRep": 0,     "statRange": [25, 48]},
    {"name": "Rising Star",   "baseValue": 5,    "valueRange": [3, 10],      "multiplier": 1,   "color": "bg-emerald-500","minRep": 50,    "statRange": [40, 58]},
    {"name": "Professional",  "baseValue": 25,   "valueRange": [15, 35],     "multiplier": 2.5, "color": "bg-blue-500",   "minRep": 120,   "statRange": [50, 68]},
    {"name": "International", "baseValue": 100,  "valueRange": [60, 120],    "multiplier": 5,   "color": "bg-purple-500", "minRep": 300,   "statRange": [60, 78]},
    {"name": "World Class",   "baseValue": 500,  "valueRange": [300, 600],   "multiplier": 12,  "color": "bg-amber-500",  "minRep": 800,   "statRange": [72, 86]},
    {"name": "Superstar",     "baseValue": 2500, "valueRange": [1500, 3000], "multiplier": 25,  "color": "bg-red-500",    "minRep": 2000,  "statRange": [82, 96]},
]

UPGRADE_TYPES = {
    "scoutingNetwork":   {"name": "Scouting Network",     "baseCost": 2500,   "description": "Discover talent worldwide",        "effect": "+15% reputation gain per level"},
    "negotiationSkills": {"name": "Negotiation Skills",    "baseCost": 5000,   "description": "Secure better deals",              "effect": "+15% commission per level"},
    "officeSpace":       {"name": "Office Expansion",      "baseCost": 15000,  "description": "Expand your agency capacity",      "effect": "+1 agent slot, +2 player slots per level"},
    "marketingTeam":     {"name": "Marketing Department",  "baseCost": 25000,  "description": "Unlock premium markets",           "effect": "Access higher tier players"},
    "legalTeam":         {"name": "Legal Department",      "baseCost": 50000,  "description": "Handle complex transfers",         "effect": "+10% transfer bonus per level"},
    "mediaConnections":  {"name": "Media Network",         "baseCost": 100000, "description": "Attract sponsorships",             "effect": "Sponsorship deal chance per level"},
    "autoSign":          {"name": "Auto-Scout AI",         "baseCost": 250000, "description": "Automatically scout & sign players","effect": "Toggle auto-signing when purchased"},
}

EXPENSE_CATEGORIES = {
    "office_rent":    {"name": "Office Rent",       "per_level": 150,  "source": "officeSpace"},
    "agent_salary":   {"name": "Agent Salaries",    "per_unit": 500,   "source": "agents"},
    "player_upkeep":  {"name": "Player Management", "per_unit": 30,    "source": "players"},
    "legal_retainer": {"name": "Legal Retainer",    "per_level": 250,  "source": "legalTeam"},
    "marketing_cost": {"name": "Marketing Spend",   "per_level": 200,  "source": "marketingTeam"},
    "media_fees":     {"name": "Media Fees",        "per_level": 400,  "source": "mediaConnections"},
}

# ---------------------------------------------------------------------------
# Club match system - opponent clubs by difficulty tier
# ---------------------------------------------------------------------------
OPPONENT_CLUBS = [
    # Tier 0: Amateur clubs (OVR 30-45)
    {"name": "Sunday League FC", "tier": 0, "ovrRange": [30, 40], "reward": 200, "repReward": 5},
    {"name": "Local United", "tier": 0, "ovrRange": [35, 45], "reward": 350, "repReward": 8},
    {"name": "Village Rovers", "tier": 0, "ovrRange": [32, 42], "reward": 250, "repReward": 6},
    # Tier 1: Semi-pro clubs (OVR 45-60)
    {"name": "Regional Athletic", "tier": 1, "ovrRange": [45, 55], "reward": 800, "repReward": 20},
    {"name": "County Town FC", "tier": 1, "ovrRange": [48, 58], "reward": 1200, "repReward": 30},
    {"name": "Harbor City", "tier": 1, "ovrRange": [50, 60], "reward": 1500, "repReward": 40},
    # Tier 2: Professional clubs (OVR 60-75)
    {"name": "Metro Stars", "tier": 2, "ovrRange": [60, 68], "reward": 5000, "repReward": 80},
    {"name": "Capital Athletic", "tier": 2, "ovrRange": [63, 72], "reward": 7500, "repReward": 120},
    {"name": "Industrial FC", "tier": 2, "ovrRange": [65, 75], "reward": 10000, "repReward": 180},
    # Tier 3: Top-flight clubs (OVR 75-85)
    {"name": "Champions United", "tier": 3, "ovrRange": [75, 82], "reward": 25000, "repReward": 400},
    {"name": "Royal Athletic", "tier": 3, "ovrRange": [78, 84], "reward": 35000, "repReward": 600},
    {"name": "Imperial FC", "tier": 3, "ovrRange": [80, 85], "reward": 50000, "repReward": 900},
    # Tier 4: Elite clubs (OVR 85-95)
    {"name": "Galactic Stars", "tier": 4, "ovrRange": [85, 90], "reward": 100000, "repReward": 2000},
    {"name": "Legends United", "tier": 4, "ovrRange": [88, 93], "reward": 200000, "repReward": 3500},
    {"name": "World Elite FC", "tier": 4, "ovrRange": [90, 95], "reward": 400000, "repReward": 6000},
]

# Training types for club players
TRAINING_TYPES = {
    "fitness": {"name": "Fitness Training", "cost": 2500, "stats": ["pace", "physical"], "boost": (1, 2), "cooldown": 900},
    "technical": {"name": "Technical Drills", "cost": 4000, "stats": ["dribbling", "passing"], "boost": (1, 2), "cooldown": 900},
    "shooting": {"name": "Shooting Practice", "cost": 4000, "stats": ["shooting"], "boost": (1, 3), "cooldown": 900},
    "tactical": {"name": "Tactical Sessions", "cost": 6000, "stats": ["defending", "passing"], "boost": (1, 2), "cooldown": 900},
    "intensive": {"name": "Intensive Camp", "cost": 25000, "stats": ["pace", "shooting", "passing", "dribbling", "defending", "physical"], "boost": (1, 1), "cooldown": 1800},
}

# Random events that can occur every 5-10 minutes
# Events are designed to be IMPACTFUL - they should feel exciting and game-changing
RANDOM_EVENTS = [
    {
        "id": "sponsor_windfall",
        "name": "MEGA SPONSORSHIP DEAL",
        "description": "A global brand wants an EXCLUSIVE deal with your agency! This is a once-in-a-lifetime opportunity!",
        "effect": "money",
        "value": (5000, 25000),
        "weight": 10,
        "icon": "💰",
        "color": "emerald",
    },
    {
        "id": "reputation_boost",
        "name": "VIRAL MEDIA SENSATION",
        "description": "Your agency went viral on social media! Everyone is talking about your players!",
        "effect": "reputation",
        "value": (30, 150),
        "weight": 15,
        "icon": "📺",
        "color": "blue",
    },
    {
        "id": "player_breakthrough",
        "name": "SUPERSTAR PERFORMANCE",
        "description": "One of your players just scored a hat-trick in a major tournament! Their value is going up!",
        "effect": "player_value",
        "value": (1.1, 1.3),
        "weight": 12,
        "icon": "⭐",
        "color": "amber",
    },
    {
        "id": "scout_tip",
        "name": "INSIDER TIP - HIDDEN GEM",
        "description": "A trusted scout found an interesting talent that nobody knows about yet!",
        "effect": "free_scout",
        "value": None,
        "weight": 8,
        "icon": "🔍",
        "color": "purple",
    },
    {
        "id": "bonus_commission",
        "name": "SURPRISE BONUS PAYOUT",
        "description": "A completed transfer triggered a performance bonus clause!",
        "effect": "money",
        "value": (3000, 15000),
        "weight": 12,
        "icon": "💵",
        "color": "emerald",
    },
    {
        "id": "agent_motivation",
        "name": "AGENT FOCUS MODE",
        "description": "Your team is working extra hard today! They're closing deals efficiently.",
        "effect": "temp_earnings_boost",
        "value": (1.2, 1.6),
        "weight": 10,
        "icon": "🔥",
        "color": "red",
    },
    {
        "id": "training_opportunity",
        "name": "TRAINING CAMP OFFER",
        "description": "A coach is offering a FREE training session for your players!",
        "effect": "free_training",
        "value": None,
        "weight": 8,
        "icon": "🏋️",
        "color": "purple",
    },
    {
        "id": "contract_extension",
        "name": "CONTRACT RENEWAL BONUS",
        "description": "A club renewed your player's contract with a nice bonus!",
        "effect": "money",
        "value": (4000, 20000),
        "weight": 10,
        "icon": "📝",
        "color": "emerald",
    },
    {
        "id": "youth_talent",
        "name": "WONDERKID DISCOVERED",
        "description": "You've found a promising young talent! People are starting to notice your agency.",
        "effect": "reputation",
        "value": (25, 100),
        "weight": 12,
        "icon": "🌟",
        "color": "amber",
    },
    {
        "id": "lucky_break",
        "name": "LUCKY DAY",
        "description": "A deal closed with extra bonuses and some good press coverage!",
        "effect": "multi",
        "value": {"money": (5000, 20000), "reputation": (30, 120)},
        "weight": 5,
        "icon": "🎰",
        "color": "amber",
    },
]


# ---------------------------------------------------------------------------
# Helper functions for data generation
# ---------------------------------------------------------------------------
def generate_realistic_player_name(nationality=None):
    """Generate a player name that fits the nationality."""
    if nationality is None:
        nationality = random.choice(ALL_NATIONALITIES)
    names = PLAYER_NAMES.get(nationality, PLAYER_NAMES["english"])
    first, last = random.choice(names)
    return first, last, nationality


def generate_player_stats(position, tier_name):
    """Generate realistic stats based on position and tier."""
    tier = None
    for t in PLAYER_TIERS:
        if t["name"] == tier_name:
            tier = t
            break
    if not tier:
        tier = PLAYER_TIERS[0]

    lo, hi = tier.get("statRange", [30, 55])
    weights = POSITION_STAT_WEIGHTS.get(position, (0.5, 0.5, 0.5, 0.5, 0.5, 0.5))
    stat_names = ["pace", "shooting", "passing", "dribbling", "defending", "physical"]
    stats = {}
    for i, sn in enumerate(stat_names):
        w = weights[i]
        base = random.randint(lo, hi)
        adjusted = int(base * (0.7 + w * 0.5))
        stats[sn] = max(1, min(99, adjusted))
    stats["overall"] = int(sum(stats.values()) / len(stats))
    return stats


def get_realistic_club(market_tier, player_nationality=None):
    """Pick a club that makes sense for the player's profile."""
    eligible_leagues = []
    for league_name, league_data in CLUB_LEAGUES.items():
        if league_data["tier"] <= market_tier:
            weight = 1
            if player_nationality and player_nationality in league_data["nationalities"]:
                weight = 3
            eligible_leagues.append((league_name, league_data, weight))

    if not eligible_leagues:
        eligible_leagues = [("Youth Academies", CLUB_LEAGUES["Youth Academies"], 1)]

    total = sum(w for _, _, w in eligible_leagues)
    r = random.random() * total
    for league_name, league_data, w in eligible_leagues:
        r -= w
        if r <= 0:
            return random.choice(league_data["clubs"]), league_name
    return eligible_leagues[0][1]["clubs"][0], eligible_leagues[0][0]


# ---------------------------------------------------------------------------
# Card Collection Albums
# ---------------------------------------------------------------------------
CARD_ALBUMS = {
    "rising_talents": {
        "name": "Rising Talents",
        "description": "Collect 5 Rising Star or higher players",
        "requirement": {"minTier": "Rising Star", "count": 5},
        "bonus": {"type": "earnings_mult", "value": 1.1, "label": "+10% Earnings"},
        "icon": "star",
    },
    "world_beaters": {
        "name": "World Beaters",
        "description": "Collect 3 World Class or Superstar players",
        "requirement": {"minTier": "World Class", "count": 3},
        "bonus": {"type": "earnings_mult", "value": 1.2, "label": "+20% Earnings"},
        "icon": "trophy",
    },
    "full_squad": {
        "name": "Full Squad",
        "description": "Have 11 or more players in your roster",
        "requirement": {"type": "player_count", "count": 11},
        "bonus": {"type": "rep_mult", "value": 1.15, "label": "+15% Reputation"},
        "icon": "users",
    },
    "global_network": {
        "name": "Global Network",
        "description": "Have players from 5 different nationalities",
        "requirement": {"type": "nationalities", "count": 5},
        "bonus": {"type": "earnings_mult", "value": 1.15, "label": "+15% Earnings"},
        "icon": "globe",
    },
    "position_master": {
        "name": "Position Master",
        "description": "Have players in 8 different positions",
        "requirement": {"type": "positions", "count": 8},
        "bonus": {"type": "earnings_mult", "value": 1.1, "label": "+10% Earnings"},
        "icon": "grid",
    },
    "transfer_king": {
        "name": "Transfer King",
        "description": "Complete 20 transfers",
        "requirement": {"type": "transfers", "count": 20},
        "bonus": {"type": "earnings_mult", "value": 1.25, "label": "+25% Earnings"},
        "icon": "exchange",
    },
    "millionaire": {
        "name": "Millionaire Club",
        "description": "Accumulate $1,000,000 total commission",
        "requirement": {"type": "commission", "count": 1000000},
        "bonus": {"type": "earnings_mult", "value": 1.3, "label": "+30% Earnings"},
        "icon": "dollar",
    },
    "champion": {
        "name": "Champion Manager",
        "description": "Win 10 club matches",
        "requirement": {"type": "wins", "count": 10},
        "bonus": {"type": "rep_mult", "value": 1.2, "label": "+20% Reputation"},
        "icon": "medal",
    },
}

TIER_ORDER = ["Prospect", "Rising Star", "Professional", "International", "World Class", "Superstar"]

# League system - countries and their league tiers
LEAGUE_COUNTRIES = {
    "england": {"name": "England", "flag": "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f", "leagues": ["League Two", "League One", "Championship", "Premier League"]},
    "spain": {"name": "Spain", "flag": "\U0001f1ea\U0001f1f8", "leagues": ["Segunda B", "Segunda Divisi\u00f3n", "La Liga 2", "La Liga"]},
    "germany": {"name": "Germany", "flag": "\U0001f1e9\U0001f1ea", "leagues": ["3. Liga", "2. Bundesliga", "Bundesliga"]},
    "france": {"name": "France", "flag": "\U0001f1eb\U0001f1f7", "leagues": ["National 2", "National", "Ligue 2", "Ligue 1"]},
    "italy": {"name": "Italy", "flag": "\U0001f1ee\U0001f1f9", "leagues": ["Serie C", "Serie B", "Serie A"]},
    "portugal": {"name": "Portugal", "flag": "\U0001f1f5\U0001f1f9", "leagues": ["Liga 3", "Liga Portugal 2", "Primeira Liga"]},
    "netherlands": {"name": "Netherlands", "flag": "\U0001f1f3\U0001f1f1", "leagues": ["Eerste Divisie", "Eredivisie"]},
    "brazil": {"name": "Brazil", "flag": "\U0001f1e7\U0001f1f7", "leagues": ["S\u00e9rie D", "S\u00e9rie C", "S\u00e9rie B", "S\u00e9rie A"]},
    "usa": {"name": "USA", "flag": "\U0001f1fa\U0001f1f8", "leagues": ["USL League One", "USL Championship", "MLS"]},
}
