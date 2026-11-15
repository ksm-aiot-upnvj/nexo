"""Detect likely credentials without retaining their values."""

import math
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class CredentialFinding:
    category: str
    confidence: str


_DATABASE_URL = re.compile(
    r"(?i)\b(?:postgres(?:ql)?(?:\+[a-z0-9_]+)?|mysql|mongodb(?:\+srv)?|redis)://[^\s<>\"']+"
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"""(?ix)
    \b(?P<key>
        (?:[a-z0-9]+[_-])*
        (?:api[_-]?key|access[_-]?token|auth[_-]?token|
        client[_-]?secret|secret[_-]?key|secret|password|passwd|token)
    )\b
    \s*[:=]\s*
    [\"']?(?P<value>[^\s\"'`,;]{8,})
    """
)
_KNOWN_SECRET_PATTERNS = (
    ("github_token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "private_key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE),
    ),
)
_PLACEHOLDERS = {
    "changeme",
    "change-me",
    "example",
    "password",
    "replace_me",
    "your_api_key",
    "your_password",
    "your_secret",
    "your_token",
    "xxx",
    "xxxx",
}


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower().strip("{}[]()")
    return normalized in _PLACEHOLDERS or normalized.startswith(("example_", "your_"))


def _shannon_entropy(value: str) -> float:
    if not value:
        return 0.0

    length = len(value)
    return -sum(
        (count / length) * math.log2(count / length)
        for count in (value.count(char) for char in set(value))
    )


def _has_database_password(candidate: str) -> bool:
    try:
        password = urlsplit(candidate.rstrip(".,);]")).password
    except ValueError:
        return False
    return bool(password and not _is_placeholder(password))


def scan_credentials(text: str) -> list[CredentialFinding]:
    """Return categories and confidence only; never return matched secret text."""
    findings: dict[str, CredentialFinding] = {}

    for category, pattern in _KNOWN_SECRET_PATTERNS:
        if pattern.search(text):
            findings[category] = CredentialFinding(category, "high")

    if any(
        _has_database_password(match.group(0)) for match in _DATABASE_URL.finditer(text)
    ):
        findings["database_connection_url"] = CredentialFinding(
            "database_connection_url", "high"
        )

    if findings:
        return list(findings.values())

    for match in _CREDENTIAL_ASSIGNMENT.finditer(text):
        value = match.group("value").rstrip(".,:)")
        if _is_placeholder(value):
            continue
        if len(value) >= 8:
            confidence = (
                "medium"
                if len(value) >= 12 and _shannon_entropy(value) >= 2.5
                else "low"
            )
            category = f"{match.group('key').lower()}_value"
            findings.setdefault(category, CredentialFinding(category, confidence))

    return list(findings.values())
