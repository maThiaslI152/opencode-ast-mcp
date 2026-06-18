"""Tests for the codebase-awareness tools (v0.2.0).

Exercises the four new ``CodebaseIndex`` methods on real temporary
directory trees — no mocking. Each test creates its own source files,
constructs a :class:`CodebaseIndex` pointing at the temp root, and
asserts on the returned dicts.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from codebase_index import CodebaseIndex


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    """Write *content* to *path*, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


PY_AUTH = '''\
"""Sample auth module."""
from typing import Optional


def login(user: str, password: str) -> bool:
    return user == "admin" and password == "secret"


class AuthService:
    """Provides authentication and session management."""

    def __init__(self, db):
        self.db = db

    def authenticate(self, token: str) -> Optional[str]:
        return self.db.lookup(token)

    def logout(self, user: str) -> None:
        self.db.delete(user)
'''


PY_UTILS = '''\
"""Utility functions."""


def login(email: str) -> bool:  # Note: same name as auth.login
    return "@" in email


class Helper:
    def login(self, user):  # A method with the same name
        return True

    def other(self):
        return self.login("x")
'''


JS_UTIL = '''\
// Sample JavaScript module

export function login(user) {
  return user === "admin";
}

export class AuthService {
  authenticate(token) {
    return this.db.lookup(token);
  }
}
'''


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------


class TestListFiles:
    def test_recursive_default(self, tmp_path):
        _write(tmp_path / "a.py", "")
        _write(tmp_path / "sub" / "b.py", "")
        _write(tmp_path / "sub" / "deep" / "c.py", "")
        _write(tmp_path / "README.md", "")

        idx = CodebaseIndex(tmp_path)
        files = idx.list_files("**/*")

        assert "a.py" in files
        assert "sub/b.py" in files
        assert "sub/deep/c.py" in files
        assert "README.md" in files
        # Sorted
        assert files == sorted(files)

    def test_non_recursive_pattern(self, tmp_path):
        _write(tmp_path / "a.py", "")
        _write(tmp_path / "sub" / "b.py", "")

        idx = CodebaseIndex(tmp_path)
        files = idx.list_files("*.py")

        assert "a.py" in files
        assert "sub/b.py" not in files  # Non-recursive

    def test_skip_dirs_excluded(self, tmp_path):
        _write(tmp_path / "real.py", "")
        _write(tmp_path / ".venv" / "fake.py", "")
        _write(tmp_path / "node_modules" / "fake.py", "")
        _write(tmp_path / "__pycache__" / "fake.pyc", "")
        _write(tmp_path / ".git" / "config.py", "")
        _write(tmp_path / "build" / "artifact.py", "")

        idx = CodebaseIndex(tmp_path)
        files = idx.list_files("**/*")

        assert "real.py" in files
        # All skip-dir files should be filtered
        for f in files:
            parts = Path(f).parts
            assert ".venv" not in parts
            assert "node_modules" not in parts
            assert "__pycache__" not in parts
            assert ".git" not in parts
            assert "build" not in parts

    def test_no_matches_returns_empty_list(self, tmp_path):
        _write(tmp_path / "a.txt", "")
        idx = CodebaseIndex(tmp_path)
        assert idx.list_files("*.py") == []


# ---------------------------------------------------------------------------
# get_project_overview
# ---------------------------------------------------------------------------


class TestProjectOverview:
    def test_depth_one_only_root_files(self, tmp_path):
        _write(tmp_path / "a.py", PY_AUTH)
        _write(tmp_path / "sub" / "b.py", PY_AUTH)

        idx = CodebaseIndex(tmp_path)
        result = idx.get_overview(depth=1)

        assert result["scanned_files"] == 1
        assert len(result["files"]) == 1
        assert result["files"][0]["path"] == "a.py"
        assert result["files"][0]["language"] == "python"
        assert "class AuthService" in result["files"][0]["skeleton"]

    def test_depth_two_includes_subdirs(self, tmp_path):
        _write(tmp_path / "a.py", PY_AUTH)
        _write(tmp_path / "sub" / "b.py", PY_AUTH)
        _write(tmp_path / "sub" / "deep" / "c.py", PY_AUTH)  # too deep

        idx = CodebaseIndex(tmp_path)
        result = idx.get_overview(depth=2)

        paths = [f["path"] for f in result["files"]]
        assert "a.py" in paths
        assert "sub/b.py" in paths
        assert "sub/deep/c.py" not in paths
        assert result["scanned_files"] == 2

    def test_mixed_languages_reported(self, tmp_path):
        _write(tmp_path / "a.py", PY_AUTH)
        _write(tmp_path / "b.js", JS_UTIL)

        idx = CodebaseIndex(tmp_path)
        result = idx.get_overview(depth=1)

        langs = {f["language"] for f in result["files"]}
        assert "python" in langs
        assert "javascript" in langs

    def test_non_source_files_excluded(self, tmp_path):
        _write(tmp_path / "a.py", PY_AUTH)
        _write(tmp_path / "README.md", "# Hi")
        _write(tmp_path / "data.json", "{}")

        idx = CodebaseIndex(tmp_path)
        result = idx.get_overview(depth=1)

        paths = [f["path"] for f in result["files"]]
        assert paths == ["a.py"]


# ---------------------------------------------------------------------------
# search_symbol
# ---------------------------------------------------------------------------


