mkdir -p rnv-evcalc/cards && cd rnv-evcalc && cat > evcalc.py << 'PYEOF'
#!/usr/bin/env python3
"""RNV grading EV calculator — grade vs. sell raw, on net proceeds.

Resolve or refuse, never guess: stale or missing inputs return a
verification checklist, not a verdict. Spec: SPEC_grading_ev_calculator.md.

Usage:
    python3 evcalc.py cards/my-card.json
    python3 evcalc.py --batch cards/
    python3 evcalc.py --fees other_fees.json cards/my-card.json
"""

import argparse
import csv
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

GRADES = ("10", "9", "8", "le7")


def load_json(path):
    with open(path) as f:
        return json.load(f)


def parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def check_priced(node, label, staleness_days, today, problems):
    """A price node needs value + as_of, and as_of must be fresh."""
    if not isinstance(node, dict) or node.get("value") is None:
        problems.append(f"{label}: missing value")
        return None
    as_of = node.get("as_of")
    if not as_of:
        problems.append(f"{label}: missing as_of date")
        return None
    age = (today - parse_date(as_of)).days
    if age > staleness_days:
        problems.append(f"{label}: comp is {age} days old (window {staleness_days}); re-verify")
    return float(node["value"])


def check_fee_block(block, label, staleness_days, today, required, problems):
    if block is None:
        problems.append(f"{label}: missing from fees config")
        return
    for key in required:
        if block.get(key) is None:
            problems.append(f"{label}.{key}: not filled (enter a current, dated value)")
    v = block.get("verified_on")
    if v:
        age = (today - parse_date(v)).days
        if age > staleness_days:
            problems.append(f"{label}: verified {age} days ago (window {staleness_days}); re-verify")
    else:
        problems.append(f"{label}.verified_on: missing")


def evaluate(card, fees, today=None):
    today = today or date.today()
    cfg_stale = fees.get("staleness_days", 30)
    problems = []

    raw = check_priced(card.get("raw_price"), "raw_price", cfg_stale, today, problems)

    graded_prices = {}
    for g in ("10", "9", "8"):
        node = (card.get("graded_prices") or {}).get(g)
        graded_prices[g] = check_priced(node, f"graded_prices.{g}", cfg_stale, today, problems)
    graded_prices["le7"] = check_priced(
        card.get("bulk_le7_value"), "bulk_le7_value", cfg_stale, today, problems
    )

    probs = card.get("grade_probs") or {}
    p = {g: float(probs.get(g, 0)) for g in GRADES}
    if abs(sum(p.values()) - 1.0) > 0.01:
        problems.append(f"grade_probs: sum to {sum(p.values()):.3f}, need 1.0")

    mkt = fees.get("marketplaces", {})
    mk_raw = mkt.get(card.get("marketplace_raw"))
    mk_graded = mkt.get(card.get("marketplace_graded"))
    check_fee_block(mk_raw, f"marketplaces.{card.get('marketplace_raw')}", cfg_stale, today, ("pct", "flat"), problems)
    check_fee_block(mk_graded, f"marketplaces.{card.get('marketplace_graded')}", cfg_stale, today, ("pct", "flat"), problems)

    svc = (card.get("grading") or {}).get("service")
    tier_name = (card.get("grading") or {}).get("tier")
    tier = ((fees.get("grading_services", {}).get(svc) or {}).get("tiers", {}) or {}).get(tier_name)
    check_fee_block(
        tier, f"grading_services.{svc}.tiers.{tier_name}", cfg_stale, today,
        ("fee", "roundtrip_ship_insured", "turnaround_days"), problems,
    )

    if problems:
        return {"verdict": "REFUSED", "problems": problems, "card": card.get("name", "?")}

    ship_raw = float(card.get("ship_raw_seller", 0))
    ship_graded = float(card.get("ship_graded_seller", 0))

    net_raw = raw * (1 - mk_raw["pct"]) - mk_raw["flat"] - ship_raw
    if net_raw <= 0:
        return {"verdict": "REFUSED", "problems": [f"net_raw is {net_raw:.2f}; nothing to compare"], "card": card.get("name", "?")}

    ev_gross = sum(p[g] * graded_prices[g] for g in GRADES)
    grading_cost = tier["fee"] + tier["roundtrip_ship_insured"]
    net_graded = ev_gross * (1 - mk_graded["pct"]) - mk_graded["flat"] - grading_cost - ship_graded

    th = fees["thresholds"]
    delta = net_graded - net_raw
    rel = delta / net_raw
    passes = rel >= th["rel_uplift"] and delta >= th["abs_floor"]
    verdict = "GRADE" if passes else "RAW"

    A = 1 - mk_graded["pct"]
    C = mk_graded["flat"] + grading_cost + ship_graded
    non10 = 1 - p["10"]
    W = (
        sum(p[g] * graded_prices[g] for g in ("9", "8", "le7")) / non10
        if non10 > 0 else 0.0
    )
    D_star = max(th["rel_uplift"] * net_raw, th["abs_floor"])
    P10 = graded_prices["10"]
    if A * (P10 - W) <= 0:
        breakeven = None
    else:
        breakeven = (net_raw + D_star + C - A * W) / (A * (P10 - W))

    p10_half = p["10"] / 2
    scale = (1 - p10_half) / non10 if non10 > 0 else 0
    p_frag = {"10": p10_half, **{g: p[g] * scale for g in ("9", "8", "le7")}}
    ev_frag = sum(p_frag[g] * graded_prices[g] for g in GRADES)
    net_frag = ev_frag * A - C
    delta_frag = net_frag - net_raw
    frag_passes = (delta_frag / net_raw) >= th["rel_uplift"] and delta_frag >= th["abs_floor"]
    fragile = passes and not frag_passes

    lock_days = int(tier["turnaround_days"]) + int(fees.get("relist_buffer_days", 7))
    timing = ""
    if card.get("sale_window"):
        back_by = today + timedelta(days=lock_days)
        window = parse_date(card["sale_window"])
        if verdict == "GRADE" and back_by > window:
            timing = (
                f"grading returns ~{back_by} — after the {window} window. "
                f"Choose: RAW-NOW (sell inside the window) or GRADE-AND-HOLD (sell after)."
            )
        elif verdict == "GRADE":
            timing = f"grading returns ~{back_by}, inside the {window} window."

    return {
        "verdict": verdict,
        "card": card.get("name", "?"),
        "net_raw": round(net_raw, 2),
        "ev_graded_gross": round(ev_gross, 2),
        "net_graded": round(net_graded, 2),
        "delta": round(delta, 2),
        "rel": round(rel, 4),
        "breakeven_p10": None if breakeven is None else round(breakeven, 3),
        "current_p10": p["10"],
        "fragile": fragile,
        "capital_lock_days": lock_days,
        "timing": timing,
        "as_of_oldest": min(
            n["as_of"]
            for n in [card["raw_price"], card["bulk_le7_value"], *card["graded_prices"].values()]
        ),
    }


