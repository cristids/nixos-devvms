import unittest
from claude_review import validate, estimated_usd, MODEL, MAX_OUTPUT

class ReviewBoundary(unittest.TestCase):
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
