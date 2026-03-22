"""
playbook_engine/loader.py

PlaybookLoader: loads and validates playbook files (YAML or JSON),
validates them against playbook_schema_v1.json, and returns typed Playbook objects.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional

import yaml
from jsonschema import Draft202012Validator, ValidationError

from .models import Playbook

# Resolve schema and playbook directory paths relative to this file
# so the package works regardless of the working directory.
_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_SCHEMA_PATH = os.path.join(_PKG_ROOT, "CONTRACTS", "playbook_schema_v1.json")
DEFAULT_PLAYBOOKS_DIR = os.path.join(_PKG_ROOT, "PLAYBOOKS")

_SUPPORTED_EXTENSIONS = (".yaml", ".yml", ".json")


class PlaybookValidationError(Exception):
    """Raised when a playbook file fails JSON Schema validation."""

    def __init__(self, path: str, errors: List[str]) -> None:
        self.path = path
        self.errors = errors
        first = errors[0] if errors else "unknown error"
        super().__init__(f"Playbook validation failed for {path!r}: {first}")


class PlaybookLoader:
    """
    Loads playbook YAML or JSON files, validates them against the playbook
    JSON Schema, and returns typed Playbook objects.

    Usage:
        loader = PlaybookLoader()
        playbook = loader.load("PLAYBOOKS/rule_replenishment_discount_v1.yaml")
        registry = loader.load_directory()   # loads all files in PLAYBOOKS/
    """

    def __init__(self, schema_path: Optional[str] = None) -> None:
        path = schema_path or DEFAULT_SCHEMA_PATH
        with open(path, "r") as f:
            self._schema = json.load(f)
        self._validator = Draft202012Validator(self._schema)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, path: str) -> Playbook:
        """
        Load a single playbook from a YAML (.yaml/.yml) or JSON (.json) file.

        Validates against the playbook schema before building the typed object.

        Raises:
            FileNotFoundError       — file does not exist
            ValueError              — unsupported file extension
            PlaybookValidationError — schema validation failure
        """
        raw = self._read_file(path)
        errors = self.validate_raw(raw)
        if errors:
            raise PlaybookValidationError(path, errors)
        return Playbook.from_dict(raw)

    def load_directory(self, directory: Optional[str] = None) -> Dict[str, Playbook]:
        """
        Load all supported playbook files from a directory.

        Returns a dict of  rule_id -> Playbook  sorted by rule_id.
        Files that fail validation are skipped with a warning on stderr
        (so one bad file never blocks the rest from loading).

        Raises:
            FileNotFoundError — directory does not exist
        """
        target = directory or DEFAULT_PLAYBOOKS_DIR
        if not os.path.isdir(target):
            raise FileNotFoundError(f"Playbook directory not found: {target!r}")

        playbooks: Dict[str, Playbook] = {}
        for filename in sorted(os.listdir(target)):
            if not filename.endswith(_SUPPORTED_EXTENSIONS):
                continue
            filepath = os.path.join(target, filename)
            try:
                pb = self.load(filepath)
                playbooks[pb.rule_id] = pb
            except PlaybookValidationError as exc:
                print(
                    f"[PlaybookLoader] WARNING: skipping {filename!r} — "
                    f"validation failed: {exc.errors[0]}",
                    file=sys.stderr,
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[PlaybookLoader] WARNING: skipping {filename!r} — {exc}",
                    file=sys.stderr,
                )
        return dict(sorted(playbooks.items()))

    def validate_raw(self, data: dict) -> List[str]:
        """
        Validate a raw dict against the playbook schema without building
        a Playbook object.

        Returns a list of human-readable error strings.
        An empty list means the data is valid.
        """
        errors = []
        for err in self._validator.iter_errors(data):
            path = (
                " > ".join(str(p) for p in err.absolute_path)
                if err.absolute_path
                else "root"
            )
            errors.append(f"[{path}] {err.message}")
        return errors

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_file(self, path: str) -> dict:
        """Parse a YAML or JSON file into a raw dict."""
        _, ext = os.path.splitext(path.lower())
        if ext not in _SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file format {ext!r}. "
                f"Supported: {', '.join(_SUPPORTED_EXTENSIONS)}"
            )
        with open(path, "r") as f:
            if ext == ".json":
                return json.load(f)
            return yaml.safe_load(f)
