import pytest


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEHOUND_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")


@pytest.fixture
def github_bundle():
    import copy

    repo = {"id": 11, "full_name": "octocat/project", "private": False}
    pull = {
        "number": 7,
        "title": "Fix expired sessions",
        "body": "Keep refresh tokens valid.",
        "state": "open",
        "base": {"sha": "a" * 40, "ref": "main", "repo": repo},
        "head": {"sha": "b" * 40, "ref": "fix", "repo": repo},
        "changed_files": 1,
        "additions": 1,
        "deletions": 1,
    }
    file = {
        "filename": "tests/test_auth.py",
        "status": "modified",
        "additions": 1,
        "deletions": 1,
        "changes": 2,
        "sha": "d" * 40,
        "patch": "@@ -1 +1 @@\n-assert False\n+assert True",
    }
    diff = (
        "diff --git a/tests/test_auth.py b/tests/test_auth.py\n"
        "--- a/tests/test_auth.py\n+++ b/tests/test_auth.py\n"
        "@@ -1 +1 @@\n-assert False\n+assert True\n"
    )

    class FakeGitHub:
        def __init__(self):
            self.repo = copy.deepcopy(repo)
            self.pull = copy.deepcopy(pull)
            self.after = None
            self.comparison = {
                "base_commit": {"sha": "a" * 40},
                "merge_base_commit": {"sha": "c" * 40},
                "files": [copy.deepcopy(file)],
            }
            self.patch = diff
            self.calls = []
            self.failure = None
            self.pull_reads = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def json(self, path, params=None):
            self.calls.append(path)
            if self.failure:
                raise self.failure
            if "/compare/" in path:
                return copy.deepcopy(self.comparison)
            if "/pulls/" in path:
                self.pull_reads += 1
                return copy.deepcopy(
                    self.after if self.pull_reads > 1 and self.after else self.pull
                )
            return copy.deepcopy(self.repo)

        async def diff(self, path):
            self.calls.append(path)
            return self.patch

    return FakeGitHub()


@pytest.fixture
def authenticated_client():
    import time

    from fastapi.testclient import TestClient

    from codehound.api import github
    from codehound.main import app

    github.sessions["test-session"] = {
        "token": "test-token",
        "user": {"id": 123, "login": "octocat"},
        "expires": time.time() + 600,
    }
    with TestClient(app) as client:
        client.cookies.set(github.SESSION_COOKIE, "test-session")
        yield client
    github.sessions.pop("test-session", None)
