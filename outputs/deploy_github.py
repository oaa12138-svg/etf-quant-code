"""把最新页面发布到 GitHub Pages（使用 Contents API，绕开 git TLS 问题）。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "outputs" / "recommendation_workflow.html"
COPY = ROOT / "work" / "github-pages" / "index.html"
UPLOAD = ROOT / "work" / "upload.json"
GH_CONFIG = ROOT / "work" / "gh-config"

OWNER = "oaa12138-svg"
REPO = "etf-workflow"
BRANCH = "main"
REMOTE_PATH = "index.html"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["GH_CONFIG_DIR"] = str(GH_CONFIG)
    return subprocess.run(cmd, env=env, capture_output=True, text=True)


def _blob_sha(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def main() -> int:
    if not PAGE.exists():
        print(f"page not found: {PAGE}")
        return 1

    COPY.parent.mkdir(parents=True, exist_ok=True)
    COPY.write_bytes(PAGE.read_bytes())

    remote = _run([
        "gh", "api",
        f"repos/{OWNER}/{REPO}/contents/{REMOTE_PATH}?ref={BRANCH}",
        "--jq", ".sha",
    ])
    remote_sha = remote.stdout.strip() if remote.returncode == 0 else ""

    local_sha = _blob_sha(COPY)

    if remote_sha and local_sha and remote_sha == local_sha:
        print("no change; skip GitHub upload")
        return 0

    payload = {
        "message": f"Update ETF workflow {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "content": base64.b64encode(PAGE.read_bytes()).decode("ascii"),
        "branch": BRANCH,
    }
    if remote_sha:
        payload["sha"] = remote_sha
    UPLOAD.write_text(json.dumps(payload), encoding="utf-8")

    result = _run([
        "gh", "api", "-X", "PUT",
        f"repos/{OWNER}/{REPO}/contents/{REMOTE_PATH}",
        "--input", str(UPLOAD),
    ])
    if result.returncode != 0:
        print("upload failed:", result.stderr[:500])
        return 1
    print("uploaded to GitHub Pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
