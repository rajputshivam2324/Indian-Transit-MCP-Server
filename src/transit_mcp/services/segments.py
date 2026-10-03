"""Best booking segment: book a longer ticket on the same train, then board/alight where you want.

Indian Railways allocates quota per station pair, so the pair ``user_from -> user_to`` can be
badly waitlisted (RLWL / PQWL) while a longer pair on the *same train* that fully covers it
(for example one that starts at the train's origin, GNWL) has a much better chance. The
traveller books the longer pair, moves the boarding point to ``user_from`` and gets off at
``user_to``.

How a search runs, per train:

1. Fetch the full route and keep what matters: each stop's effective halt, distance, and the
   journey day it is reached / left.
2. Locate the user's stations. They must be on the route, in that order (never a reverse
   trip), and the train must really halt at both (or they must be its origin/terminus).
3. Candidate booking points are the ``max_extra_stations_each_side`` nearest *halting major*
   stops beyond each user station - counted in stations, not raw route rows, because a route
   lists every pass-through signal cabin - plus the train's origin and terminus.
4. Every (booking_from, booking_to) pair that extends the user leg on at least one side is
   ranked and the first ``max_candidates`` are looked up. Ranking is "shortest extra distance
   first" *within tiers*: pairs anchored at the train's origin come first (that is where
   general-quota waitlists usually start), then boarding-side-only extensions (the boarding
   station drives the waitlist class far more than the destination does), then
   alighting-side-only, then both-sided. Without tiers a small cap would only ever try tiny
   hops next to the user's own stations and never the train origin.
5. Each lookup is one upstream search (all classes at once) through the shared TTL cache.
   For a booking that starts on an earlier day of an overnight run the search date is shifted
   accordingly, and the real dates are returned.
6. Each candidate is compared, class by class, with the user leg; those gaining at least
   ``min_gain_pct`` points are returned, best first.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass

from ..common import add_days, parse_time_to_minutes
from ..infra.errors import InvalidInputError, TransitError
from ..infra.logging import get_logger
from ..interfaces.providers import ConfirmationEstimator
from ..models.domain import ClassAvailability, Stop, Train, TrainRoute
from ..models.segments import (
    BestSegment,
    BookingSegmentReport,
    SegmentClassFare,
    SegmentStop,
    SegmentSuggestion,
    SegmentUserLeg,
    TrainSegmentResult,
)
from .ranking import TrainFilters, class_is_available
from .routes import RouteService
from .stations import StationService
from .trains import TrainSearchService

log = get_logger("segments")

GENERAL_QUOTA = "GN"

DEFAULT_MIN_GAIN_PCT = 10

# Every candidate pair is an upstream search; these bounds keep one call polite.
# Defaults evaluate the full station window / corridor so a normal call does not need
# the agent to raise knobs via next_actions. Lower them only to save upstream cost.
MAX_EXTRA_STATIONS_LIMIT = 15
MAX_CANDIDATES_LIMIT = 64
MAX_TRAIN_LIMIT = 30
DEFAULT_MAX_EXTRA_STATIONS = 5
DEFAULT_MAX_CANDIDATES = MAX_CANDIDATES_LIMIT
DEFAULT_TRAIN_LIMIT = MAX_TRAIN_LIMIT
_DEFAULT_CONCURRENCY = 6

_MINUTES_PER_DAY = 1440

# Short, stable reasons (machine-friendly); ``detail`` carries the specifics.
REASON_NOT_ON_ROUTE = "not on route / wrong direction"
REASON_NOT_A_HALT = "user origin/destination is not a scheduled halt"
REASON_NO_ROUTE = "route unavailable"
REASON_NO_EXTENSION = "user's leg already covers the train's whole run"
REASON_NO_BASELINE = "no availability on the user's leg"
REASON_CLASS_NOT_OFFERED = "requested class not offered on the user's leg"
REASON_NO_PREDICTION = "no confirmation prediction on the user's leg"
REASON_BASELINE_STRONG = "user's leg already has high confirmation"
REASON_NO_GAIN = "no booking segment meets min_gain_pct"
REASON_INTERNAL = "analysis failed"


class _Skip(Exception):
    """Internal control flow: this train cannot (or need not) be analysed further."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


