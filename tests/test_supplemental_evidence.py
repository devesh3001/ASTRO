"""Packaging must not accept stale, mislabeled or unrelated extra validation."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from harness.provenance import CONTRACT_VERSION
from scripts.package_submission import SUPPLEMENTAL_REPORTS, validate_supplemental_report


class TestSupplementalEvidence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for target, value in (("ROOT", self.root), ("fingerprints", ("h", "s")),
                              ("contract_fingerprint", "c")):
            active=patch('scripts.package_submission.'+target, value) if target=='ROOT' else patch(
                'scripts.package_submission.'+target, return_value=value)
            active.start();self.addCleanup(active.stop)
        self.sources={}
        for name in SUPPLEMENTAL_REPORTS['runb-contract.json']:
            file=self.root/name;file.parent.mkdir(parents=True,exist_ok=True)
            file.write_bytes(b'# synthetic test source\r\n')
            self.sources[name]=hashlib.sha256(b'# synthetic test source\n').hexdigest()
        self.path=self.root/'runb-contract.json'
        self.record={'contract_version':CONTRACT_VERSION,'harness_sha256':'h','sandbox_sha256':'s',
                     'runtime_contract_sha256':'c','passed':True,'model_called':False,
                     'validation_sources':self.sources,'evidence_role':'synthetic_provider_conformance',
                     'real_model_calls':0,'external_provider_requests':0,'tests':13,
                     'failures':0,'errors':0,'skipped':0,'loopback_requests':20}

    def save(self):
        self.path.write_text(json.dumps(self.record),encoding='utf-8')

    def test_matching_sources_accept_crlf_and_lf(self):
        self.save()
        self.assertEqual(validate_supplemental_report(self.path),self.record)
        for name in self.sources:(self.root/name).write_bytes(b'# synthetic test source\n')
        self.assertEqual(validate_supplemental_report(self.path),self.record)

    def test_stale_validation_code_is_rejected(self):
        self.save()
        (self.root/next(iter(self.sources))).write_text('changed')
        with self.assertRaisesRegex(ValueError,'source changed'):
            validate_supplemental_report(self.path)

    def test_stale_runtime_or_contract_is_rejected(self):
        for field in ('harness_sha256','sandbox_sha256','runtime_contract_sha256','contract_version'):
            with self.subTest(field=field):
                original=self.record[field];self.record[field]='stale';self.save()
                with self.assertRaisesRegex(ValueError,'older runtime'):
                    validate_supplemental_report(self.path)
                self.record[field]=original

    def test_failed_or_real_model_report_is_rejected(self):
        for field,value in (('passed',False),('passed',1),('model_called',True),('model_called',None)):
            with self.subTest(field=field,value=value):
                original=self.record[field];self.record[field]=value;self.save()
                with self.assertRaisesRegex(ValueError,'explicitly offline'):
                    validate_supplemental_report(self.path)
                self.record[field]=original

    def test_source_allowlist_blocks_missing_and_external_paths(self):
        for sources in ({}, {'../.env':'abc'}, {**self.sources,'unrelated.py':'abc'}, []):
            with self.subTest(sources=sources):
                self.record['validation_sources']=sources;self.save()
                with self.assertRaisesRegex(ValueError,'allowlist'):
                    validate_supplemental_report(self.path)

    def test_unrecognized_report_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'Unknown supplemental'):
            validate_supplemental_report(self.root/'other.json')

    def test_role_and_external_request_counters_cannot_contradict_offline_label(self):
        for field,value in (('evidence_role','real_model_evaluation'),('evidence_role',None),
                            ('real_model_calls',1),('external_provider_requests',1),
                            ('real_model_calls',False),('external_provider_requests','0')):
            with self.subTest(field=field,value=value):
                original=self.record[field];self.record[field]=value;self.save()
                with self.assertRaisesRegex(ValueError,'role or request counters'):
                    validate_supplemental_report(self.path)
                self.record[field]=original

    def test_pass_flag_cannot_override_empty_or_failed_test_counts(self):
        for field,value in (('tests',0),('tests',True),('failures',1),('errors',1),
                            ('skipped',1),('loopback_requests',0)):
            with self.subTest(field=field,value=value):
                original=self.record[field];self.record[field]=value;self.save()
                with self.assertRaisesRegex(ValueError,'test counts'):
                    validate_supplemental_report(self.path)
                self.record[field]=original

    def test_archive_includes_exact_report_and_rejects_summary_mismatch(self):
        from scripts.package_submission import assemble
        from tests.test_packaging import TestEvidence
        import shutil,zipfile
        logs=self.root/'logs';logs.mkdir()
        fixture=TestEvidence()
        for index in range(3):fixture.fixture(logs,str(index),'failure' if index==0 else 'success')
        bundle=self.root/'submission_bundle';bundle.mkdir()
        for name in ('deck.pdf','design-doc.pdf'):(bundle/name).write_bytes(b'%PDF-synthetic-fixture')
        self.save();reports=logs/'benchmarks';reports.mkdir()
        shutil.copy2(self.path,reports/self.path.name)
        summary={key:self.record[key] for key in ('contract_version','harness_sha256',
                 'sandbox_sha256','runtime_contract_sha256')}
        summary['supplemental_validation']={self.path.name:dict(self.record)}
        reviewed=bundle/'validation-evidence.json'
        reviewed.write_text(json.dumps(summary))
        target=self.root/'Astro.zip'
        assemble('Astro',logs,target)
        with zipfile.ZipFile(target) as archive:
            self.assertEqual(json.loads(archive.read('baseline-results/runb-contract.json')),self.record)
        summary['supplemental_validation'][self.path.name]['tests']=14
        reviewed.write_text(json.dumps(summary))
        with self.assertRaisesRegex(ValueError,'reviewed summary'):
            assemble('Astro',logs,target)

    def mission_report(self):
        self.path=self.root/'mission-stress.json'
        sources={}
        for name in SUPPLEMENTAL_REPORTS[self.path.name]:
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'# independent stress source\n')
            sources[name]=hashlib.sha256(path.read_bytes()).hexdigest()
        support=self.root/'tests/support.py';support.write_bytes(b'# local HTTP fixture\n')
        self.record.update(evidence_role='offline_mission_stress',validation_sources=sources,
            complete=True,stopped_reason=None,episodes=2,expectations_met=2,failures=[],
            simulator_cases=1,http_cases=1,support_fixture_sha256=hashlib.sha256(support.read_bytes()).hexdigest(),
            results=[{'transport':'simulator','expectation_met':True,'passed':True,'expected_success':True},
                     {'transport':'http','expectation_met':True,'passed':False,'expected_success':False}])

    def test_stress_fixture_change_invalidates_earlier_measurement(self):
        self.mission_report();self.save()
        self.assertEqual(validate_supplemental_report(self.path),self.record)
        (self.root/'tests/support.py').write_text('changed fixture')
        with self.assertRaisesRegex(ValueError,'fixture source changed'):
            validate_supplemental_report(self.path)

    def test_stress_pass_flag_cannot_hide_incomplete_or_inconsistent_cases(self):
        self.mission_report()
        for field,value in (('complete',False),('episodes',0),('expectations_met',1),
                            ('http_cases',0),('simulator_cases',2),('failures',[{'error':'failed'}]),
                            ('results',[])):
            with self.subTest(field=field):
                original=self.record[field];self.record[field]=value;self.save()
                with self.assertRaisesRegex(ValueError,'case counts'):
                    validate_supplemental_report(self.path)
                self.record[field]=original
