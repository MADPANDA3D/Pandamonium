"""
context_compactor.py

Auto-compacts conversation history when approaching context window limits.
Summarizes older messages via the same LLM, preserving key context.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from src.context_budget import context_class_budget_percent
from src.model_context import annotate_context_messages, get_context_length, estimate_tokens
from src.llm_core import llm_call_async
from src.endpoint_resolver import resolve_endpoint
from core.models import ChatMessage

logger = logging.getLogger(__name__)


def _content_as_text(content: Any) -> str:
    """Flatten a message's content to plain text.

    Handles the three shapes that flow through history: a plain string, a
    multimodal list of content blocks (vision/image attachments), and None
    (assistant turns that carried only native tool_calls persist content as
    None). Returns "" for anything without text so callers can safely slice
    the result.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("text")
        )
    return ""


COMPACT_THRESHOLD = 0.85  # Trigger compaction at 85% of context window
SUMMARY_MAX_TOKENS = 1024
SMALL_CONTEXT_LIMIT = 8192  # Models with context <= this get aggressive trimming

# Cursor-style self-summarization prompt — produces structured, dense summaries
SELF_SUMMARY_SYSTEM_PROMPT = """You are summarizing a conversation to preserve context after compaction. Produce a structured summary that lets the conversation continue seamlessly.

Use this format:

## Conversation Summary
**Turns summarized:** {count}  |  **Compactions so far:** {n}

### User Goal
One sentence describing what the user is trying to accomplish.

### What Was Done
- Bullet points of completed actions, decisions made, and key outputs
- Include specific file paths, function names, variable names, URLs, and config values
- Note any errors encountered and how they were resolved

### Current State
What is the system/code/task state right now? What was the last thing discussed?

### Pending / Next Steps
- What remains to be done
- Any open questions or blockers

### Key Context
- Important constraints, preferences, or decisions that must not be forgotten
- Specific values: model names, ports, paths, credentials references, versions

Keep the summary under 1000 tokens. Be dense — every token should carry information. Do not include pleasantries or meta-commentary."""


def _sanitize_tool_messages(msgs: List[Dict]) -> List[Dict]:
    """Drop orphaned `tool` messages and dangling assistant `tool_calls`.

    OpenAI's API requires every `role:"tool"` message to immediately
    follow an assistant message that carries `tool_calls` (or another
    tool message in the same batch). Front-trimming the history can cut
    the assistant `tool_calls` parent while keeping its tool responses,
    which triggers: "messages with role 'tool' must be a response to a
    preceding message with 'tool_calls'". This pass repairs that:
      - drops `tool` messages with no valid preceding tool_calls
      - drops assistant `tool_calls` messages whose tool responses were
        all trimmed away (some providers reject unanswered tool_calls)
    """
    # Pass 1: drop orphan tool messages.
    cleaned: List[Dict] = []
    in_batch = False  # are we right after an assistant tool_calls (or mid-batch)?
    for m in msgs:
        role = m.get("role")
        if role == "tool":
            if in_batch:
                cleaned.append(m)
            # else: orphan — drop
            continue
        if role == "assistant" and m.get("tool_calls"):
            in_batch = True
        else:
            in_batch = False
        cleaned.append(m)

    # Pass 2: drop assistant tool_calls messages that have NO following
    # tool response (dangling) — walk backwards so we know what follows.
    out: List[Dict] = []
    for i, m in enumerate(cleaned):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            nxt = cleaned[i + 1] if i + 1 < len(cleaned) else None
            if not (nxt and nxt.get("role") == "tool"):
                # Dangling tool_calls — keep the message but strip the
                # tool_calls so it's a plain assistant turn (preserves any
                # text content the model produced alongside the calls).
                m = {k: v for k, v in m.items() if k != "tool_calls"}
                if not (m.get("content") or "").strip():
                    continue  # nothing left worth keeping
        out.append(m)
    return out


def _message_text_token_estimate(text: str) -> int:
    if not isinstance(text, str):
        return 4
    return int(len(text) * 0.3) + 4


