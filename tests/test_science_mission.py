"""Coupled science outcomes, causal inventory and public-tool timing regressions.

Pure simulator coverage is separate from local HTTP integration. Neither invokes
a model API or claims production container isolation.
"""
import copy
import json
import unittest

from harness.agents import oracle_actions
from harness.env import Env
from harness.tasks import build_task
from harness.verifier import AuthoritativeOrbitSimulator
from tests.support import environment


TASK = 'science_store_forward'
SEEDS = range(100, 200)


def ready(seed=101, task_id=TASK, mission=None):
    task = build_task(task_id, seed)
    cfg = Env('')._parse_subsystem_configs({**task.files, **task.reference})
    sim = AuthoritativeOrbitSimulator(task.initial_soc_pct, task.initial_temp_c,
                                     task.initial_pointing_error_deg, task.task_id,
                                     copy.deepcopy(mission if mission is not None else task.mission))
    return task, cfg, sim


def adjusted_plan(task, duties=(1.0, 1.0)):
    """Only change payload ON amplitudes, retaining real reference timing."""
    plan, window = [], 0
    for name, values in oracle_actions(task):
        values = dict(values)
        if (name == 'write_file' and values['path'] == 'subsystems/eps.cfg'
                and 'payload_duty_cycle = 1.0' in values['content']):
            values['content'] = values['content'].replace('payload_duty_cycle = 1.0',
                                                        f'payload_duty_cycle = {duties[window]}')
            window += 1
        plan.append((name, values))
    return plan


def execute(seed=101, duties=(1.0, 1.0), radio='reference', miss_first_contact=False):
    """Execute the actual public reference plan numerically, including write cost."""
    task, _, sim = ready(seed)
    files = dict(task.files)
    first_pass = task.mission['ground_passes'][0]
    first_pass_end = first_pass['start_s'] + first_pass['duration_s']
    cfg = None
    for name, values in adjusted_plan(task, duties):
        if name == 'write_file':
            files[values['path']] = values['content']
        cfg = Env('')._parse_subsystem_configs(files)
        if radio == 'nominal':
            cfg.update(tx_power_dbm=30, baudrate_bps=9600)
        elif radio == 'maximum':
            cfg.update(tx_power_dbm=36, baudrate_bps=38400)
        if miss_first_contact and sim.time_s < first_pass_end:
            cfg['tx_power_dbm'] = -10
        sim.advance(values['minutes']*60 if name == 'step_orbit' else 30, cfg)
    return task, sim, sim.finalize_orbit(cfg)


def science_state(sim):
    return {name: getattr(sim, name) for name in (
        'time_s', 'soc_wh', 'temp_c', 'pointing_err_deg', 'heater_on',
        'total_downlinked_kb', 'tx_energy_wh', 'payload_energy_wh', 'observation_s',
        'science_buffer_kb', 'science_generated_kb', 'science_downlinked_kb',
        'science_dropped_kb', 'science_peak_buffer_kb', 'science_window_exposure_s',
        'min_bus_v', 'max_temp_c', 'min_temp_c', 'min_link_margin_db',
        'catastrophic_failure', 'failure_reason')}


