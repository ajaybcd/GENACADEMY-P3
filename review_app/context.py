"""Pinned context retrieval with full-file caching and bounded model-facing windows."""
from .domain import DirectoryContext, ReviewError


def source_window(path: str, content: str, start_line: int = 1, end_line: int = 160) -> dict:
    if start_line < 1 or end_line < start_line or end_line - start_line >= 200:
        raise ReviewError("Request a valid range of at most 200 lines.")
    lines = content.splitlines()
    if start_line > len(lines) and lines:
        raise ReviewError(f"Start line exceeds the file's {len(lines)} lines.")
    result, length = [], 0
    for line in lines[start_line - 1:end_line]:
        if length + len(line) + 1 > 16000:
            if not result:
                raise ReviewError("One source line exceeds the 16,000 character response limit.")
            break
        result.append(line)
        length += len(line) + 1
    last = start_line + len(result) - 1
    return {"path": path, "start_line": start_line, "end_line": last, "total_lines": len(lines),
            "has_more": last < len(lines), "source": "\n".join(result)}


class ContextReader:
    def __init__(self, snapshot, github):
        self.snapshot = snapshot
        self.github = github
        self.cache = {}
        self.used_bytes = 0

    def content(self, path: str) -> str:
        if path in self.snapshot.files:
            return self.snapshot.files[path].content
        if path in self.cache:
            return self.cache[path]
        if self.snapshot.demo or self.github is None:
            raise ReviewError("Additional repository context is unavailable in the offline demonstration.")
        if len(self.cache) >= 6:
            raise ReviewError("Additional context limit reached: six files per specialist.")
        remaining = 2_000_000 - self.used_bytes
        if remaining <= 0:
            raise ReviewError("Additional context limit reached: 2 MB per specialist.")
        text = self.github.content(self.snapshot.head_repo, path, self.snapshot.head_sha,
                                   max_bytes=min(1_000_000, remaining))
        self.used_bytes += len(text.encode("utf-8"))
        self.cache[path] = text
        return text

    def read(self, path: str, start_line: int = 1, end_line: int = 160) -> dict:
        return source_window(path, self.content(path), start_line, end_line)

    def search(self, path: str, text: str) -> dict:
        if not text or len(text) > 200:
            raise ReviewError("Search text must contain 1–200 characters.")
        try:
            content = self.content(path)
        except DirectoryContext as directory:
            return self.search_directory(path, text, directory.entries)
        matches = [{"line": i, "source": line[:500]} for i, line in enumerate(content.splitlines(), 1) if text in line]
        return {"path": path, "matches": matches[:30], "total_matches": len(matches),
                "has_more_matches": len(matches) > 30}

    def search_directory(self, path: str, text: str, entries: list[dict]) -> dict:
        candidates = [e["path"] for e in entries if e["type"] == "file" and e["path"].endswith(".py")]
        candidates.sort(key=lambda p: (p not in self.snapshot.files, p not in self.cache, p))
        matches, searched, skipped = [], [], []
        for filename in candidates[:6]:
            try:
                content = self.content(filename)
                searched.append(filename)
                matches.extend({"path": filename, "line": i, "source": line[:300]}
                               for i, line in enumerate(content.splitlines(), 1) if text in line)
            except ReviewError as error:
                skipped.append({"path": filename, "reason": str(error)})
        skipped.extend({"path": p, "reason": "Six-file directory search limit"} for p in candidates[6:])
        subdirectories = [e["path"] for e in entries if e["type"] == "dir"]
        return {"path": path, "kind": "directory_search", "matches": matches[:30],
                "total_matches": len(matches), "has_more_matches": len(matches) > 30,
                "searched_files": searched, "skipped_files": skipped,
                "unsearched_subdirectories": subdirectories,
                "complete": not skipped and not subdirectories,
                "scope": "Direct Python files only; at most six files. Search a specific file or subdirectory to refine."}

    def list_files(self, directory: str = "") -> list[dict]:
        if self.snapshot.demo or self.github is None:
            return [{"path": p, "type": "file"} for p in self.snapshot.files if p.startswith(directory)]
        return self.github.list_directory(self.snapshot.head_repo, directory, self.snapshot.head_sha)
