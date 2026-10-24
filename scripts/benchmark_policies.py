"""Offline policy ablation through the real HTTP environment; never calls a model API."""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harness.provenance import CONTRACT_VERSION, contract_fingerprint
from harness.agents import oracle_actions
from harness.tasks import TASKS, _EPS_CFG_NOMINAL, _ADCS_CFG_NOMINAL, _THERMAL_CFG_NOMINAL, _LINK_BUDGET_SCRIPT_FIXED
from tests.support import environment
from scripts.package_submission import fingerprints


def actions(task, policy):
    if policy == 'noop':
        return []
    if policy == 'reference':
        return oracle_actions(task)  # privileged oracle, explicitly not a model capability claim
    # Keep protected hardware facts but copy nominal software controls from the manual.
    import configparser
    cfg = configparser.ConfigParser()
    cfg.read_string(task.files['subsystems/comms.cfg'])
    cfg['comms']['tx_power_dbm'] = '36' if policy == 'max_radio' else '30'
    cfg['comms']['baudrate_bps'] = '38400' if policy == 'max_radio' else '9600'
    from io import StringIO
    output = StringIO()
    cfg.write(output)
    files = {'subsystems/eps.cfg': _EPS_CFG_NOMINAL, 'subsystems/adcs.cfg': _ADCS_CFG_NOMINAL,
            'subsystems/thermal.cfg': _THERMAL_CFG_NOMINAL, 'subsystems/comms.cfg': output.getvalue(),
            **({'scripts/link_budget.py': _LINK_BUDGET_SCRIPT_FIXED}
               if 'scripts/link_budget.py' in task.files else {})}
    return [('write_file', {'path': p, 'content': c}) for p, c in files.items()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tasks', nargs='+', choices=list(TASKS), default=list(TASKS))
    parser.add_argument('--seeds', type=int, nargs='+', default=list(range(100, 105)))
    parser.add_argument('--output', type=Path, default=ROOT / 'logs' / 'benchmarks' / 'policy-ablation.json')
    args = parser.parse_args()
    policies = ['noop', 'nominal', 'max_radio', 'reference']
    rows, counts = [], defaultdict(lambda: {'successes': 0, 'episodes': 0})
    with environment() as env:
        for task in args.tasks:
            for seed in args.seeds:
                for policy in policies:
                    env.reset(task, seed)
                    for name, values in actions(env.task, policy):
                        text, error = env.step(name, values)
                        if error:
                            raise RuntimeError(text)
                    reward = env.score()
                    passed = env.last_verifier['passed']
                    rows.append({'task': task, 'seed': seed, 'policy': policy,
                                 'reward': reward['total'], **env.last_verifier})
                    counts[f'{task}/{policy}']['episodes'] += 1
                    counts[f'{task}/{policy}']['successes'] += int(passed)
            print(task, {p: counts[f'{task}/{p}'] for p in policies}, flush=True)
    harness, sandbox = fingerprints()
    result = {'benchmark_type': 'scripted_policy_ablation', 'model_called': False,
              'contract_version': CONTRACT_VERSION, 'runtime_contract_sha256':contract_fingerprint(),
              'harness_sha256': harness, 'sandbox_sha256': sandbox,
              'seeds': args.seeds, 'tasks': args.tasks, 'policies': policies,
              'reference_is_privileged': True, 'production_isolation_tested': False,
              'summary': dict(counts), 'episodes': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('Saved offline benchmark:', args.output)


if __name__ == '__main__':
    main()
