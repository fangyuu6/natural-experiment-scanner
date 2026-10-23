"""L3 outcome sweep, library (rule id EX-1, exploratory; versions SWEEP-V1, D1-V1, D3-V2).

Rules: docs/plans/l3-meta/OUTCOME_SWEEP_DESIGN_20261015.md (design), PREREG_AMEND_20261015_D_QUESTIONS.md (entries D1 to D3) and the
dated build amendment PREREG_AMEND_20261015_SWEEP_V1.md (hash record PREREG_AMEND_20261015_SWEEP_HASH.txt).

What this file does.  It applies the frozen treatment table and the estimator of an already estimated L2 design to other outcome series,
reusing the L2 engine READ-ONLY (functions are imported, nothing is written under scripts/l2_pipeline or data/l2_pipeline; bytecode
writing is off).  The estimator is linear in the outcome, so for one (design, cell pattern) the ATT is `overall . y` and the ATTs of the
999 fixed label reassignments are `ri_overall . y`: the operators are built once per (design, cell pattern) and then applied to every
series that has that cell pattern.

Pieces: series store (catalog V15 artifacts through the L2 constructor, CDC WONDER exports through a small reader), design context
(a per-entry copy of the DRY-LOAD of registered.load_registration), declaration builder (support_check -> numerical_declaration, as L2
does for a secondary outcome), aligned label bundles, operators, reproduction gate, support rule of D1.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
import random
import sys
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True

RULE_ID = "EX-1"
RI_SEED = 20261002                 # the engine's fixed seed of the label reassignments (estimator.RI_SEED), checked against the engine at import
N_DRAWS = 999
TOL = 1e-8                         # reproduction tolerance: |x - y| <= TOL * max(1, |x|, |y|)
MIN_FEASIBLE_FRACTION = 0.9        # engine rule and D1 support rule
MIN_KEPT_TREATED = 0.8             # D1 support rule: share of the primary's treated states kept
MIN_COMMON_DRAWS = 900             # design-level floor of the build amendment (90% of 999)
CATALOG = ROOT / "scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V15.json"
INVENTORY_CSV = ROOT / "docs/plans/l3-meta/OUTCOME_DIMENSION_INVENTORY_20261015.csv"
LIVE = ROOT / "data/l3_meta/l2_set_live.json"
# EX-1 section 1: the first certified run of ERPO is the original v12 folder; the live set points to the timing-corrected later run
RUN_OVERRIDE = {"run8_extreme_risk_protection_order":
                "data/l2_pipeline/sources/research_panel/v8b_20261009/fresh_audit_jobs/confirmed_c10/c11/work/run8_extreme_risk_protection_order/v12"}
REGISTERED_GROUPS = ("registry_terminal", "autopilot_estimate")


# ---------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------
def rel(p):
    p = Path(p)
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        try:
            return str(p.resolve().relative_to(ROOT))
        except ValueError:
            return str(p)


def sha256_file(p, chunk=1 << 22):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def read_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def jdump(obj):
    """Canonical JSON text of a python object (numpy scalars and arrays are converted; NaN and inf are refused)."""
    return json.dumps(clean(obj), sort_keys=True, separators=(",", ":"), allow_nan=False)


def clean(obj):
    if isinstance(obj, np.ndarray):
        return clean(obj.tolist())
    if isinstance(obj, (np.floating, np.integer)):
        obj = obj.item()
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(clean(v) for v in obj)
    return obj


def agree(x, y, tol=TOL):
    """|x - y| <= tol * max(1, |x|, |y|)."""
    return abs(x - y) <= tol * max(1.0, abs(x), abs(y))


def max_scaled_diff(a, b):
    """Largest |a - b| / max(1, |a|, |b|) over two equal-length sequences (0.0 for empty input)."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if a.size == 0:
        return 0.0
    return float(np.max(np.abs(a - b) / np.maximum(1.0, np.maximum(np.abs(a), np.abs(b)))))


def mask_fingerprint(design):
    """Identity of an estimating structure: the observed cells, the units and every comparison cell of the design."""
    cells = [[c["g"], c["t"], c["base_period"], c["event_time"], bool(c["post"]), sorted(c["treated"]), sorted(c["controls"])] for c in design["cells"]]
    return hashlib.sha256(jdump({"mask": sorted([list(k) for k in design["mask"]]), "units": design["units"], "cells": cells,
                                 "group": design["control_group"]}).encode()).hexdigest()


# ---------------------------------------------------------------------------------------------
# L2 import (read-only) and hash pinning
# ---------------------------------------------------------------------------------------------
class _L2:
    loaded = False


L2 = _L2()


def load_l2():
    """Import the L2 modules this file uses.  Idempotent.  Nothing is written (bytecode is off); the files read are listed by l2_files()."""
    if L2.loaded:
        return L2
    sys.dont_write_bytecode = True
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.l2_pipeline import registered, support, variants, storage
    from scripts.l2_pipeline.contract_card import registered_execution_card
    from scripts.l2_pipeline.hygiene import inventory_names
    from scripts.l2_pipeline.stages.s3_support import variant_treatment
    from scripts.l2_pipeline.engine import estimator, ri, design, panels, structure
    from scripts.l2_pipeline.connectors import construct, ConnectorRequest
    from scripts.l2_pipeline.connectors import wonder as wonder_mod
    from scripts.l2_pipeline.connectors import rates as rates_mod
    from scripts.l2_pipeline.connectors.common import STATES
    L2.registered, L2.support, L2.variants, L2.storage = registered, support, variants, storage
    L2.registered_execution_card, L2.inventory_names, L2.variant_treatment = registered_execution_card, inventory_names, variant_treatment
    L2.estimator, L2.ri, L2.design, L2.panels, L2.structure = estimator, ri, design, panels, structure
    L2.construct, L2.ConnectorRequest, L2.wonder, L2.rates, L2.STATES = construct, ConnectorRequest, wonder_mod, rates_mod, STATES
    if estimator.RI_SEED != RI_SEED:
        raise RuntimeError("engine RI_SEED differs from the registered 20261002")
    L2.loaded = True
    return L2


