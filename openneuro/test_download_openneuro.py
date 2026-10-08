"""Container integration tests with synthetic annex data; no OpenNeuro downloads."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import download_openneuro as worker


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.calls = []
        self.actual_run = worker.run
        self.run_patch = patch.object(worker, 'run', self.fixture)
        self.run_patch.start()
        self.addCleanup(self.run_patch.stop)
        self.latest = patch.object(worker, 'latest_version', return_value='1.0.3').start()
        self.addCleanup(patch.stopall)

    def fixture(self, args, cwd):
        self.calls.append(args)
        if args[0] != 'deno':
            return self.actual_run(args, cwd)
        repo = cwd / args[-1]
        if repo.exists():
            return
        repo.mkdir()
        for command in [['git', 'init', '-q'], ['git', 'annex', 'init', 'test']]:
            subprocess.run(command, cwd=repo, check=True, stdout=subprocess.DEVNULL)
        for name in ['one', 'two']:
            (repo / name).write_bytes(b'payload' * 1000)
        subprocess.run(['git', 'annex', 'add', '.'], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                        '-c', 'commit.gpgsign=false', 'commit', '-qm', 'test'], cwd=repo, check=True)
        subprocess.run(['git', 'tag', '1.0.3'], cwd=repo, check=True)

    def stage(self, spec='ds002721'):
        return self.root / '.openneuro-work' / spec

    def state(self):
        return json.loads((self.stage() / 'state.json').read_text())

    def invoke(self, spec='ds002721'):
        worker.download(spec, self.root, 1)

    def assert_export(self, spec='ds002721'):
        repo = self.root / spec
        self.assertFalse((repo / '.git').exists())
        for name in ['one', 'two']:
            self.assertFalse((repo / name).is_symlink())
            self.assertEqual((repo / name).read_bytes(), b'payload' * 1000)
        self.assertNotEqual((repo / 'one').stat().st_ino, (repo / 'two').stat().st_ino)
        receipt = json.loads((repo / worker.RECEIPT).read_text())
        self.assertEqual(receipt['version'], '1.0.3')

    def test_export_versions_and_completed_skip(self):
        for spec in ['ds002721', 'ds002721v1.0.3']:
            self.invoke(spec)
            self.assert_export(spec)
        self.latest.assert_called_once()
        calls = len(self.calls)
        self.invoke()
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(self.state()['phase'], 'complete')
        clones = [c for c in self.calls if c[0] == 'deno']
        self.assertTrue(all(c[-4:] == ['--version', '1.0.3', 'ds002721', 'ds002721'] for c in clones))

    def test_existing_output_is_not_overwritten(self):
        (self.root / 'ds002721').mkdir()
        with self.assertRaises(FileExistsError):
            self.invoke()
        self.assertEqual(self.calls, [])

    def test_clone_failure_pins_latest(self):
        with patch.object(worker, 'run', side_effect=subprocess.CalledProcessError(1, ['deno'])):
            with self.assertRaises(subprocess.CalledProcessError):
                self.invoke()
        self.assertEqual(self.state()['version'], '1.0.3')
        self.latest.return_value = '2.0.0'
        self.invoke()
        self.latest.assert_called_once()
        self.assert_export()

    def test_get_failure_reuses_repository(self):
        def fail(args, cwd):
            if args[0] == 'datalad':
                raise subprocess.CalledProcessError(1, args)
            return self.fixture(args, cwd)
        with patch.object(worker, 'run', fail):
            with self.assertRaises(subprocess.CalledProcessError):
                self.invoke()
        self.assertEqual(self.state()['phase'], 'get')
        self.invoke()
        self.assertEqual(sum(c[0] == 'deno' for c in self.calls), 1)
        self.assert_export()

    def interrupt_materialize(self):
        replace = worker.os.replace
        def interrupt(src, dst):
            replace(src, dst)
            if Path(dst).name in ('one', 'two'):
                raise KeyboardInterrupt()
        with patch.object(worker.os, 'replace', interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke()
        self.assertEqual(self.state()['phase'], 'materialize')

    def test_materialize_resume_skips_download(self):
        self.interrupt_materialize()
        calls = len(self.calls)
        self.invoke()
        self.assertEqual(len(self.calls), calls)
        self.assert_export()

    def test_modified_materialized_file_blocks_cleanup(self):
        self.interrupt_materialize()
        repo = self.stage() / 'ds002721'
        path = next(p for p in [repo / 'one', repo / 'two'] if not p.is_symlink())
        path.write_text('corrupted')
        with self.assertRaises(RuntimeError):
            self.invoke()
        self.assertTrue((repo / '.git').is_dir())
        self.assertFalse((self.root / 'ds002721').exists())

    def test_cleanup_resume_after_git_removed(self):
        remove = worker.remove_git
        def interrupt(path):
            remove(path)
            raise KeyboardInterrupt()
        with patch.object(worker, 'remove_git', interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke()
        self.assertEqual(self.state()['phase'], 'cleanup')
        self.invoke()
        self.assert_export()

    def test_publish_resume_after_rename(self):
        write = worker.write_json
        def interrupt(path, value):
            if path.name == 'state.json' and value.get('phase') == 'complete':
                raise KeyboardInterrupt()
            write(path, value)
        with patch.object(worker, 'write_json', interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke()
        self.assertEqual(self.state()['phase'], 'publish')
        self.assertTrue((self.root / 'ds002721').is_dir())
        self.invoke()
        self.assert_export()

    def test_concurrent_process_is_rejected(self):
        self.stage().mkdir(parents=True)
        with worker.lock(self.stage() / 'lock'):
            with self.assertRaises(RuntimeError):
                self.invoke()
        self.assertEqual(self.calls, [])

    def test_fsck_failure_returns_to_get(self):
        def fail(args, cwd):
            if args[:3] == ['git', 'annex', 'fsck']:
                raise subprocess.CalledProcessError(1, args)
            return self.fixture(args, cwd)
        with patch.object(worker, 'run', fail):
            with self.assertRaises(subprocess.CalledProcessError):
                self.invoke()
        self.assertEqual(self.state()['phase'], 'get')
        self.invoke()
        self.assert_export()
        self.assertEqual(sum(c[0] == 'datalad' for c in self.calls), 2)

    def test_changed_commit_rejected_on_resume(self):
        def fail(args, cwd):
            if args[0] == 'datalad':
                raise subprocess.CalledProcessError(1, args)
            return self.fixture(args, cwd)
        with patch.object(worker, 'run', fail):
            with self.assertRaises(subprocess.CalledProcessError):
                self.invoke()
        repo = self.stage() / 'ds002721'
        subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                        '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-qm', 'changed'],
                       cwd=repo, check=True)
        with self.assertRaises(RuntimeError):
            self.invoke()
        self.assertTrue((repo / '.git').is_dir())

    def test_invalid_links(self):
        repo = self.root / 'data'
        repo.mkdir()
        (repo / '.git').mkdir()
        link = repo / 'link'
        link.symlink_to('.git/missing')
        with self.assertRaises(RuntimeError):
            worker.materialize(repo)
        link.unlink()
        outside = self.root / 'outside'
        outside.write_text('preserve')
        link.symlink_to(outside)
        with self.assertRaises(RuntimeError):
            worker.materialize(repo)
        self.assertEqual(outside.read_text(), 'preserve')


if __name__ == '__main__':
    unittest.main()