# --------------------------------------------------------------------------- #
# Route analysis (pure, no I/O)
# --------------------------------------------------------------------------- #
def effective_halt_min(stop: Stop) -> int | None:
    """Scheduled halt in minutes.

    ``HaltMinutes`` is missing on some real halting stops (arrival 18:28, departure 18:30 and
    no halt value), so fall back to the clock times. A pass-through has arrival == departure,
    hence 0.
    """
    if stop.halt_min is not None:
        return stop.halt_min
    arr = parse_time_to_minutes(stop.arrival)
    dep = parse_time_to_minutes(stop.departure)
    if arr is None or dep is None:
        return None
    return (dep - arr) % _MINUTES_PER_DAY


def departure_day(stop: Stop) -> int:
    """1-based journey day on which the train *leaves* ``stop``.

    ``Stop.day`` is the day of arrival; a halt that runs past midnight departs the next day.
    """
    arr = parse_time_to_minutes(stop.arrival)
    dep = parse_time_to_minutes(stop.departure)
    if arr is not None and dep is not None and dep < arr:
        return stop.day + 1
    return stop.day


@dataclass(frozen=True)
class RouteStop:
    """A route stop annotated with what the segment analysis needs."""

    index: int
    code: str
    name: str
    arrival: str | None
    departure: str | None
    km: float | None
    arrival_day: int
    departure_day: int
    halt_min: int | None
    is_major: bool
    is_first: bool
    is_last: bool

    @property
    def is_halt(self) -> bool:
        """A passenger can board/alight here: the origin, the terminus, or a real halt."""
        return self.is_first or self.is_last or (self.halt_min or 0) > 0

    @property
    def is_candidate(self) -> bool:
        """Usable as a *booking* point: a major stop (or origin/terminus) that really halts."""
        return (self.is_major or self.is_first or self.is_last) and self.is_halt


def build_route_stops(route: TrainRoute) -> list[RouteStop]:
    last = len(route.stops) - 1
    return [
        RouteStop(
            index=i,
            code=s.code,
            name=s.name,
            arrival=s.arrival,
            departure=s.departure,
            km=s.distance_from_origin,
            arrival_day=s.day,
            departure_day=departure_day(s),
            halt_min=effective_halt_min(s),
            is_major=s.is_major,
            is_first=i == 0,
            is_last=i == last,
        )
        for i, s in enumerate(route.stops)
    ]


def _describe_stop(stop: RouteStop) -> str:
    return f"{stop.code} ({stop.name})" if stop.name else stop.code


def locate_user_stops(stops: list[RouteStop], origin: str, destination: str) -> tuple[int, int]:
    """Indices of the user's origin and destination, in travel order.

    Raises :class:`_Skip` when either station is missing, the trip would run against the
    train's direction, or the train only passes a station without halting.
    """
    codes = [s.code for s in stops]
    missing = [c for c in (origin, destination) if c not in codes]
    if missing:
        raise _Skip(
            REASON_NOT_ON_ROUTE,
            f"{' and '.join(missing)} is not a stop on this train's route.",
        )
    i_o = codes.index(origin)
    later = [i for i, c in enumerate(codes) if c == destination and i > i_o]
    if not later:
        raise _Skip(
            REASON_NOT_ON_ROUTE,
            f"{destination} comes before {origin} on this train's route, so it does not run "
            f"{origin}->{destination}. Booking segments only ever extend the same direction.",
        )
    i_d = later[0]
    for idx, label in ((i_o, "origin"), (i_d, "destination")):
        if not stops[idx].is_halt:
            raise _Skip(
                REASON_NOT_A_HALT,
                f"Your {label} {_describe_stop(stops[idx])} is on the route but the train runs "
                "through without a scheduled halt, so no ticket can start or end there.",
            )
    return i_o, i_d


def candidate_windows(
    stops: list[RouteStop], i_o: int, i_d: int, max_extra: int
) -> tuple[list[int], list[int]]:
    """Possible booking origins (``<= i_o``) and booking destinations (``>= i_d``).

    The window is ``max_extra`` halting *major* stops on each side - stations, not raw rows -
    plus the train's origin and terminus. The user's own station is always first in its list.
    """
    last = len(stops) - 1
    before = [i for i in range(i_o - 1, -1, -1) if stops[i].is_candidate][:max_extra]
    after = [i for i in range(i_d + 1, last + 1) if stops[i].is_candidate][:max_extra]
    boards = [i_o, *before]
    alights = [i_d, *after]
    if i_o > 0 and 0 not in boards:
        boards.append(0)
    if i_d < last and last not in alights:
        alights.append(last)
    return boards, alights


