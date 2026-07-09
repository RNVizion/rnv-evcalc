#!/usr/bin/env python3
"""intake.py — photo → triage flag → evcalc stub.

Spec: SPEC_card_intake.md. Prime directive: resolve or refuse, never guess.

This tool asserts identity (given to it, or read by a vision pass) and geometry
(measured). It NEVER asserts a price and NEVER assigns a grade.

    python3 intake.py front.jpg --name "Number 39: Utopia" --set-code YS12-EN039
    python3 intake.py front.jpg --name "..." --rarity "secret" --game ygo
    python3 intake.py front.jpg --qc-only

Exit codes: 0 ok, 2 capture refused, 3 flag=PASS (no stub written).
"""

import argparse
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

# A standard TCG card is 63 x 88 mm.
CARD_ASPECT = 88.0 / 63.0            # 1.3968...
ASPECT_TOL = 0.01                    # [confirm/fill] 1% of 1.397
EDGE_TOL   = 0.02                    # opposite edges may differ by 2%
ANGLE_TOL  = 2.0                     # corners within 2 degrees of square
CANON_W, CANON_H = 630, 880          # 10 px per mm


class CaptureRefused(Exception):
    """The photo cannot support the measurement. Ask for a better one."""


# ---------------------------------------------------------------- geometry

def load_image(path):
    im = ImageOps.exif_transpose(Image.open(path))
    return cv2.cvtColor(np.array(im.convert("RGB")), cv2.COLOR_RGB2BGR)


def find_card(img):
    """Locate the card. Returns (ordered_quad, aspect, quad_is_true).

    quad_is_true=False means we fell back to a bounding box, which cannot reveal
    keystone distortion. Centering must not be measured from such a quad.
    """
    scale = 900.0 / max(img.shape[:2])
    small = cv2.resize(img, None, fx=scale, fy=scale)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)

    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))

    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        raise CaptureRefused("no card found: is the card on a dark, matte background?")

    c = max(cnts, key=cv2.contourArea)
    frac = cv2.contourArea(c) / (small.shape[0] * small.shape[1])
    if frac < 0.15:
        raise CaptureRefused(f"card fills only {frac:.0%} of frame; move closer")
    if frac > 0.95:
        raise CaptureRefused("card fills the frame; back off so all four edges are visible")

    # The TRUE quad: four corners as they actually sit, keystone and all.
    peri = cv2.arcLength(c, True)
    approx = cv2.approxPolyDP(c, 0.02 * peri, True)
    if len(approx) == 4:
        quad = order_quad(approx.reshape(4, 2).astype(np.float32) / scale)
        true_quad = True
    else:
        quad = order_quad((cv2.boxPoints(cv2.minAreaRect(c)) / scale).astype(np.float32))
        true_quad = False

    tl, tr, br, bl = quad
    top, bottom = np.linalg.norm(tr - tl), np.linalg.norm(br - bl)
    left, right = np.linalg.norm(bl - tl), np.linalg.norm(br - tr)
    aspect = ((left + right) / 2.0) / ((top + bottom) / 2.0)
    return quad, aspect, true_quad


def qc_quad(quad):
    """Keystone check. A symmetric tilt preserves bounding aspect but destroys
    centering, so opposite edges must be equal and corners square."""
    tl, tr, br, bl = quad
    top, bottom = np.linalg.norm(tr - tl), np.linalg.norm(br - bl)
    left, right = np.linalg.norm(bl - tl), np.linalg.norm(br - tr)

    h_skew = abs(top - bottom) / max(top, bottom)
    v_skew = abs(left - right) / max(left, right)
    if max(h_skew, v_skew) > EDGE_TOL:
        raise CaptureRefused(
            f"keystone: opposite edges differ by {max(h_skew, v_skew):.1%} "
            f"(tolerance {EDGE_TOL:.0%}). The lens was tilted, not parallel. "
            "Shoot straight down. A tilted photo reports a wrong centering ratio "
            "and never says so."
        )

    for a, b, c_ in ((bl, tl, tr), (tl, tr, br), (tr, br, bl), (br, bl, tl)):
        v1, v2 = a - b, c_ - b
        cosang = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9)
        ang = np.degrees(np.arccos(np.clip(cosang, -1, 1)))
        if abs(ang - 90) > ANGLE_TOL:
            raise CaptureRefused(
                f"corner angle {ang:.1f}deg (want 90 +/- {ANGLE_TOL}). Card not flat, "
                "or lens not parallel."
            )
    return max(h_skew, v_skew)


