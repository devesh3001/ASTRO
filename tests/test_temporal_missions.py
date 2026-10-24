"""Action history, deadlines and exposure allocation must constrain task completion."""
import unittest
from harness.agents import oracle_actions
from harness.env import Env
from harness.tasks import build_task
from harness.verifier import AuthoritativeOrbitSimulator
from tests.support import environment


def ready(task_id, seed=0):
    task = build_task(task_id, seed)
    cfg = Env('')._parse_subsystem_configs({**task.files, **task.reference})
    sim = AuthoritativeOrbitSimulator(task.initial_soc_pct, task.initial_temp_c,
                                     task.initial_pointing_error_deg, task.task_id, task.mission)
    return task, cfg, sim


class TestTemporalMissions(unittest.TestCase):
    def test_early_contact_reference_and_nominal_gain_differ_on_separate_seeds(self):
        for seed in range(100, 200):
            with self.subTest(seed=seed):
                task, cfg, sim = ready('early_pass_recovery', seed)
                self.assertTrue(sim.finalize_orbit(cfg).passed)
                _, _, slow = ready('early_pass_recovery', seed)
                result = slow.finalize_orbit({**cfg, 'control_gain': 2})
                self.assertFalse(result.passed)
                self.assertFalse(result.checks['payload_target'])

    def test_fixing_after_contact_does_not_restore_missed_data(self):
        task, cfg, sim = ready('early_pass_recovery')
        sim.advance(task.mission['ground_pass_start_s'] + task.mission['ground_pass_duration_s'],
                    {**cfg, 'tx_power_dbm': -10, 'control_gain': .4})
        result = sim.finalize_orbit(cfg)
        self.assertLessEqual(sim.pointing_err_deg, .15)
        self.assertEqual(sim.total_downlinked_kb, 0)
        self.assertFalse(result.passed)
        self.assertIn('payload_target', result.failure_reason)

    def test_transmitter_ceiling_cannot_be_relaxed_by_final_repair(self):
        _, cfg, sim = ready('early_pass_recovery')
        sim.advance(30, {**cfg, 'tx_power_dbm': 36})
        result = sim.finalize_orbit(cfg)
        self.assertEqual(result.reward['total'], 0)
        self.assertIn('ceiling', result.failure_reason)

    def test_early_contact_accepts_another_feasible_controller(self):
        _, cfg, sim = ready('early_pass_recovery')
        self.assertTrue(sim.finalize_orbit({**cfg, 'control_gain': 8}).passed)

    def test_no_constant_payload_duty_can_meet_both_requirements(self):
        for seed in range(100, 200):
            task, cfg, _ = ready('observation_window_scheduling', seed)
            minimum_static_duty = task.mission['required_observation_s'] / task.mission['observation_duration_s']
            maximum_static_duty = task.mission['max_payload_energy_wh'] / (6 * 5700 / 3600)
            self.assertLess(maximum_static_duty, minimum_static_duty)
            for duty in (0, .1, .5, .9, 1):
                with self.subTest(seed=seed, duty=duty):
                    _, _, sim = ready('observation_window_scheduling', seed)
                    self.assertFalse(sim.finalize_orbit({**cfg, 'payload_duty_cycle': duty}).passed)

    def test_exact_window_credit_and_energy_for_unaligned_boundaries(self):
        task, cfg, sim = ready('observation_window_scheduling')
        sim.mission.update(observation_start_s=1217, observation_duration_s=613)
        sim.advance(2000, {**cfg, 'payload_duty_cycle': .5}, dt_s=73)
        self.assertAlmostEqual(sim.observation_s, 613*.5)
        self.assertAlmostEqual(sim.payload_energy_wh, 6*.5*2000/3600)

    def test_late_enable_cannot_rewrite_observation_history(self):
        task, cfg, sim = ready('observation_window_scheduling')
        sim.advance(task.mission['observation_start_s'] + task.mission['observation_duration_s'], cfg)
        sim.advance(30, {**cfg, 'payload_duty_cycle': 1})
        result = sim.finalize_orbit(cfg)
        self.assertEqual(sim.observation_s, 0)
        self.assertFalse(result.passed)
        self.assertIn('observation_target', result.failure_reason)

    def test_reference_schedule_and_an_alternative_schedule_pass(self):
        for seed in range(100, 200):
            task, cfg, sim = ready('observation_window_scheduling', seed)
            sim.advance(task.mission['observation_start_s'], cfg)
            sim.advance(task.mission['observation_duration_s'], {**cfg, 'payload_duty_cycle': .95})
            result = sim.finalize_orbit(cfg)
            self.assertTrue(result.passed, (seed, result.failure_reason))

    def test_integration_reference_actions_preserve_exposure_and_state_hash(self):
        with environment() as env:
            for task_id in ('early_pass_recovery', 'observation_window_scheduling'):
                env.reset(task_id, 101)
                self.assertEqual(env.score()['total'], 0)
                first = env.state_hash()
                env.reset(task_id, 101)
                self.assertEqual(first, env.state_hash())
                for name, args in oracle_actions(env.task):
                    text, error = env.step(name, args)
                    self.assertFalse(error, text)
                self.assertEqual(env.score()['total'], 1, env.last_verifier)
                self.assertTrue(all(env.last_verifier['checks'].values()))

    def test_payload_energy_has_no_free_credit_outside_the_window(self):
        _, cfg, sim = ready('observation_window_scheduling')
        sim.advance(300, {**cfg, 'payload_duty_cycle': 1})
        self.assertGreater(sim.payload_energy_wh, 0)
        self.assertEqual(sim.observation_s, 0)


if __name__ == '__main__':
    unittest.main()
