# rnv-evcalc

Personal ops tooling for card triage. Two tools: one decides whether a card sells raw or grades first; the other turns a scan into a triage flag while a rip is live.

One rule governs both — **resolve or refuse, never guess.** Stale or missing inputs return a verification checklist, not a verdict. No price is asserted from memory; every figure in `fees.json` carries a `verified_on` date, and a comp past the staleness window refuses rather than estimates.

## Layout

| Path | What it is | Lifecycle |
|---|---|---|
| `scripts/evcalc.py` | Grading EV calculator | source |
| `scripts/intake.py` | Scan to triage flag to evcalc stub | source |
| `scripts/shipping.py` | Shipping helper; imported by `evcalc.py` | source |
| `fees.json` | Fee, grading, and threshold config | hand-edited, dated |
| `chases.json` | Watchlist consulted by intake | hand-edited |
| `cards/` | Per-card files; `_example.json` is the copy-me template | input and generated |
| `flag.json` | Runtime state; the only interface to the OBS overlay | machine-written |
| `overlay.html` | OBS browser source; polls `flag.json` | web surface |

The JSON files sit at root deliberately. They share a serializer, not a role: `fees.json` and `chases.json` are hand-edited config, `cards/` is data, `flag.json` is runtime state overwritten on every scan. Grouping by file extension would put four lifecycles in one folder and tell you nothing.

`flag.json` and `overlay.html` must stay siblings. The overlay fetches the flag as a relative URL, and OBS points at the overlay from outside this repo — a path `git mv` cannot reach.

## Run from the repo root

Every default path resolves against the working directory, not against the script. `--fees` defaults to `fees.json`, `--chases` to `chases.json`, `--out` to `cards/`, and batch runs write `batch_report.csv` into the CWD. Invoke from anywhere else and those defaults miss.

```
cd /path/to/rnv-evcalc
```

## evcalc

```
python3 scripts/evcalc.py cards/my-card.json
python3 scripts/evcalc.py --batch cards/
python3 scripts/evcalc.py --batch cards/ --order-cards 5 --order-declared-value 900
python3 scripts/evcalc.py --fees other_fees.json cards/my-card.json
```

Single-card runs print a report table: net raw, EV graded, net graded, the delta and ratio, the verdict, the break-even PSA-10 rate, a FRAGILE flag where the verdict is sensitive to an optimistic prior, capital-lock days, and the `as_of` date behind every price used. Batch mode sorts by delta descending — that is the collection triage.

A REFUSED report names the exact fields to verify and their last `as_of` dates. It is the expected output on a fresh `fees.json`, which ships all-null by design.

Spec: `SPEC_grading_ev_calculator.md`.

## intake

```
python3 scripts/intake.py front.jpg --name "Number 39: Utopia" --set-code YS12-EN039
python3 scripts/intake.py front.jpg --name "..." --rarity "secret" --game ygo
python3 scripts/intake.py front.jpg --qc-only
```

Exit codes: `0` ok, `2` capture refused, `3` flag returned PASS and no stub was written.

Intake asserts identity and measures geometry. It never asserts a price and never assigns a grade — those need comps and a human, off-stream.

Spec: `SPEC_card_intake.md`.

## The overlay contract

`flag.json` is the only interface between intake and the OBS overlay: intake writes it, the overlay polls it, neither imports the other. The contract, its state machine, and its forbidden fields are documented inside the file itself.

Nothing in that file may carry a value, a predicted grade, or a score. Cost is permitted where it comes from `fees.json` dated; worth is not. The stream VOD becomes the shop provenance link, so anything on the overlay travels to the buyer.