class TestScienceMission(unittest.TestCase):
    def assert_conserved(self, sim):
        self.assertAlmostEqual(sim.science_generated_kb,
                               sim.science_buffer_kb + sim.science_downlinked_kb + sim.science_dropped_kb,
                               places=7)
        self.assertAlmostEqual(sim.total_downlinked_kb, sim.science_downlinked_kb, places=7)
        self.assertGreaterEqual(sim.science_buffer_kb, -1e-8)
        self.assertLessEqual(sim.science_peak_buffer_kb, sim.mission['science_buffer_capacity_kb'] + 1e-8)

    def test_reference_and_equivalent_fractional_schedule_on_100_seeds(self):
        for seed in SEEDS:
            for duty in (1.0, .95):
                with self.subTest(seed=seed, duty=duty):
                    task, sim, result = execute(seed, (duty, duty))
                    self.assertTrue(result.passed, result.failure_reason)
                    self.assertTrue(all(result.checks.values()))
                    self.assert_conserved(sim)
                    self.assertAlmostEqual(sim.science_dropped_kb, 0)
                    expected = sum(w['duration_s'] for w in task.mission['science_windows']) * duty
                    self.assertAlmostEqual(sum(sim.science_window_exposure_s), expected)
                    self.assertAlmostEqual(sim.payload_energy_wh, 6*duty*(expected/duty+60)/3600)

    def test_every_constant_duty_is_excluded_and_five_samples_fail(self):
        for seed in SEEDS:
            task, cfg, _ = ready(seed)
            minimum = max(w['required_exposure_s']/w['duration_s'] for w in task.mission['science_windows'])
            maximum = task.mission['max_payload_energy_wh']/(6*5700/3600)
            self.assertLess(maximum, minimum)
            for duty in (0, .1, .5, .9, 1):
                with self.subTest(seed=seed, duty=duty):
                    _, _, sim = ready(seed)
                    result = sim.finalize_orbit({**cfg, 'payload_duty_cycle':duty})
                    self.assertFalse(result.passed)
                    self.assert_conserved(sim)

    def test_nominal_and_maximum_radio_shortcuts_fail_100_seeds(self):
        for seed in SEEDS:
            for radio, check in (('nominal', 'science_payload_delivered'),
                                 ('maximum', 'rf_energy_allocation')):
                with self.subTest(seed=seed, radio=radio):
                    _, sim, result = execute(seed, radio=radio)
                    self.assertFalse(result.passed)
                    self.assertFalse(result.checks[check])
                    self.assert_conserved(sim)

    def test_missing_first_contact_cannot_deliver_or_restore_lost_science(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                task, sim, result = execute(seed, miss_first_contact=True)
                self.assertFalse(result.passed)
                self.assertFalse(result.checks['science_payload_delivered'])
                self.assertFalse(result.checks['science_buffer_no_loss'])
                self.assertLess(sim.science_downlinked_kb, task.mission['required_science_downlink_kb'])
                self.assertGreater(sim.science_dropped_kb, 0)
                self.assert_conserved(sim)

    def test_each_window_quota_is_required(self):
        for seed in range(100, 110):
            for skipped in (0, 1):
                duties = [1, 1]
                duties[skipped] = 0
                with self.subTest(seed=seed, skipped=skipped):
                    _, sim, result = execute(seed, duties)
                    self.assertFalse(result.passed)
                    self.assertFalse(result.checks[f'science_window_{skipped+1}'])
                    self.assertEqual(sim.science_window_exposure_s[skipped], 0)

    def test_aggregate_exposure_does_not_compensate_for_missed_window_quota(self):
        task, sim, result = execute(115, (.8, 1))
        self.assertGreaterEqual(sum(sim.science_window_exposure_s),
                                sum(w['required_exposure_s'] for w in task.mission['science_windows']))
        self.assertFalse(result.checks['science_window_1'])
        self.assertTrue(result.checks['science_window_2'])
        self.assertFalse(result.passed)

    def test_good_contact_cannot_transmit_nonexistent_science(self):
        task, cfg, sim = ready()
        first = task.mission['ground_passes'][0]
        sim.advance(first['start_s']+first['duration_s'], cfg)
        self.assertGreater(sim.tx_energy_wh, 0)
        self.assertEqual(sim.science_generated_kb, 0)
        self.assertEqual(sim.science_downlinked_kb, 0)
        self.assertEqual(sim.total_downlinked_kb, 0)
        self.assertEqual(sim.science_buffer_kb, 0)
        self.assert_conserved(sim)

    def test_unaligned_windows_buffer_overflow_and_exact_conservation(self):
        task, cfg, _ = ready()
        mission = copy.deepcopy(task.mission)
        mission.update(science_windows=[{'start_s':61,'duration_s':103,'required_exposure_s':0}],
                       ground_passes=[{'start_s':211,'duration_s':79}],
                       science_rate_kb_s=1.9, science_buffer_capacity_kb=40)
        outputs = []
        for chunk in (400, 7, 13, 73):
            _, _, sim = ready(mission=mission)
            remaining = 400
            while remaining:
                dt = min(chunk, remaining)
                sim.advance(dt, {**cfg,'payload_duty_cycle':.7})
                self.assert_conserved(sim)
                remaining -= dt
            self.assertAlmostEqual(sim.science_window_exposure_s[0], 103*.7)
            self.assertAlmostEqual(sim.science_generated_kb, 103*.7*1.9)
            self.assertAlmostEqual(sim.science_downlinked_kb, 40)
            self.assertAlmostEqual(sim.science_buffer_kb, 0)
            self.assertAlmostEqual(sim.science_dropped_kb, 103*.7*1.9-40)
            outputs.append((sim.science_generated_kb,sim.science_dropped_kb,sim.science_downlinked_kb))
        for values in outputs[1:]:
            for actual, expected in zip(values,outputs[0]):
                self.assertAlmostEqual(actual,expected)

    def test_payload_power_outside_science_windows_has_no_useful_credit(self):
        _, cfg, sim = ready()
        sim.advance(300, {**cfg,'payload_duty_cycle':.5})
        self.assertAlmostEqual(sim.payload_energy_wh, 6*.5*300/3600)
        self.assertEqual(sim.observation_s, 0)
        self.assertEqual(sim.science_generated_kb, 0)

    def test_constant_control_history_is_independent_of_public_tool_grouping(self):
        for task_id in (TASK, 'early_pass_recovery'):
            with self.subTest(task=task_id):
                _, cfg, whole = ready(task_id=task_id)
                _, _, grouped = ready(task_id=task_id)
                whole.advance(5700, cfg)
                for _ in range(190):
                    grouped.advance(30, cfg)
                self.assertEqual(science_state(whole),science_state(grouped))

    def test_integration_dt_argument_does_not_change_global_tick_evolution(self):
        _, cfg, first = ready()
        _, _, second = ready()
        first.advance(5700,cfg,dt_s=13)
        second.advance(5700,cfg,dt_s=73)
        self.assertEqual(science_state(first),science_state(second))

    def test_existing_radio_missions_still_have_their_original_queued_payload(self):
        _, cfg, sim = ready(task_id='rf_energy_tradeoff')
        result = sim.finalize_orbit(cfg)
        self.assertTrue(result.passed, result.failure_reason)
        self.assertGreater(sim.total_downlinked_kb, 0)
        self.assertEqual(sim.science_generated_kb, 0)
        self.assertEqual(sim.science_downlinked_kb, 0)

    def test_public_reference_plan_baseline_reset_and_live_inventory_on_three_seeds(self):
        with environment() as env:
            for seed in (0, 101, 199):
                with self.subTest(seed=seed):
                    env.reset(TASK,seed)
                    self.assertEqual(env.score()['total'],0)
                    initial_hash = env.state_hash()
                    env.reset(TASK,seed)
                    self.assertEqual(env.state_hash(),initial_hash)
                    self.assertEqual(env.sim.science_generated_kb,0)
                    for name,args in oracle_actions(env.task):
                        output,error = env.step(name,args)
                        self.assertFalse(error,output)
                        self.assert_conserved(env.sim)
                    live = copy.deepcopy(science_state(env.sim))
                    self.assertEqual(env.score()['total'],1,env.last_verifier)
                    self.assertEqual(science_state(env.sim),live)
                    self.assertTrue(all(env.last_verifier['checks'].values()))
                    self.assertGreater(env.last_verifier['science_downlinked_kb'],env.sim.science_downlinked_kb)
                    self.assertTrue(env.last_verifier['projected'])

    def test_public_alternative_schedule_is_accepted(self):
        with environment() as env:
            env.reset(TASK,101)
            for name,args in adjusted_plan(env.task,(.95,.95)):
                output,error = env.step(name,args)
                self.assertFalse(error,output)
            self.assertEqual(env.score()['total'],1,env.last_verifier)

    def test_public_mission_and_telemetry_edits_cannot_create_inventory(self):
        with environment() as env:
            env.reset(TASK,101)
            protected = copy.deepcopy(env.sim.mission)
            fake = {**protected,'science_buffer_capacity_kb':1e9,'science_rate_kb_s':1e9,
                    'required_science_downlink_kb':0,
                    'science_windows':[{'start_s':0,'duration_s':5700,'required_exposure_s':0}]}
            for path,content in (('docs/mission.json',json.dumps(fake)),
                                 ('telemetry/current_status.json',json.dumps({'science_generated_kb':1e9,
                                                                            'science_downlinked_kb':1e9}))):
                output,error = env.step('write_file',{'path':path,'content':content})
                self.assertFalse(error,output)
            output,error = env.step('step_orbit',{'minutes':1})
            self.assertFalse(error,output)
            self.assertEqual(env.sim.mission,protected)
            self.assertEqual(env.sim.science_generated_kb,0)
            self.assertEqual(env.sim.science_downlinked_kb,0)
            self.assertEqual(env.score()['total'],0)
            self.assertFalse(env.last_verifier['checks']['science_payload_delivered'])

    def test_inventory_and_each_window_are_in_state_hash(self):
        with environment() as env:
            env.reset(TASK,101)
            original = env.state_hash()
            for field in ('science_buffer_kb','science_generated_kb','science_downlinked_kb',
                          'science_dropped_kb','science_peak_buffer_kb'):
                value = getattr(env.sim,field)
                setattr(env.sim,field,value+1)
                self.assertNotEqual(env.state_hash(),original,field)
                setattr(env.sim,field,value)
            env.sim.science_window_exposure_s[1] += 1
            self.assertNotEqual(env.state_hash(),original)

    def test_public_failed_quota_caps_reward_even_with_high_raw_score(self):
        with environment() as env:
            env.reset(TASK,115)
            for name,args in adjusted_plan(env.task,(.8,1)):
                output,error = env.step(name,args)
                self.assertFalse(error,output)
            reward = env.score()
            self.assertGreaterEqual(reward['raw_components']['total'],.95)
            self.assertLess(reward['total'],.95)
            self.assertFalse(env.last_verifier['passed'])
            self.assertFalse(env.last_verifier['checks']['science_window_1'])


if __name__ == '__main__':
    unittest.main()
