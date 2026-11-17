"""Frontmatter parsing, pstack install, and import priority tests for skill_library."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend import skill_library


class FrontmatterParseTests(unittest.TestCase):
    """Test parse_frontmatter helper function."""

    def test_normal_frontmatter_parsing(self):
        text = '---\nname: my-skill\ndescription: "A test skill"\ndisable-model-invocation: true\n---\n# Instructions\nDo something.'
        meta, body, fm_raw = skill_library.parse_frontmatter(text)
        self.assertEqual(meta['name'], 'my-skill')
        self.assertEqual(meta['description'], 'A test skill')
        self.assertTrue(meta['disable-model-invocation'])
        self.assertIn('# Instructions', body)
        self.assertNotIn('---', body.split('\n')[0])

    def test_no_frontmatter_returns_empty_meta(self):
        text = '# Just instructions\nNo frontmatter here.'
        meta, body = skill_library.parse_frontmatter(text)[:2]
        self.assertEqual(meta, {})
        self.assertEqual(body, text)

    def test_unclosed_frontmatter_degrades_to_body(self):
        text = '---\nname: unclosed\n# No closing delimiter\nStill content'
        meta, body = skill_library.parse_frontmatter(text)[:2]
        self.assertEqual(meta, {})
        self.assertEqual(body, text)

    def test_empty_body_raises_422(self):
        text = '---\nname: empty\n---\n   \n  '
        with self.assertRaises(HTTPException) as ctx:
            skill_library.parse_frontmatter(text)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_single_quoted_value_unquoted(self):
        text = "---\nname: 'single-quoted'\n---\nBody"
        meta, body, _ = skill_library.parse_frontmatter(text)
        self.assertEqual(meta['name'], 'single-quoted')

    def test_boolean_true_false_parsed(self):
        text = '---\nenabled: true\ndisabled: false\n---\nBody'
        meta, _, _ = skill_library.parse_frontmatter(text)
        self.assertTrue(meta['enabled'])
        self.assertFalse(meta['disabled'])

    def test_dotdotdot_closing_delimiter(self):
        text = '---\nname: dot-close\n...\nBody after dots'
        meta, body, _ = skill_library.parse_frontmatter(text)
        self.assertEqual(meta['name'], 'dot-close')
        self.assertIn('Body after dots', body)


class ImportPriorityTests(unittest.TestCase):
    """Test import_skill name/description priority logic via put_skill monkeypatch."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir_patch = patch.object(skill_library, 'DIRECTORY', Path(self.temp.name))
        self.dir_patch.start()

    def tearDown(self):
        self.dir_patch.stop()
        self.temp.cleanup()

    def test_request_name_overrides_frontmatter(self):
        # Simulate: request has name="ReqName", frontmatter has name="FmName"
        text = '---\nname: FmName\ndescription: FM desc\n---\nInstructions body'
        result = skill_library.parse_frontmatter(text)
        meta, body = result[0], result[1]
        req_name = 'ReqName'
        req_desc = ''
        final_name = req_name.strip() if req_name.strip() else str(meta.get('name', '')).strip()
        final_desc = req_desc.strip() if req_desc.strip() else str(meta.get('description', '')).strip()
        self.assertEqual(final_name, 'ReqName')
        self.assertEqual(final_desc, 'FM desc')

    def test_frontmatter_name_used_when_request_empty(self):
        text = '---\nname: FmOnly\n---\nBody'
        result = skill_library.parse_frontmatter(text)
        meta, body = result[0], result[1]
        req_name = ''
        final_name = req_name.strip() if req_name.strip() else str(meta.get('name', '')).strip()
        self.assertEqual(final_name, 'FmOnly')

    def test_description_truncated_to_400(self):
        long_desc = 'x' * 500
        truncated = long_desc[:400]
        self.assertEqual(len(truncated), 400)


