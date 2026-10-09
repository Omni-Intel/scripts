"""AWS prefetch tests: real local annex, fake S3 transfers, no network."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('worker', Path(__file__).with_name('download-openneuro.py'))
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
UUID = '94543a26-e5d2-4e2f-a1c7-d0e31024cb0e'


class MetadataTests(unittest.TestCase):
    def test_hash_backends_and_unsafe_keys(self):
        for algorithm in ('md5', 'sha1', 'sha256', 'sha512'):
            digest = hashlib.new(algorithm, b'hello').hexdigest()
            key = algorithm.upper() + 'E-s5--' + digest + '.txt'
            self.assertEqual(worker.annex_hash(key), (algorithm, 5, digest))
            self.assertIsNone(worker.annex_hash(key + '/outside'))
        self.assertIsNone(worker.annex_hash('URL--anything'))
        self.assertIsNone(worker.annex_hash('MD5E-s1--1234'))

    def test_remote_selection_is_public_aws_only(self):
        text = '\n'.join([f'{UUID} type=S3 bucket=openneuro.org host=s3.amazonaws.com',
                           'private type=S3 bucket=openneuro-private host=s3.amazonaws.com',
                           'gcs type=S3 bucket=openneuro.org host=storage.googleapis.com'])
        self.assertEqual(worker.s3_remotes(text), {UUID})

    def test_version_removals_precision_and_prefix(self):
        text = f'''1.0000000001s {UUID}:V +old#ds000001/old
1.0000000002s {UUID}:V -old#ds000001/old
2s {UUID}:V +good#ds000001/a +other#ds000002/a
3s {UUID}:V +new#ds000001/b
4s other:V +no#ds000001/a
'''
        self.assertEqual(worker.s3_versions(text, {UUID}, 'ds000001'),
                         [{'version_id':'new','s3_key':'ds000001/b'}, {'version_id':'good','s3_key':'ds000001/a'}])

    def test_dataset_cancellation_does_not_cancel_other_datasets(self):
        parent = threading.Event()
        a, b = worker.DownloadStop(parent), worker.DownloadStop(parent)
        a.set()
        self.assertTrue(a.is_set())
        self.assertFalse(b.is_set())
        self.assertFalse(parent.is_set())
        parent.set()
        self.assertTrue(b.wait(0))

    def test_cancel_terminates_aws_process(self):
        stop = threading.Event()
        timer = threading.Timer(0.3, stop.set)
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(InterruptedError):
                worker.aws_command([sys.executable, '-c', 'import time; time.sleep(60)'], Path('/tmp'), stop)
        finally:
            timer.cancel()
        self.assertLess(time.monotonic() - started, 6)


class PrefetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'; self.repo.mkdir()
        self.stage = self.root / 'stage'; self.stage.mkdir()
        self.payload = b'aws payload\n' * 1024
        self.env = dict(os.environ, GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid',
                        GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid')
        self.git('init', '-q')
        self.git('annex', 'init', 'aws-test')
        for name in ('a.dat', 'b.dat'):
            (self.repo / name).write_bytes(self.payload)
        self.git('annex', 'add', '--backend=SHA256E', 'a.dat', 'b.dat')
        self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture')
        self.key = Path(os.readlink(self.repo / 'a.dat')).name
        self.git('annex', 'drop', '--force', 'a.dat', 'b.dat')
        self.cache = self.stage / 'aws-cache'
        self.cache.mkdir(); (self.cache / 'partial').mkdir()
        self.add_metadata()
        self.which = patch.object(worker.shutil, 'which', return_value='/fake/aws')
        self.which.start(); self.addCleanup(self.which.stop)

    def git(self, *args, input=None, env=None):
        return subprocess.check_output(['git', *args], cwd=self.repo, env=env or self.env,
                                       input=input, text=True, stderr=subprocess.STDOUT).strip()

    def add_metadata(self):
        env = dict(self.env, GIT_INDEX_FILE=str(self.root / 'metadata-index'))
        self.git('read-tree', 'git-annex', env=env)
        values = {'remote.log':f'{UUID} type=S3 bucket=openneuro.org host=s3.amazonaws.com autoenable=false name=s3-PUBLIC encryption=none\n',
                  self.key + '.log.rmet':f'1s {UUID}:V +historical#ds000001/a.dat\n'}
        for name, text in values.items():
            blob = self.git('hash-object', '-w', '--stdin', input=text)
            self.git('update-index', '--add', '--cacheinfo', '100644', blob, name, env=env)
        tree = self.git('write-tree', env=env)
        commit = self.git('commit-tree', tree, '-p', 'git-annex', input='test S3 metadata\n')
        self.git('update-ref', 'refs/heads/git-annex', commit)

    def fake_aws(self, args, cwd, stop):
        self.assertEqual(args[args.index('--version-id') + 1], 'historical')
        self.assertIn('--no-sign-request', args)
        Path(args[-1]).write_bytes(self.payload)

    def test_manifest_deduplicates_and_uses_version_mapping(self):
        items = worker.aws_manifest(self.repo, 'ds000001')
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['key'], self.key)
        self.assertEqual(items[0]['candidates'], [{'version_id':'historical','s3_key':'ds000001/a.dat'}])

    def test_download_import_and_skip_on_resume(self):
        with patch.object(worker, 'aws_command', self.fake_aws):
            worker.aws_prefetch(self.repo, self.stage, 'ds000001', 2)
        for name in ('a.dat', 'b.dat'):
            self.assertEqual((self.repo / name).read_bytes(), self.payload)
        self.git('annex', 'fsck', '--numcopies=1', '--', 'a.dat', 'b.dat')
        self.assertEqual(self.git('status', '--porcelain'), '')
        summary = json.loads((self.stage / 'aws-summary.json').read_text())
        self.assertEqual(summary['downloaded'], 1)
        self.assertEqual(summary['imported'], 1)
        with patch.object(worker, 'aws_command') as download:
            worker.aws_prefetch(self.repo, self.stage, 'ds000001', 2)
        download.assert_not_called()

    def test_completed_cache_recovers_without_network(self):
        (self.cache / self.key).write_bytes(self.payload)
        with patch.object(worker, 'aws_command') as download:
            worker.aws_prefetch(self.repo, self.stage, 'ds000001', 1)
        download.assert_not_called()
        self.assertEqual((self.repo / 'a.dat').read_bytes(), self.payload)
        self.assertEqual(json.loads((self.stage / 'aws-summary.json').read_text())['reused'], 1)

    def test_full_partial_recovers_after_interruption(self):
        item = worker.aws_manifest(self.repo, 'ds000001')[0]
        def interrupted(args, cwd, stop):
            Path(args[-1]).write_bytes(self.payload)
            raise InterruptedError('interrupted after transfer')
        with patch.object(worker, 'aws_command', interrupted), self.assertRaises(InterruptedError):
            worker.aws_fetch(item, self.cache, threading.Event())
        with patch.object(worker, 'aws_command') as download:
            worker.aws_prefetch(self.repo, self.stage, 'ds000001', 1)
        download.assert_not_called()
        self.assertEqual((self.repo / 'a.dat').read_bytes(), self.payload)

    def test_mismatch_never_imports(self):
        def wrong(args, cwd, stop):
            Path(args[-1]).write_bytes(b'wrong')
        with patch.object(worker, 'aws_command', wrong):
            worker.aws_prefetch(self.repo, self.stage, 'ds000001', 1)
        self.assertFalse((self.repo / 'a.dat').exists())
        self.assertEqual(json.loads((self.stage / 'aws-summary.json').read_text())['fallback'], 1)

    def test_transport_failure_defers_without_trying_all_versions(self):
        item = worker.aws_manifest(self.repo, 'ds000001')[0]
        item['candidates'] *= 3
        with patch.object(worker, 'aws_command', side_effect=RuntimeError('network')) as download:
            result = worker.aws_fetch(item, self.cache, threading.Event())
        self.assertFalse(result['ok'])
        self.assertEqual(download.call_count, 1)
        self.assertFalse((self.repo / 'a.dat').exists())

    def test_missing_mapping_defers_to_datalad(self):
        with patch.object(worker, 'aws_command') as download:
            worker.aws_prefetch(self.repo, self.stage, 'ds999999', 1)
        download.assert_not_called()
        self.assertFalse((self.repo / 'a.dat').exists())
        self.assertEqual(json.loads((self.stage / 'aws-summary.json').read_text())['fallback'], 1)

    def test_symlink_cache_rejected(self):
        outside = self.root / 'outside'; outside.write_bytes(b'preserve')
        (self.cache / self.key).symlink_to(outside)
        item = worker.aws_manifest(self.repo, 'ds000001')[0]
        with self.assertRaisesRegex(RuntimeError, 'symlink'):
            worker.aws_fetch(item, self.cache, threading.Event())
        self.assertEqual(outside.read_bytes(), b'preserve')

if __name__ == '__main__':
    unittest.main()