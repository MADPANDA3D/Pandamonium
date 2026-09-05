"""Shared routing predicates for deliberate worker delegation."""

import re


_PROJECT_WORK_ACTION_RE = re.compile(
    r"\b(?:analy[sz]e|audit|build|change|check|debug|deploy|diagnose|edit|fix|implement|inspect|"
    r"investigate|patch|pull|push|read|restart|review|run|test|update|verify|write)\b",
    re.I,
)
_PROJECT_WORK_SCOPE_RE = re.compile(
    r"\b(?:branches?|code|commits?|configurations?|containers?|deployments?|files?|git|hosts?|issues?|"
    r"projects?|pull requests?|repos?|repositories|scripts?|services?|servers?|sources?|systemd|tests?)\b",
    re.I,
)


def is_explicit_project_work_request(message: str) -> bool:
    """Return true only when a turn names both project work and its action."""
    text = str(message or "")
    return bool(_PROJECT_WORK_ACTION_RE.search(text) and _PROJECT_WORK_SCOPE_RE.search(text))
