from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from pydantic import ValidationError

from backend.routine_store import RoutineBusy, RoutineConflict, RoutineForbidden, RoutineNotFound, RoutineSpec, RoutineStore


class RoutineStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "routines.sqlite3"
        self.store = RoutineStore(self.path)
        self.project = str(uuid4())
        self.room = str(uuid4())
        self.start = datetime(2026, 1, 1, 8, tzinfo=UTC)

    def create(self, trigger=None, **kwargs):
        spec = {"name": "Weekly review", "goal": "List work ready for review"}
        if trigger:
            spec["trigger"] = trigger
        request_id = kwargs.pop("request_id", str(uuid4()))
        room = kwargs.pop("room", self.room)
        project = kwargs.pop("project", self.project)
        return self.store.create(
            project, room, {**spec, **kwargs}, "team-revision", request_id, now=self.start,
        )

    def enable(self, routine, now=None):
        return self.store.set_enabled(
            routine["project_id"], routine["room_id"], routine["id"], True,
            routine["revision"], now=now or self.start,
        )

    def test_scope_and_idempotency_survive_update_and_restart(self):
        create_id = str(uuid4())
        routine = self.create(request_id=create_id)
        trial_id = str(uuid4())
        trial = self.store.manual_run(self.project, self.room, routine["id"], trial_id, now=self.start)
        updated = self.store.update(
            self.project, self.room, routine["id"], {"name": "Renamed", "goal": "Summarize reviews"},
            "new-team-revision", routine["revision"], now=self.start + timedelta(hours=1),
        )
        other = self.create(name="Other routine")
        with self.assertRaises(RoutineConflict):
            self.store.manual_run(self.project, self.room, other["id"], trial_id)
        with self.assertRaises(RoutineConflict):
            self.store.update(
                self.project, self.room, routine["id"], {"name": "Obsolete", "goal": "Old draft"},
                "obsolete", expected_revision=1,
            )
        with self.assertRaises(RoutineNotFound):
            self.store.get(None, self.room, routine["id"])

        self.store = RoutineStore(self.path)
        self.assertEqual(self.create(request_id=create_id), updated)
        self.assertEqual(routine["client_request_id"], create_id)
        self.assertEqual(trial["client_request_id"], trial_id)
        self.assertEqual(self.store.active(), [trial])
        self.assertEqual(self.store.manual_run(self.project, self.room, routine["id"], trial_id), trial)
        self.assertEqual(trial["spec"]["goal"], "List work ready for review")
        self.assertEqual(trial["team_revision"], "team-revision")
        self.assertEqual(trial["routine_revision"], 1)
        with self.assertRaises(RoutineConflict):
            self.create(name="Different payload", request_id=create_id)
        unscoped = self.create(project=None, request_id=create_id)
        self.assertEqual([item["id"] for item in self.store.list(None, self.room)], [unscoped["id"]])
        self.assertEqual({item["id"] for item in self.store.list(self.project, self.room)}, {routine["id"], other["id"]})

    def test_database_permissions_and_symlink_guard(self):
        self.create()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        link = self.path.with_name("linked.sqlite3")
        link.symlink_to(self.path)
        with self.assertRaises(OSError):
            RoutineStore(link).list(self.project, self.room)
        self.assertEqual(len(self.store.list(self.project, self.room)), 1)

    def test_pause_resume_and_restart_catch_up_latest_due_once(self):
        routine = self.create({"kind": "daily", "time": "09:00", "timezone": "UTC"})
        self.assertFalse(routine["enabled"])
        self.assertIsNone(routine["next_run_at"])
        self.assertEqual(self.store.claim_scheduled(now=self.start + timedelta(days=2)), [])
        routine = self.enable(routine, self.start + timedelta(days=2, hours=1))
        self.assertEqual(datetime.fromisoformat(routine["next_run_at"]), self.start + timedelta(days=3, hours=1))
        paused = self.store.set_enabled(
            self.project, self.room, routine["id"], False, routine["revision"],
            now=self.start + timedelta(days=3),
        )
        self.assertIsNone(paused["next_run_at"])
        self.assertEqual(self.store.claim_scheduled(now=self.start + timedelta(days=4)), [])
        self.enable(paused, self.start + timedelta(days=4, hours=2))
        self.store = RoutineStore(self.path)
        late = self.start + timedelta(days=7, hours=2)
        [occurrence] = self.store.claim_scheduled(now=late)
        self.assertEqual(datetime.fromisoformat(occurrence["scheduled_at"]), self.start + timedelta(days=7, hours=1))
        self.assertEqual(self.store.claim_scheduled(now=late), [])
        self.assertEqual(self.store.history(self.project, self.room, routine["id"]), [occurrence])
        self.assertEqual(
            datetime.fromisoformat(self.store.get(self.project, self.room, routine["id"])["next_run_at"]),
            self.start + timedelta(days=8, hours=1),
        )

    def test_daily_dst_gap_and_repeated_hour(self):
        self.start = datetime(2026, 3, 8, 0, tzinfo=UTC)
        routine = self.enable(self.create({"kind": "daily", "time": "02:30", "timezone": "America/New_York"}))
        gap = datetime(2026, 3, 8, 7, 30, tzinfo=UTC)
        self.assertEqual(datetime.fromisoformat(routine["next_run_at"]), gap)
        self.assertEqual(self.store.claim_scheduled(now=gap - timedelta(seconds=1)), [])
        [occurrence] = self.store.claim_scheduled(now=gap)
        self.store.finish(occurrence["id"], "complete", now=gap)
        self.assertEqual(
            datetime.fromisoformat(self.store.get(self.project, self.room, routine["id"])["next_run_at"]),
            datetime(2026, 3, 9, 6, 30, tzinfo=UTC),
        )
        fall = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
        updated = self.store.update(
            self.project, self.room, routine["id"],
            {"name": routine["name"], "goal": routine["goal"],
             "trigger": {"kind": "daily", "time": "01:30", "timezone": "America/New_York"}},
            "team-revision", routine["revision"], now=fall - timedelta(minutes=1),
        )
        self.assertEqual(datetime.fromisoformat(updated["next_run_at"]), fall)
        [occurrence] = self.store.claim_scheduled(now=fall)
        self.store.finish(occurrence["id"], "complete", now=fall)
        self.assertEqual(self.store.claim_scheduled(now=fall + timedelta(hours=1)), [])

    def test_weekly_catch_up_uses_local_weekday_across_utc_date(self):
        routine = self.enable(self.create({
            "kind": "weekly", "time": "23:30", "timezone": "America/Los_Angeles", "weekday": 4,
        }))
        self.assertEqual(datetime.fromisoformat(routine["next_run_at"]), datetime(2026, 1, 3, 7, 30, tzinfo=UTC))
        [occurrence] = self.store.claim_scheduled(now=datetime(2026, 1, 19, 17, tzinfo=UTC))
        self.assertEqual(datetime.fromisoformat(occurrence["scheduled_at"]), datetime(2026, 1, 17, 7, 30, tzinfo=UTC))

    def test_concurrent_claims_skip_known_busy_and_hold_until_dispatch_terminal(self):
        routine = self.enable(self.create({"kind": "daily", "time": "09:00", "timezone": "UTC"}))
        due = self.start + timedelta(hours=1)
        with ThreadPoolExecutor(max_workers=4) as executor:
            calls = [
                executor.submit(RoutineStore(self.path).claim_scheduled, now=due)
                for _ in range(4)
            ]
        [occurrence] = [claim for call in calls for claim in call.result()]
        run_id = str(uuid4())
        self.store.finish(occurrence["id"], "submitted", run_id=run_id, now=due)
        with self.assertRaises(RoutineBusy):
            self.store.manual_run(self.project, self.room, routine["id"], str(uuid4()), now=due)
        overdue = due + timedelta(days=1)
        self.assertEqual(self.store.claim_scheduled(now=overdue), [])
        skipped, submitted = self.store.history(self.project, self.room, routine["id"])
        self.assertEqual((skipped["status"], submitted["status"]), ("skipped", "submitted"))
        self.assertEqual(self.store.active(), [submitted])
        finished = self.store.finish(occurrence["id"], "complete", now=overdue)
        self.assertEqual(finished["run_id"], run_id)
        self.assertEqual(self.store.finish(occurrence["id"], "failed", now=overdue), finished)
        self.assertEqual(self.store.claim_scheduled(now=overdue), [])
        self.assertEqual(self.store.manual_run(self.project, self.room, routine["id"], str(uuid4()))["status"], "pending")

    def test_distinct_board_cards_queue_in_order_and_other_routine_proceeds(self):
        first = self.enable(self.create({"kind": "board_card", "tag": "review"}))
        second = self.enable(self.create({"kind": "board_card", "tag": "review"}, name="Second routine"))
        stamp = self.start + timedelta(minutes=1)
        events = [{
            "id": str(uuid4()), "project_id": self.project, "room_id": self.room,
            "title": title, "description": "", "tags": ["review"], "created_at": created, "local": True,
        } for created, title in ((stamp + timedelta(seconds=1), "Later"), (stamp, "Earlier"))]
        claims = self.store.claim_board_cards(events, now=stamp + timedelta(seconds=2))
        self.assertEqual(len(claims), 4)
        earlier = self.store.pending()
        self.assertEqual({item["routine_id"] for item in earlier}, {first["id"], second["id"]})
        self.assertEqual([item["event"]["title"] for item in earlier], ["Earlier", "Earlier"])
        for item in earlier:
            self.store.finish(item["id"], "submitted", run_id=str(uuid4()))
        self.assertEqual(self.store.pending(), [])
        self.assertEqual(self.store.claim_board_cards(events), [])
        self.store = RoutineStore(self.path)
        self.store.finish(earlier[0]["id"], "complete")
        [later] = self.store.pending()
        self.assertEqual(later["routine_id"], earlier[0]["routine_id"])
        self.assertEqual(later["event"]["title"], "Later")
        self.assertEqual(
            later, next(item for item in claims if item["routine_id"] == later["routine_id"] and item["event"]["title"] == "Later")
        )

    def test_board_card_exact_scope_tag_activation_and_immutable_event(self):
        routine = self.enable(self.create({"kind": "board_card", "tag": "review"}))
        created = self.start + timedelta(minutes=2)
        card_id = str(uuid4())
        self.assertEqual(self.store.claim_board_card(None, self.room, card_id, ["review"], created), [])
        self.assertEqual(self.store.claim_board_card(self.project, str(uuid4()), card_id, ["review"], created), [])
        self.assertEqual(self.store.claim_board_card(self.project, self.room, str(uuid4()), ["Review"], created), [])
        self.assertEqual(self.store.claim_board_card(self.project, self.room, str(uuid4()), ["review"], self.start), [])
        [occurrence] = self.store.claim_board_card(
            self.project, self.room, card_id, ["review"], created,
            title="Original card", description="Original instructions", trusted_local=True, now=created,
        )
        self.assertEqual(occurrence["kind"], "board_card")
        self.store = RoutineStore(self.path)
        self.assertEqual(self.store.claim_board_card(
            self.project, self.room, card_id, ["review"], created, title="Later edit", now=created,
        ), [])
        self.assertEqual(self.store.pending(), [occurrence])
        self.assertEqual(occurrence["event"]["title"], "Original card")
        self.assertTrue(occurrence["event"]["trusted_local"])
        self.store.finish(occurrence["id"], "complete")
        paused = self.store.set_enabled(self.project, self.room, routine["id"], False, routine["revision"], now=created)
        self.enable(paused, created + timedelta(minutes=1))
        self.assertEqual(self.store.claim_board_card(self.project, self.room, str(uuid4()), ["review"], created), [])

    def test_board_native_permission_skips_phone_event_and_is_checked_inside_store(self):
        routine = self.enable(self.create({"kind": "board_card", "tag": "review"}, allow_native=True))
        with self.assertRaises(RoutineForbidden):
            self.store.manual_run(self.project, self.room, routine["id"], str(uuid4()), allow_native=False)
        with self.assertRaises(RoutineForbidden):
            self.store.set_enabled(
                self.project, self.room, routine["id"], False, routine["revision"], allow_native=False,
            )
        card_id = str(uuid4())
        created = self.start + timedelta(minutes=1)
        self.assertEqual(self.store.claim_board_card(
            self.project, self.room, card_id, ["review"], created, trusted_local=False,
        ), [])
        self.assertEqual(self.store.claim_board_card(
            self.project, self.room, card_id, ["review"], created, trusted_local=True,
        ), [])
        [skipped] = self.store.history(self.project, self.room, routine["id"])
        self.assertEqual(skipped["status"], "skipped")
        self.assertFalse(skipped["event"]["trusted_local"])
        self.assertIn("本机执行权限", skipped["detail"])

    def test_update_to_manual_turns_off_automatic_runs(self):
        routine = self.enable(self.create({"kind": "daily", "time": "09:00", "timezone": "UTC"}))
        updated = self.store.update(
            self.project, self.room, routine["id"], {"name": routine["name"], "goal": routine["goal"]},
            "team-revision", routine["revision"], now=self.start + timedelta(minutes=15),
        )
        self.assertFalse(updated["enabled"])
        self.assertIsNone(updated["next_run_at"])
        self.assertEqual(self.store.claim_scheduled(now=self.start + timedelta(days=1)), [])
        with self.assertRaises(RoutineConflict):
            self.enable(updated)
        self.assertEqual(self.store.manual_run(self.project, self.room, routine["id"], str(uuid4()))["status"], "pending")

    def test_validation_rejects_invalid_trigger_permission_and_text_budget(self):
        valid = {"name": "Review", "goal": "Summarize reviews"}
        for trigger in (
            {"kind": "weekly", "timezone": "UTC", "time": "08:00"},
            {"kind": "weekly", "timezone": "UTC", "time": "08:00", "weekday": True},
            {"kind": "daily", "timezone": "Bad/Zone", "time": "08:00"},
            {"kind": "daily", "timezone": "UTC", "time": "24:00"},
            {"kind": "interval"},
            {"kind": "interval", "every_hours": 0},
            {"kind": "interval", "every_hours": True},
            {"kind": "board_card", "tag": "  "},
            {"kind": "board_status"},
        ):
            with self.subTest(trigger=trigger), self.assertRaises(ValidationError):
                RoutineSpec.model_validate({**valid, "trigger": trigger})
        with self.assertRaises(ValidationError):
            RoutineSpec.model_validate({**valid, "allow_native": "false"})
        with self.assertRaises(ValidationError):
            RoutineSpec.model_validate({
                **valid, "goal": "a" * 3000, "process": "b" * 2500, "checks": "c" * 1501,
            })
        with self.assertRaises(ValidationError):
            RoutineSpec.model_validate({
                **valid, "goal": "a" * 2500, "process": "b" * 2000, "judgment": "c" * 1500, "examples": "d" * 1001,
            })

    def test_interval_schedule_catch_up_once_and_advance_without_storm(self):
        routine = self.create({"kind": "interval", "every_hours": 48})
        routine = self.enable(routine, self.start)
        self.assertEqual(datetime.fromisoformat(routine["next_run_at"]), self.start + timedelta(hours=48))
        self.assertEqual(self.store.claim_scheduled(now=self.start + timedelta(hours=47)), [])
        # Downtime across 3 intervals (48h, 96h, 144h) catches up only slot 3 once.
        late = self.start + timedelta(hours=150)
        [claimed] = self.store.claim_scheduled(now=late)
        self.assertEqual(claimed["kind"], "interval")
        self.assertEqual(datetime.fromisoformat(claimed["scheduled_at"]), self.start + timedelta(hours=144))
        reloaded = self.store.get(self.project, self.room, routine["id"])
        self.assertEqual(datetime.fromisoformat(reloaded["next_run_at"]), self.start + timedelta(hours=192))
        self.store = RoutineStore(self.path)
        self.assertEqual(self.store.claim_scheduled(now=late + timedelta(hours=1)), [])

    def test_board_status_trigger_with_optional_tag_and_phone_native_guard(self):
        any_review = self.enable(self.create({"kind": "board_status", "board_status": "review"}))
        tagged_review = self.enable(self.create(
            {"kind": "board_status", "board_status": "review", "tag": "release"}, allow_native=True,
        ))
        changed = self.start + timedelta(minutes=2)
        event_id = str(uuid4())
        card_id = str(uuid4())
        claimed = self.store.claim_board_status_events([{
            "event_id": event_id, "id": card_id, "project_id": self.project, "room_id": self.room,
            "title": "发布前核查", "status": "review", "changed_at": changed.isoformat(),
            "tags": ["release"], "description": "请核对清单", "local": False,
        }], now=changed)
        # Non-native routine claims it; native routine records skipped because local=False.
        self.assertEqual([item["routine_id"] for item in claimed], [any_review["id"]])
        self.assertEqual(claimed[0]["kind"], "board_status")
        [skipped] = self.store.history(self.project, self.room, tagged_review["id"])
        self.assertEqual(skipped["status"], "skipped")
        self.assertIn("本机执行权限", skipped["detail"])
        # Replaying same event_id does nothing.
        self.assertEqual(self.store.claim_board_status_events([{
            "event_id": event_id, "id": card_id, "project_id": self.project, "room_id": self.room,
            "title": "发布前核查", "status": "review", "changed_at": changed.isoformat(),
            "tags": ["release"], "description": "请核对清单", "local": True,
        }], now=changed + timedelta(seconds=5)), [])

    def test_correlation_key_deduplication_waiting_case_and_prior_context(self):
        routine = self.enable(self.create(
            {"kind": "board_card", "tag": "booking"},
            correlation_field="订单号",
            judgment="信息不全时记录等待补充信息，不要重复创建预约",
        ))
        first = self.store.manual_run(
            self.project, self.room, routine["id"], str(uuid4()),
            now=self.start, correlation_key="ORD-42", input_note="客户尚未回复时间",
        )
        self.assertEqual(first["correlation_key"], "ORD-42")
        self.assertEqual(first["event"]["input_note"], "客户尚未回复时间")
        # Active run with same correlation_key blocks duplicate manual run.
        with self.assertRaises(RoutineBusy):
            self.store.manual_run(
                self.project, self.room, routine["id"], str(uuid4()),
                now=self.start + timedelta(minutes=1), correlation_key="ORD-42",
            )
        self.store.finish(first["id"], "submitted", run_id=str(uuid4()), now=self.start + timedelta(seconds=5))
        self.store.finish(first["id"], "complete", detail="等待客户确认时间", now=self.start + timedelta(seconds=50))
        self.store.add_feedback(
            self.project, self.room, routine["id"], first["id"],
            "needs_improvement", "需等待客户回复最终时段", case_state="waiting_details",
            now=self.start + timedelta(minutes=2),
        )
        # Waiting case blocks duplicate automatic board trigger and non-resume manual run.
        with self.assertRaises(RoutineBusy):
            self.store.manual_run(
                self.project, self.room, routine["id"], str(uuid4()),
                now=self.start + timedelta(minutes=3), correlation_key="ORD-42",
            )
        created = self.start + timedelta(minutes=4)
        self.assertEqual(self.store.claim_board_card(
            self.project, self.room, str(uuid4()), ["booking"], created,
            title="处理 订单号:ORD-42", description="重复来信", trusted_local=True, now=created,
        ), [])
        history = self.store.history(self.project, self.room, routine["id"])
        self.assertEqual(history[0]["status"], "skipped")
        self.assertEqual(history[0]["correlation_key"], "ORD-42")
        self.assertIn("等待补充信息", history[0]["detail"])
        # Explicit resume resolves prior waiting_details and freezes prior context into event.
        resumed = self.store.manual_run(
            self.project, self.room, routine["id"], str(uuid4()),
            now=self.start + timedelta(minutes=5), correlation_key="ORD-42",
            input_note="客户已确认周三下午三点", resume_case=True,
        )
        self.assertIn("需等待客户回复最终时段", resumed["event"]["prior_case_context"])
        self.assertEqual(resumed["event"]["input_note"], "客户已确认周三下午三点")
        metrics = self.store.telemetry(self.project, self.room, routine["id"])
        self.assertEqual(metrics["waiting_cases"], [])

    def test_revisions_feedback_proposals_telemetry_and_allow_native_lock(self):
        routine = self.create(allow_native=False)
        self.assertEqual([item["revision"] for item in self.store.revisions(self.project, self.room, routine["id"])], [1])
        run = self.store.manual_run(self.project, self.room, routine["id"], str(uuid4()), now=self.start)
        self.store.finish(run["id"], "submitted", run_id=str(uuid4()), now=self.start + timedelta(seconds=4))
        self.store.finish(run["id"], "needs_attention", detail="来源冲突未裁决", now=self.start + timedelta(seconds=34))
        self.store.add_feedback(
            self.project, self.room, routine["id"], run["id"],
            "wrong_judgment", "邮件与看板冲突时应以看板为准", now=self.start + timedelta(minutes=1),
        )
        metrics = self.store.telemetry(self.project, self.room, routine["id"])
        self.assertEqual(metrics["total_runs"], 1)
        self.assertEqual(metrics["needs_attention"], 1)
        self.assertEqual(metrics["avg_queue_seconds"], 4.0)
        self.assertEqual(metrics["avg_run_seconds"], 30.0)
        self.assertEqual(metrics["feedback_counts"]["wrong_judgment"], 1)
        self.assertTrue(metrics["needs_reflection"])

        proposal = self.store.save_proposal(
            self.project, self.room, routine["id"], routine["revision"],
            "缺少冲突裁决规则", ["补充数据源优先级"],
            {"judgment": "当邮件和看板状态不一致时，一律以看板为准", "allow_native": True},
            [run["id"]], now=self.start + timedelta(minutes=2),
        )
        # Hard security invariant: proposal can never elevate allow_native.
        self.assertFalse(proposal["proposed_spec"]["allow_native"])
        updated, applied = self.store.apply_proposal(
            self.project, self.room, routine["id"], proposal["id"],
            "team-revision", routine["revision"], now=self.start + timedelta(minutes=3),
        )
        self.assertEqual(updated["revision"], 2)
        self.assertFalse(updated["allow_native"])
        self.assertIn("一律以看板为准", updated["judgment"])
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(applied["applied_revision"], 2)
        revisions = self.store.revisions(self.project, self.room, routine["id"])
        self.assertEqual([item["revision"] for item in revisions], [2, 1])
        self.assertEqual(revisions[0]["change_note"], "采纳自改进建议")
        self.assertEqual(revisions[0]["proposal_id"], proposal["id"])
        with self.assertRaises(RoutineConflict):
            self.store.apply_proposal(
                self.project, self.room, routine["id"], proposal["id"],
                "team-revision", updated["revision"],
            )

    def test_legacy_sqlite_schema_migrates_cleanly(self):
        import sqlite3
        legacy_path = self.path.with_name("legacy.sqlite3")
        with sqlite3.connect(legacy_path) as conn:
            conn.executescript("""
                CREATE TABLE routines (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, room_id TEXT NOT NULL,
                    create_request_id TEXT NOT NULL, create_spec TEXT NOT NULL, spec TEXT NOT NULL,
                    team_revision TEXT NOT NULL, revision INTEGER NOT NULL, enabled INTEGER NOT NULL,
                    enabled_at TEXT, next_run_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    deleted_at TEXT
                );
                CREATE TABLE routine_occurrences (
                    id TEXT PRIMARY KEY, routine_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    room_id TEXT NOT NULL, occurrence_key TEXT NOT NULL, kind TEXT NOT NULL,
                    created_at TEXT NOT NULL, scheduled_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    spec TEXT NOT NULL, team_revision TEXT NOT NULL, routine_revision INTEGER NOT NULL,
                    event TEXT, status TEXT NOT NULL, run_id TEXT, detail TEXT
                );
            """)
            rid = str(uuid4())
            legacy_spec = '{"name":"Old","goal":"Keep working","process":"","checks":"","trigger":{"kind":"manual","time":null,"timezone":null,"weekday":null,"tag":null},"document_formats":[],"allow_native":false}'
            ts = self.start.isoformat()
            conn.execute(
                "INSERT INTO routines VALUES (?, ?, ?, ?, ?, ?, 'tr', 1, 0, NULL, NULL, ?, ?, NULL)",
                (rid, self.project, self.room, str(uuid4()), legacy_spec, legacy_spec, ts, ts),
            )
            conn.execute(
                "INSERT INTO routine_occurrences VALUES (?, ?, ?, ?, ?, 'manual', ?, ?, ?, ?, 'tr', 1, NULL, 'complete', NULL, NULL)",
                (str(uuid4()), rid, self.project, self.room, f"manual:{uuid4()}", ts, ts, ts, legacy_spec),
            )
        migrated = RoutineStore(legacy_path)
        item = migrated.get(self.project, self.room, rid)
        self.assertEqual(item["judgment"], "")
        self.assertEqual(item["examples"], "")
        self.assertFalse(item["auto_reflect"])
        self.assertEqual(len(migrated.revisions(self.project, self.room, rid)), 1)
        self.assertEqual(migrated.telemetry(self.project, self.room, rid)["completed"], 1)


if __name__ == "__main__":
    unittest.main()
