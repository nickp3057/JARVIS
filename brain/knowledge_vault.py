"""Small, dependency-free Obsidian/Markdown knowledge source.

The vault is deliberately searched locally.  Only a bounded set of relevant
excerpts is returned to the agent, so adding notes does not make every Claude
request grow with the size of the vault.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from jarvis_logger import get_logger

log = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_VAULT = _PROJECT_ROOT / "memory" / "obsidian"
_PROFILE_NOTE = Path("Jarvis Memory") / "Profile.md"
_CORE_NOTE = Path("Jarvis Memory") / "Core Profile.md"
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_'’-]*", re.IGNORECASE)
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "do", "for",
    "from", "had", "has", "have", "he", "her", "him", "his", "i", "in",
    "is", "it", "me", "my", "of", "on", "or", "our", "she", "that", "the",
    "their", "them", "they", "this", "to", "was", "we", "were", "what",
    "when", "where", "which", "who", "will", "with", "you", "your",
}


def vault_path() -> Path:
    """Return the live-configured vault root."""
    configured = os.getenv("OBSIDIAN_VAULT_PATH", "").strip()
    return Path(configured).expanduser() if configured else _DEFAULT_VAULT


def _positive_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _authorized_roots(root: Path) -> list[Path]:
    configured = os.getenv("OBSIDIAN_INDEX_FOLDERS", "Jarvis Memory")
    folders = [item.strip() for item in configured.split(",") if item.strip()]
    if not folders:
        return []

    resolved_root = root.resolve()
    authorized: list[Path] = []
    for folder in folders:
        candidate = (root / folder).resolve()
        try:
            candidate.relative_to(resolved_root)
        except ValueError:
            log.warning("Ignoring Obsidian folder outside vault: %s", folder)
            continue
        authorized.append(candidate)
    return authorized


def _tokens(text: str) -> list[str]:
    return [
        token.lower().replace("’", "'")
        for token in _WORD_RE.findall(text)
        if len(token) > 1 and token.lower() not in _STOP_WORDS
    ]


def _markdown_files() -> Iterable[tuple[Path, Path]]:
    root = vault_path()
    if not root.exists():
        return
    resolved_root = root.resolve()
    max_bytes = _positive_int("OBSIDIAN_MAX_FILE_BYTES", 1_000_000, 1_024, 10_000_000)
    seen: set[Path] = set()
    for selected in _authorized_roots(root):
        if not selected.exists() or not selected.is_dir():
            continue
        for path in selected.rglob("*.md"):
            try:
                resolved = path.resolve()
                resolved.relative_to(resolved_root)
                if resolved in seen or not path.is_file() or path.stat().st_size > max_bytes:
                    continue
            except (OSError, ValueError):
                continue
            seen.add(resolved)
            yield path, resolved.relative_to(resolved_root)


def _best_excerpt(text: str, query_tokens: set[str], limit: int) -> tuple[str, int]:
    lines = text.splitlines()
    best_index = 0
    best_score = 0
    current_heading = ""
    headings: dict[int, str] = {}
    for index, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            current_heading = line.strip()
        headings[index] = current_heading
        score = sum(line.lower().count(token) for token in query_tokens)
        if score > best_score:
            best_score = score
            best_index = index
    if best_score <= 0:
        return "", 0

    start = max(0, best_index - 2)
    end = min(len(lines), best_index + 5)
    excerpt_lines = lines[start:end]
    heading = headings.get(best_index, "")
    if heading and heading not in excerpt_lines:
        excerpt_lines.insert(0, heading)
    excerpt = "\n".join(line for line in excerpt_lines if line.strip()).strip()
    return excerpt[:limit], best_score


_SECTIONS_PER_NOTE = 3
_HEADING_LINE_RE = re.compile(r"^#{1,6} ", re.MULTILINE)


def _sections(text: str) -> list[str]:
    """The note split at its headings (each section keeps its heading)."""
    starts = [m.start() for m in _HEADING_LINE_RE.finditer(text)]
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    return [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)]) if text[a:b].strip()]


def search_vault(query: str, *, max_results: int | None = None, max_chars: int | None = None) -> list[dict[str, Any]]:
    """Search authorized Markdown folders and return bounded excerpts."""
    query_tokens = set(_tokens(query))
    if not query_tokens:
        return []
    result_limit = max_results or _positive_int("OBSIDIAN_MAX_RESULTS", 4, 1, 20)
    char_limit = max_chars or _positive_int("OBSIDIAN_MAX_CHARS", 4000, 200, 20_000)
    per_excerpt = max(200, char_limit // result_limit)
    results: list[dict[str, Any]] = []
    for path, relative in _markdown_files():
        if relative in (_PROFILE_NOTE, _CORE_NOTE):
            continue  # Profile facts and the core profile are injected every turn already.
        try:
            text = path.read_text(encoding="utf-8")
            title_tokens = set(_tokens(relative.stem))
            title_score = len(query_tokens & title_tokens) * 4
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
            # Each heading's section competes on its own, so one long note (a
            # big "everything about me" import) can answer with the right
            # part, and up to three parts, instead of one excerpt per file.
            hits = []
            for section in _sections(text):
                excerpt, body_score = _best_excerpt(section, query_tokens, per_excerpt)
                if body_score > 0 and excerpt:
                    hits.append((body_score + title_score, excerpt))
            hits.sort(key=lambda hit: -hit[0])
            for score, excerpt in hits[:_SECTIONS_PER_NOTE]:
                results.append({"source": relative.as_posix(), "excerpt": excerpt,
                                "score": score, "modified_at": modified})
        except (OSError, UnicodeError) as exc:
            log.warning("Could not index Obsidian note %s: %s", path, exc)
    results.sort(key=lambda item: (-item["score"], item["source"].lower()))

    selected: list[dict[str, Any]] = []
    used = 0
    for result in results[:result_limit]:
        remaining = char_limit - used
        if remaining <= 0:
            break
        item = dict(result)
        item["excerpt"] = item["excerpt"][:remaining]
        used += len(item["excerpt"])
        selected.append(item)
    return selected


def render_vault_context(query: str) -> str:
    results = search_vault(query)
    if not results:
        return ""
    lines = ["## Relevant notes"]
    for result in results:
        lines.append(f"### Source: {result['source']}")
        lines.append(result["excerpt"])
    return "\n\n".join(lines)


def sync_profile_note(facts: list[dict[str, Any]]) -> None:
    """Atomically mirror structured profile facts into one Obsidian note."""
    target = vault_path() / _PROFILE_NOTE
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        "jarvis_managed: true",
        f"updated: {datetime.now(timezone.utc).isoformat()}",
        "---",
        "",
        "# JARVIS Profile Memory",
        "",
        "> Managed by JARVIS. Edit memories through JARVIS so IDs stay synchronized.",
        "",
    ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        grouped.setdefault(str(fact.get("category") or "general"), []).append(fact)
    for category in sorted(grouped, key=str.lower):
        lines.extend((f"## {category}", ""))
        for fact in grouped[category]:
            content = str(fact.get("content") or "").replace("\n", " ").strip()
            lines.append(f"- {content} <!-- jarvis:{fact.get('id', '')} -->")
            metadata = []
            if fact.get("key"):
                metadata.append(f"key={fact['key']}")
            if "confidence" in fact:
                metadata.append(f"confidence={fact['confidence']}")
            if fact.get("source"):
                metadata.append(f"source={fact['source']}")
            if fact.get("updated_at"):
                metadata.append(f"updated={fact['updated_at']}")
            if metadata:
                lines.append(f"  - Metadata: {', '.join(metadata)}")
        lines.append("")
    temporary = target.with_suffix(".md.tmp")
    temporary.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    temporary.replace(target)


def ensure_profile_note(facts: list[dict[str, Any]]) -> None:
    """Create the managed mirror for pre-existing JSON memory on first use."""
    if not (vault_path() / _PROFILE_NOTE).exists():
        sync_profile_note(facts)


def describe_search(query: str) -> str:
    results = search_vault(query)
    if not results:
        return "No relevant authorized Obsidian notes found."
    return "\n\n".join(
        f"Source: {item['source']}\n{item['excerpt']}" for item in results
    )


# --- Reading: the companion's vault browser (GET /api/knowledge) -------------

_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n+", re.DOTALL)
_COMMENT_RE = re.compile(r"\s*<!--.*?-->", re.DOTALL)
_METADATA_LINE_RE = re.compile(r"^\s+- Metadata: .*$\n?", re.MULTILINE)


def list_notes() -> list[dict[str, Any]]:
    """Every note JARVIS may read (the authorized folders): the profile note
    first, then newest first. ``path`` is vault-relative, for ``read_note``."""
    notes = []
    for path, relative in _markdown_files():
        try:
            modified = path.stat().st_mtime
        except OSError:
            continue
        notes.append({"path": relative.as_posix(), "title": path.stem,
                      "folder": relative.parent.as_posix(), "modified": modified})
    profile = _PROFILE_NOTE.as_posix()
    notes.sort(key=lambda n: (n["path"] != profile, -n["modified"]))
    return notes


def read_note(relative: str) -> dict[str, Any] | None:
    """One authorized note, cleaned for reading: no frontmatter, no JARVIS's
    hidden fact IDs or metadata lines. None if it isn't an authorized note."""
    for path, rel in _markdown_files():
        if rel.as_posix() != relative:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        text = _METADATA_LINE_RE.sub("", _COMMENT_RE.sub("", _FRONTMATTER_RE.sub("", text)))
        return {"path": relative, "title": path.stem, "text": text.strip() + "\n"}
    return None


