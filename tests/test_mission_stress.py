"""Independent mission policies and correctly rejected near-boundary decisions."""
import copy
import hashlib
import unittest

from harness.agents import oracle_actions
from harness.provenance import CONTRACT_VERSION, contract_fingerprint, fingerprints
from harness.tasks import build_task
from scripts.stress_missions import (
    HARD_TASKS, ROOT, case_definitions, corner_seeds, file_sha, independent_plan,
    record_case, run_stress, simulate_plan,
)
from tests.support import environment


class TestMissionStress(unittest.TestCase):
    def test_plans_use_public_inputs_and_are_distinct_from_reference(self):
        for task_id in HARD_TASKS:
            task = build_task(task_id,1001)
            plan = independent_plan(task)
            self.assertNotEqual(plan,oracle_actions(task),task_id)
            task.reference = {'DO NOT READ':'privileged sentinel'}
            task.reference_plan = object()
            self.assertEqual(plan,independent_plan(task),task_id)
            for name,args in plan:
                self.assertIn(name,('write_file','read_file','step_orbit'))
                if name=='step_orbit':
                    self.assertIsInstance(args['minutes'],int)
                    self.assertTrue(1<=args['minutes']<=30)

    def test_independent_alternatives_pass_separate_seeds(self):
        for task_id in HARD_TASKS:
            for seed in (1000,1011,1077):
                with self.subTest(task=task_id,seed=seed):
                    task = build_task(task_id,seed)
                    row = record_case(task,'alternative',True,None)
                    self.assertTrue(row['expectation_met'],row)
                    self.assertTrue(row['passed'])
                    self.assertTrue(all(row['checks'].values()))

    def test_expected_bad_decisions_are_rejected_for_the_required_reason(self):
        for task_id in HARD_TASKS:
            for seed in (1000,1011,1077):
                task = build_task(task_id,seed)
                for case,expected,required in case_definitions(task_id)[1:]:
                    with self.subTest(task=task_id,seed=seed,case=case):
                        row = record_case(task,case,expected,required)
                        self.assertTrue(row['expectation_met'],row)
                        self.assertFalse(row['passed'])

    def test_one_milliwatt_hour_overrun_fails_without_missing_exposure(self):
        for task_id in ('observation_window_scheduling','science_store_forward'):
            task = build_task(task_id,1000)
            row = record_case(task,'above_payload_energy',False,'payload_energy_allocation')
            self.assertTrue(row['expectation_met'],row)
            energy = row['diagnostics']['payload_energy_wh']
            self.assertAlmostEqual(energy-task.mission['max_payload_energy_wh'],.001,places=7)
            quota_checks = ('observation_target',) if task_id=='observation_window_scheduling' else ('science_window_1','science_window_2')
            self.assertTrue(all(row['checks'][name] for name in quota_checks))

    def test_exposure_just_below_quota_cannot_receive_success(self):
        task = build_task('observation_window_scheduling',1000)
        row = record_case(task,'below_exposure_quota',False,'observation_target')
        deficit = task.mission['required_observation_s']-row['diagnostics']['observation_s']
        self.assertGreater(deficit,0)
        self.assertLess(deficit,1)
        self.assertTrue(row['checks']['payload_energy_allocation'])
        self.assertTrue(row['expectation_met'])

    def test_missing_first_contact_loses_science_and_keeps_conservation(self):
        task = build_task('science_store_forward',1011)
        row = record_case(task,'missed_first_contact',False,'science_buffer_no_loss')
        counters = row['diagnostics']
        self.assertTrue(row['expectation_met'],row)
        self.assertGreater(counters['science_dropped_kb'],0)
        self.assertTrue(row['checks']['science_data_conservation'])
        self.assertFalse(row['checks']['science_payload_delivered'])

    def test_plan_and_results_are_deterministic_without_mutating_mission(self):
        task = build_task('science_store_forward',1003)
        initial = copy.deepcopy(task.mission)
        first = record_case(task,'alternative',True,None)
        second = record_case(task,'alternative',True,None)
        self.assertEqual(first,second)
        self.assertEqual(task.mission,initial)

    def test_corner_seed_selection_replays_and_covers_generated_extremes(self):
        for task_id in HARD_TASKS:
            seeds = corner_seeds(task_id,1000,128)
            self.assertEqual(seeds,corner_seeds(task_id,1000,128))
            self.assertTrue(all(1000<=seed<1128 for seed in seeds))
            self.assertTrue(1<=len(seeds)<=6)
            for seed in seeds:
                row = record_case(build_task(task_id,seed),'alternative',True,None)
                self.assertTrue(row['expectation_met'],row)
        self.assertEqual(corner_seeds(HARD_TASKS[0],count=0),[])

    def test_public_http_independent_alternative_matches_pure_outcomes(self):
        with environment() as env:
            for task_id in HARD_TASKS:
                with self.subTest(task=task_id):
                    env.reset(task_id,1000)
                    pure = record_case(env.task,'alternative',True,None)
                    actual = record_case(env.task,'alternative',True,None,'http',env)
                    self.assertTrue(actual['expectation_met'],actual)
                    self.assertEqual(actual['checks'],pure['checks'])
                    for key in ('observation_s','science_generated_kb','science_downlinked_kb','science_dropped_kb'):
                        self.assertAlmostEqual(actual['diagnostics'][key],pure['diagnostics'][key],places=5)

    def test_public_http_distinguishes_small_energy_overrun(self):
        with environment() as env:
            for task_id in ('observation_window_scheduling','science_store_forward'):
                env.reset(task_id,1000)
                row = record_case(env.task,'above_payload_energy',False,'payload_energy_allocation','http',env)
                self.assertTrue(row['expectation_met'],row)
                self.assertFalse(row['checks']['payload_energy_allocation'])
                self.assertLess(row['diagnostics']['reward']['total'],.95)

    def test_report_separates_expected_failures_and_matches_source_provenance(self):
        report = run_stress(seeds=[1000],http_seeds=[],corner_pool_size=0,max_http_cases=0,budget_seconds=15)
        self.assertTrue(report['passed'],report['failures'])
        self.assertTrue(report['complete'])
        self.assertEqual(report['episodes'],sum(len(case_definitions(t)) for t in HARD_TASKS))
        self.assertEqual(report['expectations_met'],report['episodes'])
        self.assertEqual(report['http_cases'],0)
        self.assertFalse(report['model_called'])
        self.assertFalse(report['reference_policy_used'])
        self.assertFalse(report['production_isolation_tested'])
        self.assertEqual(report['evidence_role'],'offline_mission_stress')
        self.assertEqual(report['real_model_calls'],0)
        self.assertEqual(report['external_provider_requests'],0)
        self.assertEqual(report['contract_version'],CONTRACT_VERSION)
        self.assertEqual(report['runtime_contract_sha256'],contract_fingerprint(ROOT))
        self.assertEqual((report['harness_sha256'],report['sandbox_sha256']),fingerprints(ROOT))
        for name,digest in report['validation_sources'].items():
            self.assertEqual(digest,file_sha(ROOT/name))
            self.assertEqual(digest,hashlib.sha256((ROOT/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest())

    def test_time_budget_stops_without_claiming_a_complete_pass(self):
        report = run_stress(seeds=[1000],http_seeds=[],corner_pool_size=0,max_http_cases=0,budget_seconds=.000001)
        self.assertFalse(report['passed'])
        self.assertFalse(report['complete'])
        self.assertEqual(report['stopped_reason'],'time_budget')


if __name__=='__main__':
    unittest.main()
