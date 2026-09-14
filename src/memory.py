
import json
import logging
import os
import time
import uuid
import re
from typing import List, Dict, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

MEMORY_STATUSES = {"candidate", "approved", "rejected", "superseded", "deleted"}

# Function words removed before relevance scoring so shared stop words ("the",
# "is", "what") cannot make an unrelated memory look relevant.
_STOPWORDS = frozenset({
    "a", "about", "an", "and", "are", "am", "as", "at", "be", "been", "but",
    "by", "can", "could", "did", "do", "does", "for", "from", "had", "has",
    "have", "he", "her", "hers", "him", "his", "how", "i", "if", "in", "is",
    "it", "its", "me", "my", "myself", "of", "on", "or", "our", "ours", "she",
    "should", "that", "the", "their", "theirs", "them", "then", "there",
    "these", "they", "this", "those", "to", "was", "we", "were", "what",
    "when", "where", "which", "who", "whom", "why", "will", "with", "would",
    "you", "your", "yours", "tell", "give", "show", "please", "know",
})


def _content_tokens(text: str) -> set:
    """Tokens that carry topical meaning (stop words removed)."""
    return {
        token
        for token in tokenize(str(text or "").lower())
        if token and token not in _STOPWORDS
    }


def _default_confidence(source: str) -> float:
    """Policy confidence for a write, based on how the fact was admitted.

    Confidence is descriptive provenance, not authority: it never promotes a
    fact by itself. Operator/user statements are exact; extracted/imported
    statements are provisional.
    """
    if source in {"user", "operator", "correction"}:
        return 1.0
    if source in {"auto", "ai_agent", "jarvis"}:
        return 0.6
    if source == "migration":
        return 0.5
    if source == "memory_audit":
        return 0.5
    return 0.5


