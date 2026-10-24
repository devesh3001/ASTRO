"""Bounded offline stress of existing hard missions, using independent scripted plans.

Policies inspect public starting files and mission requirements. They never read
the privileged reference or call a model. Public HTTP checks use a private local
test server; these checks do not certify production container isolation.
"""
import argparse
import configparser
import copy
import hashlib
import io
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harness.env import Env
from harness.provenance import CONTRACT_VERSION, contract_fingerprint, fingerprints
from harness.tasks import build_task
from harness.verifier import AuthoritativeOrbitSimulator
from tests.support import environment

HARD_TASKS = ('rf_energy_tradeoff', 'early_pass_recovery',
              'observation_window_scheduling', 'science_store_forward')

# Independently written from the published equation, not copied from the oracle.
RADIO_DIAGNOSTIC = '''import configparser
import math
from pathlib import Path

def calc_margin():
    parser = configparser.ConfigParser(interpolation=None)
    source = Path("subsystems/comms.cfg")
    parser.read(source if source.exists() else "comms.cfg")
    section = parser["comms"]
    return round(section.getfloat("tx_power_dbm") + section.getfloat("antenna_gain_dbi")
                 + 113.5 - section.getfloat("path_loss_db")
                 - 10 * math.log10(section.getfloat("baudrate_bps", fallback=9600)/9600), 2)

if __name__ == "__main__":
    print(calc_margin())
'''


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def edit_config(content, section, **updates):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(content)
    for name, value in updates.items():
        parser.set(section, name, str(value))
    output = io.StringIO()
    parser.write(output)
    return output.getvalue()


def write(path, content):
    return 'write_file', {'path':path, 'content':content}


def payload(task, duty):
    return write('subsystems/eps.cfg', edit_config(task.files['subsystems/eps.cfg'],
                                                'power', payload_duty_cycle=duty))


def radio(task, baud, tx):
    return write('subsystems/comms.cfg', edit_config(task.files['subsystems/comms.cfg'],
                                                  'comms', baudrate_bps=baud, tx_power_dbm=tx))


def margin_power(task, baud, headroom=.08):
    m = task.mission
    threshold = 3 + m['path_loss_db'] - m['antenna_gain_dbi'] - 113.5 + 10*math.log10(baud/9600)
    return math.ceil((threshold+headroom)*100)/100


def energy_limit_power(task):
    m = task.mission
    contacts = m.get('ground_passes', [{'duration_s':m['ground_pass_duration_s']}])
    total_seconds = sum(w['duration_s'] for w in contacts)
    load_limit = m['max_tx_energy_wh']*3600/total_seconds
    return 10*math.log10((load_limit-8)*1000/2)


def wait_until(plan, now, target):
    delta = target-now
    if delta < 0 or abs(delta/60-round(delta/60)) > 1e-8:
        raise ValueError('planner target must follow current time by whole minutes')
    while delta:
        advance = min(1800, delta)
        plan.append(('step_orbit', {'minutes':int(advance/60)}))
        delta -= advance
    return target


def nominal_plan(task, maximum=False):
    """Known simple shortcut; hardware facts remain untouched."""
    updates = {
        'eps':('power',dict(solar_efficiency=.72,undervoltage_threshold=3.2,payload_duty_cycle=0)),
        'adcs':('attitude',dict(wheel_bias_rpm=0,pointing_deadband_deg=.05,control_gain=2)),
        'thermal':('thermal',dict(battery_heater_setpoint=15,thermal_cutoff_temp=45,heater_hysteresis_c=2)),
        'comms':('comms',dict(tx_power_dbm=36 if maximum else 30,baudrate_bps=38400 if maximum else 9600)),
    }
    plan = [write(f'subsystems/{name}.cfg',edit_config(task.files[f'subsystems/{name}.cfg'],section,**values))
            for name,(section,values) in updates.items()]
    if 'scripts/link_budget.py' in task.files:
        plan.append(write('scripts/link_budget.py',RADIO_DIAGNOSTIC))
    return plan


