"""Deploy CampaignPilot to a free Hugging Face Space (Docker).

Run by GitHub Actions on every push to main (see .github/workflows/deploy.yml),
or by hand:

    HF_TOKEN=hf_xxx GOOGLE_API_KEY=xxx python scripts/deploy_space.py

Environment variables:
    HF_TOKEN         Hugging Face token with WRITE access (required)
    GOOGLE_API_KEY   Gemini key, stored as a Space *secret* (optional after the first deploy)
    ACCESS_CODE      optional code visitors must enter before running agents
    SPACE_NAME       Space name (default: campaignpilot)

The Space is created on the first run. Its README needs Hugging Face's
config header, so the script uploads a copy of the repo with that header
added instead of changing the GitHub README.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent.parent
INCLUDE = ["campaignpilot", "scripts", "data/knowledge", "Dockerfile", "requirements.txt", "LICENSE"]
SPACE_HEADER = """---
title: CampaignPilot
emoji: 📣
colorFrom: yellow
colorTo: gray
sdk: docker
app_port: 8000
pinned: false
short_description: Multi-agent marketing assistant (LangGraph, RAG, MCP)
---

"""
SPACE_VARIABLES = {
    "LLM_PROVIDER": "gemini",
    "TOOLS_MODE": "mcp",
    "QDRANT_URL": "local",
    "MAX_RUNS_PER_HOUR": "20",
}


def build_bundle(dest: Path) -> None:
    for item in INCLUDE:
        src = ROOT / item
        if not src.exists():
            continue
        target = dest / item
        if src.is_dir():
            shutil.copytree(src, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    (dest / "README.md").write_text(SPACE_HEADER + readme, encoding="utf-8")


def main() -> int:
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        print("HF_TOKEN is not set; skipping deployment.")
        return 0
    api = HfApi(token=token)
    user = api.whoami()["name"]
    repo_id = f"{user}/{os.environ.get('SPACE_NAME', 'campaignpilot').strip() or 'campaignpilot'}"

    api.create_repo(repo_id, repo_type="space", space_sdk="docker", exist_ok=True)
    print(f"Space ready: {repo_id}")

    if key := os.environ.get("GOOGLE_API_KEY", "").strip():
        api.add_space_secret(repo_id, "GOOGLE_API_KEY", key)
    if code := os.environ.get("ACCESS_CODE", "").strip():
        api.add_space_secret(repo_id, "ACCESS_CODE", code)
    # Route AI calls through the deployed LLM Gateway when its URL and key are provided.
    gateway_url = os.environ.get("GATEWAY_URL", "").strip()
    if gateway_url and (gw_key := os.environ.get("GATEWAY_API_KEY", "").strip()):
        api.add_space_secret(repo_id, "GATEWAY_API_KEY", gw_key)
        api.add_space_variable(repo_id, "GATEWAY_URL", gateway_url)
        variables = {**SPACE_VARIABLES, "LLM_PROVIDER": "gateway"}
    else:
        variables = SPACE_VARIABLES
    for name, value in variables.items():
        api.add_space_variable(repo_id, name, value)

    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp)
        build_bundle(bundle)
        api.upload_folder(
            repo_id=repo_id, repo_type="space", folder_path=bundle,
            commit_message=f"Deploy {os.environ.get('GITHUB_SHA', 'local')[:7]}",
            # remove app files that no longer exist in the repo (keeps the Space's .gitattributes)
            delete_patterns=["campaignpilot/**", "scripts/**", "data/**", "*.txt", "Dockerfile"],
        )

    subdomain = repo_id.replace("/", "-").replace("_", "-").lower()
    print(f"Deployed. Space page: https://huggingface.co/spaces/{repo_id}")
    print(f"App URL (after the build finishes): https://{subdomain}.hf.space")
    return 0


if __name__ == "__main__":
    sys.exit(main())
