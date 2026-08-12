#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


MINIMUM_PYTHON = (3, 10)
if sys.version_info < MINIMUM_PYTHON:
    current_python = ".".join(str(part) for part in sys.version_info[:3])
    print(
        "ERROR PLUGIN_PYTHON_UNSUPPORTED: "
        "delivery-graph requires Python 3.10+; "
        f"found Python {current_python}",
        file=sys.stderr,
    )
    raise SystemExit(1)


sys.path.insert(0, str(Path(__file__).resolve().parent))

from hdg.mcp_server import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
