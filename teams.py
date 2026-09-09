# Canonical 2026/2027 teams, managers, and which historical years count toward each manager's trend profile
TEAMS = [
    {"team": "X BLADZ", "manager": "Johnny", "years": {2024: "X BLADZ", 2025: "X BLADZ", 2026: "X BLADZ"}},
    {"team": "The Hunter Gatherers", "manager": "Kody", "years": {2026: "The Hunter Gatherers"}},  # new manager 2026 only
    {"team": "Moonlight Graham", "manager": "Greg", "years": {2024: "Moonlight Graham", 2025: "Moonlight Graham", 2026: "Moonlight Graham"}},
    {"team": "My Filipina \u2764\ufe0f's Dried Fish", "manager": "Billy", "years": {2024: "MyFilipina \u2764\ufe0fDriedFish", 2025: "My Filipina \u2764\ufe0f's Dried Fish", 2026: "My Filipina \u2764\ufe0f's Dried Fish"}},
    {"team": "Ball Knower", "manager": "Evan", "years": {2025: "Lisan Al Gain", 2026: "Ball Knower"}},  # different manager 2024
    {"team": "For Whom Skubal Tolls", "manager": "Blake", "years": {2024: "Burnesing Down the House", 2025: "For Whom Skubal Tolls", 2026: "For Whom Skubal Tolls"}},
    {"team": "Acuna & Friends", "manager": "Connor", "years": {2024: "Acuna & Friends", 2025: "Acuna & Friends", 2026: "Acuna & Friends"}},
    {"team": "Lightning McLean", "manager": "Vinny", "years": {2024: "Purple Princes", 2025: "Obi-Juan Kenobi", 2026: "Lightning McLean"}},
    {"team": "jd", "manager": "John", "years": {2024: "jd", 2025: "jd", 2026: "jd"}},
    {"team": "The Boss Hogg Brigade", "manager": "Joe", "years": {2024: "The Boss Hogg Brigade", 2025: "The Boss Hogg Brigade", 2026: "The Boss Hogg Brigade"}},
    {"team": "Cruz Control", "manager": "Andrew", "years": {2024: "Power Rangers", 2025: "Cruz Control", 2026: "Cruz Control"}},  # same manager all 3 years, team renamed
    {"team": "New York No Sox", "manager": "Rob", "years": {2024: "New York No Sox", 2025: "New York No Sox", 2026: "New York No Sox"}},
]

# Fantrax roster-export short codes -> canonical team name
TEAM_CODE_MAP = {
    "jd": "jd",
    "THG": "The Hunter Gatherers",
    "BHB": "The Boss Hogg Brigade",
    "NYX": "New York No Sox",
    "MG": "Moonlight Graham",
    "AF": "Acuna & Friends",
    "XBLADZ": "X BLADZ",
    "iKnoBall": "Ball Knower",
    "2xChamp": "My Filipina \u2764\ufe0f's Dried Fish",
    "Ka-Chow": "Lightning McLean",
    "ThePhils": "Cruz Control",
    "BVB": "For Whom Skubal Tolls",
}

# Roster board layout, in display order. Each entry is (slot_id, slot_label).
# Active spots per your league settings, then bench/IL/MiLB.
ROSTER_SLOTS = (
    [("C", "C"), ("1B", "1B"), ("2B", "2B"), ("SS", "SS"), ("3B", "3B"), ("INF", "INF")]
    + [(f"OF{i}", "OF") for i in range(1, 4)]
    + [("UT", "UT")]
    + [(f"SP{i}", "SP") for i in range(1, 3)]
    + [(f"RP{i}", "RP") for i in range(1, 3)]
    + [(f"P{i}", "P") for i in range(1, 5)]
    + [(f"RES{i}", "RES") for i in range(1, 10)]
    + [(f"IL{i}", "IL") for i in range(1, 4)]
    + [(f"MILB{i}", "MiLB") for i in range(1, 5)]
)

SCORING_CATEGORIES = {
    "Hitting": ["H", "HR", "RBI", "R", "SB", "AVG", "OPS"],
    "Pitching": ["HLD", "SV", "ERA", "K/9", "BB/BF", "WHIP", "WQCS"],
}
