"""shipping.py — drop-in for evcalc.py.

Replaces tier["roundtrip_ship_insured"], a per-card scalar that was wrong twice
over: both graders bill shipping PER ORDER, and both scale it with declared
value. Verified July 9, 2026 against the PSA and Beckett submission wizards.

Shipping hangs off the SERVICE, not the tier: Beckett quoted identical insurance
and return at Base and Standard, so the band keys on declared value alone.

Contract: resolve_order_shipping returns the order total and its per-card share,
or raises ShippingUnverified. Resolve or refuse.
"""

from dataclasses import dataclass


class ShippingUnverified(Exception):
    """No verified band covers this order. Feeds REFUSED; never a silent guess."""


@dataclass(frozen=True)
class ShippingQuote:
    order_total: float
    per_card: float
    verified_on: str
    includes_outbound: bool
    note: str = ""


def resolve_order_shipping(fees, service, n_cards, declared_value_total):
    svc = (fees.get("grading_services") or {}).get(service)
    if svc is None:
        raise ShippingUnverified(f"unknown grading service: {service!r}")

    shipping = svc.get("shipping") or {}
    bands = shipping.get("bands") or []
    if not bands:
        raise ShippingUnverified(f"grading_services.{service}.shipping.bands: empty")

    if declared_value_total is None:
        raise ShippingUnverified(
            "declared_value: missing. PSA and BGS both price shipping off it; "
            "it is your estimate of the card's value AFTER grading."
        )

    covering = [
        b for b in bands
        if n_cards <= b["n_cards_max"]
        and declared_value_total <= b["declared_value_total_max"]
    ]
    if not covering:
        raise ShippingUnverified(
            f"grading_services.{service}.shipping: no verified band covers "
            f"{n_cards} card(s) at ${declared_value_total:,.2f} declared value. "
            "Re-quote in the submission wizard and add the band, with its date."
        )

    # Narrowest covering band wins: a $250 order must not inherit a $5,000 rate.
    band = min(covering, key=lambda b: (b["n_cards_max"], b["declared_value_total_max"]))
    includes_outbound = bool(shipping.get("inbound_paid_to_service"))

    if includes_outbound:
        total = band.get("order_total")
        note = ""
    else:
        total = band.get("order_total_excl_outbound")
        note = "EXCLUDES outbound; add your insured cost to ship to the grader."

    if total is None:
        raise ShippingUnverified(
            f"grading_services.{service}.shipping: band matched but its total is null"
        )

    return ShippingQuote(
        order_total=round(float(total), 2),
        per_card=round(float(total) / n_cards, 2),
        verified_on=band.get("verified_on") or "",
        includes_outbound=includes_outbound,
        note=note,
    )


def turnaround_calendar_days(fees, service, tier_name, business_days_per_week=5):
    """Total calendar days the card is gone: grading + round-trip transit.

    Comparing Beckett's business-day quote to a calendar-date sale window is how
    a November deadline gets missed on paper and then in the mail.
    Returns (calendar_days, problems).
    """
    problems = []
    svc = (fees.get("grading_services") or {}).get(service) or {}
    tier = (svc.get("tiers") or {}).get(tier_name) or {}
    shipping = svc.get("shipping") or {}

    value = tier.get("turnaround_days")
    unit = tier.get("turnaround_unit", "calendar_days")
    if value is None:
        problems.append(
            f"grading_services.{service}.tiers.{tier_name}.turnaround_days: "
            "not filled (read the live estimate off the wizard)"
        )
        return None, problems

    if unit == "calendar_days":
        grading_days = int(value)
    elif unit == "business_days":
        grading_days = int(round(value / business_days_per_week * 7))
    else:
        problems.append(f"turnaround_unit: unrecognized {unit!r}")
        return None, problems

    transit = shipping.get("transit_days_roundtrip")
    if transit is None:
        problems.append(
            f"grading_services.{service}.shipping.transit_days_roundtrip: "
            "not filled; this tier excludes transit, so the window math is wrong without it"
        )
        return None, problems

    return grading_days + int(transit), problems
