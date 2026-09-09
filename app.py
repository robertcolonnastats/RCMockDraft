import json
import random
import csv
from pathlib import Path

import streamlit as st

DATA_DIR = Path(__file__).parent

st.set_page_config(page_title="All About the Fundies — Mock Draft", layout="wide")

# ----------------------------- Data loading -----------------------------

@st.cache_data
def load_sequence():
    return json.load(open(DATA_DIR / "pick_sequence.json", encoding="utf-8"))

@st.cache_data
def load_pool():
    rows = list(csv.DictReader(open(DATA_DIR / "adp_pool.csv", encoding="utf-8")))
    for r in rows:
        r["adp"] = float(r["adp"])
        r["adp_round"] = int(r["adp_round"])
    rows.sort(key=lambda r: r["adp"])
    return rows

@st.cache_data
def load_trends():
    return json.load(open(DATA_DIR / "team_trends.json", encoding="utf-8"))

@st.cache_data
def load_managers():
    import sys
    sys.path.insert(0, str(DATA_DIR))
    from teams import TEAMS
    return {t["team"]: t["manager"] for t in TEAMS}

SEQUENCE = load_sequence()
TRENDS = load_trends()
MANAGERS = load_managers()
ALL_TEAMS = sorted(MANAGERS.keys())

POS_MAP = {
    "LF": "OF", "CF": "OF", "RF": "OF", "OF": "OF",
    "DH": "UT", "INF": "UT", "UT": "UT",
    "SP": "SP", "RP": "RP", "P": "SP",
    "C": "C", "1B": "1B", "2B": "2B", "3B": "3B", "SS": "SS",
}


def primary_position(positions_str):
    tokens = [t.strip() for t in positions_str.split(",") if t.strip()]
    specific = [POS_MAP.get(t) for t in tokens if POS_MAP.get(t) and POS_MAP.get(t) != "UT"]
    if specific:
        return specific[0]
    for t in tokens:
        if POS_MAP.get(t):
            return POS_MAP[t]
    return "UT"


def round_bucket(round_num):
    if round_num <= 5:
        return "1-5"
    if round_num <= 10:
        return "6-10"
    if round_num <= 15:
        return "11-15"
    if round_num <= 20:
        return "16-20"
    return "21-25"


def auto_pick(team, round_num, available):
    """Pick a player for an auto-drafted team using ADP proximity + that
    team's historical positional tendencies for this stretch of the draft."""
    bucket = round_bucket(round_num)
    weights = TRENDS["teams"].get(team, {}).get(bucket) or TRENDS["league"].get(bucket, {})
    window = available[:15] if len(available) > 15 else available[:]
    scored = []
    for rank, p in enumerate(window):
        pos = primary_position(p["positions"])
        pos_w = weights.get(pos, 0.03)
        adp_w = 1.0 / (rank + 1)
        scored.append(pos_w * adp_w + 0.0001)
    total = sum(scored)
    probs = [s / total for s in scored]
    choice = random.choices(window, weights=probs, k=1)[0]
    return choice


# ----------------------------- Session state -----------------------------

if "user_team" not in st.session_state:
    st.session_state.user_team = None
if "idx" not in st.session_state:
    st.session_state.idx = 0
if "board" not in st.session_state:
    st.session_state.board = []
if "pool" not in st.session_state:
    st.session_state.pool = load_pool()


def team_label(t):
    return f"{t} ({MANAGERS.get(t, '?')})"


def remove_from_pool(player_name):
    st.session_state.pool = [p for p in st.session_state.pool if p["player"] != player_name]


def advance_auto_and_keepers():
    """Process picks until it's the user's turn to make a live pick, or the draft ends."""
    while st.session_state.idx < len(SEQUENCE):
        pick = SEQUENCE[st.session_state.idx]
        if pick["is_keeper"]:
            st.session_state.board.append({
                **pick, "player": pick["keeper_player"], "pos": "", "adp": None, "source": "keeper"
            })
            remove_from_pool(pick["keeper_player"])
            st.session_state.idx += 1
            continue
        if pick["team"] == st.session_state.user_team:
            return  # pause here for user input
        # auto-draft
        if not st.session_state.pool:
            break
        chosen = auto_pick(pick["team"], pick["round"], st.session_state.pool)
        st.session_state.board.append({
            **pick, "player": chosen["player"], "pos": primary_position(chosen["positions"]),
            "adp": chosen["adp"], "source": "auto"
        })
        remove_from_pool(chosen["player"])
        st.session_state.idx += 1


# ----------------------------- UI -----------------------------

st.title("🏟️ All About the Fundies — 2027 Custom Mock Draft")

if st.session_state.user_team is None:
    st.write("Pick the team you want to control. Every other team auto-drafts using that "
             "manager's real draft history (position tendencies by stretch of the draft), "
             "layered on top of current ADP. Keepers are locked in at their real slots.")
    choice = st.selectbox("Your team", ALL_TEAMS, format_func=team_label)
    if st.button("Start Draft", type="primary"):
        st.session_state.user_team = choice
        st.rerun()
    st.stop()

advance_auto_and_keepers()

left, right = st.columns([2, 1])

with right:
    st.subheader("On the Clock")
    if st.session_state.idx >= len(SEQUENCE):
        st.success("Draft complete!")
    else:
        pick = SEQUENCE[st.session_state.idx]
        st.markdown(f"**Round {pick['round']}, Pick {pick['slot']}** (overall #{pick['overall']})")
        st.markdown(f"**{team_label(pick['team'])}**")

        if pick["team"] == st.session_state.user_team:
            pool = st.session_state.pool
            search = st.text_input("Search player")
            filtered = [p for p in pool if search.lower() in p["player"].lower()] if search else pool[:40]
            options = [f"{p['player']} — {p['positions']} — ADP {p['adp']:.1f}" for p in filtered]
            if options:
                sel = st.selectbox("Available players", options)
                sel_player = filtered[options.index(sel)]
                if st.button("Draft this player", type="primary"):
                    st.session_state.board.append({
                        **pick, "player": sel_player["player"],
                        "pos": primary_position(sel_player["positions"]),
                        "adp": sel_player["adp"], "source": "user"
                    })
                    remove_from_pool(sel_player["player"])
                    st.session_state.idx += 1
                    st.rerun()
            else:
                st.info("No players match your search.")

    st.divider()
    if st.button("Reset Draft"):
        for k in ["user_team", "idx", "board", "pool"]:
            del st.session_state[k]
        st.rerun()

with left:
    st.subheader("Draft Board")
    if not st.session_state.board:
        st.info("Draft hasn't started yet.")
    else:
        rounds_seen = sorted(set(b["round"] for b in st.session_state.board), reverse=True)
        for r in rounds_seen[:3]:
            st.markdown(f"**Round {r}**")
            row = [b for b in st.session_state.board if b["round"] == r]
            row.sort(key=lambda b: b["slot"])
            cols = st.columns(len(row))
            for c, b in zip(cols, row):
                tag = "⭐ KEEP" if b["source"] == "keeper" else ("🧑 YOU" if b["source"] == "user" else "")
                c.markdown(f"**{b['team']}**  \n{b['player']}  \n{tag}")
        with st.expander("Full draft board"):
            for r in sorted(set(b["round"] for b in st.session_state.board)):
                st.markdown(f"**Round {r}**")
                row = [b for b in st.session_state.board if b["round"] == r]
                row.sort(key=lambda b: b["slot"])
                for b in row:
                    tag = " (KEEPER)" if b["source"] == "keeper" else (" (YOU)" if b["source"] == "user" else "")
                    st.write(f"{b['slot']}. {b['team']} — {b['player']}{tag}")
