"""Mission planning regressions: rate, power, payload, event timing and protected targets."""
import copy
import json
import math
import unittest

from harness.env import Env
from harness.tasks import build_task
from harness.verifier import AuthoritativeOrbitSimulator, link_margin_db
from tests.support import environment


def ready(seed=0):
    task = build_task('rf_energy_tradeoff', seed)
    configs = Env('')._parse_subsystem_configs({**task.files, **task.reference})
    return task, configs


def simulator(task):
    return AuthoritativeOrbitSimulator(task.initial_soc_pct, task.initial_temp_c,
                                       task.initial_pointing_error_deg, task.task_id, task.mission)


class TestContactPlanning(unittest.TestCase):
    def test_manual_can_be_read_without_lost_or_duplicated_characters(self):
        import re
        with environment() as env:
            env.reset('rf_energy_tradeoff', 0)
            offset, chunks = 0, []
            while True:
                text, error = env.step('read_file', {'path': 'docs/flight_manual.md', 'offset': offset})
                self.assertFalse(error, text)
                self.assertLessEqual(len(text), 4000)
                match = re.search(r'\n\[more: next_offset=(\d+), total_characters=(\d+)\]$', text)
                if not match:
                    chunks.append(text)
                    break
                chunks.append(text[:match.start()])
                offset = int(match.group(1))
            self.assertGreater(len(chunks), 1)
            self.assertEqual(''.join(chunks), env.task.files['docs/flight_manual.md'])

    def test_read_pagination_rejects_invalid_ranges(self):
        from harness.tools import check_args
        for args in ({'offset': -1}, {'offset': True}, {'limit': 3501}, {'limit': 0}):
            self.assertIsNotNone(check_args('read_file', {'path': 'x', **args}))
        self.assertIsNone(check_args('read_file', {'path': 'x', 'offset': 3500, 'limit': 3000}))

    def test_bandwidth_has_a_physical_link_cost(self):
        _, cfg = ready()
        low = link_margin_db({**cfg, 'baudrate_bps': 9600})
        high = link_margin_db({**cfg, 'baudrate_bps': 38400})
        self.assertAlmostEqual(low - high, 10 * math.log10(4))

    def test_contact_boundaries_do_not_invent_airtime(self):
        task, cfg = ready()
        task.mission.update(ground_pass_start_s=4217, ground_pass_duration_s=277)
        expected_data = cfg['baudrate_bps'] / 8 * .85 / 1000 * 277
        expected_energy = (8 + 2 * 10 ** (cfg['tx_power_dbm'] / 10) / 1000) * 277 / 3600
        for dt in (13, 30, 60, 73):
            with self.subTest(dt=dt):
                sim = simulator(task)
                sim.advance(17, cfg, dt_s=dt)
                sim.advance(5683, cfg, dt_s=dt)
                self.assertAlmostEqual(sim.total_downlinked_kb, expected_data, places=6)
                self.assertAlmostEqual(sim.tx_energy_wh, expected_energy, places=8)
                self.assertEqual(sim.time_s, 5700)
                sim.advance(100, cfg)
                self.assertAlmostEqual(sim.total_downlinked_kb, expected_data, places=6)

    def test_nominal_and_maximum_radio_shortcuts_fail_holdout_seeds(self):
        choices = set()
        for seed in range(100, 200):
            task, cfg = ready(seed)
            choices.add((cfg['tx_power_dbm'], cfg['baudrate_bps']))
            with self.subTest(seed=seed):
                reference = simulator(task).finalize_orbit(cfg)
                self.assertTrue(reference.passed, reference)
                nominal = simulator(task).finalize_orbit({**cfg, 'tx_power_dbm': 30, 'baudrate_bps': 9600})
                self.assertFalse(nominal.passed)
                self.assertFalse(nominal.checks['payload_target'])
                maximum = simulator(task).finalize_orbit({**cfg, 'tx_power_dbm': 36, 'baudrate_bps': 38400})
                self.assertFalse(maximum.passed)
                self.assertFalse(maximum.checks['rf_energy_allocation'])
        self.assertGreater(len(choices), 20)

    def test_alternative_valid_plan_is_accepted(self):
        task, cfg = ready()
        result = simulator(task).finalize_orbit({**cfg, 'tx_power_dbm': cfg['tx_power_dbm'] + .05})
        self.assertTrue(result.passed, result)
        self.assertTrue(all(result.checks.values()))

    def test_nearly_met_battery_target_still_fails(self):
        task, cfg = ready()
        projected = simulator(task)
        projected.finalize_orbit(cfg)
        projected.mission['min_final_soc_pct'] = projected.soc_wh / 40 * 100 + .001
        result = projected.finalize_orbit(cfg)
        self.assertGreaterEqual(result.reward['total'], .95)
        self.assertFalse(result.passed)
        self.assertIn('battery_reserve', result.failure_reason)

    def test_unsupported_rate_is_latched_even_after_repair(self):
        task, cfg = ready()
        sim = simulator(task)
        sim.advance(30, {**cfg, 'baudrate_bps': 36000})
        self.assertTrue(sim.catastrophic_failure)
        result = sim.finalize_orbit(cfg)
        self.assertEqual(result.reward['total'], 0)
        self.assertIn('not supported', result.failure_reason)

    def test_workspace_mission_edit_does_not_relax_payload_target(self):
        with environment() as env:
            env.reset('rf_energy_tradeoff', 101)
            fake = {**env.task.mission, 'required_downlink_kb': 1, 'max_tx_energy_wh': 100}
            env.step('write_file', {'path': 'docs/mission.json', 'content': json.dumps(fake)})
            # Repair the script but keep the inadequate nominal radio settings.
            env.step('write_file', {'path': 'scripts/link_budget.py',
                                   'content': env.task.reference['scripts/link_budget.py']})
            self.assertLess(env.score()['total'], .95)
            self.assertFalse(env.last_verifier['checks']['payload_target'])

    def test_failed_required_check_caps_normalized_success_reward(self):
        with environment() as env:
            env.reset('rf_energy_tradeoff', 0)
            for path, content in env.task.reference.items():
                env.step('write_file', {'path': path, 'content': content})
            self.assertEqual(env.score()['total'], 1)
            projected = copy.deepcopy(env.sim)
            cfg = env._parse_subsystem_configs({**env.task.files, **env.task.reference})
            projected.finalize_orbit(cfg)
            env.sim.mission['min_final_soc_pct'] = projected.soc_wh / 40 * 100 + .001
            self.assertLess(env.score()['total'], .95)
            self.assertFalse(env.last_verifier['passed'])
            self.assertIn('battery_reserve', env.last_verifier['failure_reason'])


if __name__ == '__main__':
    unittest.main()
