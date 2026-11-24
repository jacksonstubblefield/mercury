"""Source control actions for a project's checkout: status, diffs, staging, commits, push and pull.

Every command runs at the repository's top level, so paths are always relative to the repo root.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Literal

from .gitinfo import GitError

LOCAL_TIMEOUT = 30
# Push, pull and fetch talk to a server, and commit may run hooks.
SLOW_TIMEOUT = 180
MAX_DIFF_CHARS = 400_000
MAX_STATUS_ENTRIES = 5_000
MAX_FILE_BYTES = 1_000_000

_ENV = {
    # Fail instead of waiting for a username/password prompt no one can answer.
    # (Credential managers with their own window, like Git Credential Manager, still work.)
    "GIT_TERMINAL_PROMPT": "0",
    # Paths from the UI are file names, never globs or pathspec magic.
    "GIT_LITERAL_PATHSPECS": "1",
}

DiffKind = Literal["staged", "unstaged", "untracked"]


def _run(
    repo: str,
    *args: str,
    input: str | None = None,
    timeout: int = LOCAL_TIMEOUT,
    ok: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", repo, *args],
            input=input,
            stdin=subprocess.DEVNULL if input is None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**os.environ, **_ENV},
        )
    except FileNotFoundError as exc:
        raise GitError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} timed out after {timeout}s") from exc
    if proc.returncode not in ok:
        raise GitError(proc.stderr.strip() or proc.stdout.strip() or f"git {' '.join(args)} failed")
    return proc


def _git(repo: str, *args: str, **kw) -> str:
    return _run(repo, *args, **kw).stdout


def _nul(paths: list[str]) -> str:
    return "\0".join(paths)


def repo_root(repo_path: str) -> str | None:
    """The checkout's top-level folder, or None if `repo_path` isn't inside a git repository."""
    if not Path(repo_path).is_dir():
        raise GitError(f"Folder not found: {repo_path}")
    proc = _run(repo_path, "rev-parse", "--show-toplevel", ok=(0, 128))
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def require_root(repo_path: str) -> str:
    root = repo_root(repo_path)
    if root is None:
        raise GitError(f"Not a git repository: {repo_path}")
    return root


def init(repo_path: str) -> None:
    _git(repo_path, "init")


# --------------------------------------------------------------------------- status


def status(root: str) -> dict:
    out = _git(
        root, "--no-optional-locks", "status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all"
    )
    tokens = out.split("\0")
    head = {"oid": None, "branch": None, "upstream": None, "ahead": None, "behind": None}
    staged: list[dict] = []
    unstaged: list[dict] = []
    untracked: list[dict] = []
    conflicted: list[dict] = []
    count = 0
    truncated = False

    i = 0
    while i < len(tokens):
        line = tokens[i]
        i += 1
        if not line:
            continue
        if line.startswith("# "):
            key, _, value = line[2:].partition(" ")
            if key == "branch.oid":
                head["oid"] = None if value == "(initial)" else value
            elif key == "branch.head":
                head["branch"] = None if value == "(detached)" else value
            elif key == "branch.upstream":
                head["upstream"] = value
            elif key == "branch.ab":
                a, b = value.split()
                head["ahead"], head["behind"] = int(a), abs(int(b))
            continue

        if count >= MAX_STATUS_ENTRIES:
            truncated = True
            if line[0] == "2":
                i += 1  # skip the rename's original path
            continue
        count += 1

        kind = line[0]
        if kind == "?":
            untracked.append({"path": line[2:], "status": "U"})
        elif kind == "u":
            path = line.split(" ", 10)[10]
            conflicted.append({"path": path, "status": "!", "xy": line[2:4]})
        elif kind in "12":
            parts = line.split(" ", 9 if kind == "2" else 8)
            x, y = parts[1][0], parts[1][1]
            path = parts[-1]
            orig = None
            if kind == "2":
                orig = tokens[i]
                i += 1
            if x != ".":
                staged.append({"path": path, "status": x, "orig_path": orig})
            if y != ".":
                unstaged.append({"path": path, "status": y, "orig_path": None})

    remotes = [r for r in _git(root, "remote").splitlines() if r]
    return {
        "is_repo": True,
        "root": root,
        "branch": head["branch"],
        "head": head["oid"],
        "upstream": head["upstream"],
        "ahead": head["ahead"],
        "behind": head["behind"],
        "remotes": remotes,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "conflicted": conflicted,
        "truncated": truncated,
    }


# --------------------------------------------------------------------------- diffs


def file_diff(root: str, path: str, kind: DiffKind, orig_path: str | None = None) -> dict:
    base = ["diff", "--no-color", "--no-ext-diff", "-M"]
    if kind == "untracked":
        # --no-index exits 1 when the files differ, which they always do here.
        text = _git(root, *base, "--no-index", "--", "/dev/null", path, ok=(0, 1))
    else:
        if kind == "staged":
            base.append("--cached")
        text = _git(root, *base, "--", *([orig_path] if orig_path else []), path)
    truncated = len(text) > MAX_DIFF_CHARS
    return {"path": path, "kind": kind, "diff": text[:MAX_DIFF_CHARS], "truncated": truncated}


def relative_path(root: str, path: str) -> str:
    """`path` (absolute, or relative to the repo root) as a repo-relative POSIX path.

    Agents report absolute paths. Anything that resolves outside the checkout, including through a
    symlink, is refused, because these endpoints must never read files elsewhere on the machine.
    """
    root_p = Path(root).resolve()
    p = Path(path)
    if not p.is_absolute():
        p = root_p / p
    p = p.resolve()
    # Compare case-insensitively where the filesystem is (Windows): agents don't always match the case on disk.
    root_s, p_s = os.path.normcase(str(root_p)), os.path.normcase(str(p))
    if p_s == root_s or not p_s.startswith(root_s.rstrip(os.sep) + os.sep):
        raise GitError(f"{path} is outside this project's repository.")
    return Path(str(p)[len(str(root_p)) :].lstrip("\\/")).as_posix()


def _empty_tree(root: str) -> str:
    return _git(root, "hash-object", "-t", "tree", "--stdin", input="").strip()


def changes_since_commit(root: str, path: str) -> dict:
    """Everything that differs in one file since the last commit, staged or not, new or deleted."""
    rel = relative_path(root, path)
    exists = (Path(root) / rel).is_file()
    tracked = bool(_git(root, "ls-files", "--", rel).strip())
    base = ["diff", "--no-color", "--no-ext-diff"]
    if tracked:
        has_head = _run(root, "rev-parse", "--verify", "-q", "HEAD", ok=(0, 1)).returncode == 0
        text = _git(root, *base, "HEAD" if has_head else _empty_tree(root), "--", rel)
    elif exists:
        text = _git(root, *base, "--no-index", "--", "/dev/null", rel, ok=(0, 1))
    else:
        text = ""
    truncated = len(text) > MAX_DIFF_CHARS
    return {"path": rel, "tracked": tracked, "exists": exists, "diff": text[:MAX_DIFF_CHARS], "truncated": truncated}


def read_file(root: str, path: str) -> dict:
    """A file's current contents, for viewing. Binary files report that rather than their bytes."""
    rel = relative_path(root, path)
    full = Path(root) / rel
    if not full.is_file():
        raise GitError(f"{rel} doesn't exist (it may have been deleted or moved).")
    with full.open("rb") as fh:
        data = fh.read(MAX_FILE_BYTES + 1)
    truncated = len(data) > MAX_FILE_BYTES
    data = data[:MAX_FILE_BYTES]
    if b"\0" in data[:8000]:
        return {"path": rel, "binary": True, "content": "", "truncated": False}
    return {"path": rel, "binary": False, "content": data.decode("utf-8", errors="replace"), "truncated": truncated}


