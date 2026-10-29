"""Allow-list safety checker for generated ``user_logic.cpp``.

The source is parsed with tree-sitter-cpp and walked; every named node type that is not
explicitly allowed is a violation. The function is pure (no I/O) and collects all violations.

Parse errors and size-limit failures return early: the tree is not reliable then, and the
model must fix those first.
"""

import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field

import tree_sitter_cpp
from pydantic import BaseModel
from tree_sitter import Language, Node, Parser, Tree

from firmloop.hardware.registry import Registry

MAX_LINES = 300
MAX_BYTES = 12_000
MAX_ARRAY = 64
MAX_LOOP_ITERATIONS = 64
MAX_DEPTH = 120

_PARSER = Parser(Language(tree_sitter_cpp.language()))
_PARSER_LOCK = threading.Lock()  # a tree-sitter Parser must not be shared between threads


class Violation(BaseModel):
    line: int
    rule: str
    message: str


class SafetyReport(BaseModel):
    ok: bool
    violations: list[Violation]


# --- tables ----------------------------------------------------------------------------------

INCLUDE_TEXT = '"FirmLoop.h"'
VAR_TYPES = frozenset(
    {"bool", "int", "float", "int8_t", "uint8_t", "int16_t", "uint16_t", "int32_t", "uint32_t"}
)
RETURN_TYPES = frozenset({"void", "bool", "int32_t", "uint32_t", "uint8_t", "int", "float"})
LOOP_TYPE_RANGES: dict[str, tuple[int, int]] = {
    "int": (-(2**31), 2**31 - 1),
    "int8_t": (-(2**7), 2**7 - 1),
    "uint8_t": (0, 2**8 - 1),
    "int16_t": (-(2**15), 2**15 - 1),
    "uint16_t": (0, 2**16 - 1),
    "int32_t": (-(2**31), 2**31 - 1),
    "uint32_t": (0, 2**32 - 1),
}
FL_CALLS = frozenset(
    {
        "fl::readFloat",
        "fl::readInt",
        "fl::readBool",
        "fl::setBool",
        "fl::displayLine",
        "fl::displayClear",
        "fl::now",
    }
)
LIBC_CALLS = frozenset(
    {"snprintf", "strcmp", "strncmp", "fabsf", "roundf", "floorf", "ceilf", "fminf", "fmaxf"}
)
# call -> (expected channel kind, expected channel type)
CHANNEL_CALLS: dict[str, tuple[str, str]] = {
    "fl::readFloat": ("sensor", "float"),
    "fl::readInt": ("sensor", "int"),
    "fl::readBool": ("sensor", "bool"),
    "fl::setBool": ("actuator", "bool"),
}
READ_FUNCTION_FOR_TYPE = {"float": "fl::readFloat", "int": "fl::readInt", "bool": "fl::readBool"}

# Node types passed through: their children are visited.
PASS_THROUGH = frozenset(
    {
        "compound_statement",
        "expression_statement",
        "if_statement",
        "else_clause",
        "switch_statement",
        "case_statement",
        "break_statement",
        "continue_statement",
        "return_statement",
        "condition_clause",
        "parenthesized_expression",
        "binary_expression",
        "unary_expression",
        "update_expression",
        "assignment_expression",
        "conditional_expression",
        "argument_list",
        "number_literal",
        "true",
        "false",
        "qualified_identifier",
        "namespace_identifier",
    }
)