def l2_files():
    """{repo-relative path: sha256} of every module under scripts/l2_pipeline that is currently loaded."""
    out = {}
    base = str((ROOT / "scripts" / "l2_pipeline").resolve())
    for m in list(sys.modules.values()):
        f = getattr(m, "__file__", None)
        if f and str(Path(f).resolve()).startswith(base) and f.endswith(".py"):
            out[rel(f)] = sha256_file(f)
    return dict(sorted(out.items()))


# ---------------------------------------------------------------------------------------------
# catalog and families
# ---------------------------------------------------------------------------------------------
def inventory_rows(path=INVENTORY_CSV):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def catalog_series_table(path=CATALOG, inv_path=INVENTORY_CSV):
    """{series id: dict(family, data_state, units, frequency, adapter, artifact, exports, value_field, registered_connector)} for the series on disk.

    On disk = data_state on_disk_parsed (88) or on_disk_exports (15) in the inventory CSV (design note section 3).
    """
    cat = read_json(path)["series"]
    out = OrderedDict()
    for r in inventory_rows(inv_path):
        if r["row_type"] != "variable" or r["layer"] != "connector_catalog" or r["data_state"] not in ("on_disk_parsed", "on_disk_exports"):
            continue
        sid = r["outcome_key"]
        s = cat[sid]
        freq = s["frequency"][0] if isinstance(s["frequency"], list) else s["frequency"]
        out[sid] = dict(family=r["family"], data_state=r["data_state"], units=s.get("units"), frequency=freq, adapter=s.get("adapter"),
                        artifact=(s.get("artifact") or {}).get("path"), exports=s.get("exports"), value_field=s.get("value_field"),
                        registered_connector=s.get("registered_connector"), years=s.get("years"), definition_break=r.get("definition_break") or "",
                        inv_adapter=r.get("adapter"), required_filters=s.get("required_filters"))
    return out


# ---------------------------------------------------------------------------------------------
# series store
# ---------------------------------------------------------------------------------------------
def strict_eligible(meta):
    """L2's own rule (engine.panels.metadata_eligible_keys) applied to a metadata record as the CSV writer would serialise it."""
    def cell(v):
        return "" if v is None else str(v)
    flags_ok = all(cell(meta.get(f, "1")) == "1" for f in ("available", "annual_usable", "linkage_certified"))
    sup_ok = all(cell(meta.get(f, "0")).strip().lower() in {"0", "false", ""} for f in ("suppression_flag", "suppressed", "unreliable"))
    return flags_ok and sup_ok


def semantic_eligible(meta):
    """The same test with True/'True'/1/'1' read as true (differs from strict_eligible only where a builder wrote Python booleans)."""
    def truth(v, default):
        if v is None:
            return default
        return str(v).strip().lower() in {"1", "true"}
    ok = all(truth(meta.get(f), True) for f in ("available", "annual_usable", "linkage_certified"))
    bad = any(truth(meta.get(f), False) for f in ("suppression_flag", "suppressed", "unreliable"))
    return ok and not bad


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def build_artifact_series(sid, spec, design_card):
    """Rows and metadata of a parsed-artifact series through the L2 constructor, on the maximal grid (all 51 units, all years of the series).

    design_card: any registered execution card (only its non-outcome fields are used by secondary_card; its outcome units and years are replaced)."""
    L = load_l2()
    cat = read_json(CATALOG)["series"][sid]
    base = copy.deepcopy(design_card)
    base["outcome"] = dict(base["outcome"])
    base["outcome"]["units"] = sorted(L.STATES.values())
    base["outcome"]["period_years"] = list(cat["years"])
    base["outcome"].pop("years", None)
    entry = {"role": "secondary", "outcome": sid, "connector": cat["registered_connector"]}
    card = L.variants.secondary_card(base, entry)
    pd = L.construct(L.ConnectorRequest.from_card(card, ROOT))
    val, ok, sem, den = {}, {}, {}, {}
    for r, m in zip(pd.rows, pd.metadata):
        key = (r["unit"], int(r["year"]))
        v = r.get("outcome_value")
        has = _finite(v)
        val[key] = float(v) if has else float("nan")
        e = strict_eligible(m) and has
        ok[key] = bool(e)
        sem[key] = bool(semantic_eligible(m) and has)
        d = r.get("population_denominator")
        den[key] = float(d) if _finite(d) else float("nan")
    return dict(val=val, ok=ok, sem=sem, den=den, source="l2_construct", units_string=cat.get("units"))


WONDER_SIMPLE = {"Deaths"}


def is_death_count_series(sid, spec_cat):
    L = load_l2()
    return spec_cat["value_field"] == "Deaths" and L.rates.count_kind({"series_id": sid, "outcome_unit": spec_cat["units"]}) == "deaths"


