import csv
import io
import json
import random
from pathlib import Path

import streamlit as st

from teams import TEAMS, ROSTER_SLOTS, SCORING_CATEGORIES
import roster_logic as RL
import parsers as P

DIR = Path(__file__).parent
st.set_page_config(page_title="All About the Fundies — Mock Draft", page_icon=str(DIR / "logo.png"), layout="wide")

logo_col, title_col = st.columns([1, 6])
with logo_col:
    st.image(str(DIR / "logo.png"), width=90)
with title_col:
    st.markdown("## All About the Fundies — 2027 Custom Mock Draft")

MANAGERS = {t["team"]: t["manager"] for t in TEAMS}
ALL_TEAMS = sorted(MANAGERS.keys())


def team_label(t):
    return f"{t} ({MANAGERS.get(t, '?')})"


# ----------------------------- Bundled defaults -----------------------------

@st.cache_data
def default_draft_order():
    rows = list(csv.DictReader(open(DIR / "draft_order_2027.csv", encoding="utf-8")))
    for r in rows:
        r["round"] = int(r["round"])
        r["slot"] = int(r["slot"])
    return rows


@st.cache_data
def default_adp_pool():
    rows = list(csv.DictReader(open(DIR / "adp.csv", encoding="utf-8")))
    for r in rows:
        r["adp"] = float(r["adp"])
        r["adp_round"] = int(r["adp_round"])
    return rows


@st.cache_data
def default_full_player_list():
    return P.parse_full_player_list(open(DIR / "all_players.csv", "rb").read())


@st.cache_data
def default_merged_pool():
    return P.merge_full_pool(default_adp_pool(), default_full_player_list())


@st.cache_data
def default_keeper_workbook_rows():
    return P.parse_keeper_workbook(open(DIR / "keeper_values.xlsx", "rb").read())


@st.cache_data
def default_keeper_selections():
    rows = list(csv.DictReader(open(DIR / "keepers_resolved.csv", encoding="utf-8")))
    return [{"team": r["team"], "round": int(r["round"]), "player": r["player"]} for r in rows]


@st.cache_data
def default_injured_names():
    return P.parse_injury_csvs(
        open(DIR / "injured_taken.csv", "rb").read(),
        open(DIR / "injured_available.csv", "rb").read(),
    )


@st.cache_data
def default_milb_names():
    return P.parse_milb_csvs(
        open(DIR / "minors_taken.csv", "rb").read(),
        open(DIR / "minors_available.csv", "rb").read(),
    )


@st.cache_data
def load_trends():
    return json.load(open(DIR / "team_trends.json", encoding="utf-8"))


TRENDS = load_trends()

POS_MAP = {"LF": "OF", "CF": "OF", "RF": "OF", "OF": "OF", "DH": "UT", "INF": "UT", "UT": "UT",
           "SP": "SP", "RP": "RP", "P": "SP", "C": "C", "1B": "1B", "2B": "2B", "3B": "3B", "SS": "SS"}


def primary_position(positions_str):
    toks = [t.strip() for t in positions_str.split(",") if t.strip()]
    specific = [POS_MAP.get(t) for t in toks if POS_MAP.get(t) and POS_MAP.get(t) != "UT"]
    if specific:
        return specific[0]
    for t in toks:
        if POS_MAP.get(t):
            return POS_MAP[t]
    return "UT"


def round_bucket(r):
    if r <= 5: return "1-5"
    if r <= 10: return "6-10"
    if r <= 15: return "11-15"
    if r <= 20: return "16-20"
    return "21-25"


# Which roster-slot labels count toward "need" for a given position category.
# UT/RES are deliberately excluded — they're generic overflow, not a signal
# that a position is actually needed.
NEED_LABELS = {
    "C": ["C"], "1B": ["1B", "INF"], "2B": ["2B", "INF"], "3B": ["3B", "INF"], "SS": ["SS", "INF"],
    "OF": ["OF"], "SP": ["SP", "P"], "RP": ["RP", "P"],
}
CAPACITY_BY_LABEL = {}
for _sid, _label in ROSTER_SLOTS:
    CAPACITY_BY_LABEL[_label] = CAPACITY_BY_LABEL.get(_label, 0) + 1


