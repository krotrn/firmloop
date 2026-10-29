import time
from pathlib import Path

import pytest

from firmloop.hardware.registry import Registry
from firmloop.safety.checker import SafetyReport, check

FIXTURES = Path(__file__).parent / "fixtures"

HEAD = '#include "FirmLoop.h"\n'
TAIL = "void user_setup() {}\nvoid user_tick(uint32_t now_ms) {\n%s\n}\n"


def tick(body: str, prelude: str = "") -> str:
    """A complete program (valid scaffolding) with `body` inside user_tick."""
    return HEAD + prelude + (TAIL % body)


def rules(report: SafetyReport) -> set[str]:
    return {v.rule for v in report.violations}


# --- valid programs ------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["valid_fan", "valid_helpers", "valid_prototype"])
def test_valid_programs_pass(name: str, registry: Registry) -> None:
    report = check((FIXTURES / f"{name}.cpp").read_text(), registry)
    assert report.violations == []
    assert report.ok


def test_check_is_fast(registry: Registry) -> None:
    source = (FIXTURES / "valid_helpers.cpp").read_text()
    check(source, registry)  # warm
    start = time.perf_counter()
    check(source, registry)
    assert time.perf_counter() - start < 0.05


# --- one failing fixture per rule: (id, source, rule, line) ----------------------------------