def build_wonder_series(sid, spec_cat, scale="rate"):
    """CDC WONDER export series (TSV on disk).  scale 'rate' (amendment SWEEP_V1 3.2): count series ('Deaths', kind deaths) become rates per 100,000
    with the Population of the same export, as engine.connectors.rates.apply_scale does; a cell is available when the published value is numeric
    (not suppressed, not 'Unreliable'), its denominator exists and the published rate is not flagged unreliable.  scale 'count' (amendment B 1.1):
    the published count itself, available when numeric.  Published-rate series keep the published value in both.  Reuses L2's parse_export."""
    L = load_l2()
    field = spec_cat["value_field"]
    count_series = is_death_count_series(sid, spec_cat)
    if scale not in ("rate", "count") or (scale == "count" and not count_series):
        raise ValueError("scale count exists only for the WONDER death-count series")
    rate_conversion = count_series and scale == "rate"
    val, ok, den = {}, {}, {}
    seen = set()
    for e in spec_cat["exports"]:
        path = ROOT / e["raw_path"]
        if sha256_file(path) != e["sha256"]:
            raise RuntimeError(f"WONDER export bytes differ from the catalog: {e['raw_path']}")
        header = next(csv.reader([path.read_text(encoding="utf-8-sig").splitlines()[0]], delimiter="\t"))
        cells = L.wonder.parse_export(path, measure=field)
        if set(cells) & seen:
            raise RuntimeError(f"overlapping WONDER vintages for {sid}")
        seen |= set(cells)
        pop = L.wonder.parse_export(path, measure="Population") if (rate_conversion and "Population" in header) else {}
        crude = L.wonder.parse_export(path, measure="Crude Rate") if (rate_conversion and "Crude Rate" in header) else {}
        for key, c in cells.items():
            v = c["value"]
            if rate_conversion:
                p = pop.get(key)
                d = L.rates._numeric(p["value"]) if p is not None else None       # engine.connectors.rates._wonder_denominators
                unreliable = bool(c.get("unreliable")) or bool(crude.get(key, {}).get("unreliable"))
                avail = v is not None and d is not None and not unreliable
                val[key] = float(v) / d * 100000.0 if avail else float("nan")
                den[key] = d if d is not None else float("nan")
                ok[key] = bool(avail)
            else:
                avail = v is not None
                val[key] = float(v) if avail else float("nan")
                den[key] = float("nan")
                ok[key] = bool(avail)
    units = ("deaths per 100,000 residents (rate from counts)" if rate_conversion else "deaths (published count)" if count_series else spec_cat["units"])
    return dict(val=val, ok=ok, sem=dict(ok), den=den, source="wonder_tsv", units_string=units)


UNITS51 = None


def grid_axes():
    L = load_l2()
    return sorted(L.STATES.values())


def store_arrays(store, meta):
    """store: {sid: dict(val, ok, sem, den, ...)} -> the arrays of one npz: a value, eligible, semantic and denominator grid per series (units x years)."""
    units = grid_axes()
    years = list(range(1960, 2031))
    ui = {u: i for i, u in enumerate(units)}
    yi = {y: i for i, y in enumerate(years)}
    arrays = {"units": np.array(units), "years": np.array(years)}
    for sid, s in store.items():
        for kind, dflt in (("val", np.nan), ("den", np.nan), ("ok", False), ("sem", False)):
            a = np.full((len(units), len(years)), dflt, dtype=float if kind in ("val", "den") else bool)
            for (u, y), v in s[kind].items():
                if u in ui and y in yi:
                    a[ui[u], yi[y]] = v
            arrays[f"{sid}__{kind}"] = a
    arrays["meta_json"] = np.array(json.dumps(meta, sort_keys=True))
    return arrays


def save_series_store(path, store, meta):
    np.savez_compressed(path, **store_arrays(store, meta))


def arrays_equal(arrays, path):
    """Whether the npz file at `path` holds exactly these arrays."""
    z = np.load(path, allow_pickle=False)
    if set(z.files) != set(arrays):
        return False
    for k, a in arrays.items():
        b = z[k]
        if a.dtype.kind == "f":
            if not np.array_equal(a, b, equal_nan=True):
                return False
        elif not np.array_equal(a, b):
            return False
    return True


class SeriesStore:
    """Read side of the series store: the 'rate' file (every swept series) and, optionally, the 'count' file (the 12 WONDER death-count series,
    amendment B).  `mode` selects the version of a series that has two; a series with one version is read from the first file in every mode."""

    def __init__(self, path, count_path=None):
        z = np.load(path, allow_pickle=False)
        self.units = [str(u) for u in z["units"]]
        self.years = [int(y) for y in z["years"]]
        self.ui = {u: i for i, u in enumerate(self.units)}
        self.yi = {y: i for i, y in enumerate(self.years)}
        self.meta = json.loads(str(z["meta_json"]))
        self.sids = sorted({k.rsplit("__", 1)[0] for k in z.files if "__" in k})
        self._z = {k: z[k] for k in z.files if "__" in k}
        self._zc = {}
        self.count_sids = []
        if count_path is not None and Path(count_path).exists():
            zc = np.load(count_path, allow_pickle=False)
            if [str(u) for u in zc["units"]] != self.units or [int(y) for y in zc["years"]] != self.years:
                raise ValueError("the count store has other axes than the rate store")
            self.count_sids = sorted({k.rsplit("__", 1)[0] for k in zc.files if "__" in k})
            self._zc = {k: zc[k] for k in zc.files if "__" in k}

    def arr(self, sid, kind, mode="rate"):
        if mode == "count" and sid in self.count_sids:
            return self._zc[f"{sid}__{kind}"]
        return self._z[f"{sid}__{kind}"]

    def eligible_keys(self, sid, units, years, kind="ok", mode="rate"):
        a = self.arr(sid, kind, mode)
        ys = [y for y in years if y in self.yi]
        return {(u, y) for u in units if u in self.ui for y in ys if a[self.ui[u], self.yi[y]]}

    def value(self, sid, key, kind="val", mode="rate"):
        u, y = key
        return float(self.arr(sid, kind, mode)[self.ui[u], self.yi[y]])

    def values(self, sid, keys, kind="val", mode="rate"):
        a = self.arr(sid, kind, mode)
        return np.array([a[self.ui[u], self.yi[y]] for u, y in keys], dtype=float)

    def view(self, mode):
        return _StoreView(self, mode)


