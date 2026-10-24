"""Unknown-model response contract, exercised without a real provider or API credits."""
import importlib.util
import json
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from harness.agents import MockClient, OpenAIClient, ProviderError, Reply, ToolCall
from harness.runner import Limits, run_episode
from harness.tools import TOOLS


def tool_response(calls, content=None):
    return json.dumps({'choices': [{'message': {'content': content, 'tool_calls': calls}}],
                       'usage': {'total_tokens': 23}})


def call(identifier, arguments='{}', name='list_files'):
    return {'id': identifier, 'type': 'function',
            'function': {'name': name, 'arguments': arguments}}


@contextmanager
def endpoint(status=200, body='{}', disconnect=False):
    """Serve loopback only; the real subprocess makes a real HTTP request to this fixture."""
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request=self.rfile.read(int(self.headers.get('Content-Length', 0)))
            received.append({'path': self.path, 'body': json.loads(request)})
            if disconnect:
                self.close_connection=True
                return
            payload=body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self,*args):
            pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}/v1', received
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class TestResponseNormalization(unittest.TestCase):
    def test_mock_solver_chunks_long_privileged_plans_without_skipping_actions(self):
        from types import SimpleNamespace
        task=SimpleNamespace(reference_plan=[('list_files',{}) for _ in range(23)])
        client=MockClient('solver')
        client.start(task)
        batches=[client.complete([],TOOLS,10).tool_calls for _ in range(4)]
        self.assertEqual([len(batch) for batch in batches],[9,9,6,1])
        self.assertEqual([c.name for batch in batches[:-1] for c in batch],['list_files']*23+['run_python'])
        self.assertEqual(batches[-1][0].name,'finish')
        client.start(task)
        self.assertEqual(len(client.complete([],TOOLS,10).tool_calls),9)

    def test_missing_duplicate_and_reserved_ids_are_unique_and_safe(self):
        raw=tool_response([call('normalized_1'),call('normalized_1'),call(None),
                           call('unsafe id/with spaces'),call('x'*100)])
        reply=OpenAIClient.normalize(raw)
        self.assertIsNone(reply.error)
        ids=[item.id for item in reply.tool_calls]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(ids[0],'normalized_1')
        for value in ids:
            self.assertRegex(value,r'^[A-Za-z0-9_-]{1,64}$')
        self.assertEqual(reply.raw,raw)

    def test_invalid_function_names_become_unreadable_feedback(self):
        for name in ('bad name','x'*65,''):
            with self.subTest(name=name):
                reply=OpenAIClient.normalize(tool_response([call('c',name=name)]))
                self.assertIsNotNone(reply.error)
                self.assertFalse(reply.tool_calls)

    def test_integer_decoder_limit_does_not_crash_or_lose_the_reply(self):
        arguments='{"n":'+('9'*5000)+'}'
        raw=tool_response([call('c',arguments)])
        first=OpenAIClient.normalize(raw)
        class Env:
            task=None; deadline=None; last_verifier={}
            def reset(self,*args): return 'offline task'
            def state_hash(self): return 'hash'
            def score(self): return {'total':0.0}
            def step(self,*args): raise AssertionError('malformed tool must not execute')
        class Client:
            def __init__(self):
                self.replies=iter([first,Reply(None,[ToolCall('finish','finish','{}')],raw='{}')])
            def start(self,task): pass
            def complete(self,*args,**kwargs): return next(self.replies)
        class Log:
            def __init__(self): self.records=[]
            def write(self,row): self.records.append(row)
        log=Log()
        result=run_episode(Env(),'offline',0,Client(),'',Limits(),log)
        self.assertEqual(result['steps'],2)
        self.assertEqual(result['termination'],'failure')
        self.assertEqual(log.records[0]['model_output_raw'],raw)
        self.assertEqual(log.records[0]['errors'][0]['kind'],'unparsable_arguments')

    def test_deep_json_and_decoder_errors_are_feedback(self):
        # CPython versions differ in JSON recursion handling; either a decoded non-object
        # or a RecursionError must produce a normal unreadable reply, never escape.
        reply=OpenAIClient.normalize('['*10000+'0'+']'*10000)
        self.assertIsNotNone(reply.error)
        from unittest.mock import patch
        with patch('harness.agents.json.loads',side_effect=RecursionError('deep JSON')):
            self.assertIsNotNone(OpenAIClient.normalize('{}').error)
            self.assertIsNotNone(ToolCall('c','list_files','{}').parse()[1])
        with patch('harness.agents.json.dumps',side_effect=RecursionError('deep history')):
            self.assertEqual(ToolCall('c','list_files','{}').history_arguments(),'{}')


