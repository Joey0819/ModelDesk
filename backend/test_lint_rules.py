"""Tests for lint rules checker.

Uses importlib to load scripts/lint_rules.py by path (no __init__.py needed).
All fixtures use real historical failure cases to prove each rule catches issues.
Only standard library; no network access.
"""
import ast
import importlib.util
import json
import os
import sys
import tempfile
import textwrap
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

# Load lint_rules module by path
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "lint_rules.py"
_spec = importlib.util.spec_from_file_location("lint_rules", _SCRIPT_PATH)
lint_rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint_rules)


class TestR1PlatformCopy(unittest.TestCase):
    """R1: detect Mac/macOS in user-facing string literals with ratchet baseline."""

    def test_real_backend_hit_detected(self):
        """Real文案『模型连接只能在这台 Mac 的 ModelDesk 中管理』must be caught.

        The sentence is kept here as a fixture rather than read back from
        model_connections.py: that file has since been migrated to
        machine_label(), and a rule test that only passes while the bug is still
        in the tree fails the moment somebody fixes the bug. The historical
        literal is the evidence that matters, so it lives in the test.
        """
        historical = 'raise HTTPException(403, "模型连接只能在这台 Mac 的 ModelDesk 中管理")'
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py",
            dir=str(lint_rules.REPO_ROOT / "backend"), delete=False,
        ) as f:
            f.write(historical + "\n")
            tmpname = f.name
        try:
            literals = lint_rules._extract_py_string_literals(Path(tmpname))
            mac_strings = [t for _, t in literals if lint_rules.R1_PATTERN.search(t)]
            found = any("模型连接只能在这台 Mac 的 ModelDesk 中管理" in s for s in mac_strings)
            self.assertTrue(
                found,
                "R1 failed to flag the real historical sentence; "
                f"extracted literals were {mac_strings}",
            )
        finally:
            os.unlink(tmpname)

    def test_migrated_production_copy_uses_machine_label(self):
        """The sentence above is historical: production code must not carry it."""
        filepath = lint_rules.REPO_ROOT / "backend" / "model_connections.py"
        if not filepath.exists():
            self.skipTest("model_connections.py not present")
        source = filepath.read_text(encoding="utf-8")
        self.assertNotIn(
            "模型连接只能在这台 Mac 的 ModelDesk 中管理",
            source,
            "model_connections.py regressed to hardcoded Mac copy; use machine_label()",
        )

    def test_neutral_text_not_flagged(self):
        """Neutral text『运行 ModelDesk 的这台电脑』should NOT match R1 pattern."""
        neutral = "运行 ModelDesk 的这台电脑"
        match = lint_rules.R1_PATTERN.search(neutral)
        self.assertIsNone(match, f"Neutral text should not match R1 pattern, got: {match}")

    def test_baseline_match_passes(self):
        """A hit that exists in baseline should NOT produce a violation."""
        baseline = {
            ("R1", "backend/fake.py", "这台 Mac 上的服务"): {"rule": "R1"}
        }
        # Create a temp file with the matching string
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", dir=str(lint_rules.REPO_ROOT / "backend"), delete=False) as f:
            f.write('x = "这台 Mac 上的服务"\n')
            tmpname = f.name
        try:
            relpath = os.path.relpath(tmpname, lint_rules.REPO_ROOT)
            baseline_key = ("R1", relpath, "这台 Mac 上的服务")
            baseline[baseline_key] = {"rule": "R1"}
            viols, shrink = lint_rules.check_r1(baseline)
            new_viols = [v for v in viols if relpath in v]
            self.assertEqual(len(new_viols), 0, f"Baseline match should pass, got: {new_viols}")
        finally:
            os.unlink(tmpname)

    def test_shrink_reported_not_failure(self):
        """When baseline has entry but code doesn't, report shrink without failure."""
        # Build a baseline that covers ALL current real hits plus one phantom entry
        full_baseline = {}
        # Scan real code to build complete baseline
        # Build the baseline the same way check_r1 decides what is a hit, so
        # exempted capability statements are not mistaken for shrink candidates.
        for relpath in lint_rules._walk_backend_py(exclude_tests=True, exclude_platform_support=True):
            if relpath in lint_rules.R1_EXEMPT_FILES:
                continue
            filepath = lint_rules.REPO_ROOT / relpath
            literals = lint_rules._extract_py_string_literals(filepath)
            for _, text in literals:
                if not lint_rules.R1_PATTERN.search(text):
                    continue
                if lint_rules.R1_EXCLUDE_PATTERN.search(text):
                    continue
                fp = text.strip()
                full_baseline[("R1", relpath, fp)] = {"rule": "R1"}
        for relpath in lint_rules._walk_frontend_src(exclude_tests=True):
            if relpath in lint_rules.R1_EXEMPT_FILES:
                continue
            filepath = lint_rules.REPO_ROOT / relpath
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    source = f.read()
            except (UnicodeDecodeError, OSError):
                continue
            stripped = lint_rules._strip_js_comments(source)
            source_lines = stripped.split("\n")
            strings = lint_rules._extract_js_strings(stripped)
            for lineno, text in strings:
                if not lint_rules.R1_PATTERN.search(text):
                    continue
                if lint_rules.R1_EXCLUDE_PATTERN.search(text):
                    continue
                if lint_rules._r1_guarded(source_lines, lineno):
                    continue
                fp = text.strip()
                full_baseline[("R1", relpath, fp)] = {"rule": "R1"}
        # Add phantom entry that doesn't exist in code
        full_baseline[("R1", "backend/nonexistent_removed.py", "旧的 Mac 文案")] = {"rule": "R1"}
        viols, shrink = lint_rules.check_r1(full_baseline)
        self.assertEqual(len(viols), 0, f"Shrink should not produce violations, got: {viols[:3]}")
        self.assertGreater(len(shrink), 0, "Should report at least one shrink entry")
        named = [s for s in shrink if "nonexistent_removed.py" in s]
        self.assertTrue(named, f"Shrink should name the phantom file, got: {shrink[:3]}")

    def test_new_violation_without_baseline(self):
        """New Mac string NOT in baseline must be flagged as violation."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", dir=str(lint_rules.REPO_ROOT / "backend"), delete=False) as f:
            f.write('msg = "请在这台 Mac 操作"\n')
            tmpname = f.name
        try:
            relpath = os.path.relpath(tmpname, lint_rules.REPO_ROOT)
            viols, shrink = lint_rules.check_r1(baseline={})
            new_viols = [v for v in viols if relpath in v]
            self.assertGreater(len(new_viols), 0, "New Mac string should be flagged")
            self.assertIn("machine_label", new_viols[0], "Error must mention machine_label() fix")
        finally:
            os.unlink(tmpname)


class TestR2BareSuppression(unittest.TestCase):
    """R2: every suppression comment needs reason + valid expires."""

    def test_valid_suppression_passes(self):
        """Suppression with reason=... expires=2099-01-01 should pass."""
        line = '// eslint-disable next-line reason=legacy API expires=2099-01-01'
        # Mock file scanning to return just this line
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", dir=str(lint_rules.REPO_ROOT / "backend"), delete=False) as f:
            f.write(f'{line}\nx = 1\n')
            tmpname = f.name
        try:
            relpath = os.path.relpath(tmpname, lint_rules.REPO_ROOT)
            orig_walk = lint_rules._walk_backend_py
            def mock_walk(*a, **kw):
                yield relpath
            with patch.object(lint_rules, '_walk_backend_py', side_effect=lambda *a, **kw: [relpath] if not kw.get('exclude_tests') else []):
                with patch.object(lint_rules, '_walk_frontend_src', return_value=[]):
                    viols = lint_rules.check_r2()
            file_viols = [v for v in viols if relpath in v]
            self.assertEqual(len(file_viols), 0, f"Valid suppression should pass, got: {file_viols}")
        finally:
            os.unlink(tmpname)

    def test_bare_eslint_disable_flagged(self):
        """Bare eslint-disable without reason/expires must be flagged."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", dir=str(lint_rules.REPO_ROOT / "backend"), delete=False) as f:
            # Write as actual Python comment, not string literal
            f.write('# eslint-disable next-line\nx = 1\n')
            tmpname = f.name
        try:
            relpath = os.path.relpath(tmpname, lint_rules.REPO_ROOT)
            with patch.object(lint_rules, '_walk_backend_py', side_effect=lambda *a, **kw: [relpath]):
                with patch.object(lint_rules, '_walk_frontend_src', return_value=[]):
                    viols = lint_rules.check_r2()
            file_viols = [v for v in viols if relpath in v]
            self.assertGreater(len(file_viols), 0, "Bare eslint-disable should be flagged")
            self.assertIn("reason", file_viols[0], "Error must mention missing reason")
        finally:
            os.unlink(tmpname)

    def test_empty_reason_flagged(self):
        """reason= (empty) must be flagged."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", dir=str(lint_rules.REPO_ROOT / "backend"), delete=False) as f:
            # Write as actual Python comment with empty reason
            f.write('# noqa reason= expires=2099-01-01\nx = 1\n')
            tmpname = f.name
        try:
            relpath = os.path.relpath(tmpname, lint_rules.REPO_ROOT)
            with patch.object(lint_rules, '_walk_backend_py', side_effect=lambda *a, **kw: [relpath]):
                with patch.object(lint_rules, '_walk_frontend_src', return_value=[]):
                    viols = lint_rules.check_r2()
            file_viols = [v for v in viols if relpath in v]
            self.assertGreater(len(file_viols), 0, "Empty reason should be flagged")
        finally:
            os.unlink(tmpname)

    def test_expired_date_flagged(self):
        """expires=2020-01-01 (past) must be flagged."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", dir=str(lint_rules.REPO_ROOT / "backend"), delete=False) as f:
            f.write('# type: ignore reason=legacy expires=2020-01-01\nx = 1\n')
            tmpname = f.name
        try:
            relpath = os.path.relpath(tmpname, lint_rules.REPO_ROOT)
            with patch.object(lint_rules, '_walk_backend_py', side_effect=lambda *a, **kw: [relpath]):
                with patch.object(lint_rules, '_walk_frontend_src', return_value=[]):
                    viols = lint_rules.check_r2()
            file_viols = [v for v in viols if relpath in v]
            self.assertGreater(len(file_viols), 0, "Expired date should be flagged")
            self.assertIn("expires", file_viols[0], "Error must mention expires issue")
        finally:
            os.unlink(tmpname)


