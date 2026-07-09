"""
shipping.py — drop-in for evcalc.py

Replaces the old per-card scalar `roundtrip_ship_insured`.

Why: PSA and BGS both bill shipping PER ORDER, and both scale it with declared
value. A single number per service was wrong in two directions at once: it
over-charged every card in a batch, and it silently assumed one declared-value
band. Verified July 9, 2026 against both submission wizards.

Contract: resolve_order_shipping() returns the TOTAL shipping for the order, or
raises ShippingUnverified. evcalc allocates it across cards. Resolve or refuse.
"""

from dataclasses import dataclass


class ShippingUnverified(Exception):
    """No verified band covers this order. Feeds the REFUSED path, never a guess."""


@dataclass(frozen=True)
class ShippingQuote:
    order_total: float
    per_card: float
    band_verified_on: str
    includes_outbound: bool
    notes: str = ""


def resolve_order_shipping(fees: dict, service: str, n_cards: int,
                           declared_value_total: float) -> ShippingQuote:
    """Total shipping for one submission, plus its per-card allocation.

    Raises ShippingUnverified when the order falls outside every recorded band.
    That refusal is the point: an unverified band is exactly where a silent
    estimate would do the most damage to the verdict.
    """
    svc = fees["grading_services"].get(service)
    if svc is None:
        raise ShippingUnverified(f"unknown service: {service}")

    shipping = svc.get("shipping") or {}
    bands = shipping.get("bands") or []
    if not bands:
        raise ShippingUnverified(f"{service}: no shipping bands recorded")

    # Narrowest covering band wins, so a $250 order never inherits a $5,000 rate.
    covering = [
        b for b in bands
        if n_cards <= b["n_cards_max"]
        and declared_value_total <= b["declared_value_total_max"]
    ]
    if not covering:
        raise ShippingUnverified(
            f"{service}: no verified shipping band covers "
            f"{n_cards} card(s) at ${declared_value_total:,.2f} declared value. "
            f"Re-quote in the submission wizard and add the band, with its date."
        )

    band = min(
        covering,
        key=lambda b: (b["n_cards_max"], b["declared_value_total_max"]),
    )

    includes_outbound = bool(shipping.get("inbound_paid_to_service"))

    if includes_outbound:
        total = band["order_total"]
        notes = ""
    else:
        total = band["order_total_excl_outbound"]
        notes = (
            "EXCLUDES outbound: you ship to the grader yourself. "
            "Add your insured outbound cost before comparing services."
        )

    return ShippingQuote(
        order_total=round(total, 2),
        per_card=round(total / n_cards, 2),
        band_verified_on=band["verified_on"],
        includes_outbound=includes_outbound,
        notes=notes,
    )


def turnaround_calendar_days(fees: dict, service: str,
                             business_days_per_week: int = 5) -> int | None:
    """Business days -> calendar days. Returns None when unverified.

    Beckett quotes business days and excludes transit; PSA's own figure is not
    yet pinned. Comparing a business-day quote to a calendar-day window is how a
    November deadline gets missed on paper and in the mail.
    """
    t = fees["grading_services"][service].get("turnaround") or {}
    value, unit = t.get("value"), t.get("unit")
    if value is None:
        return None
    if unit == "calendar_days":
        return int(value)
    if unit == "business_days":
        weeks = value / business_days_per_week
        return int(round(weeks * 7))
    raise ShippingUnverified(f"{service}: unrecognized turnaround unit {unit!r}")
