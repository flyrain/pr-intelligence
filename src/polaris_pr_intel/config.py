from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    github_token: str
    github_owner: str = "apache"
    github_repo: str = "polaris"
    github_webhook_secret: str = ""



def load_settings() -> Settings:
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required")
    return Settings(
        github_token=token,
        github_owner=os.getenv("GITHUB_OWNER", "apache"),
        github_repo=os.getenv("GITHUB_REPO", "polaris"),
        github_webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET", ""),
    )
