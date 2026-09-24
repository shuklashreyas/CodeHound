"""Canonical GitHub identifiers. User URLs never become arbitrary network targets."""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
REPO = r"[A-Za-z0-9_.-]{1,100}"


@dataclass(frozen=True)
class PullReference:
    owner: str
    repo: str
    number: int

    @property
    def repository(self):
        return f"{self.owner}/{self.repo}"

    @property
    def url(self):
        return f"https://github.com/{self.repository}/pull/{self.number}"


def parse_pull_url(value: str) -> PullReference:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Provide a public GitHub pull request URL.")
    value = value.strip()
    if any(ord(char) < 33 for char in value) or "\\" in value:
        raise ValueError("The pull request URL contains invalid characters.")
    try:
        parsed = urlsplit(value)
        match = re.fullmatch(rf"/({OWNER})/({REPO})/pull/([1-9][0-9]{{0,9}})/?", parsed.path)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname == "github.com"
            and not parsed.username
            and not parsed.password
            and not parsed.port
            and not parsed.query
            and not parsed.fragment
            and match
        )
    except ValueError as exc:
        raise ValueError("Provide https://github.com/owner/repo/pull/123.") from exc
    if not valid or match[2] in (".", "..") or match[1].endswith("-"):
        raise ValueError("Provide https://github.com/owner/repo/pull/123 without query parameters.")
    return PullReference(match[1], match[2], int(match[3]))
