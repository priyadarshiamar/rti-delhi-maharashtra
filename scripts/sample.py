#!/usr/bin/env python3
"""Seeded, reproducible draw of Delhi and Maharashtra public authorities
for RTI filing — one combined batch, sampled at 10% within each state.

Same design as rti-telangana / rti-karnataka (adapted from in-rolls/rti):
one Random(seed) drives every draw, rows are sorted by a stable key before
any randomness touches them, and the batch metadata freezes the seed, the
config, and the SHA-256 of the frame so the draw reproduces exactly.

Within each state, stratification is by department group (leading segment
of the office name); allocation is proportional with a floor of 1 and a
cap per stratum, small groups pooled into OTHER. Treatment is 70/30
plain/legal-salience within each state; RA assignment is a continuing
round-robin across the state x treatment cells so overall loads stay even.

Usage: python3 scripts/sample.py   (from the repository root)
"""

import csv
import hashlib
import json
import random
import re
from pathlib import Path

# ---- frozen batch design -------------------------------------------------
BATCH_ID = "dm2026q3_01"
SEED = 20260908          # new batch, new seed; never reuse a seed
SAMPLING_RATE = 0.10
RAS = ["RA1", "RA2", "RA3", "RA4"]
CAP_PER_STRATUM = 15
MIN_STRATUM_SIZE = 5
LEGAL_SHARE = 0.30
STATES = {"Delhi": "DL", "Maharashtra": "MH"}

ROOT = Path(__file__).resolve().parent.parent
FRAME = ROOT / "data" / "frame_dl_mh.csv"
OUT = ROOT / "out" / BATCH_ID


def dept_group(name: str) -> str:
    """Department group from an office name: text before the first comma or
    parenthesis (both states put the institution/office type first),
    lightly normalised so variants land together."""
    g = re.split(r"[(,]", name)[0]
    g = g.upper().replace("&", " AND ")
    g = re.sub(r"\s+", " ", g).strip(" .-")
    g = re.sub(r"\s+(DEPARTMENT|DEPT\.?)$", "", g)
    return g or "UNKNOWN"


def largest_remainder(weights, total, caps, floors):
    keys = sorted(weights)
    alloc = {k: floors[k] for k in keys}
    remaining = total - sum(alloc.values())
    if remaining < 0:
        raise SystemExit("floors exceed total")
    while remaining > 0:
        open_keys = [k for k in keys if alloc[k] < caps[k]]
        if not open_keys:
            raise SystemExit("caps too tight for requested n")
        wsum = sum(weights[k] for k in open_keys)
        quotas = {k: remaining * weights[k] / wsum for k in open_keys}
        gave = 0
        for k in sorted(open_keys, key=lambda k: (-quotas[k], k)):
            if remaining - gave == 0:
                break
            take = min(int(quotas[k]) or 1, caps[k] - alloc[k], remaining - gave)
            alloc[k] += take
            gave += take
        if gave == 0:
            for k in sorted(open_keys, key=lambda k: (-quotas[k], k)):
                if remaining - gave == 0:
                    break
                alloc[k] += 1
                gave += 1
        remaining -= gave
    return alloc


def draw_state(rows, n, rng):
    strata = {}
    for r in rows:
        strata.setdefault(r["dept_group"], []).append(r)
    pooled = {}
    for g, members in strata.items():
        key = g if len(members) >= MIN_STRATUM_SIZE else "OTHER (POOLED SMALL OFFICES)"
        pooled.setdefault(key, []).extend(members)
    strata = pooled
    weights = {g: len(m) for g, m in strata.items()}
    caps = {g: min(CAP_PER_STRATUM, len(m)) for g, m in strata.items()}
    floors = {g: 1 for g in strata}
    if sum(floors.values()) > n:
        # more strata than slots: drop floors, allocation is proportional only
        floors = {g: 0 for g in strata}
    alloc = largest_remainder(weights, n, caps, floors)
    sampled = []
    for g in sorted(strata):
        sampled.extend(rng.sample(strata[g], alloc[g]))
    return sampled


