"""Shared routing predicates for deliberate worker delegation."""

import re

from src.agent_worker_adapters import worker_catalog


_PROJECT_WORK_ACTION_RE = re.compile(
    r"\b(?:analy[sz]e|audit|build|change|check|compare|create|debug|deploy|diagnose|edit|fix|"
    r"find|implement|inspect|investigate|patch|pull|push|read|restart|review|run|search|start|stop|test|"
    r"update|verify|write)\b",
    re.I,
)
_PROJECT_WORK_NEGATOR_RE = re.compile(r"\b(?:do\s+not|don[’']t|dont|never|not\s+to)\b", re.I)
# A period ends a clause only when it is followed by whitespace or the end of
# the turn. Splitting every period breaks ordinary project artifact names such
# as README.md into separate clauses, separating the requested action from its
# project scope and silently leaving the selected worker unused.
_CLAUSE_BOUNDARY_RE = re.compile(r"[!?;,\n]|\.(?=\s|$)|\b(?:but|however)\b", re.I)
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


def selected_worker_workspace(worker: str, message: str) -> str | None:
    """Resolve a selected worker's workspace from installation-owned aliases.

    An explicitly named allowed alias wins. The selected Friday/PC-Codex lane
    otherwise belongs to Home Lab; it must not inherit a stale Business alias
    from an older conversation or let the reasoning model guess one.
    """
    details = worker_catalog().get(str(worker or "")) or {}
    allowed = [str(item) for item in details.get("workspaces") or [] if str(item)]
    value = str(message or "").lower()
    for alias in allowed:
        if re.search(rf"(?<![a-z0-9_-]){re.escape(alias.lower())}(?![a-z0-9_-])", value):
            return alias
    if worker == "pc-codex" and "home-lab" in allowed:
        return "home-lab"
    return allowed[0] if allowed else None
