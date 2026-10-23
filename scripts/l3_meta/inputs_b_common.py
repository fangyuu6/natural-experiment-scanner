"""Shared helpers for the L3 package B input builders (stdlib only).

Used by build_covariates_v1.py, build_party_histories_v1.py, build_q2_join_v1.py and build_n1_sets_v1.py.
Nothing here touches scripts/l2_pipeline or data/l2_pipeline; the builders only read those trees.
Outputs are new, versioned files: write_new() refuses to replace a file whose bytes differ.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------------------------
# 50 states + DC.  (name, USPS, FIPS, Berry et al. state code 1-50 = alphabetical order of the 50 states)
# ---------------------------------------------------------------------------------------------
_STATES = [
    ("Alabama", "AL", "01"), ("Alaska", "AK", "02"), ("Arizona", "AZ", "04"), ("Arkansas", "AR", "05"),
    ("California", "CA", "06"), ("Colorado", "CO", "08"), ("Connecticut", "CT", "09"), ("Delaware", "DE", "10"),
    ("District of Columbia", "DC", "11"), ("Florida", "FL", "12"), ("Georgia", "GA", "13"), ("Hawaii", "HI", "15"),
    ("Idaho", "ID", "16"), ("Illinois", "IL", "17"), ("Indiana", "IN", "18"), ("Iowa", "IA", "19"),
    ("Kansas", "KS", "20"), ("Kentucky", "KY", "21"), ("Louisiana", "LA", "22"), ("Maine", "ME", "23"),
    ("Maryland", "MD", "24"), ("Massachusetts", "MA", "25"), ("Michigan", "MI", "26"), ("Minnesota", "MN", "27"),
    ("Mississippi", "MS", "28"), ("Missouri", "MO", "29"), ("Montana", "MT", "30"), ("Nebraska", "NE", "31"),
    ("Nevada", "NV", "32"), ("New Hampshire", "NH", "33"), ("New Jersey", "NJ", "34"), ("New Mexico", "NM", "35"),
    ("New York", "NY", "36"), ("North Carolina", "NC", "37"), ("North Dakota", "ND", "38"), ("Ohio", "OH", "39"),
    ("Oklahoma", "OK", "40"), ("Oregon", "OR", "41"), ("Pennsylvania", "PA", "42"), ("Rhode Island", "RI", "44"),
    ("South Carolina", "SC", "45"), ("South Dakota", "SD", "46"), ("Tennessee", "TN", "47"), ("Texas", "TX", "48"),
    ("Utah", "UT", "49"), ("Vermont", "VT", "50"), ("Virginia", "VA", "51"), ("Washington", "WA", "53"),
    ("West Virginia", "WV", "54"), ("Wisconsin", "WI", "55"), ("Wyoming", "WY", "56"),
]
STATE_NAME_TO_USPS = {n: u for n, u, f in _STATES}
USPS_TO_NAME = {u: n for n, u, f in _STATES}
USPS_TO_FIPS = {u: f for n, u, f in _STATES}
FIPS_TO_USPS = {f: u for n, u, f in _STATES}
USPS_ORDER = [u for n, u, f in _STATES]  # alphabetical by state name, DC after Delaware
_FIFTY = [(n, u) for n, u, f in _STATES if u != "DC"]
BERRY_CODE_TO_USPS = {i + 1: u for i, (n, u) in enumerate(_FIFTY)}
BERRY_CODE_TO_NAME = {i + 1: n for i, (n, u) in enumerate(_FIFTY)}


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def rel(p) -> str:
    p = Path(p)
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def write_new(path, data) -> dict:
    """Write bytes/str to a NEW file.  If the file exists with identical bytes, accept it; if it differs, refuse."""
    path = Path(path)
    if isinstance(data, str):
        data = data.encode("utf-8")
    if path.exists():
        if sha256_file(path) == sha256_bytes(data):
            return {"path": rel(path), "bytes": len(data), "sha256": sha256_bytes(data), "status": "identical_existing"}
        raise FileExistsError(f"{rel(path)} exists with different bytes; versioned outputs are never overwritten. "
                              f"Bump the version in the builder instead.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as fh:
        fh.write(data)
    return {"path": rel(path), "bytes": len(data), "sha256": sha256_bytes(data), "status": "written"}


def csv_bytes(header, rows) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    return buf.getvalue().encode("utf-8")


def json_bytes(obj) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------------------------
# minimal xlsx reader (cached values only; no formulas executed)
# ---------------------------------------------------------------------------------------------
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def _col_index(ref):
    n = 0
    for ch in re.sub(r"\d", "", ref):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def xlsx_sheets(path):
    z = zipfile.ZipFile(path)
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid2t = {r.get("Id"): r.get("Target") for r in rels}
    out = []
    for s in wb.find("m:sheets", _NS):
        t = rid2t[s.get("{%s}id" % _NS["r"])].lstrip("/")
        out.append((s.get("name"), t if t.startswith("xl/") else "xl/" + t))
    return out


def xlsx_rows(path, sheet_xml):
    z = zipfile.ZipFile(path)
    strings = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")):
            strings.append("".join(t.text or "" for t in si.iter("{%s}t" % _NS["m"])))
    root = ET.fromstring(z.read(sheet_xml))
    rows = []
    for row in root.findall("m:sheetData/m:row", _NS):
        cells = {}
        for c in row:
            idx = _col_index(c.get("r"))
            t = c.get("t")
            v = c.find("m:v", _NS)
            if t == "inlineStr":
                is_ = c.find("m:is", _NS)
                val = "".join(x.text or "" for x in is_.iter("{%s}t" % _NS["m"])) if is_ is not None else None
            elif v is None:
                val = None
            elif t == "s":
                val = strings[int(v.text)]
            else:
                val = v.text
            cells[idx] = val
        rows.append([cells.get(i) for i in range(max(cells) + 1)] if cells else [])
    return rows


def l4_manifest():
    """data/l4/MANIFEST.csv rows keyed by file_path (relative to repo root)."""
    out = {}
    with open(ROOT / "data/l4/MANIFEST.csv", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[r["file_path"]] = r
    return out