class TestR3ReturnArity(unittest.TestCase):
    """R3: detect functions returning tuples of varying lengths."""

    def test_mixed_arity_flagged(self):
        """Function returning (a,b) and (a,b,c) must be flagged."""
        source = textwrap.dedent('''\
            def process(flag):
                if flag:
                    return (1, 2)
                else:
                    return (1, 2, 3)
        ''')
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", dir=str(lint_rules.REPO_ROOT / "backend"), delete=False) as f:
            f.write(source)
            tmpname = f.name
        try:
            relpath = os.path.relpath(tmpname, lint_rules.REPO_ROOT)
            orig_walk = lint_rules._walk_backend_py
            with patch.object(lint_rules, '_walk_backend_py', return_value=[relpath]):
                viols = lint_rules.check_r3()
            file_viols = [v for v in viols if relpath in v]
            self.assertGreater(len(file_viols), 0, "Mixed arity should be flagged")
            self.assertIn("process", file_viols[0], "Error must name the function")
        finally:
            os.unlink(tmpname)

    def test_uniform_arity_passes(self):
        """Function always returning 2-tuple should pass."""
        source = textwrap.dedent('''\
            def get_pair(x):
                if x > 0:
                    return (x, x*2)
                return (0, 0)
        ''')
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", dir=str(lint_rules.REPO_ROOT / "backend"), delete=False) as f:
            f.write(source)
            tmpname = f.name
        try:
            relpath = os.path.relpath(tmpname, lint_rules.REPO_ROOT)
            with patch.object(lint_rules, '_walk_backend_py', return_value=[relpath]):
                viols = lint_rules.check_r3()
            file_viols = [v for v in viols if relpath in v]
            self.assertEqual(len(file_viols), 0, f"Uniform arity should pass, got: {file_viols}")
        finally:
            os.unlink(tmpname)

    def test_none_and_single_pass(self):
        """Mixing bare return / return None / single value should pass."""
        source = textwrap.dedent('''\
            def maybe(x):
                if x is None:
                    return
                if x < 0:
                    return None
                return x
        ''')
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", dir=str(lint_rules.REPO_ROOT / "backend"), delete=False) as f:
            f.write(source)
            tmpname = f.name
        try:
            relpath = os.path.relpath(tmpname, lint_rules.REPO_ROOT)
            with patch.object(lint_rules, '_walk_backend_py', return_value=[relpath]):
                viols = lint_rules.check_r3()
            file_viols = [v for v in viols if relpath in v]
            self.assertEqual(len(file_viols), 0, f"Non-tuple returns should pass, got: {file_viols}")
        finally:
            os.unlink(tmpname)

    def test_nested_function_not_counted(self):
        """Nested function with different arity should not cause parent to fail."""
        source = textwrap.dedent('''\
            def outer(x):
                def inner(y):
                    return (y, y+1, y+2)
                return (x, inner(x))
        ''')
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", dir=str(lint_rules.REPO_ROOT / "backend"), delete=False) as f:
            f.write(source)
            tmpname = f.name
        try:
            relpath = os.path.relpath(tmpname, lint_rules.REPO_ROOT)
            with patch.object(lint_rules, '_walk_backend_py', return_value=[relpath]):
                viols = lint_rules.check_r3()
            file_viols = [v for v in viols if "outer" in v and relpath in v]
            self.assertEqual(len(file_viols), 0, f"Nested func arity should not affect parent, got: {file_viols}")
        finally:
            os.unlink(tmpname)


