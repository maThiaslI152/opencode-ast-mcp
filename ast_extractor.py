"""Multi-language AST extractor using tree-sitter.

Supports Python, JavaScript, and TypeScript with graceful fallback
when language grammars are not installed. Provides:
- extract_function_code: Find a specific function by name
- get_skeleton: Compact structural outline of a file
- get_ast_json: Structured JSON representation of file nodes
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from tree_sitter import Language, Parser, Query, QueryCursor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Language registry — load grammars with graceful fallback
# ---------------------------------------------------------------------------

# Maps file extension -> (Language object, config dict)
# Config dict keys: function_node, class_node, method_node, extra_nodes
_LANGUAGES: dict[str, tuple[Language, dict[str, Any]]] = {}

# Python (always available — project requirement)
try:
    import tree_sitter_python as tspython

    _LANGUAGES[".py"] = (
        Language(tspython.language()),
        {
            "function_node": "function_definition",
            "class_node": "class_definition",
            "method_node": "function_definition",
            "extra_nodes": [],
        },
    )
except ImportError:
    logger.warning("tree-sitter-python not installed — .py files unsupported")

# JavaScript
try:
    import tree_sitter_javascript as tsjavascript

    _LANGUAGES[".js"] = (
        Language(tsjavascript.language()),
        {
            "function_node": "function_declaration",
            "class_node": "class_declaration",
            "method_node": "method_definition",
            "extra_nodes": ["arrow_function", "lexical_declaration"],
        },
    )
except ImportError:
    logger.info("tree-sitter-javascript not installed — .js files unsupported")

# TypeScript
try:
    import tree_sitter_typescript as tstypescript

    _ts_config = {
        "function_node": "function_declaration",
        "class_node": "class_declaration",
        "method_node": "method_definition",
        "extra_nodes": [
            "interface_declaration",
            "type_alias_declaration",
            "arrow_function",
            "lexical_declaration",
        ],
    }
    _LANGUAGES[".ts"] = (Language(tstypescript.language_typescript()), _ts_config)
    _LANGUAGES[".tsx"] = (Language(tstypescript.language_tsx()), _ts_config)
except ImportError:
    logger.info("tree-sitter-typescript not installed — .ts/.tsx files unsupported")


def detect_language(filepath: str) -> str:
    """Return the lowercase file extension (e.g. '.py', '.ts')."""
    return Path(filepath).suffix.lower()


def supported_extensions() -> list[str]:
    """Return the list of currently supported file extensions."""
    return list(_LANGUAGES.keys())


class ASTExtractor:
    """Multi-language AST extractor powered by tree-sitter.

    Initialises parsers for all available language grammars at
    construction time.  Methods accept a file path and automatically
    select the correct parser based on the file extension.
    """

    def __init__(self) -> None:
        self._parsers: dict[str, tuple[Parser, Language, dict[str, Any]]] = {}
        for ext, (lang, config) in _LANGUAGES.items():
            parser = Parser(lang)
            self._parsers[ext] = (parser, lang, config)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_parser(
        self, filepath: str
    ) -> tuple[Parser, Language, dict[str, Any]]:
        """Return (parser, language, config) for *filepath*.

        Raises ``ValueError`` if the language is not supported.
        """
        ext = detect_language(filepath)
        if ext not in self._parsers:
            raise ValueError(
                f"Unsupported file extension '{ext}'. "
                f"Supported: {supported_extensions()}"
            )
        return self._parsers[ext]

    def _read_source(self, file_path: str) -> bytes:
        """Read file as bytes, raising on missing files."""
        with open(file_path, "rb") as f:
            return f.read()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_function_code(self, file_path: str, function_name: str) -> dict:
        """Extract the full source code of a named function or method.

        Returns a dict with keys: file, function, code, start_line,
        end_line.  On failure returns a dict with an 'error' key.
        """
        try:
            parser, lang, config = self._get_parser(file_path)
        except ValueError as exc:
            return {"error": str(exc)}

        try:
            source_code = self._read_source(file_path)
        except FileNotFoundError:
            return {"error": f"File not found: {file_path}"}

        tree = parser.parse(source_code)

        # Build a query for the language's function node type
        func_type = config["function_node"]
        query_string = f"""
        ({func_type}
            name: (identifier) @func_name
            (#eq? @func_name "{function_name}")
        ) @target_function
        """

        try:
            query = Query(lang, query_string)
            captures = QueryCursor(query).captures(tree.root_node)
        except Exception:
            # Fallback: manual tree walk if query syntax doesn't fit
            return self._walk_find(tree.root_node, source_code, function_name, file_path)

        for node in captures.get("target_function", []):
            code = source_code[node.start_byte : node.end_byte].decode("utf-8")
            return {
                "file": file_path,
                "function": function_name,
                "code": code,
                "start_line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
            }

        # Also search inside classes (methods)
        method_type = config["method_node"]
        if method_type != func_type:
            query_string = f"""
            ({method_type}
                name: (identifier) @func_name
                (#eq? @func_name "{function_name}")
            ) @target_function
            """
            try:
                query = Query(lang, query_string)
                captures = QueryCursor(query).captures(tree.root_node)
                for node in captures.get("target_function", []):
                    code = source_code[node.start_byte : node.end_byte].decode("utf-8")
                    return {
                        "file": file_path,
                        "function": function_name,
                        "code": code,
                        "start_line": node.start_point[0] + 1,
                        "end_line": node.end_point[0] + 1,
                    }
            except Exception:
                pass

        return {"error": f"Function '{function_name}' not found in {file_path}"}

    def get_skeleton(self, file_path: str) -> str:
        """Return a compact skeleton of a file's top-level structure.

        Output format::

            class ClassName:
              def method_name(...)
            def function_name(...)
        """
        try:
            parser, lang, config = self._get_parser(file_path)
        except ValueError as exc:
            return f"Error: {exc}"

        try:
            source_code = self._read_source(file_path)
        except FileNotFoundError:
            return f"Error: File not found: {file_path}"

        tree = parser.parse(source_code)
        lines: list[str] = []
        func_type = config["function_node"]
        class_type = config["class_node"]
        method_type = config["method_node"]

        for node in tree.root_node.children:
            if node.type == class_type:
                name = self._get_identifier(node)
                lines.append(f"class {name}:")
                # Find methods inside the class body
                for child in node.children:
                    if child.type == "block" or child.type == "class_body":
                        for item in child.children:
                            if item.type in (method_type, func_type):
                                fn_name = self._get_identifier(item)
                                lines.append(f"  def {fn_name}(...)")
            elif node.type == func_type:
                name = self._get_identifier(node)
                lines.append(f"def {name}(...)")

        return "\n".join(lines) if lines else "(empty skeleton)"

    def get_ast_json(self, file_path: str) -> dict:
        """Return a structured JSON representation of the file's nodes.

        Returns::

            {
                "file": "path/to/file.py",
                "language": "python",
                "nodes": [
                    {
                        "type": "function",
                        "name": "foo",
                        "start_line": 1,
                        "end_line": 5,
                        "params": ["a", "b"]
                    },
                    {
                        "type": "class",
                        "name": "Bar",
                        "start_line": 7,
                        "end_line": 20,
                        "methods": ["__init__", "run"]
                    }
                ]
            }
        """
        try:
            parser, lang, config = self._get_parser(file_path)
        except ValueError as exc:
            return {"error": str(exc)}

        try:
            source_code = self._read_source(file_path)
        except FileNotFoundError:
            return {"error": f"File not found: {file_path}"}

        ext = detect_language(file_path)
        lang_name = {".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx"}.get(
            ext, ext
        )

        tree = parser.parse(source_code)
        nodes: list[dict] = []
        func_type = config["function_node"]
        class_type = config["class_node"]
        method_type = config["method_node"]

        for node in tree.root_node.children:
            if node.type == class_type:
                name = self._get_identifier(node)
                methods: list[str] = []
                for child in node.children:
                    if child.type in ("block", "class_body"):
                        for item in child.children:
                            if item.type in (method_type, func_type):
                                methods.append(self._get_identifier(item))
                nodes.append(
                    {
                        "type": "class",
                        "name": name,
                        "start_line": node.start_point[0] + 1,
                        "end_line": node.end_point[0] + 1,
                        "methods": methods,
                    }
                )
            elif node.type == func_type:
                name = self._get_identifier(node)
                params = self._get_params(node, source_code)
                nodes.append(
                    {
                        "type": "function",
                        "name": name,
                        "start_line": node.start_point[0] + 1,
                        "end_line": node.end_point[0] + 1,
                        "params": params,
                    }
                )

        return {"file": file_path, "language": lang_name, "nodes": nodes}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_identifier(node) -> str:
        """Extract the identifier (name) from a definition node."""
        for child in node.children:
            if child.type == "identifier" or child.type == "property_identifier":
                return child.text.decode("utf-8")
        return "?"

    @staticmethod
    def _get_params(func_node, source_code: bytes) -> list[str]:
        """Extract parameter names from a function definition node."""
        params: list[str] = []
        for child in func_node.children:
            if child.type == "parameters" or child.type == "formal_parameters":
                for param in child.children:
                    if param.type == "identifier":
                        params.append(param.text.decode("utf-8"))
                    elif param.type in (
                        "typed_parameter",
                        "default_parameter",
                        "typed_default_parameter",
                    ):
                        # First identifier child is the param name
                        for sub in param.children:
                            if sub.type == "identifier":
                                params.append(sub.text.decode("utf-8"))
                                break
                break
        return params

    def _walk_find(
        self, root, source_code: bytes, target_name: str, file_path: str
    ) -> dict:
        """Fallback: recursively walk the tree to find a named function."""
        for node in root.children:
            if node.type in ("function_definition", "function_declaration", "method_definition"):
                name = self._get_identifier(node)
                if name == target_name:
                    code = source_code[node.start_byte : node.end_byte].decode("utf-8")
                    return {
                        "file": file_path,
                        "function": target_name,
                        "code": code,
                        "start_line": node.start_point[0] + 1,
                        "end_line": node.end_point[0] + 1,
                    }
            # Recurse into child nodes
            result = self._walk_find(node, source_code, target_name, file_path)
            if "error" not in result:
                return result
        return {"error": f"Function '{target_name}' not found in {file_path}"}


# --- Local Testing Block ---
if __name__ == "__main__":
    extractor = ASTExtractor()

    print("=== Supported extensions ===")
    print(supported_extensions())

    print("\n=== Skeleton of dummy_auth.py ===")
    print(extractor.get_skeleton("dummy_auth.py"))

    print("\n=== AST JSON of dummy_auth.py ===")
    import json
    print(json.dumps(extractor.get_ast_json("dummy_auth.py"), indent=2))

    print("\n=== Extract function 'my_auth_logic' ===")
    result = extractor.extract_function_code("dummy_auth.py", "my_auth_logic")
    print(json.dumps(result, indent=2))