def independent_plan(task, case='alternative'):
    """Build from public data only; reference/reference_plan are never accessed."""
    if case in ('nominal_shortcut','maximum_radio_shortcut'):
        return nominal_plan(task,case=='maximum_radio_shortcut')
    m, task_id = task.mission, task.task_id
    if task_id == 'rf_energy_tradeoff':
        required_rate = m['required_downlink_kb']*8000/(.85*m['ground_pass_duration_s'])
        baud = next(rate for rate in m['allowed_baudrates_bps'] if rate>=required_rate)
        tx = margin_power(task,baud)
        if case == 'below_link_margin':
            tx = margin_power(task,baud,headroom=-.03)
        elif case == 'above_rf_energy':
            tx = energy_limit_power(task)+.02
        elif case == 'below_channel_capacity':
            baud = max(rate for rate in m['allowed_baudrates_bps'] if rate<required_rate)
            tx = margin_power(task,baud)
        return [radio(task,baud,tx),write('scripts/link_budget.py',RADIO_DIAGNOSTIC)]
    if task_id == 'early_pass_recovery':
        gain = 4.2 if case!='slow_controller' else 2
        tx = margin_power(task,9600)
        if case == 'above_power_ceiling':
            tx = m['max_tx_power_dbm']+.01
        plan = [write('subsystems/adcs.cfg',edit_config(task.files['subsystems/adcs.cfg'],'attitude',
                    wheel_bias_rpm=0,pointing_deadband_deg=.05,control_gain=gain)),radio(task,9600,tx)]
        return [('step_orbit',{'minutes':30})]+plan if case=='late_repair' else plan
    if task_id == 'observation_window_scheduling':
        start,duration = m['observation_start_s'],m['observation_duration_s']
        duty = .95
        if case == 'below_exposure_quota':
            duty = m['required_observation_s']/(duration-30)-.001
        plan = [payload(task,0),('read_file',{'path':'docs/mission.json'})]
        now = wait_until(plan,60,start+(180 if case=='late_enable' else 0))
        plan.append(payload(task,duty))
        now += 30
        now = wait_until(plan,now,now+duration-60)
        plan.append(payload(task,0))
        now += 30
        if case == 'above_payload_energy':
            # A measurable1mWh violation outside the useful window. The270s ON
            # interval includes the ON write, four-minute wait, then OFF write.
            used = 6*duty*(duration-30)/3600
            extra_duty = (m['max_payload_energy_wh']-used+.001)*3600/(6*270)
            plan += [payload(task,extra_duty),('step_orbit',{'minutes':4}),payload(task,0)]
        return plan
    if task_id == 'science_store_forward':
        windows = m['science_windows']
        tx = margin_power(task,38400)
        if case == 'above_rf_energy':
            tx = energy_limit_power(task)+.02
        initial_tx = -10 if case=='missed_first_contact' else tx
        plan = [payload(task,0),radio(task,38400,initial_tx),('read_file',{'path':'docs/mission.json'})]
        now = 90
        duty = .899 if case=='below_exposure_quota' else .95
        for index,window in enumerate(windows):
            now = wait_until(plan,now,window['start_s']-30)
            plan.append(payload(task,duty))
            now += 30
            now = wait_until(plan,now,window['start_s']+window['duration_s'])
            plan.append(payload(task,0))
            now += 30
        if case == 'missed_first_contact':
            plan.append(radio(task,38400,tx))
        elif case == 'above_payload_energy':
            used = 6*duty*(sum(w['duration_s'] for w in windows)+60)/3600
            extra_duty = (m['max_payload_energy_wh']-used+.001)*3600/(6*270)
            plan += [payload(task,extra_duty),('step_orbit',{'minutes':4}),payload(task,0)]
        return plan
    raise ValueError(f'unsupported stress task: {task_id}')


def case_definitions(task_id):
    common = [('alternative',True,None),('nominal_shortcut',False,None),('maximum_radio_shortcut',False,None)]
    return common + {
        'rf_energy_tradeoff':[('below_link_margin',False,'contact_link_margin'),
                              ('above_rf_energy',False,'rf_energy_allocation'),
                              ('below_channel_capacity',False,'payload_target')],
        'early_pass_recovery':[('slow_controller',False,'payload_target'),('late_repair',False,'payload_target'),
                               ('above_power_ceiling',False,None)],
        'observation_window_scheduling':[('below_exposure_quota',False,'observation_target'),
                                         ('late_enable',False,'observation_target'),
                                         ('above_payload_energy',False,'payload_energy_allocation')],
        'science_store_forward':[('below_exposure_quota',False,'science_window_1'),
                                  ('missed_first_contact',False,'science_buffer_no_loss'),
                                  ('above_rf_energy',False,'rf_energy_allocation'),
                                  ('above_payload_energy',False,'payload_energy_allocation')],
    }[task_id]


def diagnostic_values(sim):
    return {name:round(getattr(sim,name),9) for name in (
        'time_s','soc_wh','temp_c','pointing_err_deg','total_downlinked_kb','tx_energy_wh',
        'payload_energy_wh','observation_s','science_buffer_kb','science_generated_kb',
        'science_downlinked_kb','science_dropped_kb','science_peak_buffer_kb')}


