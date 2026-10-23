#!/usr/bin/env python3
"""Build deploy_output/places_points.json: place / county points for the site map.

Stdlib only; streams the 1.5GB policy DB with the csv module (never read whole).
Locations are extracted from the `title` column only, high precision over recall:

  (a) "City, ST" / "City, State"  -> US Census Gazetteer place file, exact match on
      (state, normalized name without ' city'/' town'/' CDP'/... suffix).
      Several places with the same normalized name in one state -> skipped (ambiguous).
  (b) "<Name> County|Parish|Borough" + a state -> Gazetteer county centroid.
      State comes from ", ST"/", State" right after the county name; else from a title
      segment that is exactly a state name ("...: Kentucky; Jefferson County ...");
      else from `jurisdiction` when it is a state (helper imported from build_map_data).

Not matched on purpose: "State of X", bare state names, bare "Washington", rivers,
federal agency names, all-caps titles, plural "Counties".

Inputs:
  data/output/policy_db_20260415_483k.csv   (policy_id, title, jurisdiction, source_family, policy_topic)
  data/geo/2023_Gaz_place_national.txt      (tab-separated; USPS, NAME, INTPTLAT, INTPTLONG)
  data/geo/2023_Gaz_counties_national.txt
Output:
  deploy_output/places_points.json

Usage: python3 scripts/build_places_data.py [--audit N] [--seed S]
"""
import argparse
import csv
import json
import random
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_map_data import STATES, classify  # noqa: E402  (jurisdiction -> state helper)

DB_PATH = REPO / "data" / "output" / "policy_db_20260415_483k.csv"
GEO_DIR = REPO / "data" / "geo"
PLACE_FILE = "2023_Gaz_place_national.txt"
COUNTY_FILE = "2023_Gaz_counties_national.txt"
OUT = REPO / "deploy_output" / "places_points.json"

TOP_TOPICS = 3
N_SAMPLES = 3
TITLE_MAX = 90

STATE_NAME_TO_CODE = {v.lower(): k for k, v in STATES.items()}
STATE_NAMES_DESC = sorted(STATES.values(), key=len, reverse=True)
STATE_ALT = "|".join(re.escape(n) for n in STATE_NAMES_DESC)

# ---------------------------------------------------------------- normalization

