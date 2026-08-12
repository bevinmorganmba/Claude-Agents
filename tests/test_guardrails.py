"""Guardrail tests.

These are the assertions the whole design rests on: that policy is enforced
outside the model, that an approval authorises exactly one action once, and
that the audit log cannot be quietly rewritten.

Run with:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hermes import approvals, audit, config, tools  # noqa: E402
from hermes.broker import Broker  # noqa: E402
from hermes.policy import Policy  # noqa: E402


class Isolated(unittest.TestCase):
    """Redirect the audit log and approvals DB into a temp dir so tests never
    touch the real ones."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._saved = (config.VAR, config.AUDIT_LOG, config.APPROVALS_DB)
        config.VAR = tmp
        config.AUDIT_LOG = tmp / "audit.jsonl"
        config.APPROVALS_DB = tmp / "approvals.json"
        self.broker = Broker(Policy.load())

    def tearDown(self):
        config.VAR, config.AUDIT_LOG, config.APPROVALS_DB = self._saved
        self._tmp.cleanup()


class TestHardDeny(Isolated):
    """The five rules Bevin specified. None is overridable."""

    def test_never_email_an_external_client(self):
        r = self.broker.execute("barbara", "gmail.send",
                                {"to": "cfo@halcyon.example", "subject": "hi", "body": "x"})
        self.assertEqual(r.status, "denied")

    def test_never_delete_anything(self):
        for tool, args in [("drive.delete", {"path": "/Clients/x.pdf"})]:
            with self.subTest(tool=tool):
                self.assertEqual(self.broker.execute("barbara", tool, args).status, "denied")

    def test_never_touch_legal_or_financial(self):
        cases = [
            ("drive.list_metadata", {"path_prefix": "/Legal/"}),
            ("drive.list_metadata", {"path_prefix": "/Financial/"}),
            ("drive.move", {"source_path": "/Inbox/a.pdf",
                            "destination_path": "/Contracts/a.pdf"}),
            # guarded on the *destination* too — a rule keyed on one argument
            # name only would let this through
            ("drive.move", {"source_path": "/Tax/2025.pdf",
                            "destination_path": "/Inbox/2025.pdf"}),
        ]
        for tool, args in cases:
            with self.subTest(args=args):
                r = self.broker.execute("barbara", tool, args)
                self.assertEqual(r.status, "denied", f"{args} should be refused")
                self.assertEqual(r.rule, "no-legal-or-financial")

    def test_never_publish_to_substack(self):
        r = self.broker.execute("barbara", "substack.publish", {"title": "t", "body": "b"})
        self.assertEqual(r.status, "denied")

    def test_deny_survives_approval(self):
        """A queued action re-checked at redemption still cannot beat a deny."""
        pending = approvals.enqueue("barbara", "drive.delete", {"path": "/x.pdf"}, "delete x")
        r = self.broker.redeem(pending.code)
        self.assertEqual(r.status, "denied")


class TestDefaultDeny(Isolated):
    def test_tool_outside_allow_list_is_refused(self):
        r = self.broker.execute("bailey", "calendar.create_event",
                                {"day_offset": 1, "start_time": "10:00",
                                 "duration_minutes": 30, "title": "x"})
        self.assertEqual(r.status, "denied")
        self.assertEqual(r.rule, "default-deny")

    def test_router_holds_nothing(self):
        """Olivia is the only agent reachable from outside. She must hold no
        capability at all — this is the blast-radius guarantee."""
        for tool in ("calendar.list_events", "context.read", "kanban.list_tasks",
                     "drive.list_metadata"):
            with self.subTest(tool=tool):
                r = self.broker.execute("olivia", tool, {"day_offset": 0, "path": "x"})
                self.assertEqual(r.status, "denied")

    def test_unknown_agent_is_refused(self):
        r = self.broker.execute("mallory", "calendar.list_events", {"day_offset": 0})
        self.assertEqual(r.status, "denied")


class TestApprovals(Isolated):
    def _queue(self):
        r = self.broker.execute("holt", "calendar.create_event",
                                {"day_offset": 1, "start_time": "10:00",
                                 "duration_minutes": 30, "title": "Test"})
        self.assertEqual(r.status, "pending")
        return approvals.outstanding()[0].code

    def test_queued_action_does_not_execute(self):
        self._queue()
        events = [e["event"] for e in audit.read()]
        self.assertIn("tool.queued", events)
        self.assertNotIn("tool.executed", events)

    def test_approval_executes_once(self):
        code = self._queue()
        self.assertTrue(self.broker.redeem(code).ok)
        self.assertEqual(self.broker.redeem(code).status, "denied", "code must be single use")

    def test_expired_code_is_refused(self):
        code = self._queue()
        original = config.APPROVAL_TTL_SECONDS
        config.APPROVAL_TTL_SECONDS = -1  # everything is now expired
        try:
            self.assertEqual(self.broker.redeem(code).status, "denied")
        finally:
            config.APPROVAL_TTL_SECONDS = original

    def test_code_is_bound_to_one_exact_action(self):
        """Approving one event must not authorise a different one."""
        a = {"day_offset": 1, "start_time": "10:00", "duration_minutes": 30, "title": "A"}
        b = {**a, "title": "B"}
        self.assertNotEqual(
            approvals.fingerprint("holt", "calendar.create_event", a),
            approvals.fingerprint("holt", "calendar.create_event", b),
        )

    def test_rejection_drops_the_action(self):
        code = self._queue()
        self.assertTrue(self.broker.reject(code).ok)
        self.assertEqual(self.broker.redeem(code).status, "denied")


