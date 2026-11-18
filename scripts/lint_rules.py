#!/usr/bin/env python3
"""ModelDesk lint rules checker.

Usage:
    python3 scripts/lint_rules.py            # exit 0 = pass, 1 = violations
    python3 scripts/lint_rules.py --report   # human-readable summary

Rules are registered in REGISTRY and can be imported by tests via
importlib.util.spec_from_file_location.
"""
import ast
import json
import os
import re
import sys
import tokenize
from datetime import date
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
BASELINE_PATH = SCRIPT_DIR / "rules-baseline.json"
CLAUDE_MD_PATH = REPO_ROOT / "CLAUDE.md"
TODAY = date.today()

# R1 exists for one specific bug: user-facing copy that asserts the *current
# host* is a Mac without consulting the platform. Verified against the three
# historical commits (9b12b67, e85bcba, 8be0ee6) — every line they had to fix
# matches R1_PATTERN. Not every mention of Mac is that bug: naming macOS as a
# capability ("the Muse bridge only works on macOS, Linux is unsupported") is
# accurate on any host, and a platform id -> label map ("darwin" -> "macOS")
# is the source of truth rather than an assumption, so both are exempt below.
R1_PATTERN = re.compile(r"(?<![A-Za-z])Mac(?![a-z])|(?<![A-Za-z])macOS(?![A-Za-z])")
R1_EXCLUDE_PATTERN = re.compile(r"仅在|只在|不支持|不是\s*Mac|非\s*macOS")
R1_GUARD_PATTERN = re.compile(r"is_macos|machine_label|isMacos|machine\.|platform")
R1_EXEMPT_FILES = {
    "frontend/src/machine.js",        # the label table is the source of truth
    "frontend/src/fleet-state.js",    # platform id -> display name map
    "backend/platform_support.py",    # defines machine_label() itself
}


def _r1_guarded(source_lines, lineno):
    """A line that already consults the platform is allowed to name it."""
    if not source_lines or not (1 <= lineno <= len(source_lines)):
        return False
    return bool(R1_GUARD_PATTERN.search(source_lines[lineno - 1]))


# ── Helpers ──────────────────────────────────────────────────────────

