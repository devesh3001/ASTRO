"""Read-only PS evidence audit. Never sends model requests or prints credentials."""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harness.tasks import TASKS, DEMO_TASK
from package_submission import (collect_evidence, fingerprints, contract_fingerprint, provenance_matches,
                                validate_supplemental_report, SUPPLEMENTAL_REPORTS)
from harness.provenance import CONTRACT_VERSION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--team-name', default='Astro')
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--github', action='store_true')
    parser.add_argument('--organizer', help='Exact GitHub username from submission-form email')
    parser.add_argument('--output', type=Path, default=ROOT/'logs/audit/ps-audit.json')
    parser.add_argument('--strict', action='store_true', help='Also fail for unresolved administrative requirements')
    args = parser.parse_args()
    rows = []
    def add(name, status, evidence, administrative=False):
        rows.append(dict(requirement=name, status=status, evidence=evidence, administrative=administrative))
    def git(*values):
        return subprocess.check_output(['git', *values], cwd=ROOT, text=True).strip()
    branch = git('branch', '--show-current')
    add('Judge branch is main', 'pass' if branch == 'main' else 'fail', branch)
    add('Checkout clean', 'pass' if not git('status', '--porcelain') else 'fail', 'git status --porcelain')
    tracked = git('ls-files', '-z').split('\0')
    add('Required source structure', 'pass' if all((ROOT/p).exists() for p in [
        'harness', 'sandbox', 'tests', 'scripts', 'docker-compose.yml', 'Dockerfile.harness',
        'Dockerfile.sandbox', 'requirements.lock', '.env.example', 'README.md']) else 'fail', 'Required paths checked')
    add('Five or more tasks, two or more levels', 'pass' if len(TASKS)>=5 and len({b(0).difficulty for b in TASKS.values()})>=2 else 'fail',
        f'{len(TASKS)} task families; {len({b(0).difficulty for b in TASKS.values()})} difficulty levels')
    add('Named demo', 'pass' if DEMO_TASK in TASKS and DEMO_TASK in (ROOT/'README.md').read_text(encoding='utf-8') else 'fail', DEMO_TASK)
    ignored = subprocess.run(['git', 'check-ignore', '-q', '.env'], cwd=ROOT).returncode == 0
    add('Real .env excluded from Git', 'pass' if ignored and '.env' not in tracked else 'fail', 'Ignore rule and tracked index checked')
    settings = {}
    if (ROOT/'.env').exists():
        settings = dict(line.split('=',1) for line in (ROOT/'.env').read_text(encoding='utf-8').splitlines()
                        if '=' in line and not line.lstrip().startswith('#'))
    secret = settings.get('API_KEY', '').strip().strip('"\'').encode()
    leaks = [name for name in tracked if name and secret and secret in (ROOT/name).read_bytes()]
    add('Supplied key absent from tracked files', 'fail' if leaks else 'pass', leaks or 'No key matches; values withheld')
    example = (ROOT/'.env.example').read_text(encoding='utf-8')
    add('Credential-free example', 'pass' if re.search(r'^API_KEY=\s*$', example, re.M) else 'fail', '.env.example has empty API_KEY')
    harness, sandbox = fingerprints()
    runtime_contract = contract_fingerprint()
    try:
        gate, runs, success, failure = collect_evidence(ROOT/'logs')
        add('Current-code all-task baseline gate', 'pass', gate.name)
        add('At least three genuine current-code model runs', 'pass', f'{len(runs)} runs, matched source fingerprints and complete episode records')
        provenance = json.loads((failure.parent/'manifest.json').read_text(encoding='utf-8'))
        add('Success and failure samples', 'pass', {'success': success.parent.name, 'failure': failure.parent.name,
            'failure_role': provenance.get('evidence_role', 'real_model')})
    except ValueError as exc:
        add('Current-code evidence', 'fail', str(exc))
    archive = args.archive or ROOT/f'{args.team_name}.zip'
    try:
        with zipfile.ZipFile(archive) as bundle:
            manifest = json.loads(bundle.read('evidence-manifest.json'))
            assert manifest['team'] == args.team_name
            assert provenance_matches(manifest, (harness, sandbox), runtime_contract)
            assert bundle.testzip() is None
            for name, expected in manifest['files'].items():
                data = bundle.read(name)
                assert hashlib.sha256(data).hexdigest() == expected
                assert not secret or secret not in data
            names = bundle.namelist()
            assert all(n in names for n in ['baseline-results/gate.json', 'sample-trajectories/success.jsonl',
                'sample-trajectories/failure.jsonl', 'design-doc.pdf', 'deck.pdf'])
            add('Archive layout, evidence and credential scan', 'pass', f'{archive.name}: {len(names)} files, current fingerprints and verified checksums')
            reviewed_path = ROOT/'submission_bundle/validation-evidence.json'
            if reviewed_path.is_file():
                reviewed = json.loads(reviewed_path.read_text(encoding='utf-8'))
                assert json.loads(bundle.read('baseline-results/validation-evidence.json')) == reviewed
                supplemental = reviewed.get('supplemental_validation', {})
                for report_name in SUPPLEMENTAL_REPORTS:
                    if report_name in supplemental:
                        current = validate_supplemental_report(ROOT/'logs/benchmarks'/report_name)
                        assert supplemental[report_name] == current
                        assert json.loads(bundle.read('baseline-results/'+report_name)) == current
                if supplemental:
                    add('Source-bound supplemental Run B evidence', 'pass',
                        {'reports': sorted(supplemental), 'model_calls': 0,
                         'scope': 'Synthetic interface and independent scripted mission checks; not undisclosed-model performance'})

            try:
                import io
                from pypdf import PdfReader
                lengths = {name: len(PdfReader(io.BytesIO(bundle.read(name))).pages) for name in ('design-doc.pdf','deck.pdf')}
                add('Design document and presentation PDFs', 'pass' if 1<=lengths['design-doc.pdf']<=2 and lengths['deck.pdf']>0 else 'fail', lengths)
            except ImportError:
                add('PDF page-count audit', 'review', 'Install pypdf for page-count verification; PDFs are present')
    except (OSError, KeyError, AssertionError, ValueError, zipfile.BadZipFile) as exc:
        add('Archive validation', 'fail', type(exc).__name__)
    ci_verified, ci_evidence = False, "Run with --github after the current main workflow finishes"
    if args.github:
        remote = git('remote', 'get-url', 'origin')
        match = re.search(r'github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$', remote)
        if not match:
            add('GitHub metadata', 'fail', 'Unrecognized remote URL')
        else:
            repo = '/'.join(match.groups())
            try:
                req = Request('https://api.github.com/repos/'+repo, headers={'User-Agent':'Astro-PS-audit'})
                with urlopen(req, timeout=20) as response:
                    metadata = json.load(response)
                add('GitHub default branch', 'pass' if metadata['default_branch']=='main' else 'fail', metadata['default_branch'])
                head = git('rev-parse', 'HEAD')
                req = Request(f'https://api.github.com/repos/{repo}/actions/runs?head_sha={head}&per_page=20', headers={'User-Agent':'Astro-PS-audit'})
                with urlopen(req, timeout=20) as response:
                    workflows = json.load(response)['workflow_runs']
                current = next((r for r in workflows if r['name']=='AeroSentinel CI' and r['head_sha']==head and r['head_branch']=='main'), None)
                ci_evidence = {'commit':head, 'workflow':current['html_url'] if current else None,
                               'conclusion':current['conclusion'] if current else 'not found'}
                if current and current['conclusion']=='success':
                    req = Request(current['jobs_url'], headers={'User-Agent':'Astro-PS-audit'})
                    with urlopen(req, timeout=20) as response:
                        jobs = json.load(response)['jobs']
                    ci_verified = any(any(step['name']=='Measure isolated cold build' and step['conclusion']=='success'
                                          for step in job['steps']) for job in jobs)
                    ci_evidence['cold_build_budget_step_passed'] = ci_verified

                add('Private repository required by PS', 'pass' if metadata['private'] else 'exception',
                    'Public at user request' if not metadata['private'] else 'Private', True)
                add('Repository spelling equals registered team', 'pass' if metadata['name']==args.team_name else 'review',
                    {'actual':metadata['name'], 'registered':args.team_name, 'note':'Case differs; confirm accepted spelling with organizer'}, True)
            except Exception as exc:
                add('GitHub metadata', 'review', type(exc).__name__, True)
    add('Organizer collaborator access', 'review', 'Username supplied but access not yet independently confirmed' if args.organizer else 'Organizer GitHub username not supplied', True)
    add('Submission form, deadline and upload', 'review', 'Submission email/form and deadline confirmation required; no upload performed', True)
    add('Hackathon timing and authorship', 'review', 'Team must attest eligibility and understand the implementation; commit times alone do not prove authorship', True)
    try:
        record = json.loads((ROOT/'submission_bundle/validation-evidence.json').read_text(encoding='utf-8'))
        matched = provenance_matches(record, (harness, sandbox), runtime_contract)
        passed = matched and record['linux_tests']['passed'] and record['live_security_tests']['passed'] and record['gate']['passed']
        add('Source-matched local runtime validation record', 'pass' if passed else 'fail',
            {'linux_tests':record['linux_tests'], 'live_security_tests':record['live_security_tests'], 'gate':record['gate']['run_id'],
             'scope':'Recorded local checks; inspect raw evidence and the matching GitHub CI result independently'})
    except (OSError, ValueError, KeyError):
        add('Source-matched local runtime validation record', 'review', 'Validation record missing or unreadable')
    add('Current GitHub CI and isolated cold build', 'pass' if ci_verified else 'review', ci_evidence)
    bad = any(r['status']=='fail' for r in rows)
    open_items = any(r['status'] not in ('pass',) for r in rows)
    result = {'overall_ps_compliance': 'NOT_COMPLETE' if bad or open_items else 'PASS',
              'technical_evidence_failures': bad, 'contract_version': CONTRACT_VERSION,
              'harness_sha256': harness, 'sandbox_sha256': sandbox, 'runtime_contract_sha256': runtime_contract,
              'checks': rows, 'note':'Read-only audit; no model API calls, repository changes or form submission'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    for row in rows:
        print(row['status'].upper()+': '+row['requirement'])
    print('Overall PS compliance:', result['overall_ps_compliance'])
    print('Saved audit:', args.output)
    if bad or (args.strict and open_items):
        raise SystemExit(1)


if __name__=='__main__':
    main()