class TestAuditChain(Isolated):
    def test_chain_is_intact_after_activity(self):
        self.broker.execute("holt", "calendar.list_events", {"day_offset": 0})
        self.broker.execute("barbara", "drive.delete", {"path": "/x"})
        ok, msg = audit.verify()
        self.assertTrue(ok, msg)

    def test_editing_a_past_entry_breaks_the_chain(self):
        self.broker.execute("barbara", "drive.delete", {"path": "/x"})
        self.broker.execute("holt", "calendar.list_events", {"day_offset": 0})
        text = config.AUDIT_LOG.read_text()
        config.AUDIT_LOG.write_text(text.replace("tool.denied", "tool.executed", 1))
        ok, msg = audit.verify()
        self.assertFalse(ok, "a rewritten entry must be detected")
        self.assertIn("modified", msg)

    def test_removing_an_entry_breaks_the_chain(self):
        for _ in range(3):
            self.broker.execute("holt", "calendar.list_events", {"day_offset": 0})
        lines = config.AUDIT_LOG.read_text().splitlines()
        config.AUDIT_LOG.write_text("\n".join(lines[:1] + lines[2:]) + "\n")
        ok, _ = audit.verify()
        self.assertFalse(ok, "a deleted entry must be detected")

    def test_denials_are_recorded_not_just_blocked(self):
        self.broker.execute("barbara", "substack.publish", {"title": "t", "body": "b"})
        denied = [e for e in audit.read() if e["event"] == "tool.denied"]
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]["rule"], "no-substack")


class TestToolBehaviour(Isolated):
    def test_drive_listing_withholds_protected_folders(self):
        out = tools.drive_list_metadata("/")
        for forbidden in ("Legal", "Financial", "returns", "MSA"):
            self.assertNotIn(forbidden, out, f"{forbidden} leaked into a root listing")
        self.assertIn("withheld", out)

    def test_context_read_cannot_escape_the_repo(self):
        self.assertIn("Refused", tools.context_read("../../etc/passwd"))

    def test_in_person_slot_respects_travel_buffer(self):
        """Fixture has a 15:00 in-person dentist tomorrow. A 14:00 in-person
        meeting is clear on paper but not once travel is counted."""
        remote = tools.calendar_check_slot(1, "14:00", 45, in_person=False)
        in_person = tools.calendar_check_slot(1, "14:00", 45, in_person=True)
        self.assertTrue(remote.startswith("FREE"), remote)
        self.assertTrue(in_person.startswith("NOT FREE"), in_person)

    def test_slots_never_fall_outside_working_hours(self):
        out = tools.calendar_find_slots(30, 1, 3)
        for line in out.splitlines()[1:]:
            start = line.strip().split()[-1].split("–")[0]
            self.assertGreaterEqual(start, config.WORKDAY_START, line)

    def test_kanban_filters_by_available_time(self):
        out = tools.kanban_list_tasks(max_minutes=20)
        self.assertNotIn("t-001", out, "a 90-minute task must not be offered for 20 minutes")
        self.assertIn("t-002", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestWeekendConsistency(Isolated):
    """find_slots skips weekends. check_slot must agree with it, or Holt will
    confirm a Saturday that his own availability search would never offer."""

    def _next_weekday_offset(self, target: int) -> int:
        from datetime import datetime, timedelta
        today = datetime.now(config.TZ).date()
        for off in range(1, 8):
            if (today + timedelta(days=off)).weekday() == target:
                return off
        raise AssertionError("unreachable")

    def test_check_slot_refuses_saturday(self):
        out = tools.calendar_check_slot(self._next_weekday_offset(5), "11:00", 30)
        self.assertTrue(out.startswith("NOT FREE"), out)
        self.assertIn("weekend", out)

    def test_find_slots_never_offers_a_weekend(self):
        out = tools.calendar_find_slots(30, 0, 13)
        for day in ("Sat", "Sun"):
            self.assertNotIn(day, out, f"{day} offered as availability")