def order_quad(pts):
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)],
                     pts[np.argmax(s)], pts[np.argmax(d)]], dtype=np.float32)


def qc_aspect(aspect):
    """The self-validating capture check. A tilted phone lies about centering."""
    err = abs(aspect - CARD_ASPECT) / CARD_ASPECT
    if err > ASPECT_TOL:
        raise CaptureRefused(
            f"aspect ratio {aspect:.3f} vs a real card's {CARD_ASPECT:.3f} "
            f"({err:.1%} off, tolerance {ASPECT_TOL:.0%}). "
            "The lens was not parallel to the card. Shoot straight down, braced, "
            "card flat. Centering measured from this photo would be wrong."
        )
    return err


def rectify(img, quad):
    dst = np.array([[0, 0], [CANON_W - 1, 0],
                    [CANON_W - 1, CANON_H - 1], [0, CANON_H - 1]], np.float32)
    M = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(img, M, (CANON_W, CANON_H))


def find_printed_frame(card):
    """The printed design frame inside the card edge.

    Centering is the border between the card's cut edge and this frame.
    Layouts differ per game, so this refuses rather than guessing when it can't
    find a confident rectangle in the expected inset band.
    """
    gray = cv2.cvtColor(card, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 50, 150)

    cnts, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        area = (w * h) / float(CANON_W * CANON_H)
        if not (0.55 <= area <= 0.95):
            continue
        if x < 2 or y < 2 or x + w > CANON_W - 2 or y + h > CANON_H - 2:
            continue                       # hugging the cut edge: that's the card, not the frame
        ar = h / float(w)
        if abs(ar - CARD_ASPECT) / CARD_ASPECT > 0.12:
            continue                       # frame should roughly echo the card
        if best is None or area > best[0]:
            best = (area, (x, y, w, h))

    if best is None:
        raise CaptureRefused(
            "printed frame not found. Per-game frame detection needs calibration "
            "[confirm/fill]; refusing rather than reporting a centering ratio "
            "measured against the wrong rectangle."
        )
    return best[1]


def centering(frame):
    x, y, w, h = frame
    left, right = x, CANON_W - (x + w)
    top, bottom = y, CANON_H - (y + h)
    lr = 100.0 * left / (left + right) if (left + right) else 50.0
    tb = 100.0 * top / (top + bottom) if (top + bottom) else 50.0
    return {
        "left_right": [round(lr), round(100 - lr)],
        "top_bottom": [round(tb), round(100 - tb)],
        "worst_axis_pct": round(max(abs(lr - 50), abs(tb - 50)) + 50),
    }


# ---------------------------------------------------------------- flagging

def load_chases(path):
    return json.loads(Path(path).read_text())


def flag_card(chases, name, set_code, rarity, extra_text=""):
    """Two independent sources. Either fires. Reasons never merged."""
    reasons = []
    blob = " ".join(filter(None, [name or "", rarity or "", extra_text or ""])).lower()

    for row in chases["chases"]:
        if set_code and set_code.upper() in [s.upper() for s in row.get("set_codes", [])]:
            reasons.append({"source": "watchlist", "match": "set_code",
                            "row": row["name"], "grade_watch": row.get("grade_watch"),
                            "verified_on": row["market_snapshot"].get("verified_on")})
            continue
        if name and row["name"].lower() in (name or "").lower():
            rar = [r.lower() for r in row.get("rarity_any_of", [])]
            if not rar or any(r in blob for r in rar):
                reasons.append({"source": "watchlist", "match": "name+rarity",
                                "row": row["name"], "grade_watch": row.get("grade_watch"),
                                "verified_on": row["market_snapshot"].get("verified_on")})

    sm = chases["structural_markers"]
    hits = [k for k in sm["rarity_keywords"] if k in blob]
    hits += [k for k in sm["other_markers"] if k in blob]
    if re.search(sm["one_of_one_regex"], blob):
        hits.append("1/1")
    elif re.search(sm["serial_number_regex"], blob):
        hits.append("serial numbered")
    if hits:
        reasons.append({"source": "structural", "match": ", ".join(sorted(set(hits)))})

    return ("LOOK" if reasons else "PASS"), reasons


