"""Frontmatter parsing, pstack install, and import priority tests for skill_library."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException

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

    def test_double_quoted_backslash_unescape(self):
        text = '---\ndescription: "Use for \\"interrogate\\", x"\n---\nBody'
        meta, _, _ = skill_library.parse_frontmatter(text)
        self.assertEqual(meta['description'], 'Use for "interrogate", x')


class SkillImportTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the browser import route with manifests written to a temporary store."""

    async def asyncSetUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(skill_library, 'DIRECTORY', self.directory))
        app = FastAPI()
        app.include_router(skill_library.router)
        self.client = await self.enterAsyncContext(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=('127.0.0.1', 1234)),
            base_url='http://127.0.0.1:8001', headers={'Origin': 'http://localhost:5173'},
        ))

    async def test_frontmatter_wins_over_generic_filename_for_lf_and_crlf(self):
        for newline in ('\n', '\r\n'):
            with self.subTest(newline=repr(newline)):
                body = newline.join(('# Instructions', '  Keep original spacing.  ', ''))
                header = newline.join(('---', 'name: FmOnly', 'description: A test skill', '---', ''))
                payload = {'filename': 'SKILL.md', 'instructions': header + body}
                response = await self.client.post('/api/skills/import', json=payload)
                self.assertEqual(response.status_code, 200, response.text)
                public = response.json()
                self.assertEqual((public['name'], public['description']), ('FmOnly', 'A test skill'))
                self.assertEqual(public['source'], 'local')
                self.assertIsNone(public['revision'])
                self.assertNotIn('instructions', public)
                self.assertEqual(skill_library.get_skill(public['id'])['instructions'], body)
                self.assertEqual(public['sha256'], hashlib.sha256(body.encode()).hexdigest())
                repeated = await self.client.post('/api/skills/import', json=payload | {'name': ''})
                self.assertEqual(repeated.json()['id'], public['id'])

    async def test_manual_name_and_description_override_frontmatter(self):
        text = '---\nname: FmName\ndescription: FM desc\n---\nInstructions body'
        response = await self.client.post('/api/skills/import', json={
            'name': ' ReqName ', 'description': ' Request description ', 'filename': 'SKILL.md',
            'instructions': text,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['name'], 'ReqName')
        self.assertEqual(response.json()['description'], 'Request description')

    async def test_filename_fallback_with_no_frontmatter_name(self):
        text = '---\ndescription: ' + ('x' * 500) + '\n---\n# Plain skill'
        response = await self.client.post('/api/skills/import', json={
            'name': ' ', 'filename': r'C:\fixtures\Report drafting.MD', 'instructions': text,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['name'], 'Report drafting')
        self.assertEqual(response.json()['description'], 'x' * 400)

    async def test_secret_in_crlf_frontmatter_raw_rejected(self):
        token = '-'.join(('sk', 'proj', 'abcdefghijklmnop1234567890'))
        text = f'---\r\nname: Fixture\r\napi_key: {token}\r\n---\r\n# Clean instructions'
        response = await self.client.post('/api/skills/import', json={'instructions': text})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['detail'], 'Skill 不得包含密钥或凭据')
        self.assertEqual(list(self.directory.iterdir()), [])

    async def test_import_still_requires_local_admin(self):
        response = await self.client.post('/api/skills/import',
                                          json={'name': 'Fixture', 'instructions': '# Instructions'},
                                          headers={'Origin': 'https://fixture.invalid'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(list(self.directory.iterdir()), [])


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
            'sha256': hashlib.sha256(b'Some instructions').hexdigest()
        }
        (skill_dir / 'manifest.json').write_text(json.dumps(old_manifest))
        # get_skill should work
        data = skill_library.get_skill('old-skill')
        self.assertEqual(data['name'], 'Old Skill')
        # public_skill should not crash and should have empty description
        pub = skill_library.public_skill(data)
        self.assertEqual(pub.get('description', ''), '')


class InstallPstackEndpointTests(unittest.IsolatedAsyncioTestCase):
    """Verify install_pstack_endpoint returns dict with name and installed list."""

    async def test_endpoint_returns_dict_with_installed_list(self):
        fake_results = [{'id': 'pstack-tdd', 'name': 'TDD 缺陷修复'}]
        with patch.object(skill_library, 'install_pstack', return_value=fake_results):
            result = await skill_library.install_pstack_endpoint()
        self.assertIsInstance(result, dict)
        self.assertEqual(result['name'], 'pstack 方法论精选')
        self.assertEqual(result['installed'], fake_results)


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
        self.assertIn(skill_library.PSTACK_COMMIT, tdd_manifest['source'])
        # Verify references/ was NOT copied
        self.assertFalse((installed_dir / 'pstack-tdd' / 'references').exists())

    def test_missing_last_skill_preserves_previously_installed_version(self):
        original = skill_library.put_skill('pstack-tdd', 'TDD', 'Previously installed material')
        for source in list(skill_library.PSTACK_SKILLS)[:-1]:
            self._write_skill(source, f'New instructions for {source}')
        with self.assertRaises(FileNotFoundError):
            skill_library.install_pstack_files(self.fake_folder)
        self.assertEqual(skill_library.get_skill('pstack-tdd')['sha256'], original['sha256'])
        self.assertFalse((skill_library.DIRECTORY / 'pstack-unslop').exists())

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