def norm(s):
    """Accent-insensitive, punctuation/space-free key; St./Saint, Ft./Fort, Mt./Mount unified."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("’", "'").replace(".", "").replace("'", "")
    s = re.sub(r"\bsaint\b", "st", s)
    s = re.sub(r"\bfort\b", "ft", s)
    s = re.sub(r"\bmount\b", "mt", s)
    return re.sub(r"[^a-z0-9]", "", s)


PLACE_SUFFIX = re.compile(
    r"\s+(?:city and borough|metropolitan government|metro government|consolidated government"
    r"|unified government|urban county|metro township|municipality|CDP|city|town|village"
    r"|borough|township|plantation|comunidad|zona urbana|urbana|government|corporation)$"
)
# Consolidated governments whose everyday name differs from the Gazetteer name.
ALIASES = {
    "Nashville-Davidson": "Nashville",
    "Louisville/Jefferson County": "Louisville",
    "Lexington-Fayette": "Lexington",
    "Athens-Clarke County": "Athens",
    "Augusta-Richmond County": "Augusta",
    "Macon-Bibb County": "Macon",
    "Butte-Silver Bow": "Butte",
    "Urban Honolulu": "Honolulu",
    "Boise City": "Boise",
    "Lynchburg, Moore County": "Lynchburg",
}


def strip_place_suffix(name):
    name = re.sub(r"\s*\(balance\)$", "", name.strip())
    prev = None
    while prev != name:  # e.g. "... metropolitan government (balance)"
        prev = name
        name = PLACE_SUFFIX.sub("", name)
    return name.strip()


# ---------------------------------------------------------------- gazetteer

def read_gazetteer(path):
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.reader(f, delimiter="\t")
        header = [h.strip() for h in next(r)]
        ix = {h: i for i, h in enumerate(header)}
        need = {"USPS", "GEOID", "NAME", "INTPTLAT", "INTPTLONG"}
        if need - set(ix):
            sys.exit("%s missing columns %s (has %s)" % (path, sorted(need - set(ix)), header))
        for row in r:
            if len(row) < len(header):
                continue
            row = [c.strip() for c in row]
            try:
                lat, lon = float(row[ix["INTPTLAT"]]), float(row[ix["INTPTLONG"]])
            except ValueError:
                continue
            yield row[ix["USPS"]], row[ix["GEOID"]], row[ix["NAME"]], lat, lon


def load_places(path):
    """(state, norm) -> {geoid: (display_name, lat, lon)}"""
    idx = defaultdict(dict)
    for st, geoid, name, lat, lon in read_gazetteer(path):
        if st not in STATES:  # drops PR and the like
            continue
        base = strip_place_suffix(name)
        forms = {base}
        if base in ALIASES:
            forms.add(ALIASES[base])
        for form in forms:
            idx[(st, norm(form))][geoid] = (ALIASES.get(base, base), lat, lon)
    return idx


def load_counties(path):
    """(state, norm(full name)) -> (display_name_without_suffix, lat, lon)"""
    idx = {}
    for st, geoid, name, lat, lon in read_gazetteer(path):
        if st not in STATES:
            continue
        short = re.sub(r"\s+(County|Parish|Borough)$", "", name)
        idx[(st, norm(name))] = (short, lat, lon, name)
    return idx


# ---------------------------------------------------------------- extraction

TAIL_RE = re.compile(
    r",\s+(?:(?P<code>[A-Z]{2})(?![\w-])|(?P<name>%s)(?!\w))" % STATE_ALT
)
DELIM_RE = re.compile(r"[;:,()\[\]“”\"]|\s[-–—]+\s*|[-–—]+\s+")
CONNECTORS = {
    "in", "at", "near", "of", "for", "and", "or", "to", "from", "between", "over",
    "off", "by", "on", "the", "within", "along", "around", "with",
}
LEAD_RE = re.compile(r"^(?:the\s+)?(?:(?:city|town|village|borough|township)\s+of\s+)?", re.I)
CAPTOK = re.compile(r"^[A-Z][A-Za-z.'’\-]*$")
COUNTY_JOIN = {"of", "the", "de", "la", "du", "del", "des"}


def is_allcaps(title):
    letters = [c for c in title if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) / len(letters) >= 0.6


def tail_state(m):
    """State code for a TAIL_RE match, or None. Same-name-as-state places are kept."""
    if m.group("code"):
        code = m.group("code")
        return code if code in STATES else None
    return STATE_NAME_TO_CODE[m.group("name").lower()]


def candidates(segment):
    """Place-name candidates for a segment: the segment itself, then the text after each
    lowercase connector word ('in', 'of', 'and', ...), longest first."""
    toks = segment.split()
    out = []
    starts = [0] + [i + 1 for i, t in enumerate(toks) if t in CONNECTORS]
    for s in starts:
        cand = " ".join(toks[s:])
        cand = LEAD_RE.sub("", cand).strip()
        if not cand or not cand[0].isupper() or len(cand.split()) > 6:
            continue
        if cand not in out:
            out.append(cand)
    return out


PLURAL_COUNTY_RE = re.compile(r"\b(?:Counties|Parishes)\b")


def is_county_name(counties, st, cand):
    k = norm(cand)
    return any((st, k + s) in counties for s in ("county", "parish", "borough"))


def find_places(title, places, counties, amb):
    """Yield (state, name, lat, lon) for each ', ST' / ', State' tail whose preceding
    segment is an exact, unambiguous Gazetteer place."""
    # 'Counties of Bronx, Nassau, New York' / 'Lehigh and Northampton, PA': in titles that
    # list counties, a name that is also a county of that state is read as the county.
    plural = bool(PLURAL_COUNTY_RE.search(title))
    for m in TAIL_RE.finditer(title):
        st = tail_state(m)
        if st is None:
            continue
        seg = DELIM_RE.split(title[:m.start()])[-1].strip()
        if not seg:
            continue
        by_name = m.group("name") is not None
        for cand in candidates(seg):
            key = norm(cand)
            # 'Delaware, Ohio' in a list of states: a place named like a state, followed by a
            # full state name, is too often just a state list -> only same-name pairs survive.
            if by_name and key in STATE_NORM and key != norm(m.group("name")):
                continue
            ents = places.get((st, key))
            if not ents:
                continue
            if plural and is_county_name(counties, st, cand):
                continue
            if len(ents) > 1:
                amb[0] += 1
                continue
            (name, lat, lon), = ents.values()
            yield st, name, lat, lon
            break


STATE_NORM = {norm(n) for n in STATES.values()}

COUNTY_SUFFIX_RE = re.compile(r"\b(County|Parish|Borough)\b")
COUNTY_AFTER_RE = re.compile(
    r"^,\s+(?:(?P<code>[A-Z]{2})(?![\w-])|(?P<name>%s)(?!\w))" % STATE_ALT
)
STATE_SEG_RE = re.compile(r"^(?:(?:State|Commonwealth) of )?(%s)$" % STATE_ALT, re.I)


def segment_state(title):
    """State if exactly one ';'/':'-delimited segment of the title is a bare state name."""
    found = set()
    for seg in re.split(r"[;:]", title):
        m = STATE_SEG_RE.match(seg.strip())
        if m:
            found.add(STATE_NAME_TO_CODE[m.group(1).lower()])
    return found.pop() if len(found) == 1 else None


def find_counties(title, counties, jur_state):
    seg_state = None
    seg_done = False
    for m in COUNTY_SUFFIX_RE.finditer(title):
        pre = title[:m.start()]
        if not pre.endswith(" "):
            continue
        words = pre.split(" ")[:-1]
        coll = []
        i = len(words) - 1
        while i >= 0 and len(coll) < 5:
            w = words[i]
            if CAPTOK.match(w):
                coll.append(w)
            elif w in COUNTY_JOIN and coll:
                coll.append(w)
            else:
                break
            i -= 1
        coll.reverse()
        while coll and coll[0] in COUNTY_JOIN:
            coll.pop(0)
        if not coll:
            continue
        # state: immediately after, else exact state segment, else jurisdiction state
        am = COUNTY_AFTER_RE.match(title[m.end():])
        if am:
            st = am.group("code") or STATE_NAME_TO_CODE[am.group("name").lower()]
            if st not in STATES:
                continue
        else:
            if not seg_done:
                seg_state, seg_done = segment_state(title), True
            st = seg_state or jur_state
            if not st:
                continue
        for k in range(len(coll), 0, -1):
            if coll[len(coll) - k] in COUNTY_JOIN:
                continue
            full = " ".join(coll[len(coll) - k:]) + " " + m.group(1)
            ent = counties.get((st, norm(full)))
            if ent:
                yield st, ent[0], ent[1], ent[2], ent[3]
                break


# ---------------------------------------------------------------- main

def clip(title):
    title = " ".join(title.split())
    return title if len(title) <= TITLE_MAX else title[: TITLE_MAX - 1].rstrip() + "…"


def new_point(kind, name, st, lat, lon):
    return {"kind": kind, "name": name, "state": st, "lat": round(lat, 4), "lon": round(lon, 4),
            "n": 0, "n_federal": 0, "n_state": 0, "topics": Counter(), "samples": [], "_titles": set()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", type=int, default=0, help="print N random matched pairs")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    csv.field_size_limit(sys.maxsize)
    places = load_places(GEO_DIR / PLACE_FILE)
    counties = load_counties(GEO_DIR / COUNTY_FILE)
    print("gazetteer: %d place keys, %d county keys" % (len(places), len(counties)))

    points = {}
    seen = set()
    amb = [0]
    rows = matched_place = matched_county = matched_any = 0
    by_family = Counter()
    rng = random.Random(args.seed)
    audit, audit_seen = [], 0

    with open(DB_PATH, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        need = {"policy_id", "title", "jurisdiction", "source_family", "policy_topic"}
        missing = need - set(r.fieldnames or [])
        if missing:
            sys.exit("policy db missing columns: %s" % sorted(missing))
        for row in r:
            pid = row["policy_id"]
            if pid in seen:
                continue
            seen.add(pid)
            rows += 1
            title = (row["title"] or "").replace("’", "'").strip()
            if not title or is_allcaps(title):
                continue

            hits = {}
            if ", " in title:
                for st, name, lat, lon in find_places(title, places, counties, amb):
                    hits[("place", name, st)] = (lat, lon)
            if "County" in title or "Parish" in title or "Borough" in title:
                bucket, _ = classify(row["jurisdiction"])
                jur_state = bucket if bucket in STATES else None
                for st, name, lat, lon, full in find_counties(title, counties, jur_state):
                    hits[("county", name, st)] = (lat, lon)
            if not hits:
                continue

            matched_any += 1
            kinds = {k[0] for k in hits}
            matched_place += "place" in kinds
            matched_county += "county" in kinds
            fam = row["source_family"] or "(blank)"
            by_family[fam] += 1
            fed = fam.startswith("Federal")
            topic = (row["policy_topic"] or "").strip()
            short = clip(title)
            for (kind, name, st), (lat, lon) in hits.items():
                p = points.get((kind, name, st))
                if p is None:
                    p = points[(kind, name, st)] = new_point(kind, name, st, lat, lon)
                p["n"] += 1
                p["n_federal" if fed else "n_state"] += 1
                if topic:
                    p["topics"][topic] += 1
                if len(p["samples"]) < N_SAMPLES and short not in p["_titles"]:
                    p["_titles"].add(short)
                    p["samples"].append({"id": pid, "title": short})
                if args.audit:
                    audit_seen += 1
                    if len(audit) < args.audit:
                        audit.append((title, kind, name, st))
                    else:
                        j = rng.randrange(audit_seen)
                        if j < args.audit:
                            audit[j] = (title, kind, name, st)

    out_points = []
    for p in sorted(points.values(), key=lambda p: (-p["n"], p["kind"], p["state"], p["name"])):
        p["topics"] = [[t, n] for t, n in p["topics"].most_common(TOP_TOPICS)]
        del p["_titles"]
        out_points.append(p)

    doc = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": {"place_file": PLACE_FILE, "county_file": COUNTY_FILE},
        "stats": {
            "rows": rows,
            "matched_place": matched_place,
            "matched_county": matched_county,
            "matched_any": matched_any,
            "by_source_family": dict(by_family.most_common()),
        },
        "points": out_points,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    size = OUT.stat().st_size
    print("rows=%d matched_place=%d matched_county=%d matched_any=%d ambiguous_skipped=%d"
          % (rows, matched_place, matched_county, matched_any, amb[0]))
    print("points=%d (place=%d county=%d) size=%.2f MB -> %s" % (
        len(out_points), sum(p["kind"] == "place" for p in out_points),
        sum(p["kind"] == "county" for p in out_points), size / 1e6, OUT))
    print("by_source_family: %s" % dict(by_family))
    for p in out_points[:10]:
        print("  top: %-28s %s %-6s n=%d fed=%d state=%d" % (
            p["name"], p["state"], p["kind"], p["n"], p["n_federal"], p["n_state"]))
    if args.audit:
        print("--- audit sample (title -> match)")
        for title, kind, name, st in audit:
            print("[%s] %s, %s  <=  %s" % (kind, name, st, title[:160]))


if __name__ == "__main__":
    main()
