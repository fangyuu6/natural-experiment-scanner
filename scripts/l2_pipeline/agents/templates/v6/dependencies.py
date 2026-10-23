"""Bind active prompt resources into the static freeze dependency closure."""
from pathlib import Path

TEMPLATE_FILES = (
    Path(__file__).with_name("literature_record.md"),
    Path(__file__).with_name("purpose_verifier_v6_1.md"),
    Path(__file__).with_name("purpose_verifier_v6_2.md"),
)

