"""Regression checks for state consistency and useful malformed-reply feedback."""
import configparser
import unittest
from harness.agents import Reply
from harness.env import Env
from harness.tasks import TASKS,build_task
from harness.verifier import AuthoritativeOrbitSimulator
from harness.runner import run_episode,Limits
from tests.test_hardening import FakeEnv,Log,reply
from tests.support import environment


class TestConsistency(unittest.TestCase):
    def test_allowed_tool_groupings_produce_identical_physical_history(self):
        for task_id in TASKS:
            task=build_task(task_id,101)
            cfg=Env('')._parse_subsystem_configs({**task.files,**task.reference})
            states=[]
            for grouping in (30,60,600):
                sim=AuthoritativeOrbitSimulator(task.initial_soc_pct,task.initial_temp_c,
                                               task.initial_pointing_error_deg,task.task_id,task.mission)
                for _ in range(5700//grouping):sim.advance(grouping,cfg,dt_s=grouping)
                if sim.time_s<5700:sim.advance(5700-sim.time_s,cfg,dt_s=grouping)
                states.append({k:v for k,v in vars(sim).items() if k not in ('frames',)})
            with self.subTest(task=task_id):
                self.assertEqual(states[0],states[1])
                self.assertEqual(states[0],states[2])

    def test_scalar_damping_matches_the_declared_one_minute_retention(self):
        task=build_task('early_pass_recovery',101)
        cfg=Env('')._parse_subsystem_configs({**task.files,**task.reference})
        sim=AuthoritativeOrbitSimulator(75,12,45,task.task_id,task.mission)
        sim.advance(600,cfg)
        self.assertAlmostEqual(sim.pointing_err_deg,45*(1-.33)**10,places=10)

    def test_unreadable_reply_can_be_followed_by_a_strict_provider_request(self):
        class Strict:
            def start(self,task):self.calls=0
            def complete(self,messages,tools,timeout):
                self.calls+=1
                for item in messages:
                    if item['role']=='assistant':
                        assert item.get('content') or item.get('tool_calls'), 'invalid empty assistant message'
                return Reply(None,raw='{bad',error='unreadable') if self.calls==1 else reply(('finish',{}))
        log=Log();result=run_episode(FakeEnv(),'task',0,Strict(),'',Limits(),log)
        self.assertEqual(result['steps'],2)
        self.assertIsNone(result['error'])
        self.assertEqual(next(r for r in log.records if r.get('step')==1)['model_output_raw'],'{bad')

    def test_calculation_cases_include_live_schema_and_refresh_when_config_changes(self):
        from harness.agents import oracle_actions
        with environment() as env:
            env.reset('rf_energy_tradeoff',101)
            for name,args in oracle_actions(env.task):env.step(name,args)
            recorded=[]
            original=env._post
            def capture(path,payload,timeout):
                if path=='/eval_script':recorded.extend(payload['test_cases'])
                return original(path,payload,timeout)
            env._post=capture
            self.assertEqual(env.score()['total'],1)
            self.assertEqual(len(recorded),8)
            current=original('/fetch_configs',{},60)['files']['subsystems/comms.cfg']
            self.assertEqual(recorded[0]['subsystems/comms.cfg'],current)
            for case in recorded:
                parser=configparser.ConfigParser();parser.read_string(case['subsystems/comms.cfg'])
                self.assertIn('frequency_mhz',parser['comms'])
            count=env._eval_call_count
            env.step('write_file',{'path':'subsystems/comms.cfg','content':current+'\n[diagnostics]\nrevision = 3\n'})
            self.assertEqual(env.score()['total'],1)
            self.assertEqual(env._eval_call_count,count+1)

    def test_invalid_or_missing_hardware_fields_never_become_nominal_defaults(self):
        task=build_task('eps_eclipse_recovery',101)
        files={**task.files,**task.reference}
        for old,new in [('battery_capacity_wh = 40.0','battery_capacity_wh = invalid'),
                        ('battery_capacity_wh = 40.0',''),('solar_efficiency = 0.72','solar_efficiency = bad')]:
            with self.subTest(new=new):
                cfg=Env('')._parse_subsystem_configs({**files,'subsystems/eps.cfg':files['subsystems/eps.cfg'].replace(old,new)})
                self.assertTrue(cfg['_config_errors'])
                sim=AuthoritativeOrbitSimulator(task.initial_soc_pct,task.initial_temp_c,task.initial_pointing_error_deg,task.task_id,task.mission)
                result=sim.finalize_orbit(cfg)
                self.assertFalse(result.passed)
                self.assertEqual(result.reward['total'],0)

    def test_thousands_of_rejected_calls_do_not_overflow_the_next_request(self):
        from harness.agents import ToolCall
        from harness.runner import estimate_tokens
        class CountEnv(FakeEnv):
            def __init__(self):super().__init__();self.executed=0
            def step(self,name,args):self.executed+=1;return 'ok',False
        class Strict:
            def start(self,task):self.calls=0
            def complete(self,messages,tools,timeout):
                self.calls+=1
                if self.calls==1:
                    return Reply(None,[ToolCall(f'bulk-{i}','list_files','{}') for i in range(2000)],raw='synthetic-large-batch')
                assert estimate_tokens(messages)<48000
                assistant=next(m for m in messages if m['role']=='assistant')
                assert len(assistant['tool_calls'])==10
                ids={c['id'] for c in assistant['tool_calls']}
                assert {m['tool_call_id'] for m in messages if m['role']=='tool'}==ids
                assert 'extra tool calls were rejected' in messages[-1]['content']
                return reply(('finish',{}))
        env,log=CountEnv(),Log()
        result=run_episode(env,'task',0,Strict(),'',Limits(),log)
        self.assertEqual(result['steps'],2)
        self.assertIsNone(result['error'])
        self.assertEqual(env.executed,10)
        self.assertEqual(len(log.records[0]['tool_result']),2000)
        self.assertEqual(sum(e['kind']=='too_many_tool_calls' for e in log.records[0]['errors']),1990)

    def test_invalid_baud_scores_zero_before_behavioral_math(self):
        from harness.agents import oracle_actions
        with environment() as env:
            for baud in (0,-1):
                env.reset('rf_downlink_closure',101)
                for name,args in oracle_actions(env.task):env.step(name,args)
                current=env._post('/fetch_configs',{},60)['files']['subsystems/comms.cfg']
                parser=configparser.ConfigParser();parser.read_string(current)
                parser['comms']['baudrate_bps']=str(baud)
                import io
                output=io.StringIO();parser.write(output)
                env.step('write_file',{'path':'subsystems/comms.cfg','content':output.getvalue()})
                self.assertEqual(env.score()['total'],0)
                self.assertFalse(env.last_verifier['passed'])
                self.assertIn('baud',env.last_verifier['failure_reason'].lower())

    def test_oversized_signed_metadata_drops_a_complete_exchange(self):
        from harness.agents import ToolCall
        from harness.runner import estimate_tokens
        signature='s'*60000
        class WorkingEnv(FakeEnv):
            def step(self,*args):return 'Tool completed successfully',False
        class Strict:
            def start(self,task):self.calls=0
            def complete(self,messages,tools,timeout):
                self.calls+=1
                if self.calls==1:
                    return Reply(None,[ToolCall('signed','list_files','{}',
                        {'google':{'thought_signature':signature}})],raw='original-signed-raw')
                assert estimate_tokens(messages)<48000
                assert not any(m['role']=='tool' for m in messages)
                assert 'actions already executed' in messages[-1]['content']
                return reply(('finish',{}))
        log=Log();result=run_episode(WorkingEnv(),'task',0,Strict(),'exact append',Limits(),log)
        self.assertEqual(result['steps'],2)
        self.assertIsNone(result['error'])
        self.assertEqual(log.records[0]['model_output_raw'],'original-signed-raw')
        self.assertTrue(log.records[1]['model_input'][0]['content'].endswith('exact append'))
