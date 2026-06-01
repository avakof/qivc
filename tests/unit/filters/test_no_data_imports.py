"""
Import lint test: verifies no module in qivc.filters imports from qivc.data at
module level (top-level, non-indented). Filters must be pure functions with no
data-layer dependencies.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path


def _filter_source_files() -> list[Path]:
    import qivc.filters as pkg

    pkg_path = Path(pkg.__file__).parent  # type: ignore[arg-type]
    return sorted(pkg_path.glob("*.py"))


def test_no_filters_module_imports_qivc_data() -> None:
    """Every module in qivc.filters must have zero top-level imports from qivc.data."""
    violations: list[str] = []

    for src_path in _filter_source_files():
        try:
            tree = ast.parse(src_path.read_text())
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if not module.startswith("qivc.data"):
                    continue
            else:
                # ast.Import — check each alias
                if not any(alias.name.startswith("qivc.data") for alias in node.names):
                    continue

            # Only flag top-level imports (col_offset == 0)
            if node.col_offset == 0:
                violations.append(f"{src_path.name}:{node.lineno}")

    assert violations == [], f"qivc.filters modules have top-level qivc.data imports: {violations}"


def test_filters_are_importable() -> None:
    """All filter modules must be importable without errors."""
    import qivc.filters as pkg

    pkg_path = Path(pkg.__file__).parent  # type: ignore[arg-type]
    failed: list[str] = []

    for py_file in sorted(pkg_path.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        mod_name = f"qivc.filters.{py_file.stem}"
        try:
            importlib.import_module(mod_name)
        except Exception as exc:
            failed.append(f"{mod_name}: {exc}")

    assert failed == [], f"Filter modules failed to import: {failed}"
