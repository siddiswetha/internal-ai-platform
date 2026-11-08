"""Shared helpers for the unit tests: load a service's main.py by path."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_service(name, service_dir):
    """Load services/<service_dir>/main.py as a module named <name>."""
    path = ROOT / "services" / service_dir / "main.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
