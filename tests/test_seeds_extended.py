"""Reset hashes and both baselines across all 100 task/seed combinations."""
import unittest
from harness.tasks import TASKS
from harness.agents import oracle_actions
from tests.support import environment


class TestExtendedSeeds(unittest.TestCase):
    def test_all_100_combinations(self):
        with environment() as env:
            for task_id in TASKS:
                for seed in range(20):
                    with self.subTest(task=task_id, seed=seed):
                        env.reset(task_id, seed)
                        first = env.state_hash()
                        env.reset(task_id, seed)
                        self.assertEqual(first, env.state_hash())
                        self.assertLessEqual(env.score()["total"], 0.05)
                        for name, args in oracle_actions(env.task):
                            result, error = env.step(name, args)
                            self.assertFalse(error, result)
                        self.assertGreaterEqual(env.score()["total"], 0.95, env.last_verifier)
                print(task_id, "20 seeds passed", flush=True)


if __name__ == "__main__":
    unittest.main()
