"""Bash bootstrap tests using a fake runtime; no network or real downloads."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

RUNTIME = r'''#!/usr/bin/python3
import os, pathlib, subprocess, sys
args = sys.argv[1:]
binding = args[args.index('--bind') + 1]
root = binding.split(':')[0]
with open(os.environ['CALLS'], 'a') as log:
    log.write(('curl' if 'curl' in args else 'check' if '-c' in args else 'worker') + '\n')
if 'curl' in args:
    target = pathlib.Path(root) / pathlib.Path(args[args.index('--output') + 1]).name
    mode = os.environ.get('DOWNLOAD_MODE', 'ok')
    target.write_text('<html>error</html>' if mode == 'invalid' else "print('downloaded')\n")
    sys.exit(22 if mode == 'fail' else 0)
if '-c' in args:
    command = args[args.index('python3'):]
    command[-1] = str(pathlib.Path(root) / pathlib.Path(command[-1]).name)
    sys.exit(subprocess.call(command))
assert args[args.index('--dataset-jobs') + 1] == os.environ.get('EXPECTED_DATASET_JOBS', '1')
assert args[args.index('--backend') + 1] == os.environ.get('EXPECTED_BACKEND', 'aws')
sys.exit(0)
'''


class BootstrapTests(unittest.TestCase):
    def check_case(self, mode='ok', local=False, parallel=1, piped=False, backend=None):
        with tempfile.TemporaryDirectory(prefix='bootstrap test ') as tmp:
            root = Path(tmp)
            shell = root / 'download-openneuro.sh'
            shell.write_bytes(Path(__file__).with_name('download-openneuro.sh').read_bytes())
            runtime = root / 'apptainer'
            runtime.write_text(RUNTIME)
            runtime.chmod(0o755)
            image = root / 'test.sif'
            image.touch()
            worker_dir = root / 'output' / '.openneuro-tools' if piped else root
            worker_dir.mkdir(parents=True, exist_ok=True)
            worker = worker_dir / 'download-openneuro.py'
            if local:
                worker.write_text('# local worker\n')
            calls = root / 'calls'
            env = dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'],
                       OPENNEURO_CONTAINER=str(image), CALLS=str(calls), DOWNLOAD_MODE=mode, EXPECTED_DATASET_JOBS=str(parallel), EXPECTED_BACKEND=backend or 'aws')
            result = subprocess.run(['bash', *(['-s', '--'] if piped else [str(shell)]), '-o', str(root / 'output'), '-p', str(parallel), *(['--backend', backend] if backend else []), 'ds002721'],
                                    env=env, text=True, capture_output=True,
                                    input=shell.read_text() if piped else None)
            invocations = calls.read_text().splitlines()
            self.assertEqual(list(worker_dir.glob('.download-openneuro.py.*')), [])
            if local:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(worker.read_text(), '# local worker\n')
                self.assertEqual(invocations, ['worker'])
            elif mode == 'ok':
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(worker.read_text(), "print('downloaded')\n")
                self.assertEqual(invocations, ['curl', 'check', 'worker'])
            else:
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(worker.exists())
                self.assertNotIn('worker', invocations)

    def test_datalad_backend_forwarded(self):
        self.check_case(local=True, backend='datalad')

    def test_stdin_downloads_worker(self):
        self.check_case(piped=True, parallel=4)

    def test_stdin_reuses_worker(self):
        self.check_case(piped=True, local=True)

    def test_stdin_failed_download_is_cleaned(self):
        self.check_case(piped=True, mode='fail')

    def test_existing_worker_is_untouched(self):
        self.check_case(local=True)

    def test_missing_worker_is_downloaded(self):
        self.check_case()

    def test_dataset_parallelism_is_forwarded(self):
        self.check_case(local=True, parallel=4)

    def test_failed_download_is_cleaned(self):
        self.check_case(mode='fail')

    def test_invalid_python_is_rejected(self):
        self.check_case(mode='invalid')


if __name__ == '__main__':
    unittest.main()