CASES: list[tuple[str, str, str, int | None]] = [
    ("parse_error", tick("int x = ;"), "parse_error", 4),
    (
        "parse_missing_brace",
        HEAD + "void user_setup() {}\nvoid user_tick(uint32_t t) {\n",
        "parse_error",
        None,
    ),
    (
        "extra_include",
        '#include "FirmLoop.h"\n#include <Arduino.h>\n' + (TAIL % ""),
        "preprocessor",
        2,
    ),
    ("define", tick("", "#define X 1\n"), "preprocessor", 2),
    ("ifdef", tick("", "#ifdef X\n#endif\n"), "preprocessor", 2),
    ("missing_include", TAIL % "", "preprocessor", None),
    ("long", tick("long x = 1;"), "type", 4),
    ("unsigned_long", tick("unsigned long x = 1;"), "type", 4),
    ("double", tick("double x = 1.0;"), "type", 4),
    ("string_class", tick("String s;"), "type", 4),
    ("std_type", tick("std::string s;"), "type", 4),
    ("auto", tick("auto x = 1;"), "type", 4),
    ("struct", tick("", "struct P { int x; };\n"), "type", 2),
    ("struct_local", tick("struct P { int x; } p;"), "type", 4),
    ("typedef", tick("", "typedef int myint;\n"), "type", 2),
    ("using", tick("", "using myint = int;\n"), "type", 2),
    ("enum", tick("", "enum E { A, B };\n"), "type", 2),
    ("char_scalar", tick("char c = 'a';"), "type", 4),
    ("global_non_static", tick("", "int counter = 0;\n"), "top_level", 2),
    ("global_namespace", tick("", "namespace n { }\n"), "top_level", 2),
    ("while", tick("while (true) { }"), "statement", 4),
    ("do_while", tick("do { } while (false);"), "statement", 4),
    ("goto", tick("goto end;\nend: ;"), "statement", 4),
    ("label", tick("end: ;"), "statement", 4),
    ("try", tick("try { } catch (...) { }"), "statement", 4),
    ("throw", tick("throw 1;"), "statement", 4),
    ("asm", tick('asm("nop");'), "statement", 4),
    ("subscript", tick("int x = 0;\nx = buf[1];", "static int buf[1];\n"), "array", 6),
    ("subscript_on_char_buffer", tick("char b[8] = {0};\nb[0] = 'a';"), "array", 5),
    ("non_char_array", tick("int a[4];"), "array", 4),
    ("global_non_char_array", tick("", "static float vals[4];\n"), "array", 2),
    ("array_too_big", tick("char b[65] = {0};"), "array", 4),
    ("array_non_literal_size", tick("char b[N];", "static const int N = 8;\n"), "array", 5),
    ("array_misused", tick('char b[8] = "";\nfl::setBool("fan", b);'), "array", 5),
    ("array_as_value", tick("char b[8] = {0};\nint x = b;"), "array", 5),
    ("pointer_decl", tick("int* p = 0;"), "pointer", 4),
    ("pointer_global", tick("", "static int *gp;\n"), "pointer", 2),
    ("reference", tick("int a = 0;\nint& r = a;"), "pointer", 5),
    ("deref", tick("int a = 0;\nint b = *(&a);"), "pointer", 5),
    ("address_of", tick("int a = 0;\nint b = &a;"), "pointer", 5),
    ("arrow", tick("p->x = 1;"), "pointer", 4),
    ("c_cast", tick("int x = (int)3.5f;"), "pointer", 4),
    ("register_poke", tick("*(uint32_t*)0x3FF44004 = 1;"), "pointer", 4),
    ("static_cast", tick("int x = static_cast<int>(3.5f);"), "pointer", 4),
    ("reinterpret_cast", tick("int x = reinterpret_cast<int>(3);"), "pointer", 4),
    ("new", tick("int* p = new int;"), "pointer", 4),
    ("delete", tick("delete p;"), "pointer", 4),
    ("sizeof_type", tick("int n = sizeof(int);"), "sizeof", 4),
    ("string_outside_call", tick('int x = "abc";'), "string", 4),
    ("percent_n", tick('char b[8] = "";\nsnprintf(b, sizeof b, "%n", 1);'), "format", 5),
    ("delay", tick("delay(100);"), "call", 4),
    ("millis", tick("uint32_t t = millis();"), "call", 4),
    ("digitalWrite", tick("digitalWrite(2, 1);"), "call", 4),
    ("exit", tick("exit(0);"), "call", 4),
    ("malloc", tick("malloc(4);"), "call", 4),
    ("serial", tick('Serial.println("hi");'), "call", 4),
    ("std_call", tick("std::abs(3);"), "call", 4),
    ("undefined_helper", tick("helper(3);"), "call", 4),
    ("direct_recursion", tick("", "static int f(int n) { return f(n - 1); }\n"), "recursion", 2),
    (
        "mutual_recursion",
        tick(
            "",
            "static int b(int n);\nstatic int a(int n) { return b(n); }\n"
            "static int b(int n) { return a(n); }\n",
        ),
        "recursion",
        3,
    ),
    ("for_bound_too_big", tick("for (int i = 0; i < 65; i++) { }"), "for_loop", 4),
    ("for_bound_inclusive_too_big", tick("for (int i = 0; i <= 64; i++) { }"), "for_loop", 4),
    ("for_down_too_big", tick("for (int i = 100; i > 0; i--) { }"), "for_loop", 4),
    ("for_non_literal_bound", tick("int n = 3;\nfor (int i = 0; i < n; i++) { }"), "for_loop", 5),
    ("for_non_literal_start", tick("int n = 3;\nfor (int i = n; i < 9; i++) { }"), "for_loop", 5),
    ("for_var_assigned", tick("for (int i = 0; i < 4; i++) { i = 7; }"), "for_loop", 4),
    ("for_var_compound_assigned", tick("for (int i = 0; i < 4; i++) { i += 2; }"), "for_loop", 4),
    ("for_var_incremented_in_body", tick("for (int i = 0; i < 4; i++) { i++; }"), "for_loop", 4),
    ("for_empty", tick("for (;;) { }"), "for_loop", 4),
    ("for_wrong_direction", tick("for (int i = 0; i < 4; i--) { }"), "for_loop", 4),
    ("for_step_two", tick("for (int i = 0; i < 4; i += 2) { }"), "for_loop", 4),
    ("for_uint8_overflow", tick("for (uint8_t i = 0; i <= 255; i++) { }"), "for_loop", 4),
    ("for_unsigned_down_to_zero", tick("for (uint8_t i = 3; i >= 0; i--) { }"), "for_loop", 4),
    ("for_float_var", tick("for (float f = 0; f < 4; f++) { }"), "for_loop", 4),
    ("for_range", tick("for (int x : y) { }"), "statement", 4),
    ("channel_unknown", tick('float t = fl::readFloat("tmp");'), "channel", 4),
    ("channel_non_literal", tick("const int c = 1;\nfl::readFloat(c);"), "channel", 5),
    ("channel_wrong_type_float_as_int", tick('int t = fl::readInt("temp");'), "channel", 4),
    ("channel_wrong_type_bool_as_float", tick('float t = fl::readFloat("motion");'), "channel", 4),
    ("channel_actuator_read", tick('bool b = fl::readBool("fan");'), "channel", 4),
    ("channel_sensor_set", tick('fl::setBool("motion", true);'), "channel", 4),
    ("channel_set_float", tick('fl::setBool("temp", true);'), "channel", 4),
    ("channel_display_read", tick('fl::readBool("display");'), "channel", 4),
    ("missing_tick", HEAD + "void user_setup() {}\n", "entry_point", None),
    ("missing_setup", HEAD + "void user_tick(uint32_t t) {}\n", "entry_point", None),
    ("duplicate_tick", tick("") + "void user_tick(uint32_t t) {}\n", "entry_point", 6),
    ("duplicate_setup", tick("") + "void user_setup() {}\n", "entry_point", 6),
    (
        "tick_wrong_signature",
        HEAD + "void user_setup() {}\nvoid user_tick() {}\n",
        "entry_point",
        3,
    ),
    (
        "tick_wrong_return",
        HEAD + "void user_setup() {}\nint user_tick(uint32_t t) { return 0; }\n",
        "entry_point",
        3,
    ),
    (
        "setup_with_param",
        HEAD + "void user_setup(int a) {}\nvoid user_tick(uint32_t t) {}\n",
        "entry_point",
        2,
    ),
    ("size_lines", tick("\n".join(["int a;"] * 300)), "size", 1),
    ("size_bytes", tick("// " + "x" * 12000), "size", 1),
]


