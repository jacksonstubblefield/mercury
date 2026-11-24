"""Read-only git information for a project's local checkout."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def _git(repo: str, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except FileNotFoundError as exc:  # git not installed
        raise GitError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)} timed out") from exc
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def github_repo_from_remote(url: str) -> str | None:
    """Turn a GitHub remote URL into "owner/name"."""
    m = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?/?$", url.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def repo_summary(repo_path: str, commit_limit: int = 15) -> dict:
    path = Path(repo_path)
    if not path.is_dir():
        raise GitError(f"Folder not found: {repo_path}")
    if _git(repo_path, "rev-parse", "--is-inside-work-tree").strip() != "true":
        raise GitError(f"Not a git repository: {repo_path}")

    try:
        branch = _git(repo_path, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except GitError:
        branch = None  # no commits yet

    status_lines = [line for line in _git(repo_path, "status", "--porcelain").splitlines() if line]
    changed = [{"status": line[:2].strip(), "path": line[3:]} for line in status_lines]

    commits: list[dict] = []
    if branch:
        sep = "\x1f"
        log = _git(
            repo_path,
            "log",
            f"-n{commit_limit}",
            f"--pretty=format:%H{sep}%h{sep}%an{sep}%aI{sep}%s",
        )
        for line in log.splitlines():
            full, short, author, date, subject = line.split(sep, 4)
            commits.append(
                {"sha": full, "short_sha": short, "author": author, "date": date, "subject": subject}
            )

    remote = None
    try:
        remote = _git(repo_path, "remote", "get-url", "origin").strip()
    except GitError:
        pass

    ahead = behind = None
    if branch:
        try:
            counts = _git(repo_path, "rev-list", "--left-right", "--count", "HEAD...@{upstream}").split()
            ahead, behind = int(counts[0]), int(counts[1])
        except GitError:
            pass  # no upstream

    return {
        "branch": branch,
        "remote": remote,
        "github_repo": github_repo_from_remote(remote) if remote else None,
        "changed_files": changed,
        "ahead": ahead,
        "behind": behind,
        "commits": commits,
    }
