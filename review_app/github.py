from __future__ import annotations

import base64
import time
from urllib.parse import quote

import httpx

from .domain import DirectoryContext, PRRef, ReviewError, Snapshot, SourceFile, added_lines


class GitHub:
    """Bounded GitHub REST reads; write requests are never automatically retried."""

    def __init__(self, token: str = "", client: httpx.Client | None = None):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = client or httpx.Client(base_url="https://api.github.com", headers=headers,
                                           timeout=30, follow_redirects=False)
        self._content_cache = {}
        self._directory_cache = {}
        self._cached_bytes = 0

    def close(self):
        self.client.close()

    def get(self, path: str, **params):
        for attempt in range(3):
            try:
                response = self.client.get(path, params=params)
                retry = response.status_code in {429, 500, 502, 503, 504}
                if response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0":
                    raise ReviewError("GitHub rate limit reached. Wait until it resets or use a token.")
                if retry and attempt < 2:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                if response.status_code in {401, 403, 404}:
                    raise ReviewError("GitHub access failed. Check the PR URL and token repository permissions.")
                if response.status_code != 200:
                    raise ReviewError(f"GitHub read failed (HTTP {response.status_code}).")
                return response.json()
            except httpx.RequestError:
                if attempt == 2:
                    raise ReviewError("GitHub could not be reached after three attempts.") from None
                time.sleep(0.5 * (2 ** attempt))
        raise ReviewError("GitHub read did not complete.")

    def content(self, repo: str, path: str, sha: str, max_bytes: int = 250_000) -> str:
        if not path or path.startswith("/") or ".." in path.split("/") or "\\" in path:
            raise ReviewError("Invalid repository-relative path.")
        cache_key = (repo, path, sha)
        if cache_key in self._content_cache:
            text = self._content_cache[cache_key]
            if len(text.encode()) > max_bytes:
                raise ReviewError(f"File exceeds the {max_bytes // 1000} KB read limit.")
            return text
        data = self.get(f"/repos/{repo}/contents/{quote(path, safe='/')}", ref=sha)
        if isinstance(data, list):
            raise DirectoryContext(data)
        if not isinstance(data, dict) or data.get("type") != "file" or data.get("encoding") != "base64":
            raise ReviewError("Context is not a supported text file.")
        if data.get("size", 0) > max_bytes:
            raise ReviewError(f"File exceeds the {max_bytes // 1000} KB read limit.")
        try:
            text = base64.b64decode(data["content"]).decode("utf-8")
        except (UnicodeError, ValueError, KeyError):
            raise ReviewError("File is not readable UTF-8 text.") from None
        if "\x00" in text or len(text.encode()) > max_bytes:
            raise ReviewError("Binary or oversized context file.")
        if len(self._content_cache) < 32 and self._cached_bytes + len(text.encode()) <= 6_000_000:
            self._content_cache[cache_key] = text
            self._cached_bytes += len(text.encode())
        return text

    def list_directory(self, repo: str, directory: str, sha: str) -> list[dict]:
        if directory.startswith("/") or ".." in directory.split("/") or "\\" in directory:
            raise ReviewError("Invalid repository-relative directory.")
        cache_key = (repo, directory, sha)
        if cache_key in self._directory_cache:
            return self._directory_cache[cache_key]
        data = self.get(f"/repos/{repo}/contents/{quote(directory, safe='/')}", ref=sha)
        if not isinstance(data, list):
            raise ReviewError("Requested path is not a repository directory.")
        entries = [{"path": item["path"], "type": item["type"], "size": item.get("size", 0)} for item in data[:100]]
        if len(self._directory_cache) < 20:
            self._directory_cache[cache_key] = entries
        return entries

    def snapshot(self, ref: PRRef) -> Snapshot:
        metadata = self.get(ref.api_path)
        if not metadata["head"].get("repo"):
            raise ReviewError("The PR head repository is no longer available.")
        snapshot = Snapshot(ref.url, metadata["title"][:500], metadata["head"]["sha"],
                            metadata["base"]["sha"], metadata["head"]["repo"]["full_name"], {})
        total = int(metadata["changed_files"])
        if total > 100:
            raise ReviewError("This MVP supports PRs with at most 100 changed files. Choose a smaller PR.")
        changed = self.get(ref.api_path + "/files", per_page=100)
        if len(changed) != total:
            raise ReviewError("The changed-file listing is incomplete. Retry with a smaller PR.")
        size = 0
        for item in changed:
            path, patch = item["filename"], item.get("patch", "")
            if not path.endswith(".py") or item["status"] == "removed":
                snapshot.skipped.append(f"{path}: outside Python added/modified-file scope")
                continue
            if len(snapshot.files) >= 15:
                snapshot.skipped.append(f"{path}: 15-file limit")
                continue
            try:
                added_lines(patch)
                if len(patch) > 40_000:
                    raise ReviewError("Patch exceeds the 40 KB limit.")
                content = self.content(snapshot.head_repo, path, snapshot.head_sha)
                if size + len(content.encode()) + len(patch.encode()) > 240_000:
                    raise ReviewError("Run exceeds the 240 KB source and patch limit.")
                size += len(content.encode()) + len(patch.encode())
                snapshot.files[path] = SourceFile(path, patch, content)
            except ReviewError as error:
                snapshot.skipped.append(f"{path}: {error}")
        current = self.get(ref.api_path)
        if current["head"]["sha"] != snapshot.head_sha or current["base"]["sha"] != snapshot.base_sha:
            raise ReviewError("PR changed while loading. Start a new review.")
        if not snapshot.files:
            detail = "; ".join(snapshot.skipped[:3])
            raise ReviewError(f"No reviewable Python changes were found. {detail}")
        return snapshot

    def find_review(self, ref: PRRef, run_id: str) -> str | None:
        marker = f"<!-- week3-review:{run_id} -->"
        for page in range(1, 11):
            reviews = self.get(ref.api_path + "/reviews", per_page=100, page=page)
            for review in reviews:
                if marker in (review.get("body") or ""):
                    return review["html_url"]
            if len(reviews) < 100:
                return None
        raise ReviewError("Too many reviews to safely reconcile this run.")

    def post_review(self, ref: PRRef, sha: str, findings: list[dict], run_id: str) -> str:
        payload = {
            "commit_id": sha, "event": "COMMENT",
            "body": f"[AI Review] Human-approved findings. AI-generated; verify before acting.\n\n<!-- week3-review:{run_id} -->",
            "comments": [{"path": f["path"], "line": f["line"], "side": "RIGHT",
                          "body": f"[AI Review] {f['severity']} · {f['category']}\n\n{f['body']}\n\nAI-generated; human approved."}
                         for f in findings],
        }
        try:
            response = self.client.post(ref.api_path + "/reviews", json=payload)
            if response.status_code != 200:
                raise ReviewError(f"GitHub did not confirm the post (HTTP {response.status_code}). Reconcile before any new run.")
            return response.json()["html_url"]
        except (httpx.RequestError, ValueError, KeyError):
            raise ReviewError("Post result is uncertain. Reconcile this run; do not retry posting automatically.") from None