_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
_HEADING2_RE = re.compile(r"^## +(.+?)\s*$")


def graph(max_notes: int = 400) -> dict[str, Any]:
    """The vault as a map for the companion (``GET /api/knowledge/graph``).

    Nodes: a ``root`` (JARVIS's memory), each folder, each note, and the
    Profile note's topics (its ``## category`` headings, weighted by how many
    facts sit under each). Edges: folder tree, note -> its folder, Profile ->
    topics, and every ``[[wikilink]]`` that resolves to another authorized
    note (``kind: "link"``). Titles only, never note text."""
    files = list(_markdown_files())[:max_notes]
    nodes: list[dict[str, Any]] = [{"id": "root", "title": "Memory", "kind": "root", "weight": 1}]
    edges: list[dict[str, str]] = []
    by_stem: dict[str, str] = {}
    by_path: dict[str, str] = {}
    folders: set[str] = set()

    def folder(path: str) -> str:
        if path in ("", "."):
            return "root"
        node = f"folder:{path}"
        if path not in folders:
            folders.add(path)
            nodes.append({"id": node, "title": path.split("/")[-1], "kind": "folder", "weight": 1})
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
            edges.append({"from": node, "to": folder(parent), "kind": "tree"})
        return node

    texts: dict[str, str] = {}
    for path, relative in files:
        rel = relative.as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        texts[rel] = text
        nodes.append({"id": rel, "title": path.stem, "kind": "note",
                      "weight": max(1, min(12, len(text) // 800 + 1))})
        edges.append({"from": rel, "to": folder(relative.parent.as_posix()), "kind": "tree"})
        by_stem.setdefault(path.stem.lower(), rel)
        by_path[rel.lower().removesuffix(".md")] = rel

    for rel, text in texts.items():
        for target in {m.group(1).strip().lower() for m in _WIKILINK_RE.finditer(text)}:
            resolved = by_path.get(target) or by_stem.get(target.rsplit("/", 1)[-1])
            if resolved and resolved != rel:
                edges.append({"from": rel, "to": resolved, "kind": "link"})

    profile = texts.get(_PROFILE_NOTE.as_posix())
    if profile:
        topic, count = None, 0

        def close() -> None:
            if topic:
                nodes.append({"id": f"topic:{topic}", "title": topic, "kind": "topic", "weight": max(1, count)})
                edges.append({"from": f"topic:{topic}", "to": _PROFILE_NOTE.as_posix(), "kind": "topic"})

        for line in profile.splitlines():
            heading = _HEADING2_RE.match(line)
            if heading:
                close()
                topic, count = heading.group(1), 0
            elif topic and line.startswith("- "):
                count += 1
        close()
    return {"nodes": nodes, "edges": edges}


# --- Writing: quick capture and the daily journal ---------------------------

_NOTE_NAME_RE = re.compile(r"[^A-Za-z0-9 _\-]+")


def _vault_folder(env_name: str, default: str) -> Path:
    """Resolve a vault-relative folder setting, refusing paths outside the vault."""
    root = vault_path()
    configured = (os.getenv(env_name, "") or "").strip() or default
    candidate = (root / configured)
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        log.warning("%s points outside the vault; using %s", env_name, default)
        candidate = root / default
    return candidate


def journal_folder() -> Path:
    return _vault_folder("OBSIDIAN_JOURNAL_FOLDER", "Jarvis Memory/Journal")


def notes_folder() -> Path:
    return _vault_folder("OBSIDIAN_NOTES_FOLDER", "Jarvis Memory/Notes")


def _daily_note_path(moment: datetime) -> Path:
    return journal_folder() / f"{moment.strftime('%Y-%m-%d')}.md"


def _new_daily_note(moment: datetime) -> str:
    return (
        "---\n"
        "jarvis_managed: true\n"
        f"date: {moment.strftime('%Y-%m-%d')}\n"
        "---\n\n"
        f"# {moment.strftime('%A, %B')} {moment.day}, {moment.year}\n"
    )


def _append_under_heading(text: str, heading: str, line: str) -> str:
    """Append ``line`` at the end of the ``heading`` section, creating it if needed."""
    lines = text.rstrip("\n").split("\n") if text.strip() else []
    try:
        start = next(index for index, item in enumerate(lines) if item.strip() == heading)
    except StopIteration:
        if lines:
            lines.append("")
        lines.extend((heading, "", line))
        return "\n".join(lines) + "\n"
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    # Insert after the last non-blank line of the section so entries stay
    # contiguous, keeping one blank line before any following heading.
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, line)
    if insert_at + 1 < len(lines) and lines[insert_at + 1].startswith("## "):
        lines.insert(insert_at + 1, "")
    return "\n".join(lines) + "\n"


def append_journal_entry(
    text: str,
    *,
    section: str = "Notes",
    at: datetime | None = None,
) -> Path:
    """Append one timestamped line to today's journal note under ``section``.

    ``section`` is a level-two heading name such as ``Notes`` (things Nick asked
    to capture) or ``Log`` (what JARVIS did for him). Returns the note path.
    """
    moment = at or datetime.now().astimezone()
    text = " ".join((text or "").split())
    if not text:
        raise ValueError("empty journal entry")
    path = _daily_note_path(moment)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else _new_daily_note(moment)
    entry = f"- {moment.strftime('%H:%M')} — {text}"
    updated = _append_under_heading(existing, f"## {section}", entry)
    temporary = path.with_suffix(".md.tmp")
    temporary.write_text(updated, encoding="utf-8")
    temporary.replace(path)
    return path


def capture_note(text: str, title: str = "") -> str:
    """Save something Nick said to keep. Titled notes get their own file."""
    text = (text or "").strip()
    if not text:
        return "Refused: nothing to capture."
    title = " ".join(_NOTE_NAME_RE.sub(" ", title or "").split())[:80]
    moment = datetime.now().astimezone()
    try:
        if not title:
            path = append_journal_entry(text, section="Notes", at=moment)
            relative = path.relative_to(vault_path()).as_posix()
            return f"Captured in today's journal ({relative})."
        folder = notes_folder()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{title}.md"
        if path.exists():
            body = path.read_text(encoding="utf-8").rstrip("\n")
            body += f"\n\n{moment.strftime('%Y-%m-%d %H:%M')}\n{text}\n"
            action = "Added to"
        else:
            body = (
                "---\njarvis_managed: true\n"
                f"created: {moment.isoformat(timespec='minutes')}\n---\n\n"
                f"# {title}\n\n{text}\n"
            )
            action = "Created"
        temporary = path.with_suffix(".md.tmp")
        temporary.write_text(body, encoding="utf-8")
        temporary.replace(path)
        relative = path.relative_to(vault_path()).as_posix()
        return f"{action} note {relative}."
    except OSError as exc:
        log.error("Could not write to the vault: %s", exc)
        return f"Could not write to the vault: {exc}"


# --- Importing: a pasted "everything you know about me" list ------------------

_IMPORT_MAX_CHARS = 200_000


def import_note(title: str, text: str) -> str:
    """Save pasted text (e.g. what another assistant knows about Nick) as a
    note under ``Jarvis Memory/Imported`` and return its vault-relative path.
    Searched like any note, section by section, so headings help. An existing
    note of the same name is replaced. Raises ValueError on empty input."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Nothing to import.")
    if len(text) > _IMPORT_MAX_CHARS:
        raise ValueError(f"That's over {_IMPORT_MAX_CHARS:,} characters; split it into a few imports.")
    name = " ".join(_NOTE_NAME_RE.sub(" ", title or "").split())[:80] or datetime.now().strftime("Import %Y-%m-%d %H%M")
    folder = _vault_folder("OBSIDIAN_IMPORT_FOLDER", "Jarvis Memory/Imported")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{name}.md"
    stamp = datetime.now(timezone.utc).isoformat()
    body = f"---\nimported: {stamp}\n---\n\n# {name}\n\n{text}\n"
    temporary = target.with_suffix(".md.tmp")
    temporary.write_text(body, encoding="utf-8")
    temporary.replace(target)
    return target.relative_to(vault_path()).as_posix()


# --- Core profile: the always-known summary distilled from imports ----------
# ``memory_extractor.refresh_core_profile`` writes it; ``memory.render_memory_block``
# injects it every turn. It's a plain note, so Nick can read or edit it.

_CORE_SOURCE_CHARS = 120_000


def core_profile_path() -> Path:
    return vault_path() / _CORE_NOTE


def read_core_profile() -> str:
    """The core profile's body (frontmatter stripped), or ""."""
    try:
        text = core_profile_path().read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + 5:]
    return text.strip()


def write_core_profile(body: str) -> None:
    target = core_profile_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    temporary = target.with_suffix(".md.tmp")
    temporary.write_text(f"---\ndistilled: {stamp}\n---\n\n{body.strip()}\n", encoding="utf-8")
    temporary.replace(target)


def imported_notes() -> list[tuple[str, float, str]]:
    """Every note under the import folder: (name, modified time, text),
    oldest first, together capped at ``_CORE_SOURCE_CHARS``."""
    folder = _vault_folder("OBSIDIAN_IMPORT_FOLDER", "Jarvis Memory/Imported")
    notes: list[tuple[str, float, str]] = []
    if not folder.is_dir():
        return notes
    for path in sorted(folder.glob("*.md")):
        try:
            notes.append((path.stem, path.stat().st_mtime, path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError):
            continue
    notes.sort(key=lambda note: note[1])
    kept, used = [], 0
    for name, mtime, text in reversed(notes):  # newest imports win the budget
        if used >= _CORE_SOURCE_CHARS:
            break
        text = text[: _CORE_SOURCE_CHARS - used]
        used += len(text)
        kept.append((name, mtime, text))
    return list(reversed(kept))


def core_profile_stale() -> bool:
    """True when an import is newer than the core profile (or it's missing)."""
    notes = imported_notes()
    if not notes:
        return False
    try:
        built = core_profile_path().stat().st_mtime
    except OSError:
        return True
    return max(mtime for _, mtime, _ in notes) > built
