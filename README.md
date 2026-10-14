# Parlay Researcher & Tracker — v0 (Stages 1–4)

Free ($0) stack. No paid APIs, no signups, no sportsbook logins, **never places a bet**.
Jake places anything he chooses himself, on his phone.

## Stages 2–4 (added 2026-10-13)
- **Covers human experts**: single-matchup prediction articles parsed via JSON-LD
  (author = capper, datePublished = posted_at) + the `TOP PICK` heading
  (team/market). The article's price sits in a client-side widget that is not in
  the static HTML, so picks are graded at the **ESPN/DK price available at
  capture** (`odds_available`). Roundups / "early leans" articles without a
  TOP PICK heading are skipped and counted in `ingest_log`.
- **CapperTek service pages — checked, NOT a pick source**: a service page offers
  "Purchase Picks", shows a Documented Record of *past* picks, and renders its
  Latest/Pending pick via JS behind the purchase/login flow. No documented
  pre-game pick is publicly exposed, so CapperTek stays **discovery-only**
  (CLAIMED leaderboard, refreshed at most once/day inside the poll cycle; the
  service-page check also re-runs daily and logs its finding). Nothing is
  circumvented.
- **Records (Stage 3)**: per-capper and per-source scorecards — W-L-P, flat-1u
  ROI, win rate, units, current + longest streaks, **max drawdown (units)**,
  avg approx-CLV + **beat-close %**, split by sport×market, windows all-time +
  last 100 picks. Records never include CLAIMED/CapperTek rows.
- **Consensus + paper suggestions (Stage 4)**: a leg qualifies only with **2+
  independent source families** on the same game/market/side, **each family at
  100+ tracked picks** (full weight 500+). Independence = source family: all
  Covers properties (computer + experts) count as ONE family/one vote; the
  earliest captured pick is the family's vote. Builder: max 3 legs, cross-game
  preferred (same-game filler flags the ticket SGP), prices = ESPN/DK proxy
  (labelled), fair price = product of leg decimals, **haircut shown as
  "n/a — estimate only"** because no book parlay price exists (never invented).
  Paper stake 0.25u (0.5u when every family is full-weight), auto-graded from
  leg grades, paper P/L kept separate. **Paper period: Oct 13 – Nov 12, 2026.**
  ntfy fires ONLY for a new paper suggestion or a graded paper result, and
  never 23:00–07:00 ET (quiet hours); silent when nothing qualifies.

## History / baselines + TRACKED vs CLAIMED (added 2026-10-13)
- **Historical RESULTS backfill (context only)**: ESPN free scoreboard, stored in
  SEPARATE tables (`hist_events` / `hist_results` / `baselines`) — never mixed
  into the picks ledger, never read by records/consensus. What free actually
  serves (verified live): NHL whole months in ONE call (`?dates=YYYYMM`;
  range syntax `?dates=YYYYMMDD-YYYYMMDD` = HTTP 400), NFL one week per call
  (`week=N&seasontype=2/3&dates=2025`). Backfilled: **NHL 2025-26 full season —
  9 calls, 1,394 finals** (Oct 7 2025–Jun 15 2026, incl. playoffs; Feb dip =
  Olympic break); **NFL 2025 full season — 23 calls, 285 finals** (272 regular +
  13 playoffs; the Pro Bowl exhibition is excluded — ESPN lists its teams as
  literally "NFC"/"AFC"). Current-season finals keep accumulating via the
  normal ESPN ingest (into `events`; the history tables stay the baseline set).
- **Baselines** (dashboard, separate "League baselines — historical context"
  section): NHL — home win 51.9%, avg total 6.23 goals, median 6, Over 5.5 =
  57.2%, Over 6.5 = 46.3%. NFL — home win 53.5%, avg total 45.96 pts, median 45,
  Over 41.5 = 62.1%, Over 44.5 = 52.3%, Over 47.5 = 42.5%. Sanity-checked:
  Oct 2025 NHL home win % recomputed by a second path (fresh ESPN fetch,
  in-memory) = 52.5% (n=179), exact match with the stored rows; grading
  re-run on real finals (ML win/loss, total, spread push) all correct
  (`app.py history-check`).
- **NOT available free (not faked)**: ESPN past-date payloads carry **no odds
  block at all** (0 of ~2,700 past events probed), so historical favourite win %
  and historical closing lines do not exist in this stack — the dashboard says
  "not available free". The only close-movement stats come from **our own**
  `closing_lines` archive (approx — ESPN/DK, small n, labelled as such).
