"""Exercise every baseline through the environment and behavioural verifier."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness.tasks import TASKS
from harness.agents import oracle_actions
from tests.support import environment


def run_physics_validation():
    with environment() as env:
        for task_id in TASKS:
            env.reset(task_id, 0)
            assert env.score()["total"] <= 0.05, task_id
            for name, args in oracle_actions(env.task):
                result, error = env.step(name, args)
                assert not error, result
            reward = env.score()["total"]
            assert reward >= 0.95, (task_id, reward, env.last_verifier)
            print(task_id, "reference", reward, "no-op 0.0")


if __name__ == "__main__":
    run_physics_validation()