@pytest.mark.parametrize(
    ("source", "rule", "line"),
    [pytest.param(s, r, ln, id=i) for i, s, r, ln in CASES],
)
def test_violation(source: str, rule: str, line: int | None, registry: Registry) -> None:
    report = check(source, registry)
    assert not report.ok
    matching = [v for v in report.violations if v.rule == rule]
    assert matching, f"expected rule {rule}, got {report.violations}"
    if line is not None:
        assert line in {v.line for v in matching}, (line, matching)
    assert all(v.message for v in report.violations)


# --- behaviour -----------------------------------------------------------------------------


def test_collects_all_violations_and_dedupes(registry: Registry) -> None:
    source = tick("delay(1);\nint x = millis() + millis();\nlong y;\nwhile (1) {}")
    report = check(source, registry)
    assert rules(report) == {"call", "type", "statement"}
    keys = [(v.line, v.rule) for v in report.violations]
    assert len(keys) == len(set(keys))
    assert [v.line for v in report.violations] == sorted(v.line for v in report.violations)
    assert {v.line for v in report.violations if v.rule == "call"} == {4, 5}


def test_messages_tell_the_model_what_to_do(registry: Registry) -> None:
    report = check(tick("uint32_t t = millis();"), registry)
    assert "now_ms" in report.violations[0].message
    report = check(tick("delay(5);"), registry)
    assert "now_ms" in report.violations[0].message
    report = check(tick('float t = fl::readFloat("tmp");'), registry)
    assert "temp" in report.violations[0].message  # lists known channels


def test_empty_source(registry: Registry) -> None:
    report = check("", registry)
    assert not report.ok


def test_deep_nesting_does_not_crash(registry: Registry) -> None:
    report = check(tick("int x = " + "(" * 3000 + "1" + ")" * 3000 + ";"), registry)
    assert not report.ok


def test_for_loop_boundaries_allowed(registry: Registry) -> None:
    ok_bodies = [
        "for (int i = 0; i < 64; i++) { }",
        "for (int i = 1; i <= 64; ++i) { }",
        "for (int i = 64; i > 0; i--) { }",
        "for (int i = 63; i >= 0; --i) { }",
        "for (int i = 0; i < 0; i++) { }",
        "for (int8_t i = 0; i < 10; i++) { int j = i; j++; }",
        "for (int i = -5; i < 5; i++) { }",
    ]
    for body in ok_bodies:
        report = check(tick(body), registry)
        assert report.ok, (body, report.violations)


def test_nested_loops_independent_vars(registry: Registry) -> None:
    report = check(
        tick("for (int i = 0; i < 8; i++) { for (int j = 0; j < 8; j++) { } }"), registry
    )
    assert report.ok, report.violations


