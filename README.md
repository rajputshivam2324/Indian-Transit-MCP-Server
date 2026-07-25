# Indian Transit MCP

An MCP server that gives AI assistants live Indian train search. Plug it into any MCP client
(Kiro, Claude Desktop, or your own agent) and it can look up trains, seat availability, fares,
waitlist confirmation odds, full routes, and single-transfer trip plans.

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

That starts the server over stdio.

## Connect it to your client

Add it to your MCP config. For Kiro that is `~/.kiro/settings/mcp.json`, for Claude Desktop it is
`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "indian-transit": {
      "command": "uv",
      "args": ["run", "indian-transit-mcp"],
      "cwd": "/absolute/path/to/indian-transit-mcp",
      "disabled": false
    }
  }
}
```

If you would rather not depend on `uv` at runtime, point `command` straight at the installed
script (something like `/abs/path/.venv/bin/indian-transit-mcp`) with `"args": []`. Every tool is
read-only, so it is safe to auto-approve them.

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
| `search_buses` / `search_flights(origin, destination, date)` | Optional modes, off by default |
| `plan_trip(origin, destination, date, modes?)` | Compare trains, buses, and flights side by side |

Dates take `DD-MM-YYYY`, `YYYY-MM-DD`, `today`, or `tomorrow`. Origin and destination take a station
code or a plain name.

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
`train_search`, `availability`, `confirmation`, `routes`, `nearby`, `split`, and `multimodal`.

## Configuration

Copy `.env.example` to `.env`. Everything has a default, so you can skip this and it still runs.
Settings are read from the environment with a `TRANSIT_` prefix (see `src/transit_mcp/config.py`).
The ones you are most likely to touch:

- `TRANSIT_ENABLE_BUS`, `TRANSIT_ENABLE_FLIGHT` to turn on the optional modes.
- `TRANSIT_HTTP_TIMEOUT_SECONDS`, `TRANSIT_HTTP_MAX_RETRIES` for upstream timeouts and retries.
- `TRANSIT_SEARCH_CACHE_TTL`, `TRANSIT_STATION_CACHE_TTL`, `TRANSIT_ROUTE_CACHE_TTL` for caching.

## Development

```bash
uv pip install -e ".[dev]"
uv run pytest
```

The tests run fully offline against captured fixtures, so you do not need network access.

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
