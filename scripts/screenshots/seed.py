"""Seed a throwaway Mercury instance with a demo project for the README screenshots (docs/screenshots).

Never point this at ~/.mercury. The steps (paths are examples):

  1. cd frontend && npx vite build --outDir <tmp>/dist --emptyOutDir
  2. git init a small repo at <tmp>/trailmix (any commit will do)
  3. from backend/, with MERCURY_DATA_DIR=<tmp>/data MERCURY_PORT=8791 MERCURY_FRONTEND_DIST=<tmp>/dist
     MERCURY_EMBEDDINGS=0 MERCURY_DOC_SUMMARIES=0:  .venv/Scripts/python -m mercury
  4. python seed.py 8791 <tmp>/trailmix <tmp>/data/mercury.db
  5. with ELECTRON_RUN_AS_NODE unset, SHOOT_URL=http://127.0.0.1:8791:  node node_modules/electron/cli.js scripts/screenshots/shoot.cjs

Usage: python seed.py <port> <repo_path> <db_path>
"""

import json
import sqlite3
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

PORT, REPO, DB = int(sys.argv[1]), sys.argv[2], sys.argv[3]
BASE = f"http://127.0.0.1:{PORT}/api"
ORIGIN = f"http://127.0.0.1:{PORT}"


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Origin", ORIGIN)
    with urllib.request.urlopen(req) as res:
        raw = res.read()
        return json.loads(raw) if raw else None


# --------------------------------------------------------------------------- projects

trail = call("POST", "/projects", {
    "name": "Trailmix",
    "key": "TRL",
    "description": "Plans day hikes from GPX files and the weather",
    "repo_path": REPO,
    "color": "#4fd18b",
})
P = trail["id"]
# A couple of neighbours so the sidebar isn't empty.
call("POST", "/projects", {"name": "Sourdough Log", "key": "SD", "description": "Bake notes", "color": "#f0b44c"})
call("POST", "/projects", {"name": "Pixel Garden", "key": "PG", "description": "Tiny generative art", "color": "#ff6fb5"})

# --------------------------------------------------------------------------- tasks

TASKS = [
    ("Import routes from GPX files", "done", "high"),
    ("Elevation profile under the map", "done", "medium"),
    ("Offline map tiles", "backlog", "low"),
    ("Share a trip as a link", "backlog", "medium"),
    ("Packing list per trip", "backlog", "low"),
    ("Weather warnings for the start time", "in_progress", "high"),
    ("Estimate hike time from distance and climb", "todo", "high"),
    ("Dark map style", "todo", "medium"),
    ("Remember the last trailhead", "todo", "low"),
    ("Sunset time on the trip card", "review", "medium"),
    ("Fix km/mi toggle on the summary", "review", "urgent"),
]
tasks = {}
for i, (title, status, priority) in enumerate(TASKS):
    t = call("POST", f"/projects/{P}/tasks", {"title": title, "status": status, "priority": priority, "position": i})
    tasks[title] = t

# --------------------------------------------------------------------------- docs

planning = call("POST", f"/projects/{P}/collections", {
    "name": "Planning", "description": "How trips get planned: time estimates, weather, routes."})
data = call("POST", f"/projects/{P}/collections", {
    "name": "Data sources", "description": "Where maps, elevation and weather come from."})

DOCS = [
    {
        "title": "Hike time estimate: Naismith's rule plus a pace factor",
        "collection_id": planning["id"],
        "summary": "Trip times use Naismith's rule (5 km/h plus 1 h per 600 m of climb), scaled by a per-user "
                   "pace factor learned from finished trips. Descents steeper than 12% add time. Decided 2026-11-18.",
        "body": """## Decision
Trip times use **Naismith's rule**: 5 km/h on the flat, plus 1 hour for every 600 m of climb.

That gets close for most day hikes, so we scale it by a **pace factor** per person instead of asking for a fitness level.

## Pace factor
- Starts at `1.0`.
- After a finished trip, nudge it toward `actual / estimated`, weighted 0.3.
- Clamp to `0.6`-`1.8`, so one bad day doesn't wreck it.

## Descents
Naismith ignores downhill. Steep descents are slow, so:

| Grade | Extra time |
|---|---|
| under 12% | none |
| 12-20% | 10 min per 300 m down |
| over 20% | 20 min per 300 m down |

## What didn't work
- Tobler's hiking function per GPX segment: noisy, because GPX elevation jitters by a few metres between points. Smoothing helped but the plain rule was just as close.
- Asking for a fitness level up front. Nobody picks "slow".
""",
    },
    {
        "title": "Weather: Open-Meteo, hourly, cached per trailhead",
        "collection_id": data["id"],
        "summary": "Weather comes from Open-Meteo's hourly forecast (no key), fetched for the trailhead's coordinates "
                   "and cached for 30 minutes. Warnings fire on gusts over 50 km/h, thunder, or under 2 C.",
        "body": """## Source
Open-Meteo hourly forecast. Free, no API key, good enough in the mountains.

## Caching
Cached per trailhead for 30 minutes, keyed by coordinates rounded to 2 decimals.

## Warnings
- Gusts over 50 km/h
- Thunderstorm codes (95-99)
- Below 2 C at the start time
""",
    },
    {
        "title": "Elevation data: DEM lookup, not GPX altitudes",
        "collection_id": data["id"],
        "summary": "Elevation comes from a 30 m DEM lookup rather than the altitudes in GPX files, which vary by device. "
                   "Profiles are resampled every 25 m.",
        "body": "## Why\nGPX altitudes depend on the device and drift. A DEM lookup is consistent.\n",
    },
    {
        "title": "Units: metric inside, convert at the edges",
        "summary": "Everything is stored in metres and seconds; the km/mi toggle only changes formatting.",
        "body": "## Rule\nStore metres and seconds. Convert only when formatting.\n",
    },
    {
        "title": "How to work on Trailmix",
        "kind": "rules",
        "summary": "Rules for agents: keep the map code in src/map, run npm test before finishing, no new dependencies without asking.",
        "body": "## Rules\n- Map code stays in `src/map`.\n- Run `npm test` before finishing.\n- Ask before adding a dependency.\n",
    },
]
docs = {}
for d in DOCS:
    docs[d["title"]] = call("POST", f"/projects/{P}/docs", d)