CALL_HINTS: dict[str, str] = {
    "millis": "use now_ms (the user_tick parameter) or fl::now() instead of millis()",
    "micros": "use now_ms or fl::now(); microsecond timing is not available",
    "delay": (
        "never block; user_tick runs every 50 ms. Keep state in static variables and compare "
        "now_ms with a stored deadline instead of delay()"
    ),
    "delayMicroseconds": "never block; compare now_ms with a stored deadline instead",
    "digitalWrite": 'do not touch pins; use fl::setBool("<actuator channel>", value)',
    "digitalRead": 'do not touch pins; use fl::readBool("<sensor channel>")',
    "analogRead": 'do not touch pins; use fl::readInt("<sensor channel>")',
    "analogWrite": 'do not touch pins; use fl::setBool("<actuator channel>", value)',
    "pinMode": "pins are configured by the drivers; remove pinMode()",
    "exit": "do not terminate the program; user_tick must simply return",
    "abort": "do not terminate the program; user_tick must simply return",
    "malloc": "dynamic memory is not allowed; use static variables",
    "calloc": "dynamic memory is not allowed; use static variables",
    "free": "dynamic memory is not allowed; use static variables",
    "system": "running commands is not allowed",
    "printf": "console output is not available; show text with fl::displayLine(row, text)",
    "puts": "console output is not available; show text with fl::displayLine(row, text)",
    "sprintf": "use snprintf(buf, sizeof buf, fmt, ...) with a char buffer",
    "strcpy": 'use snprintf(buf, sizeof buf, "%s", ...) instead of strcpy',
    "memcpy": "raw memory access is not allowed",
    "memset": "raw memory access is not allowed",
}
CALL_GENERIC = (
    "only these calls are allowed: fl::readFloat, fl::readInt, fl::readBool, fl::setBool, "
    "fl::displayLine, fl::displayClear, fl::now, snprintf, strcmp, strncmp, fabsf, roundf, "
    "floorf, ceilf, fminf, fmaxf, and static functions defined in this file"
)
LOOP_HINT = (
    "user_tick runs every 50 ms; keep progress in static variables and do one step per tick. "
    "For fixed repetition use a for loop with literal bounds (at most 64 iterations)"
)
# node type -> (rule, message)
NODE_HINTS: dict[str, tuple[str, str]] = {
    "while_statement": ("statement", "while loops are not allowed. " + LOOP_HINT),
    "do_statement": ("statement", "do-while loops are not allowed. " + LOOP_HINT),
    "for_range_loop": ("statement", "range-based for is not allowed. " + LOOP_HINT),
    "goto_statement": ("statement", "goto is not allowed; use if/else, switch or return"),
    "labeled_statement": ("statement", "labels are not allowed; use if/else, switch or return"),
    "try_statement": (
        "statement",
        "try/catch is not allowed (no exceptions); check values with if",
    ),
    "throw_statement": ("statement", "throw is not allowed (no exceptions); return instead"),
    "gnu_asm_expression": ("statement", "inline assembly is not allowed"),
    "subscript_expression": (
        "array",
        "array subscripts are not allowed. Use plain variables; char buffers may only be passed to "
        "snprintf (first argument) and fl::displayLine (second argument)",
    ),
    "pointer_expression": (
        "pointer",
        "unary * and & are not allowed: no pointers or raw memory access. Use plain variables and "
        "the fl:: functions",
    ),
    "pointer_declarator": ("pointer", "pointer declarations are not allowed; use plain values"),
    "reference_declarator": ("pointer", "references are not allowed; use plain values"),
    "cast_expression": (
        "pointer",
        "C-style casts are not allowed. Use implicit conversion or arithmetic (e.g. x * 1.0f)",
    ),
    "new_expression": ("pointer", "new is not allowed; use static variables"),
    "delete_expression": ("pointer", "delete is not allowed; use static variables"),
    "field_expression": (
        "pointer",
        "member access (. and ->) is not allowed; there are no structs or objects. Use plain "
        "variables and the fl:: functions",
    ),
    "struct_specifier": ("type", "struct is not allowed; use separate static variables"),
    "union_specifier": ("type", "union is not allowed; use separate static variables"),
    "enum_specifier": ("type", "enum is not allowed; use static const int constants"),
    "class_specifier": ("type", "class is not allowed; use static variables and functions"),
    "type_definition": ("type", "typedef is not allowed; use the types directly"),
    "alias_declaration": ("type", "using aliases are not allowed; use the types directly"),
    "using_declaration": ("type", "using is not allowed; use fully spelled names"),
    "template_declaration": ("type", "templates are not allowed"),
    "lambda_expression": ("statement", "lambdas are not allowed; use a static function"),
    "comma_expression": ("statement", "the comma operator is not allowed; use separate statements"),
    "initializer_list": (
        "statement",
        "brace initializers are not allowed; assign values separately",
    ),
}

_INT_LITERAL = re.compile(r"^-?(0[xX][0-9a-fA-F]+|[0-9]+)[uU]?$")
_CONVERSION = re.compile(r"%[-+ 0#]*[0-9]{0,2}(\.[0-9]{1,2})?(l[diuxX]|[diuxXfcs])")
_FORMAT_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}
ATTRIBUTE_NODES = frozenset(
    {"attribute_specifier", "attribute_declaration", "ms_declspec_modifier"}
)
MAX_WORK = 20_000  # static upper bound of work units per entry point
ENTRY_NAMES = frozenset({"user_setup", "user_tick"})


def _text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def _line(node: Node) -> int:
    return node.start_point.row + 1


def _named_children(node: Node) -> list[Node]:
    return [c for c in node.children if c.is_named and c.type != "comment"]


def _unwrap_parens(node: Node | None) -> Node | None:
    while node is not None and node.type == "parenthesized_expression":
        inner = _named_children(node)
        if len(inner) != 1:
            return node
        node = inner[0]
    return node


def _int_literal(node: Node | None) -> int | None:
    """Value of an integer literal (optionally negated), else None."""
    node = _unwrap_parens(node)
    if node is None:
        return None
    if node.type == "number_literal":
        text = _text(node)
        if _INT_LITERAL.match(text):
            try:
                return int(text.rstrip("uU"), 0)
            except ValueError:  # e.g. 010 (octal spelling): reject rather than guess
                return None
        return None
    if node.type == "unary_expression" and node.children and _text(node.children[0]) == "-":
        inner = _int_literal(node.children[-1])
        return None if inner is None else -inner
    return None


def _walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


@dataclass
class _Func:
    name: str
    line: int
    ret: str
    params: list[str]
    calls: set[str] = field(default_factory=lambda: set[str]())
    body: Node | None = None
    static: bool = False


@dataclass
class _Sym:
    buffer: bool
    size: int = 0


@dataclass
class _Proto:
    name: str
    line: int
    ret: str
    params: list[str]


