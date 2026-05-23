from __future__ import annotations

import re

_TEST_PATTERNS = [
    re.compile(r"(^|/)tests?/"),
    re.compile(r"(^|/)test_[^/]+\.py$"),
    re.compile(r"(^|/)[^/]+_test\.py$"),
    re.compile(r"(^|/)conftest\.py$"),
]


def normalize_path(p: str) -> str:
    return p.replace("\\", "/").strip("/")


def is_test_file(path: str) -> bool:
    path = normalize_path(path)
    return any(pat.search(path) for pat in _TEST_PATTERNS)


def should_include(path: str, nloc: int, *, min_nloc: int = 10) -> bool:
    path = normalize_path(path)
    if is_test_file(path):
        return False
    if path.endswith("__init__.py") and nloc < min_nloc:
        return False
    if nloc < min_nloc:
        return False
    return True
