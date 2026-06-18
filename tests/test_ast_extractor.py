"""Tests for the multi-language AST extractor."""

import json
import os
import tempfile

import pytest

from ast_extractor import ASTExtractor, detect_language, supported_extensions


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def extractor():
    return ASTExtractor()


@pytest.fixture
def python_file(tmp_path):
    """Create a temporary Python file with known structure."""
    code = '''\
import os

class AuthManager:
    def login(self, user: str, password: str) -> bool:
        if user == "admin" and password == "secret":
            return True
        return False

    def logout(self) -> None:
        pass

def hash_password(raw: str) -> str:
    return raw[::-1]
'''
    f = tmp_path / "test_module.py"
    f.write_text(code)
    return str(f)


@pytest.fixture
def js_file(tmp_path):
    """Create a temporary JavaScript file."""
    code = '''\
function greet(name) {
    return `Hello, ${name}!`;
}

class Calculator {
    add(a, b) {
        return a + b;
    }
}
'''
    f = tmp_path / "test_module.js"
    f.write_text(code)
    return str(f)


# ---------------------------------------------------------------------------
# detect_language
# ---------------------------------------------------------------------------

class TestDetectLanguage:
    def test_python(self):
        assert detect_language("foo/bar.py") == ".py"

    def test_javascript(self):
        assert detect_language("src/index.js") == ".js"

    def test_typescript(self):
        assert detect_language("app.ts") == ".ts"

    def test_tsx(self):
        assert detect_language("Component.tsx") == ".tsx"

    def test_unknown(self):
        assert detect_language("readme.md") == ".md"


# ---------------------------------------------------------------------------
# ASTExtractor — Python files
# ---------------------------------------------------------------------------

class TestExtractorPython:
    def test_extract_function(self, extractor, python_file):
        result = extractor.extract_function_code(python_file, "hash_password")
        assert "error" not in result
        assert result["function"] == "hash_password"
        assert "raw[::-1]" in result["code"]
        assert result["start_line"] > 0
        assert result["end_line"] >= result["start_line"]

    def test_extract_nonexistent_function(self, extractor, python_file):
        result = extractor.extract_function_code(python_file, "nonexistent")
        assert "error" in result

    def test_extract_from_missing_file(self, extractor):
        result = extractor.extract_function_code("/tmp/no_such_file_xyz.py", "foo")
        assert "error" in result

    def test_skeleton(self, extractor, python_file):
        skeleton = extractor.get_skeleton(python_file)
        assert "class AuthManager:" in skeleton
        assert "def login(...)" in skeleton
        assert "def logout(...)" in skeleton
        assert "def hash_password(...)" in skeleton

    def test_ast_json(self, extractor, python_file):
        result = extractor.get_ast_json(python_file)
        assert result["language"] == "python"
        assert len(result["nodes"]) >= 2  # At least 1 class + 1 function

        # Find the class
        classes = [n for n in result["nodes"] if n["type"] == "class"]
        assert len(classes) == 1
        assert classes[0]["name"] == "AuthManager"
        assert "login" in classes[0]["methods"]

        # Find the function
        functions = [n for n in result["nodes"] if n["type"] == "function"]
        assert any(f["name"] == "hash_password" for f in functions)

    def test_unsupported_extension(self, extractor, tmp_path):
        f = tmp_path / "readme.md"
        f.write_text("# Hello")
        result = extractor.extract_function_code(str(f), "foo")
        assert "error" in result
        assert "Unsupported" in result["error"]


# ---------------------------------------------------------------------------
# ASTExtractor — JavaScript files (conditional)
# ---------------------------------------------------------------------------

class TestExtractorJavaScript:
    @pytest.fixture(autouse=True)
    def skip_if_no_js(self):
        if ".js" not in supported_extensions():
            pytest.skip("tree-sitter-javascript not installed")

    def test_extract_function(self, extractor, js_file):
        result = extractor.extract_function_code(js_file, "greet")
        assert "error" not in result
        assert "Hello" in result["code"]

    def test_skeleton(self, extractor, js_file):
        skeleton = extractor.get_skeleton(js_file)
        assert "greet" in skeleton.lower() or "def greet" in skeleton

    def test_ast_json(self, extractor, js_file):
        result = extractor.get_ast_json(js_file)
        assert result["language"] == "javascript"
        assert len(result["nodes"]) >= 1
