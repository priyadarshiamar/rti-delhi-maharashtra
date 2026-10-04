#!/usr/bin/env python3
"""Supplementary Delhi + Maharashtra batch dm2026q3_02: doubling each
state's sampling rate from 10% to 20% — +22 Delhi and +33 Maharashtra on
top of dm2026q3_01, drawn from the SAME frozen frame, excluding offices
already sampled.

Design note: docs/design_notes/2026-09-29_supplementary_batches.md.
Top-up allocation: proportional allocation (floor 1, cap scaled to the union size) is computed
for each state's 20% UNION, with each stratum's floor set to what
dm2026q3_01 already drew there; the new batch takes only the difference,
from unsampled offices. dm2026q3_01 is untouched.

Usage: python3 scripts/sample_supplement.py   (from the repository root)
"""

import csv
import hashlib
import json
import random
from pathlib import Path

from sample import dept_group, largest_remainder  # same rules as wave 1

BATCH_ID = "dm2026q3_02"
PRIOR_BATCH = "dm2026q3_01"
SEED = 20260930          # new batch, new seed; never reuse a seed
RAS = ["RA1", "RA2", "RA3", "RA4"]
CAP_UNION = 30       # wave-1 cap 15 scaled by the doubled union size
MIN_STRATUM_SIZE = 5
# per-state: union at a 20% rate, new = union - wave 1, legal = 30% of new
DESIGN = {
    "Delhi":       {"prefix": "DL", "n_union": 44, "n_new": 22, "n_legal_new": 7,
                    "template_version": "v3", "id_start": 23},
    "Maharashtra": {"prefix": "MH", "n_union": 66, "n_new": 33, "n_legal_new": 10,
                    "template_version": "v5_mh150", "id_start": 34},
}

ROOT = Path(__file__).resolve().parent.parent
FRAME = ROOT / "data" / "frame_dl_mh.csv"
OUT = ROOT / "out" / BATCH_ID


def main():
    rows = list(csv.DictReader(open(FRAME, encoding="utf-8")))
    frame_sha = hashlib.sha256(FRAME.read_bytes()).hexdigest()
    prior = list(csv.DictReader(open(ROOT / "out" / PRIOR_BATCH / "assignments.csv",
                                     encoding="utf-8")))
    prior_meta = json.load(open(ROOT / "out" / PRIOR_BATCH / "batch_meta.json"))
    if prior_meta["frame_sha256"] != frame_sha:
        raise SystemExit("frame changed since the prior batch; refuse to draw")

    for r in rows:
        r["dept_group"] = dept_group(r["Department"])
    rows.sort(key=lambda r: (r["State"], r["dept_group"], r["Department"]))

    rng = random.Random(SEED)
    sampled = []
    for state in sorted(DESIGN):
        d = DESIGN[state]
        srows = [r for r in rows if r["State"] == state]
        taken = {p["office_name"] for p in prior if p["state"] == state}

        strata = {}
        for r in srows:
            strata.setdefault(r["dept_group"], []).append(r)
        pooled = {}
        for g, members in strata.items():
            key = g if len(members) >= MIN_STRATUM_SIZE else "OTHER (POOLED SMALL OFFICES)"
            pooled.setdefault(key, []).extend(members)
        strata = pooled

        group_of = {}
        for g, members in strata.items():
            for m in members:
                group_of[m["Department"]] = g
        existing = {g: 0 for g in strata}
        for p in prior:
            if p["state"] == state:
                existing[group_of[p["office_name"]]] += 1

        weights = {g: len(m) for g, m in strata.items()}
        # the cap stops any single department dominating; the pooled OTHER
        # stratum is not a department, so it is uncapped
        caps = {g: len(m) if g.startswith("OTHER") else min(CAP_UNION, len(m))
                for g, m in strata.items()}
        alloc_union = largest_remainder(weights, d["n_union"], caps, dict(existing))
        need = {g: alloc_union[g] - existing[g] for g in strata}
        assert sum(need.values()) == d["n_new"] and all(v >= 0 for v in need.values())

        picked = []
        for g in sorted(strata):
            avail = [m for m in strata[g] if m["Department"] not in taken]
            if need[g] > len(avail):
                raise SystemExit(f"{state}/{g}: need {need[g]}, only {len(avail)} unsampled")
            picked.extend(rng.sample(avail, need[g]))

        picked.sort(key=lambda r: (r["dept_group"], r["Department"]))
        rng.shuffle(picked)
        for i, r in enumerate(picked):
            r["treatment"] = "legal_salience" if i < d["n_legal_new"] else "plain"
        sampled.extend(picked)

    offset = 0
    for state in sorted(DESIGN):
        for arm in ("legal_salience", "plain"):
            cell = [r for r in sampled
                    if r["State"] == state and r["treatment"] == arm]
            rng.shuffle(cell)
            for i, r in enumerate(cell):
                r["assigned_ra"] = RAS[(offset + i) % len(RAS)]
            offset = (offset + len(cell)) % len(RAS)

    sampled.sort(key=lambda r: (r["State"], r["dept_group"], r["Department"]))
    counters = {s: DESIGN[s]["id_start"] for s in DESIGN}
    for r in sampled:
        s = r["State"]
        r["application_id"] = f"{DESIGN[s]['prefix']}-{counters[s]:03d}"
        counters[s] += 1

    fields = list(prior[0].keys())

    def to_row(r):
        base = {k: "" for k in fields}
        base.update({
            "application_id": r["application_id"], "batch_id": BATCH_ID,
            "state": r["State"], "office_name": r["Department"],
            "dept_group": r["dept_group"], "district": r["District"],
            "block": r["Block"], "portal": r["Website"],
            "portal_value_id": r["portal_value_id"],
            "treatment": r["treatment"], "assigned_ra": r["assigned_ra"],
            "template_version": DESIGN[r["State"]]["template_version"],
            "language": "en", "channel": "portal",
        })
        return base

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "worklists").mkdir(exist_ok=True)
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
        "batch_id": BATCH_ID, "seed": SEED, "prior_batch": PRIOR_BATCH,
        "states": {s: {k: v for k, v in d.items() if k != "prefix"}
                   for s, d in DESIGN.items()},
        "ras": RAS, "cap_union": CAP_UNION,
        "min_stratum_size": MIN_STRATUM_SIZE,
        "frame_csv": str(FRAME.relative_to(ROOT)), "frame_sha256": frame_sha,
        "design_note": "docs/design_notes/2026-09-29_supplementary_batches.md",
        "rationale": "oversample states with smaller online-filable universes "
                     "for credible within-state estimates; KA/TN portals "
                     "unreliable, RA capacity reallocated",
    }
    with open(OUT / "batch_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    from collections import Counter
    print(f"{len(sampled)} new | "
          f"{dict(Counter((r['State'], r['treatment']) for r in sampled))} | "
          f"RA {dict(sorted(Counter(r['assigned_ra'] for r in sampled).items()))}")


if __name__ == "__main__":
    main()