def need_ratio(team_roster, pos_category):
    labels = NEED_LABELS.get(pos_category)
    if not labels:
        return 1.0
    total = sum(CAPACITY_BY_LABEL.get(l, 0) for l in labels)
    if total == 0:
        return 0.5
    open_count = sum(
        1 for sid, label in ROSTER_SLOTS
        if label in labels and team_roster.get(sid) is None
    )
    return max(open_count / total, 0.15)  # floor so a full position isn't literally impossible, just discouraged


def open_active_count(team_roster):
    return sum(1 for sid, label in ROSTER_SLOTS if label not in ("IL", "MiLB") and team_roster.get(sid) is None)


def open_count_for_label(team_roster, target_label):
    return sum(1 for sid, label in ROSTER_SLOTS if label == target_label and team_roster.get(sid) is None)


# A player is only "worth" stashing on IL if they're a real, established
# name — not just anyone hurt. MiLB stashing has no such gate: the whole
# point of a prospect slot is speculative upside regardless of team depth.
# Real historical skew (2024-2026 drafts): late rounds see a sharp jump in
# picks age 24-or-younger — 8% in rounds 1-15, 19% in 16-20, 32% in 21-25 —
# reflecting managers speculating on future keeper stashes rather than
# drafting the best immediately-useful veteran. Modeled as a scoring bonus
# for young players, scaled to roughly match the observed jump.
YOUTH_STASH_BONUS = {"16-20": 2.0, "21-25": 3.5}
YOUTH_AGE_CUTOFF = 24


def youth_bonus(age, bucket):
    mult = YOUTH_STASH_BONUS.get(bucket)
    if mult and age is not None and age <= YOUTH_AGE_CUTOFF:
        return mult
    return 1.0


IL_QUALITY_ADP_THRESHOLD = 150
# Teams with fewer total picks (due to trades) can't afford to burn one on
# a speculative IL stash — only teams with real pick depth bother.
IL_MIN_TEAM_PICKS_TO_BOTHER = 24


def auto_pick(team, round_num, available):
    bucket = round_bucket(round_num)
    weights = TRENDS["teams"].get(team, {}).get(bucket) or TRENDS["league"].get(bucket, {})
    team_roster = st.session_state.team_rosters[team]

    if open_active_count(team_roster) <= 0:
        # Every active/bench spot is full — the only slots left are IL/MiLB.
        il_open = open_count_for_label(team_roster, "IL")
        milb_open = open_count_for_label(team_roster, "MiLB")
        team_total_picks = st.session_state.team_pick_counts.get(team, 0)
        consider_il = il_open > 0 and team_total_picks >= IL_MIN_TEAM_PICKS_TO_BOTHER

        candidates = []
        if consider_il:
            candidates += [
                p for p in available
                if is_injured(p["player"], p["mlb_team"]) and p["adp"] <= IL_QUALITY_ADP_THRESHOLD
            ]
        if milb_open > 0:
            candidates += [p for p in available if is_milb(p["player"], p["mlb_team"])]
        # de-dupe (a player could in theory match both)
        seen = set()
        deduped = []
        for p in candidates:
            if p["player"] not in seen:
                seen.add(p["player"])
                deduped.append(p)
        deduped.sort(key=lambda p: p["adp"])
        window = deduped[:15] if deduped else available[:15]
    else:
        window = available[:15]

    scored = []
    for rank, p in enumerate(window):
        pos = primary_position(p["positions"])
        pos_w = weights.get(pos, 0.03)
        need_w = need_ratio(team_roster, pos)
        adp_w = 1.0 / (rank + 1)
        age_w = youth_bonus(p.get("age"), bucket)
        scored.append(pos_w * need_w * adp_w * age_w + 0.0001)
    total = sum(scored)
    probs = [s / total for s in scored]
    return random.choices(window, weights=probs, k=1)[0]


