# pub-trivia-night

> Local-first self-hosted pub-trivia night generator. Paste a brief about your
> crowd + vibe, pick a theme, get back a host script with rounds, questions,
> scoring rules, tiebreaker math, and a 0–100 night-feasibility score across
> 5 axes — all in 60s, no API keys, no LLM.

![domain](https://img.shields.io/badge/domain-consumer_%2F_party--hosting-blue)
![stack](https://img.shields.io/badge/stack-Flask+%2B+SQLite+%2B+vanilla_JS-green)
![llm](https://img.shields.io/badge/llm-zero_API_keys-purple)
![local](https://img.shields.io/badge/local--first-100%25-orange)

## What it does

1. **Brief input.** Host pastes 1–6 sentences describing the night:
   who is coming, how long, vibe (mixed adults / kids / family / work),
   memorable past rounds, off-limits topics.
2. **Forge.** Backend deterministically builds:
   - **Round structure** — 3–12 rounds (default 8), each with a curated 8-question
     bank pulled from the theme's question pool (4 themes have ≥6 rounds, 15+
     themes total: General Knowledge, Music, Geography, Sports, History,
     Science, Food & Drink, Film & TV, Literature, 80s, 90s, Office,
     Holidays, Tech, Kids' Birthday).
   - **Host script** — per-round estimated minutes, host cues, scoring rules
     (1 pt/Q + 0.5 bonus if all teams agree on tiebreaker).
   - **Tiebreaker math** — deterministic per session_id problem
     (e.g. "Compute (32 + 9) × 7 - 19 = 246").
   - **Team-balance handicaps** if N teams is disclosed.
   - **5-axis score** — balance / pace / ambiguity / accessibility /
     replayability → 0–100 overall with verdict band
     (`ring_the_bell` ≥85 / `mostly_ready` ≥70 / `needs_polish` ≥55 /
     `tight_but_usable` ≥40 / `needs_rework` <40).
   - **Risk flags** — runaway-leader risk, picture-round logistics, vibe
     mismatch (kids audience + literature theme), etc.
3. **Save.** Each session is logged to SQLite with optional 1–5 star
   self-rating + free-form notes.
4. **Export.** Markdown export (print-ready, Q-by-Q + Answer key + Risk flags
   + Host script + Tiebreaker) and JSON dump for archival.

## Why this matters

Hosting a quiz night is genuinely hard:

- You have to **balance difficulty** so the room stays engaged (too easy =
  bored, too hard = grumpy).
- You need **logistics** sorted: picture sheets, music clips, scoring rubrics.
- You want **a tiebreaker math problem** that can't be guessed.
- You want to **avoid running out of steam** at round 7 of 8.
- Every host forgets to time-box a round; everyone notices at 11pm.

`pub-trivia-night` keeps the host focused on the room and off the prep
tedium: it serves the questions, suggests timing, flags logistics risks,
and prints a printable answer key.

## Who it's for

- Anyone hosting a **house game night** for 4-20 friends.
- Bars, libraries, and community centers running **monthly trivia**.
- **Workplace team-building** leads who need a soft, accessible round.
- **Family reunions** with mixed adult / kid audiences.
- **Teachers** running in-class trivia as a review game.

## Tech stack

- **Backend**: Python 3.11+ / Flask 3 / SQLite.
- **Frontend**: Vanilla JS (no framework, no build step).
- **Styling**: Single hand-rolled CSS file, dark dense workbench theme.
- **LLM**: none — deterministic question bank + rule-based scoring.
- **API keys**: none — fully local.

## Run it

```bash
cd pub-trivia-night
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# start
python3 app.py
# → http://127.0.0.1:5071
```

Open `http://127.0.0.1:5071` in your browser. Click **Load sample** to see
what a complete forge looks like.

## Smoke tests

```bash
python3 tests.py
```

30+ assertions covering: theme catalog, analyze roundtrip, question
shape, score axes, verdict banding, tiebreaker determinism, risk flag
categories, all 15 themes, Markdown export, PATCH round-trip, sessions
list, accessibility drop on kids+academic, duration extremes.

## REST API

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/health` | GET | liveness |
| `/api/themes` | GET | 15-theme catalog |
| `/api/sample` | GET | pre-filled brief for first-run |
| `/api/analyze` | POST | forge a quiz night |
| `/api/sessions` | GET | history (last 50) |
| `/api/sessions/<id>` | GET | full session + payload |
| `/api/sessions/<id>` | PATCH | save self_rating + notes |
| `/api/export/<id>?fmt=md\|json` | GET | downloadable export |

## Domain rotation slot

`consumer / party-hosting` — adjacent but distinct from any existing
build: not a murder-mystery (round-robin-mystery), not a dinner-party-
deck (conversation prompts), not a movie-night ranker, not a kids'
fire-safety prep. This is the **host-side trivia generator** — the
moment in the calendar when you've invited 8 people to your living
room at 7pm Saturday and need to be ready in 60 seconds.

Future runs should rotate away from `consumer / party-hosting` for at
least 7-14 days.
