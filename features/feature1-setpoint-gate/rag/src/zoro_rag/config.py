from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import os

import yaml


@dataclass(frozen=True)
class Settings:
    root: Path
    data: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path = "config/settings.yaml") -> "Settings":
        config_path = Path(path).resolve()
        with config_path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        for env, key in (("ZORO_DEVICE", "device"), ("ZORO_MODEL_CACHE", "cache_dir"), ("ZORO_SPARSE_CACHE", "sparse_cache_dir")):
            if os.environ.get(env):
                data.setdefault("models", {})[key] = os.environ[env]
        return cls(config_path.parent.parent, data)

    def path(self, *keys: str) -> Path:
        value: Any = self.data
        for key in keys:
            value = value[key]
        path = Path(value)
        return path if path.is_absolute() else self.root / path

    def get(self, *keys: str, default: Any = None) -> Any:
        value: Any = self.data
        for key in keys:
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value

