from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    meili_url: str = os.getenv("MEILI_URL", "http://127.0.0.1:7700")
    meili_key: str = os.getenv("MEILI_MASTER_KEY", "ptgen-local-master-key")
    index_name: str = os.getenv("MEILI_INDEX", "works")
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

    ptgen_repo_url: str = os.getenv("PTGEN_REPO_URL", "https://github.com/ourbits/PtGen.git")
    ptgen_path: Path = Path(os.getenv("PTGEN_PATH", "/data/ptgen"))
    ptgen_branch: str = os.getenv("PTGEN_BRANCH", "main")
    skip_git_update: bool = env_bool("PTGEN_SKIP_GIT_UPDATE", False)
    max_files_per_source: int = env_int("PTGEN_MAX_FILES_PER_SOURCE", 0)
    include_files: str = os.getenv("PTGEN_INCLUDE_FILES", "")

    state_dir: Path = Path(os.getenv("STATE_DIR", "/data/state"))
    poster_cache_dir: Path = Path(os.getenv("POSTER_CACHE_DIR", "/data/posters"))
    poster_max_bytes: int = env_int("POSTER_MAX_BYTES", 5_000_000)
    poster_fetch_timeout_seconds: int = env_int("POSTER_FETCH_TIMEOUT_SECONDS", 20)
    poster_failure_ttl_seconds: int = env_int("POSTER_FAILURE_TTL_SECONDS", 3600)
    ingest_run_on_start: bool = env_bool("INGEST_RUN_ON_START", True)
    ingest_interval_seconds: int = env_int("INGEST_INTERVAL_SECONDS", 86400)
    ingest_batch_size: int = env_int("INGEST_BATCH_SIZE", 2500)

    @property
    def include_file_list(self) -> list[str]:
        return [item.strip() for item in self.include_files.split(",") if item.strip()]


def get_settings() -> Settings:
    return Settings()