- **TRACKED vs CLAIMED**: two buckets, never merged. **TRACKED** = picks WE
  timestamped pre-game in the picks ledger — the only thing scorecards, ROI,
  consensus and the followable bar ever read. **CLAIMED** = records cappers /
  platforms publish about themselves (CapperTek `claimed_record` strings) —
  unverified, rendered only in the dashboard's separate "Claimed records —
  CLAIMED — unverified, does not count" section, and a capper with only
  claimed data has **no** scorecard (unit-tested). Dashboard headings carry
  the labels: leaderboard/scorecards = "TRACKED — our timestamped pre-game
  record"; claimed section = "CLAIMED — unverified, does not count".

## Jake as a tracked source (added 2026-10-13)
Jake's own picks are a dedicated source family: **source `Jake` (kind=own)**.
File one by forwarding with `capper=Jake` (any case) or `source=Jake`, or use
the dashboard **Log my pick** form (POST /log-pick, same parser path, verified
live over HTTP). Consensus implication: source family is the independence
unit, so Jake's picks are **independent of third-party picks filed under
Manual forward-in** — and the Jake family can vote in consensus once it
reaches **100+ tracked picks, the same bar as everyone**. No owner
exceptions: flat-1u grading, and a late Jake pick is EXCLUDED_LATE like any
other. Jake's picks show in scorecards labelled TRACKED under source Jake.

## Suggestions = bet-slip-ready slips (Stage 5)
Every suggestion — dashboard, ntfy, and the daily brief — is the same concrete,
copy-paste **slip text block**, targeted at one book:

```
PARLAY SLIP — PAPER (not placed)
Book: FanDuel — check price on FanDuel
Leg 1: Colorado Avalanche ML (-102) — NHL Colorado Avalanche @ Dallas Stars Wed Oct 14 07:30 PM ET
Leg 2: Toronto Maple Leafs ML (+136) — NHL Boston Bruins @ Toronto Maple Leafs Wed Oct 14 07:00 PM ET
Parlay odds: +342 (implied 22.62%) — fair product of ESPN/DK proxy prices, check price on FanDuel
Stake: 0.25u paper
Why: consensus of Covers (n=150 tracked, approx-CLV 2.5%) + Manual forward-in (n=120 tracked, approx-CLV 1.1%) on the same legs.
```

Rules baked in: **target book first** (setting `target_book`, default FanDuel);
legs in plain bet-slip language in order, each with its odds; combined parlay
odds last. Prices are ESPN/DK proxy, never the book's price — so every slip
carries the exact label **"check price on FanDuel"**. Stake is **units only**
(paper stake during the paper period) — a dollar amount never appears;
`unit_value` stays unset until Jake sets it. Still suggestion-only: the system
never opens a book and never places anything.

**Dashboard** (phone-first, no login on tailnet): Today's slips at the TOP,
each slip block with a **Copy slip button** (tap slip → tap Copy = the full
slip text in the clipboard, 2 taps). Then the capper leaderboard (sample
warnings visible), then open paper slips with live leg status, consensus
board, recent picks, watchlist, sources, and settings.

**ntfy**: ONLY for a new qualifying consensus parlay (the slip block is the
message), max one suggestion/day by construction, quiet hours respected
**unless a leg starts within the hour**. Graded results do not push — they're
on the dashboard and in the brief.

## Settings — one place
`settings.yaml` in the data volume (botserver: `/data/settings.yaml`), shown
on the dashboard, read/edited via:
```
docker exec parlay-tracker python app.py settings
docker exec parlay-tracker python app.py settings-set target_book BetMGM
```
Keys: `target_book` (FanDuel), `sports_nhl` / `sports_nfl` (on/off — gates
ingest), `unit_value` (blank = unset; stakes render in units regardless),
`quiet_hours_start` / `quiet_hours_end` (23 / 7, ET). Runs fine untouched.