class _StoreView:
    """The store read in one scale mode (same methods, no mode argument)."""

    def __init__(self, store, mode):
        self.store, self.mode = store, mode
        self.sids = store.sids

    def eligible_keys(self, sid, units, years, kind="ok"):
        return self.store.eligible_keys(sid, units, years, kind, self.mode)

    def value(self, sid, key, kind="val"):
        return self.store.value(sid, key, kind, self.mode)

    def values(self, sid, keys, kind="val"):
        return self.store.values(sid, keys, kind, self.mode)


# ---------------------------------------------------------------------------------------------
# design context: a per-entry copy of the DRY-LOAD of engine registered.load_registration (draft mode)
# ---------------------------------------------------------------------------------------------
LEGACY = "legacy_no_min_post"       # frozen support summary without the scalar min_post_periods: the historical geometry has no post-period minimum
STRICT = "strict"


class Entry:
    """One registered outcome (primary or secondary) of a design."""

    def __init__(self, name):
        self.name = name
        self.status = "not_loaded"        # ready | unsupported | error
        self.reason = None
        self.declaration = None
        self.required = None              # frozenset of (unit, period) in design space of the calendar labels of the panel files
        self.panel_path = None
        self.metadata_path = None
        self.panel_sha256 = None
        self.metadata_sha256 = None
        self.mode = None                  # STRICT or LEGACY
        self.design = None
        self.support = None
        self.card = None
        self.regenerated_support_agrees = None


class DesignContext:
    def __init__(self, design_id, root):
        self.design_id = design_id
        self.root = Path(root)
        self.manifest = None
        self.card = None
        self.cards = None
        self.spec = None
        self.treatment = None
        self.entries = OrderedDict()
        self.errors = []                  # design-level problems (identity, adapters)
        self.adapter = None
        self.mode = None                  # STRICT or LEGACY (mode of the primary)

    @property
    def primary(self):
        return self.entries.get("primary")


def _bound(root, manifest, relative):
    L = load_l2()
    path = L.storage.local(root, relative)
    if relative not in manifest["files"] or L.storage.sha(path) != manifest["files"][relative]["sha256"]:
        raise ValueError("Execution dependency absent/changed in final freeze: " + relative)
    return path


def mask_keys(declaration, eligible):
    """engine.registered.registered_panel_keys without the file read: the eligible (unit, calendar year) keys after the declaration's own row filters."""
    L = load_l2()
    retained = L.design.structural_rows(declaration, [(u, t, 0.0, None) for u, t in sorted(eligible)])
    calendar = declaration.get("period_years")
    return {(u, calendar[t] if calendar else t) for u, t, _, _ in retained}


def build_design_from_keys(declaration, keys):
    L = load_l2()
    return L.design.build_design(declaration, [(u, t, 0.0, None) for u, t in sorted(keys)])


def legacy_declaration(declaration):
    d = copy.deepcopy(declaration)
    d["case"].pop("min_post_periods", None)
    return d


def load_design_context(design_id, root):
    """Everything the sweep needs of one registered design, with per-entry status (an entry that cannot be rebuilt does not stop the others)."""
    L = load_l2()
    root = Path(root)
    ctx = DesignContext(design_id, root)
    manifest = read_json(root / "FREEZE_FINAL.json")
    ctx.manifest = manifest
    def read(relative):
        return json.loads(_bound(root, manifest, relative).read_text())
    card = L.registered_execution_card(root, manifest)
    spec = read("SPEC.json")
    sha = L.storage.sha
    identity = spec.get("card_sha256") in (None, sha(Path(card["_card_path"])))
    if not identity and "REGISTRATION.json" in manifest["files"]:
        registration = read("REGISTRATION.json")
        retained = [n for n, r in manifest["files"].items() if r["role"] == "card"]
        identity = (len(retained) == 1 and Path(card["_card_path"]).resolve() == L.storage.local(root, retained[0]).resolve()
                    and registration.get("card_sha256") == spec.get("card_sha256"))
    if spec.get("design_id") != card["design_id"] or not identity:
        ctx.errors.append("IDENTITY: scientific execution identity changed")
    if any(spec[k] != card[k] for k in ("design_id", "version", "track", "analysis", "treatment", "sensitivities", "outcome")):
        ctx.errors.append("IDENTITY: card differs from registered scientific specification")
    ctx.spec, ctx.card = spec, card
    ctx.adapter = card["analysis"].get("support_adapter")
    expected = L.variants.registered_names(spec)
    support_name, panel_name = L.inventory_names(root)
    support_records = read(support_name)["variants"]
    panel_records = read(panel_name)["variants"]
    if L.variants.secondary_entries(spec) and not all(L.variants.secondary_name(s) in {p["variant"] for p in panel_records} for s in L.variants.secondary_entries(spec)):
        secondaries = read("SECONDARY_PANELS.json")["secondaries"]
        index = {r["outcome"]: r for r in secondaries}
        if len(index) != len(secondaries) or set(index) != {s["outcome"] for s in L.variants.secondary_entries(spec)}:
            raise ValueError("Secondary panel registered variant inventory changed")
        for entry in L.variants.secondary_entries(spec):
            name = L.variants.secondary_name(entry)
            record = index[entry["outcome"]]
            prefix = "secondary_panels/" + entry["outcome"] + "/"
            summary = read(prefix + "SUPPORT.json")
            panel_records.append(dict(record["construction"], variant=name, panel_relative=prefix + "panel.csv", metadata_relative=prefix + "metadata.csv"))
            support_records.append(dict(summary, variant=name))
    support = L.registered._index(support_records, expected, "Support")
    panels = L.registered._index(panel_records, expected, "Panel")
    ctx.treatment = read("TREATMENT.json")
    scientific_card = {k: v for k, v in card.items() if k != "_card_path"}
    ctx.cards = L.variants.registered_cards(scientific_card)
    for name in expected:
        if not (name == "primary" or name.startswith("secondary_")):
            continue
        e = Entry(name)
        ctx.entries[name] = e
        e.card = ctx.cards[name]
        e.support = support[name]
        p = panels[name]
        if name.startswith("secondary_") and p.get("state") == "UNSUPPORTED":
            e.status, e.reason = "unsupported", "panel inventory: UNSUPPORTED"
            continue
        sup_pass = support[name].get("support_pass", support[name].get("passed"))
        if sup_pass is not True:
            e.status, e.reason = "unsupported", "registered metadata support failed"
            continue
        try:
            panel = _bound(root, manifest, p.get("panel_relative", "panels/" + name + "/panel.csv"))
            metadata = _bound(root, manifest, p.get("metadata_relative", "panels/" + name + "/metadata.csv"))
            e.panel_path, e.metadata_path = panel, metadata
            e.panel_sha256, e.metadata_sha256 = p["panel_sha256"], p["metadata_sha256"]
            dec = L.registered.numerical_declaration(e.card, support[name], ctx.treatment, name)
            required = L.registered.registered_panel_keys(dec, metadata)
            e.required = frozenset(required)
            # strict geometry first; if the frozen cell inventory is not reproduced, the historical geometry without the post-period minimum
            design = build_design_from_keys(dec, required)
            e.mode = STRICT
            if "cell_inventory" not in support[name] or design["cells"] != support[name]["cell_inventory"]:
                dec2 = legacy_declaration(dec)
                design2 = build_design_from_keys(dec2, required)
                if "cell_inventory" in support[name] and design2["cells"] == support[name]["cell_inventory"] and "min_post_periods" not in support[name]:
                    dec, design, e.mode = dec2, design2, LEGACY
                else:
                    e.status, e.reason = "error", "Numerical/registered cell inventory differs (strict and historical geometry)"
                    e.declaration, e.design = dec, design
                    continue
            e.declaration, e.design = dec, design
            e.status = "ready"
        except Exception as exc:          # one entry failing does not stop the others
            e.status, e.reason = "error", f"{type(exc).__name__}: {str(exc)[:300]}"
    prim = ctx.primary
    ctx.mode = prim.mode if prim is not None and prim.status == "ready" else None
    return ctx


