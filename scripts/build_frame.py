#!/usr/bin/env python3
"""Build data/frame_dl_mh.csv from the raw portal scrapes.

Delhi and Maharashtra both run the central "RTI Online" software, whose
public PIO-lookup page (pio_app_details.php) carries the complete Public
Authority dropdown with no login, consent, or CAPTCHA. The raw HTML of
each page is committed unmodified under data/raw/portal_scrape_<date>/;
this script parses the MinistryId <select> options, keeps names
portal-verbatim (whitespace normalised only), joins district/block from
the project's earlier state_department classification where an office
matches, and writes one combined frame plus a provenance record.

Usage: python3 scripts/build_frame.py   (from the repository root)
"""

import csv
import hashlib
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = sorted((ROOT / "data" / "raw").glob("portal_scrape_*"))[-1]
OUT = ROOT / "data" / "frame_dl_mh.csv"
PROV = ROOT / "data" / "frame_provenance.json"
LEGACY = ROOT / "data" / "raw" / "state_department_dl_mh_legacy.csv"

SOURCES = [
    ("Delhi", "RTI_Online_Delhi", "delhi_pio_app_details.html",
     "https://rtionline.delhi.gov.in/pio_app_details.php"),
    ("Maharashtra", "RTI_Online_Maharashtra", "maharashtra_pio_app_details.html",
     "https://rtionline.maharashtra.gov.in/request/pio_app_details.php"),
]


def squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def fuzzy(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def parse_options(html: str):
    sel = re.search(r'<select[^>]*(?:name|id)="MinistryId"[^>]*>(.*?)</select>',
                    html, re.S | re.I)
    if not sel:
        raise SystemExit("MinistryId select not found")
    out = []
    for val, label in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>',
                                 sel.group(1), re.S):
        label = squash(re.sub(r"<[^>]+>", " ", label))
        if label and "--Select--" not in label and val.strip():
            out.append((val.strip(), label))
    return out


def main():
    legacy = {}
    if LEGACY.exists():
        for r in csv.DictReader(open(LEGACY, encoding="utf-8")):
            legacy[(r["State"], fuzzy(r["Department"]))] = r

    counts, matched_total = {}, 0
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["State", "Department", "Website",
                                          "District", "Block", "portal_value_id"])
        w.writeheader()
        for state, website, fname, _url in SOURCES:
            html = (RAW_DIR / fname).read_text(encoding="utf-8", errors="replace")
            opts = parse_options(html)
            seen = set()
            n = 0
            for val, name in sorted(opts, key=lambda x: x[1]):
                if fuzzy(name) in seen:
                    continue
                seen.add(fuzzy(name))
                old = legacy.get((state, fuzzy(name)))
                if old:
                    matched_total += 1
                w.writerow({
                    "State": state, "Department": name, "Website": website,
                    "District": old["District"] if old else "",
                    "Block": old["Block"] if old else "",
                    "portal_value_id": val,
                })
                n += 1
            counts[state] = n

    prov = {
        "scraped_on": RAW_DIR.name.replace("portal_scrape_", ""),
        "built_on": str(date.today()),
        "sources": {s: u for s, _w, _f, u in SOURCES},
        "unique_offices": counts,
        "district_block_joined_from_legacy_file": matched_total,
        "frame_sha256": hashlib.sha256(OUT.read_bytes()).hexdigest(),
        "notes": [
            "names kept portal-verbatim after whitespace normalisation",
            "pio_app_details.php serves the same MinistryId dropdown the "
            "request form uses, with no login, consent gate, or CAPTCHA",
            "portal_value_id is the dropdown option value, kept for provenance",
        ],
    }
    PROV.write_text(json.dumps(prov, indent=2))
    print(f"{counts} offices ({matched_total} with legacy district) -> {OUT.name}")


if __name__ == "__main__":
    main()
