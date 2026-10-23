"""Bind active prompt resources into the static freeze dependency closure."""
from pathlib import Path

TEMPLATE_FILES = (
    Path(__file__).with_name("auditor.md"),
    Path(__file__).with_name("coder.md"),
    Path(__file__).with_name("persona.md"),
)