@dataclass(frozen=True)
class CandidatePair:
    """One extended ``board -> alight`` booking (indices into the route stop list)."""

    board: int
    alight: int
    tier: int
    extra_km: float | None
    extra_stops: int


def pair_tier(board: int, alight: int, i_o: int, i_d: int, last: int) -> int:
    """Evaluation priority (lower first); see the module docstring for the rationale."""
    board_ext = board < i_o
    alight_ext = alight > i_d
    if board == 0 and board_ext and (alight == i_d or alight == last):
        return 0  # anchored at the train's origin
    if board_ext and not alight_ext:
        return 1  # boarding side only
    if alight_ext and not board_ext:
        return 2  # alighting side only
    return 3  # both sides


def _extra_km(stops: list[RouteStop], board: int, alight: int, i_o: int, i_d: int) -> float | None:
    """Kilometres booked beyond the user leg (both sides), or ``None`` if a distance is missing."""
    board_km, user_from = stops[board].km, stops[i_o].km
    user_to, alight_km = stops[i_d].km, stops[alight].km
    if board_km is None or user_from is None or user_to is None or alight_km is None:
        return None
    return (user_from - board_km) + (alight_km - user_to)


def rank_candidate_pairs(
    stops: list[RouteStop], i_o: int, i_d: int, boards: list[int], alights: list[int]
) -> list[CandidatePair]:
    """Every pair that extends the user leg on at least one side, best-to-try first."""
    last = len(stops) - 1
    pairs: list[CandidatePair] = []
    for b in boards:
        for a in alights:
            if b == i_o and a == i_d:
                continue
            pairs.append(
                CandidatePair(
                    board=b,
                    alight=a,
                    tier=pair_tier(b, a, i_o, i_d, last),
                    extra_km=_extra_km(stops, b, a, i_o, i_d),
                    extra_stops=(i_o - b) + (a - i_d),
                )
            )
    inf = float("inf")
    pairs.sort(
        key=lambda p: (
            p.tier,
            p.extra_km if p.extra_km is not None else inf,
            p.extra_stops,
            p.board,
            p.alight,
        )
    )
    return pairs


def _shift(date: str, days: int) -> str | None:
    try:
        return add_days(date, days)
    except (ValueError, TypeError):
        return None


def booking_date(user_date: str, stops: list[RouteStop], i_o: int, i_board: int) -> str | None:
    """Date the train leaves ``stops[i_board]``, given it leaves the user's origin on ``user_date``.

    Earlier stops of an overnight run are reached on an earlier calendar day.
    """
    return _shift(user_date, stops[i_board].departure_day - stops[i_o].departure_day)


def alighting_date(user_date: str, stops: list[RouteStop], i_o: int, i_alight: int) -> str | None:
    """Date the train reaches ``stops[i_alight]``, given the user's departure date."""
    return _shift(user_date, stops[i_alight].arrival_day - stops[i_o].departure_day)


def build_route_proof(
    stops: list[RouteStop],
    i_o: int,
    i_d: int,
    board_indices: frozenset[int] | set[int] = frozenset(),
    alight_indices: frozenset[int] | set[int] = frozenset(),
) -> list[SegmentStop]:
    """The stations behind a result, in route order with distances.

    That is the user's two stations plus every station a suggestion books from / to - enough
    to see they lie on the route in travel order and are real halts.
    """
    last = len(stops) - 1
    roles: dict[int, str] = {}
    for i in board_indices:
        roles[i] = "train_origin" if i == 0 else "booking_from"
    for i in alight_indices:
        roles[i] = "train_terminus" if i == last else "booking_to"
    roles[i_o] = "user_origin"
    roles[i_d] = "user_destination"
    return [
        SegmentStop(
            code=stops[i].code,
            name=stops[i].name or None,
            distance_km=stops[i].km,
            role=role,
            halt_min=None if (stops[i].is_first or stops[i].is_last) else stops[i].halt_min,
        )
        for i, role in sorted(roles.items())
    ]


