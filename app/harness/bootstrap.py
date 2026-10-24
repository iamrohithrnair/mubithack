"""Register every workstream engine, once per process.

Built-in engines first (returns, AP invoices), then every planted workstream
stored in the database. Called from the app's startup and lazily by the decision
service, so tests that never start the app still find their engines.
"""

from __future__ import annotations

import threading

_loaded = False
_lock = threading.Lock()


def load(force: bool = False) -> None:
    global _loaded
    with _lock:
        if _loaded and not force:
            return
        from app.harness import builtin
        from app.workstreams import registry

        builtin.register()
        registry.load_all()
        _loaded = True


def reset() -> None:
    """Forget what was loaded; the next `load()` registers everything again."""
    global _loaded
    with _lock:
        _loaded = False
