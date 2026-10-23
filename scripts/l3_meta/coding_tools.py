"""Shared helpers for the blind-coding stages of X2 and X8 (same protocol as T64: two coders, a third read of disagreements, anchors, frozen table)."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
LABELS = {"+1": 1, "1": 1, "-1": -1, "0": 0, "+0": 0, "-0": 0}


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def read_packet(folder):
    with open(Path(folder) / "coding/packets/packet.csv", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_labels(path, packet):
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if [r["packet_id"] for r in rows] != [p["packet_id"] for p in packet]:
        raise SystemExit(f"{path}: packet ids differ from the packet ({len(rows)} vs {len(packet)} rows)")
    out = {}
    for r in rows:
        lab = LABELS.get(str(r["label"]).strip())
        if lab is None:
            raise SystemExit(f"{path}: label {r['label']!r} for {r['packet_id']} is not +1, -1 or 0")
        out[r["packet_id"]] = (lab, r.get("reason", ""))
    return out


def kappa(a, b):
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((sum(x == c for x in a) / n) * (sum(y == c for y in b) / n) for c in (-1, 0, 1))
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def write_adjudication(folder, id_field, extra_fields):
    folder = Path(folder)
    packet = read_packet(folder)
    c1 = read_labels(folder / "coding/coder1/labels.csv", packet)
    c2 = read_labels(folder / "coding/coder2/labels.csv", packet)
    q = lambda s: '"' + str(s).replace('"', '""') + '"'
    lines = ["packet_id," + ",".join(extra_fields) + ",label_a,label_b"]
    n = 0
    for r in packet:
        a, b = c1[r["packet_id"]][0], c2[r["packet_id"]][0]
        if a != b:
            lo, hi = sorted((a, b))
            lines.append(r["packet_id"] + "," + ",".join(q(r[f]) for f in extra_fields) + f",{lo:+d},{hi:+d}")
            n += 1
    p = folder / "coding/adjudication_packets.csv"
    with open(p, "x", encoding="utf-8", newline="") as fh:
        fh.write("\n".join(lines) + "\n")
    return n, len(packet)


def freeze(folder, key_field, anchors_key, anchors_required, table_name):
    """Final table, report; returns report dict.  key_field: packet column that names the anchor key (e.g. series_id, policy_name)."""
    folder = Path(folder)
    packet = read_packet(folder)
    c1 = read_labels(folder / "coding/coder1/labels.csv", packet)
    c2 = read_labels(folder / "coding/coder2/labels.csv", packet)
    adj = {}
    ap = folder / "coding/adjudicator/labels.csv"
    if ap.is_file():
        for r in csv.DictReader(open(ap, newline="", encoding="utf-8")):
            adj[r["packet_id"]] = LABELS[str(r["label"]).strip()]
    rows = []
    for r in packet:
        pid = r["packet_id"]
        a, b = c1[pid][0], c2[pid][0]
        if a == b:
            final, third = a, ""
        else:
            if pid not in adj:
                raise SystemExit(f"disagreement {pid} has no third-read label")
            third = adj[pid]
            final = third if third in (a, b) else 0
        rows.append((pid, r[key_field], a, b, third, final, int(a == b)))
    anchors = {}
    for r in csv.DictReader(open(folder / "ANCHORS_V1.csv", newline="", encoding="utf-8")):
        anchors[r[anchors_key].strip().lower()] = LABELS[r["expected"].strip()]
    fin = {x[1].strip().lower(): x[5] for x in rows}
    c1d = {x[1].strip().lower(): x[2] for x in rows}
    c2d = {x[1].strip().lower(): x[3] for x in rows}
    missing = [k for k in anchors if k not in fin]
    if missing:
        raise SystemExit(f"anchors not in packet: {missing}")
    lab1, lab2 = [x[2] for x in rows], [x[3] for x in rows]
    n_ok = sum(fin[k] == v for k, v in anchors.items())
    rep = dict(n=len(rows), raw_agreement=sum(x[6] for x in rows) / len(rows), cohen_kappa=kappa(lab1, lab2), n_disagreements=sum(1 for x in rows if not x[6]),
               final_counts={str(v): sum(1 for x in rows if x[5] == v) for v in (1, -1, 0)}, coder1_counts={str(v): lab1.count(v) for v in (1, -1, 0)},
               coder2_counts={str(v): lab2.count(v) for v in (1, -1, 0)}, third_equals_neither=sum(1 for x in rows if x[4] != "" and x[4] not in (x[2], x[3])),
               anchors=dict(n=len(anchors), final_match=n_ok, coder1_match=sum(c1d[k] == v for k, v in anchors.items()), coder2_match=sum(c2d[k] == v for k, v in anchors.items()),
                            required=anchors_required, passed=bool(n_ok >= anchors_required)))
    q = lambda s: '"' + str(s).replace('"', '""') + '"'
    with open(folder / table_name, "x", encoding="utf-8", newline="") as fh:
        fh.write("packet_id,key,label_coder1,label_coder2,label_third,final,agreed_first_pass\n" + "".join(f"{x[0]},{q(x[1])},{x[2]},{x[3]},{x[4]},{x[5]},{x[6]}\n" for x in rows))
    with open(folder / "CODING_REPORT_V1.json", "x", encoding="utf-8") as fh:
        fh.write(json.dumps(rep, indent=1) + "\n")
    return rep


def read_table(path):
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[r["key"]] = int(r["final"])
    return out


def write_supplement(rec_path, number, reason, amend_rel, amend_path, files, note, root):
    utc = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    t = [f"Supplement {number} to {Path(rec_path).name} ({reason}; rule id EX-1)", f"reason: {reason}", f"file: {amend_rel}", f"sha256: {sha256_file(amend_path)}", "(unchanged)",
         f"supplement_utc: {utc}", "frozen files (pinned):"]
    t += [f"  {p} sha256 {sha256_file(Path(root) / p)}" for p in files]
    t.append(f"note: {note}")
    target = Path(rec_path).with_name(f"{Path(rec_path).stem}_S{number}.txt")
    with open(target, "x", encoding="utf-8") as fh:
        fh.write("\n".join(t) + "\n")
    return target
