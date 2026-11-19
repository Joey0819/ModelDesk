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
            {"kind": "board_card", "tag": "  "},
        ):
            with self.subTest(trigger=trigger), self.assertRaises(ValidationError):
                RoutineSpec.model_validate({**valid, "trigger": trigger})
        with self.assertRaises(ValidationError):
            RoutineSpec.model_validate({**valid, "allow_native": "false"})
        with self.assertRaises(ValidationError):
            RoutineSpec.model_validate({
                **valid, "goal": "a" * 3000, "process": "b" * 2500, "checks": "c" * 1501,
            })


if __name__ == "__main__":
    unittest.main()
