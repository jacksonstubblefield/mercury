"""Source Control tab: status, staging, diffs, commits, push/pull against a local bare remote."""

from __future__ import annotations

import subprocess

from claude_agent_sdk import ResultMessage

IDENTITY = ["-c", "user.name=T", "-c", "user.email=t@example.com"]


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def _repo(tmp_path, name="repo"):
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "user.email", "t@example.com")
    return repo


def _project(client, repo):
    r = client.post("/api/projects", json={"name": "Demo", "repo_path": str(repo)})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _paths(entries):
    return sorted(e["path"] for e in entries)


def test_status_stage_unstage_commit(client, tmp_path):
    repo = _repo(tmp_path)
    pid = _project(client, repo)
    (repo / "a.txt").write_text("one\n")
    (repo / "sub").mkdir()
    (repo / "sub" / "b [1].txt").write_text("b\n")  # brackets would be a glob without literal pathspecs

    st = client.get(f"/api/projects/{pid}/scm/status").json()
    assert st["is_repo"] and st["branch"] == "main" and st["head"] is None
    assert _paths(st["untracked"]) == ["a.txt", "sub/b [1].txt"]

    # Unstage works before the first commit too.
    st = client.post(f"/api/projects/{pid}/scm/stage", json={"paths": ["sub/b [1].txt"]}).json()
    assert _paths(st["staged"]) == ["sub/b [1].txt"]
    st = client.post(f"/api/projects/{pid}/scm/unstage", json={"paths": None}).json()
    assert st["staged"] == [] and len(st["untracked"]) == 2

    r = client.post(f"/api/projects/{pid}/scm/commit", json={"message": "first"})
    assert r.status_code == 409 and "no staged changes" in r.json()["detail"]

    r = client.post(f"/api/projects/{pid}/scm/commit", json={"message": "first\n\nbody", "stage_all": True})
    assert r.status_code == 200, r.text
    assert r.json()["status"]["untracked"] == []
    assert _git(repo, "log", "-1", "--pretty=%B").strip() == "first\n\nbody"

    (repo / "a.txt").write_text("two\n")
    (repo / "c.txt").write_text("c\n")
    st = client.post(f"/api/projects/{pid}/scm/stage", json={"paths": ["c.txt"]}).json()
    assert [(e["path"], e["status"]) for e in st["staged"]] == [("c.txt", "A")]
    assert [(e["path"], e["status"]) for e in st["unstaged"]] == [("a.txt", "M")]

    d = client.get(f"/api/projects/{pid}/scm/diff", params={"path": "a.txt", "kind": "unstaged"}).json()
    assert "-one" in d["diff"] and "+two" in d["diff"]
    d = client.get(f"/api/projects/{pid}/scm/diff", params={"path": "c.txt", "kind": "staged"}).json()
    assert "+c" in d["diff"]

    # Only the staged file is committed.
    r = client.post(f"/api/projects/{pid}/scm/commit", json={"message": "add c", "stage_all": True}).json()
    assert _paths(r["status"]["unstaged"]) == ["a.txt"]
    assert _git(repo, "show", "--name-only", "--pretty=", "HEAD").split() == ["c.txt"]