def simulate_plan(task,plan):
    files = dict(task.files)
    sim = AuthoritativeOrbitSimulator(task.initial_soc_pct,task.initial_temp_c,
                                     task.initial_pointing_error_deg,task.task_id,copy.deepcopy(task.mission))
    for name,args in plan:
        if name=='write_file':
            files[args['path']] = args['content']
        cfg = Env('')._parse_subsystem_configs(files)
        cfg['script_verified'] = (task.task_id!='rf_energy_tradeoff'
                                  or files.get('scripts/link_budget.py')==RADIO_DIAGNOSTIC)
        sim.advance(args['minutes']*60 if name=='step_orbit' else 30,cfg)
    outcome = sim.finalize_orbit(cfg)
    return sim,outcome


def record_case(task,case,expected,required_failure,transport='simulator',env=None):
    plan = independent_plan(task,case)
    base = {'task':task.task_id,'seed':None,'case':case,'transport':transport,
            'expected_success':expected,'required_failed_check':required_failure,
            'action_count':len(plan),
            'plan_sha256':hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest(),
            'mission':task.mission}
    if transport=='http':
        errors = []
        for name,args in plan:
            text,error = env.step(name,args)
            if error:
                errors.append({'tool':name,'detail':text})
        score = env.score()
        verifier = env.last_verifier
        passed,checks,reason = verifier['passed'],verifier['checks'],verifier['failure_reason']
        detail = {**verifier,'reward':score,'tool_errors':errors}
        # Invalid ceiling settings intentionally return a clear tool error.
        if errors and case!='above_power_ceiling':
            detail['unexpected_tool_error'] = True
    else:
        sim,outcome = simulate_plan(task,plan)
        passed,checks,reason = outcome.passed,outcome.checks,outcome.failure_reason
        detail = {**diagnostic_values(sim),'science_window_exposure_s':list(sim.science_window_exposure_s),
                  'reward':outcome.reward,
                  'script_behavior_scope':'physics assumes the supplied independent RF diagnostic is valid; HTTP checks execute hidden fixtures'}
    okay = passed==expected and (required_failure is None or checks.get(required_failure) is False)
    if detail.get('unexpected_tool_error'):
        okay = False
    return {**base,'passed':passed,'expectation_met':okay,'checks':checks,'failure_reason':reason,'diagnostics':detail}


def corner_seeds(task_id,start=1000,count=128):
    """Select observed generated extremes, never fabricate new tasks or mutate requirements."""
    candidates = []
    for seed in range(start,start+count):
        m = build_task(task_id,seed).mission
        if task_id=='rf_energy_tradeoff':
            metrics = (m['required_downlink_kb']/m['ground_pass_duration_s'],
                       m['path_loss_db']-m['antenna_gain_dbi'],m['max_tx_energy_wh'])
        elif task_id=='early_pass_recovery':
            metrics = (m['ground_pass_start_s'],m['ground_pass_duration_s'],
                       m['path_loss_db']-m['antenna_gain_dbi'])
        elif task_id=='observation_window_scheduling':
            metrics = (m['observation_start_s'],m['observation_duration_s'])
        else:
            windows,contacts = m['science_windows'],m['ground_passes']
            metrics = (abs(windows[0]['duration_s']-windows[1]['duration_s']),
                       m['science_rate_kb_s']*max(w['duration_s'] for w in windows)/min(p['duration_s'] for p in contacts),
                       m['path_loss_db']-m['antenna_gain_dbi'])
        candidates.append((seed,metrics))
    if not candidates:
        return []
    selected = set()
    for index in range(len(candidates[0][1])):
        selected.add(min(candidates,key=lambda item:(item[1][index],item[0]))[0])
        selected.add(max(candidates,key=lambda item:(item[1][index],-item[0]))[0])
    return sorted(selected)


