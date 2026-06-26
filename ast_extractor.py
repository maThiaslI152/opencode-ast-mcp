"""Multi-language AST extractor using tree-sitter.

Supports Python, JavaScript, TypeScript, Go, Rust, Java, C/C++,
Ruby, PHP, and LaTeX with graceful fallback when language grammars
are not installed. Provides:
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

# Go
try:
    import tree_sitter_go as tsgo

    _LANGUAGES[".go"] = (
        Language(tsgo.language()),
        {
            "function_node": "function_declaration",
            "class_node": "type_declaration",
            "method_node": "method_declaration",
            "extra_nodes": ["type_spec"],
        },
    )
except ImportError:
    logger.info("tree-sitter-go not installed — .go files unsupported")

# Rust
try:
    import tree_sitter_rust as tsrust

    _LANGUAGES[".rs"] = (
        Language(tsrust.language()),
        {
            "function_node": "function_item",
            "class_node": "struct_item",
            "method_node": "function_item",
            "extra_nodes": ["enum_item", "impl_item", "trait_item", "mod_item"],
        },
    )
except ImportError:
    logger.info("tree-sitter-rust not installed — .rs files unsupported")

# Java
try:
    import tree_sitter_java as tsjava

    _LANGUAGES[".java"] = (
        Language(tsjava.language()),
        {
            "function_node": "method_declaration",
            "class_node": "class_declaration",
            "method_node": "method_declaration",
            "extra_nodes": ["constructor_declaration", "interface_declaration"],
        },
    )
except ImportError:
    logger.info("tree-sitter-java not installed — .java files unsupported")

# C
try:
    import tree_sitter_c as tsc

    _LANGUAGES[".c"] = (
        Language(tsc.language()),
        {
            "function_node": "function_definition",
            "class_node": "struct_specifier",
            "method_node": "function_definition",
            "extra_nodes": [],
        },
    )
except ImportError:
    logger.info("tree-sitter-c not installed — .c files unsupported")

# C++
try:
    import tree_sitter_cpp as tscpp

    _cpp_config = {
        "function_node": "function_definition",
        "class_node": "class_specifier",
        "method_node": "function_definition",
        "extra_nodes": ["struct_specifier", "namespace_definition"],
    }
    _LANGUAGES[".cpp"] = (Language(tscpp.language()), _cpp_config)
    _LANGUAGES[".cc"] = (Language(tscpp.language()), _cpp_config)
    _LANGUAGES[".cxx"] = (Language(tscpp.language()), _cpp_config)
    _LANGUAGES[".hpp"] = (Language(tscpp.language()), _cpp_config)
    _LANGUAGES[".h"] = (Language(tscpp.language()), _cpp_config)
except ImportError:
    logger.info("tree-sitter-cpp not installed — .cpp files unsupported")

# Ruby
try:
    import tree_sitter_ruby as tsruby

    _LANGUAGES[".rb"] = (
        Language(tsruby.language()),
        {
            "function_node": "method",
            "class_node": "class",
            "method_node": "method",
            "extra_nodes": ["module", "singleton_method"],
        },
    )
except ImportError:
    logger.info("tree-sitter-ruby not installed — .rb files unsupported")

# PHP
try:
    import tree_sitter_php as tsphp

    _LANGUAGES[".php"] = (
        Language(tsphp.language_php()),
        {
            "function_node": "function_definition",
            "class_node": "class_declaration",
            "method_node": "method_declaration",
            "extra_nodes": ["interface_declaration", "trait_declaration"],
        },
    )
except ImportError:
    logger.info("tree-sitter-php not installed — .php files unsupported")

# LaTeX (optional — requires tree-sitter-latex from git)
try:
    import tree_sitter_latex as tslatex

    _LANGUAGES[".tex"] = (
        Language(tslatex.language()),
        {
            "function_node": "generic_command",
            "class_node": "generic_environment",
            "method_node": "generic_command",
            "extra_nodes": [
                "section", "subsection", "subsubsection",
                "paragraph", "subparagraph", "chapter", "part",
                "new_command_definition", "old_command_definition",
                "environment_definition", "theorem_definition",
            ],
        },
    )
    _LANGUAGES[".ltx"] = _LANGUAGES[".tex"]
except ImportError:
    logger.info("tree-sitter-latex not installed — .tex/.ltx files unsupported")


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
        ext = detect_language(file_path)
        return self.build_skeleton_from_tree(tree, source_code, ext)

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
        lang_name = {
            ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx",
            ".go": "go", ".rs": "rust", ".java": "java", ".c": "c",
            ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".h": "c",
            ".rb": "ruby", ".php": "php", ".tex": "latex", ".ltx": "latex",
        }.get(ext, ext)

        tree = parser.parse(source_code)
        nodes: list[dict] = []
        func_type = config["function_node"]
        class_type = config["class_node"]
        method_type = config["method_node"]
        extra_types = config.get("extra_nodes", [])

        for node in tree.root_node.children:
            if node.type == class_type:
                name = self._get_identifier(node)
                methods: list[str] = []
                found_body = False
                for child in node.children:
                    if child.type in ("block", "class_body"):
                        found_body = True
                        for item in child.children:
                            if item.type in (method_type, func_type) or item.type in extra_types:
                                methods.append(self._get_identifier(item))
                if not found_body:
                    for child in node.children:
                        if child.type in (method_type, func_type) or child.type in extra_types:
                            methods.append(self._get_identifier(child))
                nodes.append(
                    {
                        "type": "class",
                        "name": name,
                        "start_line": node.start_point[0] + 1,
                        "end_line": node.end_point[0] + 1,
                        "methods": methods,
                    }
                )
            elif node.type == func_type or node.type in extra_types:
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
        """Extract the identifier (name) from a definition node.

        First checks direct children for identifier-typed nodes. If not
        found, recurses into known container types (Go's ``type_spec``,
        C++'s ``function_declarator``, etc.) whose only purpose is to
        wrap the name. Does NOT recurse into return-type containers
        (``qualified_identifier``, ``primitive_type``, etc.).

        Recognised identifier node types across all supported languages:
        ``identifier``, ``property_identifier``, ``type_identifier``,
        ``constant`` (Ruby classes), ``name`` (PHP).

        Returns ``"?"`` if no identifier is found.
        """
        for child in node.children:
            if child.type in (
                "identifier", "property_identifier", "type_identifier",
                "constant", "name",
            ):
                return child.text.decode("utf-8")

        # LaTeX-specific name extraction — commands use command_name and
        # curly groups for their textual identity.
        for child in node.children:
            if child.type == "command_name":
                return child.text.decode("utf-8")
            if child.type == "curly_group_command_name":
                for sub in child.children:
                    if sub.type == "command_name":
                        return sub.text.decode("utf-8")
            if child.type in ("curly_group_text", "curly_group"):
                for sub in child.children:
                    if sub.type == "word":
                        return sub.text.decode("utf-8")
                    if sub.type == "text":
                        for w in sub.children:
                            if w.type == "word":
                                return w.text.decode("utf-8")
            # LaTeX environments nest the name inside begin/end children
            if child.type in ("begin", "end"):
                for sub in child.children:
                    if sub.type == "curly_group_text":
                        for w in sub.children:
                            if w.type == "word":
                                return w.text.decode("utf-8")
                            if w.type == "text":
                                for word in w.children:
                                    if word.type == "word":
                                        return word.text.decode("utf-8")

        # Only recurse into containers that are known name-wrappers.
        _NAME_CONTAINERS = {
            "type_spec", "function_declarator",
            "struct_item", "enum_item",
        }
        for child in node.children:
            if child.type in _NAME_CONTAINERS:
                name = ASTExtractor._get_identifier(child)
                if name != "?":
                    return name
        return "?"

    @staticmethod
    def _walk_identifier_nodes(node):
        """Yield every identifier/property_identifier descendant of *node*.

        Handles the identifier types used across all supported languages:
        - Python/JS/TS/Java: ``identifier``, ``property_identifier``
        - Go/Rust/C/C++:     ``type_identifier``, ``field_identifier``
        - Ruby:              ``constant`` (class names)
        - PHP:               ``name`` (class/function names)
        """
        if node.type in (
            "identifier", "property_identifier", "type_identifier",
            "field_identifier", "constant", "name",
        ):
            yield node
        for child in node.children:
            yield from ASTExtractor._walk_identifier_nodes(child)

    @staticmethod
    def build_skeleton_from_tree(tree, source: bytes, ext: str) -> str:
        """Build the skeleton string from a pre-parsed tree.

        Used by ``codebase_index`` to avoid re-parsing files whose AST is
        already cached. Output format matches :meth:`get_skeleton`.
        """
        if ext not in _LANGUAGES:
            return f"Error: Unsupported extension '{ext}'"
        config = _LANGUAGES[ext][1]
        lines: list[str] = []
        func_type = config["function_node"]
        class_type = config["class_node"]
        method_type = config["method_node"]
        extra_types = config.get("extra_nodes", [])

        for node in tree.root_node.children:
            if node.type == class_type:
                name = ASTExtractor._get_identifier(node)
                lines.append(f"class {name}:")
                found_body = False
                for child in node.children:
                    if child.type in ("block", "class_body"):
                        found_body = True
                        for item in child.children:
                            if item.type in (method_type, func_type) or item.type in extra_types:
                                fn_name = ASTExtractor._get_identifier(item)
                                lines.append(f"  def {fn_name}(...)")
                if not found_body:
                    for child in node.children:
                        if child.type in (method_type, func_type) or child.type in extra_types:
                            fn_name = ASTExtractor._get_identifier(child)
                            lines.append(f"  def {fn_name}(...)")
            elif node.type == func_type or node.type in extra_types:
                name = ASTExtractor._get_identifier(node)
                lines.append(f"def {name}(...)")

        return "\n".join(lines) if lines else "(empty skeleton)"

    @staticmethod
    def find_named_nodes(
        tree, source: bytes, ext: str, name: str
    ) -> list[dict[str, Any]]:
        """Find all top-level functions/classes/methods named *name*.

        Returns a list of dicts with ``name``, ``type`` ("function" /
        "method" / "class"), ``start_line``, ``end_line``. The caller is
        responsible for attaching the file path.

        Recurses into ``export_statement`` and ``lexical_declaration``
        wrappers (JS/TS) so that ``export function foo()`` and
        ``const bar = function() {}`` are discovered.
        """
        if ext not in _LANGUAGES:
            return []
        config = _LANGUAGES[ext][1]
        matches: list[dict[str, Any]] = []
        func_type = config["function_node"]
        class_type = config["class_node"]
        method_type = config["method_node"]
        extra_types = config.get("extra_nodes", [])

        def _visit(node, depth: int = 0) -> None:
            """Walk the top level, recursing into export/lexical wrappers."""
            if node.type == class_type:
                cls_name = ASTExtractor._get_identifier(node)
                if cls_name == name:
                    matches.append(
                        {
                            "name": cls_name,
                            "type": "class",
                            "start_line": node.start_point[0] + 1,
                            "end_line": node.end_point[0] + 1,
                        }
                    )
                found_body = False
                for child in node.children:
                    if child.type in ("block", "class_body"):
                        found_body = True
                        for item in child.children:
                            if item.type in (method_type, func_type) or item.type in extra_types:
                                m_name = ASTExtractor._get_identifier(item)
                                if m_name == name:
                                    matches.append(
                                        {
                                            "name": m_name,
                                            "type": "method",
                                            "start_line": item.start_point[0] + 1,
                                            "end_line": item.end_point[0] + 1,
                                        }
                                    )
                if not found_body:
                    for child in node.children:
                        if child.type in (method_type, func_type) or child.type in extra_types:
                            m_name = ASTExtractor._get_identifier(child)
                            if m_name == name:
                                matches.append(
                                    {
                                        "name": m_name,
                                        "type": "method",
                                        "start_line": child.start_point[0] + 1,
                                        "end_line": child.end_point[0] + 1,
                                    }
                                )
            elif node.type == func_type or node.type in extra_types:
                fn_name = ASTExtractor._get_identifier(node)
                if fn_name == name:
                    matches.append(
                        {
                            "name": fn_name,
                            "type": "function",
                            "start_line": node.start_point[0] + 1,
                            "end_line": node.end_point[0] + 1,
                        }
                    )
            elif node.type in ("export_statement", "lexical_declaration"):
                # JS/TS: recurse into the wrapper to find the function/class
                # it contains.
                for child in node.children:
                    _visit(child, depth + 1)

        for child in tree.root_node.children:
            _visit(child)
        return matches

    @staticmethod
    def find_identifier_references(
        tree, source: bytes, ext: str, name: str
    ) -> list[dict[str, Any]]:
        """Find all identifier nodes whose text matches *name*.

        Returns a list of dicts with ``line`` and ``context`` (the full
        source line). The caller is responsible for attaching the file
        path. Unlike ``grep``, this skips string literals and comments
        because those aren't ``identifier`` nodes in the tree-sitter AST.
        """
        matches: list[dict[str, Any]] = []
        for ident in ASTExtractor._walk_identifier_nodes(tree.root_node):
            text = source[ident.start_byte : ident.end_byte].decode(
                "utf-8", errors="replace"
            )
            if text != name:
                continue
            line_start = source.rfind(b"\n", 0, ident.start_byte) + 1
            line_end = source.find(b"\n", ident.end_byte)
            if line_end == -1:
                line_end = len(source)
            context = source[line_start:line_end].decode("utf-8", errors="replace")
            matches.append(
                {
                    "line": ident.start_point[0] + 1,
                    "context": context,
                }
            )
        return matches

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
