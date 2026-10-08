"""Container worker for download-openneuro.sh; Python standard library only."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile


def run(args, cwd):
    print('+ ' + shlex.join(map(str, args)), flush=True)
    subprocess.run(args, cwd=cwd, check=True)


def files(root):
    """Walk payload only; reject nested repositories and directory links."""
    for directory, dirs, names in os.walk(root, followlinks=False):
        parent = Path(directory)
        if parent == root:
            dirs[:] = [d for d in dirs if d != '.git']
        elif '.git' in dirs or '.git' in names:
            raise RuntimeError(f'Nested repository is unsupported: {parent}')
        for name in dirs:
            if (parent / name).is_symlink():
                raise RuntimeError(f'Directory symlink is unsupported: {parent / name}')
        for name in names:
            yield parent / name


def materialize(root):
    # Validate all links before modifying any: no broken/external targets.
    links = []
    for path in files(root):
        if not path.is_file():
            raise RuntimeError(f'Missing or non-regular payload: {path}')
        if path.is_symlink():
            target = path.resolve(strict=True)
            if not target.is_relative_to(root.resolve()):
                raise RuntimeError(f'Link points outside dataset: {path}')
            links.append(path)
    for path in links:
        # Real copies, not hard links: duplicated annex keys become independent files.
        fd, temporary = tempfile.mkstemp(prefix='.materialize-', dir=path.parent)
        temporary = Path(temporary)
        try:
            digest = hashlib.sha256()
            with os.fdopen(fd, 'wb') as dst, path.open('rb') as src:
                while block := src.read(8 * 1024 * 1024):
                    dst.write(block)
                    digest.update(block)
                dst.flush()
                os.fsync(dst.fileno())
            with temporary.open('rb') as copied:
                if hashlib.file_digest(copied, 'sha256').digest() != digest.digest():
                    raise RuntimeError(f'Copy verification failed: {path}')
            shutil.copystat(path, temporary, follow_symlinks=True)
            temporary.chmod(temporary.stat().st_mode | 0o200)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    for path in files(root):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f'Payload not fully materialized: {path}')
    print(f'Materialized and verified {len(links)} links.', flush=True)


def download(spec, output, jobs):
    match = re.fullmatch(r'(ds[0-9]{6})(?:v([0-9]+\.[0-9]+\.[0-9]+))?', spec)
    if not match:
        raise ValueError(f'Invalid dataset ID: {spec}')
    accession, version = match.groups()
    destination = output / spec
    if os.path.lexists(destination):
        raise FileExistsError(f'Refusing to overwrite: {destination}')
    stage = Path(tempfile.mkdtemp(prefix=f'.{spec}.partial-', dir=output))
    dataset = stage / accession
    try:
        command = ['deno', 'run', '-A', 'jsr:@openneuro/cli@5.6.0', 'download']
        if version:
            command += ['--version', version]
        run(command + [accession, accession], stage)
        gitdir = dataset / '.git'
        if gitdir.is_symlink() or not gitdir.is_dir():
            raise RuntimeError('Expected a standalone Git dataset with a real .git directory')
        index = subprocess.check_output(['git', 'ls-files', '--stage', '-z'], cwd=dataset)
        if any(record.startswith(b'160000 ') for record in index.split(b'\0')):
            raise RuntimeError('Dataset contains subdatasets; refusing incomplete export')
        if version:
            head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=dataset)
            tagged = subprocess.check_output(
                ['git', 'rev-parse', f'refs/tags/{version}^{{commit}}'], cwd=dataset)
            if head != tagged:
                raise RuntimeError('Downloaded HEAD does not match requested version')
        run(['datalad', 'get', '-J', str(jobs), '.'], dataset)
        run(['git', 'annex', 'fsck', '--numcopies=1'], dataset)
        materialize(dataset)
        # Only the newly downloaded staging repository is removed.
        # git-annex deliberately makes object directories read-only.
        for directory, dirs, _ in os.walk(gitdir, followlinks=False):
            path = Path(directory)
            path.chmod(path.stat().st_mode | 0o700)
            for name in dirs:
                child = path / name
                if not child.is_symlink():
                    child.chmod(child.stat().st_mode | 0o700)
        shutil.rmtree(gitdir)
        if os.path.lexists(destination):
            raise FileExistsError(f'Output appeared during download: {destination}')
        dataset.rename(destination)
        stage.rmdir()
        print(f'COMPLETE: {destination}', flush=True)
    except BaseException:
        print(f'Incomplete download retained at: {stage}', flush=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('datasets', nargs='+')
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    output = Path.cwd().resolve()
    # The SIF cache is read-only. Seed a persistent writable cache to reuse its CLI.
    cache = output / '.openneuro-deno-cache'
    cache.mkdir(exist_ok=True)
    os.environ['DENO_DIR'] = str(cache)
    if Path('/opt/deno-cache').is_dir():
        shutil.copytree('/opt/deno-cache', cache, dirs_exist_ok=True)
    failed = []
    for spec in dict.fromkeys(args.datasets):
        try:
            download(spec, output, args.jobs)
        except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
            print(f'FAILED {spec}: {error}', flush=True)
            failed.append(spec)
    if failed:
        print('Failed datasets: ' + ' '.join(failed), flush=True)
    return bool(failed)


if __name__ == '__main__':
    raise SystemExit(main())
