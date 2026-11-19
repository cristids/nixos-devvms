import unittest
from claude_review import validate, estimated_usd, MODEL, MAX_OUTPUT, validate_alert, post_alert, AlertOutbox
from unittest.mock import patch
import json
import tempfile
from pathlib import Path

class ReviewBoundary(unittest.TestCase):
    def test_notification_outbox_survives_outage_and_restart(self):
        with tempfile.TemporaryDirectory() as d:
            def unavailable(event):raise OSError('offline')
            box=AlertOutbox(Path(d),unavailable)
            self.assertFalse(box.send(self.alert()))
            recovered={**self.alert(),'state':'recovered','retry_seconds':0,'error_type':''}
            self.assertFalse(box.send(recovered))
            delivered=[]
            box=AlertOutbox(Path(d),delivered.append)
            box.retry();box.retry()
            self.assertEqual([e['state'] for e in delivered],['waiting','recovered'])
            self.assertTrue(box.send(self.alert()))
            self.assertEqual(len(delivered),2)

    def alert(self):
        return {'run':'ops-synthetic-test','model':'kimi-k3','state':'waiting','attempt':1,'retry_seconds':30,'error_type':'http_503','incident':'a'*32}

    def test_alert_rejects_payloads_and_arbitrary_message_text(self):
        self.assertEqual(validate_alert(self.alert()),self.alert())
        for change in ({'message':'private content'},{'model':'x\nprivate'},{'retry_seconds':-1},{'state':'approve'},{'error_type':'https://secret.invalid'}):
            with self.assertRaises(ValueError):validate_alert({**self.alert(),**change})

    def test_mattermost_message_is_metadata_only(self):
        class Response:
            status=201
            def __enter__(self):return self
            def __exit__(self,*args):pass
        with patch.dict('os.environ',{'MATTERMOST_BOT_TOKEN':'synthetic-test','MATTERMOST_CHANNEL_ID':'test-channel','MATTERMOST_URL':'http://localhost'}),patch('urllib.request.urlopen',return_value=Response()) as send:
            post_alert(self.alert())
        request=send.call_args.args[0]
        body=json.loads(request.data)
        self.assertEqual(body['channel_id'],'test-channel')
        self.assertIn('nu avansează',body['message'])
        self.assertNotIn('synthetic-test',body['message'].replace('ops-synthetic-test',''))

    def request(self):
        return {'model': MODEL, 'stream': False, 'max_tokens': 400,
                'messages': [{'role': 'system', 'content': 'Review purpose'},
                             {'role': 'user', 'content': 'Inspect service metadata'}]}

    def test_output_bounded_and_no_model_or_tool_escalation(self):
        request = self.request()
        request['max_tokens'] = 1000000
        self.assertEqual(validate('ops-test', request)['max_tokens'], MAX_OUTPUT)
        for patch in [{'model': 'unapproved'}, {'stream': True}, {'tools': []},
                      {'base_url': 'https://unapproved.invalid'}]:
            with self.assertRaises(ValueError):
                validate('ops-test', {**request, **patch})

    def test_disclosure_and_wrong_task_shape_rejected(self):
        request = self.request()
        request['messages'][1]['content'] = 'PRIVATE_BUSINESS_RECORD_A'
        with self.assertRaises(ValueError):validate('ops-test', request)
        with self.assertRaises(ValueError):validate('../other', self.request())
        request = self.request(); request['messages'].append({'role':'user','content':'extra'})
        with self.assertRaises(ValueError):validate('ops-test', request)

    def test_estimate_counts_input_and_output_separately(self):
        self.assertAlmostEqual(estimated_usd({'prompt_tokens': 1000000, 'completion_tokens': 100000}), 3)

if __name__ == '__main__':unittest.main()
