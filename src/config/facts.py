"""Single Source of Truth for key project facts.

Any value that appears in code, docs, AND paper should be defined here.
Run `python src/scripts/check_consistency.py` to verify all references match.
"""

# ── Prompt & Model ──────────────────────────────────────────────────────
PROMPT_VERSION = "prompt.20260420.2"
PRIMARY_MODEL = "Qwen 3.6 Plus"
PRIMARY_MODEL_ID = "qwen/qwen3.6-plus:free"
TEMPERATURE = 0

# ── Pipeline Architecture ───────────────────────────────────────────────
N_PHASES = 6  # gatekeeper → design → scoring → literature → angles → topic

# ── Scoring Weights ─────────────────────────────────────────────────────
W_ID = 0.35   # identification
W_PS = 0.25   # policy_significance
W_TI = 0.20   # treatment_intensity
W_DF = 0.20   # data_feasibility

# ── Feasibility Gates ──────────────────────────────────────────────────
FEASIBILITY_GATE_COUNT = 5
FEASIBILITY_MIN = 2.5
ID_MIN = 2
PS_MIN = 2
ID_HARD_CAP_THRESHOLD = 2     # id <= this → feasibility capped
ID_HARD_CAP_VALUE = 2.5
POWER_INSUFFICIENT_CAP = 2.0

# ── Literature Bonuses ──────────────────────────────────────────────────
LIT_BONUS_BLUE_OCEAN = 0.4
LIT_BONUS_EMERGING = 0.2
LIT_PENALTY_COMPETITIVE = -0.15
LIT_PENALTY_CROWDED = -0.4

# ── Data Access Penalties ───────────────────────────────────────────────
DATA_PENALTY_RDC = -0.5
DATA_PENALTY_STATE_AGENCY = -0.3
BORDER_COUNTY_BONUS = 0.25

# ── Benchmark Results (update after each full benchmark run) ────────────
BENCHMARK_N_PAPERS = 162       # post-2016-effective evaluation set
BENCHMARK_N_EVALUATED = 161    # 162 - 1 fetch failure (econ-013)
GT_N_BILLS = 316               # analyzed ground-truth bill entries
GT_N_DID = 104                 # ground truth DID papers (99 DID + 5 Both)
DID_RECALL_N = 101             # correctly identified DID papers (majority vote k=3)
DID_RECALL_PCT = 97.1          # 101/104 * 100
DID_RECALL_ECON = 95.6         # econ track: 43/45
DID_RECALL_CROSS = 98.3        # cross track: 58/59
DID_PRECISION_PCT = 65.6       # 101/154 DID positives are true positives

# ── Stability Metrics (Qwen 3.6 Plus, prompt.20260420.2, 100 bills × 5 runs) ─────────────
DESIGN_TYPE_EXACT_MATCH = 82      # percent (5/5 identical)
DESIGN_TYPE_MAJORITY_AGREE = 100  # percent (≥3/5 agree)
DESIGN_TYPE_FLEISS_KAPPA = 0.873
COMPOSITE_EXACT_MATCH = 25        # percent
COMPOSITE_PM_1_0 = 95             # percent within ±1.0
COMPOSITE_AVG_SD = 0.162
IDENTIFICATION_AVG_SD = 0.159
K3_VS_K5_SAME_LABEL_MINIMAX = 95.9
K3_VS_K5_SAME_BOUNDARY_MINIMAX = 97.9
K3_VS_K5_SAME_LABEL_DEEPSEEK = 94.9
K3_VS_K5_SAME_BOUNDARY_DEEPSEEK = 98.0

# ── Data Coverage ───────────────────────────────────────────────────────
OPEN_STATES_BILLS = "~2 million"
BILL_VERSION_COVERAGE = 97.5   # percent
FULLTEXT_COVERAGE = 90         # percent (~89-90%)

# ── Cross-Model Divergence / Accuracy (Claude Opus 4 judge) ────────────
CROSS_MODEL_ALL_AGREE_PCT = 43
DIVERGENCE_HIGH_N = 33
DIVERGENCE_MEDIUM_N = 44
DIVERGENCE_LOW_N = 23

JUDGE_MODEL_NAME = "Claude~Opus~4.6"
ACCURACY_DEEPSEEK = 4.22
ACCURACY_MINIMAX = 4.02
ACCURACY_GROK = 4.25
ACCURACY_GPT = 4.14
ACCURACY_XIAOMI = 4.22
ACCURACY_QWEN = 4.32
ACCURACY_MINIMAX27 = 3.97