def print_report(r):
    print(f"\n=== {r['card']} ===")
    if r["verdict"] == "REFUSED":
        print("VERDICT: REFUSED — verify before deciding:")
        for pr in r["problems"]:
            print(f"  - {pr}")
        return
    print(f"net raw            ${r['net_raw']}")
    print(f"EV graded (gross)  ${r['ev_graded_gross']}")
    print(f"net graded         ${r['net_graded']}")
    print(f"uplift             ${r['delta']}  ({r['rel'] * 100:.1f}%)")
    print(f"VERDICT: {r['verdict']}" + ("  [FRAGILE — halved PSA-10 odds flip it]" if r["fragile"] else ""))
    if r["breakeven_p10"] is not None:
        if r["breakeven_p10"] > 1:
            print("break-even PSA-10: >100% — never grades at these prices")
        else:
            print(
                f"break-even PSA-10: {r['breakeven_p10'] * 100:.0f}% "
                f"(your estimate: {r['current_p10'] * 100:.0f}%)"
            )
    print(f"capital locked     ~{r['capital_lock_days']} days")
    if r["timing"]:
        print(f"timing             {r['timing']}")
    print(f"oldest comp        {r['as_of_oldest']}")


def main():
    ap = argparse.ArgumentParser(description="Grade vs. raw, on net proceeds.")
    ap.add_argument("target", help="card JSON, or a directory with --batch")
    ap.add_argument("--batch", action="store_true")
    ap.add_argument("--fees", default="fees.json")
    args = ap.parse_args()

    fees = load_json(args.fees)

    if args.batch:
        rows = []
        for f in sorted(Path(args.target).glob("[!_]*.json")):
            r = evaluate(load_json(f), fees)
            print_report(r)
            rows.append(r)
        decided = [r for r in rows if r["verdict"] != "REFUSED"]
        decided.sort(key=lambda r: r["delta"], reverse=True)
        out = Path("batch_report.csv")
        if decided:
            with out.open("w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(decided[0].keys()))
                w.writeheader()
                w.writerows(decided)
            print(f"\nbatch report → {out} ({len(decided)} decided, "
                  f"{len(rows) - len(decided)} refused)")
    else:
        print_report(evaluate(load_json(args.target), fees))


if __name__ == "__main__":
    main()
PYEOF
cat > fees.json << 'FEESEOF'
{
  "_note": "No number enters this file without a verified_on date. Nulls refuse.",
  "thresholds": { "rel_uplift": 0.15, "abs_floor": 25 },
  "staleness_days": 30,
  "relist_buffer_days": 7,
  "marketplaces": {
    "ebay": { "pct": null, "flat": null, "verified_on": null }
  },
  "grading_services": {
    "PSA": {
      "tiers": {
        "value": {
          "fee": null,
          "max_declared_value": null,
          "roundtrip_ship_insured": null,
          "turnaround_days": null,
          "verified_on": null
        }
      }
    }
  }
}
FEESEOF
cat > cards/_example.json << 'CARDEOF'
{
  "name": "EXAMPLE — copy me, fill with real comps",
  "set": "Example Set",
  "number": "EX-001",
  "condition_notes": "pack fresh, centering slightly right",
  "raw_price":     { "value": 120, "source_url": "https://ebay/sold/...", "as_of": "2026-07-05" },
  "graded_prices": {
    "10": { "value": 420, "source_url": "https://ebay/sold/...", "as_of": "2026-07-05" },
    "9":  { "value": 175, "source_url": "https://ebay/sold/...", "as_of": "2026-07-05" },
    "8":  { "value": 110, "source_url": "https://ebay/sold/...", "as_of": "2026-07-04" }
  },
  "bulk_le7_value": { "value": 80, "as_of": "2026-07-04" },
  "grade_probs": { "10": 0.25, "9": 0.55, "8": 0.15, "le7": 0.05 },
  "ship_raw_seller": 5,
  "ship_graded_seller": 8,
  "sale_window": "2026-12-20",
  "marketplace_raw": "ebay",
  "marketplace_graded": "ebay",
  "grading": { "service": "PSA", "tier": "value" }
}
CARDEOF
python3 evcalc.py cards/_example.json