def test_array_allowed_uses(registry: Registry) -> None:
    body = (
        'char a[16] = "";\nchar b[16] = "";\nsnprintf(a, sizeof a, "%d", 3);\n'
        'snprintf(b, sizeof(b), "%s", "hi");\nfl::displayLine(0, a);\nfl::displayLine(1, b);'
    )
    assert check(tick(body), registry).ok
    assert not check(tick('char a[16] = "";\nstrcmp(a, "x");'), registry).ok


# --- format strings, buffers, linkage, work budget ---


def snp(fmt_args: str, decl: str = "char b[16] = {0};") -> str:
    return tick(f"{decl}\nsnprintf(b, sizeof b, {fmt_args});\nfl::displayLine(0, b);")


FIX1_REJECT: list[tuple[str, str, str]] = [
    # 1. format strings
    ("fmt_hex_escape", snp(r'"%\x6e", 1073741824'), "format"),
    ("fmt_octal_escape", snp(r'"%\156", 1073741824'), "format"),
    ("fmt_positional", snp('"%1$n", 1073741824'), "format"),
    ("fmt_qn", snp('"%qn", 1073741824'), "format"),
    ("fmt_backslash_newline", snp('"%\\\nn", 1073741824'), "format"),
    ("fmt_star", snp('"%*d", 1, 2'), "format"),
    ("fmt_p", snp('"%p", 1'), "format"),
    ("fmt_lld", snp('"%lld", 1'), "format"),
    ("fmt_not_literal", snp("b, 1"), "format"),
    # 2. %s with non-text, arg count
    ("fmt_s_with_int", snp('"%s", 1073741824'), "format"),
    ("fmt_s_with_var", snp('"%s", x', "char b[16] = {0}; int x = 3;"), "format"),
    ("fmt_d_with_string", snp('"%d", "abc"'), "format"),
    ("fmt_too_few_args", snp('"%d %d", 1'), "format"),
    ("fmt_too_many_args", snp('"%d", 1, 2'), "format"),
    # 3. buffer and size
    (
        "buf_size_literal_too_big",
        snp('"%d", 1').replace("sizeof b", "1000").replace("[16]", "[4]"),
        "array",
    ),
    ("buf_width_900", tick('char b[4] = "";\nsnprintf(b, 1000, "%900d", 1);'), "format"),
    (
        "buf_size_of_other",
        tick('char b[4] = ""; char c[64] = "";\nsnprintf(b, sizeof c, "%d", 1);'),
        "array",
    ),
    ("buf_literal_dest", tick('snprintf("x", 3, "%d", 1);'), "array"),
    ("fmt_width_3_digits", snp('"%100d", 1'), "format"),
    (
        "display_non_buffer",
        tick("int x = 3;\nfl::displayLine(0, x);"),
        "array",
    ),
    # 4. overload recursion
    (
        "overload_recursion",
        tick(
            "int r = a(1);",
            "static int a(int x);\nstatic int b(int x);\n"
            "static int a(int x) { return b(x); }\nstatic int a(float x) { return 0; }\n"
            "static int b(int x) { return a(x); }\nstatic int b(float x) { return 0; }\n",
        ),
        "top_level",
    ),
    # 5. work budget
    (
        "work_nested_loops",
        tick(
            "for (int a = 0; a < 64; a++) { for (int b = 0; b < 64; b++) {"
            " for (int c = 0; c < 64; c++) { for (int d = 0; d < 64; d++) {"
            " for (int e = 0; e < 64; e++) { fl::now(); } } } } }"
        ),
        "work_budget",
    ),
    (
        "work_call_chain",
        tick(
            "f0();",
            "static void f29() { fl::now(); }\n"
            + "".join(
                f"static void f{i}() {{ f{i + 1}(); f{i + 1}(); }}\n" for i in range(28, -1, -1)
            ),
        ),
        "work_budget",
    ),
    # 6. linkage and entry points
    ("nonstatic_helper", tick("", "int abs(int x) { return x; }\n"), "top_level"),
    ("app_main", tick("", "void app_main() {}\n"), "top_level"),
    (
        "static_entry",
        HEAD + "void user_setup() {}\nstatic void user_tick(uint32_t n) {}\n",
        "entry_point",
    ),
    ("char_return", tick("", "static char f(char a) { return a; }\n"), "type"),
    ("char_param", tick("", "static int f(char a) { return 1; }\n"), "type"),
    ("static_for_var", tick("for (static int i = 0; i < 3; i++) { }"), "for_loop"),
]


