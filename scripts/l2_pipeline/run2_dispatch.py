"""Host dispatcher compatibility entry point with per-design --design-id.

All authorization, byte bindings, reservations and no-retry checks remain in
the established dispatcher. This module never launches anything on import.
"""
from __future__ import annotations
import sys


def main(argv=None):
    from .agents.dispatch import main as dispatch_main
    args = list(sys.argv[1:] if argv is None else argv)
    if '--help' in args or '-h' in args:
        print('RUN2: --design-id ID (repeatable) selects individual designs; alias of --design.\n')
    translated = [('--design' if arg == '--design-id' else
                   '--design=' + arg.split('=', 1)[1] if arg.startswith('--design-id=') else arg)
                  for arg in args]
    return dispatch_main(translated)


if __name__ == '__main__':
    raise SystemExit(main())