def _clamp_confidence(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _default_admitted_by(source: str) -> str:
    """Return the existing policy/operator boundary for a memory source."""
    if source == "auto":
        return "policy:auto_memory"
    if source in {"ai_agent", "jarvis"}:
        return "jarvis.tool"
    if source == "legacy":
        return "legacy"
    return "operator"

def tokenize(text: str) -> List[str]:
    """Simple tokenizer that splits on whitespace and removes punctuation."""
    return [word.strip('.,!?";') for word in text.split()]

def get_text_similarity(text1: str, text2: str) -> float:
    """Calculate Jaccard similarity between two texts."""
    if not text1 or not text2:
        return 0.0
    
    tokens1 = set(tokenize(text1.lower()))
    tokens2 = set(tokenize(text2.lower()))
    
    if not tokens1 and not tokens2:
        return 1.0
    if not tokens1 or not tokens2:
        return 0.0
        
    intersection = tokens1.intersection(tokens2)
    union = tokens1.union(tokens2)
    
    return len(intersection) / len(union)

class MemoryManager:
    def __init__(self, data_dir: str):
        self.memory_file = os.path.join(data_dir, "memory.json")
        self.ensure_file_exists()
        
    def extract_memory_from_chat(self, chat_history: List[Dict], session_id: str = None) -> List[Dict]:
        """
        Extract memory entries from chat history as a fallback when LLM fails.
        
        Args:
            chat_history: List of chat messages with 'role' and 'content' keys
            session_id: Optional session ID to associate with extracted memories
            
        Returns:
            List of memory entries with text, timestamp, and optional session_id
        """
        memories = []
        
        for msg in chat_history:
            if not isinstance(msg, dict):
                continue
            if msg.get("role") == "assistant":
                content = str(msg.get("content", ""))
                lines = content.split('\n')
                
                for line in lines:
                    line = line.strip()
                    # Look for bullet points or numbered lists that might contain memories
                    if re.match(r'^[-*•]|\d+\.', line):
                        # Extract the text after the bullet/number. Group both
                        # markers so the capture applies to either — the previous
                        # `^[-*•]|\d+\.\s*(.*)` put the group on the numbered branch
                        # only, so a bullet line matched with group(1)=None and
                        # crashed on .strip().
                        text_match = re.match(r'^(?:[-*•]|\d+\.)\s*(.*)', line)
                        if text_match:
                            text = text_match.group(1).strip()
                            if text:
                                memories.append({
                                    "text": text,
                                    "timestamp": int(datetime.now().timestamp()),
                                    "session_id": session_id
                                })
                    # If we see a heading that suggests memories
                    elif re.search(r'memory|fact|note|remember', line, re.I):
                        pass
                    # If we see a clear separator or end
                    elif re.match(r'^={3,}|-{3,}|_{3,}', line):
                        pass
                        
        return memories
        
    def process_inline_memory_command(self, message: str) -> Tuple[bool, str]:
        """
        Check if a message is an inline memory command (e.g. "remember: X").
        
        Args:
            message: The user message to check
            
        Returns:
            Tuple of (is_command, extracted_text) where is_command is True if 
            the message matches the memory command pattern
        """
        # Pattern for memory commands: "remember: X", "memorize: X", "save: X", etc.
        pattern = r'^(?:remember|memorize|save|note|store)[:\-]?\s+(.+)$'
        match = re.match(pattern, message.strip(), re.IGNORECASE)
        
        if match:
            return True, match.group(1).strip()
        else:
            return False, ""
    
    def ensure_file_exists(self):
        """Create memory file if it doesn't exist."""
        if not os.path.exists(self.memory_file):
            with open(self.memory_file, 'w', encoding='utf-8') as f:
                json.dump([], f, ensure_ascii=False, indent=2)
    
    def load_all(self) -> List[Dict]:
        """Load all memory entries from JSON file (unfiltered)."""
        if not os.path.exists(self.memory_file):
            return []

        try:
            with open(self.memory_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return self._validate_entries(data)
        except (json.JSONDecodeError, PermissionError) as e:
            logger.error("Error loading memory.json: %s", e)
            return self._migrate_from_legacy()

        return []

    def load(self, owner: str = None, statuses: Optional[Tuple[str, ...]] = ("approved",)) -> List[Dict]:
        """Load recallable memories, optionally filtered by owner and status.

        ``load_all`` is the review/audit surface. Normal consumers use this
        method and therefore cannot recall candidates, rejected records,
        superseded records, or deletion tombstones.
        """
        entries = self.load_all()
        if statuses is not None:
            allowed = set(statuses)
            entries = [e for e in entries if e.get("status") in allowed]
        if owner is not None:
            entries = [e for e in entries if e.get("owner") == owner]
        return entries

    def claim_ownerless(self, owner: str):
        """Assign all ownerless memory entries to the given owner."""
        entries = self.load_all()
        changed = False
        claimed = 0
        for entry in entries:
            if not entry.get("owner"):
                entry["owner"] = owner
                entry["owner_id"] = owner
                changed = True
                claimed += 1
        if changed:
            self.save(entries)
            logger.info("Claimed %d ownerless memories for %s", claimed, owner)
    
    def _validate_entries(self, entries: List[Dict]) -> List[Dict]:
        """Normalize legacy records into the JOS P3 provenance envelope."""
        validated = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if "id" not in entry:
                entry["id"] = str(uuid.uuid4())
            entry["memory_id"] = entry["id"]
            if "timestamp" not in entry:
                entry["timestamp"] = int(time.time())
            if "source" not in entry:
                entry["source"] = "unknown"
            if "category" not in entry:
                entry["category"] = "fact"
            if "uses" not in entry:
                entry["uses"] = 0
            status = entry.get("status", "approved")
            entry["status"] = status if status in MEMORY_STATUSES else "candidate"
            entry.setdefault("source_ref", self._default_source_ref(entry))
            entry.setdefault("source_time", entry["timestamp"])
            entry.setdefault("admitted_at", entry["timestamp"])
            entry.setdefault("admitted_by", "legacy")
            entry.setdefault("supersedes", None)
            entry.setdefault("confidence", _default_confidence(str(entry.get("source") or "unknown")))
            entry["owner_id"] = entry.get("owner")
            validated.append(entry)
        return validated

    @staticmethod
    def _default_source_ref(entry: Dict) -> str:
        session_id = entry.get("session_id")
        if session_id:
            return f"session:{session_id}"
        return f"{entry.get('source', 'unknown')}:{entry.get('id', 'unknown')}"
    
    def _migrate_from_legacy(self) -> List[Dict]:
        """Migrate from old text format to JSON if needed."""
        legacy_path = os.path.join(os.path.dirname(self.memory_file), "memory.txt")
        if not os.path.exists(legacy_path):
            return []
            
        logger.info("Converting legacy memory.txt to new JSON format")
        try:
            with open(legacy_path, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f.readlines() if ln.strip()]
            
            entries = []
            for line in lines:
                entries.append({
                    "id": str(uuid.uuid4()),
                    "text": line,
                    "timestamp": int(time.time()),
                    "source": "user",
                    "category": "fact"
                })
            
            self.save(entries)
            return entries
        except Exception as e:
            logger.error("Failed to convert legacy memory: %s", e)
            return []
    
    def save(self, entries: List[Dict]):
        """Save memory entries to JSON file."""
        entries = self._validate_entries(entries)
        
        # Use atomic write
        tmp_file = self.memory_file + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, self.memory_file)
    
    def add_entry(
        self,
        text: str,
        source: str = "user",
        category: str = "fact",
        owner: str = None,
        *,
        status: str = "approved",
        source_ref: Optional[str] = None,
        source_time: Optional[int] = None,
        admitted_by: Optional[str] = None,
        supersedes: Optional[str] = None,
        confidence: Optional[float] = None,
    ) -> Dict:
        """Create a provenance-complete memory record without saving it."""
        if not text.strip():
            raise ValueError("Memory text cannot be empty")
        if status not in MEMORY_STATUSES:
            raise ValueError(f"Invalid memory status: {status}")

        now = int(time.time())
        memory_id = str(uuid.uuid4())
        entry = {
            "id": memory_id,
            "memory_id": memory_id,
            "text": text.strip(),
            "timestamp": now,
            "source": source,
            "category": category,
            "uses": 0,
            "status": status,
            "source_ref": source_ref or f"{source}:{memory_id}",
            "source_time": source_time or now,
            "admitted_at": now,
            "admitted_by": admitted_by or _default_admitted_by(source),
            "supersedes": supersedes,
            "confidence": (
                _clamp_confidence(confidence)
                if confidence is not None
                else _default_confidence(source)
            ),
            "owner_id": owner,
        }
        if owner:
            entry["owner"] = owner
        return entry

    def replace_entry(
        self,
        memory_id: str,
        text: str,
        *,
        owner: Optional[str] = None,
        category: Optional[str] = None,
        admitted_by: str = "operator",
    ) -> Optional[Dict]:
        """Supersede an approved memory with a new immutable record."""
        entries = self.load_all()
        original = next((entry for entry in entries if entry.get("id") == memory_id), None)
        if original is None or original.get("status") != "approved":
            return None
        if owner is not None and original.get("owner") != owner:
            return None

        replacement = self.add_entry(
            text,
            source="correction",
            category=category or original.get("category", "fact"),
            owner=original.get("owner"),
            source_ref=f"memory:{memory_id}",
            source_time=int(time.time()),
            admitted_by=admitted_by,
            supersedes=memory_id,
        )
        for inherited in ("session_id", "pinned", "metadata"):
            if inherited in original:
                replacement[inherited] = original[inherited]
        original["status"] = "superseded"
        original["superseded_by"] = replacement["id"]
        original["superseded_at"] = replacement["admitted_at"]
        entries.append(replacement)
        self.save(entries)
        return replacement

    def delete_entry(
        self,
        memory_id: str,
        *,
        owner: Optional[str] = None,
        deleted_by: str = "operator",
    ) -> bool:
        """Tombstone a memory so provenance remains auditable but recall stops."""
        entries = self.load_all()
        target = next((entry for entry in entries if entry.get("id") == memory_id), None)
        if target is None or target.get("status") == "deleted":
            return False
        if owner is not None and target.get("owner") != owner:
            return False
        target["status"] = "deleted"
        target["deleted_at"] = int(time.time())
        target["deleted_by"] = deleted_by
        self.save(entries)
        return True

    def increment_uses(self, ids: List[str]) -> None:
        """Bump the uses counter for each memory id. Called after a memory has
        actually been injected into a chat's context (not just retrieved)."""
        if not ids:
            return
        id_set = set(ids)
        entries = self.load_all()
        changed = False
        for e in entries:
            if e.get("id") in id_set:
                e["uses"] = int(e.get("uses", 0) or 0) + 1
                changed = True
        if changed:
            self.save(entries)
    
    def find_duplicates(self, text: str, entries: List[Dict] = None) -> List[Dict]:
        """Find duplicate memory entries based on text content."""
        if entries is None:
            entries = self.load()
            
        text_lower = text.strip().lower()
        return [
            entry for entry in entries
            if entry.get("status", "approved") == "approved"
            and entry["text"].lower() == text_lower
        ]
            
    def categorize_memory_by_relevance(self, message: str, memories: list):
        """Categorize memories by type and relevance"""
        categories = {
            "contacts": [],
            "preferences": [],
            "facts": [],
            "tasks": []
        }
        
        msg_lower = message.lower()
        
        for mem in memories:
            text_lower = mem["text"].lower()
            
            # Contact info
            if any(word in text_lower for word in ["phone", "email", "address", "lives", "works"]):
                if any(word in msg_lower for word in ["contact", "phone", "address", "email"]):
                    categories["contacts"].append(mem)
            
            # Personal preferences
            elif any(word in text_lower for word in ["likes", "dislikes", "prefers", "favorite"]):
                if any(word in msg_lower for word in ["like", "prefer", "favorite", "want"]):
                    categories["preferences"].append(mem)
            
            # Tasks and todos
            elif any(word in text_lower for word in ["todo", "task", "remind", "meeting"]):
                if any(word in msg_lower for word in ["todo", "task", "schedule", "remind"]):
                    categories["tasks"].append(mem)
            
            # General facts - only if very relevant
            else:
                if get_text_similarity(message, mem["text"]) > 0.4:
                    categories["facts"].append(mem)
        
        return categories

    def get_relevant_memories(self, query: str, memories: list, threshold: float = 0.05, max_items: int = 8):
        """Rank approved memories by topical overlap with the query.

        Scoring uses whole content tokens with a prefix match ("prefer" against
        "prefers") and a stop-word filter, so shared function words ("the",
        "is", "what") can no longer make an unrelated memory look relevant.
        Identity memories are only force-included when the query is actually
        about identity, and results are deduplicated by text.
        """
        memories = [m for m in memories if m.get("status", "approved") == "approved"]
        if not memories or not query.strip():
            return []

        identity_words = {"name", "who", "i", "am", "called", "identity", "myself", "me", "my"}
        strong_identity_words = {"name", "who", "myself", "identity", "named"}
        contact_words = {"phone", "email", "address", "contact", "number", "where", "located", "reach"}
        preference_words = {"like", "prefer", "favorite", "want", "love", "hate", "dislike", "enjoy", "interested"}
        task_words = {"todo", "task", "remind", "meeting", "appointment", "schedule", "deadline"}

        query_lower = query.lower()
        query_tokens = set(tokenize(query_lower))
        query_content = _content_tokens(query_lower)

        def _has_query_word(words) -> bool:
            return bool(query_tokens & set(words))

        # Topical groups win over the broad identity group: "where do I live"
        # contains "i" but is a location lookup, and "what do I prefer" is a
        # preference lookup even though it also contains "i".
        query_type = "fact"
        if _has_query_word(preference_words):
            query_type = "preference"
        elif _has_query_word(contact_words):
            query_type = "contact"
        elif _has_query_word(task_words):
            query_type = "task"
        elif _has_query_word(identity_words):
            query_type = "identity"

        def _is_identity_memory(memory: dict) -> bool:
            text = str(memory.get("text") or "")
            return bool(
                re.search(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", text)
                or any(
                    marker in text.lower()
                    for marker in ("name is", "i'm", "i am", "called", "my name", "named", "call me")
                )
            )

        def _prefix_overlap(tokens: set, memory_content: set) -> float:
            if not tokens:
                return 0.0
            hits = 0
            for token in tokens:
                if any(
                    token == other
                    or (
                        len(token) >= 4
                        and (other.startswith(token) or token.startswith(other))
                    )
                    for other in memory_content
                ):
                    hits += 1
            return hits / len(tokens)

        identity_query = query_type == "identity"
        strong_identity = bool(query_tokens & strong_identity_words)
        scored = []
        for memory in memories:
            text = str(memory.get("text") or "")
            memory_content = _content_tokens(text)
            base_similarity = _prefix_overlap(query_content, memory_content)
            is_identity = _is_identity_memory(memory)
            if base_similarity <= 0:
                # A memory with no content overlap may only enter when the
                # query itself is an identity question whose content tokens are
                # all stop words ("who am I") and this is an identity fact.
                if not (identity_query and strong_identity and is_identity and not query_content):
                    continue
                base_similarity = 0.5
            final_score = base_similarity
            if identity_query and strong_identity and is_identity:
                final_score *= 1.5
            lowered_text = text.lower()
            if query_type == "contact" and any(
                marker in lowered_text
                for marker in ("@", ".com", "phone", "number", "address", "http", "www", "tel:")
            ):
                final_score *= 1.4
            elif query_type == "preference" and any(
                marker in lowered_text
                for marker in ("like", "love", "hate", "dislike", "prefer", "favorite", "enjoy", "interested")
            ):
                final_score *= 1.3
            elif query_type == "task" and any(
                marker in lowered_text
                for marker in ("todo", "task", "remind", "meeting", "appointment", "schedule", "deadline", "need to")
            ):
                final_score *= 1.3
            if query_lower.strip() and query_lower in lowered_text:
                final_score = max(final_score, 0.8)
            if final_score >= threshold:
                scored.append((final_score, memory))

        scored.sort(key=lambda item: item[0], reverse=True)
        seen_ids = set()
        seen_texts = set()
        results = []
        for _score, memory in scored:
            memory_id = memory.get("id")
            text_key = str(memory.get("text") or "").strip().lower()
            if memory_id in seen_ids or text_key in seen_texts:
                continue
            seen_ids.add(memory_id)
            seen_texts.add(text_key)
            results.append(memory)
            if len(results) >= max_items:
                break
        return results
