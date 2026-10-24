"""Synthetic judge-interface scenarios: real SDK/subprocess/HTTP plus the episode runner.

All provider traffic goes to explicitly bound loopback fixtures. This module tests
protocol conformance, not undisclosed-model ability, real judge instructions, or physics.
"""
import copy
import hashlib
import importlib.util
import json
import re
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from harness.agents import OpenAIClient
from harness.run import load_append
from harness.runner import Limits, SYSTEM_PROMPT, estimate_tokens, run_episode
from harness.tools import TOOLS

SCENARIO_RECORDS = []
SYNTHETIC_APPEND = '\ufeffJudge exercise: \u03a9 / \u4e16\u754c / \U0001f680\r\n  preserve leading spaces\t\r\nDo not change telemetry.  \r\n'
OPAQUE_MODEL = 'judge/private-deployment:revision-17@region-x'


def tool(name='list_files', arguments='{}', identifier='call_0', extra=None):
    value = {'id': identifier, 'type': 'function', 'function': {'name': name, 'arguments': arguments}}
    if extra is not None:
        value['extra_content'] = extra
    return value


def body(calls=None, content=None, usage=True, **message_fields):
    message = {'role': 'assistant', 'content': content, **message_fields}
    if calls is not None:
        message['tool_calls'] = calls
    result = {'id': 'synthetic-completion', 'object': 'chat.completion',
              'choices': [{'index': 0, 'message': message, 'finish_reason': 'tool_calls' if calls else 'stop'}]}
    if usage:
        result['usage'] = {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18}
    return json.dumps(result, ensure_ascii=False)


FINISH = body([tool('finish', identifier='done')])


def check_history(messages):
    """Require complete call/result groups and schema-safe history on the actual wire."""
    pending = set()
    for message in messages:
        role = message['role']
        if role == 'tool':
            identifier = message.get('tool_call_id')
            if identifier not in pending:
                raise AssertionError('orphan or duplicate tool result')
            pending.remove(identifier)
        else:
            if pending:
                raise AssertionError('tool results missing before next message')
            if role == 'assistant':
                calls = message.get('tool_calls') or []
                if not message.get('content') and not calls:
                    raise AssertionError('assistant lacks content and calls')
                for item in calls:
                    identifier = item['id']
                    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', identifier) or identifier in pending:
                        raise AssertionError('invalid or duplicate history call id')
                    if not isinstance(json.loads(item['function']['arguments']), dict):
                        raise AssertionError('history arguments must be a JSON object')
                    pending.add(identifier)
    if pending:
        raise AssertionError('unanswered call at end of provider input')


@contextmanager
def judge_endpoint(responses, validate=None):
    """Serve only 127.0.0.1. Responses may be normal bodies or explicit HTTP error fixtures."""
    captured, violations = [], []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            wire = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            request = json.loads(wire)
            index = len(captured)
            captured.append({'body': request, 'wire': wire, 'path': self.path})
            try:
                check_history(request['messages'])
                if self.path != '/v1/chat/completions':
                    raise AssertionError('unexpected API path')
                if request.get('stream', False):
                    raise AssertionError('the harness must request a normal nonstreaming reply')
                if validate is not None:
                    validate(request, index)
                if index >= len(responses):
                    raise AssertionError('unexpected extra provider request')
                fixture = responses[index]
                fixture = {'body': fixture} if isinstance(fixture, str) else fixture
                status, payload = fixture.get('status', 200), fixture.get('body', FINISH).encode('utf-8')
                delay = fixture.get('delay', 0)
            except Exception as exc:
                violations.append(f'{type(exc).__name__}: {exc}')
                status, payload, delay = 400, b'{"error":{"message":"synthetic contract violation"}}', 0
            if delay:
                time.sleep(delay)
            try:
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # expected when a deadline cancels a delayed local fixture
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}/v1', captured, violations
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        SCENARIO_RECORDS.append({'provider': 'synthetic_loopback_only', 'requests': len(captured),
                                 'contract_violations': list(violations),
                                 'max_request_bytes': max((len(r['wire']) for r in captured), default=0),
                                 'max_message_utf8_bytes': max((estimate_tokens(r['body']['messages']) for r in captured), default=0)})


