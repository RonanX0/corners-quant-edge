"""Corners Quant Edge v4 — providers, models, backtests and storage.

The legacy statistical engine (``engine.py`` at the repository root) is kept
and reused; this package adds the multi-source data layer, the hierarchical
team-strength model, walk-forward validation, the opportunity radar and the
optional database.
"""

VERSION = "4.0.0"
USER_AGENT = f"CornersQuantEdge/{VERSION} (+https://github.com/RonanX0/corners-quant-edge)"