def lookup_player_meta(name):
    """Best-effort (positions, mlb_team) lookup for a player we don't have a
    full pool row for (i.e. keepers) — checks the ADP pool, then the keeper
    workbook if one's been uploaded."""
    for p in st.session_state.adp_all:
        if p["player"] == name:
            return p["positions"], p["mlb_team"]
    if st.session_state.keeper_workbook_rows:
        for r in st.session_state.keeper_workbook_rows:
            if r["player"] == name:
                return r.get("position") or "", r.get("mlb_team")
    return "", None


# ------------------------------- Session init -------------------------------

def init_state():
    ss = st.session_state
    ss.setdefault("draft_order", default_draft_order())
    ss.setdefault("adp_ranked", default_adp_pool())
    ss.setdefault("full_player_rows", default_full_player_list())
    ss.setdefault("adp_all", None)
    ss.setdefault("injured_names", default_injured_names())
    ss.setdefault("milb_names", default_milb_names())
    ss.setdefault("keeper_workbook_rows", default_keeper_workbook_rows())
    ss.setdefault("keeper_selections", default_keeper_selections())
    ss.setdefault("pick_sequence", None)
    ss.setdefault("user_team", None)
    ss.setdefault("idx", 0)
    ss.setdefault("board", [])
    ss.setdefault("pool", None)
    ss.setdefault("team_rosters", None)
    ss.setdefault("team_overflow", None)


init_state()
if st.session_state.adp_all is None:
    st.session_state.adp_all = P.merge_full_pool(st.session_state.adp_ranked, st.session_state.full_player_rows)


def file_is_new(upload_key, file_bytes):
    """Streamlit keeps an uploaded file's value across every rerun until the
    user removes it, so any upload handler that just checks 'is not None'
    re-processes (and re-resets the draft) on every unrelated interaction
    elsewhere in the app. Guard every uploader with this instead."""
    h = hash(file_bytes)
    if st.session_state.get(f"_filehash_{upload_key}") == h:
        return False
    st.session_state[f"_filehash_{upload_key}"] = h
    return True


def rebuild_pool():
    st.session_state.adp_all = P.merge_full_pool(st.session_state.adp_ranked, st.session_state.full_player_rows)


def rebuild_pick_sequence():
    placements, bumps = P.assign_keepers_to_slots(st.session_state.draft_order, st.session_state.keeper_selections)
    st.session_state.keeper_placements = placements
    st.session_state.keeper_bump_log = bumps
    st.session_state.pick_sequence = P.build_pick_sequence(st.session_state.draft_order, placements)
    counts = {}
    for p in st.session_state.pick_sequence:
        counts[p["team"]] = counts.get(p["team"], 0) + 1
    st.session_state.team_pick_counts = counts


def is_injured(name, team=None):
    return _match_flag(name, team, st.session_state.injured_names)


def is_milb(name, team=None):
    return _match_flag(name, team, st.session_state.milb_names)


def _match_flag(name, team, entries):
    """entries is a set of (name, team) tuples. Requires team match when we
    have one (to avoid false positives like two different 'Jose Ramirez's);
    falls back to name-only when team is unknown (e.g. keeper picks) but only
    if that name is unambiguous in the source list.
    """
    if team:
        if (name, team) in entries:
            return True
        return False
    candidates = [e for e in entries if e[0] == name]
    return len(candidates) == 1


def slot_a_pick(team, player, mlb_team, positions_str):
    roster = st.session_state.team_rosters[team]
    open_slots = [(sid, label) for sid, label in ROSTER_SLOTS if roster[sid] is None]
    injured = is_injured(player, mlb_team)
    milb = is_milb(player, mlb_team)
    slot_id = RL.choose_best_slot(open_slots, positions_str, injured=injured, milb=milb)
    if slot_id is None:
        st.session_state.team_overflow[team].append({"player": player, "mlb_team": mlb_team, "positions": positions_str})
    else:
        roster[slot_id] = {"player": player, "mlb_team": mlb_team}