def changes_for_message(root: str, max_chars: int = 60_000) -> dict:
    """The diff a commit message should describe: staged changes, or every change if nothing is staged."""
    st = status(root)
    staged_only = bool(st["staged"])
    if staged_only:
        stat = _git(root, "diff", "--cached", "--stat", "--no-color")
        diff = _git(root, "diff", "--cached", "--no-color", "--no-ext-diff", "-M")
    else:
        tracked_base = ["HEAD"] if st["head"] else []
        stat = _git(root, "diff", *tracked_base, "--stat", "--no-color")
        diff = _git(root, "diff", *tracked_base, "--no-color", "--no-ext-diff", "-M")
        for f in st["untracked"]:
            if len(diff) >= max_chars:
                break
            stat += f" {f['path']} (new file)\n"
            diff += file_diff(root, f["path"], "untracked")["diff"]
    truncated = len(diff) > max_chars
    log = _git(root, "log", "-n10", "--pretty=format:%s", ok=(0, 128)) if st["head"] else ""
    return {
        "branch": st["branch"],
        "staged_only": staged_only,
        "stat": stat.strip(),
        "diff": diff[:max_chars],
        "truncated": truncated,
        "recent_subjects": [s for s in log.splitlines() if s],
    }


# --------------------------------------------------------------------------- staging


