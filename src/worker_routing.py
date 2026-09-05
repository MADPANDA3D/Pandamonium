"""Shared routing predicates for deliberate worker delegation."""

import re


_PROJECT_WORK_ACTION_RE = re.compile(
    r"\b(?:analy[sz]e|audit|build|change|check|compare|create|debug|deploy|diagnose|edit|fix|"
    r"implement|inspect|investigate|patch|pull|push|read|restart|review|run|start|stop|test|"
    r"update|verify|write)\b",
    re.I,
)
_PROJECT_WORK_SCOPE_RE = re.compile(
    r"\b(?:apis?|branches?|bugs?|code|commits?|configurations?|containers?|deployments?|files?|git|hosts?|issues?|"
    r"projects?|pull requests?|repos?|repositories|scripts?|services?|servers?|sources?|systemd|tests?)\b",
    re.I,
)
_CONTEXTUAL_WORK_TARGET_RE = re.compile(r"\b(?:it|that|this|them|those|again)\b", re.I)
_APP_DATA_SCOPE_RE = re.compile(
    r"\b(?:books?|library|calendar|emails?|mailbox|messages?|notes?|tasks?|to[- ]?dos?|reminders?)\b",
    re.I,
)


def is_explicit_project_work_request(message: str) -> bool:
    """Return true only when a turn names both project work and its action."""
    text = str(message or "")
    return bool(_PROJECT_WORK_ACTION_RE.search(text) and _PROJECT_WORK_SCOPE_RE.search(text))


def is_contextual_project_work_followup(message: str) -> bool:
    """Return true for action-only follow-ups that still need an active task."""
    text = str(message or "")
    return bool(
        _PROJECT_WORK_ACTION_RE.search(text)
        and _CONTEXTUAL_WORK_TARGET_RE.search(text)
        and not _APP_DATA_SCOPE_RE.search(text)
    )
