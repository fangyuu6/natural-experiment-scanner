"""Load config from environment."""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

CONGRESS_API_KEY = os.getenv("CONGRESS_API_KEY", "")
CONGRESS_BASE_URL = os.getenv("CONGRESS_BASE_URL", "https://api.congress.gov/v3")
GOVINFO_API_KEY = os.getenv("GOVINFO_API_KEY", "")

# Regulations.gov (optional; use DEMO_KEY for testing)
REGULATIONS_API_KEY = os.getenv("REGULATIONS_API_KEY", "")

# Open States (state legislation, e.g. Florida): https://openstates.org/accounts/signup/
OPEN_STATES_API_KEY = os.getenv("OPEN_STATES_API_KEY", "")
# Bulk: restored PostgreSQL dump (e.g. postgresql://localhost/openstates_restore)
OPEN_STATES_BULK_DB_URL = os.getenv("OPEN_STATES_BULK_DB_URL", "")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")  # optional, for compatible endpoints
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "qwen/qwen3.6-plus:free")

# Literature assessment model (separate from main analysis)
LITERATURE_LLM_API_KEY = os.getenv("LITERATURE_LLM_API_KEY", "")
LITERATURE_LLM_BASE_URL = os.getenv("LITERATURE_LLM_BASE_URL", "")
LITERATURE_LLM_MODEL = os.getenv("LITERATURE_LLM_MODEL", "")

# ── New data source API keys (optional, free tier available) ──
# LegiScan: all 50 US states, 30k calls/month free. https://legiscan.com/legiscan
LEGISCAN_API_KEY = os.getenv("LEGISCAN_API_KEY", "")
# ProPublica Congress API: free key at https://www.propublica.org/datastore/api/propublica-congress-api
PROPUBLICA_API_KEY = os.getenv("PROPUBLICA_API_KEY", "")
# Germany Bundestag DIP API: free key at https://dip.bundestag.de/über-dip/hilfe/api
BUNDESTAG_API_KEY = os.getenv("BUNDESTAG_API_KEY", "")
# South Korea National Assembly: free key at https://open.assembly.go.kr/
KOREA_ASSEMBLY_API_KEY = os.getenv("KOREA_ASSEMBLY_API_KEY", "")

# MVP: how many bills to fetch per run (keep small for cost / manual check)
DEFAULT_BILL_LIMIT = int(os.getenv("BILL_LIMIT", "20"))
# Congress number (118 = current as of 2023–2024)
DEFAULT_CONGRESS = int(os.getenv("CONGRESS", "118"))