def populate_keeper_rosters():
    """Slot every keeper onto its team's roster immediately — keepers are
    locked in before the draft even starts, so they should be visible on
    Team Rosters right away, not only once the draft sequence reaches them.
    """
    for k in st.session_state.keeper_placements:
        positions_str, mlb_team = lookup_player_meta(k["player"])
        slot_a_pick(k["team"], k["player"], mlb_team, positions_str)


def reset_draft(clear_team=False):
    st.session_state.idx = 0
    st.session_state.board = []
    st.session_state.team_rosters = {team: {sid: None for sid, _ in ROSTER_SLOTS} for team in ALL_TEAMS}
    st.session_state.team_overflow = {team: [] for team in ALL_TEAMS}
    kept_names = {p["player"] for p in st.session_state.keeper_selections}
    st.session_state.pool = [p for p in st.session_state.adp_all if p["player"] not in kept_names]
    if clear_team:
        st.session_state.user_team = None
    rebuild_pick_sequence()
    populate_keeper_rosters()


if st.session_state.pool is None:
    reset_draft()


def remove_from_pool(name):
    st.session_state.pool = [p for p in st.session_state.pool if p["player"] != name]


def advance_auto_and_keepers():
    seq = st.session_state.pick_sequence
    while st.session_state.idx < len(seq):
        pick = seq[st.session_state.idx]
        if pick["is_keeper"]:
            # Already slotted onto the roster in populate_keeper_rosters() —
            # just log it to the draft board and move on.
            positions_str, mlb_team = lookup_player_meta(pick["keeper_player"])
            st.session_state.board.append({**pick, "player": pick["keeper_player"], "mlb_team": mlb_team, "positions": positions_str, "adp": None, "source": "keeper"})
            st.session_state.idx += 1
            continue
        if pick["team"] == st.session_state.user_team:
            return
        if not st.session_state.pool:
            break
        chosen = auto_pick(pick["team"], pick["round"], st.session_state.pool)
        st.session_state.board.append({**pick, "player": chosen["player"], "mlb_team": chosen["mlb_team"], "positions": chosen["positions"], "adp": chosen["adp"], "source": "auto"})
        slot_a_pick(pick["team"], chosen["player"], chosen["mlb_team"], chosen["positions"])
        remove_from_pool(chosen["player"])
        st.session_state.idx += 1


# ---------------------------------- Tabs -----------------------------------

tab_draft, tab_keepers, tab_docs = st.tabs(["Draft", "⭐ Keepers", "📄 League Documents"])

# ------------------------------- Keepers tab --------------------------------

