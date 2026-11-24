"""GitHub access via the `gh` CLI, so Mercury reuses your existing `gh auth login`."""

from __future__ import annotations

import json
import shutil
import subprocess


class GitHubError(RuntimeError):
    pass


def _gh(*args: str) -> object:
    if shutil.which("gh") is None:
        raise GitHubError("GitHub CLI (gh) is not installed. Install it and run `gh auth login`.")
    proc = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if proc.returncode != 0:
        raise GitHubError(proc.stderr.strip() or f"gh {' '.join(args)} failed")
    return json.loads(proc.stdout or "null")


ISSUE_FIELDS = "number,title,body,state,url,labels,assignees,updatedAt"
PR_FIELDS = "number,title,state,url,headRefName,isDraft,author,updatedAt,reviewDecision"


def list_issues(repo: str, state: str = "open", limit: int = 100) -> list[dict]:
    return _gh("issue", "list", "--repo", repo, "--state", state, "--limit", str(limit), "--json", ISSUE_FIELDS)  # type: ignore[return-value]


def list_pulls(repo: str, state: str = "open", limit: int = 50) -> list[dict]:
    return _gh("pr", "list", "--repo", repo, "--state", state, "--limit", str(limit), "--json", PR_FIELDS)  # type: ignore[return-value]
