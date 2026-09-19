"""Parsers for every uploadable document the app accepts. Each function takes
raw bytes (from st.file_uploader) and returns clean Python data structures.
"""
import csv
import io
import re
import shutil
import subprocess
import tempfile

import openpyxl

from teams import TEAMS, TEAM_CODE_MAP

CANON_TEAMS = [t["team"] for t in TEAMS]


# --------------------------- Draft order (PDF) ---------------------------

def _flexible_pattern(name):
    # Tolerate any amount of whitespace anywhere spacing might vary across
    # different Fantrax exports (e.g. around the heart emoji in the
    # "My Filipina" team name), by allowing optional whitespace between
    # every character of the (space-stripped) name.
    chars = [c for c in name if not c.isspace()]
    return r"\s*".join(re.escape(c) for c in chars)


def parse_draft_order_pdf(file_bytes):
    """Parse the Fantrax 'By Round' draft-picks PDF export into
    [{round, slot, team, original_owner}, ...] for all 25 rounds x 12 teams.
    Requires the `pdftotext` binary (poppler-utils) to be available.
    """
    if shutil.which("pdftotext") is None:
        raise RuntimeError(
            "pdftotext isn't installed on this server. Add a packages.txt file "
            "with a single line 'poppler-utils' to your repo so Streamlit Cloud "
            "installs it, then reboot the app."
        )
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp_in, \
         tempfile.NamedTemporaryFile(suffix=".txt") as tmp_out:
        tmp_in.write(file_bytes)
        tmp_in.flush()
        subprocess.run(["pdftotext", "-layout", tmp_in.name, tmp_out.name], check=True)
        text = open(tmp_out.name, encoding="utf-8").read()

    lines = text.split("\n")
    clean = [
        l for l in lines
        if "fantrax.com" not in l and not l.strip().startswith("9/") and l.strip() != ""
        and not ("Team" in l and "Original Owner" in l)
    ]
    body = re.sub(r"[ \t]+", " ", "\n".join(clean))
    pattern = "|".join(_flexible_pattern(t) for t in sorted(CANON_TEAMS, key=len, reverse=True))
    raw_matches = re.findall(pattern, body)
    stripped_to_canon = {re.sub(r"\s+", "", t): t for t in CANON_TEAMS}
    matches = [stripped_to_canon[re.sub(r"\s+", "", m)] for m in raw_matches]

    if len(matches) != 600:
        raise ValueError(
            f"Expected 600 team-name tokens (25 rounds x 12 slots x 2 columns) "
            f"but found {len(matches)}. The PDF layout may not match what this "
            f"parser expects — check for a Fantrax export format change."
        )

    draft_order = []
    idx = 0
    round_pairs = [(r, r + 1) for r in range(1, 25, 2)]
    for rA, rB in round_pairs:
        for slot in range(1, 13):
            teamA, origA, teamB, origB = matches[idx:idx + 4]
            idx += 4
            draft_order.append({"round": rA, "slot": slot, "team": teamA, "original_owner": origA})
            draft_order.append({"round": rB, "slot": slot, "team": teamB, "original_owner": origB})
    for slot in range(1, 13):
        team, orig = matches[idx:idx + 2]
        idx += 2
        draft_order.append({"round": 25, "slot": slot, "team": team, "original_owner": orig})

    draft_order.sort(key=lambda r: (r["round"], r["slot"]))
    return draft_order


# --------------------------- Manual draft order reordering ---------------------------

def extract_trades(draft_order):
    """Returns {(round, original_owner_team): current_owner_team} for every
    pick that was traded away from its original slot-owner. Untraded picks
    (team == original_owner) aren't included — they default back to whoever
    owns that slot.
    """
    trades = {}
    for r in draft_order:
        if r["team"] != r["original_owner"]:
            trades[(r["round"], r["original_owner"])] = r["team"]
    return trades


def slot_order_from_draft_order(draft_order):
    """The fixed slot-1..12 -> team mapping (a team's original draft
    position never changes round to round, only who currently owns a given
    round's pick does), read off round 1."""
    round1 = {r["slot"]: r["original_owner"] for r in draft_order if r["round"] == 1}
    return [round1[s] for s in range(1, 13)]