# --------------------------------------------------------------------------- a session mid-run
# Inserted straight into the database with status "running": the stream replays the events and then
# waits, so the UI shows a live session without calling Claude.

task = tasks["Weather warnings for the start time"]
now = datetime.now(timezone.utc).replace(tzinfo=None)
start = now - timedelta(minutes=3)
prompt = (
    "Complete task TRL-6: Weather warnings for the start time\n\n"
    "Show a warning on the trip card when the forecast at the planned start time looks bad."
)

db = sqlite3.connect(DB)
cur = db.execute(
    "INSERT INTO agentrun (prompt, task_id, model, permission_mode, project_id, purpose, status, session_id, images,"
    " created_at, started_at, seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
    (prompt, task["id"], "claude-opus-5-5", "acceptEdits", P, "work", "running", "demo-session", "[]",
     start.isoformat(" "), start.isoformat(" "), 0),
)
run_id = cur.lastrowid

edit_old = """export function warningsFor(hour: HourlyForecast): string[] {
  return [];
}"""
edit_new = """export function warningsFor(hour: HourlyForecast): string[] {
  const warnings: string[] = [];
  if (hour.windGustsKmh > 50) warnings.push(`Gusts up to ${Math.round(hour.windGustsKmh)} km/h`);
  if (hour.weatherCode >= 95) warnings.push("Thunderstorms likely");
  if (hour.temperatureC < 2) warnings.push(`Cold start: ${Math.round(hour.temperatureC)} °C`);
  return warnings;
}"""

EVENTS = [
    ("system", {"subtype": "init", "session_id": "demo-session", "model": "claude-opus-5-5", "cwd": "~/src/trailmix"}),
    ("tool_use", {"id": "t1", "name": "mcp__mercury__search_docs", "input": {"query": "weather warnings thresholds"}, "parent_tool_use_id": None}),
    ("tool_result", {"tool_use_id": "t1", "content": "TRL-D2 Weather: Open-Meteo, hourly, cached per trailhead", "is_error": False}),
    ("text", {"text": "The weather doc (TRL-D2) already settles the thresholds: gusts over 50 km/h, thunderstorm codes 95-99, and below 2 °C at the start time. Let me look at how the forecast reaches the trip card.", "parent_tool_use_id": None}),
    ("tool_use", {"id": "t2", "name": "Grep", "input": {"pattern": "HourlyForecast", "path": "src"}, "parent_tool_use_id": None}),
    ("tool_result", {"tool_use_id": "t2", "content": "src/weather/openMeteo.ts\nsrc/weather/warnings.ts\nsrc/trip/TripCard.tsx", "is_error": False}),
    ("tool_use", {"id": "t3", "name": "Read", "input": {"file_path": "src/weather/warnings.ts"}, "parent_tool_use_id": None}),
    ("tool_result", {"tool_use_id": "t3", "content": "1  import type { HourlyForecast } from './openMeteo';\n2\n3  export function warningsFor(hour: HourlyForecast): string[] {\n4    return [];\n5  }", "is_error": False}),
    ("text", {"text": "`warningsFor` is a stub. I'll fill it in with the three rules from the doc, then show the result on `TripCard`.", "parent_tool_use_id": None}),
    ("tool_use", {"id": "t4", "name": "Edit", "input": {"file_path": "src/weather/warnings.ts", "old_string": edit_old, "new_string": edit_new}, "parent_tool_use_id": None}),
    ("tool_result", {"tool_use_id": "t4", "content": "The file src/weather/warnings.ts has been updated.", "is_error": False}),
    ("tool_use", {"id": "t5", "name": "Bash", "input": {"command": "npm test -- warnings", "description": "Run the warnings tests"}, "parent_tool_use_id": None}),
]
for seq, (kind, payload) in enumerate(EVENTS, start=1):
    at = start + timedelta(seconds=12 * seq)
    db.execute("INSERT INTO agentevent (run_id, seq, kind, payload, created_at) VALUES (?,?,?,?,?)",
               (run_id, seq, kind, json.dumps(payload), at.isoformat(" ")))

# A finished session too, so the list has more than one.
done_at = now - timedelta(hours=5)
cur = db.execute(
    "INSERT INTO agentrun (prompt, task_id, model, permission_mode, project_id, purpose, status, session_id, images,"
    " result, cost_usd, num_turns, created_at, started_at, finished_at, seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
    ("Complete task TRL-10: Sunset time on the trip card", tasks["Sunset time on the trip card"]["id"],
     "claude-opus-5-5", "acceptEdits", P, "work", "succeeded", "demo-session-2", "[]",
     "Added sunset time to the trip card.", 0.41, 9,
     done_at.isoformat(" "), done_at.isoformat(" "), (done_at + timedelta(minutes=6)).isoformat(" "), 1),
)
# The project header shows repo_path; keep the real temp path (and the user's name in it) out of the shots.
db.execute("UPDATE project SET repo_path = '~/src/trailmix' WHERE id = ?", (P,))
db.commit()
print(json.dumps({"project_id": P, "run_id": run_id}))
