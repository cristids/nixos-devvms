import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location(
    "pi_task_erpnext_broker", Path(__file__).with_name("pi-task-erpnext-broker.py"))
broker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(broker)


class ERPNextBrokerTests(unittest.TestCase):
    def test_filters_map_and_credentials_to_one_bench(self):
        env = {"root": "/benches", "benches": {
            "test1": {"served": ["test1.localhost"]},
            "test2": {"served": ["test2.localhost"]},
        }}
        creds = {"test1.localhost": {"key": "one"}, "test2.localhost": {"key": "two"}}
        env_out, creds_out = broker.filtered("test1", env, creds)
        self.assertEqual(["test1"], list(env_out["benches"]))
        self.assertEqual(["test1.localhost"], list(creds_out))

    def test_refuses_uat_and_missing_credentials(self):
        with self.assertRaisesRegex(RuntimeError, "bench refused"):
            broker.filtered("uat", {"benches": {}}, {})
        with self.assertRaisesRegex(RuntimeError, "credentials unavailable"):
            broker.filtered("test1", {"benches": {
                "test1": {"served": ["test1.localhost"]},
            }}, {})


if __name__ == "__main__":
    unittest.main()