def run_stress(seeds=range(1000,1016),http_seeds=(1000,1011),corner_pool_size=128,
               max_http_cases=24,budget_seconds=100):
    started = time.monotonic()
    deadline = started+budget_seconds
    rows,failures = [],[]
    used_seeds,corner_profiles = {},{}
    stopped = None
    for task_id in HARD_TASKS:
        corners = corner_seeds(task_id,count=corner_pool_size)
        corner_profiles[task_id] = corners
        selected = sorted(set(seeds)|set(corners))
        used_seeds[task_id] = selected
        for seed in selected:
            task = build_task(task_id,seed)
            for case,expected,required in case_definitions(task_id):
                if time.monotonic()>=deadline:
                    stopped = 'time_budget'
                    break
                try:
                    row = record_case(task,case,expected,required)
                except Exception as exc:
                    row = {'task':task_id,'case':case,'expected_success':expected,
                           'transport':'simulator','expectation_met':False,
                           'error':f'{type(exc).__name__}: {exc}'}
                row['seed'] = seed
                rows.append(row)
                if not row['expectation_met']:
                    failures.append(row)
            if stopped:
                break
        if stopped:
            break
    http_count = 0
    if not stopped and max_http_cases:
        with environment() as env:
            env.deadline = deadline
            env.tool_timeout = env.verify_timeout = min(15,budget_seconds)
            for task_id in HARD_TASKS:
                for seed in http_seeds:
                    # One independent feasible and two independently scored bad
                    # decisions per public interface/seed. The larger pure sweep
                    # checks all boundary families without expensive fixture reuse.
                    cases = [case_definitions(task_id)[0],case_definitions(task_id)[1],
                             case_definitions(task_id)[-1]]
                    for case,expected,required in cases:
                        if http_count>=max_http_cases or time.monotonic()>=deadline:
                            stopped = 'time_budget' if time.monotonic()>=deadline else None
                            break
                        try:
                            env.reset(task_id,seed)
                            row = record_case(env.task,case,expected,required,'http',env)
                        except Exception as exc:
                            row = {'task':task_id,'case':case,'expected_success':expected,
                                   'transport':'http','expectation_met':False,
                                   'error':f'{type(exc).__name__}: {exc}'}
                        row['seed'] = seed
                        rows.append(row)
                        http_count += 1
                        if not row['expectation_met']:
                            failures.append(row)
                    if stopped or http_count>=max_http_cases:
                        break
                if stopped or http_count>=max_http_cases:
                    break
    counts = defaultdict(lambda:{'episodes':0,'expectations_met':0,'actual_successes':0,'expected_successes':0})
    for row in rows:
        key = f"{row['task']}/{row['transport']}/{row['case']}"
        counts[key]['episodes'] += 1
        counts[key]['expectations_met'] += int(row['expectation_met'])
        counts[key]['actual_successes'] += int(row.get('passed',False))
        counts[key]['expected_successes'] += int(row['expected_success'])
    source_harness,source_sandbox = fingerprints(ROOT)
    return {'benchmark_type':'deterministic_offline_mission_stress','model_called':False,
            'evidence_role':'offline_mission_stress','real_model_calls':0,'external_provider_requests':0,
            'passed':not failures and not stopped,'complete':not stopped,'stopped_reason':stopped,
            'contract_version':CONTRACT_VERSION,'harness_sha256':source_harness,'sandbox_sha256':source_sandbox,
            'runtime_contract_sha256':contract_fingerprint(ROOT),
            'validation_sources':{name:file_sha(ROOT/name) for name in
                                  ('scripts/stress_missions.py','tests/test_mission_stress.py')},
            'support_fixture_sha256':file_sha(ROOT/'tests/support.py'),
            'runtime_contract_scope':'expected build-input hashes, not observed Docker enforcement',
            'production_isolation_tested':False,'reference_policy_used':False,
            'task_registry_modified':False,
            'seed_scope':'public deterministic seeds separate from the0-19 and100-199 development suites; not secret evaluation data',
            'tasks':list(HARD_TASKS),'seeds':used_seeds,'corner_seeds':corner_profiles,
            'http_seeds':list(http_seeds),'http_case_limit':max_http_cases,
            'simulator_cases':sum(row['transport']=='simulator' for row in rows),'http_cases':http_count,
            'episodes':len(rows),'expectations_met':sum(row['expectation_met'] for row in rows),
            'elapsed_s':round(time.monotonic()-started,3),
            'summary':dict(sorted(counts.items())),'failures':failures,'results':rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds',type=int,nargs='+',default=list(range(1000,1016)))
    parser.add_argument('--http-seeds',type=int,nargs='+',default=[1000,1011])
    parser.add_argument('--corner-pool-size',type=int,default=128)
    parser.add_argument('--max-http-cases',type=int,default=24)
    parser.add_argument('--budget-seconds',type=float,default=100)
    parser.add_argument('--output',type=Path,default=ROOT/'logs/benchmarks/mission-stress.json')
    args = parser.parse_args()
    if not (0<=args.corner_pool_size<=1024 and 0<=args.max_http_cases<=100
            and 0<args.budget_seconds<=110 and 0<len(args.seeds)<=100):
        parser.error('limits:<=100 seeds,<=1024 corner profiles,<=100 HTTP cases, and<=110 seconds')
    report = run_stress(args.seeds,args.http_seeds,args.corner_pool_size,args.max_http_cases,args.budget_seconds)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({key:report[key] for key in ('passed','complete','episodes','simulator_cases',
                                                'http_cases','expectations_met','elapsed_s')}))
    if report['failures']:
        print(json.dumps(report['failures'],indent=2))
    print('Saved scripted stress evidence:',args.output)
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
