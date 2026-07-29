"""Root pytest configuration for the Backtest360 MCP server.

IMPORT-ISOLATION GUARD
======================
``pythonpath = ["src"]`` in pyproject prepends this project's ``src/`` to
``sys.path`` so the test suite imports the local ``backtest360_mcp`` package. If
a different copy of the package is installed elsewhere on the interpreter (for
example an editable install, ``pip install -e``, pointing at another checkout),
its import hook can shadow ``sys.path`` and silently redirect ``import
backtest360_mcp`` to that other copy — so the tests would exercise code you are
not looking at.

This guard makes that situation loud: on every run it asserts the imported
package resolves under pytest's rootpath (this project's root). It only
asserts — it never mutates ``sys.path`` — so it does not paper over a
misconfiguration. If it fires, uninstall the stray copy and rely on
``pythonpath=["src"]``.
"""

import importlib
from pathlib import Path

import pytest

_GUARDED_PACKAGE = "backtest360_mcp"


def pytest_configure(config):
    try:
        module = importlib.import_module(_GUARDED_PACKAGE)
    except Exception as exc:  # pragma: no cover - surfaced loudly below
        pytest.exit(
            f"IMPORT-ISOLATION GUARD: could not import {_GUARDED_PACKAGE!r}: {exc}",
            returncode=2,
        )

    pkg_file = getattr(module, "__file__", None)
    if not pkg_file:
        return

    pkg_path = Path(pkg_file).resolve()
    rootpath = Path(config.rootpath).resolve()
    if not pkg_path.is_relative_to(rootpath):
        pytest.exit(
            f"IMPORT-ISOLATION GUARD: {_GUARDED_PACKAGE!r} was imported from\n"
            f"    {pkg_path}\n"
            f"which is OUTSIDE this project's test rootpath\n"
            f"    {rootpath}\n"
            "Another installed copy is shadowing this checkout's src/. Tests "
            "would run against the wrong code. Uninstall it "
            f"(`pip uninstall {_GUARDED_PACKAGE.replace('_', '-')}`) and rely on "
            "pythonpath=['src'].",
            returncode=2,
        )
