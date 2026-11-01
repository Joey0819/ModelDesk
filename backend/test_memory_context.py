import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend import super_platform as platform
from backend.memory_context import pack_context, memory_source, result_memory
from backend.platform_store import PlatformStore


class MemoryContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = PlatformStore(Path(self.temp.name) / 'resources.json')
        self.patcher = patch.object(platform, 'store', self.store)
        self.patcher.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.patcher.stop)

    def job(self, **changes):
        return {'id': 'original-job', 'mode': 'group', 'models': ['a'], 'prompt': '完成图文',
                'remember': True, 'status': 'complete', 'result': {'delivery': {
                    'status': 'partial', 'content': '长稿正文' * 9000 + '末尾修订',
                    'limitations': ['图片未生成，不能算完整交付'], 'sources': ['original-file']}}, **changes}

    def test_long_partial_result_persists_full_valid_json_and_caveats(self):
        job = self.job()
        saved = platform.remember_result(job)
        record = json.loads(self.store.get('memories', saved['id'])['content'])
        self.assertEqual(record['content'], job['result']['delivery']['content'])
        self.assertEqual(record['limitations'], ['图片未生成，不能算完整交付'])
        self.assertEqual(record['sources'], ['original-file'])
        self.assertEqual(record['request'], job['prompt'])
        material, refs, truncated = platform.context_material({**job, 'memory_ids': [saved['id']]})
        value = json.loads(material)
        self.assertEqual(value['source']['result_state']['delivery_status'], 'partial')
        self.assertIn('图片未生成', value['source']['result_state']['limitations'][0])
        self.assertTrue(truncated)
        self.assertTrue(refs[0]['truncated'])
        self.assertLessEqual(len(material), 8000)
        self.assertTrue(record['content'].endswith('末尾修订'))

    def test_manual_5000_character_memory_is_no_longer_silently_cut_at_3000(self):
        memory = self.store.put('memories', {'name': '完整资料', 'content': '字' * 4998 + '结尾'})
        material, refs, truncated = platform.context_material(self.job(memory_ids=[memory['id']]))
        self.assertEqual(json.loads(material)['text'], memory['content'])
        self.assertEqual(refs[0]['included_characters'], 5000)
        self.assertFalse(truncated)

    def test_budget_reports_actual_included_and_omitted_sources(self):
        sources = [{'text': '"\\\n' * 5000, 'reference': {'kind': 'memory', 'id': str(i)}} for i in range(2)]
        material, refs, truncated = pack_context(sources, budget=1000)
        self.assertLessEqual(len(material), 1000)
        parsed = json.loads(material)
        self.assertEqual(parsed['text'], sources[0]['text'][:refs[0]['included_characters']])
        self.assertFalse(refs[1]['included'])
        self.assertEqual(refs[1]['included_characters'], 0)
        self.assertTrue(truncated)

    def test_selected_memory_has_priority_and_is_not_duplicated_by_retrieval(self):
        memory = self.store.put('memories', {'name': '主题', 'content': '主题正文'})
        knowledge = self.store.put('knowledge', {'name': '主题文档', 'content': '主题\n' * 6000})
        _, refs, _ = platform.context_material(self.job(prompt='主题', memory_ids=[memory['id'], memory['id']],
            knowledge_ids=[knowledge['id']], workflow={'memory': True}))
        self.assertEqual(refs[0]['id'], memory['id'])
        self.assertEqual(sum(r['id'] == memory['id'] for r in refs), 1)

    def test_knowledge_chunk_preserves_full_document_fingerprint(self):
        from backend.memory_context import digest
        document = self.store.put('knowledge', {'name': '主题', 'content': '主题材料。\n' * 1000})
        _, refs, _ = platform.context_material(self.job(prompt='主题', knowledge_ids=[document['id']]))
        self.assertGreater(len(refs), 1)
        self.assertEqual(refs[0]['source_sha256'], document['sha256'])
        self.assertEqual(refs[0]['source_characters'], len(document['content']))
        self.assertNotEqual(refs[0]['source_sha256'], refs[0]['excerpt_sha256'])
        self.assertEqual(refs[0]['excerpt_sha256'], digest(refs[0]['content']))

    def test_edited_memory_does_not_retain_stale_result_status(self):
        saved = platform.remember_result(self.job())
        self.assertIn('result_state', memory_source(saved)['reference'])
        saved['content'] = '用户手动修正后的事实'
        self.assertNotIn('result_state', memory_source(saved)['reference'])

    def test_huge_control_metadata_is_omitted_instead_of_silently_losing_caveats(self):
        source = {'text': '正文', 'reference': {'result_state': {'limitations': ['x' * 2000]}}}
        material, refs, truncated = pack_context([source], budget=1000)
        self.assertEqual(material, '')
        self.assertFalse(refs[0]['included'])
        self.assertTrue(truncated)

    def test_no_implicit_long_term_save(self):
        self.assertIsNone(platform.remember_result(self.job(remember=False)))
        self.assertEqual(self.store.list('memories'), [])