# ---------------------------------------------------------------------------------------------
# declaration builder: how engine L2 builds the declaration and the analysis mask of a secondary outcome from its own metadata
# ---------------------------------------------------------------------------------------------
class Builder:
    """Declaration, mask and design for a set of eligible (unit, year) keys under the treatment table and settings of one design.

    Follows stages.s3_support.run (support_check on the eligible keys with the variant's treatment records) and registered.numerical_declaration,
    the two steps engine L2 applies to a secondary outcome.  Only the standard annual adapter is available; designs registered with the
    annual full-year adapter have no certified secondary adapter in L2 and are not swept."""

    def __init__(self, ctx):
        L = load_l2()
        if ctx.adapter == "annual_full_year_v1":
            raise NotImplementedError("annual_full_year_v1")
        if ctx.card["outcome"].get("frequency", "annual") != "annual":
            raise NotImplementedError("non-annual primary")
        self.ctx = ctx
        self.card = ctx.cards["primary"]
        self.mode = ctx.mode
        self.years = L.support.declared_annual_periods(self.card["outcome"])
        self.tyd = self.card.get("transition_year_drop", False)

    def summary(self, eligible):
        L = load_l2()
        records = L.variant_treatment(self.ctx.treatment["records"], self.card)
        summ = L.support.support_check(eligible, records, self.card["analysis"], period_years=self.years, transition_year_drop=self.tyd)
        if self.mode == LEGACY:
            summ = dict(summ, post_support_excluded_units=[])            # the historical support check had no post-period exclusion
        return summ

    def build(self, eligible, label="sweep"):
        """-> (declaration, mask keys, design, support summary).  Raises ValueError where the engine refuses the structure."""
        L = load_l2()
        summ = self.summary(eligible)
        dec = L.registered.numerical_declaration(self.card, summ, self.ctx.treatment, label)
        if self.mode == LEGACY:
            dec = legacy_declaration(dec)
        keys = mask_keys(dec, eligible)
        design = build_design_from_keys(dec, keys)
        return dec, keys, design, summ


# ---------------------------------------------------------------------------------------------
# label bundles
# ---------------------------------------------------------------------------------------------
def full_exposure(declaration, units, years):
    """engine exposure dicts of `units` over every period of `years` (calendar labels), taken from the engine's own constructor on a full rectangle."""
    rows = [(u, t, 0.0, None) for u in units for t in years]
    d = build_design_from_keys(declaration, [(u, t) for u, t, _, _ in rows])
    return d["exposure"]


class AlignedLabels:
    """The fixed label reassignments drawn once on the unit list of the registered primary (seed RI_SEED, 999 draws) and restricted to the units of
    a cell pattern.  Draw b gives unit j of the primary the label (adoption, operative offset, exposure, never-pool membership) of the primary unit
    perm_b[j]; a series that lost units keeps the labels its remaining units get in the primary's draw.  With the primary's own cell pattern the
    bundle equals engine.ri.predraw_labels exactly."""

    def __init__(self, declaration0, design0, years, n_draws=N_DRAWS, seed=RI_SEED):
        self.units0 = list(design0["units"])
        self.pos0 = {u: i for i, u in enumerate(self.units0)}
        exp = full_exposure(declaration0, self.units0, years)
        for u in self.units0:             # the full-rectangle exposure must agree with the primary's own wherever the primary has a period
            for t, cell in design0["exposure"][u].items():
                if exp[u].get(t) != cell:
                    raise RuntimeError(f"exposure of {u} in period {t} differs between the full rectangle and the primary mask")
        self.labels0 = [(design0["adoption"][u],
                         None if design0["adoption"][u] is None else design0["operative"][u] - design0["adoption"][u],
                         exp[u], design0["whole_window_clean"][u]) for u in self.units0]
        rng = random.Random(seed)
        self.perms = []
        for _ in range(n_draws):
            idx = list(range(len(self.units0)))
            rng.shuffle(idx)
            self.perms.append(idx)

    def bundles(self, design):
        pos = [self.pos0[u] for u in design["units"]]              # KeyError if a unit is not a unit of the primary
        for perm in self.perms:
            yield [self.labels0[perm[p]] for p in pos]


