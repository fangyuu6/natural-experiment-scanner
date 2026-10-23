"""Bind the V9 official-history template into freeze dependency inventories."""
from pathlib import Path

TEMPLATE_FILES = (
    Path(__file__).with_name("official_history_coder.md"),
    Path(__file__).with_name("official_history_coder_fx10.md"),
    Path(__file__).with_name("mechanism_aliases_fx10.json"),
)