# ---------------------------------------------------------------- stub

def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "card").lower()).strip("-")[:60]


def emit_stub(name, set_code, game, cent, flag, reasons, out_dir):
    note = "pack-fresh"
    if cent:
        note += (f"; centering F {cent['left_right'][0]}/{cent['left_right'][1]} L-R, "
                 f"{cent['top_bottom'][0]}/{cent['top_bottom'][1]} T-B (measured)")
    stub = {
        "name": name, "set": game, "number": set_code, "language": "EN",
        "condition_notes": note,
        "declared_value": None,
        "grading": {"service": "psa", "tier": "regular"},
        "graded_prices_service": "psa",
        "marketplace_raw": "ebay", "marketplace_graded": "ebay",
        "raw_price": {"value": None, "source_url": None, "as_of": None},
        "graded_prices": {g: {"value": None, "source_url": None, "as_of": None}
                          for g in ("10", "9", "8")},
        "bulk_le7_value": {"value": None, "as_of": None},
        "grade_probs": None,
        "ship_raw_seller": None, "ship_graded_seller": None,
        "_flag": {"verdict": flag, "reasons": reasons},
        "_centering": cent,
    }
    p = Path(out_dir) / f"{slugify(name)}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(stub, indent=2))
    return p


# ---------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser(description="Card intake: flag, measure, stub.")
    ap.add_argument("photo")
    ap.add_argument("--name", default=None)
    ap.add_argument("--set-code", default=None)
    ap.add_argument("--rarity", default="")
    ap.add_argument("--game", default="")
    ap.add_argument("--chases", default="chases.json")
    ap.add_argument("--out", default="cards/")
    ap.add_argument("--qc-only", action="store_true")
    args = ap.parse_args()

    img = load_image(args.photo)

    try:
        quad, aspect, true_quad = find_card(img)
        if not true_quad:
            raise CaptureRefused(
                "could not resolve four clean corners; keystone cannot be ruled out. "
                "Improve contrast: dark matte background, card flat, even side light."
            )
        err = qc_aspect(aspect)
        skew = qc_quad(quad)
        print(f"capture OK   aspect {aspect:.3f} ({err:.2%} off)   edge skew {skew:.2%}")
    except CaptureRefused as e:
        print(f"CAPTURE REFUSED: {e}")
        sys.exit(2)

    if args.qc_only:
        return

    cent = None
    try:
        card = rectify(img, quad)
        cent = centering(find_printed_frame(card))
        print(f"centering    L-R {cent['left_right'][0]}/{cent['left_right'][1]}   "
              f"T-B {cent['top_bottom'][0]}/{cent['top_bottom'][1]}")
    except CaptureRefused as e:
        print(f"centering    NOT MEASURED: {e}")

    if not args.name:
        print("\nno --name given; identity is the vision step. Stub not written.")
        return

    chases = load_chases(args.chases)
    flag, reasons = flag_card(chases, args.name, args.set_code, args.rarity)
    print(f"\nFLAG: {flag}")
    for r in reasons:
        if r["source"] == "watchlist":
            age = "undated" if not r.get("verified_on") else r["verified_on"]
            print(f"  - watchlist: {r['row']}  (snapshot: {age})")
            if r.get("grade_watch"):
                print(f"      {r['grade_watch']}")
        else:
            print(f"  - structural: {r['match']}")

    if flag == "PASS":
        print("\nPASS — logged, no stub. Next card.")
        sys.exit(3)

    p = emit_stub(args.name, args.set_code, args.game, cent, flag, reasons, args.out)
    print(f"\nstub → {p}   (all prices null; evcalc will refuse until you enter comps)")


if __name__ == "__main__":
    main()
