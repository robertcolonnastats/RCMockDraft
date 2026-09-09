# All About the Fundies — 2027 Custom Mock Draft

## Run it
```
pip install -r requirements.txt
streamlit run app.py
```

## How it works
- **Draft order**: real 2027 snake order (25 rounds x 12 teams), parsed from your
  Fantrax "By Round" export, including every traded pick.
- **Keepers**: all 48 (4 per team) locked into the draft board at their assigned
  round. Two conflicts (jd's two Round 8 keepers, Cruz Control's two Round 15
  keepers) were resolved by bumping the second player to the next earlier round,
  per your instruction. Any team+round where the team owned zero picks that round
  (pick traded away entirely) was resolved the same way — bumped to the nearest
  earlier round that team still owns. **Flag this to me if that's not how you want
  it handled** — see `data/keepers_final.csv` for exactly where every keeper landed
  (`intended_round` vs `actual_round`).
- **Player pool**: FantasyPros ADP (using the Fantrax/FT column), with Ohtani
  split into "Shohei Ohtani (Pitcher)" and "Shohei Ohtani (Batter)". All 48
  keepers removed from the pool before the draft starts.
- **Auto-draft logic**: every team except yours drafts using a blend of (a) ADP
  proximity — it strongly prefers the best players still on the board — and (b)
  that manager's real positional tendencies for that stretch of the draft,
  computed from their actual 2024–2026 draft history (weighted 3x/2x/1x toward
  more recent years). Manager continuity across years follows your notes (e.g.
  Kody's history only counts from 2026, since he's a new manager; Evan's excludes
  his 2024 draft under the prior manager).

## Data files (data/)
- `teams.py` — team/manager mapping + which years count toward each manager's trend profile
- `historical_drafts.csv` — 2024-2026 draft results mapped to current team names
- `team_trends.json` — computed position-tendency weights by team and round-range
- `adp_pool.csv` — draftable player pool (keepers already removed)
- `keepers_final.csv` — final keeper-to-slot assignments
- `pick_sequence.json` — the full 300-pick snake order with keepers merged in

## Known simplifications (flag if you want these changed)
- Auto-pick only looks at position tendency + ADP proximity — it doesn't model
  reach/value behavior (how far above/below ADP a manager tends to draft), since
  that needs historical ADP data I don't have.
- Rosters file wasn't wired into the app logic (not needed for a full redraft),
  but was used to help nail down the team-code mapping.
