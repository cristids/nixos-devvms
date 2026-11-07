import importlib.util
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import os
import tempfile
import threading
import time
import unittest
from unittest import mock
import io


SPEC = importlib.util.spec_from_file_location(
    "pi_task_control", Path(__file__).with_name("pi-task-control.py"))
control = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(control)


class Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clock = Clock(datetime(2026, 11, 6, 12, tzinfo=control.TZ))
        self.controller = control.Controller(self.root, now=self.clock)
        self.actor = {"user": "reviewer", "session": "s", "pane": "p"}

    def tearDown(self):
        self.temp.cleanup()

    def register(self, worker_id):
        return self.controller.register(worker_id, self.actor, "approved test slice")

    def test_triage_is_advisory_and_budgeted_without_persisting_summary(self):
        body = {"model": "clef", "answers": {"failure": {
            "choice": "infrastructure", "probabilities": {"infrastructure": 1.0}}},
            "usage": {"input_tokens": 20}}
        with mock.patch.object(control, "read_key", return_value="test-key"):
            result = control.triage(self.controller, "PRIVATE SUMMARY", self.actor,
                opener=lambda *a, **kw: io.BytesIO(json.dumps(body).encode()))
        self.assertTrue(result["advisory"])
        self.assertTrue(result["review_required"])
        self.assertEqual("infrastructure", result["category"])
        self.assertEqual("0.004", self.controller.status()["spent"])
        self.assertNotIn("PRIVATE SUMMARY", self.controller.audit_path.read_text())
        self.assertNotIn("PRIVATE SUMMARY", self.controller.state_path.read_text())

    def test_triage_skips_without_network_when_budget_or_capacity_unavailable(self):
        opener = mock.Mock(side_effect=AssertionError("must not call provider"))
        self.controller.set_cap(0, self.actor, "test")
        self.assertEqual("skipped", control.triage(self.controller, "failure", self.actor, opener)["status"])
        self.controller.set_cap(200, self.actor, "test")
        self.controller.set_worker_limit(0, self.actor, "test")
        self.assertEqual("skipped", control.triage(self.controller, "failure", self.actor, opener)["status"])
        opener.assert_not_called()

    def test_triage_provider_failure_does_not_change_existing_workers(self):
        before = self.register("existing")
        with mock.patch.object(control, "read_key", return_value="test-key"):
            result = control.triage(self.controller, "failure", self.actor,
                                   opener=mock.Mock(side_effect=TimeoutError()))
        self.assertEqual("unavailable", result["status"])
        self.assertEqual(before, self.controller.state["workers"]["existing"])
        self.assertEqual("0", self.controller.status()["reserved"])

    def wait_for(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("condition was not reached")

    def test_initial_controls_and_unbounded_live_limit(self):
        status = self.controller.status()
        self.assertEqual("200", status["monthly_cap"])
        self.assertEqual(5, status["worker_limit"])
        self.controller.set_worker_limit(41, self.actor, "Cristian approved expansion")
        self.controller.set_cap("450.75", self.actor, "Cristian approved budget")
        status = self.controller.status()
        self.assertEqual(41, status["worker_limit"])
        self.assertEqual("450.75", status["monthly_cap"])

    def test_more_than_five_requires_explicit_selective_resume(self):
        for number in range(1, 7):
            self.register(f"worker-{number}")
        self.assertEqual(["worker-6"], self.controller.status()["workers"]["limit-waiting"])
        self.controller.set_worker_limit(7, self.actor, "raise live worker limit")
        self.assertEqual(["worker-6"], self.controller.status()["workers"]["limit-waiting"])
        self.controller.resume(["worker-6"], None, False, self.actor,
                               "selectively admit worker 6")
        self.assertIn("worker-6", self.controller.status()["workers"]["active"])

    def test_selective_pause_cap_raise_does_not_resume_and_resume_wakes(self):
        self.register("one")
        self.register("two")
        self.controller.pause(["two"], None, False, self.actor, "pause noisy worker")
        self.controller.set_cap(500, self.actor, "budget only")
        self.assertEqual(["two"], self.controller.status()["workers"]["manual-paused"])
        self.controller.resume(["two"], None, False, self.actor, "review complete")
        self.assertIn("two", self.controller.status()["workers"]["active"])

    def test_count_resume_targets_oldest_waiting_workers(self):
        for worker in ("one", "two", "three"):
            self.register(worker)
        self.controller.pause(["two", "three"], None, False, self.actor, "pause two")
        affected = self.controller.resume(None, 1, False, self.actor, "resume oldest wait")
        self.assertEqual(["two"], affected)
        self.assertEqual(["three"], self.controller.status()["workers"]["manual-paused"])

    def test_deterministic_lowering_allows_in_flight_to_finish(self):
        self.controller.set_worker_limit(6, self.actor, "six test workers")
        for number in range(1, 7):
            self.register(f"w{number}")
        self.controller.reserve("w6", "w6:req")
        self.controller.set_worker_limit(2, self.actor, "reduce deterministically")
        status = self.controller.status()
        self.assertEqual(["w1", "w2"], status["workers"]["active"])
        self.assertEqual(["w6"], status["workers"]["in-flight"])
        self.register("w7")
        self.assertIn("w7", self.controller.status()["workers"]["limit-waiting"])
        self.controller.settle("w6:req", "0.01", "ok")
        self.assertIn("w6", self.controller.status()["workers"]["limit-waiting"])

    def test_budget_wait_and_raise_wakes_without_spending_while_blocked(self):
        self.controller.set_cap(0, self.actor, "exercise budget wait")
        self.register("budget")
        result = []
        thread = threading.Thread(
            target=lambda: result.append(self.controller.reserve("budget", "budget:req")))
        thread.start()
        self.wait_for(lambda: self.controller.status()["workers"]["budget-waiting"] == ["budget"])
        status = self.controller.status()
        self.assertEqual("0", status["spent"])
        self.assertEqual("0", status["reserved"])
        self.controller.set_cap(1, self.actor, "Cristian approved continuation")
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(result)
        self.controller.settle("budget:req", "0.02", "ok")

    def test_stop_all_wakes_waiter_with_controlled_failure(self):
        self.controller.set_worker_limit(0, self.actor, "hold all workers")
        self.register("blocked")
        errors = []

        def reserve():
            try:
                self.controller.reserve("blocked", "blocked:req")
            except Exception as exc:  # asserted below
                errors.append(exc)

        thread = threading.Thread(target=reserve)
        thread.start()
        self.wait_for(lambda: self.controller.status()["workers"]["limit-waiting"] == ["blocked"])
        self.controller.stop(None, None, True, self.actor, "stop test pool")
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(errors[0], control.WorkerStopped)
        self.assertIn("stopped", str(errors[0]))

    def test_atomic_reservation_race_never_exceeds_cap(self):
        reservation = control.REQUEST_RESERVATION
        self.controller.set_worker_limit(10, self.actor, "race test")
        self.controller.set_cap(reservation * 2, self.actor, "two reservations")
        for worker in ("a", "b", "c"):
            self.register(worker)
        completed = []

        def reserve(worker):
            self.controller.reserve(worker, f"{worker}:req")
            completed.append(worker)

        threads = [threading.Thread(target=reserve, args=(worker,))
                   for worker in ("a", "b", "c")]
        for thread in threads:
            thread.start()
        self.wait_for(lambda: Decimal(self.controller.status()["reserved"]) == reservation * 2)
        self.assertEqual(2, len(completed))
        first = completed[0]
        self.controller.settle(f"{first}:req", "0", "ok")
        self.wait_for(lambda: len(completed) == 3)
        for worker in completed[1:]:
            self.controller.settle(f"{worker}:req", "0.01", "ok")
        for thread in threads:
            thread.join(2)

    def test_restart_recovery_conservatively_settles_and_persists(self):
        self.register("restart")
        self.controller.reserve("restart", "restart:req")
        recovered = control.Controller(self.root, now=self.clock)
        status = recovered.status()
        self.assertEqual(control.decimal_string(control.REQUEST_RESERVATION), status["spent"])
        self.assertEqual("0", status["reserved"])
        self.assertEqual(["restart"], status["workers"]["failed"])

    def test_calendar_month_rollover_is_persistent(self):
        self.register("rollover")
        self.controller.reserve("rollover", "rollover:req")
        self.controller.settle("rollover:req", "1.25", "ok")
        self.clock.value = datetime(2026, 12, 1, 0, 1, tzinfo=control.TZ)
        status = self.controller.status()
        self.assertEqual("2026-12", status["month"])
        self.assertEqual("0", status["spent"])
        loaded = control.Controller(self.root, now=self.clock, recover=False)
        self.assertEqual("1.25", loaded.state["months"]["2026-11"]["spent"])

    def test_reason_actor_and_old_new_are_audited(self):
        with self.assertRaisesRegex(control.ControlError, "nonempty"):
            control.dispatch(self.controller, {"action": "set-cap", "value": 3})
        control.dispatch(self.controller, {
            "action": "set-cap", "value": 3, "reason": "Cristian instructed",
            "actor": self.actor,
        })
        records = [json.loads(line) for line in self.controller.audit_path.read_text().splitlines()]
        record = records[-1]
        self.assertEqual("set-cap", record["action"])
        self.assertEqual("Cristian instructed", record["reason"])
        self.assertEqual("p", record["actor"]["pane"])
        self.assertEqual("200", record["old"])
        self.assertEqual("3", record["new"])

    def test_missing_usage_is_charged_conservatively(self):
        self.register("usage")
        self.controller.reserve("usage", "usage:req")
        charged = self.controller.settle("usage:req", None, "provider-error")
        self.assertEqual(control.REQUEST_RESERVATION, charged)


class ProviderBoundaryTests(unittest.TestCase):
    def test_worker_scoped_catalog_is_served_over_unix_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = control.Controller(root / "state")
            controller.register("worker-unix", {"user": "test"}, "unix proxy test")
            control.ProxyHandler.controller = controller
            path = root / "proxy.sock"
            server = control.ControlSocketServer(str(path), control.ProxyHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = control.socket.socket(control.socket.AF_UNIX, control.socket.SOCK_STREAM)
                client.settimeout(2)
                client.connect(str(path))
                client.sendall(
                    b"GET /w/worker-unix/v1/models HTTP/1.1\r\nHost: localhost\r\n"
                    b"Connection: close\r\n\r\n")
                response = bytearray()
                while chunk := client.recv(65536):
                    response += chunk
                client.close()
                self.assertIn(b"HTTP/1.1 200 OK", response)
                self.assertIn(control.MODEL.encode(), response)
            finally:
                server.shutdown()
                server.server_close()

    def test_exact_model_catalog_is_required(self):
        control.validate_catalog(json.dumps({"data": [{"id": control.MODEL}]}).encode())
        with self.assertRaisesRegex(control.ControlError, "required Melious model is absent"):
            control.validate_catalog(json.dumps({
                "data": [{"id": "deepseek-v4.1-flash"}],
            }).encode())

    def test_missing_and_empty_credentials_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("CREDENTIALS_DIRECTORY")
            os.environ["CREDENTIALS_DIRECTORY"] = tmp
            try:
                with self.assertRaisesRegex(control.ControlError, "unavailable"):
                    control.read_key()
                (Path(tmp) / "melious").write_text("\n")
                with self.assertRaisesRegex(control.ControlError, "unavailable"):
                    control.read_key()
            finally:
                if old is None:
                    os.environ.pop("CREDENTIALS_DIRECTORY", None)
                else:
                    os.environ["CREDENTIALS_DIRECTORY"] = old


if __name__ == "__main__":
    unittest.main()