def suggestion_sort_key(s: SegmentSuggestion) -> tuple:
    """Best first: largest gain, then cheapest fare, then least extra distance."""
    inf = float("inf")
    return (
        -s.gain_pct,
        s.fare if s.fare is not None else inf,
        s.extra_km if s.extra_km is not None else inf,
        s.booking_from,
        s.booking_to,
        s.travel_class,
    )


def _halt_phrase(stop: RouteStop) -> str:
    if stop.is_first:
        return f"{stop.code} is the train's origin"
    if stop.is_last:
        return f"{stop.code} is the train's terminus"
    return f"{stop.code} halts {stop.halt_min} min"


def build_warnings(
    *,
    user_from: RouteStop,
    user_to: RouteStop,
    user_date: str,
    suggestions: list[SegmentSuggestion],
    unavailable: int,
    notes: list[str],
) -> list[str]:
    """Caveats the traveller must read before acting on a suggestion."""
    warnings = list(notes)
    if suggestions:
        extends_board = any(s.booking_from != user_from.code for s in suggestions)
        extends_alight = any(s.booking_to != user_to.code for s in suggestions)
        if extends_board:
            warnings.append(
                f"Boarding change required: after booking, change the boarding point to "
                f"{user_from.code} in IRCTC before the final chart. It is allowed once and only "
                "on a confirmed/RAC ticket, not while waitlisted - an e-ticket still waitlisted "
                "at the chart is auto-cancelled, so this only helps if the waitlist clears. "
                "Verify the current rule on IRCTC."
            )
        unused = " and ".join(
            part
            for part in (
                f"before {user_from.code}" if extends_board else "",
                f"after {user_to.code}" if extends_alight else "",
            )
            if part
        )
        warnings.append(
            f"Pay extra, no refund for the unused leg: the stretch {unused} is paid for but "
            "not refunded."
        )
        if extends_alight:
            warnings.append(
                f"Early alight forfeits the balance: get off at {user_to.code}; the rest of "
                "the booked journey is lost."
            )
        warnings.append(
            f"Verify halt: {_halt_phrase(user_from)}; {_halt_phrase(user_to)}. Confirm on IRCTC "
            "that the train stops there on the travel date."
        )
        shifted = [s for s in suggestions if s.departure_date and s.departure_date != user_date]
        if shifted:
            seen: dict[str, str] = {}
            for s in shifted:
                seen.setdefault(f"{s.booking_from}->{s.booking_to}", s.departure_date or "")
            listing = "; ".join(f"{pair} on {d}" for pair, d in seen.items())
            warnings.append(
                f"Date shift: these bookings leave their own origin before {user_date}: "
                f"{listing}. Search and book using the suggestion's departure_date."
            )
    if unavailable:
        warnings.append(
            f"{unavailable} candidate segment(s) returned no usable availability for this "
            "train/date (not running, class closed, or lookup failed) and were skipped."
        )
    return warnings


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #
def _normalize_quota(quota: str | None) -> str:
    q = (quota or GENERAL_QUOTA).strip().upper()
    if q in ("GENERAL", GENERAL_QUOTA):
        return GENERAL_QUOTA
    raise InvalidInputError(
        f"quota {quota!r} is not supported: booking-segment suggestions are for the General "
        f"quota only (use 'GN')."
    )


