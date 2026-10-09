"""Read-only access to definitions-as-code (doc types, micro-apps, schemas). Immutable per deploy, so
cached. ponytail: per-tenant overrides (micro_apps table) only when a tenant actually diverges."""

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from nova_dsl import DocType, parse_doc_type

ROOT = Path(os.environ.get("DEFINITIONS_DIR", Path(__file__).resolve().parents[4] / "definitions"))
_KEY_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_")


def _safe(key: str) -> str:
    if not key or not set(key) <= _KEY_CHARS:  # keys come from URLs: no path tricks
        raise HTTPException(404, f"unknown key {key!r}")
    return key


@lru_cache
def doc_type(key: str) -> DocType:
    path = ROOT / "doc_types" / f"{_safe(key)}.yaml"
    if not path.is_file():
        raise HTTPException(422, f"unknown doc_type {key!r}")
    return parse_doc_type(path.read_text(encoding="utf-8"))


@lru_cache
def app(key: str) -> dict[str, Any]:
    path = ROOT / "apps" / f"{_safe(key)}.json"
    if not path.is_file():
        raise HTTPException(404, f"micro-app {key!r} not found")
    return dict(json.loads(path.read_text(encoding="utf-8")))


@lru_cache
def schema(key: str) -> dict[str, Any]:
    path = ROOT / "schemas" / f"{_safe(key)}.json"
    if not path.is_file():
        raise HTTPException(404, f"schema {key!r} not found")
    return dict(json.loads(path.read_text(encoding="utf-8")))