@pytest.mark.parametrize(("source", "rule"), [pytest.param(s, r, id=i) for i, s, r in FIX1_REJECT])
def test_fix1_rejected(source: str, rule: str, registry: Registry) -> None:
    report = check(source, registry)
    assert rule in rules(report), report.violations


def _chars(n: int) -> str:
    return f'char line[{n}] = "";'


FIX1_ACCEPT: list[tuple[str, str]] = [
    (
        "prototypes",
        HEAD
        + "static void helper();\nvoid user_setup();\nvoid user_tick(uint32_t n);\n"
        + "static void helper() {}\nvoid user_setup() {}\n"
        + "void user_tick(uint32_t n) { helper(); }\n",
    ),
    ("char_literal_escape", tick("int c = 3;\nif (c == '\\n') { }")),
    (
        "fmt_long_and_float",
        tick(
            f'{_chars(22)}\nint32_t h = 3;\nsnprintf(line, sizeof line, "T %.1f H %ld", 1.0f, h);'
        ),
    ),
    ("fmt_s_literal", tick(f'{_chars(22)}\nsnprintf(line, sizeof line, "%s", "Hot");')),
    ("fmt_percent_percent", tick(f'{_chars(22)}\nint pct = 4;\nsnprintf(line, 22, "%d%%", pct);')),
    ("fmt_s_buffer", tick('char a[8] = ""; char b[16] = "";\nsnprintf(b, sizeof b, "%s", a);')),
    ("display_literal", tick('fl::displayLine(0, "hi");')),
    (
        "work_two_nested_ok",
        tick(
            "for (int a = 0; a < 64; a++) { for (int b = 0; b < 64; b++) { h(); } }",
            "static void h() { fl::now(); }\n",
        ),
    ),
]


@pytest.mark.parametrize(("source",), [pytest.param(s, id=i) for i, s in FIX1_ACCEPT])
def test_fix1_accepted(source: str, registry: Registry) -> None:
    report = check(source, registry)
    assert report.violations == [], report.violations


# --- scopes, initialized buffers, attributes -------------------------------------------------

SCOPE_REJECT: list[tuple[str, str, str]] = [
    (
        "scope_block_buffer_then_int",
        tick(
            'char l[16] = "";\n{ char b[8] = ""; }\nint b = 1073741824;\n'
            'snprintf(l, sizeof l, "%s", b);'
        ),
        "format",
    ),
    (
        "scope_int_reaches_display",
        tick('{ char q[4] = ""; }\nint q = 1073741824;\nfl::displayLine(0, q);'),
        "array",
    ),
    (
        "scope_int_as_snprintf_dest",
        tick('{ char q[4] = ""; }\nint q = 1073741824;\nsnprintf(q, 4, "x");'),
        "array",
    ),
    (
        "scope_param_shadows_global",
        tick("", 'static char b[8] = "";\nstatic void f(int b) { b = 1; }\n'),
        "scope",
    ),
    (
        "scope_for_var_shadows_buffer",
        tick('char b[8] = "";\nfor (int b = 0; b < 3; b++) { }'),
        "scope",
    ),
    ("scope_local_shadows_local", tick("int a = 1;\n{ int a = 2; }"), "scope"),
    ("scope_unresolved_name", tick("int a = nothing;"), "scope"),
    ("buffer_uninitialized", tick("char a[8];"), "array"),
    ("buffer_global_uninitialized", tick("", "static char a[8];\n"), "array"),
    ("buffer_init_not_terminated", tick('char a[3] = "abc";'), "array"),
    ("buffer_init_nonzero", tick("char a[3] = {1};"), "array"),
    (
        "dest_as_s_arg",
        tick('char l[16] = "";\nsnprintf(l, sizeof l, "%s", l);'),
        "format",
    ),
    (
        "dest_as_s_arg_parenthesized",
        tick('char l[16] = "";\nsnprintf(l, sizeof l, "%s", (l));'),
        "format",
    ),
    ("buffer_init_utf8_too_long", tick('char b[4] = "\u00e9\u00e9";'), "array"),
    ("fmt_ls", tick('char l[16] = "";\nsnprintf(l, sizeof l, "%ls", "a");'), "format"),
    ("fmt_lf", tick('char l[16] = "";\nsnprintf(l, sizeof l, "%lf", 1.0f);'), "format"),
    ("fmt_escape_other", tick('char l[16] = "";\nsnprintf(l, sizeof l, "\\a");'), "format"),
    ("fmt_escape_percent_n", tick('char l[16] = "";\nsnprintf(l, sizeof l, "%\\n");'), "format"),
    (
        "size_minus_too_big",
        tick('char l[16] = "";\nsnprintf(l, sizeof l - 17, "x");'),
        "array",
    ),
    (
        "size_plus",
        tick('char l[16] = "";\nsnprintf(l, sizeof l + 1, "x");'),
        "array",
    ),
    (
        "size_of_other_minus",
        tick('char l[16] = ""; char m[64] = "";\nsnprintf(l, sizeof m - 1, "x");'),
        "array",
    ),
    (
        "global_init_call",
        tick("", "static int h() { return 1; }\nstatic int g = h();\n"),
        "top_level",
    ),
    (
        "attr_constructor",
        tick("", "static void g() __attribute__((constructor));\nstatic void g() {}\n"),
        "attribute",
    ),
    (
        "attr_alias",
        tick("", 'static int a(int) __attribute__((alias("user_tick")));\n'),
        "attribute",
    ),
    (
        "attr_weakref",
        tick("", 'static int s(int) __attribute__((weakref("system")));\n'),
        "attribute",
    ),
    (
        "proto_mismatch_params",
        tick(
            "",
            "static int f(int);\nstatic int f(float);\nstatic int f(int a) { return a; }\n",
        ),
        "top_level",
    ),
    (
        "proto_mismatch_return",
        tick("", "static float f(int);\nstatic int f(int a) { return a; }\n"),
        "top_level",
    ),
    ("proto_without_definition", tick("", "static int f(int);\n"), "top_level"),
]


