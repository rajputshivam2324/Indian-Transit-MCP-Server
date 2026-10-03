"""Deterministic, server-authored one-line syntheses attached as ``meta.summary``.

Composing a faithful summary is exactly what small models struggle with, so the
server does it from the structured data. These are purely additive - they never
replace or remove any field - so they cannot degrade output quality. Every
generator is defensive: a summary failure must never turn a good result into an
error (use :func:`safe`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...services.ranking import best_confirm_chance, min_fare
from ._guidance import _has_open_class


def safe(fn: Callable[..., str], *args: Any, **kwargs: Any) -> str | None:
    """Run a summary generator, returning ``None`` on any error."""
    try:
        text = fn(*args, **kwargs)
        return text or None
    except Exception:  # noqa: BLE001 - a summary must never break the tool call
        return None


def fmt_minutes(minutes: int | None) -> str:
    if minutes is None:
        return "?"
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m:02d}m"


def _leg_line(leg) -> str:
    t = leg.train
    dep = f"{leg.from_code} {t.departure}"
    if leg.departure_date:
        dep += f" ({leg.departure_date})"
    arr = f"{leg.to_code} {t.arrival}"
    # Only date the arrival when it rolls into another day, so an overnight leg reads
    # unambiguously instead of looking like it lands before it left.
    if leg.arrival_date and leg.arrival_date != leg.departure_date:
        arr += f" ({leg.arrival_date})"
    seg = f"{dep} -> {arr}"
    a = leg.availability
    cls = ""
    if a:
        bits = [a.travel_class]
        if a.fare is not None:
            bits.append(f"Rs {a.fare}")
        if a.status_display:
            bits.append(a.status_display)
        if a.confirm_chance is not None:
            bits.append(f"{a.confirm_chance}%")
        cls = " [" + " ".join(bits) + "]"
    return f"{t.number} {t.name} ({seg}){cls}"


def render_itinerary(it) -> str:
    """A complete, ready-to-present breakdown of one split itinerary.

    Explicitly labels the layover and every leg's time/class/fare/confirmation so a
    weak model can echo it verbatim without dropping details (the failure we saw
    where 'estimated layover' went missing).
    """
    leg1, leg2 = it.legs[0], it.legs[1]
    hub = it.transfer_arrival_station or it.transfer_station
    change = " - STATION CHANGE, add time to move between stations" if it.requires_station_change else ""
    lines = [
        f"Via {it.transfer_station}:",
        f"  Leg 1: {_leg_line(leg1)}",
        f"  Layover: {fmt_minutes(it.layover_min)} at {hub}{change}",
        f"  Leg 2: {_leg_line(leg2)}",
    ]
    total = f"  Total: {fmt_minutes(it.total_duration_min)}"
    if it.travel_class:
        total += f" in {it.travel_class}"
    if it.total_fare is not None:
        total += f", Rs {it.total_fare}"
    if it.combined_confirm_chance is not None:
        total += f", {it.combined_confirm_chance}% combined confirmation"
    lines.append(total)
    for w in it.warnings:
        lines.append(f"  ! {w}")
    return "\n".join(lines)


def summarize_search(resp) -> str:
    n = len(resp.trains)
    if n == 0:
        return (
            f"No direct trains matched {resp.origin}->{resp.destination} on {resp.date}."
        )
    parts = [
        f"{n} of {resp.total_matched} direct trains {resp.origin}->{resp.destination} "
        f"on {resp.date}."
    ]
    fastest = min(resp.trains, key=lambda t: t.duration_min or 10**9)
    parts.append(f"Fastest: {fastest.number} {fastest.name} ({fastest.duration_fmt}).")

    conf = [(t, best_confirm_chance(t)) for t in resp.trains]
    conf = [(t, ch) for t, ch in conf if ch is not None]
    if conf:
        t, ch = max(conf, key=lambda x: x[1])
        parts.append(f"Best confirmation: {t.number} ({ch}%).")

    fares = [(t, min_fare(t)) for t in resp.trains]
    fares = [(t, f) for t, f in fares if f is not None]
    if fares:
        t, f = min(fares, key=lambda x: x[1])
        parts.append(f"Cheapest: from Rs {f} ({t.number}).")

    waitlisted = sum(1 for t in resp.trains if not _has_open_class(t))
    if waitlisted:
        parts.append(f"{waitlisted} of {n} are fully waitlisted (no open seat).")
    return " ".join(parts)


def summarize_split(itineraries) -> str:
    if not itineraries:
        return "No single-transfer itineraries found."
    best = itineraries[0]
    legs = " + ".join(leg.train.number for leg in best.legs)
    s = f"{len(itineraries)} single-transfer option(s). Best via {best.transfer_station}: {legs}"
    if best.travel_class:
        s += f" in {best.travel_class}"
    if best.combined_confirm_chance is not None:
        s += f", ~{best.combined_confirm_chance}% combined"
    if best.total_fare is not None:
        s += f", Rs {best.total_fare}"
    s += f", {best.layover_min}-min layover."
    if best.requires_station_change:
        s += " Transfer changes stations."
    if best.warnings:
        s += f" {len(best.warnings)} warning(s) on the top option - read them."
    return s


def summarize_trip(options, modes_meta: dict) -> str:
    if not options:
        unavailable = [m for m, info in (modes_meta or {}).items() if not info.get("available")]
        extra = f" Unavailable: {', '.join(unavailable)}." if unavailable else ""
        return "No trip options available." + extra
    modes_shown = sorted({o.mode.value for o in options})
    fastest = min(
        options, key=lambda o: o.total_duration_min if o.total_duration_min is not None else 10**9
    )
    parts = [f"{len(options)} option(s) across {', '.join(modes_shown)}."]
    if fastest.total_duration_min:
        h, m = divmod(fastest.total_duration_min, 60)
        parts.append(f"Fastest: {fastest.summary} ({h}h {m:02d}m).")
    unavailable = [m for m, info in (modes_meta or {}).items() if not info.get("available")]
    if unavailable:
        parts.append(f"Unavailable modes: {', '.join(unavailable)}.")
    return " ".join(parts)


def summarize_confirmation(result) -> str:
    chance = f"{result.confirm_chance}%" if result.confirm_chance is not None else "unknown"
    s = (
        f"Train {result.train_number} {result.travel_class} ({result.quota}) on "
        f"{result.date}: {chance} confirmation"
    )
    if result.booking_status:
        s += f", status {result.booking_status}"
    if result.fare is not None:
        s += f", Rs {result.fare}"
    s += "."
    if result.confirm_chance is not None and result.confirm_chance < 60:
        s += " Low chance - consider a backup train, class, or date."
    return s


def summarize_segments(report) -> str:
    """One-paragraph synthesis of a booking-segment report (best option + totals)."""
    if report.best is None:
        return report.reason or "No booking segment found."
    b = report.best
    s = b.suggestion
    line = (
        f"Best: train {b.train_number} - {s.travel_class} {s.booking_from}->{s.booking_to} "
        f"{s.status or '?'} {s.confirm_chance}% vs {s.baseline_status or '?'} "
        f"{s.baseline_confirm_chance}% on your leg (+{s.gain_pct} points"
    )
    if s.extra_fare is not None:
        line += f", {'+' if s.extra_fare >= 0 else '-'}Rs {abs(s.extra_fare)}"
    line += f"). Book for {s.departure_date} departing {s.booking_from} {s.departure}. "
    line += f"{s.instruction}."
    total = sum(len(t.suggestions) for t in report.trains)
    with_s = sum(1 for t in report.trains if t.suggestions)
    line += f" {total} suggestion(s) on {with_s} of {len(report.trains)} train(s) analysed."
    return line
