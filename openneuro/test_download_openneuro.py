"""Run inside download-tools.sif: python3 -B test_download_openneuro.py."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import download_openneuro as worker


class DownloadTests(unittest.TestCase):
    def test_export(self):
        actual_run = worker.run
        calls = []

        def fixture(args, cwd):
            if args[0] != 'deno':
                return actual_run(args, cwd)
            calls.append(args)
            repo = cwd / args[-1]
            repo.mkdir()
            for command in [['git', 'init', '-q'], ['git', 'annex', 'init', 'test']]:
                subprocess.run(command, cwd=repo, check=True)
            for name in ['one', 'two']:
                (repo / name).write_bytes(b'payload' * 1000)
            subprocess.run(['git', 'annex', 'add', '.'], cwd=repo, check=True)
            subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                            '-c', 'commit.gpgsign=false', 'commit', '-qm', 'test'], cwd=repo, check=True)
            subprocess.run(['git', 'tag', '1.0.3'], cwd=repo, check=True)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(worker, 'run', fixture):
                for spec in ['ds002721', 'ds002721v1.0.3']:
                    worker.download(spec, root, 1)
                    dataset = root / spec
                    self.assertFalse((dataset / '.git').exists())
                    for name in ['one', 'two']:
                        self.assertFalse((dataset / name).is_symlink())
                        self.assertEqual((dataset / name).read_bytes(), b'payload' * 1000)
                    self.assertNotEqual((dataset / 'one').stat().st_ino, (dataset / 'two').stat().st_ino)
                with self.assertRaises(FileExistsError):
                    worker.download('ds002721', root, 1)
            self.assertNotIn('--version', calls[0])
            self.assertEqual(calls[1][-4:], ['--version', '1.0.3', 'ds002721', 'ds002721'])

    def test_invalid_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'dataset'
            root.mkdir()
            (root / '.git').mkdir()
            link = root / 'link'
            link.symlink_to('.git/missing')
            with self.assertRaises(RuntimeError):
                worker.materialize(root)
            self.assertTrue((root / '.git').is_dir())
            link.unlink()
            outside = Path(tmp) / 'outside'
            outside.write_text('preserve')
            link.symlink_to(outside)
            with self.assertRaises(RuntimeError):
                worker.materialize(root)
            self.assertEqual(outside.read_text(), 'preserve')

    def test_failure_retains_git(self):
        def fail(args, cwd):
            if args[0] == 'deno':
                repo = cwd / args[-1]
                repo.mkdir()
                (repo / '.git').mkdir()
            else:
                raise subprocess.CalledProcessError(1, args)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(worker, 'run', fail), patch.object(subprocess, 'check_output', return_value=b''):
                with self.assertRaises(subprocess.CalledProcessError):
                    worker.download('ds002721', root, 1)
            self.assertFalse((root / 'ds002721').exists())
            self.assertEqual(len(list(root.glob('.ds002721.partial-*/ds002721/.git'))), 1)


if __name__ == '__main__':
    unittest.main()