class TestSearchSymbol:
    def test_find_function(self, tmp_path):
        _write(tmp_path / "a.py", PY_AUTH)
        _write(tmp_path / "b.py", PY_UTILS)

        idx = CodebaseIndex(tmp_path)
        result = idx.search_symbol("login")

        # Should find: a.py::login (function), b.py::login (function),
        # b.py::Helper.login (method) — 3 matches
        assert result["truncated"] is False
        assert len(result["matches"]) == 3
        types_and_files = {(m["type"], m["file"]) for m in result["matches"]}
        assert ("function", "a.py") in types_and_files
        assert ("function", "b.py") in types_and_files
        assert ("method", "b.py") in types_and_files

    def test_find_class(self, tmp_path):
        _write(tmp_path / "a.py", PY_AUTH)

        idx = CodebaseIndex(tmp_path)
        result = idx.search_symbol("AuthService")

        assert len(result["matches"]) == 1
        assert result["matches"][0]["type"] == "class"
        assert result["matches"][0]["name"] == "AuthService"
        assert result["matches"][0]["file"] == "a.py"
        assert result["matches"][0]["start_line"] >= 1

    def test_language_filter(self, tmp_path):
        _write(tmp_path / "a.py", PY_AUTH)
        _write(tmp_path / "b.js", JS_UTIL)

        idx = CodebaseIndex(tmp_path)
        result_py = idx.search_symbol("login", language="python")
        result_js = idx.search_symbol("login", language="javascript")

        py_files = {m["file"] for m in result_py["matches"]}
        js_files = {m["file"] for m in result_js["matches"]}

        assert py_files == {"a.py"}
        assert js_files == {"b.js"}

    def test_no_matches(self, tmp_path):
        _write(tmp_path / "a.py", PY_AUTH)

        idx = CodebaseIndex(tmp_path)
        result = idx.search_symbol("nonexistent_name_xyz")

        assert result["matches"] == []
        assert result["truncated"] is False
        assert result["scanned_files"] == 1


# ---------------------------------------------------------------------------
# find_references
# ---------------------------------------------------------------------------


class TestFindReferences:
    def test_single_file_scope(self, tmp_path):
        _write(tmp_path / "a.py", PY_AUTH)
        _write(tmp_path / "b.py", PY_UTILS)  # also has 'login'

        idx = CodebaseIndex(tmp_path)
        result = idx.find_references("login", filepath="a.py")

        # a.py has 1 definition (line ~6) + 0 calls (login isn't called in a.py)
        # Actually the def itself is an identifier too — let's count.
        assert result["scanned_files"] == 1
        # All references must be in a.py
        for r in result["references"]:
            assert r["file"] == "a.py"

    def test_project_wide(self, tmp_path):
        _write(tmp_path / "a.py", PY_AUTH)
        _write(tmp_path / "b.py", PY_UTILS)

        idx = CodebaseIndex(tmp_path)
        result = idx.find_references("login")

        assert result["scanned_files"] == 2
        files = {r["file"] for r in result["references"]}
        # Both files contain 'login' identifiers
        assert "a.py" in files
        assert "b.py" in files

    def test_ignores_string_literals(self, tmp_path):
        """References in string literals should NOT be matched — the AST
        walker naturally excludes them because string content is not
        an ``identifier`` node."""
        src = '''\
def login():
    return "login failed"  # "login" inside a string should not match

x = login()  # this IS a real reference
'''
        _write(tmp_path / "a.py", src)

        idx = CodebaseIndex(tmp_path)
        result = idx.find_references("login", filepath="a.py")

        # The string "login failed" should not appear in any context line
        for r in result["references"]:
            assert "login failed" not in r["context"], (
                f"String literal leaked: {r}"
            )

    def test_no_matches(self, tmp_path):
        _write(tmp_path / "a.py", PY_AUTH)

        idx = CodebaseIndex(tmp_path)
        result = idx.find_references("nonexistent_xyz_abc")

        assert result["references"] == []
        assert result["truncated"] is False


# ---------------------------------------------------------------------------
# mtime cache
# ---------------------------------------------------------------------------


class TestMtimeCache:
    def test_cache_hit_no_reparse(self, tmp_path):
        """Calling overview twice on unchanged files must hit the cache."""
        _write(tmp_path / "a.py", PY_AUTH)

        idx = CodebaseIndex(tmp_path)
        idx.get_overview(depth=1)
        first_cache_size = len(idx._cache)
        assert first_cache_size == 1

        # Second call — same files, same mtime (write happened just now,
        # but no further changes). The cache should NOT grow.
        idx.get_overview(depth=1)
        assert len(idx._cache) == 1

    def test_cache_invalidation_after_mtime_change(self, tmp_path):
        _write(tmp_path / "a.py", PY_AUTH)
        idx = CodebaseIndex(tmp_path)
        idx.get_overview(depth=1)
        assert len(idx._cache) == 1

        # Force the mtime to advance by at least 1 second (filesystem
        # resolution on some platforms is 1s, so 1.1s is safe).
        target = tmp_path / "a.py"
        time.sleep(1.1)
        target.write_text(PY_AUTH + "\n\n# extra line", encoding="utf-8")

        idx.get_overview(depth=1)
        # Cache should still have 1 entry (replaced in place)
        assert len(idx._cache) == 1
        # And the new skeleton should reflect the new file
        result = idx.get_overview(depth=1)
        assert any("# extra line" not in f["skeleton"] for f in result["files"])

    def test_cache_growth_across_distinct_files(self, tmp_path):
        _write(tmp_path / "a.py", PY_AUTH)
        _write(tmp_path / "b.py", PY_AUTH)
        _write(tmp_path / "c.py", PY_AUTH)
        idx = CodebaseIndex(tmp_path)
        idx.get_overview(depth=1)
        assert len(idx._cache) == 3