def _truncate_text_to_token_budget(text: str, token_budget: int) -> str:
    """Trim a too-large current user message instead of dropping it entirely."""
    if token_budget <= 32:
        return "[Current user message omitted: it exceeded the model context window.]"

    if not isinstance(text, str):
        # This helper is typed/used as text downstream, so return an empty
        # string rather than the raw non-string (which would move the crash
        # into the caller that concatenates/measures the result).
        return ""
    # Match src.model_context.estimate_tokens' rough chars * 0.3 estimate.
    max_chars = max(200, int((token_budget - 16) / 0.3))
    if len(text) <= max_chars:
        return text

    notice = (
        "\n\n[Notice: the pasted message was too large for this model's context "
        "window, so Odysseus kept the beginning and end.]"
    )
    keep_chars = max(200, max_chars - len(notice))
    head_len = max(100, int(keep_chars * 0.7))
    tail_len = max(80, keep_chars - head_len)
    return text[:head_len].rstrip() + notice + "\n\n" + text[-tail_len:].lstrip()


def _truncate_tool_call_args(msg: Dict[str, Any], token_budget: int) -> Dict[str, Any]:
    """Shrink oversized assistant ``tool_calls`` arguments to fit ``token_budget``.

    A tool-only turn persists ``content=None`` with its whole payload in
    ``tool_calls[].function.arguments`` (e.g. a large create_document body), which
    the text-content truncation can't reach — so the message could stay over
    budget and the upstream call would 400. Replace each argument string that
    overflows its share of the budget with a small valid-JSON placeholder,
    preserving ``id``/``type``/``function.name`` so tool/result pairing and
    provider validation are unaffected. Returns msg unchanged when there is
    nothing oversized.
    """
    tool_calls = msg.get("tool_calls")
    if not isinstance(tool_calls, list) or not tool_calls:
        return msg
    # Budget left after whatever content survived (estimate_tokens counts tool
    # arguments too, so measure content alone here).
    content_tokens = estimate_tokens([{"role": msg.get("role", "assistant"), "content": msg.get("content")}])
    per_call = max(16, (max(0, token_budget - content_tokens)) // len(tool_calls))
    new_calls = []
    changed = False
    for tc in tool_calls:
        fn = tc.get("function") if isinstance(tc, dict) else None
        args = fn.get("arguments") if isinstance(fn, dict) else None
        if isinstance(args, str) and int(len(args) * 0.3) > per_call:
            new_fn = dict(fn)
            new_fn["arguments"] = json.dumps({"_truncated_for_context": len(args)})
            new_tc = dict(tc)
            new_tc["function"] = new_fn
            new_calls.append(new_tc)
            changed = True
        else:
            new_calls.append(tc)
    if not changed:
        return msg
    out = dict(msg)
    out["tool_calls"] = new_calls
    return out


def _truncate_message_to_token_budget(msg: Dict[str, Any], token_budget: int) -> Dict[str, Any]:
    """Return a copy of msg whose text content (and tool-call args) fit token_budget."""
    out = dict(msg)
    content = out.get("content", "")
    if isinstance(content, str):
        out["content"] = _truncate_text_to_token_budget(content, token_budget)
    elif isinstance(content, list):
        remaining = token_budget
        new_content = []
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text":
                new_content.append(item)
                continue
            text = item.get("text", "")
            truncated = _truncate_text_to_token_budget(text, remaining)
            cloned = dict(item)
            cloned["text"] = truncated
            new_content.append(cloned)
            remaining -= _message_text_token_estimate(truncated)
        out["content"] = new_content
    # A tool-only turn (content=None) carries its payload in tool_calls args,
    # which the branches above can't shrink — handle it so the message can fit.
    return _truncate_tool_call_args(out, token_budget)


_DROP_PRIORITY = {
    "derived_knowledge": 0,
    "retrieved_knowledge": 10,
    "recalled_memory": 20,
    "tool_catalog": 30,
    "conversation": 40,
    "time": 50,
    "working_state": 60,
    "extension_state": 70,
    "tool_result": 80,
}


def _message_context_class(message: Dict[str, Any]) -> str:
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    tag = metadata.get("jos_context") if isinstance(metadata.get("jos_context"), dict) else {}
    return str(tag.get("class") or "unknown")


def _truncate_context_message(
    message: Dict[str, Any], token_budget: int, *, source_notice: bool = True,
) -> Dict[str, Any]:
    out = _truncate_message_to_token_budget(message, token_budget)
    content = out.get("content")
    if source_notice and isinstance(content, str):
        content = content.replace(
            "[Current user message omitted: it exceeded the model context window.]",
            "[Context source omitted: it exceeded its attention budget.]",
        ).replace(
            "[Notice: the pasted message was too large for this model's context window, "
            "so Odysseus kept the beginning and end.]",
            "[Notice: this context source exceeded its attention budget, so Odysseus "
            "kept the beginning and end.]",
        )
        out["content"] = content
    metadata = dict(out.get("metadata") or {})
    metadata["context_trimmed"] = True
    metadata["original_tokens"] = estimate_tokens([message])
    out["metadata"] = metadata
    return out


def _apply_class_budgets(
    messages: List[Dict], budget: int, *, class_budget_base: Optional[int] = None,
) -> List[Dict]:
    """Keep the newest relevant data in each bounded JOS context class."""
    percentages = context_class_budget_percent()
    result = list(messages)
    keep = set(range(len(result)))
    by_class: Dict[str, List[int]] = {}
    for index, message in enumerate(result):
        context_class = _message_context_class(message)
        if context_class in percentages:
            by_class.setdefault(context_class, []).append(index)

    class_base = max(int(class_budget_base if class_budget_base is not None else budget), 0)
    for context_class, indices in by_class.items():
        remaining = max(64, int(class_base * percentages[context_class] / 100))
        for index in reversed(indices):
            tokens = estimate_tokens([result[index]])
            if tokens <= remaining:
                remaining -= tokens
            elif remaining >= 64:
                result[index] = _truncate_context_message(result[index], remaining)
                remaining = 0
            else:
                keep.discard(index)
    return [message for index, message in enumerate(result) if index in keep]


def _active_tool_pair_indices(messages: List[Dict]) -> set[int]:
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") != "assistant" or not messages[index].get("tool_calls"):
            continue
        active = {index}
        cursor = index + 1
        while cursor < len(messages) and messages[cursor].get("role") == "tool":
            active.add(cursor)
            cursor += 1
        return active
    return set()


def trim_for_context(
    messages: List[Dict],
    context_length: int,
    reserve_tokens: int = 512,
    *,
    class_budget_base: Optional[int] = None,
) -> List[Dict]:
    """Apply JOS-P2 class ceilings, then drop lowest-priority context first."""
    budget = max(int(context_length or 0) - max(int(reserve_tokens or 0), 0), 64)
    annotated = annotate_context_messages(messages)
    result = _apply_class_budgets(
        annotated,
        budget,
        class_budget_base=class_budget_base,
    )
    used = estimate_tokens(result)
    if used <= budget:
        return _sanitize_tool_messages(result)

    logger.info("Trimming messages: %s tokens > %s budget (ctx=%s)", used, budget, context_length)
    active_tool_pair = _active_tool_pair_indices(result)
    protected = set(active_tool_pair)
    first_system_index = next(
        (index for index, message in enumerate(result) if message.get("role") == "system"),
        None,
    )
    for index, message in enumerate(result):
        context_class = _message_context_class(message)
        metadata = message.get("metadata") or {}
        tag = metadata.get("jos_context") or {}
        if (
            context_class in {"policy", "operator_intent", "presentation"}
            or (
                context_class == "identity_policy"
                and (not tag.get("inferred") or index == first_system_index)
            )
            or message.get("_protected")
            or metadata.get("research_spinoff_from")
        ):
            protected.add(index)

    candidates = sorted(
        (index for index in range(len(result)) if index not in protected),
        key=lambda index: (_DROP_PRIORITY.get(_message_context_class(result[index]), 5), index),
    )
    keep = set(range(len(result)))
    for index in candidates:
        if estimate_tokens([result[item] for item in sorted(keep)]) <= budget:
            break
        keep.discard(index)
    result = [message for index, message in enumerate(result) if index in keep]
    result = _sanitize_tool_messages(result)

    # Protected means the message remains mounted, not that one huge source can
    # consume the turn. Shrink dynamic state/results first, then trusted prompt
    # tails only if the protected set itself cannot fit. Current intent is last.
    shrink_order = (
        "working_state", "retrieved_knowledge", "derived_knowledge",
        "recalled_memory", "tool_result", "extension_state", "time",
        "policy", "identity_policy", "operator_intent",
    )
    for context_class in shrink_order:
        for index in range(len(result) - 1, -1, -1):
            if estimate_tokens(result) <= budget:
                break
            if _message_context_class(result[index]) != context_class:
                continue
            current_tokens = estimate_tokens([result[index]])
            excess = estimate_tokens(result) - budget
            target = max(64, current_tokens - excess)
            result[index] = _truncate_context_message(
                result[index],
                target,
                source_notice=context_class != "operator_intent",
            )

    result = _sanitize_tool_messages(result)
    logger.info("Trimmed to %s tokens (%s messages)", estimate_tokens(result), len(result))
    return result


async def maybe_compact(
    session,
    endpoint_url: str,
    model: str,
    messages: List[Dict],
    headers: Optional[Dict] = None,
    owner: Optional[str] = None,
) -> tuple:
    """Check context usage and compact if above threshold.

    Returns (messages, context_length, was_compacted).
    """
    context_length = get_context_length(endpoint_url, model)
    used = estimate_tokens(messages)
    pct = (used / context_length) * 100 if context_length else 0

    if pct < COMPACT_THRESHOLD * 100:
        return messages, context_length, False

    logger.info(
        f"Context at {pct:.1f}% ({used}/{context_length} tokens) — compacting"
    )

    # Split into system preface and conversation
    system_msgs = []
    convo_msgs = []
    for msg in messages:
        if msg.get("role") == "system":
            system_msgs.append(msg)
        else:
            convo_msgs.append(msg)

    if len(convo_msgs) < 4:
        return messages, context_length, False

    # Split conversation: summarize older half, keep recent half
    split_point = len(convo_msgs) // 2
    older = convo_msgs[:split_point]
    recent = convo_msgs[split_point:]

    # Build the text to summarize
    convo_text = "\n".join(
        f"{msg.get('role', 'user').upper()}: {_content_as_text(msg.get('content'))[:2000]}"
        for msg in older
    )

    # Count prior compactions from existing summary messages
    compaction_count = sum(
        1 for m in system_msgs
        if "[Conversation summary" in m.get("content", "")
    )

    # Use utility model if configured, otherwise fall back to session model
    util_url, util_model, util_headers = resolve_endpoint("utility", owner=owner)
    compact_url = util_url or endpoint_url
    compact_model = util_model or model
    compact_headers = util_headers if util_url else headers

    prompt = SELF_SUMMARY_SYSTEM_PROMPT.replace(
        "{count}", str(len(older))
    ).replace(
        "{n}", str(compaction_count + 1)
    )
    summary_messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": convo_text},
    ]

    try:
        summary = await llm_call_async(
            compact_url,
            compact_model,
            summary_messages,
            temperature=0.2,
            max_tokens=SUMMARY_MAX_TOKENS,
            headers=compact_headers,
            timeout=30,
        )
    except Exception as e:
        logger.error(f"Compaction summary failed: {e}")
        # Degrade gracefully: keep the conversation intact rather than
        # silently dropping the older half. was_compacted=False signals the
        # caller nothing was summarized; trim_for_context handles length.
        return messages, context_length, False

    summary_msg = {
        "role": "system",
        "content": f"[Conversation summary — earlier messages were compacted]\n{summary}",
        "metadata": {
            "compacted": True,
            "summarized_count": len(older),
            "jos_context": {
                "class": "conversation",
                "source": "session.compaction_summary",
                "trust": "derived",
            },
        },
    }

    compacted = system_msgs + [summary_msg] + recent

    # Update session history to match. Pass len(system_msgs) so the
    # recent_history slice in _update_session_history uses the correct
    # offset — session.history INCLUDES the system messages, but
    # split_point is indexed against convo_msgs which does NOT. Without
    # this, the slice drops the leading system message(s).
    _update_session_history(session, split_point, summary, system_msg_count=len(system_msgs))

    new_used = estimate_tokens(compacted)
    logger.info(
        f"Compacted: {used} -> {new_used} tokens "
        f"({len(older)} messages summarized, {len(recent)} kept)"
    )

    return compacted, context_length, True


def _update_session_history(session, split_point: int, summary: str,
                            system_msg_count: int = 0):
    """Update the in-memory session history after compaction.

    `split_point` is the index in `convo_msgs` (system-stripped). The
    in-memory `session.history` includes leading system messages, so the
    actual recent-history slice starts at `system_msg_count + split_point`.
    Prepending `session.history[:system_msg_count]` to the new history
    preserves persona, preset, and RAG system messages that would
    otherwise be dropped.
    """
    if not session or not hasattr(session, "history"):
        return

    effective_split = system_msg_count + split_point
    if effective_split >= len(session.history):
        return

    # Keep the recent messages, prepend summary AND the leading system
    # messages so the system prompt survives compaction.
    system_prefix = list(session.history[:system_msg_count])
    recent_history = session.history[effective_split:]
    summary_msg = ChatMessage(
        role="system",
        content=f"[Conversation summary]\n{summary}",
        metadata={"compacted": True, "summarized_count": split_point},
    )
    new_history = system_prefix + [summary_msg] + recent_history
    try:
        from core.models import get_session_manager_instance
        manager = get_session_manager_instance()
    except Exception:
        manager = None
    if manager and getattr(session, "id", None):
        if manager.replace_messages(session.id, new_history):
            return
    session.history = new_history
