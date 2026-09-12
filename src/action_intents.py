"""Lightweight domain hints for adaptive conversation turns.

The harness, not the user, decides whether a normal conversation needs tools.
These conservative patterns add deterministic domain/policy hints while the
existing agent selector handles broader semantic tool discovery.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Pattern


@dataclass(frozen=True)
class ToolIntent:
    """A cheap, deterministic tool-domain routing hint."""

    needs_tools: bool
    category: str = ""
    reason: str = ""


def resolve_conversation_execution_mode(
    requested_mode: str | None,
    *,
    compare_mode: bool = False,
) -> str:
    """Return the internal execution path for a conversational turn.

    Normal sessions always enter the adaptive agent loop, whose low-signal
    fast path sends no tool schemas and whose tool selector exposes only the
    capabilities relevant to the turn. Compare is an explicit evaluation
    surface, so its chat/agent baselines remain distinct.
    """
    requested = str(requested_mode or "").strip().lower()
    if compare_mode and requested in {"chat", "agent"}:
        return requested
    return "agent"


_ACTION_QUESTION = r"\b(?:can|could|would|will)\s+you\s+"
_ACTION_FOLLOWUP = (
    r"\b(?:you\s+should\s+be\s+able\s+to|"
    r"(?:can|could|would|will|should)\s+you|"
    r"you\s+(?:can|could|would|will|should|need\s+to|have\s+to))\s+"
)
_PLEASE = r"^\s*(?:(?:please|ok(?:ay)?|alright|right|sure|cool|great|thanks)[\s,.!-]+)*"

_CALENDAR_ACTION = (
    r"(?:add|adding|create|creating|recreate|recreating|schedule|scheduling|"
    r"reschedule|rescheduling|book|booking|put|set\s+up|make|making|"
    r"delete|deleting|remove|removing|cancel|cancelling|canceling)"
)
_CALENDAR_THING = r"(?:calendar|calendar\s+(?:entry|item)|event|meeting|appointment|entry|call)"
_CALENDAR_READ_THING = r"(?:calendar|schedule|events?|meetings?|appointments?|classes?)"
_EXPLANATORY_PREFIX = re.compile(
    r"^\s*(?:how\s+(?:do|can)\s+i|can\s+you\s+explain|what\s+about|tell\s+me\s+how|show\s+me\s+how)\b",
    re.I,
)

_PANEL = (
    r"(?:calendar|notes?|inbox|email|mail|documents?|docs|library|gallery|"
    r"settings|cookbook|sessions?|chats?|skills|memories|memory|brain)"
)

# This installation's own release/version/update state is local truth. These
# patterns win over the generic web/research patterns below so a question about
# Pandamonium releases (even one phrased as "search the web for ...") answers
# from `/api/version`, the updater state, and the curated `.github/releases`
# notes instead of drifting to unrelated public results. An explicit
# self-reference keeps another project's changelog/release notes on the web
# path.
_RELEASE_SELF_REFERENCE = r"\b(?:pandamonium|odysseus|jarvis[-\s]?os)\b"
_RELEASE_TERMS = (
    r"\b(?:releases?|release\s+notes?|changelog|versions?|updates?|"
    r"up(?: |-)?to(?: |-)?date|latest\s+build)\b"
)
_RELEASE_FIRST_PERSON = (
    r"\b(?:what|which)\s+version\b.{0,60}\b(?:am i|are you|is\s+(?:this|the)\s+(?:app|install|installation|build))\b|"
    r"\bam i\s+(?:on|running)\s+(?:the\s+)?latest\b|"
    r"\bis\s+(?:pandamonium|odysseus|jarvis[-\s]?os)\s+up(?: |-)?to(?: |-)?date\b|"
    r"\b(?:is there|are there|check(?: for)?)\s+(?:an?\s+)?updates?\b(?:\s+(?:available|now|please))?\s*[?.!]*\s*$|"
    r"\bupdates?\s+available\s*[?.!]*\s*$"
)
_RELEASE_SELF_KNOWLEDGE = re.compile(
    rf"(?:{_RELEASE_SELF_REFERENCE}.{{0,80}}{_RELEASE_TERMS}|"
    rf"{_RELEASE_TERMS}.{{0,80}}{_RELEASE_SELF_REFERENCE}|"
    rf"{_RELEASE_SELF_REFERENCE}.{{0,40}}\b(?:repo(?:sitory)?|github)\b|"
    rf"\bwhat(?:'s| is)\s+new\b.{{0,40}}{_RELEASE_SELF_REFERENCE}|"
    rf"\brelease\s+notes?\b.{{0,60}}\b(?:installed|current|this)\s+(?:version|build|install(?:ation)?|release)\b|"
    rf"\b(?:installed|current|this)\s+(?:version|build|install(?:ation)?|release)\b.{{0,60}}\brelease\s+notes?\b|"
    rf"{_RELEASE_FIRST_PERSON})",
    re.I,
)


def is_release_self_knowledge(text: str) -> bool:
    """Return True when a message asks about this installation's release state."""
    return bool(text and _RELEASE_SELF_KNOWLEDGE.search(text))

