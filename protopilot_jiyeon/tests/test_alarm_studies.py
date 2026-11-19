import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from server.alarm_studies import AlarmStudies


def checkpoint(alarm_id):
    return SimpleNamespace(run_id="run", alarm={"id": alarm_id})


class AlarmStudiesTest(unittest.TestCase):
    def setUp(self):
        self.studies = AlarmStudies(Path("unused.jsonl"), workers=2)
        self.addCleanup(self.studies.close)

    def test_distinct_alarms_run_in_parallel_and_reopening_shares_work(self):
        first_started, second_started, finish = Event(), Event(), Event()
        self.addCleanup(finish.set)

        def analyze(saved):
            alarm_id = saved.alarm["id"]
            if alarm_id == 1:
                first_started.set()
            elif alarm_id == 2:
                second_started.set()
            if alarm_id in {1, 2}:
                if not finish.wait(5):
                    raise TimeoutError("Comparisons were never released")
            return alarm_id

        with patch.object(self.studies, "_analyze", side_effect=analyze) as analyze_mock:
            first_id, first = self.studies.submit(checkpoint(1))
            self.assertTrue(first_started.wait(5))
            second_id, second = self.studies.submit(checkpoint(2))
            self.assertTrue(second_started.wait(5))
            third_id, third = self.studies.submit(checkpoint(3))
            self.assertNotEqual(first_id, second_id)
            self.assertTrue(first.running())
            self.assertTrue(second.running())
            self.assertFalse(third.running())
            self.assertFalse(third.done())
            self.assertEqual(self.studies.submit(checkpoint(1)), (first_id, first))
            self.assertEqual(self.studies.submit(checkpoint(3)), (third_id, third))
            self.assertIs(self.studies.get(third_id), third)

            finish.set()
            self.assertEqual(first.result(timeout=5), 1)
            self.assertEqual(second.result(timeout=5), 2)
            self.assertEqual(third.result(timeout=5), 3)
            self.assertEqual(self.studies.submit(checkpoint(1)), (first_id, first))
            self.assertEqual(analyze_mock.call_count, 3)

    def test_retry_replaces_failed_comparison(self):
        with patch.object(self.studies, "_analyze", side_effect=[RuntimeError("failed"), 1]):
            job_id, failed = self.studies.submit(checkpoint(1))
            with self.assertRaisesRegex(RuntimeError, "failed"):
                failed.result(timeout=5)
            retry_id, retry = self.studies.submit(checkpoint(1))
            self.assertEqual(retry_id, job_id)
            self.assertIsNot(retry, failed)
            self.assertEqual(retry.result(timeout=5), 1)