## Daily brief (for the Muse cron)
```
docker exec parlay-tracker python app.py brief
```
Prints one compact brief (today's tracked picks + consensus slips in slip
format + yesterday's graded results + capper top-5) — **or prints NOTHING
when there is no qualifying consensus that day**. The cron should post stdout
to the Parlay chat only when non-empty. (The cron itself is wired by the
parent agent, not this app.)

## Add a capper (under a minute)
```
docker exec parlay-tracker python app.py watch-add "Capper Name" "Source Label" manual "@handle"
docker exec parlay-tracker python app.py watch-list
```
That registers the name on the watchlist (dashboard shows it). No other setup:
the manual forward-in path auto-creates **any** capper name the first time a
pick is forwarded with it — `PICK | capper=<any name> | ...` (see below).
Scraped sources can't be invented this way (a new scrape needs a parser), but
any person Jake follows on Pikkit/Action/X/DubClub can be tracked from their
first forwarded pick, under the same pre-game timestamp rule.
Reddit POTD stays BLOCKED until Jake approves a Reddit OAuth app (a signup) —
not created. Jake's personal 5–10 capper names: not yet provided; they slot in
via watch-add the moment he sends them.

## Gap fixes + new source families (added 2026-10-13/14)

**Fix 1 — three new families** (own family each, our-capture timestamps,
flat-1u, same late rule):
- **UltimateCapper** — Jay Cooper's daily NHL article (explicit `Pick:`
  lines: ML / puck line / totals) and the NFL staff Best Bets text lines
  (Babowski, Cooper). The NFL staff grid is a table of bare signed numbers
  with no team attribution — **not parsed by design** (assigning sides
  would mean assuming a sign convention = guessing).
- **SportsHub** — FREE PICKS only, from the `Sports-Hub--FREE-PICKS` and
  `Randall-Dickelman` handicapper profiles (server-rendered pick cards).
  Premium cards are never ingested. The gameadvisers.com / pickstoday.com
  mirrors are the **same pool** and are never ingested (family rule).
