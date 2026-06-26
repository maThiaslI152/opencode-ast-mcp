"""Project-wide codebase index for the 4 codebase-awareness MCP tools.

Provides an mtime-cached, recursive view of the project tree, with four
public methods that back the corresponding MCP tools:

- :meth:`CodebaseIndex.list_files`            → ``list_files`` tool
- :meth:`CodebaseIndex.get_overview`          → ``get_project_overview`` tool
- :meth:`CodebaseIndex.search_symbol`         → ``search_symbol`` tool
- :meth:`CodebaseIndex.find_references`       → ``find_references`` tool

Cache design:
- Keyed by relative path → mtime.
- Up to 1000 entries with FIFO eviction.
- No background threads, no file-watcher. Each call checks mtime; the
  next call after an edit sees the new mtime and re-parses.
- Parse trees are shared across all 4 methods, so a project overview
  call followed by a symbol search reuses the same trees where the
  files haven't changed.

Scope (v0.2.0):
- Recursive into the project root.
- Skips ``.venv``, ``venv``, ``__pycache__``, ``.git``, ``node_modules``,
  ``.pytest_cache``, ``.opencode``, ``dist``, ``build``, ``.mypy_cache``,
  ``.ruff_cache``, ``htmlcov`` (hardcoded set).
- Source code parsing limited to ``ast_extractor.supported_extensions()``
  (``.py``, ``.js``, ``.ts``, ``.tsx``, ``.go``, ``.rs``, ``.java``,
  ``.c``/``.cpp``/``.h``, ``.rb``, ``.php``, ``.tex``/``.ltx``).
- Match results capped at 200 per call (sets ``truncated: true``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from tree_sitter import Tree

import ast_extractor
from config import get_project_root

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SKIP_DIRS: set[str] = {
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    "node_modules",
    ".pytest_cache",
    ".opencode",
    "dist",
    "build",
    ".mypy_cache",
    ".ruff_cache",
    "htmlcov",
}
"""Directories that should never be recursed into."""

_MAX_CACHE_ENTRIES: int = 1000
"""FIFO eviction ceiling for the parsed-AST cache."""

_MATCH_CAP: int = 200
"""Maximum number of matches returned by search_symbol / find_references."""

_LANGUAGE_NAME: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".h": "c",
    ".rb": "ruby",
    ".php": "php",
    ".tex": "latex",
    ".ltx": "latex",
}
"""Display name for each supported extension."""


# ---------------------------------------------------------------------------
# Cache entry
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    """One entry in the mtime-keyed parsed-AST cache."""

    mtime: float
    tree: Tree
    source: bytes
    ext: str


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


class CodebaseIndex:
    """Lazy, mtime-cached, recursive index over the project tree.

    Parameters
    ----------
    project_root:
        Host directory to treat as the project root. Defaults to
        ``get_project_root()``.
    """

    def __init__(self, project_root: Path | None = None) -> None:
        self._root: Path = project_root or get_project_root()
        self._cache: dict[str, _CacheEntry] = {}
        self._extractor = ast_extractor.ASTExtractor()

    # ------------------------------------------------------------------
    # Public API — one method per MCP tool
    # ------------------------------------------------------------------

    def list_files(self, pattern: str = "**/*") -> list[str]:
        """Return all files matching *pattern*, sorted, relative to root.

        Filters out files inside :data:`_SKIP_DIRS`. Does not restrict
        by language — this is a generic file lister, useful for the
        agent to discover which files exist before deciding which
        language-specific tool to call next.

        Args:
            pattern: A glob pattern. Defaults to ``"**/*"`` (recursive).
                     Non-recursive patterns (``"*.py"``) are also supported.

        Returns:
            Sorted list of relative paths (POSIX-style).
        """
        results: list[str] = []
        for path in self._root.glob(pattern):
            if not path.is_file():
                continue
            if self._is_skipped(path):
                continue
            results.append(self._rel(path))
        return sorted(results)

    def get_overview(self, depth: int = 1) -> dict[str, Any]:
        """Return a top-level project map with per-file skeletons.

        Walks the project tree, stopping at *depth* directory levels
        (depth=1 = just the project root; depth=2 = one level of
        subdirectories too). For every supported-language file found,
        includes its path, language, size, and skeleton.

        Args:
            depth: Maximum number of path components relative to root.

        Returns:
            Dict with keys ``root`` (str), ``files`` (list of file
            dicts), ``truncated`` (bool), ``scanned_files`` (int).
        """
        files: list[dict[str, Any]] = []
        scanned = 0
        for path in sorted(self._root.rglob("*")):
            if not path.is_file():
                continue
            if self._is_skipped(path):
                continue
            rel = path.relative_to(self._root)
            if len(rel.parts) > depth:
                continue
            ext = path.suffix.lower()
            if ext not in ast_extractor.supported_extensions():
                continue
            try:
                tree, source = self._get_parsed(path)
            except (FileNotFoundError, ValueError):
                continue
            skeleton = ast_extractor.ASTExtractor.build_skeleton_from_tree(
                tree, source, ext
            )
            files.append(
                {
                    "path": self._rel(path),
                    "language": _LANGUAGE_NAME.get(ext, ext),
                    "size": path.stat().st_size,
                    "skeleton": skeleton,
                }
            )
            scanned += 1
        return {
            "root": str(self._root),
            "files": files,
            "truncated": False,
            "scanned_files": scanned,
        }

    def search_symbol(
        self, name: str, language: str | None = None
    ) -> dict[str, Any]:
        """Find every top-level function/class/method named *name*.

        Walks every supported-language file under the project root and
        runs a tree-sitter-based name match. ``language`` filters to a
        single language (``"python"``, ``"javascript"``,
        ``"typescript"``, ``"tsx"``, ``"go"``, ``"rust"``,
        ``"java"``, ``"c"``, ``"cpp"``, ``"ruby"``, ``"php"``,
        ``"latex"``).

        Args:
            name: Symbol name to search for (exact match).
            language: Optional language filter. ``None`` = all supported.

        Returns:
            Dict with ``matches`` (list of ``{file, name, type,
            start_line, end_line}``), ``truncated`` (bool — ``True`` if
            capped at :data:`_MATCH_CAP`), ``scanned_files`` (int).
        """
        matches: list[dict[str, Any]] = []
        scanned = 0
        truncated = False
        for path in self._iter_source_files(language_filter=language):
            scanned += 1
            ext = path.suffix.lower()
            try:
                tree, source = self._get_parsed(path)
            except (FileNotFoundError, ValueError):
                continue
            for m in ast_extractor.ASTExtractor.find_named_nodes(
                tree, source, ext, name
            ):
                if len(matches) >= _MATCH_CAP:
                    truncated = True
                    break
                m["file"] = self._rel(path)
                matches.append(m)
            if truncated:
                break
        return {
            "matches": matches,
            "truncated": truncated,
            "scanned_files": scanned,
        }

    def find_references(
        self, name: str, filepath: str | None = None
    ) -> dict[str, Any]:
        """Find every identifier reference to *name*.

        Walks the AST (not the text) so string literals and comments
        are naturally excluded — only true syntactic references are
        returned. If *filepath* is given, scopes the search to that one
        file (relative to project root). Otherwise the search is
        project-wide.

        Args:
            name: Identifier name to find (exact match).
            filepath: Optional relative path to scope the search.

        Returns:
            Dict with ``references`` (list of ``{file, line, context}``),
            ``truncated`` (bool), ``scanned_files`` (int).
        """
        matches: list[dict[str, Any]] = []
        scanned = 0
        truncated = False
        for path in self._iter_source_files(single_file=filepath):
            scanned += 1
            ext = path.suffix.lower()
            try:
                tree, source = self._get_parsed(path)
            except (FileNotFoundError, ValueError):
                continue
            for ref in ast_extractor.ASTExtractor.find_identifier_references(
                tree, source, ext, name
            ):
                if len(matches) >= _MATCH_CAP:
                    truncated = True
                    break
                ref["file"] = self._rel(path)
                matches.append(ref)
            if truncated:
                break
        return {
            "references": matches,
            "truncated": truncated,
            "scanned_files": scanned,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _iter_source_files(
        self,
        language_filter: str | None = None,
        single_file: str | None = None,
    ) -> Iterator[Path]:
        """Yield source files to scan, sorted, skipping noise.

        Exactly one of *language_filter* / *single_file* should be
        used. If *single_file* is given, the iterator yields at most one
        path; if the file doesn't exist, the iterator is empty.
        """
        if single_file is not None:
            candidate = self._root / single_file
            if candidate.is_file() and not self._is_skipped(candidate):
                yield candidate
            return

        for path in self._root.rglob("*"):
            if not path.is_file():
                continue
            if self._is_skipped(path):
                continue
            ext = path.suffix.lower()
            if ext not in ast_extractor.supported_extensions():
                continue
            if language_filter and _LANGUAGE_NAME.get(ext) != language_filter:
                continue
            yield path

    def _is_skipped(self, path: Path) -> bool:
        """Return True if *path* is inside a directory we should skip."""
        try:
            rel = path.relative_to(self._root)
        except ValueError:
            return True
        return any(part in _SKIP_DIRS for part in rel.parts)

    def _get_parsed(self, path: Path) -> tuple[Tree, bytes]:
        """Return ``(tree, source)`` for *path*, using the mtime cache.

        Re-parses when the file's mtime differs from the cached one.
        Triggers FIFO eviction when the cache exceeds
        :data:`_MAX_CACHE_ENTRIES`.

        Raises:
            FileNotFoundError: If *path* doesn't exist.
            ValueError: If the file extension isn't in
                :func:`ast_extractor.supported_extensions`.
        """
        key = self._rel(path)
        current_mtime = path.stat().st_mtime
        entry = self._cache.get(key)
        if entry is not None and entry.mtime == current_mtime:
            return entry.tree, entry.source

        ext = path.suffix.lower()
        if ext not in ast_extractor.supported_extensions():
            raise ValueError(f"Unsupported extension: {ext}")

        source = path.read_bytes()
        parser, _, _ = self._extractor._get_parser(str(path))  # noqa: SLF001
        tree = parser.parse(source)
        self._cache[key] = _CacheEntry(
            mtime=current_mtime, tree=tree, source=source, ext=ext
        )
        self._evict_if_needed()
        return tree, source

    def _evict_if_needed(self) -> None:
        """FIFO-evict oldest cache entries until size is under the cap."""
        while len(self._cache) > _MAX_CACHE_ENTRIES:
            self._cache.pop(next(iter(self._cache)))

    def _rel(self, path: Path) -> str:
        """Return *path* relative to the project root as a POSIX string."""
        return path.relative_to(self._root).as_posix()
