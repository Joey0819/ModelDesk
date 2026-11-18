import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from backend import coordination, group_delivery, skill_library, workbench


class DeliveryWritingReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_reviewer_must_quote_this_version_and_unresolved_filler_stays_partial(self):
        for repair in (True, False):
            with self.subTest(repair=repair), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                members = [{"id": role, "name": role, "kind": "model", "model": role, "role": role}
                           for role in ("author", "reviewer")]
                room = str(uuid4())
                job = {"id": str(uuid4()), "project_id": None, "conversation_id": room, "group_room_id": room,
                       "models": ["author", "reviewer"], "mode": "group", "events": [], "status": "running",
                       "prompt": "写一句具体工作进展。", "group_delivery_mode": "auto", "group_broadcast": True,
                       "group_harness_version": 1, "group_output_format": "text", "group_recipient_member_ids": ["author", "reviewer"],
                       "group_team_snapshot": {"members": members, "writing_review": True}}
                manager = workbench.JobManager(root / "jobs")
                manager.jobs[job["id"]] = job
                manager.wakeups[job["id"]] = asyncio.Event()
                guide = "Identify the person and the observed outcome."
                vague = "大家都说效果非常好。"
                specific = "编辑今天核对了 4 份表格。"
                drafts = []
                review_calls = []

                async def provider(job, model, system, prompt, **kwargs):
                    if "你是项目统筹者" in system:
                        self.assertNotIn(guide, system)
                        reply = {"title": "工作进展", "requirements": [{"id": "body", "description": "一句进展"}],
                                 "steps": [{"id": "draft", "member_id": "author", "instruction": "写一句话", "expected_output": "进展"}]}
                    elif "最终交付编辑" in system:
                        self.assertIn(guide, system)
                        drafts.append(specific if repair and drafts else vague)
                        reply = {"title": "进展", "content": drafts[-1], "missing_outputs": []}
                    elif "你是交付审查者" in system:
                        self.assertIn(guide, system)
                        self.assertEqual(model, "reviewer")
                        actual = drafts[-1]
                        excerpt = actual if review_calls else "Invented quote outside the artifact"
                        review_calls.append(excerpt)
                        passed = actual == specific
                        reply = {"goal_met": True, "checks": [{"requirement_id": "body", "met": True,
                                 "evidence_status": "verified", "reason": "有一句进展"}], "missing_requirements": [],
                                 "writing_review": {"met": passed, "excerpt": excerpt, "reason": "交代了人员和数量" if passed else "没有具体人员或结果"},
                                 "revision_instructions": "" if passed else "写明编辑核对了四份表格。"}
                    else:
                        self.assertNotIn(guide, system)
                        return specific, False
                    return json.dumps(reply, ensure_ascii=False), False

                with patch.object(skill_library, "DIRECTORY", root / "skills"):
                    installed = skill_library.put_skill(skill_library.WRITING_SKILL, "写作", guide, revision="frozen-revision")
                    job["_skill_snapshots"] = skill_library.frozen_skills(members, writing_review=True)
                    skill_library.put_skill(skill_library.WRITING_SKILL, "写作", "New instructions", revision="later")
                    with patch.object(group_delivery, "model_output", side_effect=provider):
                        result = await coordination.run(manager, copy.deepcopy(job))
                delivery = result["delivery"]
                self.assertEqual(len(drafts), 2)
                self.assertEqual(len(review_calls), 3)
                self.assertEqual(len(delivery["reviews"]), 2)
                self.assertEqual(delivery["status"], "complete" if repair else "partial")
                writing = delivery["reviews"][-1]["writing_review"]
                self.assertEqual(writing["excerpt"], delivery["content"])
                self.assertEqual(writing["met"], repair)
                self.assertEqual(writing["sha256"], installed["sha256"])
                self.assertEqual(writing["revision"], "frozen-revision")
                if not repair:
                    self.assertIn("写作复核：没有具体人员或结果", delivery["limitations"])