with tab_keepers:
    st.header("Set Keepers")
    st.write("Each team gets exactly 4 keeper slots. Every dropdown is limited to that team's own "
             "rostered players (from the Keeper Values workbook) and shows the round they'd be kept "
             "in. Swap any slot to a different player on that same roster.")

    kf = st.file_uploader("Replace Keeper Values workbook (.xlsx)", type=["xlsx"], key="kw_upload")
    if kf is not None and file_is_new("kw_upload", kf.getvalue()):
        try:
            st.session_state.keeper_workbook_rows = P.parse_keeper_workbook(kf.getvalue())
            st.success(f"Loaded {len(st.session_state.keeper_workbook_rows)} players with computed keeper rounds.")
        except Exception as e:
            st.error(f"Couldn't read that workbook: {e}")

    st.divider()
    st.caption("Already decided your keepers before? Upload a previously-saved picks file to "
               "restore them instead of re-picking from the dropdowns below.")
    saved_kf = st.file_uploader("Saved keeper picks (CSV)", type=["csv"], key="saved_keepers_upload")
    if saved_kf is not None and file_is_new("saved_keepers_upload", saved_kf.getvalue()):
        try:
            loaded = P.parse_saved_keeper_picks(saved_kf.getvalue())
            if not loaded:
                st.error("That file didn't have any valid team/round/player rows.")
            else:
                st.session_state.keeper_selections = loaded
                reset_draft(clear_team=False)
                st.success(f"Restored {len(loaded)} saved keeper picks — draft board reset.")
                st.rerun()
        except Exception as e:
            st.error(f"Couldn't read that file: {e}")

    rows = st.session_state.keeper_workbook_rows
    if not rows:
        st.error("No keeper workbook loaded — upload one above.")
        rows = []

    current = {}
    for k in st.session_state.keeper_selections:
        current.setdefault(k["team"], []).append(k)

    new_selection = []
    any_duplicates = False
    for team in ALL_TEAMS:
        team_rows = sorted([r for r in rows if r["team"] == team], key=lambda r: r["keeper_round"])
        if not team_rows:
            continue

        def opt_label(r):
            return f"{r['player']} — Round {r['keeper_round']} ({r['position']}, {r['status']})"

        options = [opt_label(r) for r in team_rows]
        team_current = sorted(current.get(team, []), key=lambda k: k["round"])

        with st.expander(f"{team_label(team)}"):
            chosen_players = []
            used_defaults = set()
            cols = st.columns(4)
            for i in range(4):
                default_idx = None
                if i < len(team_current):
                    match = next((j for j, r in enumerate(team_rows) if r["player"] == team_current[i]["player"]), None)
                    if match is not None and match not in used_defaults:
                        default_idx = match
                if default_idx is None:
                    default_idx = next((j for j in range(len(team_rows)) if j not in used_defaults), 0)
                used_defaults.add(default_idx)
                with cols[i]:
                    sel = st.selectbox(f"Keeper {i+1}", options, index=default_idx, key=f"keeper_{team}_{i}")
                chosen_row = team_rows[options.index(sel)]
                chosen_players.append(chosen_row)
                new_selection.append({"team": team, "round": chosen_row["keeper_round"], "player": chosen_row["player"]})

            names = [p["player"] for p in chosen_players]
            if len(set(names)) != len(names):
                any_duplicates = True
                st.warning("You've selected the same player in more than one slot for this team.")

    st.divider()
    col_apply, col_export = st.columns(2)
    with col_apply:
        if st.button("Apply Keeper Selections", type="primary", disabled=any_duplicates):
            st.session_state.keeper_selections = new_selection
            reset_draft(clear_team=False)
            st.success("Keepers updated — draft board reset.")
            st.rerun()
    with col_export:
        export_buf = io.StringIO()
        w = csv.writer(export_buf)
        w.writerow(["team", "round", "player"])
        for k in new_selection:
            w.writerow([k["team"], k["round"], k["player"]])
        st.download_button(
            "⬇️ Save these keeper picks (CSV)", export_buf.getvalue(),
            file_name="fundies_keeper_picks.csv", mime="text/csv"
        )
    if any_duplicates:
        st.caption("Fix the duplicate player(s) above before applying.")

    with st.expander("Current keeper slot assignments"):
        if "keeper_placements" in st.session_state:
            for k in sorted(st.session_state.keeper_placements, key=lambda x: (x["team"], x["actual_round"])):
                note = "" if k["actual_round"] == k["intended_round"] else f"  ⚠️ bumped from Round {k['intended_round']}"
                st.write(f"{k['team']} — Round {k['actual_round']}, Slot {k['slot']} — {k['player']}{note}")
        if st.session_state.get("keeper_bump_log"):
            st.warning("\n".join(st.session_state.keeper_bump_log))


# ---------------------------- League Documents tab ---------------------------

