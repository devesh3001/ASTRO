"""Run source-bound synthetic judge-interface checks; no external model or API credits."""
import argparse
import hashlib
import importlib.metadata
import json
import platform
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harness.provenance import CONTRACT_VERSION, contract_fingerprint, fingerprints
from tests import test_judge_contract


def normalized_hash(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'logs'/'benchmarks'/'runb-contract.json')
    args = parser.parse_args()
    started = time.monotonic()
    test_judge_contract.SCENARIO_RECORDS.clear()
    suite = unittest.defaultTestLoader.loadTestsFromModule(test_judge_contract)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    harness, sandbox = fingerprints(ROOT)
    try:
        sdk = importlib.metadata.version('openai')
    except importlib.metadata.PackageNotFoundError:
        sdk = None
    report = {'created_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'evidence_role':'synthetic_provider_conformance',
              'model_called':False, 'real_model_calls':0, 'external_provider_requests':0,
              'scope':'Real OpenAI SDK subprocess and episode runner against loopback HTTP fixtures and a deterministic test-only environment. No undisclosed model, real judge append, satellite outcome or cross-provider capability is evaluated.',
              'contract_version':CONTRACT_VERSION, 'harness_sha256':harness, 'sandbox_sha256':sandbox,
              'runtime_contract_sha256':contract_fingerprint(ROOT),
              'validation_sources':{name:normalized_hash(ROOT/name) for name in ('scripts/check_runb_contract.py','tests/test_judge_contract.py')},
              'python':platform.python_version(), 'openai_sdk':sdk,
              'tests':result.testsRun, 'failures':len(result.failures), 'errors':len(result.errors),
              'skipped':len(result.skipped),
              'passed':result.wasSuccessful() and result.testsRun>=8 and not result.skipped,
              'elapsed_s':round(time.monotonic()-started,3),
              'failure_details':[{'test':str(test),'traceback':detail} for test,detail in result.failures+result.errors],
              'scenarios':test_judge_contract.SCENARIO_RECORDS,
              'loopback_requests':sum(s['requests'] for s in test_judge_contract.SCENARIO_RECORDS),
              'limitations':['Synthetic conformance is not unknown-model ability or a live judge run.',
                             'Source/build hashes describe expected inputs, not an active Docker daemon attestation.',
                             'The fixture environment checks protocol transitions and does not exercise satellite physics.']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:report[key] for key in ('passed','tests','failures','errors','skipped','elapsed_s','loopback_requests')},indent=2))
    print('Saved synthetic conformance evidence:', args.output)
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
