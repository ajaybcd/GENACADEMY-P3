import base64

import httpx
import pytest

from review_app.context import ContextReader, source_window
from review_app.demo import demo_review
from review_app.domain import DirectoryContext, ReviewError
from review_app.github import GitHub


def test_large_context_is_searchable_beyond_first_page_and_cached_at_pinned_sha():
    snapshot, _ = demo_review()
    snapshot.demo = False
    snapshot.head_repo = "acme/project"
    text = "# filler padded for a large real-world test file\n" * 2500 + "def test_target_behavior():\n    assert True\n"
    calls = []

    class Repository:
        def content(self, repo, path, sha, max_bytes):
            calls.append((repo, path, sha, max_bytes))
            return text

    reader = ContextReader(snapshot, Repository())
    assert reader.read("tests/test_large.py")["has_more"]
    result = reader.search("tests/test_large.py", "def test_target_behavior")
    assert result["matches"][0]["line"] == 2501
    page = reader.read("tests/test_large.py", 2498, 2502)
    assert "def test_target_behavior" in page["source"]
    assert not page["has_more"]
    assert page["total_lines"] == 2502
    assert len(calls) == 1
    assert calls[0][2] == snapshot.head_sha
    assert reader.used_bytes == len(text.encode())


def test_window_keeps_whole_lines_and_announces_more_content():
    result = source_window("x.py", "x" * 9000 + "\n" + "y" * 9000, 1, 2)
    assert result["end_line"] == 1
    assert result["has_more"] is True
    assert result["source"] == "x" * 9000


@pytest.mark.parametrize("start,end", [(0, 10), (2, 1), (1, 201), (20, 30)])
def test_invalid_page_ranges_are_explicit(start, end):
    with pytest.raises(ReviewError):
        source_window("x.py", "one\ntwo", start, end)


def test_context_budget_is_enforced_before_another_file_fetch():
    snapshot, _ = demo_review()
    snapshot.demo = False
    calls = []

    class Repository:
        def content(self, repo, path, sha, max_bytes):
            calls.append(path)
            return "a" * 1_000_000

    reader = ContextReader(snapshot, Repository())
    reader.content("one.py")
    reader.content("two.py")
    with pytest.raises(ReviewError, match="2 MB"):
        reader.content("three.py")
    assert calls == ["one.py", "two.py"]


def test_github_allows_large_context_but_enforces_requested_limit():
    text = "# code\n" * 16000
    calls = []

    def handle(request):
        calls.append(request)
        assert request.url.params["ref"] == "pinned"
        return httpx.Response(200, json={"type": "file", "encoding": "base64", "size": len(text),
                                        "content": base64.b64encode(text.encode()).decode()})

    gh = GitHub(client=httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handle)))
    assert gh.content("a/b", "tests/test_large.py", "pinned", max_bytes=1_000_000) == text
    assert gh.content("a/b", "tests/test_large.py", "pinned", max_bytes=1_000_000) == text
    assert len(calls) == 1
    with pytest.raises(ReviewError, match="60 KB"):
        gh.content("a/b", "tests/test_large.py", "pinned", max_bytes=60_000)
    assert len(calls) == 1


def test_content_cache_never_reuses_a_different_commit():
    calls = []

    def handle(request):
        sha = request.url.params["ref"]
        calls.append(sha)
        return httpx.Response(200, json={"type": "file", "encoding": "base64", "size": len(sha),
                                        "content": base64.b64encode(sha.encode()).decode()})

    gh = GitHub(client=httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handle)))
    assert gh.content("a/b", "file.py", "first") == "first"
    assert gh.content("a/b", "file.py", "second") == "second"
    assert gh.content("a/b", "file.py", "first") == "first"
    assert calls == ["first", "second"]


def test_directory_discovery_is_pinned_and_bounded():
    def handle(request):
        assert request.url.params["ref"] == "pinned"
        return httpx.Response(200, json=[{"path": f"tests/test_{i}.py", "type": "file", "size": i} for i in range(120)])

    gh = GitHub(client=httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handle)))
    entries = gh.list_directory("a/b", "tests", "pinned")
    assert len(entries) == 100
    assert entries[0]["path"] == "tests/test_0.py"


def test_searching_directory_returns_results_and_explicit_scope_instead_of_read_error():
    snapshot, _ = demo_review()
    snapshot.demo = False

    class Repository:
        def content(self, repo, path, sha, max_bytes):
            if path == "tests":
                raise DirectoryContext([{"path": f"tests/test_{i}.py", "type": "file"} for i in range(8)]
                                       + [{"path": "tests/nested", "type": "dir"}])
            return "def test_target():\n    pass\n"

    result = ContextReader(snapshot, Repository()).search("tests", "test_target")
    assert result["kind"] == "directory_search"
    assert len(result["searched_files"]) == 6
    assert result["total_matches"] == 6
    assert len(result["skipped_files"]) == 2
    assert result["complete"] is False
    assert result["unsearched_subdirectories"] == ["tests/nested"]