def test_untracked_diff_and_discard(client, tmp_path):
    repo = _repo(tmp_path)
    (repo / "keep.txt").write_text("v1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    pid = _project(client, repo)

    (repo / "keep.txt").write_text("v2\n")
    (repo / "new.txt").write_text("hello\n")
    (repo / "other.txt").write_text("stay\n")

    d = client.get(f"/api/projects/{pid}/scm/diff", params={"path": "new.txt", "kind": "untracked"}).json()
    assert "+hello" in d["diff"]

    st = client.post(f"/api/projects/{pid}/scm/discard", json={"paths": ["keep.txt", "new.txt"]}).json()
    assert (repo / "keep.txt").read_text() == "v1\n"
    assert not (repo / "new.txt").exists()
    assert _paths(st["untracked"]) == ["other.txt"] and st["unstaged"] == []


def test_file_changes_and_contents_for_agent_paths(client, tmp_path):
    """The Agents tab asks about files by the absolute paths agents report."""
    repo = _repo(tmp_path)
    (repo / "docs").mkdir()
    (repo / "docs" / "a.md").write_text("one\n")
    (repo / "gone.txt").write_text("bye\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    pid = _project(client, repo)
    url = f"/api/projects/{pid}/scm"

    # Clean file: no diff.
    d = client.get(f"{url}/file-changes", params={"path": str(repo / "docs" / "a.md")}).json()
    assert d["path"] == "docs/a.md" and d["tracked"] and d["exists"] and d["diff"] == ""

    # Staged and unstaged edits both count as "since the last commit".
    (repo / "docs" / "a.md").write_text("two\n")
    _git(repo, "add", "docs/a.md")
    (repo / "docs" / "a.md").write_text("three\n")
    d = client.get(f"{url}/file-changes", params={"path": str(repo / "docs" / "a.md")}).json()
    assert "-one" in d["diff"] and "+three" in d["diff"] and "two" not in d["diff"]

    (repo / "docs" / "new.md").write_bytes(b"# Roadmap\n")
    d = client.get(f"{url}/file-changes", params={"path": "docs/new.md"}).json()
    assert not d["tracked"] and "+# Roadmap" in d["diff"]

    (repo / "gone.txt").unlink()
    d = client.get(f"{url}/file-changes", params={"path": str(repo / "gone.txt")}).json()
    assert d["tracked"] and not d["exists"] and "-bye" in d["diff"]

    f = client.get(f"{url}/file", params={"path": str(repo / "docs" / "new.md")}).json()
    assert f == {"path": "docs/new.md", "binary": False, "content": "# Roadmap\n", "truncated": False}
    (repo / "img.bin").write_bytes(b"\x89PNG\0\0\0")
    assert client.get(f"{url}/file", params={"path": "img.bin"}).json()["binary"] is True
    assert client.get(f"{url}/file", params={"path": "gone.txt"}).status_code == 409

    # Nothing outside the checkout can be read, whichever way the path is spelled.
    (tmp_path / "secret.txt").write_text("no\n")
    for bad in [str(tmp_path / "secret.txt"), "../secret.txt", "docs/../../secret.txt", str(repo)]:
        for endpoint in ("file", "file-changes"):
            r = client.get(f"{url}/{endpoint}", params={"path": bad})
            assert r.status_code == 409 and "outside" in r.json()["detail"], (endpoint, bad, r.text)


def test_file_changes_before_first_commit(client, tmp_path):
    repo = _repo(tmp_path)
    (repo / "a.txt").write_text("hi\n")
    _git(repo, "add", "a.txt")
    pid = _project(client, repo)
    d = client.get(f"/api/projects/{pid}/scm/file-changes", params={"path": "a.txt"}).json()
    assert d["tracked"] and "+hi" in d["diff"]


def test_push_publish_and_pull(client, tmp_path):
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    repo = _repo(tmp_path)
    _git(repo, "remote", "add", "origin", str(remote))
    (repo / "a.txt").write_text("a\n")
    pid = _project(client, repo)

    r = client.post(f"/api/projects/{pid}/scm/commit", json={"message": "a", "stage_all": True, "push": True})
    assert r.status_code == 200, r.text
    assert "error" not in r.json()
    st = r.json()["status"]
    assert st["upstream"] == "origin/main" and st["ahead"] == 0

    # Someone else pushes; pull brings it in.
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(remote), str(other))
    (other / "b.txt").write_text("b\n")
    _git(other, "add", ".")
    _git(other, *IDENTITY, "commit", "-qm", "b")
    _git(other, "push", "-q")

    st = client.post(f"/api/projects/{pid}/scm/fetch").json()["status"]
    assert st["behind"] == 1
    st = client.post(f"/api/projects/{pid}/scm/pull").json()["status"]
    assert st["behind"] == 0 and (repo / "b.txt").exists()


def test_push_without_remote_and_non_repo(client, tmp_path):
    repo = _repo(tmp_path)
    (repo / "a.txt").write_text("a\n")
    pid = _project(client, repo)
    client.post(f"/api/projects/{pid}/scm/commit", json={"message": "a", "stage_all": True})
    r = client.post(f"/api/projects/{pid}/scm/push")
    assert r.status_code == 409 and "no remote" in r.json()["detail"]

    plain = tmp_path / "plain"
    plain.mkdir()
    pid2 = _project(client, plain)
    assert client.get(f"/api/projects/{pid2}/scm/status").json() == {"is_repo": False}
    st = client.post(f"/api/projects/{pid2}/scm/init").json()
    assert st["is_repo"] is True


class FakeMessageClient:
    instances: list["FakeMessageClient"] = []

    def __init__(self, options):
        self.options = options
        self.prompt = None
        FakeMessageClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, prompt):
        self.prompt = prompt

    async def receive_response(self):
        yield ResultMessage(
            subtype="success",
            duration_ms=5,
            duration_api_ms=4,
            is_error=False,
            num_turns=1,
            session_id="s",
            result="```\nAdd greeting file\n```",
        )


def test_generate_commit_message(make_client, tmp_path):
    client = make_client(FakeMessageClient)
    repo = _repo(tmp_path)
    pid = _project(client, repo)

    r = client.post(f"/api/projects/{pid}/scm/generate-message")
    assert r.status_code == 409  # nothing to describe

    (repo / "greeting.txt").write_text("hello\n")
    r = client.post(f"/api/projects/{pid}/scm/generate-message")
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "Add greeting file"

    fake = FakeMessageClient.instances[-1]
    assert "+hello" in fake.prompt and "greeting.txt" in fake.prompt
    assert fake.options.tools == [] and fake.options.setting_sources == []