def own_bundles(design, n_draws=N_DRAWS):
    """engine.ri.predraw_labels: the engine's own bundle for the unit list of this design."""
    L = load_l2()
    return L.ri.predraw_labels(design, n_draws)


# ---------------------------------------------------------------------------------------------
# operators
# ---------------------------------------------------------------------------------------------
def family_of(cells):
    """Event times with at least 5 treated and e < 0 (the engine's lead family: ri.pre_family)."""
    mass = defaultdict(int)
    for c in cells:
        mass[c["event_time"]] += c["n_treated"]
    return sorted(e for e, m in mass.items() if e < 0 and m >= 5)


def _weights(cells, index, K, normalise):
    idx, w = [], []
    mass = sum(c["n_treated"] for c in cells) if normalise else 1
    for c in cells:
        f = (c["n_treated"] / mass) if normalise else 1.0
        nt, nc = len(c["treated"]), len(c["controls"])
        t, b = c["t"], c["base_period"]
        for u in c["treated"]:
            idx += (index[(u, t)], index[(u, b)])
            w += (f / nt, -f / nt)
        for u in c["controls"]:
            idx += (index[(u, t)], index[(u, b)])
            w += (-f / nc, f / nc)
    return np.bincount(np.array(idx, dtype=np.int64), weights=np.array(w), minlength=K)


class Ops:
    """Linear operators of one (design, cell pattern): ATT = overall . y, placebo ATTs = ri_overall . y (rows = feasible draws)."""
    pass


def build_ops(declaration, design, bundles, n_draws=N_DRAWS, with_cells=False):
    """Operators with the draws given from outside.  Mirrors engine.calibration.calibration_operators (same relabeling, same feasibility rule)."""
    L = load_l2()
    keys = sorted(design["mask"])
    index = {k: i for i, k in enumerate(keys)}
    K = len(keys)
    family = family_of(design["cells"])
    if not family:
        raise ValueError("NOT_ASSESSABLE: empty supported pre family")
    post = [c for c in design["cells"] if c["post"]]
    ops = Ops()
    ops.keys, ops.index, ops.family = keys, index, family
    ops.overall = _weights(post, index, K, True)
    ops.cells = [dict(g=c["g"], t=c["t"], base_period=c["base_period"], n_treated=c["n_treated"], n_control=c["n_control"], post=bool(c["post"]),
                      event_time=c["event_time"]) for c in design["cells"]]
    ops.cell_ops = np.array([_weights([c], index, K, False) for c in design["cells"]]) if with_cells else None
    rows, draw_idx, records = [], [], []
    for b, labels in enumerate(bundles):
        try:
            fake = L.ri.relabeled_design(declaration, design, labels)
            if family_of(fake["cells"]) != family:
                raise ValueError("RI lead inventory changed under relabeling")
            rows.append(_weights([c for c in fake["cells"] if c["post"]], index, K, True))
        except ValueError as exc:
            if not L.ri.support_failure(exc):
                raise
            records.append(dict(infeasible=True, reason=str(exc)))
        else:
            draw_idx.append(b)
            records.append(dict(infeasible=False))
    ops.feasibility = L.ri.inventory(records)
    if ops.feasibility["requested_draws"] != n_draws:
        raise ValueError("bundle count differs from the requested draws")
    ops.ri_overall = np.array(rows) if rows else np.zeros((0, K))
    ops.draw_index = draw_idx
    return ops


def project(ops, y):
    """-> (att, placebo vector of length requested_draws with NaN at infeasible draws)."""
    y = np.asarray(y, dtype=float)
    att = float(ops.overall @ y)
    out = np.full(ops.feasibility["requested_draws"], np.nan)
    if len(ops.draw_index):
        out[ops.draw_index] = ops.ri_overall @ y
    return att, out


def cell_atts(ops, y):
    return ops.cell_ops @ np.asarray(y, dtype=float)


def treated_units(design):
    return {u for c in design["cells"] if c["post"] for u in c["treated"]}


def draw_statistics(att, draws):
    """sd (n-1), 95th-percentile cutoff of |draw| (the engine's rule), two-sided p and z, over the finite draws."""
    d = np.asarray(draws, dtype=float)
    d = d[np.isfinite(d)]
    n = d.size
    if n < 2:
        return dict(n=int(n), sd=float("nan"), cutoff=float("nan"), p2=float("nan"), z=float("nan"))
    sd = float(np.std(d, ddof=1))
    cutoff = float(np.sort(np.abs(d))[math.ceil(0.95 * n) - 1])
    p2 = (1 + int(np.sum(np.abs(d) >= abs(att)))) / (n + 1)
    return dict(n=int(n), sd=sd, cutoff=cutoff, p2=p2, z=att / sd if sd > 0 else float("nan"))


# ---------------------------------------------------------------------------------------------
# reproduction gate
# ---------------------------------------------------------------------------------------------
def entry_series_id(ctx, name):
    """Catalog series id of a registered outcome, or None for an adapter slice that is not a catalog series."""
    if name.startswith("secondary_"):
        return name[len("secondary_"):]
    o = ctx.card["outcome"]
    return o.get("series_id") or o.get("measure_id") or o.get("outcome_id")


