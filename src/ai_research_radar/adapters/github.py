"""GitHub adapter — releases + recent push/tag activity via the REST API.

* Uses only public endpoints; honours GITHUB_TOKEN when present
  (60 req/h anonymous → 5000 req/h authenticated).
* Pattern expansion ("openai/*") hits the search API once per pattern, then
  /releases and /events per concrete repo.
* Every failure degrades to a warning line — one dead repo never stops a run.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
import time

from .. import net
from ..config import RadarConfig
from ..models import RawItem
from .base import FetchResult, SourceAdapter

_API = "https://api.github.com"

_gh_token_cache: str | None = None


def _token() -> str:
    """GITHUB_TOKEN env var, falling back to the local `gh` CLI if present.

    Never cached to disk and never logged; the gh fallback merely saves
    individuals from copy-pasting their token into an env file.
    """
    global _gh_token_cache
    if _gh_token_cache is not None:
        return _gh_token_cache
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if not tok and shutil.which("gh"):
        try:
            proc = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if proc.returncode == 0:
                tok = proc.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            tok = ""
    _gh_token_cache = tok
    return tok


class GitHubAdapter(SourceAdapter):
    source_type = "github"

    def __init__(self, patterns: list[str],
                 config: RadarConfig | None = None) -> None:
        super().__init__(config)
        self.patterns = patterns
        self.max_releases = int(config.options.get("github_max_releases_per_repo", 5)) \
            if config else 5
        self.include_activity = bool(config.options.get("github_include_activity", True)) \
            if config else True

    # -- API helpers -----------------------------------------------------------

    def _get(self, path: str, params: str = "") -> tuple[object | list | None, str]:
        url = f"{_API}{path}"
        if params:
            url += f"?{params}"
        try:
            data = net.fetch_json(url, token=_token())
            return data, ""
        except (RuntimeError, ValueError) as exc:
            return None, str(exc)

    def expand_patterns(self) -> tuple[list[str], list[str]]:
        """Return (repos, warnings). Patterns without '*' are used verbatim."""
        repos: set[str] = set()
        warnings: list[str] = []
        for pattern in self.patterns:
            owner, repo_pat = pattern.split("/", 1)
            if "*" not in pattern:
                repos.add(pattern)
                continue
            data, err = self._get(f"/users/{owner}/repos", "per_page=100&sort=pushed")
            if not isinstance(data, list):
                warnings.append(f"github pattern {pattern}: {err or 'unexpected response'}")
                continue
            matches = [
                f"{owner}/{r['name']}"
                for r in data
                if isinstance(r, dict) and "name" in r
                and fnmatch.fnmatch(str(r["name"]).lower(), repo_pat)
            ]
            if not matches:
                warnings.append(f"github pattern {pattern}: no matching repositories")
            repos.update(matches[:30])  # safety cap per pattern
        return sorted(repos), warnings

    # -- fetching ----------------------------------------------------------------

    def fetch(self) -> FetchResult:
        repos, warnings = self.expand_patterns()
        items: list[RawItem] = []
        for repo in repos:
            items.extend(self._repo_items(repo, warnings))
            time.sleep(0.15)  # be polite to the API
        return FetchResult(items=items, errors=warnings)

    def _repo_items(self, repo: str, warnings: list[str]) -> list[RawItem]:
        out: list[RawItem] = []

        releases, err = self._get(f"/repos/{repo}/releases", f"per_page={self.max_releases}")
        if releases is None:
            warnings.append(f"github {repo} releases: {err}")
        elif isinstance(releases, list):
            for rel in releases[: self.max_releases]:
                if not isinstance(rel, dict):
                    continue
                tag = str(rel.get("tag_name", "")).strip()
                name = str(rel.get("name", "")).strip()
                title = f"{repo} release {tag}" + (f": {name}" if name else "")
                url = str(rel.get("html_url", "")) or f"https://github.com/{repo}/releases"
                published = str(rel.get("published_at", "") or rel.get("created_at", ""))
                body = str(rel.get("body", "") or "").strip()
                prerelease = bool(rel.get("prerelease"))
                prefix = "[pre-release] " if prerelease else ""
                out.append(
                    RawItem(
                        source=f"github:{repo}",
                        source_type="github",
                        title=title[:500],
                        url=url,
                        published_at=published.replace("Z", "Z"),
                        author=str((rel.get("author") or {}).get("login", "") or ""),
                        raw_content=prefix + body,
                    )
                )

        if self.include_activity:
            events, err = self._get(f"/repos/{repo}/events", "per_page=100")
            if events is None:
                warnings.append(f"github {repo} events: {err}")
            elif isinstance(events, list):
                pushes = sum(
                    1 for e in events
                    if isinstance(e, dict) and e.get("type") == "PushEvent"
                )
                tags = sum(
                    1 for e in events
                    if isinstance(e, dict) and e.get("type") == "CreateEvent"
                    and (e.get("payload") or {}).get("ref_type") == "tag"
                )
                if pushes >= 3 or tags >= 1:
                    latest = ""
                    for e in events:
                        if isinstance(e, dict):
                            latest = str(e.get("created_at", "")).replace("Z", "Z")
                            break
                    out.append(
                        RawItem(
                            source=f"github:{repo}",
                            source_type="github",
                            title=f"[activity] {repo}: {pushes} pushes, {tags} new tags (recent)",
                            url=f"https://github.com/{repo}/commits",
                            published_at=latest,
                            author="",
                            raw_content=(
                                f"Repository activity signal: {pushes} PushEvent(s) and "
                                f"{tags} CreateEvent(tag) in the latest page of events."
                            ),
                        )
                    )
        return out
