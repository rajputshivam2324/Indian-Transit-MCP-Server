# Indian Transit MCP

An MCP server that gives AI assistants live Indian train search. Plug it into any MCP client
(Kiro, Claude Desktop, or your own agent) and it can look up trains, seat availability, fares,
waitlist confirmation odds, full routes, single-transfer trip plans, and longer bookings on the
same train that confirm more easily than a waitlisted one.

The server handles the parts models are bad at: resolving station names, ranking and filtering
trains, adding up fares, reading waitlist odds, and finding a transfer route when there is no good
direct train. It hands back plain structured data that the model turns into an answer.

Built on the [Model Context Protocol](https://modelcontextprotocol.io).

## What you can ask

Once it is connected, your assistant can answer things like:

- "Fastest available AC train from Delhi to Mumbai this Friday"
- "Cheapest confirmed 3AC seat from Bengaluru to Chennai tomorrow"
- "Will 12951 in 2AC confirm from Mumbai to Delhi on the 5th, and what's the waitlist?"
- "No direct train from Muzaffarpur to Delhi is confirmed, find me a single-transfer option"
- "15708 from Delhi to Muzaffarpur in 3AC is waitlisted, is there a better way to book it?"
- "Show the full route for train 12951"
- "Which stations can I use around Delhi?"

The client turns the question into tool calls and the server returns the data.

## What it does

- Train search with sorting (departure, arrival, duration, price, confirmation, distance) and
  filters (class, quota, time windows, max duration, max fare, minimum confirmation chance).
- Per-class availability with fare, waitlist or RAC status, and a confirmation prediction, for
  both General and Tatkal.
- Confirmation prediction for a given train, class, and route.
- Full train routes with every stop, or just the major stops if you want a shorter view.
- Station lookup from free text, plus nearby and alternate stations.
- Single-transfer planning that finds real interchange hubs, ranks options by combined
  confirmation, and warns you about station changes and tight layovers.
- Booking-segment search for waitlisted trains: finds a longer ticket on the same train (say,
  one that starts at the train's origin) that confirms more easily, then tells you to change the
  boarding point and get off early. See [Best booking segment](#best-booking-segment).
- Multi-modal comparison. Trains work today. Buses and flights are optional and off by default.

Every response is a JSON envelope. Alongside the data it carries a short summary and step-by-step
guidance, so smaller models still give complete answers instead of dropping details like the
layover.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (or pip)

## Install and run

```bash
uv venv
uv pip install -e .
uv run indian-transit-mcp
```

That serves MCP over streamable HTTP at `http://127.0.0.1:8000/mcp` and keeps running until you
stop it. It listens on loopback only and has no authentication of its own, so read
[Security](#security) before you change that. To listen somewhere else, set `TRANSIT_SERVER_HOST`,
`TRANSIT_SERVER_PORT` or `TRANSIT_SERVER_PATH` (see [Configuration](#configuration)).

## Connect it to your client

Start the server, then point your client at its URL. For Kiro, add this to
`~/.kiro/settings/mcp.json`:

```json
{
  "mcpServers": {
    "indian-transit": {
      "url": "http://127.0.0.1:8000/mcp",
      "disabled": false
    }
  }
}
```

Kiro accepts plain `http://` for localhost; any other host needs an `https://` URL. Every tool is
read-only, so it is safe to auto-approve them.

### Over stdio instead

A client that launches the server itself (Claude Desktop's `claude_desktop_config.json`, for
example) can still use stdio. Set `TRANSIT_TRANSPORT=stdio` in the server's environment:

```json
{
  "mcpServers": {
    "indian-transit": {
      "command": "uv",
      "args": ["run", "indian-transit-mcp"],
      "cwd": "/absolute/path/to/indian-transit-mcp",
      "env": { "TRANSIT_TRANSPORT": "stdio" }
    }
  }
}
```

If you would rather not depend on `uv` at runtime, point `command` straight at the installed
script (something like `/abs/path/.venv/bin/indian-transit-mcp`) with `"args": []`.

## Security

The HTTP server has **no authentication**. Anyone who can reach its port can call every tool, and
each call spends requests against the upstream service. The defaults keep that contained:

- It listens on `127.0.0.1` only, so only programs on your machine can connect.
- The MCP SDK's DNS-rebinding protection is on for that bind. A request whose `Host` or `Origin`
  is not local is refused (421 / 403), so a web page you visit cannot call the server through your
  browser.

If you expose it beyond your machine, you have to add the protection yourself:

- Put it behind something that authenticates and terminates TLS: a reverse proxy, a VPN, or a
  firewall rule. Clients such as Kiro can send a token in a `headers` entry, but the server does
  not check it. The proxy has to.
- Behind a proxy that forwards your public hostname, list it in `TRANSIT_ALLOWED_HOSTS` exactly
  as it arrives in the `Host` header: `mcp.example.com`, or `mcp.example.com:8443` when the port is
  not the default (`mcp.example.com:*` matches any port). Browser-based clients also need their
  page's origin in `TRANSIT_ALLOWED_ORIGINS`. These are added to the localhost entries, and on a
  non-loopback bind they are what switches the `Host` / `Origin` check on at all.
- Binding to a non-loopback address (`TRANSIT_SERVER_HOST=0.0.0.0`) makes the server log a warning
  at start-up, and a second one if no allow-list is set.

## Tools

| Tool | What it does |
|---|---|
| `find_station_code(query)` | Turn a place name into a station code, with ranked candidates |
| `search_trains(origin, destination, date, ...)` | Direct trains with availability, sorting, and filters |
| `get_seat_availability(train_number, origin, destination, date)` | One train's availability by class |
| `predict_confirmation(train_number, origin, destination, date, travel_class)` | Confirmation chance and booking status |
| `get_train_route(train_number, date?, stops?)` | Ordered stops (pass `stops="major"` for the short version) |
| `suggest_nearby_stations(place)` | Same-city and nearby stations |
| `plan_split_journey(origin, destination, date, ...)` | Ranked single-transfer trips |
| `find_best_booking_segment(origin, destination, date, train_number?, travel_class?, ...)` | A longer booking on the same train that confirms more easily than your own leg (board and alight at your own stations) |
| `search_buses` / `search_flights(origin, destination, date)` | Optional modes, off by default |
| `plan_trip(origin, destination, date, modes?)` | Compare trains, buses, and flights side by side |

Dates take `DD-MM-YYYY`, `YYYY-MM-DD`, `today`, or `tomorrow`. Origin and destination take a station
code or a plain name.

## Best booking segment

Indian Railways allocates quota per station pair, so the pair you need can be badly waitlisted
while a longer pair on the *same train* is much easier to confirm. For 10 Oct 2026, train 15708
Old Delhi (DLI) to Muzaffarpur (MFP) in 3AC was an RLWL with a 72% predicted chance, while
Amritsar (ASR, the train's origin) to MFP was a GNWL with 87%. `find_best_booking_segment` looks
for those longer bookings. You book the longer pair, change your boarding point to your own
origin in IRCTC, and get off at your own destination.

How it searches, per train:

- Your origin and destination must be scheduled halts, in the train's direction. It never
  suggests a reverse trip. A train that does not qualify comes back with a `reason` (for example
  `not on route / wrong direction`) and an explanation in `detail`.
- Candidate booking stations are the `max_extra_stations_each_side` (default 5) nearest halting
  major stops beyond each of your stations, plus the train's origin and terminus. They are counted
  as stations, not route rows, because a route also lists every station the train runs through.
- At most `max_candidates` (default 6) longer pairs are looked up **per train**. Pairs that start
  at the train's origin go first, then pairs that only move the boarding side (the boarding
  station drives the waitlist class far more than the destination), then alighting-side pairs,
  then both. Inside each group, shortest extra distance first. Each lookup is one search that
  covers every class, served from the same short-lived cache as `search_trains`.
- A suggestion is kept when its `confirm_chance` beats the same class on your own leg by at least
  `min_gain_pct` **percentage points** (default 10), so 72% to 87% is a gain of 15. Results are
  sorted by gain, then fare, then extra distance.
- Without `train_number`, the corridor's direct trains (up to 10) are analysed, best first. A
  train the corridor search returns from a neighbouring station (NDLS for DLI) is analysed from
  that station, and `warnings` says so.
- On an overnight train the booking station can be reached a day earlier than your own. Every
  suggestion carries the real `departure_date` and `arrival_date`; search and book with those.

### Example 1: 15708, Old Delhi to Muzaffarpur, 3AC

```json
{
  "origin": "DLI",
  "destination": "MFP",
  "date": "10-10-2026",
  "train_number": "15708",
  "travel_class": "3A"
}
```

Abridged result (captured 3 Oct 2026; predictions move during the day, so treat the numbers as a
snapshot):

| Book | 3A status | Chance | Fare | Gain | Extra |
|---|---|---|---|---|---|
| DLI to MFP (your leg) | RLWL21/WL13 | 72% | Rs 1420 | | |
| ASR to KIR | GNWL34/WL19 | 88% | Rs 1960 | +16 | Rs 540, 729 km, also get off early at MFP |
| **ASR to MFP** | GNWL36/WL21 | 87% | Rs 1735 | +15 | Rs 315, 447 km |
| SZM to MFP | RLWL26/WL12 | 82% | Rs 1420 | +10 | Rs 0, 3 km |

```json
{
  "ok": true,
  "data": {
    "best": { "train_number": "15708", "suggestion": { "booking_from": "ASR", "booking_to": "KIR", "gain_pct": 16 } },
    "trains": [
      {
        "train_number": "15708",
        "user_leg": {
          "from_code": "DLI", "to_code": "MFP", "departure": "17:35", "arrival": "14:55",
          "departure_date": "10-10-2026", "arrival_date": "11-10-2026", "distance": 1077,
          "travel_class": "3A", "fare": 1420, "status": "RLWL21/WL13", "confirm_chance": 72
        },
        "suggestions": [
          {
            "booking_from": "ASR", "booking_to": "MFP",
            "departure": "07:40", "arrival": "14:55",
            "departure_date": "10-10-2026", "arrival_date": "11-10-2026",
            "travel_class": "3A", "quota": "GN", "fare": 1735,
            "status": "GNWL36/WL21", "confirm_chance": 87,
            "baseline_status": "RLWL21/WL13", "baseline_confirm_chance": 72,
            "extra_fare": 315, "extra_km": 447, "gain_pct": 15,
            "instruction": "Book ASR->MFP, set boarding at DLI in IRCTC, alight at MFP"
          }
        ],
        "route_proof": [
          { "code": "ASR", "distance_km": 0.0, "role": "train_origin" },
          { "code": "DLI", "distance_km": 447.0, "role": "user_origin", "halt_min": 15 },
          { "code": "MFP", "distance_km": 1523.0, "role": "user_destination", "halt_min": 10 }
        ],
        "warnings": ["Boarding change required: ...", "Pay extra, no refund for the unused leg: ...", "..."]
      }
    ]
  },
  "meta": { "trains_analyzed": 1, "suggestions": 3, "summary": "Best: train 15708 - 3A ASR->KIR ..." }
}
```

### Example 2: 12204, Old Delhi to Muzaffarpur, 3AC, with a lower threshold

12204 is a Garib Rath (3AC only). Its own leg is a weak RLWL, and the gains are smaller than on
15708, so this run asks for anything that improves by 5 points or more:

```json
{
  "origin": "DLI",
  "destination": "MFP",
  "date": "10-10-2026",
  "train_number": "12204",
  "travel_class": "3A",
  "min_gain_pct": 5
}
```

Result (same snapshot):

| Book | 3A status | Chance | Fare | Gain | Extra |
|---|---|---|---|---|---|
| DLI to MFP (your leg) | RLWL99/WL63 | 73% | Rs 1015 | | |
| JUC to MFP | PQWL52/WL29 | 86% | Rs 1165 | +13 | Rs 150, 368 km |
| PGW to MFP | PQWL52/WL29 | 85% | Rs 1165 | +12 | Rs 150, 347 km |
| ASR to MFP | GNWL116/WL54 | 83% | Rs 1215 | +10 | Rs 200, 447 km |
| ASR to SHC | GNWL116/WL54 | 79% | Rs 1305 | +6 | Rs 290, 659 km, also get off early at MFP |
| DDL to MFP | RLWL73/WL47 | 78% | Rs 1150 | +5 | Rs 135, 304 km |

The train-origin to terminus booking (ASR to SHC) is in the list, but the cheaper boarding-side
bookings beat it, and they keep your destination, so there is no early alight. With the default
`min_gain_pct` of 10 only the first three would show. Each row's `instruction` reads like
`Book PGW->MFP, set boarding at DLI in IRCTC, alight at MFP`.

### Reading the result

- `data.best` is the top suggestion across all trains. `data.trains[]` has one entry per analysed
  train:
  - `user_leg`, your baseline. `classes[]` lists every class; the flat `travel_class`, `fare`,
    `status` and `confirm_chance` are set when there is a single class.
  - `suggestions[]`, `route_proof[]` (the stations the result rests on, in route order, with
    distances and halts) and `warnings[]`.
  - `candidates_considered`, `candidates_evaluated` and `candidates_unavailable`, which show how
    much of the search space was tried.
- A train with nothing to suggest has `suggestions: []`, a short `reason`, and a `detail`
  sentence. Reasons include `not on route / wrong direction`, `user origin/destination is not a
  scheduled halt`, `route unavailable`, `no availability on the user's leg`, `user's leg already
  has high confirmation`, and `no booking segment meets min_gain_pct` (the detail names the best
  gain it did find, so you know how far to lower the threshold). When no train has a suggestion,
  `data.reason` says so.
- `status` is the raw upstream status, so its prefix tells you the waitlist type (GNWL, PQWL,
  RLWL and so on). If a suggestion's `status` equals its `baseline_status`, the gain comes only
  from the predictor's station-specific estimate and the queue position is no better.
- Leave `travel_class` out and every class is compared against its own baseline. That is a lot
  of output, so pass a class when you know it.

### Before you book one

- A boarding-point change needs a **confirmed or RAC ticket**. Under the rules announced for
  1 April 2026 it can be done once, up to the final chart (about 30 minutes before departure),
  and not while the ticket is waitlisted. A ticket that is still waitlisted at the chart is
  cancelled, so this only helps if the waitlist clears. Check the current rule on IRCTC.
- You pay for the whole booked segment. The stretch before your boarding station and after your
  alighting station is not refunded, and getting off early forfeits the rest of the journey.
- Confirmation chances are ConfirmTkt's predictions, not promises, and they move through the day.
  Re-check the pair you choose with `get_seat_availability` using the suggestion's own stations
  and `departure_date`.
- General quota (`GN`) only. The tool is read-only: it never books or changes anything.

## Use it as a library

You do not need MCP to use the core. It is a normal async Python API:

```python
import asyncio
from transit_mcp.common import normalize_date
from transit_mcp.server.app import build_container

async def main():
    c = build_container()
    try:
        result = await c.train_search.search("New Delhi", "Mumbai", normalize_date("tomorrow"))
        for t in result.trains[:5]:
            cheapest = min((a.fare for a in t.availability if a.fare), default=None)
            print(f"{t.number} {t.name} {t.departure}->{t.arrival} {t.duration_fmt} from Rs {cheapest}")
    finally:
        await c.aclose()

asyncio.run(main())
```

`build_container()` sets up the providers, cache, and services and hands back ready objects:
`train_search`, `availability`, `confirmation`, `routes`, `nearby`, `split`, `multimodal`, and
`segments`.

## Configuration

Copy `.env.example` to `.env`. Everything has a default, so you can skip this and it still runs.
Settings are read from the environment with a `TRANSIT_` prefix (see `src/transit_mcp/config.py`).
The ones you are most likely to touch:

- `TRANSIT_TRANSPORT`: `streamable-http` (the default) or `stdio`.
- `TRANSIT_SERVER_HOST`, `TRANSIT_SERVER_PORT`, `TRANSIT_SERVER_PATH` for where the HTTP server
  listens (default `127.0.0.1`, `8000`, `/mcp`). Not to be confused with `TRANSIT_HTTP_*` below,
  which configure the client that calls ConfirmTkt.
- `TRANSIT_ALLOWED_HOSTS`, `TRANSIT_ALLOWED_ORIGINS`: extra `Host` / `Origin` values the HTTP
  server accepts, comma-separated. See [Security](#security).
- `TRANSIT_ENABLE_BUS`, `TRANSIT_ENABLE_FLIGHT` to turn on the optional modes.
- `TRANSIT_HTTP_TIMEOUT_SECONDS`, `TRANSIT_HTTP_MAX_RETRIES` for upstream timeouts and retries.
- `TRANSIT_SEARCH_CACHE_TTL`, `TRANSIT_STATION_CACHE_TTL`, `TRANSIT_ROUTE_CACHE_TTL` for caching.

## Development

```bash
uv pip install -e ".[dev]"
uv run pytest
```

The tests run fully offline against captured fixtures, so you do not need network access. The
HTTP tests start a throwaway server on an ephemeral loopback port and talk to it with the real
MCP client.

The code is layered. `server` holds the MCP tools and calls `services` for the logic. Services
depend on `interfaces` (Protocols); `providers` implement those interfaces and talk to `infra`
(http, cache, errors). Everything is wired in one place (`server/app.py`), so adding or swapping a
data source does not touch the services or tools.

## Data source and limits

Train data comes from ConfirmTkt's public endpoints. This is an unofficial integration. The
response format can change without warning and there is no uptime promise. The provider layer keeps
that contained: if a source breaks or is turned off, the tool returns a clean "unavailable" result
instead of failing the whole call. Confirmation numbers are ConfirmTkt's own predictions, passed
through as they are.

Everything is read-only. No booking, no payments, no PNR changes, no personal data. Not affiliated
with ConfirmTkt or IRCTC.

## License

MIT. See `pyproject.toml`.