@dataclass
class _Checker:
    registry: Registry
    violations: list[Violation] = field(default_factory=lambda: list[Violation]())
    funcs: list[_Func] = field(default_factory=lambda: list[_Func]())
    defined: set[str] = field(default_factory=lambda: set[str]())
    scopes: list[dict[str, _Sym]] = field(default_factory=lambda: [{}])
    protos: list[_Proto] = field(default_factory=lambda: list[_Proto]())
    loop_counts: dict[int, int] = field(default_factory=lambda: dict[int, int]())
    current: _Func | None = None

    # -- helpers --------------------------------------------------------------------------

    def add(self, line: int, rule: str, message: str) -> None:
        self.violations.append(Violation(line=line, rule=rule, message=message))

    def lookup(self, name: str) -> _Sym | None:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    def declare(self, name: str, node: Node, sym: _Sym) -> None:
        if self.lookup(name) is not None:
            self.add(
                _line(node),
                "scope",
                f"'{name}' is already declared in an enclosing scope; shadowing is not allowed, "
                "use a different name",
            )
            return
        self.scopes[-1][name] = sym

    def buffer(self, node: Node | None) -> _Sym | None:
        node = _unwrap_parens(node)
        if node is None or node.type != "identifier":
            return None
        sym = self.lookup(_text(node))
        return sym if sym is not None and sym.buffer else None

    def flag(self, node: Node) -> None:
        rule, message = NODE_HINTS.get(
            node.type,
            (
                "statement",
                f"'{node.type}' is not allowed in generated code; use plain C-style statements "
                "(if/else, switch, bounded for, return) and expressions on int/float/bool "
                "variables",
            ),
        )
        self.add(_line(node), rule, message)

    # -- program --------------------------------------------------------------------------

    def run(self, root: Node) -> None:
        for child in root.children:
            if child.type == "function_definition":
                name = self._function_name(child)
                if name:
                    self.defined.add(name)
        for n in _walk(root):
            if n.type in ATTRIBUTE_NODES or n.type.startswith("gnu_asm"):
                self.add(
                    _line(n),
                    "attribute",
                    "attributes and inline assembly are not allowed; remove them",
                )
        includes = 0
        for child in root.children:
            if not child.is_named or child.type == "comment":
                continue
            if child.type == "preproc_include":
                includes += 1
                if _text(child.child_by_field_name("path")) != INCLUDE_TEXT or includes > 1:
                    self.add(
                        _line(child),
                        "preprocessor",
                        'the only allowed directive is a single #include "FirmLoop.h"; '
                        "remove this one",
                    )
            elif child.type.startswith("preproc") or child.type == "linkage_specification":
                self.add(
                    _line(child),
                    "preprocessor",
                    'the only allowed directive is a single #include "FirmLoop.h"; remove this '
                    "directive and use static const variables instead of #define",
                )
            elif child.type == "function_definition":
                self.function(child)
            elif child.type == "declaration":
                self.declaration(child, top_level=True, depth=0)
            elif child.type in NODE_HINTS:
                self.flag(child)
            else:
                self.add(
                    _line(child),
                    "top_level",
                    "only static variables, static functions, user_setup and user_tick are "
                    "allowed at file scope",
                )
        if includes == 0:
            self.add(1, "preprocessor", 'the file must start with #include "FirmLoop.h"')
        self.prototypes()
        self.entry_points()
        self.overloads()
        self.recursion()
        if not self.violations:
            self.work_budget()

    def entry_points(self) -> None:
        for name, params in (("user_setup", []), ("user_tick", ["uint32_t"])):
            found = [f for f in self.funcs if f.name == name]
            signature = (
                "void user_setup()" if name == "user_setup" else "void user_tick(uint32_t now_ms)"
            )
            if not found:
                self.add(1, "entry_point", f"the file must define exactly one `{signature}`")
                continue
            for extra in found[1:]:
                self.add(extra.line, "entry_point", f"{name} is defined more than once; keep one")
            first = found[0]
            if first.ret != "void" or first.params != params:
                self.add(
                    first.line, "entry_point", f"{name} must be declared exactly as `{signature}`"
                )

    def prototypes(self) -> None:
        for proto in self.protos:
            defs = [f for f in self.funcs if f.name == proto.name]
            if not defs:
                self.add(
                    proto.line,
                    "top_level",
                    f"'{proto.name}' is declared but never defined in this file; "
                    "define it or remove the declaration",
                )
            elif not any(f.ret == proto.ret and f.params == proto.params for f in defs):
                self.add(
                    proto.line,
                    "top_level",
                    f"declaration of '{proto.name}' does not match its definition "
                    "(return type and parameter types must be identical)",
                )

    def overloads(self) -> None:
        seen: set[str] = set()
        for f in self.funcs:
            if f.name in seen and f.name not in ENTRY_NAMES:
                self.add(
                    f.line,
                    "top_level",
                    f"'{f.name}' is defined more than once; overloading is not allowed, "
                    "give each function a unique name",
                )
            seen.add(f.name)

    def work_budget(self) -> None:
        by_name = {f.name: f for f in self.funcs}
        memo: dict[str, int] = {}

        def cost(node: Node, active: frozenset[str]) -> int:
            if node.type == "for_statement":
                body = node.child_by_field_name("body")
                inner = cost(body, active) if body is not None else 0
                return self.loop_counts.get(node.id, MAX_LOOP_ITERATIONS) * (1 + inner)
            total = 0
            if node.type == "call_expression":
                total = 1
                callee = self.callee_name(node.child_by_field_name("function"))
                if callee in by_name and callee not in active:
                    total += fn_cost(callee, active)
            for c in node.children:
                if c.is_named:
                    total += cost(c, active)
            return total

        def fn_cost(name: str, active: frozenset[str]) -> int:
            if name not in memo:
                body = by_name[name].body
                memo[name] = cost(body, active | {name}) if body is not None else 0
            return memo[name]

        for entry in ("user_setup", "user_tick"):
            if entry in by_name and fn_cost(entry, frozenset()) > MAX_WORK:
                self.add(
                    by_name[entry].line,
                    "work_budget",
                    f"{entry} can do too much work per call (static bound over {MAX_WORK} units): "
                    "reduce nested loops and repeated calls to helpers that contain loops",
                )

    def recursion(self) -> None:
        graph = {f.name: f.calls for f in self.funcs}
        for func in self.funcs:
            seen: set[str] = set()
            stack = list(func.calls)
            while stack:
                callee = stack.pop()
                if callee == func.name:
                    self.add(
                        func.line,
                        "recursion",
                        f"{func.name} is part of a recursive call cycle; recursion is not "
                        "allowed. Use a bounded for loop or per-tick state instead",
                    )
                    break
                if callee not in seen:
                    seen.add(callee)
                    stack.extend(graph.get(callee, ()))

    # -- functions and declarations -----------------------------------------------------------

    def _function_name(self, fn: Node) -> str | None:
        declarator = fn.child_by_field_name("declarator")
        while declarator is not None and declarator.type in (
            "pointer_declarator",
            "reference_declarator",
        ):
            declarator = declarator.child_by_field_name("declarator")
        if declarator is None or declarator.type != "function_declarator":
            return None
        name = declarator.child_by_field_name("declarator")
        return _text(name) if name is not None and name.type == "identifier" else None

    def function(self, fn: Node) -> None:
        type_node = fn.child_by_field_name("type")
        declarator = fn.child_by_field_name("declarator")
        body = fn.child_by_field_name("body")
        ret = _text(type_node)
        self.specifiers(fn, type_node)
        self.check_type(type_node, RETURN_TYPES, "function return type")
        func_decl = declarator
        while func_decl is not None and func_decl.type in (
            "pointer_declarator",
            "reference_declarator",
        ):
            self.flag(func_decl)
            func_decl = func_decl.child_by_field_name("declarator")
        name = self._function_name(fn)
        if func_decl is None or func_decl.type != "function_declarator" or name is None:
            self.add(_line(fn), "top_level", "unsupported function definition")
            return
        self.scopes.append({})
        params = self.parameters(func_decl.child_by_field_name("parameters"), declare=True)
        has_static = any(
            c.type == "storage_class_specifier" and _text(c) == "static" for c in fn.children
        )
        func = _Func(
            name=name, line=_line(fn), ret=ret, params=params, body=body, static=has_static
        )
        self.funcs.append(func)
        self.linkage(name, has_static, _line(fn))
        if ret == "char":
            self.add(_line(fn), "type", "char is not allowed as a return type; use int8_t/uint8_t")
        if any(t == "char" for t in params):
            self.add(
                _line(fn), "type", "char is not allowed as a parameter type; use int8_t/uint8_t"
            )
        for child in fn.children:
            if (
                child.is_named
                and child.type
                not in (
                    "storage_class_specifier",
                    "type_qualifier",
                    "function_declarator",
                    "pointer_declarator",
                    "reference_declarator",
                    "comment",
                    "compound_statement",
                )
                and child.id != (type_node.id if type_node is not None else -1)
            ):
                self.flag(child)
        if body is not None:
            self.current = func
            self.visit(body, 0)
            self.current = None
        self.scopes.pop()

    def linkage(self, name: str, has_static: bool, line: int) -> None:
        if name in ENTRY_NAMES:
            if has_static:
                self.add(line, "entry_point", f"{name} must not be static")
        elif not has_static:
            self.add(
                line,
                "top_level",
                f"helper function '{name}' must be declared `static` "
                "(only user_setup and user_tick are non-static)",
            )

    def parameters(self, plist: Node | None, *, declare: bool = False) -> list[str]:
        types: list[str] = []
        if plist is None:
            return types
        for p in _named_children(plist):
            if p.type != "parameter_declaration":
                self.flag(p)
                continue
            type_node = p.child_by_field_name("type")
            declarator = p.child_by_field_name("declarator")
            if (
                declarator is None
                and _text(type_node) == "void"
                and len(_named_children(plist)) == 1
            ):
                continue  # f(void)
            self.specifiers(p, type_node)
            self.check_type(type_node, VAR_TYPES, "parameter type")
            types.append(_text(type_node))
            if declarator is not None and declarator.type != "identifier":
                self.flag(declarator)
            elif declarator is not None and declare:
                self.declare(_text(declarator), declarator, _Sym(buffer=False))
        return types

    def specifiers(self, node: Node, type_node: Node | None) -> None:
        for child in node.children:
            if child.type == "storage_class_specifier" and _text(child) != "static":
                self.add(
                    _line(child),
                    "type",
                    f"storage class '{_text(child)}' is not allowed; only `static` is allowed",
                )
            elif child.type == "type_qualifier" and _text(child) not in ("const", "constexpr"):
                self.add(
                    _line(child),
                    "type",
                    f"qualifier '{_text(child)}' is not allowed; only const and constexpr",
                )

    def check_type(self, type_node: Node | None, allowed: frozenset[str], what: str) -> None:
        if type_node is None:
            return
        text = _text(type_node)
        if type_node.type in ("primitive_type", "type_identifier") and text in allowed:
            return
        if text == "char" and type_node.type == "primitive_type":
            return  # char is vetted per declarator (array buffers only)
        allowed_list = ", ".join(sorted(allowed))
        hint = (
            f"use only these types: {allowed_list} (char only for `char name[N]` text buffers). "
            "Use float instead of double, int32_t instead of long, plain variables instead of "
            "String or structs"
        )
        if type_node.type in NODE_HINTS:
            self.flag(type_node)
        else:
            self.add(_line(type_node), "type", f"{what} '{text}' is not allowed; {hint}")

    def declaration(self, node: Node, *, top_level: bool, depth: int) -> None:
        type_node = node.child_by_field_name("type")
        self.specifiers(node, type_node)
        declarators = node.children_by_field_name("declarator")
        is_prototype = any(d.type == "function_declarator" for d in declarators)
        self.check_type(type_node, RETURN_TYPES if is_prototype else VAR_TYPES, "type")
        is_char = _text(type_node) == "char"
        if is_prototype and is_char:
            self.add(_line(node), "type", "char is not allowed as a return type")
        has_static = any(
            c.type == "storage_class_specifier" and _text(c) == "static" for c in node.children
        )
        if is_prototype and top_level:
            for d in declarators:
                inner = d.child_by_field_name("declarator")
                if d.type == "function_declarator" and inner is not None:
                    self.linkage(_text(inner), has_static, _line(node))
        elif top_level and not has_static:
            self.add(
                _line(node),
                "top_level",
                "file-scope variables must be `static` (e.g. `static bool fanOn = false;`)",
            )
        for d in declarators:
            self.declarator(
                d, is_char=is_char, top_level=top_level, depth=depth, ret=_text(type_node)
            )
            if top_level and d.type != "function_declarator":
                for n in _walk(d):
                    if n.type == "call_expression":
                        self.add(
                            _line(n),
                            "top_level",
                            "file-scope initializers must be constants; calls are only allowed "
                            "inside functions",
                        )
        for child in node.children:
            if (
                child.is_named
                and child.type
                not in (
                    "storage_class_specifier",
                    "type_qualifier",
                    "comment",
                    "primitive_type",
                    "type_identifier",
                )
                and child.id not in {d.id for d in declarators}
                and (type_node is None or child.id != type_node.id)
            ):
                self.flag(child)

    def declarator(
        self,
        d: Node,
        *,
        is_char: bool,
        top_level: bool,
        depth: int,
        ret: str = "",
        init: Node | None = None,
    ) -> None:
        if d.type == "identifier":
            if is_char:
                self.add(
                    _line(d),
                    "type",
                    "char is only allowed as a buffer `char name[N]` (N <= 64); use "
                    "int8_t/uint8_t for numbers",
                )
            self.declare(_text(d), d, _Sym(buffer=False))
        elif d.type == "init_declarator":
            inner = d.child_by_field_name("declarator")
            value = d.child_by_field_name("value")
            if inner is not None:
                self.declarator(
                    inner, is_char=is_char, top_level=top_level, depth=depth, init=value
                )
            if value is not None and (inner is None or inner.type != "array_declarator"):
                self.visit(value, depth + 1)
        elif d.type == "array_declarator":
            self.array_declarator(d, is_char=is_char, init=init)
        elif d.type == "function_declarator" and top_level:
            name_node = d.child_by_field_name("declarator")
            params = self.parameters(d.child_by_field_name("parameters"))
            if "char" in params:
                self.add(_line(d), "type", "char is not allowed as a parameter type")
            if name_node is not None:
                self.protos.append(
                    _Proto(name=_text(name_node), line=_line(d), ret=ret, params=params)
                )
        else:
            self.flag(d)

    def array_declarator(self, d: Node, *, is_char: bool, init: Node | None) -> None:
        size = d.child_by_field_name("size")
        inner = d.child_by_field_name("declarator")
        count = _int_literal(size)
        capacity = 0
        if not is_char:
            self.add(
                _line(d),
                "array",
                "only `char name[N]` text buffers are allowed as arrays; use separate "
                "variables instead",
            )
        elif inner is None or inner.type != "identifier":
            self.add(_line(d), "array", "multi-dimensional arrays are not allowed")
        elif count is None or not 1 <= count <= MAX_ARRAY:
            self.add(
                _line(d),
                "array",
                f"array size must be an integer literal between 1 and {MAX_ARRAY}",
            )
        else:
            capacity = count
            if not self.valid_buffer_init(init, count):
                self.add(
                    _line(d),
                    "array",
                    f"every char buffer must be initialized, e.g. "
                    f'`char {_text(inner)}[{count}] = "";` '
                    "(or `= {0}`); a string initializer must be shorter than the buffer",
                )
        if inner is not None and inner.type == "identifier":
            self.declare(_text(inner), inner, _Sym(buffer=True, size=capacity))

    @staticmethod
    def valid_buffer_init(init: Node | None, count: int) -> bool:
        if init is None:
            return False
        if init.type == "string_literal":
            raw = _text(init)
            if not raw.startswith('"') or "\\" in raw:
                return False
            return len(raw[1:-1].encode("utf-8")) < count  # bytes, plus the NUL
        if init.type == "initializer_list":
            items = _named_children(init)
            return len(items) == 1 and items[0].type == "number_literal" and _text(items[0]) == "0"
        return False

    # -- statements and expressions -----------------------------------------------------------

    def visit(self, node: Node, depth: int) -> None:
        if not node.is_named or node.type == "comment":
            return
        if depth > MAX_DEPTH:
            self.add(_line(node), "statement", "code is nested too deeply; simplify it")
            return
        t = node.type
        nxt = depth + 1
        if t == "char_literal":
            return  # 'a', '\\n': no children worth checking
        if t == "compound_statement":
            self.scopes.append({})
            for c in node.children:
                self.visit(c, nxt)
            self.scopes.pop()
        elif t in PASS_THROUGH:
            for c in node.children:
                self.visit(c, nxt)
        elif t == "declaration":
            self.declaration(node, top_level=False, depth=nxt)
        elif t == "for_statement":
            self.for_loop(node, nxt)
        elif t == "call_expression":
            self.call(node, nxt)
        elif t == "identifier":
            sym = self.lookup(_text(node))
            if sym is None:
                self.add(
                    _line(node),
                    "scope",
                    f"'{_text(node)}' is not declared in this scope; declare it before use",
                )
            elif sym.buffer:
                self.add(
                    _line(node),
                    "array",
                    f"char buffer '{_text(node)}' may only be used as the first argument of "
                    "snprintf, a %s argument of snprintf, or the second argument of "
                    "fl::displayLine",
                )
        elif t == "string_literal":
            self.string_literal(node)
        elif t == "sizeof_expression":
            operand = _unwrap_parens(_named_children(node)[-1] if _named_children(node) else None)
            if self.buffer(operand) is None:
                self.add(
                    _line(node),
                    "sizeof",
                    "sizeof is only allowed on a local char buffer, e.g. "
                    "snprintf(buf, sizeof buf, ...)",
                )
        elif t.startswith("preproc"):
            self.add(_line(node), "preprocessor", "preprocessor directives are not allowed here")
        else:
            self.flag(node)

    def string_literal(self, node: Node) -> None:
        parent = node.parent
        if parent is None or parent.type != "argument_list":
            self.add(
                _line(node),
                "string",
                "string literals are only allowed directly as call arguments "
                '(e.g. fl::readFloat("temp") or snprintf format)',
            )

    def callee_name(self, fn: Node | None) -> str | None:
        if fn is None:
            return None
        if fn.type == "identifier":
            return _text(fn)
        if fn.type == "qualified_identifier":
            return re.sub(r"\s+", "", _text(fn))
        return None

    def call(self, node: Node, depth: int) -> None:
        fn = node.child_by_field_name("function")
        args_node = node.child_by_field_name("arguments")
        args = _named_children(args_node) if args_node is not None else []
        name = self.callee_name(fn)
        line = _line(node)
        array_ok: int | None = None  # index of the argument allowed to be a char buffer
        if name is None:
            text = _text(fn)
            if re.match(r"^(static|reinterpret|const|dynamic)_cast\b", text):
                self.add(line, "pointer", "C++ casts are not allowed; use implicit conversion")
            elif text in ("asm", "__asm__", "__asm"):
                self.add(line, "statement", "inline assembly is not allowed")
            else:
                self.add(
                    line,
                    "call",
                    f"call target '{text}' is not allowed (no objects, members or function "
                    f"pointers; Serial is unavailable). {CALL_GENERIC}",
                )
        elif name in FL_CALLS or name in LIBC_CALLS:
            if name in CHANNEL_CALLS:
                self.channel(name, args, line)
            if name == "snprintf":
                array_ok = 0
                self.snprintf(args, line)
            elif name == "fl::displayLine":
                array_ok = 1
                if len(args) >= 2 and not self.is_text(args[1]):
                    self.add(
                        line,
                        "array",
                        "the second argument of fl::displayLine must be a char buffer or a "
                        "string literal",
                    )
        elif name in self.defined:
            if self.current is not None:
                self.current.calls.add(name)
        else:
            hint = CALL_HINTS.get(name)
            message = f"call to '{name}' is not allowed: " + (hint or CALL_GENERIC)
            self.add(line, "call", message)
        for i, arg in enumerate(args):
            if i == array_ok and self.buffer(arg) is not None:
                continue
            if (name == "snprintf" and i >= 3) or (name == "fl::displayLine" and i == 1):
                if self.is_text(arg):
                    self.visit_text(arg, depth)  # validated by the format / text rules
                    continue
            self.visit(arg, depth)

    def is_text(self, node: Node | None) -> bool:
        """String literal, ternary of two string literals, or a char buffer."""
        node = _unwrap_parens(node)
        if node is None:
            return False
        if node.type == "string_literal" or self.buffer(node) is not None:
            return True
        if node.type == "conditional_expression":
            a = _unwrap_parens(node.child_by_field_name("consequence"))
            b = _unwrap_parens(node.child_by_field_name("alternative"))
            return (
                a is not None
                and b is not None
                and a.type == "string_literal"
                and b.type == "string_literal"
            )
        return False

    def visit_text(self, node: Node, depth: int) -> None:
        inner = _unwrap_parens(node)
        if inner is not None and inner.type == "conditional_expression":
            cond = inner.child_by_field_name("condition")
            if cond is not None:
                self.visit(cond, depth)

    def snprintf(self, args: list[Node], line: int) -> None:
        if len(args) < 3:
            self.add(
                line,
                "format",
                "snprintf needs (buffer, size, format, ...): snprintf(buf, sizeof buf, fmt, ...)",
            )
            return
        dest, size, fmt = args[0], args[1], args[2]
        sym = self.buffer(dest)
        dest_name = _text(dest)
        if sym is None:
            self.add(line, "array", "the first argument of snprintf must be a char buffer variable")
        elif not self.size_ok(size, dest_name, sym.size):
            self.add(
                line,
                "array",
                f"the size argument of snprintf must be `sizeof {dest_name}`, "
                f"`sizeof {dest_name} - k`, or an integer literal not larger than the "
                f"buffer ({sym.size})",
            )
        text = _text(fmt)
        if fmt.type != "string_literal" or not text.startswith('"'):
            self.add(
                _line(fmt),
                "format",
                'the snprintf format must be a plain string literal, e.g. "T=%.1f"',
            )
            return
        body = self.decode_format(text[1:-1])
        if body is None:
            self.add(
                _line(fmt),
                "format",
                'only the escapes \\n, \\t, \\\\ and \\" are allowed in a format string',
            )
            return
        convs: list[str] = []
        i = 0
        while i < len(body):
            if body[i] != "%":
                i += 1
                continue
            if body.startswith("%%", i):
                i += 2
                continue
            m = _CONVERSION.match(body, i)
            if m is None:
                self.add(
                    _line(fmt),
                    "format",
                    "unsupported conversion in the snprintf format; allowed: %d %i %u %x %X %f %c "
                    "%s (optional flags, up to 2-digit width, .precision, l only with d i u x X) "
                    "and %%",
                )
                return
            convs.append(m.group(0)[-1])
            i = m.end()
        values = args[3:]
        if len(values) != len(convs):
            self.add(
                _line(fmt),
                "format",
                f"the format has {len(convs)} conversion(s) but {len(values)} argument(s) follow",
            )
            return
        for conv, value in zip(convs, values, strict=True):
            value_buffer = self.buffer(value)
            ok = (
                self.is_text(value)
                and not (
                    value_buffer is not None and _text(_unwrap_parens(value) or value) == dest_name
                )
                if conv == "s"
                else not self.is_text(value)
            )
            if not ok:
                self.add(
                    _line(value),
                    "format",
                    "%s needs a string literal, an ON/OFF-style ternary of two literals, or a "
                    "different char buffer; other conversions must not be given text",
                )

    @staticmethod
    def decode_format(raw: str) -> str | None:
        out: list[str] = []
        i = 0
        while i < len(raw):
            ch = raw[i]
            if ch != "\\":
                out.append(ch)
                i += 1
                continue
            decoded = _FORMAT_ESCAPES.get(raw[i + 1 : i + 2])
            if decoded is None:
                return None
            out.append(decoded)
            i += 2
        return "".join(out)

    def size_ok(self, size: Node, dest_name: str, capacity: int) -> bool:
        size = _unwrap_parens(size) or size
        literal = _int_literal(size)
        if literal is not None:
            return 0 <= literal <= capacity

        def is_sizeof_dest(n: Node | None) -> bool:
            n = _unwrap_parens(n)
            if n is None or n.type != "sizeof_expression":
                return False
            kids = _named_children(n)
            operand = _unwrap_parens(kids[-1]) if kids else None
            return (
                operand is not None and operand.type == "identifier" and _text(operand) == dest_name
            )

        if is_sizeof_dest(size):
            return True
        if size.type == "binary_expression" and _text(size.child_by_field_name("operator")) == "-":
            k = _int_literal(size.child_by_field_name("right"))
            return (
                is_sizeof_dest(size.child_by_field_name("left"))
                and k is not None
                and 0 <= k <= capacity
            )
        return False

    def channel(self, name: str, args: list[Node], line: int) -> None:
        kind, ctype = CHANNEL_CALLS[name]
        known = ", ".join(sorted(self.registry.channels))
        first = args[0] if args else None
        if first is None or first.type != "string_literal":
            self.add(
                line,
                "channel",
                f"the first argument of {name} must be a string literal channel id, "
                f'e.g. {name}("temp"). '
                f"Known channels: {known}",
            )
            return
        raw = _text(first)
        cid = raw[1:-1] if raw.startswith('"') and raw.endswith('"') else raw
        ch = self.registry.channels.get(cid)
        if ch is None:
            self.add(
                _line(first),
                "channel",
                f"unknown channel '{cid}'. Known channels: {known}",
            )
        elif ch.kind != kind:
            if ch.kind == "actuator":
                use = f'fl::setBool("{cid}", value)'
            elif ch.kind == "sensor":
                use = f'{READ_FUNCTION_FOR_TYPE.get(ch.type, "a read function")}("{cid}")'
            else:
                use = "fl::displayLine(row, text) (display channels are not addressed by id)"
            self.add(
                _line(first),
                "channel",
                f"channel '{cid}' is {'an' if ch.kind == 'actuator' else 'a'} {ch.kind}, so "
                f"{name} cannot be used with it; use {use}",
            )
        elif ch.type != ctype:
            use = READ_FUNCTION_FOR_TYPE.get(ch.type, "a function matching its type")
            self.add(
                _line(first),
                "channel",
                f"channel '{cid}' has type {ch.type}, but {name} handles {ctype}; use {use}",
            )

    def for_loop(self, node: Node, depth: int) -> None:
        self.scopes.append({})
        self.for_loop_scoped(node, depth)
        self.scopes.pop()

    def for_loop_scoped(self, node: Node, depth: int) -> None:
        problem, var = self.loop_shape(node)
        if problem is None and var is not None:
            self.declare(var, node, _Sym(buffer=False))
        body = node.child_by_field_name("body")
        if problem is not None:
            self.add(
                _line(node),
                "for_loop",
                problem + ". Allowed form: for (int i = 0; i < 10; i++) with integer literals and "
                f"at most {MAX_LOOP_ITERATIONS} iterations",
            )
            for name in ("initializer", "condition", "update"):
                part = node.child_by_field_name(name)
                if part is not None:
                    self.visit(part, depth)
        elif body is not None and var is not None:
            for n in _walk(body):
                target: Node | None = None
                if n.type == "assignment_expression":
                    target = n.child_by_field_name("left")
                elif n.type == "update_expression":
                    target = n.child_by_field_name("argument")
                if target is not None and _text(_unwrap_parens(target)) == var:
                    self.add(
                        _line(n),
                        "for_loop",
                        f"loop variable '{var}' must not be modified inside the loop body",
                    )
        if body is not None:
            self.visit(body, depth)

    def loop_shape(self, node: Node) -> tuple[str | None, str | None]:
        init = node.child_by_field_name("initializer")
        cond = node.child_by_field_name("condition")
        upd = node.child_by_field_name("update")
        if init is None or cond is None or upd is None:
            return "for needs an initializer, a condition and an update", None
        if init.type != "declaration":
            return "the for initializer must declare the loop variable (`int i = 0`)", None
        if any(c.type in ("storage_class_specifier", "type_qualifier") for c in init.children):
            return "the loop variable must be a plain integer (no static/const)", None
        type_text = _text(init.child_by_field_name("type"))
        decls = init.children_by_field_name("declarator")
        if (
            type_text not in LOOP_TYPE_RANGES
            or len(decls) != 1
            or decls[0].type != "init_declarator"
        ):
            return "the loop variable must be one integer type initialized with a literal", None
        d = decls[0]
        var_node = d.child_by_field_name("declarator")
        start = _int_literal(d.child_by_field_name("value"))
        var = _text(var_node)
        if var_node is None or var_node.type != "identifier" or start is None:
            return "the loop start must be an integer literal", None
        cond = _unwrap_parens(cond)
        if cond is not None and cond.type == "condition_clause":
            kids = _named_children(cond)
            cond = kids[0] if len(kids) == 1 else None
        if cond is None or cond.type != "binary_expression":
            return "the loop condition must be `var < literal` (or <=, >, >=)", None
        op = _text(cond.child_by_field_name("operator"))
        left = cond.child_by_field_name("left")
        bound = _int_literal(cond.child_by_field_name("right"))
        if op not in ("<", "<=", ">", ">=") or _text(left) != var or bound is None:
            return (
                f"the loop condition must compare '{var}' with an integer literal using "
                "<, <=, > or >=",
                None,
            )
        if upd.type != "update_expression" or _text(upd.child_by_field_name("argument")) != var:
            return f"the loop update must be {var}++ or {var}-- (also ++{var} / --{var})", None
        step = _text(upd.child_by_field_name("operator"))
        up = step == "++"
        if up != (op in ("<", "<=")):
            return "the loop condition and update go in opposite directions", None
        lo, hi = LOOP_TYPE_RANGES[type_text]
        # the first value that fails the test must itself be representable, or the loop never ends
        final = {"<": bound, "<=": bound + 1, ">": bound, ">=": bound - 1}[op]
        if not lo <= start <= hi or not lo <= final <= hi:
            return f"the loop bound does not fit in {type_text}, so the loop would never end", None
        count = max(0, final - start) if up else max(0, start - final)
        if count > MAX_LOOP_ITERATIONS:
            return f"the loop runs {count} times (max {MAX_LOOP_ITERATIONS})", None
        self.loop_counts[node.id] = count
        return None, var


