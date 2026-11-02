import tempfile
from pathlib import Path
import unittest
import uuid

from fastapi import HTTPException
from backend.platform_store import PlatformStore, Resource
from backend.project_memory import capture, render
from backend.task_context import build_task_context
from backend.task_brief import build_brief


class ProjectMemoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = PlatformStore(Path(self.folder.name) / 'resources.json')
        self.project = str(uuid.uuid4())
        self.body = dict(name='Language', content='中文，完整成品。', project_id=self.project,
                         memory_type='constraint', pinned=True)

    def test_full_versions_frozen_across_edit_and_archive(self):
        original = self.store.put('memories', self.body)
        frozen = capture(self.project, self.store)
        changed = self.store.put('memories', {**self.body, 'content':'英文，完整成品。', 'expected_revision':1}, original['id'])
        self.assertEqual(changed['revision'], 2)
        self.assertEqual([v['content'] for v in changed['versions']], ['中文，完整成品。','英文，完整成品。'])
        self.assertIn('中文，完整成品。', render(frozen, self.project)[0])
        self.assertIn('英文，完整成品。', render(capture(self.project, self.store), self.project)[0])
        with self.assertRaises(HTTPException) as error:
            self.store.put('memories', {**self.body, 'expected_revision':1}, original['id'])
        self.assertEqual(error.exception.status_code,409)
        self.store.archive('memories', original['id'])
        self.assertFalse(capture(self.project, self.store)['entries'])
        self.assertIn('中文，完整成品。', render(frozen, self.project)[0])
        self.store.archive('memories', original['id'], False)
        self.assertEqual(capture(self.project, self.store)['entries'][0]['revision'],4)

    def test_isolation_full_read_budget_and_digest(self):
        self.store.put('memories', self.body)
        self.store.put('memories', {**self.body, 'project_id':str(uuid.uuid4()), 'content':'别的项目'})
        self.store.put('memories', dict(name='Reference', content='不要自动引用', project_id=self.project))
        snapshot=capture(self.project,self.store)
        self.assertEqual(len(snapshot['entries']),1)
        text, refs=render(snapshot,self.project)
        self.assertNotIn('别的项目', text)
        self.assertEqual(refs[0]['included_characters'],len(self.body['content']))
        with self.assertRaises(HTTPException):
            self.store.put('memories',{**self.body,'content':'x'*8001})
        with self.assertRaises(HTTPException):
            render(snapshot,str(uuid.uuid4()))
        snapshot['entries'][0]['content']='tampered'
        with self.assertRaises(HTTPException):
            render(snapshot,self.project)

    def test_new_task_includes_pins_without_old_conversation(self):
        self.store.put('memories', self.body)
        job=dict(id=str(uuid.uuid4()), mode='group', group_context_mode='fresh', project_id=self.project,
                 group_room_id=str(uuid.uuid4()), prompt='新任务', project_memory=capture(self.project,self.store))
        context=build_task_context({},job)
        self.assertEqual(context['project_memory']['entries'][0]['content'],self.body['content'])
        self.assertEqual(build_brief(context)['project_memory']['sha256'],job['project_memory']['sha256'])

    def test_only_explicit_project_constraints_can_be_pinned(self):
        for changes in [{'project_id':None},{'memory_type':'reference'}]:
            with self.assertRaises(HTTPException):
                self.store.put('memories',{**self.body,**changes})
        with self.assertRaises(HTTPException):
            self.store.put('knowledge',self.body)
        item=self.store.put('memories',dict(name='Model generated',content='A result'))
        self.assertFalse(item['pinned'])
        self.assertEqual(item['memory_type'],'reference')
        self.assertTrue(set(Resource.model_fields) <= set(item))

    def test_import_cannot_bypass_full_project_memory_budget(self):
        before=self.store.read()
        with self.assertRaises(HTTPException):
            self.store.import_resources([('memories',{**self.body,'content':'x'*8001},str(uuid.uuid4()))],'rename')
        self.assertEqual(self.store.read(),before)

    def test_archiving_legacy_memory_preserves_original_version(self):
        item=self.store.put('memories',self.body)
        data=self.store.read();data['memories'][item['id']].pop('versions');data['memories'][item['id']].pop('revision');self.store.save(data)
        self.store.archive('memories',item['id'])
        versions=self.store.get('memories',item['id'],allow_archived=True)['versions']
        self.assertEqual([version['revision'] for version in versions],[1,2])
        self.assertFalse(versions[0]['archived'])
        self.assertTrue(versions[1]['archived'])
