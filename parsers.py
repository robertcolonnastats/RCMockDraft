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

def assign_keepers_to_slots(draft_order, keeper_selections):
    """keeper_selections: list of {team, round, player}. Places each into that
    team's earliest unused slot for that round; if none (team owns zero picks
    that round, or it's already taken by another keeper), bumps to the next
    earlier round the team owns a free slot in. Returns list of
    {team, intended_round, actual_round, slot, player} plus a bump log.
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