class TestR4RuleTable(unittest.TestCase):
    """R4: CLAUDE.md rule table must match REGISTRY bidirectionally."""

    def test_table_has_unimplemented_rule(self):
        """Table lists R9 but REGISTRY has no R9 → violation."""
        fake_content = textwrap.dedent('''\
            ## Rule Enforcement / 规则执行表
            | 规则 id | 拦什么 | 执行者 |
            |---------|--------|--------|
            | R1 | platform-copy | scripts/lint_rules.py |
            | R9 | phantom rule | scripts/lint_rules.py |
        ''')
        with patch.object(lint_rules, 'CLAUDE_MD_PATH') as mock_path:
            mock_path.exists.return_value = True
            m = unittest.mock.mock_open(read_data=fake_content)
            with patch('builtins.open', m):
                viols = lint_rules.check_r4()
        r9_viols = [v for v in viols if 'R9' in v]
        self.assertGreater(len(r9_viols), 0, "Unimplemented R9 in table should be flagged")
        self.assertIn("linter 无实现", r9_viols[0], "Error must mention missing implementation")

    def test_impl_missing_from_table(self):
        """REGISTRY has R5 but table doesn't list it → violation."""
        fake_content = textwrap.dedent('''\
            ## Rule Enforcement / 规则执行表
            | 规则 id | 拦什么 | 执行者 |
            |---------|--------|--------|
            | R1 | platform-copy | scripts/lint_rules.py |
        ''')
        # Temporarily add R5 to registry
        original_registry = dict(lint_rules.REGISTRY)
        lint_rules.REGISTRY["R5"] = lambda: []
        try:
            with patch.object(lint_rules, 'CLAUDE_MD_PATH') as mock_path:
                mock_path.exists.return_value = True
                m = unittest.mock.mock_open(read_data=fake_content)
                with patch('builtins.open', m):
                    viols = lint_rules.check_r4()
            r5_viols = [v for v in viols if 'R5' in v]
            self.assertGreater(len(r5_viols), 0, "R5 impl without table entry should be flagged")
            self.assertIn("规则表未列出", r5_viols[0], "Error must mention missing table entry")
        finally:
            lint_rules.REGISTRY.clear()
            lint_rules.REGISTRY.update(original_registry)

    def test_matching_table_passes(self):
        """When table and REGISTRY match exactly, no violations."""
        # Build table content from actual REGISTRY
        rows = "\n".join(
            f"| {rid} | desc | scripts/lint_rules.py |"
            for rid in sorted(lint_rules.REGISTRY.keys())
        )
        fake_content = f"## Rule Enforcement / 规则执行表\n| 规则 id | 拦什么 | 执行者 |\n|---------|--------|--------|\n{rows}\n"
        with patch.object(lint_rules, 'CLAUDE_MD_PATH') as mock_path:
            mock_path.exists.return_value = True
            m = unittest.mock.mock_open(read_data=fake_content)
            with patch('builtins.open', m):
                viols = lint_rules.check_r4()
        self.assertEqual(len(viols), 0, f"Matching table should pass, got: {viols}")


if __name__ == "__main__":
    unittest.main()
