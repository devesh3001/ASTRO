import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch
from harness.agents import OpenAIClient, ProviderError
from harness.env import Env
from harness.runner import run_episode, Limits
from tests.test_hardening import FakeEnv, Log, reply
from tests.support import environment


class FailingClient:
    def __init__(self, kind, failures):
        self.kind,self.failures,self.calls=kind,failures,0
    def start(self,task):
        pass
    def complete(self,*args,**kwargs):
        self.calls+=1
        if self.calls<=self.failures:
            raise ProviderError(self.kind)
        return reply(('finish',{}))


class TestFailureFeedback(unittest.TestCase):
    def test_transient_requests_retry_without_consuming_replies(self):
        log,client=Log(),FailingClient('RateLimitError',2)
        result=run_episode(FakeEnv(),'task',0,client,'',Limits(),log)
        self.assertEqual(client.calls,3)
        self.assertEqual(result['steps'],1)
        self.assertEqual(sum(r.get('retry',False) for r in log.records),2)
        self.assertIsNone(result['error'])

    def test_transient_retry_cap_preserves_error_in_summary(self):
        log,client=Log(),FailingClient('InternalServerError',10)
        result=run_episode(FakeEnv(),'task',0,client,'',Limits(),log)
        self.assertEqual(client.calls,3)
        self.assertEqual(result['steps'],0)
        self.assertEqual(result['termination'],'crash')
        self.assertIn('InternalServerError',result['error'])

    def test_authentication_failure_does_not_retry(self):
        client=FailingClient('AuthenticationError',10)
        result=run_episode(FakeEnv(),'task',0,client,'',Limits(),Log())
        self.assertEqual(client.calls,1)
        self.assertIn('AuthenticationError',result['error'])

    def test_provider_error_text_cannot_leak_response_or_credentials(self):
        client=OpenAIClient('model','test-only')
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=1,stderr='secret-body-and-key',stdout='')):
            with self.assertRaises(ProviderError) as captured:
                client.complete([],[],5)
        self.assertNotIn('secret-body-and-key',str(captured.exception))
        self.assertEqual(captured.exception.kind,'UnclassifiedProviderError')

    def test_invalid_orbit_setting_reports_its_cause(self):
        with environment() as env:
            env.reset('early_pass_recovery',0)
            content=env.task.reference['subsystems/adcs.cfg'].replace('5.5','100.0')
            env.step('write_file',{'path':'subsystems/adcs.cfg','content':content})
            text,error=env.step('step_orbit',{'minutes':1})
            self.assertTrue(error)
            self.assertIn('control_gain',text)
            self.assertNotIn('unknown tool',text)

    def test_legitimate_processing_longer_than_five_seconds_is_allowed(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                time.sleep(5.2)
                data=b'{"ok":true}'
                self.send_response(200)
                self.send_header('Content-Length',str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self,*args):
                pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            env=Env(f'http://127.0.0.1:{server.server_address[1]}',tool_timeout=10)
            started=time.monotonic()
            self.assertTrue(env._post('/test',{},10)['ok'])
            self.assertLess(time.monotonic()-started,10)
        finally:
            server.shutdown();server.server_close();thread.join()