def rebuild_draft_order_with_new_slots(base_draft_order, new_slot_order):
    """Re-seats teams into new slot 1..12 positions. Every trade stays
    attached to the team that made it, not the slot number — so if Team A
    traded away their round 5 pick, Team A's round 5 pick is still traded
    away no matter which slot Team A now sits in.
    """
    trades = extract_trades(base_draft_order)
    rounds = sorted({r["round"] for r in base_draft_order})
    new_rows = []
    for rnd in rounds:
        for slot, orig in enumerate(new_slot_order, start=1):
            team = trades.get((rnd, orig), orig)
            new_rows.append({"round": rnd, "slot": slot, "team": team, "original_owner": orig})
    return new_rows


# ------------------------------- ADP (CSV) --------------------------------

def parse_adp_csv(file_bytes):
    text = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        if not row.get("Player"):
            continue
        name = row["Player"].strip()
        ft = (row.get("FT") or "").strip()
        avg = (row.get("AVG") or "").strip()
        adp = float(ft) if ft else (float(avg) if avg else None)
        if adp is None:
            continue
        display_name = "Shohei Ohtani (Pitcher)" if name == "Shohei Ohtani" else name
        rows.append({
            "player": display_name,
            "mlb_team": row.get("Team", ""),
            "positions": row.get("Positions", ""),
            "adp": adp,
            "adp_round": int((adp - 1) // 12) + 1,
        })

    # The source file occasionally has more than one row resolving to the
    # same name (e.g. "Shohei Ohtani" -> renamed to "(Pitcher)" collides
    # with an already-separately-listed "Shohei Ohtani (Pitcher)" row
    # further down). Keep only the best (lowest) ADP per name.
    best_by_name = {}
    for r in rows:
        existing = best_by_name.get(r["player"])
        if existing is None or r["adp"] < existing["adp"]:
            best_by_name[r["player"]] = r
    rows = list(best_by_name.values())

    # Force this regardless of which duplicate row won dedup above —
    # FantasyPros doesn't have a real Fantrax pitcher-only ADP for him.
    for r in rows:
        if r["player"] == "Shohei Ohtani (Pitcher)":
            r["adp"] = 99.0
            r["adp_round"] = int((99.0 - 1) // 12) + 1

    rows.sort(key=lambda r: r["adp"])
    return rows


# ----------------------------- Rosters (CSV) -------------------------------

def parse_rosters_csv(file_bytes):
    text = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        code = row.get("Status", "")
        team = TEAM_CODE_MAP.get(code)
        rows.append({
            "player": row.get("Player", ""),
            "mlb_team": row.get("Team", ""),
            "position": row.get("Position", ""),
            "team_code": code,
            "canonical_team": team,
            "roster_status": row.get("Roster Status", ""),
        })
    return rows


# ------------------------------ Injuries (CSV) ------------------------------

def parse_injury_csvs(taken_bytes, available_bytes):
    """Returns a set of (player_name, mlb_team) tuples — same name-collision
    reasoning as MiLB matching below.
    """
    entries = set()
    for b in (taken_bytes, available_bytes):
        if not b:
            continue
        text = b.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            p = (row.get("Player") or "").strip()
            if p:
                entries.add((p, (row.get("Team") or "").strip()))
    return entries


# -------------------------------- MiLB (CSV) --------------------------------

def parse_milb_csvs(taken_bytes, available_bytes):
    """Returns a set of (player_name, mlb_team) tuples. Matching on name alone
    is unsafe — several MLB stars share exact names with obscure prospects
    (e.g. two different "Jose Ramirez"s), so team is required too.
    """
    entries = set()
    for b in (taken_bytes, available_bytes):
        if not b:
            continue
        text = b.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            name = None
            for key in ("Player", "Name", "player", "name"):
                if row.get(key):
                    name = row[key].strip()
                    break
            if name:
                entries.add((name, (row.get("Team") or "").strip()))
    return entries


# ------------------------- Full Fantrax player universe -------------------------

def parse_full_player_list(file_bytes):
    """The complete Fantrax player pool (~10k players) — used to backfill
    anyone missing from the FantasyPros ADP list (deep bench, IL/MiLB
    stashes, etc.) so the draft pool isn't artificially capped at ~600
    ADP-ranked players.
    """
    text = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        name = (row.get("Player") or "").strip()
        if not name:
            continue
        if name == "Shohei Ohtani-P":
            name = "Shohei Ohtani (Pitcher)"
        elif name == "Shohei Ohtani-H":
            name = "Shohei Ohtani (Batter)"
        rank_ov = row.get("RkOv") or ""
        try:
            rank_ov = int(float(rank_ov))
        except ValueError:
            rank_ov = None
        age = row.get("Age") or ""
        try:
            age = int(age)
        except ValueError:
            age = None
        rows.append({
            "player": name,
            "mlb_team": row.get("Team", ""),
            "positions": row.get("Position", ""),
            "rank_ov": rank_ov,
            "age": age,
        })
    return rows


def merge_full_pool(adp_rows, full_rows):
    """ADP-ranked players keep their real ADP (accurate ordering for the
    rounds that matter). Everyone else from the full Fantrax list gets
    appended after, ordered by Fantrax's own overall rank, so the whole
    player universe is draftable (needed for IL/MiLB slots late in the
    draft) without disturbing early-round accuracy. Age (from the full
    list) is attached to every row where available, since late-round
    auto-draft logic uses it to model real speculative-stash behavior.
    """
    age_by_name = {r["player"]: r["age"] for r in full_rows if r.get("age") is not None}
    known_names = {r["player"] for r in adp_rows}
    max_adp = max((r["adp"] for r in adp_rows), default=0)

    extra = [r for r in full_rows if r["player"] not in known_names]
    extra.sort(key=lambda r: (r["rank_ov"] is None, r["rank_ov"] if r["rank_ov"] is not None else 0))

    merged = []
    for r in adp_rows:
        merged.append({**r, "age": age_by_name.get(r["player"])})
    for i, r in enumerate(extra):
        synthetic_adp = max_adp + 1 + i
        merged.append({
            "player": r["player"],
            "mlb_team": r["mlb_team"],
            "positions": r["positions"],
            "adp": synthetic_adp,
            "adp_round": int((synthetic_adp - 1) // 12) + 1,
            "age": r.get("age"),
        })
    return merged


# --------------------------- Saved keeper picks (CSV) --------------------------

def parse_saved_keeper_picks(file_bytes):
    """Round-trip format for a user's own saved keeper selections — just
    team, round, player. Exported by the app and re-importable later."""
    text = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        team = (row.get("team") or "").strip()
        player = (row.get("player") or "").strip()
        rnd = row.get("round") or ""
        if not team or not player:
            continue
        try:
            rnd = int(float(rnd))
        except ValueError:
            continue
        rows.append({"team": team, "round": rnd, "player": player})
    return rows


def parse_keeper_workbook(file_bytes):
    """Reads every row with a Keeper Round value. Returns one row per
    rostered player with their pre-computed keeper round, for the user to
    manually pick 4-per-team from in the Keepers tab.
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb["Sheet1"] if "Sheet1" in wb.sheetnames else wb[wb.sheetnames[0]]
    headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    idx = {h: i for i, h in enumerate(headers) if h}

    rows = []
    for r in ws.iter_rows(min_row=2):
        vals = [c.value for c in r]
        kr = vals[idx.get("Keeper Round", -1)] if "Keeper Round" in idx else None
        if kr in (None, ""):
            continue
        manager = vals[idx.get("Manager")] if "Manager" in idx else None
        team = next((t["team"] for t in TEAMS if t["manager"] == manager), None)
        rows.append({
            "manager": manager,
            "team": team,
            "keeper_round": int(float(kr)),
            "player": vals[idx.get("Player")] if "Player" in idx else None,
            "mlb_team": vals[idx.get("Team")] if "Team" in idx else None,
            "position": vals[idx.get("Position")] if "Position" in idx else None,
            "status": vals[idx.get("Status") ] if "Status" in idx else None,
            "drafted_or_claimed": vals[idx.get("Drafted vs Claimed")] if "Drafted vs Claimed" in idx else None,
            "adp_round": vals[idx.get("ADP Round")] if "ADP Round" in idx else None,
            "keeper_value": vals[idx.get("Keeper Value")] if "Keeper Value" in idx else None,
        })
    return rows


# --------------------------- Keeper slot assignment ---------------------------

# Players nobody in the league is allowed to keep, regardless of round or status.
INELIGIBLE_KEEPERS = {"Bobby Witt Jr."}


def project_likely_keepers(team, team_candidates, draft_order, player_status=None, n=4):
    """Picks a plausible default set of N keepers for a team from its
    candidate list, respecting the same rules as assign_keepers_to_slots:
    a candidate's own exact round if the team owns a pick there; if not,
    exactly one round earlier — allowed unconditionally if the team simply
    has no pick that round at all (lost pick), or only for a claimed/
    undrafted player if the round is contested by a better-value candidate
    already chosen in this same projection (a round conflict, which a
    drafted player can't resolve). Greedily takes the highest Keeper Value
    candidates first. Returns each chosen row with its original (not
    bumped) keeper_round — the real round is worked out for real by
    assign_keepers_to_slots once this projection is actually applied. Ties/
    missing values fall back to earliest round first. This only ever
    pre-fills the UI — it never touches keeper_selections itself.
    """
    from collections import defaultdict
    player_status = player_status or {}

    slots_by_team_round = defaultdict(list)
    for r in draft_order:
        if r["team"] == team:
            slots_by_team_round[r["round"]].append(r["slot"])

    def sort_key(r):
        val = r.get("keeper_value")
        return (-(val if val is not None else -999), r["keeper_round"])

    candidates = sorted(team_candidates, key=sort_key)

    used_rounds = defaultdict(int)  # round -> slots consumed by this projection so far
    chosen = []
    for r in candidates:
        if len(chosen) >= n:
            break
        rnd = r["keeper_round"]
        capacity = len(slots_by_team_round.get(rnd, []))
        if used_rounds[rnd] < capacity:
            used_rounds[rnd] += 1
            chosen.append(r)
            continue
        target = rnd - 1
        if target < 1:
            continue
        cap_earlier = len(slots_by_team_round.get(target, []))
        if used_rounds[target] >= cap_earlier:
            continue
        # A one-round move-up is available. Unconditional if the team just
        # has no pick this round at all (lost pick); otherwise this round
        # is contested (used_rounds[rnd] >= capacity > 0), which only an
        # undrafted/claimed player can resolve.
        is_lost_pick = capacity == 0
        is_claimed = player_status.get(r["player"], "Drafted") == "Claimed"
        if is_lost_pick or is_claimed:
            used_rounds[target] += 1
            chosen.append(r)
        # else: contested round, this candidate is drafted, can't resolve — skip
    return chosen


def assign_keepers_to_slots(draft_order, keeper_selections, player_status=None):
    """keeper_selections: list of {team, round, player}.

    Matches the league bylaws exactly — two distinct mechanisms, not one
    general bump rule:

    1. Round conflict (two of the SAME team's keepers land on the same
       round, and the team doesn't own enough picks that round to cover
       both): only an undrafted/waiver-claimed keeper in that pair can move
       up exactly one round to resolve it. Two drafted players in conflict
       cannot be auto-resolved — the manager has to choose, so it's a
       blocking violation (bylaws Example #1).
    2. Lost draft pick (a team's own pick for that round was traded away —
       no conflict with another keeper, the round is just empty for them):
       that keeper moves up exactly one round, regardless of drafted or
       undrafted status (bylaws' "Lost draft picks" rule).

    Neither mechanism bumps more than one round, and neither ever moves a
    player to a LATER round. Processed in round-ascending order so a
    round's own natural claim(s) always get first dibs before a later
    round's keeper can bump backward into it.

    player_status: {player_name: "Drafted"/"Claimed"/...}. Anyone not found
    defaults to "Drafted" (the stricter rule — no conflict-bump) since we
    can't safely assume waiver-eligibility for an unknown player.

    Players in INELIGIBLE_KEEPERS are never placed, however they got in.

    Returns (placements, violations). placements: {team, intended_round,
    actual_round, slot, player}. Any non-empty violations list should block
    the draft from starting.
    """
    from collections import defaultdict
    player_status = player_status or {}

    slots_by_team_round = defaultdict(list)
    for r in draft_order:
        slots_by_team_round[(r["team"], r["round"])].append(r)

    ineligible = [k for k in keeper_selections if k["player"] in INELIGIBLE_KEEPERS]
    keeper_selections = [k for k in keeper_selections if k["player"] not in INELIGIBLE_KEEPERS]

    used = set()
    placements = []
    violations = [f"{k['team']} — {k['player']}: ineligible to be kept, not applied" for k in ineligible]

    by_team_round = defaultdict(list)
    for k in keeper_selections:
        by_team_round[(k["team"], int(k["round"]))].append(k)

    def try_place(team, rnd, player):
        available = [c for c in slots_by_team_round.get((team, rnd), []) if (team, rnd, c["slot"]) not in used]
        if not available:
            return None
        slot_row = available[0]
        used.add((team, rnd, slot_row["slot"]))
        return slot_row["slot"]

    for (team, rnd) in sorted(by_team_round.keys(), key=lambda tr: tr[1]):
        keepers = by_team_round[(team, rnd)]

        if len(keepers) == 1:
            k = keepers[0]
            slot = try_place(team, rnd, k["player"])
            if slot is not None:
                placements.append({"team": team, "intended_round": rnd, "actual_round": rnd,
                                    "slot": slot, "player": k["player"]})
                continue
            # Lost draft pick — the team's own pick that round doesn't
            # exist. Anyone (drafted or claimed) moves up exactly one round.
            target = rnd - 1
            slot = try_place(team, target, k["player"]) if target >= 1 else None
            if slot is not None:
                placements.append({"team": team, "intended_round": rnd, "actual_round": target,
                                    "slot": slot, "player": k["player"]})
            else:
                violations.append(
                    f"{team} — {k['player']}: Round {rnd} — {team} has no pick that round "
                    f"(traded away), and Round {target} isn't available either"
                )
            continue

        # Round conflict: 2+ of this team's own keepers landing on the same
        # round. Natural slots go to drafted keepers first (no flexibility);
        # claimed keepers absorb any overflow via a one-round bump.
        drafted = [k for k in keepers if player_status.get(k["player"], "Drafted") != "Claimed"]
        claimed = [k for k in keepers if player_status.get(k["player"], "Drafted") == "Claimed"]

        placed_drafted = []
        for k in drafted:
            slot = try_place(team, rnd, k["player"])
            if slot is not None:
                placements.append({"team": team, "intended_round": rnd, "actual_round": rnd,
                                    "slot": slot, "player": k["player"]})
                placed_drafted.append(k)
        unplaced_drafted = [k for k in drafted if k not in placed_drafted]
        if unplaced_drafted:
            names = ", ".join(k["player"] for k in unplaced_drafted)
            violations.append(
                f"{team} — Round {rnd}: drafted keeper(s) ({names}) couldn't get a pick that "
                f"round — drafted players can't move to resolve a conflict, so pick which one "
                f"is actually right"
            )

        for k in claimed:
            slot = try_place(team, rnd, k["player"])
            if slot is not None:
                placements.append({"team": team, "intended_round": rnd, "actual_round": rnd,
                                    "slot": slot, "player": k["player"]})
                continue
            # Round conflict, resolved by moving this undrafted keeper up one round.
            target = rnd - 1
            slot = try_place(team, target, k["player"]) if target >= 1 else None
            if slot is not None:
                placements.append({"team": team, "intended_round": rnd, "actual_round": target,
                                    "slot": slot, "player": k["player"]})
            else:
                violations.append(
                    f"{team} — {k['player']}: Round {rnd} conflict, and Round {target} "
                    f"(the one-round move-up) isn't available either"
                )

    return placements, violations


def _legacy_assign_keepers_to_slots(draft_order, keeper_selections):
    """Old behavior (bump repeatedly to any earlier open round, regardless
    of drafted/claimed status) — kept only for reference, no longer used.
    """
    from collections import defaultdict
    slots_by_team_round = defaultdict(list)
    for r in draft_order:
        slots_by_team_round[(r["team"], r["round"])].append(r)

    used = set()
    placements = []
    bump_log = []
    for k in sorted(keeper_selections, key=lambda x: x["round"]):
        team, rnd, player = k["team"], int(k["round"]), k["player"]
        target = rnd
        placed = None
        while target >= 1:
            free = [c for c in slots_by_team_round.get((team, target), [])
                    if (team, target, c["slot"]) not in used]
            if free:
                placed = free[0]
                used.add((team, target, placed["slot"]))
                break
            target -= 1
        if placed is None:
            bump_log.append(f"{team} — {player}: NO OPEN SLOT FOUND (check draft order)")
            continue
        if target != rnd:
            bump_log.append(f"{team} — {player}: bumped from Round {rnd} to Round {target}")
        placements.append({
            "team": team, "intended_round": rnd, "actual_round": target,
            "slot": placed["slot"], "player": player
        })
    return placements, bump_log


def build_pick_sequence(draft_order, keeper_placements):
    keeper_map = {(k["team"], k["actual_round"], k["slot"]): k["player"] for k in keeper_placements}
    by_round = {}
    for r in draft_order:
        by_round.setdefault(r["round"], {})[r["slot"]] = r["team"]

    sequence = []
    overall = 0
    for rnd in range(1, 26):
        slots = by_round.get(rnd, {})
        order = range(1, 13) if rnd % 2 == 1 else range(12, 0, -1)
        for slot in order:
            if slot not in slots:
                continue
            overall += 1
            team = slots[slot]
            kp = keeper_map.get((team, rnd, slot))
            sequence.append({
                "overall": overall, "round": rnd, "slot": slot, "team": team,
                "is_keeper": kp is not None, "keeper_player": kp or ""
            })
    return sequence
