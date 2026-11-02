import hashlib
import unittest

from backend.task_brief import build_brief, role_packet
from backend.task_context import build_task_context
from backend.test_task_context import turn


class TaskBriefTests(unittest.TestCase):
    def test_revision_keeps_full_original_and_latest_correction_with_separate_provenance(self):
        original = turn(0, "保留全文、三个表格与 Word/PDF。" * 200, group_context_mode="fresh")
        failed = turn(1, "只把蓝色改成绿色", result={"delivery": {"status": "partial", "content": "真实部分稿", "limitations": ["PDF 未生成"]}})
        current = turn(2, "继续完成")
        foreign = turn(1, "FOREIGN_PRIVATE"); foreign['project_id'] = 'foreign'
        context = build_task_context({item['id']: item for item in [original, failed, foreign]}, current)
        brief = build_brief(context, requirements=[{'id':'files', 'description':'Word 与 PDF'}])
        self.assertEqual(brief['objective'], original['prompt'])
        self.assertEqual(brief['latest_instruction'], current['prompt'])
        self.assertEqual(brief['revision'], 3)
        self.assertEqual(len(brief['sources']), 3)
        self.assertEqual(brief['sources'][1]['sha256'], hashlib.sha256(failed['prompt'].encode()).hexdigest())
        self.assertEqual(brief['pending_items'], ['PDF 未生成'])
        self.assertEqual(brief['requirements_origin'], 'planner_interpretation')
        self.assertNotIn('FOREIGN_PRIVATE', str(brief))
        self.assertEqual(build_brief(context, requirements=brief['requirements']), brief)
        context['requests'][-1]['prompt'] = '取消 PDF'
        self.assertNotEqual(build_brief(context, requirements=brief['requirements'])['sha256'], brief['sha256'])

    def test_role_packet_binds_actual_dependency_bytes_and_cannot_grant_tools(self):
        brief = build_brief({}, goal='保留尾部要求', requirements=[{'id':'body'}])
        member = {'id':'review', 'role':'审稿'}
        step = {'id':'check'}
        lineage = [{'id':'write', 'output':'全文结尾必须保留'}]
        packet = role_packet(member, step, brief, lineage)
        self.assertEqual(packet['brief_sha256'], brief['sha256'])
        self.assertEqual(packet['requirement_ids'], ['body'])
        self.assertEqual(packet['authority'], 'instructions_and_sources_only_no_new_tool_permissions')
        self.assertEqual(packet['dependency_outputs'][0]['sha256'], hashlib.sha256(lineage[0]['output'].encode()).hexdigest())
        lineage[0]['output'] += 'changed'
        self.assertNotEqual(role_packet(member, step, brief, lineage)['sha256'], packet['sha256'])