- **Action Network** — free articles only: the daily *Daily Dangles* NHL
  column (per-game sections, capper = the section's By: author) and
  Stuckey's NFL articles (single explicit `Pick:` conclusion). Prices in
  the articles are the cappers' claimed prices; grading uses the ESPN/DK
  price at our capture. Prediction-market prices (e.g. "(50 Cents,
  Kalshi)") are rejected as not-book-odds.

**Fix 2 — CLV math.** `clv_pct` is now **probability-based CLV in
percentage points**: devigged (proportional) fair probability of the
picked side at the ESPN/DK close minus the breakeven probability of the
captured price. Moneyline uses both closing sides; spread/total use both
closing prices at the closing line, and the grade note records line
movement in points (your line → closing line). It replaces the old raw
price ratio, which flattered results (documented example: captured +109
vs close −104/−109 read +6.55% as a ratio, +3.13pp raw, but only +1.58pp
devigged). Benchmark label stays **approx-CLV: ESPN/DK close**. Existing
grades were re-graded 2026-10-14 with the before/after table reported to
Jake (all three turned negative under honest math: +1.80→−1.46,
+3.34→−0.64, +1.74→−1.28). Caveat, stated plainly: for spread/total picks
whose closing *line* moved, the pp figure is the price component at the
closing line; the points moved are in the grade note.

**Fix 3 — NHL prop grading.** Props settle from the NHL's free official
API (`api-web.nhle.com`, no key). Note: `/v1/game-center/{id}/boxscore`
returns 404 for every game (verified from VM and container) — the working
route is `/v1/roster/{ABBREV}/current` + `/v1/player/{id}/game-log/
{season}/2` (goals/assists/points/shots). A prop grades only when its
text parses to player + stat + line + over/under (e.g. "Nathan MacKinnon
Over 1.5 Points", "Andrei Svechnikov Anytime Goal Scorer"). Multi-player
combos and unparseable text stay `ungraded — <specific reason>`, never
guessed. NFL props are **not** graded: ESPN's summary payload does carry
boxscore player stats (passing/rushing/receiving groups with per-athlete
counting stats — verified on a live final), so counting-stat NFL props
are settleable in principle, but the payload shape differs from the NHL
pattern and no NFL prop picks exist in the ledger yet — report-only by
decision, revisit if NFL props start flowing.

**Part A — contest/consensus families.**
- **Covers Contest Consensus** + **Covers Contest Top10** (A1ii): the
  contests.covers.com consensus pages are server-rendered (NHL sides =
  moneyline prices; NFL sides = ATS lines, with pick counts). A family
  pick is filed only for a clear side: ≥20 total contest picks and
  majority ≥55%; thinner rows are signal-only (counted in ingest_log).
  Rows are matched to the earliest *future* ESPN event for the matchup
  (teams meet on consecutive days). Contestant profiles (A1i) are
  **not** ingested: public profile pages expose only aggregate season
  records (CLAIMED-class data) and no pending per-pick data in static
  HTML, so there is nothing capturable pre-game to track.
- **SuperContest/Circa** (A3): Westgate only — TheSpread publishes the
  weekly contest lines + consensus counts. Top-5 consensus sides are
  filed at the **published contest line** with odds NULL: the record is
  W/L/push **graded vs stale contest lines** (that label is in the
  capper name, so it renders on every scorecard). Weekly cadence: the
  cycle fetches Sat/Sun ET or after a 6-day gap. Circa is not ingested —
  the VSiN and VegasInsider Circa/SuperContest pages probed return 404.
- **BettingPros** (A2): **not ingested** — the web app renders nothing
  server-side and the official API answers 403 "Missing Authentication
  Token" (requires an API key = a signup, banned by the $0 rule). Their
  own eligibility floor, for the record: a full-season NFL Accuracy
  board for one bet type requires 80 Quick Picks.
- **TheRX Tracker** (A4): **not ingested** — the "Picks and Record
  Tracker" forum is a frozen archive (newest activity Aug 2008) and no
  tracker forum is linked from the current index; there are no active
  tracker posters to verify.

**Part B — Reddit POTD bridge.** `reddit.py` polls r/sportsbook's hot
RSS **from inside the container** (botserver egress works; the build VM
gets 403, and bursts get 429 — so: ≤1 listing fetch per 30 min,
descriptive UA, 2-hour backoff on 429, search.rss never used). It finds
the daily "Pick of the Day" thread, reads its comment RSS, and files
picks under family **Reddit POTD**, poster = capper. Posters' self-stated
records/units are CLAIMED metadata (kept in raw text only). The thread's
most-picked side is counted in the ingest log only — same posters, not
an independent family.

*Parser widened 2026-10-14 from real thread samples* (fixture:
`tests/fixtures/potd_comments_20261014.xml`). The Atom comment feed is
flat (no reply depth), so a POTD submission is identified by the
thread's own rules — the comment must carry the poster's record line
AND a pick line; replies/chatter are counted separately, never parsed.
Pick markers: `Pick:` / `Today's Pick:` / `POTD:` (the "Last Pick:"
recap segment is excluded). Odds: decimal is dominant (`@ 1.95`,
`2.15 odds`, `($1.91)`) and is converted to American; American in
parens / `-105 @ DK` / slash pairs (`-120/1.83`) also accepted. A bet
needs team/player + market (ML/spread/total/prop) + line or odds;
priceless ML posts stay unparsed. Identifiable but ungradeable markets
(2-leg parlays, BTTS, double chance, Asian handicap, corners) are
counted as `unsupported`, never stored. The ingest detail reports
comments / submissions / stored (incl. unmatched/other-sport) /
unsupported / unparsed / chatter separately. RSS dies **Nov 13, 2026**; the permanent OAuth
route is prepared in `REDDIT-API-REQUEST.md` (Jake files it himself
before the **Oct 31, 2026** request deadline).

## What Stage 1 does
- **Ledger**: SQLite (WAL) on the botserver — sources, cappers, events, picks (append-only),
  odds snapshots, closing lines, grades, paper suggestions.
- **Lines/scores**: ESPN free scoreboard (NHL + NFL), DraftKings-sourced odds carried in the
  feed. Closing-line archive: poll ~15 min; the last pre-start snapshot is the closing
  reference, labelled **approx-CLV: ESPN/DK close** (not Pinnacle).
- **Fraud rule (no exceptions)**: a pick counts only if `captured_at < commence_time`.
  Late picks are *stored* as `EXCLUDED_LATE` and logged — never counted, never deleted.
  Picks whose event can't be matched are stored `UNMATCHED` and also never counted.
- **Grading**: legs only, from final ESPN scores, flat 1u. Props ESPN box scores can't
  settle = `ungraded — no data`, never guessed. Records = TRACKED picks only, split by
  sport×market, with ROI (flat 1u), win rate, streak, avg approx-CLV.
- **Sample-size discipline**: <100 picks = insufficient sample; 100–499 = warming up;
  **nobody is "followable" under 500 tracked picks** — the dashboard shows the warning.
- **Consensus (Stage 3/4 placeholder)**: a leg will qualify only with 2+ independent
  tracked sources, each at 100+ picks (full weight 500+), max 3 legs, haircut % shown,
  paper-tracked for the first 30 days.

## Source status (verified 2026-10-13)
| Source | Status | Notes |
|---|---|---|
| ESPN scoreboard (NHL/NFL) | ✅ LIVE | HTTP 200; events, finals, DK odds (ML/spread/total + prices) |
| Covers free picks | ✅ LIVE | HTTP 200, ~1.25 MB; "Computer Picks powered by Odds Shark" cards parsed (predicted winner ML). Intermittent: some fetches serve a variant page with no cards (parsed=0 logged in `ingest_log`); one bounded re-fetch + the 15-min poll absorb this. |
| Covers expert articles | ✅ LIVE | Prediction articles: JSON-LD author/datePublished + TOP PICK heading parsed. Verified 2026-10-13: 4 NHL picks stored (Neil Parker — Avalanche ML, Dustin Saracini — Maple Leafs ML, Todd Cordell — Over 6.5 TRACKED; one Todd Cordell pick EXCLUDED_LATE by the fraud rule). NFL single-game articles appear Thu–Sun; the "early leans" format has no TOP PICK block and is skipped/logged. Price = ESPN/DK at capture (article price is JS-widget only). |
| CapperTek leaderboard | ✅ LIVE (from botserver) | HTTP 200 on the deployed container; 100 cappers seeded with claimed records (e.g. "5-1-0 (83%) \| +3.70u \| +61.67%"). Fetch from the build VM stalled repeatedly — host-dependent; every run's real result is in `ingest_log`. Leaderboard rows are **CLAIMED only** (discovery) — never tracked picks. |
| CapperTek service pages | ⛔ NOT A PICK SOURCE (checked) | Verified live 2026-10-13 (incl. picks.asp?shs=LegendNextDoor.com): "Purchase Picks" + past Documented Record + JS-rendered pending pick behind purchase/login. No public pre-game picks — discovery-only, daily re-check logs the finding. |
| Reddit r/sportsbook POTD | ❌ BLOCKED | Unauthenticated JSON **and** RSS both HTTP 403. Needs a Jake-approved OAuth app — out of scope; not retried. |
| Manual forward-in | ✅ LIVE | See format below. Same pre-game timestamp rule. |

## Live proof (2026-10-13 evening, local run)
Covers page captured ~8:12 pm ET contained 10 NHL computer picks. Three were for games
that had already started (7:30 / 7:45 / 8:00 pm ET) → stored `EXCLUDED_LATE`. Seven were
for later games → `TRACKED`, matched to ESPN events, awaiting finals. Grading pipeline was
proven end-to-end on a completed game (Bills @ Rams final) with a clearly labelled
`PIPELINE_TEST` synthetic pick (win, +0.9091u flat-1u) that is excluded from all records.

## Manual forward-in (Jake → tracker)
**Jake's rule: no forms, no required fields from him.** Jake can paste a pick
in chat in **ANY format** — a screenshot caption, a forwarded message, one
messy line — and the agent files it into the ledger under the same timestamp
rule (counts only if filed before game start; late = EXCLUDED_LATE, stored).
The structured line below is only the machine path the agent/CLI uses when
filing it:

One line, pipe-delimited (via chat to the agent, or CLI):

```
PICK | capper=<name> | sport=NHL | market=moneyline | team=<team name> | odds=-150
PICK | capper=<name> | sport=NHL | market=spread | team=<team> | line=-1.5 | odds=+130
PICK | capper=<name> | sport=NFL | market=total | selection=Over 47.5 | line=47.5 | odds=-110
```
Optional: `source=<Pikkit/Action/X/DubClub label>`, `units=<n>` (recorded, never used —
all records are recomputed flat-1u), `starts=<YYYY-MM-DD>`.
CLI: `python3 app.py add-pick "PICK | capper=... | ..."` (inside the container:
`docker exec parlay-tracker python app.py add-pick "..."`).
Late forwards (after game start) are stored `EXCLUDED_LATE`; unmatched forwards are
stored `UNMATCHED` — neither counts.

## Run / deploy
Local: `python3 run_tests.py` (7 tests, stdlib only) · `python3 app.py cycle` ·
`python3 app.py demo-pipeline-test` · `python3 app.py serve`
Botserver: code at `~/parlay-tracker`, `docker compose up -d --build`
Dashboard: `http://<your-server>:8796/` (tailnet) · health: `/healthz`
DB: Docker volume `parlay-tracker_parlay_data` → `/data/parlay.db`
Alerts: ntfy topic `parlay-alerts` on self-hosted ntfy (self-hosted, LAN only)
Backup: `scripts/backup_db.sh` (nightly cron on botserver; 14-day retention)

## Known Stage-1 limits
- Closing lines/CLV populate only from snapshots taken *before* a game starts, so
  approx-CLV appears after the first full day of live polling.
- Covers expert picks shipped in Stage 2 (above); NFL expert articles are sparse mid-week (single-game prediction pieces appear Thu–Sun).
- CapperTek fetch stalls from the build VM but works from the botserver; if that ever
  changes, `ingest_log` will show ok=0 with the error — by design, never silently assumed.
- NFL board is thin mid-week (ESPN returns the week's games on its default board window).