with tab_docs:
    st.header("Replace League Documents")
    st.caption("Uploading any of these regenerates the underlying data and restarts the current mock draft.")

    c1, c2 = st.columns(2)
    with c1:
        do_file = st.file_uploader("Draft order (Fantrax 'By Round' PDF export)", type=["pdf"], key="doc_draftorder")
        if do_file is not None and file_is_new("doc_draftorder", do_file.getvalue()):
            try:
                st.session_state.draft_order = P.parse_draft_order_pdf(do_file.getvalue())
                st.success("Draft order updated.")
                reset_draft(clear_team=True)
            except Exception as e:
                st.error(str(e))

        adp_file = st.file_uploader("ADP rankings (CSV)", type=["csv"], key="doc_adp")
        if adp_file is not None and file_is_new("doc_adp", adp_file.getvalue()):
            try:
                st.session_state.adp_ranked = P.parse_adp_csv(adp_file.getvalue())
                rebuild_pool()
                st.success(f"ADP updated — {len(st.session_state.adp_ranked)} ranked players "
                           f"(pool now {len(st.session_state.adp_all)} total with the full player list).")
                reset_draft(clear_team=True)
            except Exception as e:
                st.error(str(e))

        full_list_file = st.file_uploader("All Fantrax players (CSV) — full league player pool", type=["csv"], key="doc_full_players")
        if full_list_file is not None and file_is_new("doc_full_players", full_list_file.getvalue()):
            try:
                st.session_state.full_player_rows = P.parse_full_player_list(full_list_file.getvalue())
                rebuild_pool()
                st.success(f"Full player list updated — {len(st.session_state.full_player_rows)} players "
                           f"(pool now {len(st.session_state.adp_all)} total).")
                reset_draft(clear_team=True)
            except Exception as e:
                st.error(str(e))

        roster_file = st.file_uploader("Current rosters (CSV)", type=["csv"], key="doc_rosters")
        if roster_file is not None and file_is_new("doc_rosters", roster_file.getvalue()):
            try:
                st.session_state.rosters = P.parse_rosters_csv(roster_file.getvalue())
                st.success(f"Rosters updated — {len(st.session_state.rosters)} players.")
            except Exception as e:
                st.error(str(e))

    with c2:
        inj_taken = st.file_uploader("Injured — rostered (CSV)", type=["csv"], key="doc_inj_taken")
        inj_avail = st.file_uploader("Injured — free agents (CSV)", type=["csv"], key="doc_inj_avail")
        inj_combined = (inj_taken.getvalue() if inj_taken else b"") + b"|" + (inj_avail.getvalue() if inj_avail else b"")
        if (inj_taken is not None or inj_avail is not None) and file_is_new("doc_injuries", inj_combined):
            try:
                names = P.parse_injury_csvs(
                    inj_taken.getvalue() if inj_taken else None,
                    inj_avail.getvalue() if inj_avail else None,
                )
                st.session_state.injured_names = names
                st.success(f"Injury list updated — {len(names)} players flagged.")
            except Exception as e:
                st.error(str(e))

        milb_taken = st.file_uploader("MiLB-eligible — rostered (CSV)", type=["csv"], key="doc_milb_taken")
        milb_avail = st.file_uploader("MiLB-eligible — available (CSV)", type=["csv"], key="doc_milb_avail")
        milb_combined = (milb_taken.getvalue() if milb_taken else b"") + b"|" + (milb_avail.getvalue() if milb_avail else b"")
        if (milb_taken is not None or milb_avail is not None) and file_is_new("doc_milb", milb_combined):
            try:
                names = P.parse_milb_csvs(
                    milb_taken.getvalue() if milb_taken else None,
                    milb_avail.getvalue() if milb_avail else None,
                )
                st.session_state.milb_names = names
                st.success(f"MiLB list updated — {len(names)} players.")
            except Exception as e:
                st.error(str(e))

    st.divider()
    st.caption(
        "Scoring categories on file (equal weight, all 1.0): "
        + "Hitting — " + ", ".join(SCORING_CATEGORIES["Hitting"])
        + " | Pitching — " + ", ".join(SCORING_CATEGORIES["Pitching"])
        + ". Team min/max requirements haven't been provided yet, so category-aware "
          "draft logic isn't active — auto-picks use position tendency + ADP only."
    )