def _load_baseline():
    """Return dict mapping (rule, file, fingerprint) -> entry."""
    if not BASELINE_PATH.exists():
        return {}
    with open(BASELINE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    result = {}
    for entry in data.get("entries", []):
        key = (entry["rule"], entry["file"], entry["fingerprint"])
        result[key] = entry
    return result


def _walk_backend_py(exclude_tests=False, exclude_platform_support=False):
    """Yield relative paths of .py files under backend/."""
    backend_dir = REPO_ROOT / "backend"
    if not backend_dir.is_dir():
        return
    for root, dirs, files in os.walk(backend_dir):
        dirs[:] = sorted(dirs)
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            if exclude_tests and fn.startswith("test_"):
                continue
            if exclude_platform_support and fn == "platform_support.py":
                continue
            yield os.path.relpath(os.path.join(root, fn), REPO_ROOT)


def _walk_frontend_src(exclude_tests=True):
    """Yield relative paths of .js/.jsx files under frontend/src/."""
    src_dir = REPO_ROOT / "frontend" / "src"
    if not src_dir.is_dir():
        return
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = sorted(dirs)
        # Skip test directories
        if exclude_tests:
            dirs[:] = [d for d in dirs if d != "tests" and d != "__tests__"]
        for fn in sorted(files):
            if not (fn.endswith(".js") or fn.endswith(".jsx")):
                continue
            if exclude_tests and fn.endswith(".test.js"):
                continue
            yield os.path.relpath(os.path.join(root, fn), REPO_ROOT)


def _is_docstring(node, parent):
    """Check if an Expr(Constant str) is a docstring (first stmt in body)."""
    if isinstance(parent, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        if hasattr(parent, "body") and len(parent.body) > 0 and parent.body[0] is node:
            return True
    return False


def _extract_py_string_literals(filepath):
    """Extract (lineno, text) from Python file, skipping docstrings."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, UnicodeDecodeError):
        return []

    results = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.parents = []

        def generic_visit(self, node):
            self.parents.append(node)
            for child in ast.iter_child_nodes(node):
                self.visit(child)
            self.parents.pop()

        def visit_Constant(self, node):
            if isinstance(node.value, str):
                parent = self.parents[-1] if self.parents else None
                grandparent = self.parents[-2] if len(self.parents) >= 2 else None
                if parent and isinstance(parent, ast.Expr) and grandparent and _is_docstring(parent, grandparent):
                    return
                results.append((node.lineno, node.value))

        def visit_JoinedStr(self, node):
            for val in node.values:
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    results.append((val.lineno, val.value))

    v = Visitor()
    v.visit(tree)
    return results


def _strip_js_comments(source):
    """Remove // and /* */ comments from JS source, respecting strings."""
    result = []
    i = 0
    n = len(source)
    in_str = None
    while i < n:
        c = source[i]
        if in_str:
            result.append(c)
            if c == "\\" and i + 1 < n:
                result.append(source[i + 1])
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
        else:
            if c in ('"', "'", "`"):
                in_str = c
                result.append(c)
                i += 1
            elif c == "/" and i + 1 < n and source[i + 1] == "/":
                while i < n and source[i] != "\n":
                    i += 1
            elif c == "/" and i + 1 < n and source[i + 1] == "*":
                i += 2
                while i < n - 1 and not (source[i] == "*" and source[i + 1] == "/"):
                    i += 1
                i += 2
            else:
                result.append(c)
                i += 1
    return "".join(result)


def _extract_js_strings(source):
    """Extract (lineno, text) from comment-stripped JS/JSX source."""
    results = []
    line_num = 1
    i = 0
    n = len(source)
    while i < n:
        c = source[i]
        if c == "\n":
            line_num += 1
            i += 1
        elif c in ('"', "'"):
            quote = c
            i += 1
            content = []
            while i < n and source[i] != quote:
                if source[i] == "\\" and i + 1 < n:
                    content.append(source[i + 1])
                    i += 2
                else:
                    if source[i] == "\n":
                        line_num += 1
                    content.append(source[i])
                    i += 1
            if i < n:
                i += 1  # skip closing quote
            results.append((line_num, "".join(content)))
        elif c == "`":
            i += 1
            content = []
            depth = 0
            while i < n:
                if source[i] == "\\" and i + 1 < n:
                    content.append(source[i + 1])
                    i += 2
                    continue
                if source[i] == "`" and depth == 0:
                    i += 1
                    break
                if source[i] == "$" and i + 1 < n and source[i + 1] == "{":
                    depth += 1
                    content.append(source[i])
                    i += 1
                elif source[i] == "}" and depth > 0:
                    depth -= 1
                    content.append(source[i])
                    i += 1
                else:
                    if source[i] == "\n":
                        line_num += 1
                    content.append(source[i])
                    i += 1
            results.append((line_num, "".join(content)))
        else:
            i += 1
    return results


# ── Rule Implementations ─────────────────────────────────────────────

def check_r1(baseline=None):
    """R1 platform-copy: detect Mac/macOS in user-facing string literals.

    Ratchet: only flag hits NOT in baseline. Report shrink opportunities.
    Returns (violations, shrink_list).
    """
    if baseline is None:
        baseline = _load_baseline()

    violations = []
    current_hits = {}  # (file, fingerprint) -> lineno

    # Backend scan
    for relpath in _walk_backend_py(exclude_tests=True, exclude_platform_support=True):
        if relpath in R1_EXEMPT_FILES:
            continue
        filepath = REPO_ROOT / relpath
        literals = _extract_py_string_literals(filepath)
        for lineno, text in literals:
            if not R1_PATTERN.search(text) or R1_EXCLUDE_PATTERN.search(text):
                continue
            fp = text.strip()
            key = ("R1", relpath, fp)
            current_hits[(relpath, fp)] = lineno
            if key not in baseline:
                violations.append(
                    f"R1 {relpath}:{lineno} — "
                    f"文案断言当前主机是 Mac「{fp[:60]}」；"
                    f"修复：改用 backend/platform_support.py 的 machine_label()"
                )

    # Frontend scan
    for relpath in _walk_frontend_src(exclude_tests=True):
        filepath = REPO_ROOT / relpath
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                source = f.read()
        except (UnicodeDecodeError, OSError):
            continue
        stripped = _strip_js_comments(source)
        source_lines = stripped.split("\n")
        strings = _extract_js_strings(stripped)
        for lineno, text in strings:
            if not R1_PATTERN.search(text) or R1_EXCLUDE_PATTERN.search(text):
                continue
            if relpath in R1_EXEMPT_FILES or _r1_guarded(source_lines, lineno):
                continue
            fp = text.strip()
            key = ("R1", relpath, fp)
            current_hits[(relpath, fp)] = lineno
            if key not in baseline:
                violations.append(
                    f"R1 {relpath}:{lineno} — "
                    f"文案断言当前主机是 Mac「{fp[:60]}」；"
                    f"修复：用 machineInfo() 的 label，或改成中性文案『运行 ModelDesk 的这台电脑』"
                )

    # Detect shrink: baseline entries that no longer exist in code
    shrink = []
    r1_baseline_entries = {k for k in baseline if k[0] == "R1"}
    current_keys = {("R1", relpath, fp) for relpath, fp in current_hits}
    for key in sorted(r1_baseline_entries - current_keys):
        shrink.append(
            f"SHRINK R1 {key[1]} — baseline 条目「{key[2][:60]}」已不在代码中，可从 baseline 删除"
        )

    return violations, shrink


def check_r2():
    """R2 bare-suppression: every suppression comment needs reason + expires.

    Zero tolerance: any suppression without valid reason=... expires=YYYY-MM-DD
    (where expires >= today) is a violation.

    Only flags actual suppression directives in comments, not mentions inside
    string literals or docstrings.
    """
    violations = []

    suppression_re = re.compile(
        r"eslint-disable|@ts-ignore|@ts-expect-error|#\s*noqa|#\s*type:\s*ignore|pylint:\s*disable"
    )

    # Extract reason and expires values from suppression comment text.
    # Strategy: find reason= and capture until next keyword or EOL;
    # then validate that captured text has non-whitespace content.
    reason_raw_pat = re.compile(r"reason\s*=\s*(.*?)\s*(?:expires\b|$)")
    expires_pat = re.compile(r"expires\s*=\s*(\d{4}-\d{2}-\d{2})")

    def _py_comment_lines(filepath):
        """Yield (lineno, comment_text) for real Python comments only."""
        try:
            with tokenize.open(filepath) as source:
                for token in tokenize.generate_tokens(source.readline):
                    if token.type == tokenize.COMMENT:
                        yield token.start[0], token.string
        except (OSError, UnicodeDecodeError, SyntaxError, tokenize.TokenError):
            return

    def _js_comment_lines(filepath):
        """Yield (lineno, comment_text) for JS/JSX comments only."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                source = f.read()
        except (UnicodeDecodeError, OSError):
            return
        lines = source.split("\n")
        i = 0
        n = len(source)
        line_num = 1
        in_str = None
        while i < n:
            c = source[i]
            if in_str:
                if c == "\\" and i + 1 < n:
                    i += 2
                    continue
                if c == "\n":
                    line_num += 1
                if c == in_str:
                    in_str = None
                i += 1
            else:
                if c in ('"', "'", "`"):
                    in_str = c
                    i += 1
                elif c == "/" and i + 1 < n and source[i + 1] == "/":
                    # Single-line comment - capture it
                    end = source.find("\n", i)
                    if end == -1:
                        end = n
                    yield (line_num, source[i:end])
                    i = end
                elif c == "/" and i + 1 < n and source[i + 1] == "*":
                    # Block comment - capture it
                    end = source.find("*/", i + 2)
                    if end == -1:
                        end = n
                    else:
                        end += 2
                    comment_text = source[i:end]
                    # May span multiple lines; report on start line
                    yield (line_num, comment_text)
                    newlines = comment_text.count("\n")
                    line_num += newlines
                    i = end
                elif c == "\n":
                    line_num += 1
                    i += 1
                else:
                    i += 1

    def _check_suppression(text, relpath, lineno):
        """Check a comment text for suppression directives."""
        if not suppression_re.search(text):
            return
        has_reason = False
        has_valid_expires = False
        rm = reason_raw_pat.search(text)
        if rm and rm.group(1).strip():
            has_reason = True
        em = expires_pat.search(text)
        if em:
            try:
                exp_date = date.fromisoformat(em.group(1))
                if exp_date >= TODAY:
                    has_valid_expires = True
            except ValueError:
                pass

        if not has_reason or not has_valid_expires:
            issues = []
            if not has_reason:
                issues.append("缺少 reason=<非空文本>")
            if not has_valid_expires:
                issues.append("缺少有效 expires=YYYY-MM-DD（需 >= 今天）")
            violations.append(
                f"R2 {relpath}:{lineno} — "
                f"抑制注释不完整：{'; '.join(issues)}；"
                f"修复：补 reason=... expires=...，或按 pstack 原则删抑制修真错"
            )

    # Scan Python files: only check actual comments, not string literals
    py_scopes = []
    for relpath in _walk_backend_py(exclude_tests=False):
        py_scopes.append(relpath)
    for fn in sorted(os.listdir(SCRIPT_DIR)):
        if fn.endswith(".py"):
            py_scopes.append(os.path.relpath(SCRIPT_DIR / fn, REPO_ROOT))

    for relpath in py_scopes:
        filepath = REPO_ROOT / relpath
        for lineno, comment_text in _py_comment_lines(filepath):
            _check_suppression(comment_text, relpath, lineno)

    # Scan JS/JSX files: only check actual comments
    for relpath in _walk_frontend_src(exclude_tests=True):
        filepath = REPO_ROOT / relpath
        for lineno, comment_text in _js_comment_lines(filepath):
            _check_suppression(comment_text, relpath, lineno)

    # Scan desktop/*.cjs: only check actual comments
    desktop_dir = REPO_ROOT / "desktop"
    if desktop_dir.is_dir():
        for fn in sorted(os.listdir(desktop_dir)):
            if fn.endswith(".cjs"):
                relpath = os.path.relpath(desktop_dir / fn, REPO_ROOT)
                filepath = REPO_ROOT / relpath
                for lineno, comment_text in _js_comment_lines(filepath):
                    _check_suppression(comment_text, relpath, lineno)

    return violations


def check_r3():
    """R3 return-arity: detect functions returning tuples of varying lengths.

    Zero tolerance: if a function directly returns tuples of different sizes,
    that's a violation. Evaluate each function in its own lexical scope.
    """
    violations = []

    for relpath in _walk_backend_py(exclude_tests=True):
        filepath = REPO_ROOT / relpath
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=str(filepath))
        except (SyntaxError, UnicodeDecodeError):
            continue

        def _collect_return_arities(node):
            """Collect tuple arities of direct return statements in a function."""
            arities = set()
            for child in ast.iter_child_nodes(node):
                # Skip nested functions, lambdas, classes
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                    continue
                if isinstance(child, ast.Return):
                    if child.value is not None:
                        if isinstance(child.value, ast.Tuple):
                            arities.add(len(child.value.elts))
                # Recurse into non-function bodies (if/for/while/try/etc.)
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                    arities.update(_collect_return_arities(child))
            return arities

        class FuncVisitor(ast.NodeVisitor):
            def _visit_func(self, node):
                name = node.name
                arities = _collect_return_arities(node)
                if len(arities) > 1:
                    violations.append(
                        f"R3 {relpath}:{node.lineno} — "
                        f"函数 {name}() 返回元组长度不一致 {sorted(arities)}；"
                        f"修复：统一为单一返回形状（推荐具名 tuple 或 dict），禁止调用方按索引猜"
                    )
                # Each nested function gets its own independent return contract.
                self.generic_visit(node)

            def visit_FunctionDef(self, node):
                self._visit_func(node)

            def visit_AsyncFunctionDef(self, node):
                self._visit_func(node)

        FuncVisitor().visit(tree)

    return violations


def check_r4():
    """R4 rule-table: CLAUDE.md rule table must match REGISTRY bidirectionally."""
    violations = []

    # Read CLAUDE.md and extract rule ids from the enforcement table
    if not CLAUDE_MD_PATH.exists():
        violations.append(
            "R4 CLAUDE.md — 文件不存在；修复：创建 CLAUDE.md 并添加规则执行表"
        )
        return violations

    with open(CLAUDE_MD_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # Find the rule enforcement table section
    # Look for table rows like | R1 | ... | ... |
    table_rule_ids = set()
    in_table = False
    for line in content.split("\n"):
        stripped = line.strip()
        if "Rule Enforcement" in line or "规则执行表" in line:
            in_table = True
            continue
        if in_table and stripped.startswith("|"):
            # Parse first column as rule id
            cols = [c.strip() for c in stripped.split("|")]
            # cols[0] is empty (before first |), cols[1] is rule id
            if len(cols) >= 2:
                rule_id = cols[1].strip()
                if rule_id and rule_id != "规则 id" and not rule_id.startswith("---"):
                    table_rule_ids.add(rule_id)
        elif in_table and not stripped.startswith("|") and stripped:
            # Left the table section
            if not stripped.startswith("#"):
                continue
            break

    registry_ids = set(REGISTRY.keys())

    # Table has code but no implementation
    for rid in sorted(table_rule_ids - registry_ids):
        violations.append(
            f"R4 CLAUDE.md — 规则表有 {rid} 但 linter 无实现；"
            f"修复：在 lint_rules.py 注册 {rid} 实现，或从表中移除"
        )

    # Implementation exists but not in table
    for rid in sorted(registry_ids - table_rule_ids):
        violations.append(
            f"R4 CLAUDE.md — linter 有 {rid} 实现但规则表未列出；"
            f"修复：在 CLAUDE.md 规则表补一行 {rid}"
        )

    return violations


# ── Registry ─────────────────────────────────────────────────────────

REGISTRY = {
    "R1": check_r1,
    "R2": check_r2,
    "R3": check_r3,
    "R4": check_r4,
}


# ── Main ─────────────────────────────────────────────────────────────

def main():
    report_mode = "--report" in sys.argv

    all_violations = []
    r1_shrink = []
    baseline = _load_baseline()
    r1_baseline_count = sum(1 for k in baseline if k[0] == "R1")

    for rule_id, checker in sorted(REGISTRY.items()):
        if rule_id == "R1":
            viols, shrink = checker(baseline)
            all_violations.extend(viols)
            r1_shrink = shrink
        else:
            viols = checker()
            all_violations.extend(viols)

    # Count R1 hits the same way check_r1 does — raw pattern matches minus the
    # documented exemptions. Counting raw matches here reported 18 while only 6
    # were actionable, which reads like missing violations that do not exist.
    r1_current_hits = 0
    for relpath_info in _walk_backend_py(exclude_tests=True, exclude_platform_support=True):
        if relpath_info in R1_EXEMPT_FILES:
            continue
        filepath = REPO_ROOT / relpath_info
        literals = _extract_py_string_literals(filepath)
        for _, text in literals:
            if R1_PATTERN.search(text) and not R1_EXCLUDE_PATTERN.search(text):
                r1_current_hits += 1
    for relpath_info in _walk_frontend_src(exclude_tests=True):
        if relpath_info in R1_EXEMPT_FILES:
            continue
        filepath = REPO_ROOT / relpath_info
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                source = f.read()
        except (UnicodeDecodeError, OSError):
            continue
        stripped = _strip_js_comments(source)
        source_lines = stripped.split("\n")
        strings = _extract_js_strings(stripped)
        for lineno, text in strings:
            if not R1_PATTERN.search(text) or R1_EXCLUDE_PATTERN.search(text):
                continue
            if _r1_guarded(source_lines, lineno):
                continue
            r1_current_hits += 1

    # Print violations
    for v in all_violations:
        print(v)

    if report_mode:
        print()
        print("=" * 60)
        print("Lint Rules Report")
        print("=" * 60)
        print(f"R1 baseline 收录数: {r1_baseline_count}")
        print(f"R1 当前命中数:     {r1_current_hits}")
        print(f"R1 新增违规数:     {sum(1 for v in all_violations if v.startswith('R1 '))}")
        print(f"R1 shrink 可收缩:  {len(r1_shrink)}")
        if r1_shrink:
            print()
            print("Shrink 清单（baseline 条目已不在代码中，可安全删除）:")
            for s in r1_shrink:
                print(f"  {s}")
        print()
        print(f"R2 违规数: {sum(1 for v in all_violations if v.startswith('R2 '))}")
        print(f"R3 违规数: {sum(1 for v in all_violations if v.startswith('R3 '))}")
        print(f"R4 违规数: {sum(1 for v in all_violations if v.startswith('R4 '))}")
        print(f"总违规数:  {len(all_violations)}")

    sys.exit(1 if all_violations else 0)


if __name__ == "__main__":
    main()
