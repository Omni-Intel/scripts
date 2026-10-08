"""Resumable OpenNeuro downloader; run through download-openneuro.sh."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import urllib.request
import uuid

CLI = 'openneuro'
RECEIPT = '.openneuro-download.json'
PHASES = ('resolve', 'clone', 'get', 'verify', 'materialize', 'cleanup', 'publish', 'complete')


def run(args, cwd):
    print('+ ' + shlex.join(map(str, args)), flush=True)
    subprocess.run(args, cwd=cwd, check=True)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.state-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def lock(path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f'Another process is using {path.parent}') from error
        yield


def real_directory(path):
    if path.is_symlink():
        raise RuntimeError(f'Refusing symlink directory: {path}')
    path.mkdir(exist_ok=True)


def latest_version(accession, endpoint):
    query = 'query($id: ID!) { dataset(id: $id) { latestSnapshot { tag } } }'
    headers = {'Content-Type': 'application/json'}
    token = os.environ.get('OPENNEURO_API_KEY')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(
        endpoint + '/crn/graphql',
        data=json.dumps({'query': query, 'variables': {'id': accession}}).encode(),
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        body = json.load(response)
    if body.get('errors'):
        raise RuntimeError('OpenNeuro: ' + '; '.join(e.get('message', 'Query failed') for e in body['errors']))
    snapshot = ((body.get('data') or {}).get('dataset') or {}).get('latestSnapshot') or {}
    version = snapshot.get('tag', '')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise RuntimeError(f'No released semantic-version snapshot for {accession}')
    return version


def files(root):
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
            if parent == root and name == RECEIPT:
                continue
            yield parent / name


def check_file(path, root):
    if not path.is_file():
        raise RuntimeError(f'Missing or non-regular payload: {path}')
    if not path.resolve(strict=True).is_relative_to(root.resolve()):
        raise RuntimeError(f'Link points outside dataset: {path}')


def signature(path):
    with path.open('rb') as stream:
        return {'size': os.fstat(stream.fileno()).st_size,
                'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}


def inventory(root):
    result = {}
    cache = {}
    for path in files(root):
        check_file(path, root)
        stat = path.stat()
        key = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        if key not in cache:
            cache[key] = signature(path)
        result[path.relative_to(root).as_posix()] = cache[key]
    if not result:
        raise RuntimeError('Dataset has no payload files')
    return result


def validate_export(root, manifest):
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError(f'Missing or unsafe dataset directory: {root}')
    paths = list(files(root))
    if {p.relative_to(root).as_posix() for p in paths} != set(manifest):
        raise RuntimeError('Payload paths differ from the saved manifest')
    for path in paths:
        check_file(path, root)
        if path.is_symlink() or signature(path) != manifest[path.relative_to(root).as_posix()]:
            raise RuntimeError(f'Export verification failed: {path}')


def materialize(root, manifest=None, scratch=None):
    manifest = inventory(root) if manifest is None else manifest
    scratch = root.parent / 'copy-buffer' if scratch is None else scratch
    real_directory(scratch)
    paths = list(files(root))
    if {p.relative_to(root).as_posix() for p in paths} != set(manifest):
        raise RuntimeError('Payload paths differ from the saved manifest')
    # Preflight all links before replacing any of them.
    for path in paths:
        check_file(path, root)
    for path in paths:
        expected = manifest[path.relative_to(root).as_posix()]
        if not path.is_symlink():
            if signature(path) != expected:
                raise RuntimeError(f'Previously materialized file changed: {path}')
            continue
        # A single deterministic buffer lives outside the payload, so a killed
        # process never leaves an untracked partial file in the exported dataset.
        temporary = scratch / 'file'
        temporary.unlink(missing_ok=True)
        try:
            with path.open('rb') as src, temporary.open('xb') as dst:
                shutil.copyfileobj(src, dst, 8 * 1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
            if signature(temporary) != expected:
                raise RuntimeError(f'Copy verification failed: {path}')
            shutil.copystat(path, temporary, follow_symlinks=True)
            temporary.chmod(temporary.stat().st_mode | 0o200)
            os.replace(temporary, path)
            sync_dir(path.parent)
        finally:
            temporary.unlink(missing_ok=True)
    validate_export(root, manifest)


def remove_git(gitdir):
    if gitdir.is_symlink():
        raise RuntimeError('Refusing symlink .git directory')
    if not gitdir.exists():
        return
    for directory, dirs, _ in os.walk(gitdir, followlinks=False):
        path = Path(directory)
        path.chmod(path.stat().st_mode | 0o700)
        for name in dirs:
            child = path / name
            if not child.is_symlink():
                child.chmod(child.stat().st_mode | 0o700)
    shutil.rmtree(gitdir)
    sync_dir(gitdir.parent)


def git_text(dataset, *args):
    return subprocess.check_output(['git', *args], cwd=dataset, text=True).strip()


def check_repo(dataset, state):
    if dataset.is_symlink() or (dataset / '.git').is_symlink() or not (dataset / '.git').is_dir():
        raise RuntimeError('Expected a standalone Git dataset with a real .git directory')
    head = git_text(dataset, 'rev-parse', 'HEAD')
    tagged = git_text(dataset, 'rev-parse', f"refs/tags/{state['version']}^{{commit}}")
    if head != tagged or (state.get('commit') and head != state['commit']):
        raise RuntimeError('Repository does not match the pinned version/commit')
    index = subprocess.check_output(['git', 'ls-files', '--stage', '-z'], cwd=dataset)
    if any(record.startswith(b'160000 ') for record in index.split(b'\0')):
        raise RuntimeError('Dataset contains subdatasets; refusing incomplete export')
    return head


def download(spec, output, jobs, cli=CLI):
    match = re.fullmatch(r'(ds[0-9]{6})(?:v([0-9]+\.[0-9]+\.[0-9]+))?', spec)
    if not match:
        raise ValueError(f'Invalid dataset ID: {spec}')
    accession, requested_version = match.groups()
    work = output / '.openneuro-work'
    real_directory(work)
    stage = work / spec
    real_directory(stage)
    destination = output / spec
    endpoint = os.environ.get('OPENNEURO_URL', 'https://openneuro.org').rstrip('/')
    with lock(stage / 'lock'):
        state_path = stage / 'state.json'
        if state_path.exists():
            state = json.loads(state_path.read_text())
            if (state.get('schema') != 1 or state.get('spec') != spec or
                    state.get('endpoint') != endpoint or state.get('phase') not in PHASES):
                raise RuntimeError(f'Incompatible saved state: {state_path}')
        else:
            if os.path.lexists(destination):
                raise FileExistsError(f'Refusing to overwrite: {destination}')
            if any(p.name != 'lock' for p in stage.iterdir()):
                raise RuntimeError(f'Work directory has files but no state: {stage}')
            state = {'schema': 1, 'spec': spec, 'endpoint': endpoint,
                     'version': requested_version, 'phase': 'resolve', 'run_id': uuid.uuid4().hex}
            write_json(state_path, state)

        def phase(value):
            state['phase'] = value
            write_json(state_path, state)

        dataset = stage / accession
        print(f"{spec}: resuming phase={state['phase']}, version={state['version']}", flush=True)
        try:
            if os.path.lexists(destination) and state['phase'] not in ('publish', 'complete'):
                raise FileExistsError(f'Refusing to overwrite: {destination}')
            if state['phase'] == 'resolve':
                state['version'] = requested_version or latest_version(accession, endpoint)
                phase('clone')  # Pin latest BEFORE the first download attempt.
            if state['phase'] == 'clone':
                if dataset.is_symlink():
                    raise RuntimeError('Refusing symlink dataset directory')
                run([cli, 'download', '--version', state['version'],
                     accession, accession], stage)
                state['commit'] = check_repo(dataset, state)
                if os.path.lexists(dataset / RECEIPT):
                    raise RuntimeError(f'Dataset uses reserved filename {RECEIPT}')
                phase('get')
            if state['phase'] == 'get':
                check_repo(dataset, state)
                run(['datalad', 'get', '-J', str(jobs), '.'], dataset)
                phase('verify')
            if state['phase'] == 'verify':
                check_repo(dataset, state)
                try:
                    run(['git', 'annex', 'fsck', '--numcopies=1'], dataset)
                except subprocess.CalledProcessError:
                    # fsck may quarantine a corrupt object. Retry get next time.
                    phase('get')
                    raise
                manifest = inventory(dataset)
                tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=dataset)
                tracked_paths = {os.fsdecode(p) for p in tracked.split(b'\0') if p}
                if not tracked_paths.issubset(manifest):
                    raise RuntimeError('Tracked files are missing from the payload')
                write_json(stage / 'manifest.json', manifest)
                phase('materialize')
            manifest = json.loads((stage / 'manifest.json').read_text())
            if state['phase'] == 'materialize':
                check_repo(dataset, state)
                materialize(dataset, manifest, stage / 'copy-buffer')
                phase('cleanup')
            if state['phase'] == 'cleanup':
                # This stage remains recoverable even if .git is partially deleted.
                validate_export(dataset, manifest)
                remove_git(dataset / '.git')
                write_json(dataset / RECEIPT, {k: state[k] for k in
                           ('schema', 'spec', 'endpoint', 'version', 'commit', 'run_id')})
                phase('publish')
            if state['phase'] == 'publish':
                if os.path.lexists(destination):
                    # Recover an interruption after rename but before state update.
                    if dataset.exists():
                        raise FileExistsError(f'Refusing to overwrite: {destination}')
                else:
                    validate_export(dataset, manifest)
                    dataset.rename(destination)
                    sync_dir(output)
                    sync_dir(stage)
                validate_receipt(destination, state)
                validate_export(destination, manifest)
                phase('complete')
            validate_receipt(destination, state)
            print(f"COMPLETE: {destination} (version {state['version']})", flush=True)
        except BaseException:
            print(f'Resume with the same command. Saved state: {stage}', flush=True)
            raise


def validate_receipt(destination, state):
    receipt = destination / RECEIPT
    if destination.is_symlink() or receipt.is_symlink() or not receipt.is_file():
        raise RuntimeError(f'Output is missing its completion receipt: {destination}')
    saved = json.loads(receipt.read_text())
    if any(saved.get(key) != state.get(key) for key in
           ('schema', 'spec', 'endpoint', 'version', 'commit', 'run_id')):
        raise RuntimeError(f'Output does not belong to this download: {destination}')
    if os.path.lexists(destination / '.git'):
        raise RuntimeError(f'Output still contains .git: {destination}')


def writable_cli(executable, cache, identity):
    """Relocate a deno-install launcher's config/lock/node_modules out of SIF."""
    launcher = Path(executable).read_text()
    command = None
    for line in launcher.splitlines():
        if line.startswith('exec '):
            parts = shlex.split(line)
            if len(parts) > 3 and Path(parts[1]).name == 'deno' and parts[2] == 'run':
                command = parts[1:]
                break
    if command is None or '--config' not in command:
        return executable
    if command[-1] != '$@':
        raise RuntimeError('Unsupported Deno launcher argument forwarding')
    command.pop()
    config_index = command.index('--config') + 1
    source_config = Path(command[config_index])
    if not source_config.is_absolute() or not source_config.is_file():
        raise RuntimeError(f'Invalid installed Deno configuration: {source_config}')
    runtime_identity = dict(identity, config=signature(source_config))
    source_lock = source_config.parent / 'deno.lock'
    if source_lock.is_file():
        runtime_identity['lock'] = signature(source_lock)
    key = hashlib.sha256(json.dumps(runtime_identity, sort_keys=True).encode()).hexdigest()[:24]
    runtimes = cache / 'cli-runtime'
    real_directory(runtimes)
    runtime = runtimes / key
    real_directory(runtime)
    wrapper = runtime / 'openneuro'
    if not wrapper.exists():
        config_dir = runtime / 'config'
        # Copy the whole installation config directory, including npm dependencies.
        shutil.copytree(source_config.parent, config_dir, dirs_exist_ok=True)
        for directory, _, names in os.walk(config_dir):
            parent = Path(directory)
            parent.chmod(parent.stat().st_mode | 0o700)
            for name in names:
                path = parent / name
                if not path.is_symlink():
                    path.chmod(path.stat().st_mode | 0o600)
        command[config_index] = str(config_dir / source_config.name)
        command[0] = shutil.which(command[0]) or command[0]
        fd, temporary = tempfile.mkstemp(prefix='.launcher-', dir=runtime)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write('#!/bin/sh\nexec ' + shlex.join(command) + ' "$@"\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o755)
            os.replace(temporary, wrapper)
            sync_dir(runtime)
        finally:
            Path(temporary).unlink(missing_ok=True)
    # git-annex-remote-openneuro calls `openneuro` via PATH as well.
    os.environ['PATH'] = str(runtime) + os.pathsep + os.environ['PATH']
    return str(wrapper)

def prepare_cli(output):
    """Use the installed Deno launcher, just like the annex special remote."""
    executable = shutil.which(CLI)
    if executable is None:
        raise RuntimeError('Container has no openneuro executable on PATH; install the Deno CLI in the container')
    # Probe against the container's original cache before selecting our writable
    # cache. No version is inferred from the dataset or from an old cache marker.
    source = Path(os.environ.get('DENO_DIR', '/opt/deno-cache')).resolve()
    version = subprocess.check_output(
        [executable, '--version'], text=True, stderr=subprocess.STDOUT, timeout=60,
    )
    version = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', version).strip()
    if not version:
        raise RuntimeError('Installed openneuro --version returned no version information')
    identity = {'command': executable, 'version': version, 'source_cache': str(source),
                'launcher_sha256': hashlib.sha256(Path(executable).read_bytes()).hexdigest()}
    cache = output / '.openneuro-deno-cache'
    real_directory(cache)
    marker = cache / '.seeded'
    with lock(cache / 'seed.lock'):
        previous = json.loads(marker.read_text()) if marker.exists() else None
        if previous != identity:
            if source.is_dir() and source != cache.resolve():
                shutil.copytree(source, cache, dirs_exist_ok=True)
            write_json(marker, identity)
        runtime_cli = writable_cli(executable, cache, identity)
    os.environ['DENO_DIR'] = str(cache)
    print(f'OpenNeuro CLI: {version} ({executable})', flush=True)
    return runtime_cli

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('datasets', nargs='+')
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    output = Path.cwd().resolve()
    cli = prepare_cli(output)

    failed = []
    for spec in dict.fromkeys(args.datasets):
        try:
            download(spec, output, args.jobs, cli=cli)
        except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
            print(f'FAILED {spec}: {error}', flush=True)
            failed.append(spec)
    if failed:
        print('Failed datasets: ' + ' '.join(failed), flush=True)
    return bool(failed)


if __name__ == '__main__':
    raise SystemExit(main())