# -------------------------------- Draft tab ---------------------------------

with tab_draft:
    if st.session_state.user_team is not None:
        advance_auto_and_keepers()

    left, right = st.columns([2, 1])

    with right:
        if st.session_state.user_team is None:
            st.write("Pick the team you want to control. Every other team auto-drafts using that "
                     "manager's real draft history, layered on ADP. Keepers are already locked in "
                     "on the rosters to the left — check them out before you start.")
            choice = st.selectbox("Your team", ALL_TEAMS, format_func=team_label)
            if st.button("Start Draft", type="primary"):
                st.session_state.user_team = choice
                st.rerun()
        else:
            st.subheader("On the Clock")
            seq = st.session_state.pick_sequence
            if st.session_state.idx >= len(seq):
                st.success("Draft complete!")
            else:
                pick = seq[st.session_state.idx]
                pick_in_round = pick["overall"] - (pick["round"] - 1) * 12
                st.markdown(f"**Round {pick['round']}, Pick {pick_in_round}** (overall #{pick['overall']})")
                st.markdown(f"**{team_label(pick['team'])}**")

                if pick["team"] == st.session_state.user_team:
                    pool = st.session_state.pool
                    pos_options = ["All"] + sorted({primary_position(p["positions"]) for p in pool})
                    pos_filter = st.selectbox("Filter by position", pos_options)
                    status_filter = st.selectbox("Filter by status", ["All", "Injured only", "Minor league eligible only"])
                    search = st.text_input("Search player")

                    filtered = pool
                    if pos_filter != "All":
                        filtered = [p for p in filtered if primary_position(p["positions"]) == pos_filter]
                    if status_filter == "Injured only":
                        filtered = [p for p in filtered if is_injured(p["player"], p["mlb_team"])]
                    elif status_filter == "Minor league eligible only":
                        filtered = [p for p in filtered if is_milb(p["player"], p["mlb_team"])]
                    if search:
                        filtered = [p for p in filtered if search.lower() in p["player"].lower()]
                    filtered = filtered[:40]

                    def opt_label(p):
                        flag = ("🚩" if is_injured(p["player"], p["mlb_team"]) else "") + ("🟢" if is_milb(p["player"], p["mlb_team"]) else "")
                        flag = f" {flag}" if flag else ""
                        return f"{p['player']}{flag} — {p['positions']} — ADP {p['adp']:.1f}"

                    if filtered:
                        sel = st.selectbox("Available players", [opt_label(p) for p in filtered])
                        sel_player = filtered[[opt_label(p) for p in filtered].index(sel)]

                        open_slots = [sid for sid, label in ROSTER_SLOTS if st.session_state.team_rosters[st.session_state.user_team][sid] is None]
                        slot_labels = dict(ROSTER_SLOTS)
                        p_injured = is_injured(sel_player["player"], sel_player["mlb_team"])
                        p_milb = is_milb(sel_player["player"], sel_player["mlb_team"])
                        eligible_slots = [
                            sid for sid in open_slots
                            if RL.can_place(slot_labels[sid], sel_player["positions"], injured=p_injured, milb=p_milb)
                        ]
                        if not eligible_slots:
                            st.warning("No open roster slot fits this player.")
                        else:
                            slot_choice = st.selectbox("Roster slot", eligible_slots, format_func=lambda s: f"{s} ({slot_labels[s]})")
                            if st.button("Draft this player", type="primary"):
                                st.session_state.board.append({**pick, "player": sel_player["player"], "mlb_team": sel_player["mlb_team"], "positions": sel_player["positions"], "adp": sel_player["adp"], "source": "user"})
                                st.session_state.team_rosters[st.session_state.user_team][slot_choice] = {"player": sel_player["player"], "mlb_team": sel_player["mlb_team"]}
                                remove_from_pool(sel_player["player"])
                                st.session_state.idx += 1
                                st.rerun()
                    else:
                        st.info("No players match your filters.")

        st.divider()
        if st.button("Reset Draft"):
            reset_draft(clear_team=True)
            st.rerun()

        st.divider()
        st.caption("Export")
        if st.session_state.board:
            board_buf = io.StringIO()
            w = csv.writer(board_buf)
            w.writerow(["overall", "round", "slot", "team", "manager", "player", "mlb_team", "positions", "source"])
            for b in sorted(st.session_state.board, key=lambda x: x["overall"]):
                w.writerow([b["overall"], b["round"], b["slot"], b["team"], MANAGERS.get(b["team"], ""),
                            b["player"], b.get("mlb_team", ""), b.get("positions", ""), b["source"]])
            st.download_button("⬇️ Draft board (CSV)", board_buf.getvalue(), file_name="fundies_2027_mock_draft.csv", mime="text/csv")
        else:
            st.caption("No picks made yet.")

        roster_buf = io.StringIO()
        w = csv.writer(roster_buf)
        w.writerow(["team", "manager", "slot_id", "slot_label", "player", "mlb_team"])
        for team in ALL_TEAMS:
            for sid, label in ROSTER_SLOTS:
                entry = st.session_state.team_rosters[team][sid]
                w.writerow([team, MANAGERS.get(team, ""), sid, label,
                            entry["player"] if entry else "", entry["mlb_team"] if entry else ""])
            for o in st.session_state.team_overflow.get(team, []):
                w.writerow([team, MANAGERS.get(team, ""), "OVERFLOW", "OVERFLOW", o["player"], o["mlb_team"]])
        st.download_button("⬇️ All team rosters (CSV)", roster_buf.getvalue(), file_name="fundies_2027_rosters.csv", mime="text/csv")

    with left:
        tabs2 = st.tabs(["Draft Board", "Team Rosters"])
        with tabs2[0]:
            if not st.session_state.board:
                st.info("Draft hasn't started yet — keepers are already on the rosters though, check the Team Rosters tab.")
            else:
                for r in sorted(set(b["round"] for b in st.session_state.board), reverse=True):
                    st.markdown(f"**Round {r}**")
                    row = sorted([b for b in st.session_state.board if b["round"] == r], key=lambda b: b["overall"])
                    for i, b in enumerate(row, start=1):
                        tag = " ⭐ KEEPER" if b["source"] == "keeper" else (" 🧑 YOU" if b["source"] == "user" else "")
                        flag = ("🚩" if is_injured(b["player"], b.get("mlb_team")) else "") + ("🟢" if is_milb(b["player"], b.get("mlb_team")) else "")
                        flag = f" {flag}" if flag else ""
                        st.write(f"{i}. **{b['team']}** — {b['player']}{flag}{tag}")

        with tabs2[1]:
            default_idx = ALL_TEAMS.index(st.session_state.user_team) if st.session_state.user_team else 0
            view_team = st.selectbox(
                "View roster for", ALL_TEAMS, index=default_idx,
                format_func=team_label, key="roster_view_team"
            )
            roster = st.session_state.team_rosters[view_team]
            for sid, label in ROSTER_SLOTS:
                entry = roster[sid]
                p = entry["player"] if entry else None
                team = entry["mlb_team"] if entry else None
                flag = ("🚩" if p and is_injured(p, team) else "") + ("🟢" if p and is_milb(p, team) else "")
                flag = f" {flag}" if flag else ""
                st.write(f"**{label}**  ({sid}): {p if p else '—'}{flag}")

            overflow = st.session_state.team_overflow.get(view_team, [])
            if overflow:
                with st.expander(f"Roster-full overflow ({len(overflow)}) — drafted but no eligible slot open"):
                    for o in overflow:
                        flag = ("🚩" if is_injured(o["player"], o["mlb_team"]) else "") + ("🟢" if is_milb(o["player"], o["mlb_team"]) else "")
                        flag = f" {flag}" if flag else ""
                        st.write(f"{o['player']}{flag} — {o['positions']}")