def _check_range(name: str, value: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise InvalidInputError(
            f"{name} must be an integer between {low} and {high}, got {value!r}."
        )
    return value


@dataclass(frozen=True)
class _Query:
    src: str
    dst: str
    date: str
    travel_class: str | None
    quota: str
    max_extra: int
    max_candidates: int
    min_gain: int
    sem: asyncio.Semaphore


class BookingSegmentService:
    """Find longer bookings on one train that confirm more easily than the user's own leg."""

    def __init__(
        self,
        stations: StationService,
        train_search: TrainSearchService,
        route_service: RouteService,
        estimator: ConfirmationEstimator,
        *,
        concurrency: int = _DEFAULT_CONCURRENCY,
    ) -> None:
        self._stations = stations
        self._search = train_search
        self._routes = route_service
        self._estimator = estimator
        self._concurrency = max(1, concurrency)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    async def find(
        self,
        origin: str,
        destination: str,
        date: str,
        *,
        train_number: str | None = None,
        travel_class: str | None = None,
        quota: str | None = None,
        max_extra_stations_each_side: int = DEFAULT_MAX_EXTRA_STATIONS,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
        min_gain_pct: int = DEFAULT_MIN_GAIN_PCT,
        train_limit: int = DEFAULT_TRAIN_LIMIT,
    ) -> BookingSegmentReport:
        """Analyse one train (``train_number``) or the corridor's direct trains.

        ``date`` is ``DD-MM-YYYY``: the day the traveller boards at ``origin``.
        """
        quota_n = _normalize_quota(quota)
        cls = (travel_class or "").strip().upper() or None
        number = (train_number or "").strip() or None
        _check_range(
            "max_extra_stations_each_side",
            max_extra_stations_each_side,
            0,
            MAX_EXTRA_STATIONS_LIMIT,
        )
        _check_range("max_candidates", max_candidates, 1, MAX_CANDIDATES_LIMIT)
        _check_range("min_gain_pct", min_gain_pct, 0, 100)
        _check_range("train_limit", train_limit, 1, MAX_TRAIN_LIMIT)
        if origin.strip().upper() == destination.strip().upper():
            raise InvalidInputError("origin and destination must be different stations.")

        matched = 1
        refs: list[tuple[str, Train | None]]
        if number:
            src, dst = await asyncio.gather(
                self._stations.resolve_code(origin),
                self._stations.resolve_code(destination),
            )
            refs = [(number, None)]
        else:
            filters = TrainFilters(classes=frozenset({cls}) if cls else frozenset(), quota=quota_n)
            resp = await self._search.search(origin, destination, date, filters=filters, limit=0)
            src, dst = resp.origin, resp.destination
            matched = resp.total_matched
            refs = self._pick_trains(resp.trains, src, dst, train_limit)
        if src == dst:
            raise InvalidInputError("origin and destination resolve to the same station.")

        q = _Query(
            src=src,
            dst=dst,
            date=date,
            travel_class=cls,
            quota=quota_n,
            max_extra=max_extra_stations_each_side,
            max_candidates=max_candidates,
            min_gain=min_gain_pct,
            sem=asyncio.Semaphore(self._concurrency),
        )
        results = list(await asyncio.gather(*(self._analyze_train(q, n, seed) for n, seed in refs)))

        # Trains with suggestions first (best first); the rest keep the search order.
        with_s = [r for r in results if r.suggestions]
        with_s.sort(key=lambda r: suggestion_sort_key(r.suggestions[0]))
        ordered = with_s + [r for r in results if not r.suggestions]

        best = None
        reason = None
        if with_s:
            top = with_s[0]
            best = BestSegment(
                train_number=top.train_number,
                train_name=top.train_name,
                suggestion=top.suggestions[0],
            )
        elif not results:
            reason = f"No direct trains found between {src} and {dst} on {date}."
        elif len(results) == 1:
            reason = f"No suggestions for train {results[0].train_number}: {results[0].reason}."
        else:
            counts = Counter(r.reason or REASON_NO_GAIN for r in results)
            breakdown = "; ".join(f"{n} {why}" for why, n in counts.most_common())
            reason = f"No suggestions on the {len(results)} trains checked: {breakdown}."

        return BookingSegmentReport(
            origin=src,
            destination=dst,
            date=date,
            train_number=number,
            quota=quota_n,
            travel_class=cls,
            min_gain_pct=min_gain_pct,
            max_extra_stations_each_side=max_extra_stations_each_side,
            max_candidates=max_candidates,
            trains_matched=matched,
            trains=ordered,
            best=best,
            reason=reason,
        )

    # ------------------------------------------------------------------ #
    # Train selection
    # ------------------------------------------------------------------ #
    @staticmethod
    def _pick_trains(
        trains: list[Train], src: str, dst: str, limit: int
    ) -> list[tuple[str, Train | None]]:
        """Up to ``limit`` distinct trains, exact-corridor matches before nearby variants.

        The upstream "nearby" search mixes in trains that start at a neighbouring station
        (NDLS for DLI). Putting exact matches first stops them from being crowded out of a
        small limit by variants that merely share a city.
        """
        exact = [t for t in trains if t.from_code == src and t.to_code == dst]
        variant = [t for t in trains if not (t.from_code == src and t.to_code == dst)]
        picked: list[tuple[str, Train | None]] = []
        seen: set[str] = set()
        for t in exact + variant:
            if t.number in seen:
                continue
            seen.add(t.number)
            picked.append((t.number, t))
            if len(picked) >= limit:
                break
        return picked

    # ------------------------------------------------------------------ #
    # One train
    # ------------------------------------------------------------------ #
    async def _analyze_train(
        self, q: _Query, number: str, seed: Train | None
    ) -> TrainSegmentResult:
        """Never raises: a failure becomes this train's ``reason`` so others still report."""
        out = TrainSegmentResult(train_number=number, train_name=seed.name if seed else None)
        try:
            await self._fill(q, out, number, seed)
        except _Skip as skip:
            out.suggestions = []
            out.reason, out.detail = skip.reason, skip.detail
        except TransitError as exc:
            log.info("segments: train %s failed: %s", number, exc)
            out.suggestions = []
            out.reason, out.detail = REASON_INTERNAL, exc.message
        except Exception as exc:  # noqa: BLE001 - one bad train must not sink the report
            log.exception("segments: unexpected failure analysing train %s", number)
            out.suggestions = []
            out.reason, out.detail = REASON_INTERNAL, f"{type(exc).__name__}: {exc}"
        return out

    async def _fill(
        self, q: _Query, out: TrainSegmentResult, number: str, seed: Train | None
    ) -> None:
        try:
            async with q.sem:
                route = await self._routes.get_route(number, q.date)
        except TransitError as exc:
            raise _Skip(REASON_NO_ROUTE, exc.message) from exc
        out.train_name = out.train_name or route.name or None
        stops = build_route_stops(route)
        if not stops:
            raise _Skip(REASON_NO_ROUTE, f"Train {number} has an empty route.")

        user_from_code, user_to_code, notes = self._user_stations(q, stops, seed)
        i_o, i_d = locate_user_stops(stops, user_from_code, user_to_code)
        uo, ud = stops[i_o], stops[i_d]

        out.route_proof = build_route_proof(stops, i_o, i_d)
        boards, alights = candidate_windows(stops, i_o, i_d, q.max_extra)
        pairs = rank_candidate_pairs(stops, i_o, i_d, boards, alights)
        out.candidates_considered = len(pairs)
        if not pairs:
            raise _Skip(
                REASON_NO_EXTENSION,
                f"{uo.code}->{ud.code} already runs from the train's origin to its terminus, "
                "so there is no longer booking on this train.",
            )

        # --- baseline: the user's own leg ------------------------------------------
        if (
            seed is not None
            and seed.number == number
            and seed.from_code == uo.code
            and seed.to_code == ud.code
        ):
            base_train: Train | None = seed
        else:
            try:
                base_train = await self._pair_train(q, number, uo.code, ud.code, q.date)
            except TransitError as exc:
                raise _Skip(
                    REASON_NO_BASELINE,
                    f"Availability lookup for {uo.code}->{ud.code} failed: {exc.message}",
                ) from exc
        if base_train is None:
            raise _Skip(
                REASON_NO_BASELINE,
                f"Train {number} has no availability between {uo.code} and {ud.code} on "
                f"{q.date} (it may not run that day, or the quota is closed).",
            )
        baseline = self._baseline_classes(q, base_train, uo, ud, number)
        out.user_leg = self._user_leg(q, base_train, stops, i_o, i_d, baseline)

        # Only classes that can still gain `min_gain` points are worth a lookup.
        targets = {
            cls: a
            for cls, a in baseline.items()
            if a.confirm_chance is not None and 100 - a.confirm_chance >= q.min_gain
        }
        if not targets:
            if all(a.confirm_chance is None for a in baseline.values()):
                raise _Skip(
                    REASON_NO_PREDICTION,
                    "The upstream gave no confirmation prediction for the user's leg, so a "
                    "gain cannot be measured.",
                )
            best_now = max(a.confirm_chance or 0 for a in baseline.values())
            raise _Skip(
                REASON_BASELINE_STRONG,
                f"The user's leg already has up to {best_now}% confirmation, so no longer "
                f"booking can add {q.min_gain}+ points.",
            )

        # --- candidates ---------------------------------------------------------------
        chosen = pairs[: q.max_candidates]
        out.candidates_evaluated = len(chosen)
        fetched = await asyncio.gather(
            *(self._fetch_candidate(q, number, stops, i_o, p) for p in chosen)
        )

        suggestions: list[SegmentSuggestion] = []
        used_boards: set[int] = set()
        used_alights: set[int] = set()
        best_seen: tuple[int, str] | None = None
        unavailable = 0
        for pair, cand_train in zip(chosen, fetched, strict=True):
            if cand_train is None:
                unavailable += 1
                continue
            cand_by_class = self._general_by_class(cand_train, q.quota)
            usable = False
            for cls, base in targets.items():
                cand_raw = cand_by_class.get(cls)
                if cand_raw is None:
                    continue
                cand = self._estimator.estimate(cand_raw)
                if cand.confirm_chance is None or not class_is_available(cand):
                    continue
                usable = True
                gain = cand.confirm_chance - (base.confirm_chance or 0)
                label = f"{stops[pair.board].code}->{stops[pair.alight].code} {cls}"
                if best_seen is None or gain > best_seen[0]:
                    best_seen = (gain, label)
                if gain < q.min_gain:
                    continue
                suggestions.append(
                    self._suggestion(q, stops, i_o, i_d, pair, cand_train, cls, base, cand, gain)
                )
                used_boards.add(pair.board)
                used_alights.add(pair.alight)
            if not usable:
                unavailable += 1

        suggestions.sort(key=suggestion_sort_key)
        out.suggestions = suggestions
        out.candidates_unavailable = unavailable
        out.route_proof = build_route_proof(stops, i_o, i_d, used_boards, used_alights)
        out.warnings = build_warnings(
            user_from=uo,
            user_to=ud,
            user_date=q.date,
            suggestions=suggestions,
            unavailable=unavailable,
            notes=notes,
        )
        if not suggestions:
            out.reason = REASON_NO_GAIN
            if best_seen is None:
                seen_text = "none of them returned usable availability"
            elif best_seen[0] <= 0:
                seen_text = "none improved on the user's leg"
            else:
                seen_text = f"the best improvement seen was +{best_seen[0]} points ({best_seen[1]})"
            out.detail = (
                f"Checked {len(chosen)} of {len(pairs)} longer segment(s) on {number}; "
                f"{seen_text}, below the required +{q.min_gain}. Lower min_gain_pct"
            )
            if len(chosen) < len(pairs):
                out.detail += ", or raise max_candidates to try more pairs in this window"
            else:
                out.detail += (
                    ", or raise max_extra_stations_each_side to look further along the route"
                )
            out.detail += "."

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _user_stations(
        q: _Query, stops: list[RouteStop], seed: Train | None
    ) -> tuple[str, str, list[str]]:
        """The user's stations as this train knows them.

        When the requested station is not on the route but the corridor search matched the
        train from a neighbouring station (NDLS for DLI), use that station and say so.
        """
        codes = {s.code for s in stops}
        origin, dest = q.src, q.dst
        notes: list[str] = []
        if origin not in codes and seed is not None and seed.from_code in codes:
            notes.append(
                f"The corridor search matched this train from nearby {seed.from_code}, not "
                f"{origin}; the analysis boards at {seed.from_code}."
            )
            origin = seed.from_code
        if dest not in codes and seed is not None and seed.to_code in codes:
            notes.append(
                f"The corridor search matched this train to nearby {seed.to_code}, not "
                f"{dest}; the analysis alights at {seed.to_code}."
            )
            dest = seed.to_code
        return origin, dest, notes

    @staticmethod
    def _general_by_class(train: Train, quota: str) -> dict[str, ClassAvailability]:
        out: dict[str, ClassAvailability] = {}
        for a in train.availability:
            if a.quota == quota:
                out.setdefault(a.travel_class, a)
        return out

    def _baseline_classes(
        self, q: _Query, train: Train, uo: RouteStop, ud: RouteStop, number: str
    ) -> dict[str, ClassAvailability]:
        by_class = self._general_by_class(train, q.quota)
        if q.travel_class:
            if q.travel_class not in by_class:
                offered = ", ".join(by_class) or "none"
                raise _Skip(
                    REASON_CLASS_NOT_OFFERED,
                    f"Class {q.travel_class} is not offered ({q.quota} quota) on train "
                    f"{number} for {uo.code}->{ud.code}; offered: {offered}.",
                )
            by_class = {q.travel_class: by_class[q.travel_class]}
        if not by_class:
            raise _Skip(
                REASON_CLASS_NOT_OFFERED,
                f"Train {number} has no {q.quota}-quota classes for {uo.code}->{ud.code}.",
            )
        return {cls: self._estimator.estimate(a) for cls, a in by_class.items()}

    @staticmethod
    def _user_leg(
        q: _Query,
        train: Train,
        stops: list[RouteStop],
        i_o: int,
        i_d: int,
        baseline: dict[str, ClassAvailability],
    ) -> SegmentUserLeg:
        uo, ud = stops[i_o], stops[i_d]
        distance = train.distance
        if distance is None and uo.km is not None and ud.km is not None:
            distance = round(ud.km - uo.km)
        leg = SegmentUserLeg(
            from_code=uo.code,
            to_code=ud.code,
            from_name=uo.name or None,
            to_name=ud.name or None,
            departure=train.departure or uo.departure,
            arrival=train.arrival or ud.arrival,
            departure_date=q.date,
            arrival_date=alighting_date(q.date, stops, i_o, i_d),
            distance=distance,
            quota=q.quota,
            classes=[
                SegmentClassFare(
                    travel_class=cls,
                    quota=a.quota,
                    fare=a.fare,
                    status=a.status,
                    confirm_chance=a.confirm_chance,
                )
                for cls, a in baseline.items()
            ],
        )
        if len(baseline) == 1:
            cls, a = next(iter(baseline.items()))
            leg.travel_class = cls
            leg.fare = a.fare
            leg.status = a.status
            leg.confirm_chance = a.confirm_chance
        return leg

    async def _pair_train(
        self, q: _Query, number: str, src: str, dst: str, date: str
    ) -> Train | None:
        """This train's row for exactly ``src -> dst`` on ``date`` (``None`` if not offered).

        Goes through the shared search cache, so repeated pairs - across classes, across trains
        that share a corridor, across calls within the TTL - cost one upstream request.
        """
        async with q.sem:
            result = await self._search.raw_search_by_code(src, dst, date)
        for t in (*result.trains, *result.nearby_trains):
            if t.number != number:
                continue
            # A nearby-station variant is a different booking; only accept the exact pair.
            if (t.from_code or src) == src and (t.to_code or dst) == dst:
                return t
        return None

    async def _fetch_candidate(
        self, q: _Query, number: str, stops: list[RouteStop], i_o: int, pair: CandidatePair
    ) -> Train | None:
        board, alight = stops[pair.board], stops[pair.alight]
        date = booking_date(q.date, stops, i_o, pair.board)
        if date is None:
            return None
        try:
            return await self._pair_train(q, number, board.code, alight.code, date)
        except TransitError as exc:
            log.info(
                "segments: %s %s->%s on %s unavailable: %s",
                number,
                board.code,
                alight.code,
                date,
                exc,
            )
            return None

    @staticmethod
    def _suggestion(
        q: _Query,
        stops: list[RouteStop],
        i_o: int,
        i_d: int,
        pair: CandidatePair,
        train: Train,
        cls: str,
        base: ClassAvailability,
        cand: ClassAvailability,
        gain: int,
    ) -> SegmentSuggestion:
        board, alight = stops[pair.board], stops[pair.alight]
        uo, ud = stops[i_o], stops[i_d]
        extra_fare = None
        if cand.fare is not None and base.fare is not None:
            extra_fare = cand.fare - base.fare
        return SegmentSuggestion(
            booking_from=board.code,
            booking_to=alight.code,
            departure=train.departure or board.departure,
            arrival=train.arrival or alight.arrival,
            departure_date=booking_date(q.date, stops, i_o, pair.board),
            arrival_date=alighting_date(q.date, stops, i_o, pair.alight),
            travel_class=cls,
            quota=q.quota,
            fare=cand.fare,
            status=cand.status,
            confirm_chance=cand.confirm_chance or 0,
            baseline_status=base.status,
            baseline_confirm_chance=base.confirm_chance or 0,
            extra_fare=extra_fare,
            extra_km=round(pair.extra_km) if pair.extra_km is not None else None,
            gain_pct=gain,
            instruction=(
                f"Book {board.code}->{alight.code}, set boarding at {uo.code} in IRCTC, "
                f"alight at {ud.code}"
            ),
        )