_ROUTING_PATTERNS: tuple[tuple[str, str, Pattern[str]], ...] = tuple(
    (category, reason, re.compile(pattern, re.I))
    for category, reason, pattern in (
        # Calendar/event creation. Covers "Can you add an entry to my
        # calendar?", imperatives like "add lunch to my calendar", and
        # follow-ups such as "you should be able to create that event now".
        ("calendar", "assistant calendar action request", rf"{_ACTION_QUESTION}{_CALENDAR_ACTION}\b.{{0,120}}\b{_CALENDAR_THING}\b"),
        ("calendar", "calendar follow-up action request", rf"{_ACTION_FOLLOWUP}{_CALENDAR_ACTION}\b.{{0,120}}\b{_CALENDAR_THING}\b"),
        ("calendar", "calendar imperative action request", rf"{_PLEASE}{_CALENDAR_ACTION}\b.{{0,120}}\b{_CALENDAR_THING}\b"),
        ("calendar", "calendar target action request", rf"{_PLEASE}{_CALENDAR_ACTION}\b.{{0,120}}\b(?:to|on|in|into|for)\s+(?:my\s+|the\s+|this\s+)?calendar\b"),
        ("calendar", "calendar item action request", rf"{_PLEASE}{_CALENDAR_ACTION}\s+(?:it\s+)?(?:a\s+|an\s+)?(?:calendar\s+)?(?:event|meeting|appointment|entry|item|call)\b"),
        ("calendar", "calendar target action request", rf"\b{_CALENDAR_ACTION}\b.{{0,120}}\b(?:to|on|in|into|for)\s+(?:my\s+|the\s+|this\s+)?calendar\b"),
        ("calendar", "put item on calendar request", r"\bput\s+.+\bon\s+(?:my\s+)?calendar\b"),

        # Calendar/event lookup. A question such as "Do I have Taekwondo
        # classes this week?" needs the calendar tool; plain chat cannot know.
        ("calendar", "calendar lookup request", rf"\b(?:list|show|check|find)\b.{{0,120}}\b(?:my\s+|the\s+)?(?:upcoming|next|today'?s?|tomorrow'?s?|this\s+week'?s?)\b.{{0,120}}\b{_CALENDAR_READ_THING}\b"),
        ("calendar", "calendar lookup question", rf"\b(?:what|which)\b.{{0,120}}\b(?:upcoming|next|today'?s?|tomorrow'?s?|this\s+week'?s?)\b.{{0,120}}\b{_CALENDAR_READ_THING}\b"),
        ("calendar", "calendar availability question", rf"\bdo\s+i\s+have\b.{{0,120}}\b(?:upcoming|next|today|tomorrow|this\s+week)\b.{{0,120}}\b{_CALENDAR_READ_THING}\b"),
        ("calendar", "calendar agenda question", r"\bwhat(?:'s| is)\s+on\s+(?:my\s+)?calendar\b"),
        ("calendar", "next calendar item question", r"\bwhen\s+(?:is|are)\s+(?:my\s+)?next\s+(?:event|meeting|appointment|class)\b"),

        # Notes, todos, checklists, and reminders.
        ("notes", "reminder request", r"\bremind\s+me\b"),
        ("notes", "assistant note/todo action request", rf"{_ACTION_QUESTION}(?:add|create|make|take|jot|write\s+down|set)\b.{{0,120}}\b(?:note|todo|task|checklist|reminder)\b"),
        ("notes", "note/todo imperative request", rf"{_PLEASE}(?:add|create|make)\s+(?:a\s+|an\s+)?(?:todo|task|reminder|note|checklist)\b"),
        ("notes", "take note request", rf"{_PLEASE}(?:take|jot|write\s+down)\s+(?:a\s+|an\s+)?note\b"),
        ("notes", "add item to notes/todo request", rf"{_PLEASE}(?:add|jot|write\s+down)\b.{{0,120}}\b(?:to|in|into)\s+(?:my\s+|the\s+)?(?:todo(?:\s+list)?|task\s+list|notes?|checklist)\b"),
        ("notes", "set reminder request", rf"{_PLEASE}set\s+(?:a\s+)?reminder\b"),
        ("notes", "assistant reminder request", rf"{_ACTION_QUESTION}set\s+(?:a\s+)?reminder\b"),

        # Email actions.
        ("email", "assistant email action request", rf"{_ACTION_QUESTION}(?:send|write|reply|email|message|archive|delete|mark)\b.{{0,120}}\b(?:emails?|mail|messages?|inbox|unread|read)\b"),
        ("email", "send/write/reply email request", rf"{_PLEASE}(?:send|write|reply)\b.{{0,120}}\b(?:emails?|mail|messages?)\b"),
        ("email", "archive/delete/mark email request", rf"{_PLEASE}(?:archive|delete|mark)\b.{{0,120}}\b(?:emails?|mail|messages?|inbox)\b"),
        ("email", "email composition request", r"\b(?:send|write|reply)\s+(?:an?\s+)?(?:email|message|mail)\b"),
        ("email", "email contact request", r"\bemail\s+\w+\b"),
        ("email", "check inbox request", r"\bcheck\s+(?:my\s+)?(?:email|inbox|mail)\b"),
        ("email", "unread email request", r"\bunread\s+(?:email|mail)s?\b"),

        # UI/control-plane actions that should open panels or flip toggles.
        ("ui", "open/show panel request", rf"{_PLEASE}(?:open|show|bring\s+up)\s+(?:me\s+)?(?:my\s+|the\s+)?{_PANEL}\b"),
        ("ui", "tool or feature toggle request", r"\b(?:disable|enable|turn\s+(?:on|off))\s+(?:the\s+)?(?:shell|search|web|browser|documents?|memory|skills|images?|calendar|email|mail|research|incognito)\b"),

        # Capability inventory is live runtime state. Promote even from Chat
        # mode so the model cannot answer from its training or a stale prompt.
        ("integrations", "live integration inventory request",
         r"\b(?:what|which)\s+(?:tools?|integrations?|plugins?|capabilities)\b.{0,100}\b(?:see|visible|available|access|have|connected|installed)\b|"
         r"\b(?:do|can)\s+you\s+(?:see|access|have)\b.{0,100}\b(?:tools?|integrations?|plugins?|capabilities)\b|"
         r"\b(?:list|show|tell\s+me)\b.{0,80}\b(?:tools?|integrations?|plugins?|capabilities)\b"),
        ("integrations", "live integration health request",
         r"\b(?:check|show|report|summari[sz]e|what(?:'s| is))\b.{0,100}\b(?:integration|plugin|mcp|portal)s?\b.{0,100}\b(?:health|status|working|connected|available)\b|"
         r"\b(?:health|status)\b.{0,100}\b(?:all\s+|my\s+|configured\s+)?(?:integration|plugin|mcp|portal)s?\b"),

        # This installation's release/version/update state is local truth.
        # Must stay ahead of the web/research patterns below so release
        # questions answer from the installed release channel and curated
        # notes instead of public search results or stale forks.
        ("release", "release/version self-knowledge request", _RELEASE_SELF_KNOWLEDGE.pattern),

        # Deep research jobs, not quick conceptual mentions of research.
        ("web", "explicit web search request", rf"{_PLEASE}(?:do|run|use|perform|make)\s+(?:a\s+)?(?:web\s+search|search\s+the\s+web)\b.+"),
        ("web", "generic search request", rf"{_PLEASE}search\s+(?!(?:my\s+)?(?:chats?|history|sessions?|notes?|todos?|emails?|mail|inbox|documents?|docs|gallery|images?|files?)\b).+"),
        ("web", "web lookup imperative request", rf"{_PLEASE}(?:web\s+search|search\s+the\s+web|search\s+online|look\s+up|google(?:\s+it)?)\b.*"),
        ("web", "short web lookup follow-up", rf"{_PLEASE}(?:just\s+)?(?:look\s+it\s+up|look\s+up|search\s+(?:online|web|now)|search\s+it)\b\s*$"),
        ("web", "assistant short web lookup request", rf"{_ACTION_QUESTION}(?:search|look\s+up|google)(?:\s+(?:online|web|now|it))?\b.*"),
        ("web", "assistant web lookup request", rf"{_ACTION_QUESTION}(?:web\s+search|search\s+the\s+web|search\s+online|look\s+up|google(?:\s+it)?)\b.*"),
        ("web", "assistant weather check request", rf"{_ACTION_QUESTION}(?:check|find|get|look\s+up)\b.{{0,100}}\b(?:weather|forecast)\b.*"),
        ("web", "news lookup request", r"\b(?:news|headlines)\s+(?:in|from|about|for)\s+[\w\s.-]{2,80}\??\s*$"),
        ("web", "forecast lookup request", r"\b(?:hourly|daily|weekly|local)\s+(?:weather\s+)?forecast\b|\b(?:weather\s+)?forecast\s+(?:for|today|tomorrow|now|hourly)\b"),
        ("web", "weather lookup request", r"\bweather\b.{0,80}\b(?:hourly|rain|raining|rin|today|tomorrow|update|current|now)\b|\b(?:hourly|rain|raining|rin)\b.{0,80}\bweather\b"),
        ("web", "rain lookup request", r"\b(?:hourly|daily|weekly|local|today|tomorrow|current|now|update)\b.{0,100}\b(?:rain|raining|rainy|precipitation|showers?)\b|\b(?:rain|raining|rainy|precipitation|showers?)\b.{0,100}\b(?:hourly|daily|weekly|local|today|tomorrow|current|now|update|in|for|at)\b"),
        ("web", "bare weather lookup request", r"\b(?:weather|forecast)\s+(?:in|for|at)?\s*[\w\s.-]{2,80}\??\s*$|\b[\w\s.-]{2,80}\s+(?:weather|forecast)\??\s*$"),
        ("web", "latest info lookup request", r"\b(?:latest|current|newest|recent|up(?: |-)?to(?: |-)?date)\s+(?:info|information|updates?|details?|developments?)\s+(?:on|about|for|in)\s+[\w\s.,:'\"/-]{2,120}\??\s*$"),
        ("web", "current/latest lookup request", r"\b(?:current|latest|today'?s?|right\s+now|live|online)\b.{0,120}\b(?:rate|price|news|weather|forecast|score|exchange|market|status)\b"),
        ("web", "rate/price/news lookup request", r"\b(?:rate|rates|price|prices|news|weather|forecast|score|exchange|currency|market)\b.{0,120}\b(?:now|today|current|latest|online|live|search|look\s+up|find)\b"),
        ("web", "conversion-rate lookup request", r"\b(?:convert|conversion|exchange)\b.{0,120}\b(?:rate|rates|currency|currencies|price|prices)\b"),
        ("research", "deep research imperative request", rf"{_PLEASE}(?:research|deep\s+dive|look\s+into|investigate)\s+.+"),
        ("research", "assistant deep research request", rf"{_ACTION_QUESTION}(?:research|do\s+research|deep\s+dive|look\s+into|investigate)\s+.+"),

        # Shell / remote-host intent.
        ("shell", "ssh request", r"\bssh\s+(?:in)?to\b"),
        ("shell", "ssh target request", r"\bssh\s+\w+"),
        ("shell", "remote command request", r"\b(run|execute)\s+.{1,40}\bon\s+\w+"),
        ("shell", "assistant command execution request", r"\b(can|could|please|would)\s+you\s+(run|execute|exec)\b"),
        (
            "shell",
            "explicit command execution request",
            rf"{_PLEASE}(?:(?:execute|exec)\b\s+\S+|"
            r"run\b(?!\s+(?:this|it)\b.{0,40}\b(?:in\s+the\s+)?background\b)\s+\S+)",
        ),
        # Shell verbs only count in imperative position (start of message,
        # optionally after "please") or as a "can you ..." request. A bare
        # word match promoted informational questions ("What does the grep
        # command do?") and incidental uses ("My cat ate my homework").
        ("shell", "imperative shell command request", rf"{_PLEASE}(deploy|build|install|restart|reboot|kill|tail|grep|cat|ls|cd|cp|mv|rm)\b\s+\S+"),
        ("shell", "assistant shell command request", rf"{_ACTION_QUESTION}(deploy|build|install|restart|reboot|kill|tail|grep|cat|ls|cd|cp|mv|rm)\b\s+\S+"),
        ("shell", "system/file check request", r"\b(check|see)\s+(if|whether|what)\s+.{1,40}\b(running|process|service|port|file|exists?)\b"),
    )
)

_TOOL_INTENT_PATTERNS: tuple[Pattern[str], ...] = tuple(
    pattern for _, _, pattern in _ROUTING_PATTERNS
)


def classify_tool_intent(text: str) -> ToolIntent:
    """Classify deterministic tool need and policy domain for a message."""
    if not text:
        return ToolIntent(False, reason="empty message")
    if _EXPLANATORY_PREFIX.search(text):
        return ToolIntent(False, reason="explanatory feature question")
    for category, reason, pattern in _ROUTING_PATTERNS:
        if pattern.search(text):
            return ToolIntent(True, category=category, reason=reason)
    return ToolIntent(False, reason="no tool-action pattern matched")


def message_needs_tools(text: str, patterns: Iterable[Pattern[str]] = _TOOL_INTENT_PATTERNS) -> bool:
    """Return True when a message has a deterministic tool-domain match."""
    if not text:
        return False
    if _EXPLANATORY_PREFIX.search(text):
        return False
    if patterns is _TOOL_INTENT_PATTERNS:
        return classify_tool_intent(text).needs_tools
    return any(pattern.search(text) for pattern in patterns)
