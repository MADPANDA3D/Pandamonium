"""Shared routing predicates for deliberate worker delegation."""

import re


_PROJECT_WORK_ACTION_RE = re.compile(
    r"\b(?:analy[sz]e|audit|build|change|debug|deploy|diagnose|edit|fix|implement|inspect|"
    r"investigate|patch|pull|push|restart|review|run|test|update|verify|write)\b",
    re.I,
)
_PROJECT_WORK_SCOPE_RE = re.compile(
    r"\b(?:branch|code|commit|configuration|container|deployment|file|git|host|issue|"
    r"project|pull request|repo(?:sitory)?|script|service|server|source|systemd|test)\b",
    re.I,
)


def is_explicit_project_work_request(message: str) -> bool:
    """Return true only when a turn names both project work and its action."""
    text = str(message or "")
    return bool(_PROJECT_WORK_ACTION_RE.search(text) and _PROJECT_WORK_SCOPE_RE.search(text))