@pytest.mark.parametrize(("source", "rule"), [pytest.param(s, r, id=i) for i, s, r in SCOPE_REJECT])
def test_scope_rejected(source: str, rule: str, registry: Registry) -> None:
    report = check(source, registry)
    assert rule in rules(report), report.violations


SCOPE_ACCEPT: list[tuple[str, str]] = [
    (
        "ternary_literals",
        tick(
            'char l[22] = "";\nbool on = true;\nsnprintf(l, sizeof l, "Fan:%s", on ? "ON" : "OFF");'
        ),
    ),
    (
        "ternary_display",
        tick('bool on = true;\nfl::displayLine(0, on ? "ON" : "OFF");'),
    ),
    (
        "sizeof_paren_minus",
        tick('char l[22] = "";\nint x = 1;\nsnprintf(l, sizeof(l) - 1, "%d", x);'),
    ),
    ("sizeof_minus", tick('char l[22] = "";\nint x = 1;\nsnprintf(l, sizeof l - 1, "%d", x);')),
    ("fmt_newline", tick('char l[22] = "";\nint x = 1;\nsnprintf(l, sizeof l, "T=%d\\n", x);')),
    ("fmt_tab_quote", tick('char l[22] = "";\nsnprintf(l, sizeof l, "a\\t\\"b\\\\");')),
    (
        "sibling_loops",
        tick(
            "int n = 0;\nfor (int i = 0; i < 4; i++) { n++; }\nfor (int i = 0; i < 4; i++) { n++; }"
        ),
    ),
    ("sibling_blocks", tick("{ int a = 1; a++; }\n{ int a = 2; a++; }")),
    ("init_zero", tick("char l[8] = {0};\nfl::displayLine(0, l);")),
    (
        "init_empty",
        tick(
            "static_assert_free:;".replace(
                "static_assert_free:;", 'char l[8] = "";\nfl::displayLine(1, l);'
            )
        ),
    ),
    (
        "source_buffer_to_snprintf",
        tick('char a[8] = ""; char b[16] = "";\nsnprintf(b, sizeof b, "%s|%s", a, "x");'),
    ),
    (
        "proto_matches",
        tick("", "static int f(int);\nstatic int f(int a) { return a; }\n"),
    ),
]


@pytest.mark.parametrize(("source",), [pytest.param(s, id=i) for i, s in SCOPE_ACCEPT])
def test_scope_accepted(source: str, registry: Registry) -> None:
    report = check(source, registry)
    assert report.violations == [], report.violations