class FixtureEnv:
    """A tiny deterministic transition fixture; deliberately not the satellite verifier."""
    task = None
    deadline = None
    last_verifier = {}
    def __init__(self, output='fixture result: ok', timeout_tools=()):
        self.output, self.timeout_tools, self.executed = output, set(timeout_tools), []
    def reset(self, *args):
        self.executed.clear()
        return 'Synthetic mission prompt: preserve \u03a9 and inspect local files.'
    def step(self, name, arguments):
        self.executed.append((name, copy.deepcopy(arguments)))
        if name in self.timeout_tools:
            raise TimeoutError('synthetic tool deadline reached')
        return self.output, False
    def state_hash(self):
        return hashlib.sha256(json.dumps(self.executed, sort_keys=True).encode()).hexdigest()
    def score(self):
        return {'total': 0.0}


class FixtureLog:
    def __init__(self):
        self.records = []
    def write(self, record):
        self.records.append(copy.deepcopy(record))


@unittest.skipUnless(importlib.util.find_spec('openai'), 'pinned provider dependency is required')
class TestJudgeContract(unittest.TestCase):
    def episode(self, responses, append='', validate=None, env=None, limits=None, model=OPAQUE_MODEL):
        env, log = env or FixtureEnv(), FixtureLog()
        with judge_endpoint(responses, validate) as (url, captured, violations), patch('harness.runner.show'):
            result = run_episode(env, 'synthetic-contract', 317,
                                 OpenAIClient(model, 'synthetic-local-key-only', url),
                                 append, limits or Limits(), log)
        self.assertEqual(violations, [])
        return result, log.records, captured, env

    def assert_completed(self, result, steps):
        self.assertEqual(result['steps'], steps)
        self.assertEqual(result['termination'], 'failure')  # fixture reward is deliberately zero
        self.assertIsNone(result['error'])

    def test_judge_file_exact_utf8_crlf_survives_loading_and_every_actual_request(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'judge-note.txt'
            source.write_bytes(SYNTHETIC_APPEND.encode('utf-8'))
            with patch('harness.run.INPUTS', Path(folder)):
                append, info = load_append('inputs/judge-note.txt')
        self.assertEqual(append.encode('utf-8'), SYNTHETIC_APPEND.encode('utf-8'))
        self.assertEqual(info['sha256'], hashlib.sha256(SYNTHETIC_APPEND.encode('utf-8')).hexdigest())
        def validate(request, index):
            self.assertEqual(request['messages'][0]['content'].encode('utf-8'),
                             (SYSTEM_PROMPT+'\n\n'+SYNTHETIC_APPEND).encode('utf-8'))
        result, records, captured, _ = self.episode([body([tool()]), FINISH], append, validate)
        self.assert_completed(result, 2)
        self.assertEqual(len(captured), 2)
        self.assertTrue(all(r['model_input'][0]['content'].endswith(SYNTHETIC_APPEND) for r in records if 'step' in r))

    def test_run_a_empty_inputs_propagates_no_append(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('harness.run.INPUTS', Path(folder)):
                append, info = load_append('')
        self.assertEqual((append, info), ('', None))
        result, _, captured, _ = self.episode([FINISH], append)
        self.assert_completed(result, 1)
        self.assertEqual(captured[0]['body']['messages'][0]['content'], SYSTEM_PROMPT)

    def test_opaque_deployment_names_are_forwarded_without_routing_or_prompt_changes(self):
        for model in (OPAQUE_MODEL, 'opaque_\u0394:rev/2026-judge'):
            with self.subTest(model=model):
                result, _, captured, _ = self.episode([FINISH], model=model)
                self.assert_completed(result, 1)
                self.assertEqual(captured[0]['body']['model'], model)
                self.assertEqual(captured[0]['body']['tools'], TOOLS)
                self.assertEqual(captured[0]['body']['messages'][0]['content'], SYSTEM_PROMPT)

    def test_valid_plaintext_null_and_mixed_reply_shapes_continue_through_actual_http(self):
        responses = [body(content='I will inspect the files.', usage=False, refusal=None),
                     body([tool()], content=None, usage=False, reasoning_content='synthetic optional metadata'),
                     body([tool('finish', identifier='done')], content='Inspection complete.')]
        result, records, captured, env = self.episode(responses)
        self.assert_completed(result, 3)
        self.assertEqual(len(env.executed), 1)
        self.assertEqual(records[0]['tokens'], 0)
        self.assertEqual(records[0]['errors'][0]['kind'], 'no_tool_call')
        self.assertIn('No tool call', captured[1]['body']['messages'][-1]['content'])

    def test_malformed_arguments_and_collision_ids_recover_in_a_strict_next_request(self):
        calls = [tool('read_file', '{bad', 'normalized_1'),
                 tool('read_file', '[]', 'normalized_1'),
                 tool('step_orbit', '{"minutes":true}', 'unsafe id/'),
                 tool('list_files', '{}', None)]
        result, records, captured, env = self.episode([body(calls), FINISH])
        self.assert_completed(result, 2)
        self.assertEqual(env.executed, [('list_files', {})])
        self.assertEqual([e['kind'] for e in records[0]['errors']],
                         ['unparsable_arguments', 'unparsable_arguments', 'invalid_arguments'])
        assistant = next(m for m in captured[1]['body']['messages'] if m['role']=='assistant')
        self.assertEqual(len({c['id'] for c in assistant['tool_calls']}), 4)
        self.assertEqual(assistant['tool_calls'][0]['function']['arguments'], '{}')
        self.assertEqual(records[0]['model_output_raw'], body(calls))

    def test_signed_metadata_is_retained_exactly_in_a_normal_continuation(self):
        signature = {'google': {'thought_signature': 'opaque-signature::\u03a9\r\n +/==', 'other': ['x', 17]}}
        def validate(request, index):
            if index == 1:
                assistant = next(m for m in request['messages'] if m['role']=='assistant')
                self.assertEqual(assistant['tool_calls'][0]['extra_content'], signature)
        first = body([tool(extra=signature)])
        result, records, _, _ = self.episode([first, FINISH], validate=validate)
        self.assert_completed(result, 2)
        self.assertEqual(records[0]['model_output_raw'], first)

    def test_oversized_signature_omits_a_whole_exchange_on_the_actual_wire(self):
        signature = {'google': {'thought_signature': 's'*60000}}
        def validate(request, index):
            self.assertEqual(request['messages'][0]['content'], SYSTEM_PROMPT+'\n\n'+SYNTHETIC_APPEND)
            if index == 1:
                self.assertFalse(any(m['role'] in ('assistant','tool') for m in request['messages']))
                self.assertIn('actions already executed', request['messages'][-1]['content'])
                self.assertIn('fixture result', request['messages'][-1]['content'])
                self.assertLess(estimate_tokens(request['messages']), 48000)
        first = body([tool(extra=signature)])
        result, records, _, env = self.episode([first, FINISH], SYNTHETIC_APPEND, validate)
        self.assert_completed(result, 2)
        self.assertEqual(len(env.executed), 1)
        self.assertEqual(records[0]['model_output_raw'], first)

    def test_multilingual_observations_trim_old_groups_before_the_next_real_request(self):
        env = FixtureEnv(output='\u6e2c\u8a66\U0001f680'*4000)
        def validate(request, index):
            self.assertEqual(request['messages'][0]['content'], SYSTEM_PROMPT+'\n\n'+SYNTHETIC_APPEND)
            self.assertLessEqual(estimate_tokens(request['messages'])+estimate_tokens(TOOLS)+4096, 48000)
        responses = [body([tool(identifier=f'observe_{i}')]) for i in range(6)] + [FINISH]
        result, records, captured, _ = self.episode(responses, SYNTHETIC_APPEND, validate, env)
        self.assert_completed(result, 7)
        self.assertEqual(len(env.executed), 6)
        self.assertTrue(all(len(r['tool_result'][0])<=4000 for r in records[:6]))
        self.assertFalse(any(c['id']=='observe_0' for m in captured[-1]['body']['messages']
                             for c in m.get('tool_calls', [])))

    def test_excess_reply_calls_execute_ten_and_leave_valid_compacted_history(self):
        calls = [tool(identifier=f'batch_{i}') for i in range(129)]
        def validate(request, index):
            if index == 1:
                assistant = next(m for m in request['messages'] if m['role']=='assistant')
                self.assertEqual(len(assistant['tool_calls']), 10)
                self.assertEqual(sum(m['role']=='tool' for m in request['messages']), 10)
                self.assertIn('119 extra tool calls were rejected', request['messages'][-1]['content'])
        first = body(calls)
        result, records, _, env = self.episode([first, FINISH], validate=validate)
        self.assert_completed(result, 2)
        self.assertEqual(len(env.executed), 10)
        self.assertEqual(len(records[0]['tool_result']), 129)
        self.assertEqual(sum(e['kind']=='too_many_tool_calls' for e in records[0]['errors']), 119)
        self.assertEqual(records[0]['model_output_raw'], first)

    def test_tool_timeout_feedback_reaches_the_next_real_provider_request(self):
        env = FixtureEnv(timeout_tools=['run_python'])
        def validate(request, index):
            if index == 1:
                self.assertIn('TimeoutError', request['messages'][-1]['content'])
                self.assertIn('synthetic tool deadline', request['messages'][-1]['content'])
        result, records, _, _ = self.episode([body([tool('run_python','{"code":"pass"}')]), FINISH],
                                           validate=validate, env=env)
        self.assert_completed(result, 2)
        self.assertEqual(records[0]['errors'][0]['kind'], 'tool_error')

    def test_unreadable_http_bodies_consume_replies_and_allow_strict_recovery(self):
        result, records, captured, _ = self.episode(['{"choices":[]}', 'not JSON', FINISH])
        self.assert_completed(result, 3)
        self.assertEqual([r['model_output_raw'] for r in records[:2]], ['{"choices":[]}', 'not JSON'])
        self.assertTrue(all(r['errors'][0]['kind']=='unreadable_reply' for r in records[:2]))
        self.assertEqual(len(captured), 3)

    def test_real_http_transient_retries_preserve_append_and_do_not_add_agent_steps(self):
        errors = [{'status':429,'body':'{"error":{"message":"synthetic-local-key-only private fixture"}}'},
                  {'status':503,'body':'{"error":{"message":"synthetic server failure"}}'}, FINISH]
        def validate(request, index):
            self.assertEqual(request['messages'][0]['content'], SYSTEM_PROMPT+'\n\n'+SYNTHETIC_APPEND)
        result, records, captured, _ = self.episode(errors, SYNTHETIC_APPEND, validate)
        self.assert_completed(result, 1)
        events = [r for r in records if r.get('event')=='model_call_failed']
        self.assertEqual([r['retry_number'] for r in events], [1,2])
        self.assertTrue(all(r['retry'] for r in events))
        self.assertNotIn('synthetic-local-key-only', json.dumps(events))
        self.assertEqual(len(captured), 3)

    def test_episode_deadline_cancels_a_real_delayed_provider_subprocess(self):
        started = time.monotonic()
        result, records, captured, env = self.episode([{'body':FINISH,'delay':10}], limits=Limits(max_seconds=4))
        self.assertEqual(result['termination'], 'time_cap')
        self.assertEqual(result['steps'], 0)
        self.assertFalse(env.executed)
        self.assertEqual(len(captured), 1)
        self.assertLess(time.monotonic()-started, 6)
        self.assertEqual(records[-1]['event'], 'episode_end')
        self.assertTrue(any(r.get('event')=='model_call_failed' for r in records))


if __name__ == '__main__':
    unittest.main()
