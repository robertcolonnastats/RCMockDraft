"""Determines which roster slots a player is eligible for."""

POS_TOKENS_HITTER = {"C", "1B", "2B", "3B", "SS", "OF", "LF", "CF", "RF", "INF", "UT", "DH"}
POS_TOKENS_PITCHER = {"SP", "RP", "P"}


def tokens(positions_str):
    return [t.strip().upper() for t in (positions_str or "").split(",") if t.strip()]


def is_pitcher(positions_str):
    toks = set(tokens(positions_str))
    return bool(toks & POS_TOKENS_PITCHER)


def can_place(slot_label, positions_str, injured=False, milb=False):
    toks = set(tokens(positions_str))
    if injured:
        # Injured players can only occupy an IL slot — nothing else.
        return slot_label == "IL"
    if slot_label == "IL":
        return False  # non-injured players can't sit in IL
    if slot_label == "MiLB":
        return milb
    if slot_label == "RES":
        return True
    if slot_label == "UT":
        return not is_pitcher(positions_str)  # any hitter
    if slot_label == "P":
        return bool(toks & {"SP", "RP"})
    if slot_label == "SP":
        return "SP" in toks
    if slot_label == "RP":
        return "RP" in toks
    if slot_label == "INF":
        return bool(toks & {"1B", "2B", "3B", "SS", "INF"})
    if slot_label == "OF":
        return bool(toks & {"OF", "LF", "CF", "RF"})
    if slot_label in ("C", "1B", "2B", "3B", "SS"):
        return slot_label in toks
    return False


# Priority order for auto-assigning a pick to a roster slot: fill the most
# specific eligible slot first, saving flexible slots (UT/RES) for later.
SLOT_PRIORITY = ["C", "1B", "2B", "3B", "SS", "OF", "SP", "RP", "INF", "P", "UT", "RES", "IL", "MiLB"]


def choose_best_slot(open_slots, positions_str, injured=False, milb=False):
    """open_slots: list of (slot_id, label) tuples that are currently empty.
    Returns the slot_id of the best-fit eligible slot, or None if the player
    can't be rostered anywhere right now (roster full for their eligibility).
    """
    eligible = [(sid, label) for sid, label in open_slots if can_place(label, positions_str, injured, milb)]
    if not eligible:
        return None
    eligible.sort(key=lambda sl: SLOT_PRIORITY.index(sl[1]) if sl[1] in SLOT_PRIORITY else 99)
    return eligible[0][0]
