"""Shared routing predicates for deliberate worker delegation."""

import re


_PROJECT_WORK_ACTION_RE = re.compile(
    r"\b(?:analy[sz]e|audit|build|change|check|compare|create|debug|deploy|diagnose|edit|fix|"
    r"find|implement|inspect|investigate|patch|pull|push|read|restart|review|run|search|start|stop|test|"
    r"update|verify|write)\b",
    re.I,
)
_PROJECT_WORK_NEGATOR_RE = re.compile(r"\b(?:do\s+not|don[’']t|dont|never|not\s+to)\b", re.I)
_CLAUSE_BOUNDARY_RE = re.compile(r"[.!?;,\n]|\b(?:but|however)\b", re.I)
_PROJECT_WORK_SCOPE_RE = re.compile(
    r"\b(?:apis?|branches?|bugs?|code|commits?|configurations?|containers?|deployments?|files?|git|hosts?|issues?|"
    r"projects?|pull requests?|repos?|repositor(?:y|ies)|scripts?|services?|servers?|sources?|systemd|tests?)\b",
    re.I,
)
_CONTEXTUAL_WORK_TARGET_RE = re.compile(r"\b(?:it|that|this|them|those|again)\b", re.I)
_APP_DATA_SCOPE_RE = re.compile(
    r"\b(?:books?|library|calendar|emails?|mailbox|messages?|notes?|tasks?|to[- ]?dos?|reminders?)\b",
    re.I,
)
_APP_DATA_LOOKUP_RE = re.compile(
    r"\b(?:check|find|inspect|list|open|read|search|show|summari[sz]e)\s+"
    r"(?:all\s+|every\s+)?(?:(?:my|the|this|that|an?)\s+)?"
    r"(?:books?|library|calendar|emails?|mailbox|messages?|notes?|tasks?|to[- ]?dos?|reminders?)\b",
    re.I,
)


def _project_work_clauses(message: str):
    """Yield clauses whose project action is positive and not an app lookup."""
    for clause in _CLAUSE_BOUNDARY_RE.split(str(message or "")):
        action = _PROJECT_WORK_ACTION_RE.search(clause)
        if not action:
            continue
        negator = _PROJECT_WORK_NEGATOR_RE.search(clause)
        if negator and _PROJECT_WORK_ACTION_RE.search(clause, negator.end()):
            continue
        if _APP_DATA_LOOKUP_RE.search(clause):
            continue
        yield clause


def is_explicit_project_work_request(message: str) -> bool:
    """Return true only when a turn names both project work and its action."""
    return any(_PROJECT_WORK_SCOPE_RE.search(clause) for clause in _project_work_clauses(message))


def is_contextual_project_work_followup(message: str) -> bool:
    """Return true for action-only follow-ups that still need an active task."""
    return any(
        _CONTEXTUAL_WORK_TARGET_RE.search(clause)
        and not _APP_DATA_SCOPE_RE.search(clause)
        for clause in _project_work_clauses(message)
    )