def panel_outcomes(entry):
    """(rows, outcomes in design space) of the stored panel of an entry."""
    L = load_l2()
    binding = L.panels.PanelBinding(entry.panel_path, entry.panel_path, entry.metadata_path, entry.panel_sha256, entry.panel_sha256,
                                    entry.metadata_sha256, frozenset(entry.required))
    rows, identity = L.panels.load_bound_panel(binding)
    outcomes = {(u, t): y for u, t, y, _ in L.design.structural_rows(entry.declaration, rows)}
    return rows, outcomes


def gate_entry(ctx, name, stored):
    """Operator path against the stored result of one registered outcome, with the engine's own label bundle.  Returns a record (no exception)."""
    rec = dict(entry=name, mode=None, ok=False)
    e = ctx.entries[name]
    rec["mode"] = e.mode
    try:
        if e.status != "ready":
            rec["error"] = f"entry not rebuilt: {e.status}: {e.reason}"
            return rec
        design = e.design
        rows, outcomes = panel_outcomes(e)
        ops = build_ops(e.declaration, design, own_bundles(design), with_cells=True)
        y = np.array([outcomes[k] for k in ops.keys])
        att, draws = project(ops, y)
        res = stored["result"]
        gt = res["group_time"]
        rec["n_cells"] = len(gt)
        key = lambda c: (c["g"], c["t"], c["base_period"], c["n_treated"], c["n_control"], bool(c["post"]))
        rec["cells_structure_equal"] = [key(c) for c in design["cells"]] == [key(c) for c in gt]
        ca = cell_atts(ops, y)
        rec["cell_max_diff"] = max_scaled_diff(ca, [c["att"] for c in gt]) if rec["cells_structure_equal"] else None
        rec["att"] = att
        rec["att_stored"] = res["overall"]["att"]
        rec["att_diff"] = abs(att - res["overall"]["att"]) / max(1.0, abs(att), abs(res["overall"]["att"]))
        nf = stored.get("noise_floor") or {}
        sd = nf.get("permutation_draws")
        rec["n_feasible"] = ops.feasibility["feasible_draws"]
        if isinstance(sd, list):
            feas = nf["feasibility"]
            bad = {x["draw_index"] for x in feas.get("infeasible_draws") or []}
            stored_idx = [i for i in range(feas["requested_draws"]) if i not in bad]
            rec["feasible_set_equal"] = stored_idx == ops.draw_index
            rec["n_stored_draws"] = len(sd)
            if rec["feasible_set_equal"] and len(sd) == len(ops.draw_index):
                rec["draw_max_diff"] = max_scaled_diff(draws[ops.draw_index], sd)
                st = draw_statistics(att, draws)
                rec["stats_equal"] = bool(agree(st["sd"], nf["sample_sd"]) and agree(st["cutoff"], nf["absolute_cutoff"]) and agree(st["p2"], nf["p_two_sided"]))
            else:
                rec["draw_max_diff"] = None
                rec["stats_equal"] = False
        else:
            rec["feasible_set_equal"] = None
            rec["draw_max_diff"] = None
            rec["stats_equal"] = None
            rec["no_stored_draws"] = True
        needs_draws = isinstance(sd, list)
        rec["ok"] = bool(rec["cells_structure_equal"] and rec["cell_max_diff"] is not None and rec["cell_max_diff"] <= TOL and rec["att_diff"] <= TOL
                         and (not needs_draws or (rec["feasible_set_equal"] and rec["draw_max_diff"] is not None and rec["draw_max_diff"] <= TOL and rec["stats_equal"])))
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    return rec


def gate_builder_entry(ctx, name):
    """Rebuild the declaration and design of a registered outcome from its metadata with the sweep's builder; must equal the registered ones."""
    rec = dict(entry=name, ok=False)
    e = ctx.entries[name]
    try:
        L = load_l2()
        b = Builder(ctx)
        eligible = L.panels.metadata_eligible_keys(e.metadata_path)
        dec, keys, design, summ = b.build(eligible, name)
        rec["mask_equal"] = set(keys) == set(e.required)
        rec["cells_equal"] = design["cells"] == e.design["cells"]
        rec["units_equal"] = design["units"] == e.design["units"]
        rec["declaration_equal"] = (dec["cohorts"] == e.declaration["cohorts"] and dec["operative_years"] == e.declaration["operative_years"]
                                    and sorted(dec["never_controls"]) == sorted(e.declaration["never_controls"])
                                    and dec["case"]["sample_mask"] == e.declaration["case"]["sample_mask"] and dec["case"]["windows"] == e.declaration["case"]["windows"]
                                    and dec["case"].get("min_post_periods") == e.declaration["case"].get("min_post_periods")
                                    and dec["case"].get("min_controls") == e.declaration["case"].get("min_controls"))
        rec["ok"] = bool(rec["mask_equal"] and rec["cells_equal"] and rec["units_equal"] and rec["declaration_equal"])
    except NotImplementedError as exc:
        rec["skipped"] = str(exc)
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    return rec


