"""Create (if needed) and update the Hugging Face Space that hosts the app.

Prereq (once):  hf auth login   (paste a *write* token from huggingface.co/settings/tokens)
Run:            .venv/bin/python scripts/deploy_hf.py [space-name]
"""
from __future__ import annotations

import sys
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent.parent
api = HfApi()
user = api.whoami()["name"]
name = sys.argv[1] if len(sys.argv) > 1 else "invoice-to-decision"
repo_id = f"{user}/{name}"

api.create_repo(repo_id, repo_type="space", space_sdk="docker", exist_ok=True, private=False)
api.upload_folder(
    folder_path=str(ROOT),
    repo_id=repo_id,
    repo_type="space",
    commit_message="Deploy invoice-to-decision app",
    ignore_patterns=[".venv/*", "runtime/*", "**/__pycache__/*", ".pytest_cache/*", ".env", ".git/*", ".claude/*", "*.pyc"],
)
sub = repo_id.replace("/", "-").replace("_", "-").lower()
print(f"Space:   https://huggingface.co/spaces/{repo_id}")
print(f"App URL: https://{sub}.hf.space")