def stage(root: str, paths: list[str] | None) -> None:
    if paths is None:
        _git(root, "add", "-A")
    elif paths:
        _git(root, "add", "-A", "--pathspec-from-file=-", "--pathspec-file-nul", input=_nul(paths))


def unstage(root: str, paths: list[str] | None) -> None:
    if paths is not None and not paths:
        return
    has_head = _run(root, "rev-parse", "--verify", "-q", "HEAD", ok=(0, 1)).returncode == 0
    if not has_head:
        # No commits yet: there's nothing to reset to, so drop the files from the index instead.
        args = ["rm", "--cached", "-r", "-q", "--ignore-unmatch"]
        if paths is None:
            _git(root, *args, "--", ".")
        else:
            _git(root, *args, "--pathspec-from-file=-", "--pathspec-file-nul", input=_nul(paths))
    elif paths is None:
        _git(root, "reset", "-q")
    else:
        _git(root, "reset", "-q", "--pathspec-from-file=-", "--pathspec-file-nul", input=_nul(paths))


def discard(root: str, paths: list[str] | None) -> int:
    """Throw away working-tree changes. Untracked files are deleted. Returns how many files changed."""
    st = status(root)
    tracked = {f["path"] for f in st["unstaged"]}
    untracked = {f["path"] for f in st["untracked"]}
    if paths is not None:
        wanted = set(paths)
        tracked &= wanted
        untracked &= wanted
    if tracked:
        _git(
            root, "restore", "--worktree", "--pathspec-from-file=-", "--pathspec-file-nul",
            input=_nul(sorted(tracked)),
        )
    todo = sorted(untracked)
    for i in range(0, len(todo), 100):  # `git clean` takes paths only as arguments
        _git(root, "clean", "-f", "-q", "--", *todo[i : i + 100])
    return len(tracked) + len(untracked)


# --------------------------------------------------------------------------- commit / sync


def commit(root: str, message: str, stage_all: bool = False) -> dict:
    if not message.strip():
        raise GitError("Write a commit message first.")
    st = status(root)
    if st["conflicted"]:
        raise GitError("Resolve the merge conflicts and stage the files before committing.")
    if not st["staged"]:
        if not stage_all:
            raise GitError("There are no staged changes to commit.")
        if not (st["unstaged"] or st["untracked"]):
            raise GitError("There are no changes to commit.")
        stage(root, None)
    proc = _run(root, "commit", "-F", "-", input=message.strip() + "\n", timeout=SLOW_TIMEOUT)
    sha = _git(root, "rev-parse", "--short", "HEAD").strip()
    return {"sha": sha, "output": (proc.stdout + proc.stderr).strip()}


def _default_remote(st: dict) -> str:
    if not st["remotes"]:
        raise GitError("This repository has no remote to push to. Add one with `git remote add origin <url>`.")
    return "origin" if "origin" in st["remotes"] else st["remotes"][0]


def push(root: str) -> dict:
    st = status(root)
    if st["branch"] is None:
        raise GitError("You're not on a branch (detached HEAD), so there's nothing to push.")
    if st["head"] is None:
        raise GitError("Make a commit before pushing.")
    if st["upstream"]:
        proc = _run(root, "push", timeout=SLOW_TIMEOUT)
    else:
        # First push of this branch: publish it and track the remote branch.
        proc = _run(root, "push", "-u", _default_remote(st), "HEAD", timeout=SLOW_TIMEOUT)
    return {"output": (proc.stdout + proc.stderr).strip()}


def pull(root: str) -> dict:
    st = status(root)
    if not st["upstream"]:
        raise GitError("This branch doesn't track a remote branch yet. Push it first to publish it.")
    proc = _run(root, "pull", "--no-edit", timeout=SLOW_TIMEOUT)
    return {"output": (proc.stdout + proc.stderr).strip()}


def fetch(root: str) -> dict:
    st = status(root)
    if not st["remotes"]:
        raise GitError("This repository has no remote to fetch from.")
    proc = _run(root, "fetch", "--prune", timeout=SLOW_TIMEOUT)
    return {"output": (proc.stdout + proc.stderr).strip()}
