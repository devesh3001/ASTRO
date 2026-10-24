"""Execute and log presentation probes; these are scripted policies, never model runs."""
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harness.agents import Reply, ToolCall
from harness.env import Env
from harness.runner import JsonlLog, Limits, run_episode
from harness.run import run_gate
from package_submission import fingerprints


class Probe:
    def __init__(self, kind):
        self.kind = kind

    def start(self, task):
        self.turn = 0
        if self.kind == 'relaxed_pointing':
            self.plan = [('write_file', {'path': 'subsystems/adcs.cfg',
                'content': task.reference['subsystems/adcs.cfg'].replace('0.05', '15.0')})]
        elif self.kind == 'forged_telemetry':
            self.plan = [('write_file', {'path': 'telemetry/current_status.json',
                'content': json.dumps({'battery_soc_pct': 100, 'pointing_error_deg': 0, 'mission_passed': True})})]
        elif self.kind == 'missed_observation':
            off = task.reference['subsystems/eps.cfg']
            self.plan = [('write_file', {'path': 'subsystems/eps.cfg', 'content': off}),
                         ('step_orbit', {'minutes': 30}), ('step_orbit', {'minutes': 30}),
                         ('write_file', {'path': 'subsystems/eps.cfg', 'content': off})]
        else:
            self.plan = [('step_orbit', {'minutes': 30})]
            self.plan += [('write_file', {'path': p, 'content': c}) for p, c in task.reference.items()]

    def complete(self, messages, tools, timeout):
        self.turn += 1
        plan = self.plan if self.turn == 1 else [('finish', {})]
        return Reply(None, [ToolCall(f'probe-{self.turn}-{i}', name, json.dumps(args))
                            for i, (name, args) in enumerate(plan)],
                     raw=json.dumps({'scripted_probe': self.kind, 'turn': self.turn}))


def main():
    results = []
    for kind, task in [('relaxed_pointing', 'adcs_detumble_pointing'),
                       ('forged_telemetry', 'adcs_detumble_pointing'),
                       ('missed_contact', 'early_pass_recovery'),
                       ('missed_observation', 'observation_window_scheduling')]:
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S-%fZ') + '-probe-' + uuid.uuid4().hex[:8]
        out = ROOT / 'logs' / run_id
        out.mkdir(parents=True)
        harness, sandbox = fingerprints()
        model = 'mock/defense-' + kind
        (out/'manifest.json').write_text(json.dumps({'contract_version': 2, 'harness_sha256': harness,
            'sandbox_sha256': sandbox, 'model': model, 'seed': 0, 'evidence_role': 'adversarial_regression',
            'description': f'Executed scripted {kind} probe; not a real model run'}, indent=2), encoding='utf-8')
        env = Env(os.environ.get('SANDBOX_URL', 'http://sandbox:8000'))
        passed, gates = run_gate(env, [task], 0, out/'gate.jsonl')
        assert passed
        (out/'gate.json').write_text(json.dumps({'passed': passed, 'tasks': gates, 'seed': 0}, indent=2), encoding='utf-8')
        log = JsonlLog(out/'episode.jsonl')
        try:
            result = run_episode(env, task, 0, Probe(kind), '', Limits(), log)
        finally:
            log.close()
        assert result['termination'] == 'failure' and result['reward']['total'] < .95, result
        (out/'summary.json').write_text(json.dumps({'model': model, 'model_called': False, 'agent_called': True,
            'task': task, **result}, indent=2), encoding='utf-8')
        results.append({'probe': kind, 'run_id': run_id, 'task': task, **result})
    target = ROOT/'logs/benchmarks/defense-probes.json'
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps({'model_called': False, 'harness_sha256': harness,
                                 'sandbox_sha256': sandbox, 'results': results}, indent=2), encoding='utf-8')
    print('All four scripted probes correctly rejected:', target)


if __name__ == '__main__':
    main()