def main():
    rows = list(csv.DictReader(open(FRAME, encoding="utf-8")))
    frame_sha = hashlib.sha256(FRAME.read_bytes()).hexdigest()
    for r in rows:
        r["dept_group"] = dept_group(r["Department"])
    rows.sort(key=lambda r: (r["State"], r["dept_group"], r["Department"]))

    rng = random.Random(SEED)
    sampled, design = [], {}
    for state in sorted(STATES):
        srows = [r for r in rows if r["State"] == state]
        n = round(SAMPLING_RATE * len(srows))
        n_legal = round(LEGAL_SHARE * n)
        picked = draw_state(srows, n, rng)
        picked.sort(key=lambda r: (r["dept_group"], r["Department"]))
        rng.shuffle(picked)
        for i, r in enumerate(picked):
            r["treatment"] = "legal_salience" if i < n_legal else "plain"
        design[state] = {"universe": len(srows), "n": n,
                         "n_legal": n_legal, "n_plain": n - n_legal}
        sampled.extend(picked)

    # RA round-robin continuing across state x treatment cells
    offset = 0
    for state in sorted(STATES):
        for arm in ("legal_salience", "plain"):
            cell = [r for r in sampled
                    if r["State"] == state and r["treatment"] == arm]
            rng.shuffle(cell)
            for i, r in enumerate(cell):
                r["assigned_ra"] = RAS[(offset + i) % len(RAS)]
            offset = (offset + len(cell)) % len(RAS)

    sampled.sort(key=lambda r: (r["State"], r["dept_group"], r["Department"]))
    counters = {s: 0 for s in STATES}
    for r in sampled:
        counters[r["State"]] += 1
        r["application_id"] = f"{STATES[r['State']]}-{counters[r['State']]:03d}"

    fields = [
        "application_id", "batch_id", "state", "office_name", "dept_group",
        "district", "block", "portal", "portal_value_id", "treatment",
        "assigned_ra", "template_version", "language", "channel",
        "filing_outcome", "not_filed_reason", "filing_date",
        "registration_number", "fee_paid_inr", "payment_mode",
        "evidence_url", "screen_recording_url", "payment_reference",
        "pio_name", "filed_by", "due_date", "notes",
    ]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "worklists").mkdir(exist_ok=True)

    def to_row(r):
        base = {k: "" for k in fields}
        base.update({
            "application_id": r["application_id"], "batch_id": BATCH_ID,
            "state": r["State"], "office_name": r["Department"],
            "dept_group": r["dept_group"], "district": r["District"],
            "block": r["Block"], "portal": r["Website"],
            "portal_value_id": r["portal_value_id"],
            "treatment": r["treatment"], "assigned_ra": r["assigned_ra"],
            # Maharashtra's portal caps the request text at 150 words, so MH
            # rows use the condensed v4_mh150 letters; Delhi keeps v3
            "template_version": "v4_mh150" if r["State"] == "Maharashtra" else "v3",
            "language": "en", "channel": "portal",
        })
        return base

    with open(OUT / "assignments.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in sampled:
            w.writerow(to_row(r))

    for ra in RAS:
        with open(OUT / "worklists" / f"{ra}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in sampled:
                if r["assigned_ra"] == ra:
                    w.writerow(to_row(r))

    meta = {
        "batch_id": BATCH_ID, "seed": SEED, "sampling_rate": SAMPLING_RATE,
        "states": design, "ras": RAS, "cap_per_stratum": CAP_PER_STRATUM,
        "min_stratum_size": MIN_STRATUM_SIZE,
        "frame_csv": str(FRAME.relative_to(ROOT)), "frame_sha256": frame_sha,
        "frame_rows": len(rows),
        "source": "portal scrape 2026-09-07 via scripts/build_frame.py (see data/frame_provenance.json)",
        "templates": {
            "Delhi": {"plain": "plain_v3.txt",
                      "legal_salience": "legal_salience_v3.txt"},
            "Maharashtra": {"plain": "plain_v4_mh150.txt",
                            "legal_salience": "legal_salience_v4_mh150.txt",
                            "note": "portal caps request text at 150 words"},
        },
    }
    with open(OUT / "batch_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    from collections import Counter
    t = Counter(r["treatment"] for r in sampled)
    ra = Counter(r["assigned_ra"] for r in sampled)
    print(f"{len(sampled)} sampled | {design} | {dict(t)} | {dict(sorted(ra.items()))}")


if __name__ == "__main__":
    main()
