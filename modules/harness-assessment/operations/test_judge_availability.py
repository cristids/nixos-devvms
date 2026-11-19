import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import patch

from judge_availability import JudgeAvailability, InvalidJudgeReply, wait_process, open_judge_request


class AvailabilityTests(unittest.TestCase):
    def test_repeated_failure_never_becomes_verdict_or_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            now=[0];delays=[];alerts=[];calls=[]
            def sleep(delay):
                self.assertTrue(gate.active.is_set())
                delays.append(delay);now[0]+=delay
            gate=JudgeAvailability(Path(tmp),alerts.append,lambda:now[0],sleep)
            def judge():
                calls.append('same-model')
                if len(calls)<=8:
                    raise urllib.error.HTTPError('https://do-not-log.invalid/secret',503,'sensitive message',{},None)
                return {'approved':False}
            self.assertEqual(gate.call('kimi-k3',judge),{'approved':False})
            self.assertEqual(delays,[30,60,120,300,600,900,900,900])
            self.assertEqual(len(calls),9)
            self.assertFalse(gate.active.is_set())
            self.assertEqual(gate.waiting_seconds(),sum(delays))
            self.assertEqual([a['state'] for a in alerts],['waiting','waiting','recovered'])
            self.assertNotIn('sensitive', (Path(tmp)/'judge-availability.jsonl').read_text())
            self.assertNotIn('do-not-log', (Path(tmp)/'judge-availability.jsonl').read_text())

    def test_notification_failure_does_not_release_barrier(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls=[];alerts=[]
            def notify(event):
                alerts.append(event);raise OSError('offline')
            def operation():
                calls.append(1)
                if len(calls)<3:raise InvalidJudgeReply()
                return False
            gate=JudgeAvailability(Path(tmp),notify,sleep=lambda _:None)
            self.assertFalse(gate.call('kimi-k3',operation))
            self.assertEqual(len(calls),3)
            self.assertTrue(any(json.loads(x).get('notification_failed') for x in
                                (Path(tmp)/'judge-availability.jsonl').read_text().splitlines()))

    def test_process_exit_cannot_advance_while_judge_waits(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate=JudgeAvailability(Path(tmp),lambda _:None)
            gate.active.set()
            class Process:
                args=['synthetic']
                def poll(self):return 0
            done=threading.Event()
            t=threading.Thread(target=lambda:(wait_process(Process(),0,gate),done.set()))
            t.start()
            self.assertFalse(done.wait(0.3))
            gate.active.clear();t.join(2)
            self.assertTrue(done.is_set())

    def test_partial_judge_response_not_exposed(self):
        class Response:
            status=200;headers={}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):raise http.client.IncompleteRead(b'partial verdict')
        with patch('urllib.request.urlopen',return_value=Response()):
            with self.assertRaises(http.client.IncompleteRead):open_judge_request(object())

    def test_other_operations_wait_for_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate=JudgeAvailability(Path(tmp),lambda _:None)
            gate.active.set();passed=threading.Event()
            t=threading.Thread(target=lambda:(gate.wait_ready(),passed.set()))
            t.start();self.assertFalse(passed.wait(0.1))
            gate.active.clear();t.join(2);self.assertTrue(passed.is_set())


if __name__=='__main__':unittest.main()
