"""Make the top-level ``bench/`` and ``tools/`` script dirs importable in tests.

These live outside ``src/`` (they are dev/diagnostic scripts, not part of the
``xdna_top`` package), so add them to ``sys.path`` for direct import by name.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _sub in ("bench", "tools"):
    _path = str(_ROOT / _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)