# --- public entry point ----------------------------------------------------------------------


def _parse_errors(tree: Tree) -> list[Violation]:
    found: list[Violation] = []
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type == "ERROR" or n.is_missing:
            if n.is_missing:
                message = f"syntax error: missing '{n.type}'"
            else:
                snippet = _text(n).strip().splitlines()[0][:40] if _text(n).strip() else ""
                message = f"syntax error near `{snippet}`; fix the C++ syntax"
            found.append(Violation(line=_line(n), rule="parse_error", message=message))
            if n.type == "ERROR":
                continue
        stack.extend(n.children)
    if not found:
        found.append(
            Violation(line=1, rule="parse_error", message="the file could not be parsed as C++")
        )
    return found


def _dedupe(violations: list[Violation]) -> list[Violation]:
    seen: set[tuple[int, str]] = set()
    out: list[Violation] = []
    for v in sorted(violations, key=lambda v: v.line):
        key = (v.line, v.rule)
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def check(source: str, registry: Registry) -> SafetyReport:
    """Check generated ``user_logic.cpp`` source; pure, no I/O."""
    data = source.encode("utf-8")
    lines = len(source.splitlines())
    if lines > MAX_LINES or len(data) > MAX_BYTES:
        return SafetyReport(
            ok=False,
            violations=[
                Violation(
                    line=1,
                    rule="size",
                    message=f"file is {lines} lines / {len(data)} bytes; the limit is "
                    f"{MAX_LINES} lines and {MAX_BYTES} bytes. Write a shorter program",
                )
            ],
        )
    with _PARSER_LOCK:
        tree = _PARSER.parse(data)
    if tree.root_node.has_error:
        return SafetyReport(ok=False, violations=_dedupe(_parse_errors(tree)))
    checker = _Checker(registry=registry)
    checker.run(tree.root_node)
    violations = _dedupe(checker.violations)
    return SafetyReport(ok=not violations, violations=violations)
