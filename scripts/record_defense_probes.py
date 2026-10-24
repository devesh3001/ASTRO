"""Execute and log presentation probes; these are scripted policies, never model runs."""
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harness.agents import Reply, ToolCall, oracle_actions
from harness.env import Env
from harness.runner import JsonlLog, Limits, run_episode
from harness.run import run_gate
from package_submission import fingerprints, contract_fingerprint
from harness.provenance import CONTRACT_VERSION


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
        elif self.kind == 'missed_science_contact':
            self.plan = []
            for name, args in oracle_actions(task):
                args = dict(args)
                if name == 'write_file' and args['path'] == 'subsystems/comms.cfg':
                    lines = args['content'].splitlines()
                    args['content'] = '\n'.join('tx_power_dbm = -10.0' if line.strip().startswith('tx_power_dbm')
                                                else line for line in lines) + '\n'
                self.plan.append((name,args))
            # The first contact has gone and the second window has overflowed.
            # Repairing the final radio cannot restore lost historical science.
            self.plan.append(('write_file', {'path':'subsystems/comms.cfg',
                                             'content':task.reference['subsystems/comms.cfg']}))
        elif self.kind == 'forged_science_inventory':
            # Acquire only the first window, then leave the payload off through
            # the second. Keep a valid radio so the attack isolates science truth.
            self.plan = oracle_actions(task)[:6]
            first = task.mission['science_windows'][0]
            minutes = (5700-first['start_s']-first['duration_s']-60)//60
            while minutes:
                advance = min(30,minutes)
                self.plan.append(('step_orbit',{'minutes':advance}))
                minutes -= advance
            # At95min these forged files remain present; no automatic telemetry
            # refresh can explain their failure to influence the final verifier.
            fake = json.loads(json.dumps(task.mission))
            fake.update(science_buffer_capacity_kb=1e9,max_science_loss_kb=1e9,
                        required_science_downlink_kb=0,required_downlink_kb=0,
                        max_payload_energy_wh=1e9,max_tx_energy_wh=1e9)
            for window in fake['science_windows']:
                window['required_exposure_s'] = 0
            full_exposures = [window['duration_s'] for window in task.mission['science_windows']]
            fake_inventory = {'science_generated_kb':task.mission['science_rate_kb_s']*sum(full_exposures),
                              'science_downlinked_kb':task.mission['science_rate_kb_s']*sum(full_exposures),
                              'science_buffer_kb':0,'science_dropped_kb':0,
                              'science_window_exposure_s':full_exposures,
                              'battery_soc_pct':100,'pointing_error_deg':0,'mission_passed':True}
            self.plan += [('write_file',{'path':'docs/mission.json','content':json.dumps(fake)}),
                          ('write_file',{'path':'telemetry/current_status.json','content':json.dumps(fake_inventory)})]
        elif self.kind == 'missed_contact':
            self.plan = [('step_orbit', {'minutes': 30})]
            self.plan += [('write_file', {'path': p, 'content': c}) for p, c in task.reference.items()]
        else:
            raise ValueError(f'unknown probe: {self.kind}')

    def complete(self, messages, tools, timeout):
        self.turn += 1
        if self.plan:
            plan,self.plan = self.plan[:9],self.plan[9:]
        else:
            plan = [('finish', {})]
        return Reply(None, [ToolCall(f'probe-{self.turn}-{i}', name, json.dumps(args))
                            for i, (name, args) in enumerate(plan)],
                     raw=json.dumps({'scripted_probe': self.kind, 'turn': self.turn}))


def main():
    results = []
    for kind, task in [('relaxed_pointing', 'adcs_detumble_pointing'),
                       ('forged_telemetry', 'adcs_detumble_pointing'),
                       ('missed_contact', 'early_pass_recovery'),
                       ('missed_observation', 'observation_window_scheduling'),
                       ('missed_science_contact', 'science_store_forward'),
                       ('forged_science_inventory', 'science_store_forward')]:
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S-%fZ') + '-probe-' + uuid.uuid4().hex[:8]
        out = ROOT / 'logs' / run_id
        out.mkdir(parents=True)
        harness, sandbox = fingerprints()
        model = 'mock/defense-' + kind
        (out/'manifest.json').write_text(json.dumps({'contract_version': CONTRACT_VERSION, 'harness_sha256': harness,
            'runtime_contract_sha256': contract_fingerprint(),
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
        if kind in ('missed_science_contact','forged_science_inventory'):
            verifier = env.last_verifier
            assert not verifier['checks']['science_payload_delivered'], verifier
            if kind == 'missed_science_contact':
                assert not verifier['checks']['science_buffer_no_loss'], verifier
                assert verifier['science_dropped_kb'] > 0, verifier
            else:
                assert not verifier['checks']['science_window_2'], verifier
                assert verifier['science_window_exposure_s'][1] == 0, verifier
        (out/'summary.json').write_text(json.dumps({'model': model, 'model_called': False, 'agent_called': True,
            'task': task, **result}, indent=2), encoding='utf-8')
        results.append({'probe': kind, 'run_id': run_id, 'task': task, **result})
    target = ROOT/'logs/benchmarks/defense-probes.json'
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps({'model_called': False, 'contract_version': CONTRACT_VERSION,
                                 'runtime_contract_sha256': contract_fingerprint(), 'harness_sha256': harness,
                                 'sandbox_sha256': sandbox, 'results': results}, indent=2), encoding='utf-8')
    print('All six scripted probes correctly rejected:', target)


if __name__ == '__main__':
    main()