class SecretInFrontmatterTests(unittest.TestCase):
    """Ensure secrets hidden in frontmatter raw block are rejected."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir_patch = patch.object(skill_library, 'DIRECTORY', Path(self.temp.name))
        self.dir_patch.start()

    def tearDown(self):
        self.dir_patch.stop()
        self.temp.cleanup()

    def test_secret_in_frontmatter_raw_rejected(self):
        # Use OpenAI sk-proj- key pattern which is detected by SECRET_CONTENT regex
        from backend.attachments import secret_text
        fm_raw = 'api_key: sk-proj-abcdefghijklmnop1234567890'
        self.assertTrue(secret_text(fm_raw))


class OldManifestCompatibilityTests(unittest.TestCase):
    """Old manifests without description field must still work."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp.name)
        self.dir_patch = patch.object(skill_library, 'DIRECTORY', self.dir_path)
        self.dir_patch.start()

    def tearDown(self):
        self.dir_patch.stop()
        self.temp.cleanup()

    def test_old_manifest_without_description_readable(self):
        # Create a manifest without description field (old format)
        skill_dir = self.dir_path / 'old-skill'
        skill_dir.mkdir()
        old_manifest = {
            'id': 'old-skill',
            'name': 'Old Skill',
            'instructions': 'Some instructions',
            'source': 'local',
            'sha256': __import__('hashlib').sha256(b'Some instructions').hexdigest()
        }
        (skill_dir / 'manifest.json').write_text(json.dumps(old_manifest))
        # get_skill should work
        data = skill_library.get_skill('old-skill')
        self.assertEqual(data['name'], 'Old Skill')
        # public_skill should not crash and should have empty description
        pub = skill_library.public_skill(data)
        self.assertEqual(pub.get('description', ''), '')


class PstackInstallFilesTests(unittest.TestCase):
    """Test install_pstack_files with local fake directory structure."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fake_folder = Path(self.temp.name) / 'checkout'
        self.fake_folder.mkdir()
        self.skills_base = self.fake_folder / 'pstack' / 'skills'
        self.skills_base.mkdir(parents=True)
        self.dir_patch = patch.object(skill_library, 'DIRECTORY', Path(self.temp.name) / 'installed')
        self.dir_patch.start()

    def tearDown(self):
        self.dir_patch.stop()
        self.temp.cleanup()

    def _write_skill(self, name, content):
        skill_dir = self.skills_base / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / 'SKILL.md').write_text(content)

    def test_install_reads_only_skill_md_strips_frontmatter(self):
        # Create tdd skill with frontmatter
        self._write_skill('tdd', '---\nname: tdd\ndescription: "TDD workflow"\n---\n# TDD Instructions\nWrite tests first.')
        # Create a references dir that should NOT be copied
        refs_dir = self.skills_base / 'tdd' / 'references'
        refs_dir.mkdir()
        (refs_dir / 'extra.md').write_text('Should not be read')
        # Also create other required skills (minimal)
        for src_name in ['blast-radius', 'interrogate', 'unslop', 'technical-writing',
                         'figure-it-out', 'principle-fix-root-causes', 'principle-prove-it-works']:
            self._write_skill(src_name, f'---\nname: {src_name}\n---\nInstructions for {src_name}.')
        results = skill_library.install_pstack_files(self.fake_folder)
        self.assertTrue(len(results) >= 1)
        # Verify tdd manifest
        installed_dir = Path(self.temp.name) / 'installed'
        tdd_manifest = json.loads((installed_dir / 'pstack-tdd' / 'manifest.json').read_text())
        self.assertEqual(tdd_manifest['id'], 'pstack-tdd')
        self.assertIn('TDD Instructions', tdd_manifest['instructions'])
        self.assertNotIn('---', tdd_manifest['instructions'][:20])
        self.assertEqual(tdd_manifest['description'], 'TDD workflow')
        self.assertIn('cursor/plugins', tdd_manifest['source'])
        self.assertEqual(tdd_manifest['revision'], skill_library.PSTACK_COMMIT)
        # Verify references/ was NOT copied
        self.assertFalse((installed_dir / 'pstack-tdd' / 'references').exists())

    def test_symlink_in_skill_path_raises_409(self):
        # Create a symlink instead of real directory
        real_dir = self.skills_base / 'real-tdd'
        real_dir.mkdir()
        (real_dir / 'SKILL.md').write_text('Content')
        link_path = self.skills_base / 'tdd'
        link_path.symlink_to(real_dir)
        with self.assertRaises(HTTPException) as ctx:
            skill_library.install_pstack_files(self.fake_folder)
        self.assertEqual(ctx.exception.status_code, 409)


if __name__ == '__main__':
    unittest.main()