# ---------------------------------------------------------------------------------------------
# series-store check and entry bookkeeping
# ---------------------------------------------------------------------------------------------
def gate_store_entry(ctx, name, sid, store):
    """Build amendment 4.6 as changed by amendment B 2.2: the stored panel of a registered entry against the series store.  For a series that has a
    count version both versions are tested; `scale_match` is the version the entry matches (None if neither)."""
    rec = dict(entry=name, series=sid, ok=False, scale_match=None, has_count_version=sid in store.count_sids)
    e = ctx.entries[name]
    try:
        if e.declaration.get("period_years") is not None:
            rec["skipped"] = "declaration with an axis index"
            rec["ok"] = True
            return rec
        rows, outcomes = panel_outcomes(e)
        keys = sorted(e.design["mask"])
        y = np.array([outcomes[k] for k in keys])
        rec["n_cells"] = len(keys)
        modes = ["rate", "count"] if sid in store.count_sids else ["rate"]
        matches = []
        for mode in modes:
            v = store.values(sid, keys, "val", mode)
            ok = store.values(sid, keys, "ok", mode).astype(bool)
            diff = max_scaled_diff(v[ok], y[ok]) if ok.any() else None
            rec[mode] = dict(all_eligible_in_store=bool(ok.all()), max_value_diff=diff)
            if ok.all() and diff is not None and diff <= 1e-9:
                matches.append(mode)
        rec["scale_match"] = matches[0] if len(matches) == 1 else None
        rec["ok"] = len(matches) == 1
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    return rec


def aligned_equals_engine(al, design, n_check=N_DRAWS):
    """The aligned bundle of the primary's own design equals engine.ri.predraw_labels (labels as tuples, exposure compared on the design's periods)."""
    own = own_bundles(design, n_check)
    mine = list(al.bundles(design))
    if len(own) != len(mine):
        return False
    for a, b in zip(own, mine):
        for la, lb in zip(a, b):
            if la[0] != lb[0] or la[1] != lb[1] or la[3] != lb[3]:
                return False
            if any(lb[2].get(t) != cell for t, cell in la[2].items()):
                return False
    return True


# ---------------------------------------------------------------------------------------------
# sweep of one design
# ---------------------------------------------------------------------------------------------
CODES = ["NO_CELLS", "NO_DESIGN", "NO_PRE_FAMILY", "KEPT_LT_80", "RI_FEASIBLE_LT_90", "CONSTANT", "SUPPORTED"]


def sweep_design(ctx, store, series_table, stored_primary=None):
    """Structure pass and operators for every swept series of one design.

    Returns dict with per-series records (code, att, placebo vector, ...), patterns and design facts.  Raises on ALIGNMENT problems."""
    prim = ctx.primary
    b = Builder(ctx)
    U0 = list(prim.design["units"])
    U0s = set(U0)
    years = b.years
    T0 = treated_units(prim.design)
    al = AlignedLabels(prim.declaration, prim.design, years)
    own_ok = aligned_equals_engine(al, prim.design)
    if not own_ok:
        raise RuntimeError("ALIGNMENT_FAIL: the aligned bundle of the primary differs from engine.ri.predraw_labels")
    recs = OrderedDict()
    groups = OrderedDict()          # fingerprint -> dict(dec, design, sids, keys)
    for sid in sorted(series_table):
        rec = dict(sid=sid, family=series_table[sid]["family"], code=None, detail="", att=float("nan"), placebo=np.full(N_DRAWS, np.nan),
                   n_cells=0, n_units=0, fingerprint="", kept=float("nan"), n_feasible=0, support_pass_l2=None, strict_dropped=0)
        recs[sid] = rec
        elig = store.eligible_keys(sid, U0s, years, "ok")
        sem = store.eligible_keys(sid, U0s, years, "sem")
        rec["strict_dropped"] = len(sem - elig)
        if not elig:
            rec["code"] = "NO_CELLS"
            continue
        try:
            dec, keys, design, summ = b.build(elig, sid)
        except ValueError as exc:
            rec["code"], rec["detail"] = "NO_DESIGN", str(exc)[:200]
            continue
        rec["support_pass_l2"] = bool(summ.get("support_pass", summ.get("passed")))
        if any(u not in U0s for u in design["units"]):
            raise RuntimeError("ALIGNMENT_FAIL: a pattern has a unit outside the primary's unit list")
        rec["n_cells"], rec["n_units"] = len(design["mask"]), len(design["units"])
        if not any(c["post"] for c in design["cells"]):
            rec["code"], rec["detail"] = "NO_DESIGN", "no post cell"
            continue
        if not family_of(design["cells"]):
            rec["code"] = "NO_PRE_FAMILY"
            continue
        rec["kept"] = len(treated_units(design) & T0) / len(T0)
        if rec["kept"] < MIN_KEPT_TREATED:
            rec["code"] = "KEPT_LT_80"
            continue
        y = store.values(sid, sorted(design["mask"]), "val")
        if not np.all(np.isfinite(y)):
            raise RuntimeError(f"non-finite value on the mask of {sid}")
        if float(np.max(y) - np.min(y)) == 0.0:
            rec["code"] = "CONSTANT"
            continue
        fp = mask_fingerprint(design)
        rec["fingerprint"] = fp
        g = groups.setdefault(fp, dict(dec=dec, design=design, sids=[]))
        g["sids"].append(sid)
    patterns = OrderedDict()
    for fp, g in groups.items():
        ops = build_ops(g["dec"], g["design"], al.bundles(g["design"]))
        patterns[fp] = dict(n_cells=len(g["design"]["mask"]), n_units=len(g["design"]["units"]), n_post_cells=sum(1 for c in g["design"]["cells"] if c["post"]),
                            feasible=ops.feasibility["feasible_draws"], support_pass=bool(ops.feasibility["support_pass"]), sids=list(g["sids"]),
                            infeasible_reasons=dict(Counter(x["reason"][:60] for x in ops.feasibility["infeasible_draws"])))
        for sid in g["sids"]:
            rec = recs[sid]
            y = store.values(sid, ops.keys, "val")
            att, draws = project(ops, y)
            rec["att"], rec["placebo"] = att, draws
            rec["n_feasible"] = int(ops.feasibility["feasible_draws"])
            rec["code"] = "SUPPORTED" if ops.feasibility["support_pass"] else "RI_FEASIBLE_LT_90"
    return dict(records=recs, patterns=patterns, units=U0, treated_primary=sorted(T0), years=list(years), aligned_equals_engine=own_ok)
