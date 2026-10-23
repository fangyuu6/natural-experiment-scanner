#!/usr/bin/env python3
"""T64-SIGNED-V1 (rule id EX-1, exploratory): signed party effect and policy-level party-slope spread on the 648-policy SPID panel.

Rules: docs/plans/l3-meta/PREREG_T64_SIGNED_PARTY_V1.md (hash record PREREG_T64_SIGNED_PARTY_V1_HASH.txt).  Stages (each output written once):
  packets    blind coding packets (policy key, description, topic only) -> data/l3_meta/t64_signed_v1/coding/packets/
  adjud      packets of the disagreements for the third read
  freeze     validate the coder files, build the final direction table + agreement/anchor report
  run        merge the frozen table with the DK panel and the state-year file, fit, write RESULTS_T64_SIGNED_PARTY_V1.json
Read-only on everything else; standard library, numpy, scipy, pandas (.venv_did).  No network.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
OUT = ROOT / "data/l3_meta/t64_signed_v1"
CODING = OUT / "coding"
DK = ROOT / "data/l3_pooled/downloads/DVKim_SPID_iqt.dta"
NATIVE = ROOT / "data/l3_pooled/downloads/SPID_v1.2_policies.tab"
SEED_PACKETS = 20261016
N_CHUNKS = 6


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def write_new(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="") as fh:
        fh.write(text)


def policy_table():
    """[(policy_key, description, topic)] for the 648 policies of the DK panel: native SPID description when present, else the DK description."""
    import pandas as pd
    d = pd.read_stata(DK, columns=["policy", "policy_description", "policyarea"]).drop_duplicates("policy")
    nat = pd.read_csv(NATIVE, sep="\t")[["policy", "description", "majortopic_str"]]
    m = d.merge(nat, on="policy", how="left")
    rows = []
    for _, r in m.iterrows():
        desc = r["description"] if isinstance(r["description"], str) and r["description"].strip() else r["policy_description"]
        topic = r["majortopic_str"] if isinstance(r["majortopic_str"], str) and r["majortopic_str"].strip() else r["policyarea"]
        rows.append((str(r["policy"]), str(desc).strip(), str(topic).strip()))
    return sorted(rows)


def stage_packets():
    rows = policy_table()
    rng = random.Random(SEED_PACKETS)
    order = list(range(len(rows)))
    rng.shuffle(order)
    ids = {rows[i][0]: f"P{k + 1:03d}" for k, i in enumerate(order)}
    ordered = [rows[i] for i in order]
    size = -(-len(ordered) // N_CHUNKS)
    key_rows = []
    for c in range(N_CHUNKS):
        chunk = ordered[c * size:(c + 1) * size]
        lines = ["packet_id,policy_key,description,topic_area"]
        for key, desc, topic in chunk:
            q = lambda s: '"' + s.replace('"', '""') + '"'
            lines.append(f"{ids[key]},{q(key)},{q(desc)},{q(topic)}")
            key_rows.append((ids[key], key))
        write_new(CODING / "packets" / f"packet_chunk_{c + 1}.csv", "\n".join(lines) + "\n")
    write_new(OUT / "PACKET_KEY_V1.csv", "packet_id,policy_key\n" + "".join(f"{a},{b}\n" for a, b in key_rows))
    print(len(rows), "policies in", N_CHUNKS, "chunks")



# ---------------------------------------------------------------------------------------------
# coding files
# ---------------------------------------------------------------------------------------------
LABELS = {"+1": 1, "1": 1, "-1": -1, "0": 0, "-0": 0, "+0": 0}


def read_packets():
    out = []
    for c in range(1, N_CHUNKS + 1):
        with open(CODING / "packets" / f"packet_chunk_{c}.csv", newline="", encoding="utf-8") as fh:
            out += [(c, r) for r in csv.DictReader(fh)]
    return out


def read_coder(folder):
    """{packet_id: (label, reason)}; every packet of every chunk must be present, once, in the packet order."""
    got = {}
    for c in range(1, N_CHUNKS + 1):
        p = Path(folder) / f"labels_chunk_{c}.csv"
        if not p.is_file():
            raise SystemExit(f"{p} is missing")
        with open(p, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        with open(CODING / "packets" / f"packet_chunk_{c}.csv", newline="", encoding="utf-8") as fh:
            want = [r["packet_id"] for r in csv.DictReader(fh)]
        if [r["packet_id"] for r in rows] != want:
            raise SystemExit(f"{p}: packet ids differ from the packet (rows {len(rows)} vs {len(want)})")
        for r in rows:
            lab = LABELS.get(str(r["label"]).strip())
            if lab is None:
                raise SystemExit(f"{p}: label {r['label']!r} for {r['packet_id']} is not +1, -1 or 0")
            got[r["packet_id"]] = (lab, r.get("reason", ""))
    return got


def kappa(a, b):
    cats = (-1, 0, 1)
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((sum(x == c for x in a) / n) * (sum(y == c for y in b) / n) for c in cats)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def stage_adjud():
    c1, c2 = read_coder(CODING / "coder1"), read_coder(CODING / "coder2")
    pk = read_packets()
    dis = [(r, c1[r["packet_id"]][0], c2[r["packet_id"]][0]) for _, r in pk if c1[r["packet_id"]][0] != c2[r["packet_id"]][0]]
    q = lambda s: '"' + s.replace('"', '""') + '"'
    lines = ["packet_id,policy_key,description,topic_area,label_a,label_b"]
    # the two labels are shown in a fixed order that does not reveal which coder gave which (sorted)
    for r, a, b in dis:
        lo, hi = sorted((a, b))
        lines.append(f"{r['packet_id']},{q(r['policy_key'])},{q(r['description'])},{q(r['topic_area'])},{lo:+d},{hi:+d}")
    write_new(CODING / "adjudication_packets.csv", "\n".join(lines) + "\n")
    print(f"{len(pk)} packets, {len(dis)} disagreements written to adjudication_packets.csv")


def stage_freeze():
    c1, c2 = read_coder(CODING / "coder1"), read_coder(CODING / "coder2")
    pk = read_packets()
    adj_path = CODING / "adjudicator" / "labels.csv"
    adj = {}
    if adj_path.is_file():
        with open(adj_path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                adj[r["packet_id"]] = LABELS[str(r["label"]).strip()]
    key = {r["packet_id"]: r["policy_key"] for _, r in pk}
    rows, changed, missing = [], 0, 0
    for _, r in pk:
        pid = r["packet_id"]
        a, b = c1[pid][0], c2[pid][0]
        if a == b:
            final, third = a, ""
        else:
            if pid not in adj:
                missing += 1
                continue
            third = adj[pid]
            final = third if third in (a, b) else 0
            changed += 1
        rows.append((pid, key[pid], a, b, third, final, int(a == b)))
    if missing:
        raise SystemExit(f"{missing} disagreements have no third-read label")
    lab1 = [x[2] for x in rows]
    lab2 = [x[3] for x in rows]
    agree = sum(x[6] for x in rows) / len(rows)
    anchors = {r["policy_key"]: int(r["expected"]) for r in csv.DictReader(open(OUT / "ANCHORS_V1.csv"))}
    fin = {x[1]: x[5] for x in rows}
    n_ok = sum(fin[k] == v for k, v in anchors.items())
    c1_ok = sum({x[1]: x[2] for x in rows}[k] == v for k, v in anchors.items())
    c2_ok = sum({x[1]: x[3] for x in rows}[k] == v for k, v in anchors.items())
    report = dict(rule_id="EX-1", version="T64-SIGNED-V1", n_policies=len(rows), raw_agreement=agree, cohen_kappa=kappa(lab1, lab2),
                  n_disagreements=sum(1 for x in rows if not x[6]), n_changed_by_third_read_or_set_to_zero=changed,
                  final_counts={str(v): sum(1 for x in rows if x[5] == v) for v in (1, -1, 0)},
                  coder1_counts={str(v): lab1.count(v) for v in (1, -1, 0)}, coder2_counts={str(v): lab2.count(v) for v in (1, -1, 0)},
                  anchors=dict(n=len(anchors), final_match=n_ok, coder1_match=c1_ok, coder2_match=c2_ok, required=27, passed=bool(n_ok >= 27)),
                  third_equals_neither=sum(1 for x in rows if x[4] != "" and x[4] not in (x[2], x[3])))
    write_new(OUT / "direction_table_v1.csv", "packet_id,policy_key,label_coder1,label_coder2,label_third,final,agreed_first_pass\n" +
              "".join(f"{x[0]},{x[1]},{x[2]},{x[3]},{x[4]},{x[5]},{x[6]}\n" for x in rows))
    write_new(OUT / "CODING_REPORT_V1.json", json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["packets", "adjud", "freeze"])
    a = ap.parse_args()
    {"packets": stage_packets, "adjud": stage_adjud, "freeze": stage_freeze}[a.stage]()
