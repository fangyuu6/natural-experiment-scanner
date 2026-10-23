"""C21 verified WONDER exports; preserve the V5 pending-data snapshot."""
from pathlib import Path
from . import outcome_v5

CATALOG_PATH=Path(__file__).with_name('OUTCOME_CATALOG_V6.json')


def catalog():
    return outcome_v5.catalog(CATALOG_PATH,6)


def register():
    return outcome_v5.register(outcome_v5.catalog(CATALOG_PATH,6,verify_sources=False))


def construct(request):
    register()
    from .rates import apply_scale
    data=catalog()
    return apply_scale(request,outcome_v5._construct(request,data),catalog_data=data)
