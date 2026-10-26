from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urlparse


class ReviewError(Exception):
    """A user-facing review failure with no secret-bearing details."""


class DirectoryContext(ReviewError):
    """A repository path resolved to a directory rather than a file."""
    def __init__(self, entries: list[dict]):
        super().__init__("Path is a directory; use directory discovery or bounded directory search.")
        self.entries = [{"path": e["path"], "type": e["type"]} for e in entries[:100]]


@dataclass(frozen=True)
class PRRef:
    owner: str
    repo: str
    number: int

    @classmethod
    def parse(cls, url: str) -> PRRef:
        parsed = urlparse(url.strip())
        match = re.fullmatch(r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9]\d*)/?", parsed.path)
        if parsed.scheme != "https" or parsed.netloc != "github.com" or not match or parsed.query or parsed.fragment:
            raise ReviewError("Use a GitHub URL such as https://github.com/owner/repo/pull/123.")
        owner, repo, number = match.groups()
        if owner in {".", ".."} or repo in {".", ".."}:
            raise ReviewError("Invalid repository name.")
        return cls(owner, repo, int(number))

    @property
    def api_path(self) -> str:
        return f"/repos/{self.owner}/{self.repo}/pulls/{self.number}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"


def added_lines(patch: str) -> set[int]:
    """Parse new-side line numbers and reject malformed or truncated hunks."""
    result: set[int] = set()
    new_line = None
    old_left = new_left = 0
    seen = False
    for raw in patch.splitlines():
        header = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", raw)
        if header:
            if old_left or new_left:
                raise ReviewError("Incomplete diff hunk.")
            old_left = int(header[2]) if header[2] is not None else 1
            new_left = int(header[4]) if header[4] is not None else 1
            new_line = int(header[3])
            seen = True
            continue
        if new_line is None or raw.startswith("\\"):
            continue
        if raw.startswith("+"):
            result.add(new_line)
            new_line += 1
            new_left -= 1
        elif raw.startswith("-"):
            old_left -= 1
        elif raw.startswith(" "):
            old_left -= 1
            new_left -= 1
            new_line += 1
        else:
            raise ReviewError("Invalid diff hunk.")
        if old_left < 0 or new_left < 0:
            raise ReviewError("Invalid diff counts.")
    if not seen or old_left or new_left:
        raise ReviewError("Missing or truncated patch.")
    return result


@dataclass
class SourceFile:
    path: str
    patch: str
    content: str


@dataclass
class Snapshot:
    url: str
    title: str
    head_sha: str
    base_sha: str
    head_repo: str
    files: dict[str, SourceFile]
    skipped: list[str] = field(default_factory=list)
    demo: bool = False

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Snapshot:
        return cls(**{**data, "files": {p: SourceFile(**f) for p, f in data["files"].items()}})


def anchor(source: SourceFile, snippet: str) -> int:
    """Require exact, unique full-line evidence and anchor to an added line."""
    lines, needle = source.content.splitlines(), snippet.splitlines()
    if not needle or not snippet.strip() or len(snippet) > 4000:
        raise ReviewError("Finding needs a nonempty snippet of at most 4,000 characters.")
    matches = [i for i in range(len(lines) - len(needle) + 1) if lines[i:i + len(needle)] == needle]
    if len(matches) != 1:
        raise ReviewError("Snippet must match exactly one location in the pinned file.")
    changed = added_lines(source.patch)
    candidates = [i for i in range(matches[0] + 1, matches[0] + len(needle) + 1) if i in changed]
    if not candidates:
        raise ReviewError("Snippet has no added line in the diff.")
    return candidates[-1]


def validate_findings(candidates: list[dict], snapshot: Snapshot) -> tuple[list[dict], list[str]]:
    findings, dropped, seen = [], [], set()
    for candidate in candidates:
        try:
            path = candidate.get("path", "")
            category = candidate.get("category", "")
            severity = candidate.get("severity", "")
            body, snippet = candidate.get("body", ""), candidate.get("snippet", "")
            if category not in {"Correctness", "Security", "Tests"} or severity not in {"Critical", "High", "Medium", "Low"}:
                raise ReviewError("Invalid category or severity.")
            if not isinstance(body, str) or not 10 <= len(body) <= 3000:
                raise ReviewError("Finding body must contain 10–3,000 characters.")
            if path not in snapshot.files or not isinstance(snippet, str):
                raise ReviewError("Finding references an unreviewed file or invalid snippet.")
            line = anchor(snapshot.files[path], snippet)
            # Keep distinct categories at one line; collapse exact repeated findings.
            key = (path, line, category, " ".join(body.lower().split()))
            if key in seen:
                continue
            seen.add(key)
            identity = hashlib.sha256(repr(key).encode()).hexdigest()[:16]
            findings.append(dict(id=identity, path=path, line=line, category=category,
                                 severity=severity, body=body, snippet=snippet))
        except (ReviewError, TypeError, AttributeError) as error:
            dropped.append(str(error) if isinstance(error, ReviewError) else "Malformed finding.")
    rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    findings.sort(key=lambda f: (rank[f["severity"]], f["path"], f["line"]))
    return findings, dropped