@unittest.skipUnless(importlib.util.find_spec('openai'),'provider dependency not installed')
class TestRealProviderSubprocess(unittest.TestCase):
    def test_normal_tool_reply_uses_the_same_openai_contract(self):
        raw=tool_response([call('provider_id','{"path":"docs/flight_manual.md"}','read_file')])
        with endpoint(body=raw) as (url,received):
            reply=OpenAIClient('unknown-stress-model','fake-local-only',url).complete(
                [{'role':'user','content':'local contract check'}],TOOLS,15)
        self.assertEqual(reply.raw,raw)
        self.assertEqual(reply.tokens,23)
        self.assertEqual(reply.tool_calls[0].parse(),({'path':'docs/flight_manual.md'},None))
        self.assertEqual(len(received),1)
        self.assertEqual(received[0]['path'],'/v1/chat/completions')
        self.assertEqual(received[0]['body']['model'],'unknown-stress-model')
        self.assertEqual(received[0]['body']['tools'],TOOLS)
        self.assertNotIn('max_tokens',received[0]['body'])

    def test_malformed_and_missing_choice_bodies_remain_raw_replies(self):
        for raw in ('{"choices":[]}','not-json',tool_response([call('c','{')])):
            with self.subTest(raw=raw), endpoint(body=raw) as (url,received):
                reply=OpenAIClient('unknown-model','fake-local-only',url).complete([],TOOLS,15)
                self.assertEqual(reply.raw,raw)
                if reply.tool_calls:
                    self.assertIsNotNone(reply.tool_calls[0].parse()[1])
                else:
                    self.assertIsNotNone(reply.error)
                self.assertEqual(len(received),1)

    def test_http_errors_are_typed_safe_and_not_hidden_retries(self):
        for status,kind,recoverable in ((429,'RateLimitError',True),(503,'InternalServerError',True),
                                        (401,'AuthenticationError',False),(400,'BadRequestError',False)):
            with self.subTest(status=status), endpoint(status,body='{"error":{"message":"fake-local-only secret body"}}') as (url,received):
                with self.assertRaises(ProviderError) as caught:
                    OpenAIClient('unknown-model','fake-local-only',url).complete([],TOOLS,15)
                self.assertEqual(caught.exception.kind,kind)
                self.assertEqual(caught.exception.recoverable,recoverable)
                self.assertNotIn('fake-local-only',str(caught.exception))
                self.assertNotIn('secret body',str(caught.exception))
                self.assertEqual(len(received),1)

    def test_connection_drop_is_a_safe_transient_provider_error(self):
        with endpoint(disconnect=True) as (url,received):
            with self.assertRaises(ProviderError) as caught:
                OpenAIClient('unknown-model','fake-local-only',url).complete([],TOOLS,15)
        self.assertEqual(caught.exception.kind,'APIConnectionError')
        self.assertTrue(caught.exception.recoverable)
        self.assertEqual(len(received),1)

    def test_oversized_body_is_rejected_without_body_or_key_leakage(self):
        body='fake-local-only private response marker '+('x'*1_000_000)
        with endpoint(body=body) as (url,received):
            with self.assertRaises(ProviderError) as caught:
                OpenAIClient('unknown-model','fake-local-only',url).complete([],TOOLS,15)
        self.assertEqual(caught.exception.kind,'ValueError')
        self.assertFalse(caught.exception.recoverable)
        self.assertNotIn('fake-local-only',str(caught.exception))
        self.assertNotIn('private response marker',str(caught.exception))
        self.assertEqual(len(received),1)


if __name__=='__main__':
    unittest.main()
