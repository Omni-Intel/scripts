"""Resumable OpenNeuro downloader; run through download-openneuro.sh."""
import argparse
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
import fcntl
from datetime import datetime
from decimal import Decimal
from functools import wraps
import threading
import time
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import shutil
import subprocess
import tempfile
import urllib.request
import uuid

CLI = 'openneuro'
AWS_ENDPOINT = 'https://s3.dualstack.us-east-1.amazonaws.com'
AWS_CHUNK_SIZE = 32 * 1024**2
AWS_CHUNK_WORKERS = 4
AWS_SLOW_WINDOW = 120
AWS_MIN_RATE = 64 * 1024
RECEIPT = '.openneuro-download.json'
PHASES = ('resolve', 'clone', 'get', 'verify', 'materialize', 'cleanup', 'publish', 'complete')


_LOG_CONTEXT = threading.local()
_LOG_LOCK = threading.Lock()


def log(message, event='INFO', spec=None):
    dataset = spec or getattr(_LOG_CONTEXT, 'spec', None) or '-'
    timestamp = datetime.now().astimezone().isoformat(timespec='seconds')
    clean = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', str(message))
    for line in clean.splitlines() or ['']:
        with _LOG_LOCK:
            print(f'[{timestamp}] [{dataset}] [{event}] {line}', flush=True)


def dataset_logging(function):
    @wraps(function)
    def wrapped(spec, *args, **kwargs):
        previous = getattr(_LOG_CONTEXT, 'spec', None)
        _LOG_CONTEXT.spec = spec
        try:
            return function(spec, *args, **kwargs)
        finally:
            _LOG_CONTEXT.spec = previous
    return wrapped


def run(args, cwd):
    log(shlex.join(map(str, args)), 'COMMAND')
    env = dict(os.environ, PYTHONUNBUFFERED='1', NO_COLOR='1')
    with subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors='replace', bufsize=1, env=env) as process:
        try:
            for line in process.stdout:
                log(line.rstrip('\r\n'), 'OUTPUT')
            returncode = process.wait()
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
    if returncode:
        raise subprocess.CalledProcessError(returncode, args)

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


# AWS is an optional prefetch step within the existing resumable get phase.
# Only public OpenNeuro S3 objects are eligible; DataLad handles all other remotes.
def annex_hash(key):
    match = re.fullmatch(r'(MD5|SHA1|SHA256|SHA512)E?(?:-s([0-9]+))?--([a-f0-9]+)(?:\.[A-Za-z0-9._-]+)?', key)
    if not match:
        return None
    algorithm, size, digest = match.groups()
    if len(digest) != hashlib.new(algorithm.lower()).digest_size * 2:
        return None
    return algorithm.lower(), int(size) if size else None, digest


def annex_matches(path, key):
    info = annex_hash(key)
    if info is None or path.is_symlink() or not path.is_file():
        return False
    algorithm, size, digest = info
    if size is not None and path.stat().st_size != size:
        return False
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest() == digest


def s3_remotes(text):
    remotes = set()
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        fields = dict(p.split('=', 1) for p in parts[1:] if '=' in p)
        if (fields.get('type') == 'S3' and fields.get('bucket') == 'openneuro.org'
                and fields.get('host', 's3.amazonaws.com') in
                ('s3.amazonaws.com', 's3.us-east-1.amazonaws.com')):
            remotes.add(parts[0])
    return remotes


def s3_versions(text, remotes, accession):
    values = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[1].endswith(':V') or parts[1][:-2] not in remotes:
            continue
        stamp = Decimal(parts[0].removesuffix('s'))
        for value in parts[2:]:
            if value[:1] not in ('+', '-'):
                continue
            identity = value[1:]
            previous = values.get(identity)
            # A removal wins a tie; a stale URL can always be handled by DataLad.
            present = value[0] == '+'
            if previous is None or stamp > previous[0] or (stamp == previous[0] and not present):
                values[identity] = stamp, present
    result = []
    for identity, (_, present) in sorted(values.items(), key=lambda item: item[1][0], reverse=True):
        version, separator, key = identity.partition('#')
        if present and separator and version and key.startswith(accession + '/'):
            result.append({'version_id': version, 's3_key': key})
    return result


def aws_manifest(dataset, accession):
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=dataset)
    objects = {}
    annex_root = (dataset / '.git' / 'annex' / 'objects').resolve()
    for raw in tracked.split(b'\0'):
        if not raw:
            continue
        path = dataset / os.fsdecode(raw)
        if not path.is_symlink() or path.exists():
            continue
        if not path.resolve().is_relative_to(annex_root):
            raise RuntimeError(f'Unsafe annex link: {path}')
        key = Path(os.readlink(path)).name
        if annex_hash(key):
            objects.setdefault(key, {'key': key, 'file': os.fsdecode(raw), 'candidates': []})
    if not objects:
        return []
    remote_text = subprocess.check_output(['git', 'show', 'git-annex:remote.log'], cwd=dataset, text=True)
    remotes = s3_remotes(remote_text)
    if not remotes:
        return list(objects.values())
    paths = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', 'git-annex'],
                                    cwd=dataset, text=True).splitlines()
    paths = [p for p in paths if p.endswith('.log.rmet') and Path(p).name[:-9] in objects]
    # One local Git batch avoids one subprocess or network query per object.
    completed = subprocess.run(['git', 'cat-file', '--batch'], cwd=dataset,
                               input=('\n'.join('git-annex:' + p for p in paths) + '\n').encode(),
                               stdout=subprocess.PIPE, check=True)
    offset = 0
    for path in paths:
        end = completed.stdout.index(b'\n', offset)
        header = completed.stdout[offset:end].split()
        if len(header) != 3 or header[1] != b'blob':
            raise ValueError('Unexpected annex metadata object')
        size = int(header[2])
        body = completed.stdout[end + 1:end + 1 + size].decode()
        offset = end + size + 2
        key = Path(path).name[:-9]
        objects[key]['candidates'] = s3_versions(body, remotes, accession)
    return list(objects.values())


class DownloadStop:
    """Cancel one AWS pool, or inherit cancellation of the enclosing batch."""
    def __init__(self, parent=None):
        self.event = threading.Event()
        self.parent = parent

    def is_set(self):
        return self.event.is_set() or bool(self.parent and self.parent.is_set())

    def set(self):
        self.event.set()

    def wait(self, timeout):
        self.event.wait(timeout)
        return self.is_set()


def aws_command(args, cwd, stop):
    env = dict(os.environ, AWS_MAX_ATTEMPTS='3', AWS_RETRY_MODE='standard', AWS_EC2_METADATA_DISABLED='true', AWS_PAGER='')
    # Check sustained throughput as well as socket timeouts. Range retries preserve other chunks.
    destination = Path(args[-1]) if '--bucket' in args else None
    sampled_at, sampled_bytes = time.monotonic(), 0
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while process.poll() is None:
                if stop.wait(0.25):
                    raise InterruptedError('AWS download cancelled')
                now = time.monotonic()
                if destination is not None and now - sampled_at >= AWS_SLOW_WINDOW:
                    size = destination.stat().st_size if destination.exists() else 0
                    if (size - sampled_bytes) / (now - sampled_at) < AWS_MIN_RATE:
                        raise RuntimeError('AWS sustained throughput below 64 KiB/s')
                    sampled_at, sampled_bytes = now, size
            if process.returncode:
                output.seek(max(0, output.tell() - 2000))
                message = output.read().decode(errors='replace')
                raise RuntimeError(f'AWS exited {process.returncode}: {message.strip()}')
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def aws_args(candidate):
    return ['aws', 's3api', 'get-object', '--bucket', 'openneuro.org',
            '--key', candidate['s3_key'], '--version-id', candidate['version_id'],
            '--no-sign-request', '--region', 'us-east-1',
            '--endpoint-url', AWS_ENDPOINT,
            '--cli-connect-timeout', '15', '--cli-read-timeout', '60']


def aws_limited(args, cache, stop, slots):
    while not slots.acquire(timeout=0.25):
        if stop.is_set():
            raise InterruptedError('AWS download cancelled')
    try:
        if stop.is_set():
            raise InterruptedError('AWS download cancelled')
        aws_command(args, cache, stop)
    finally:
        slots.release()


def aws_parts_dir(cache, key, candidate):
    identity = json.dumps(candidate, sort_keys=True).encode()
    return cache / 'chunks' / key / hashlib.sha256(identity).hexdigest()


def aws_ranges(candidate, key, size, cache, partial, stop, slots):
    """Persist successful ranges with local SHA-256 receipts, bound to key/version."""
    parts = aws_parts_dir(cache, key, candidate)
    for directory in (cache / 'chunks', parts.parent, parts):
        real_directory(directory)
    local_stop = DownloadStop(stop)
    count = (size + AWS_CHUNK_SIZE - 1) // AWS_CHUNK_SIZE

    def fetch(index):
        start = index * AWS_CHUNK_SIZE
        end = min(size, start + AWS_CHUNK_SIZE) - 1
        # Include offsets: a future chunk-size change cannot reuse different ranges.
        part = parts / f'{start}-{end}.part'
        receipt = parts / f'{start}-{end}.json'
        temporary = parts / f'{start}-{end}.tmp'
        if any(p.is_symlink() for p in (part, receipt, temporary)):
            raise RuntimeError('Refusing symlink in range cache')
        if part.is_file() and receipt.is_file() and part.stat().st_size == end - start + 1:
            try:
                expected = json.loads(receipt.read_text())['sha256']
                with part.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() == expected:
                        return part
            except (ValueError, KeyError, TypeError):
                pass
        for attempt in range(3):
            if local_stop.is_set():
                raise InterruptedError('AWS download cancelled')
            try:
                temporary.unlink(missing_ok=True)
                aws_limited(aws_args(candidate) + ['--range', f'bytes={start}-{end}', str(temporary)],
                            cache, local_stop, slots)
                if temporary.stat().st_size != end - start + 1:
                    raise RuntimeError('S3 range length mismatch')
                with temporary.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                    os.fsync(stream.fileno())
                os.replace(temporary, part)
                write_json(receipt, {'sha256': digest})
                return part
            except (OSError, RuntimeError):
                if local_stop.is_set() or attempt == 2:
                    raise
                local_stop.wait(attempt + 1)
        raise RuntimeError('Range retries exhausted')

    pool = ThreadPoolExecutor(max_workers=AWS_CHUNK_WORKERS, thread_name_prefix='s3-range')
    futures = {pool.submit(fetch, index): index for index in range(count)}
    ordered = {}
    try:
        for future in as_completed(futures):
            ordered[futures[future]] = future.result()
        with partial.open('wb') as target:
            for index in range(count):
                if local_stop.is_set():
                    raise InterruptedError('AWS download cancelled')
                with ordered[index].open('rb') as source:
                    shutil.copyfileobj(source, target, 1024**2)
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        local_stop.set()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def aws_fetch(item, cache, stop, slots=None):
    slots = slots if slots is not None else threading.BoundedSemaphore(AWS_CHUNK_WORKERS)
    key = item['key']
    ready, partial = cache / key, cache / 'partial' / key
    if ready.is_symlink() or partial.is_symlink():
        raise RuntimeError(f'Refusing symlink in AWS cache: {key}')
    if stop.is_set():
        raise InterruptedError('AWS download cancelled')
    for path in (ready, partial):
        if annex_matches(path, key):
            if path == partial:
                os.replace(partial, ready)
            return {'key': key, 'ok': True, 'reused': True, 'bytes': ready.stat().st_size}
    ready.unlink(missing_ok=True)
    errors = []
    for candidate in item['candidates']:
        if stop.is_set():
            raise InterruptedError('AWS download cancelled')
        partial.unlink(missing_ok=True)
        try:
            size = annex_hash(key)[1]
            if size is not None and size > AWS_CHUNK_SIZE:
                aws_ranges(candidate, key, size, cache, partial, stop, slots)
            else:
                aws_limited(aws_args(candidate) + [str(partial)], cache, stop, slots)
        except InterruptedError:
            raise
        except (OSError, RuntimeError) as error:
            # Transport failures are handed off, not retried against every version
            # of the same large object. AWS itself has bounded request retries.
            errors.append(str(error))
            break
        if not annex_matches(partial, key):
            errors.append('Downloaded object does not match annex hash/size')
            partial.unlink(missing_ok=True)
            parts = aws_parts_dir(cache, key, candidate)
            if parts.exists():
                shutil.rmtree(parts)
            continue
        size = partial.stat().st_size
        with partial.open('rb') as stream:
            os.fsync(stream.fileno())
        os.replace(partial, ready)
        sync_dir(cache)
        parts_root = cache / 'chunks' / key
        if parts_root.exists():
            shutil.rmtree(parts_root)
        return {'key': key, 'ok': True, 'reused': False, 'bytes': size}
    partial.unlink(missing_ok=True)
    return {'key': key, 'ok': False, 'errors': errors}


def aws_prefetch(dataset, stage, accession, jobs, stop=None):
    stop = DownloadStop(stop)
    if not shutil.which('aws'):
        log('AWS CLI unavailable; using DataLad', 'AWS_SKIP')
        return
    started = time.monotonic()
    try:
        # Deno clones Git metadata; initialize annex before inspecting its local branch.
        initialized = subprocess.run(['git', 'config', '--get', 'annex.uuid'], cwd=dataset,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if initialized.returncode:
            run(['git', 'annex', 'init'], dataset)
        items = aws_manifest(dataset, accession)
    except (subprocess.CalledProcessError, ValueError, ArithmeticError) as error:
        log(f'S3 metadata unavailable; using DataLad: {error}', 'AWS_SKIP')
        return
    cache = stage / 'aws-cache'
    real_directory(cache)
    real_directory(cache / 'partial')
    write_json(stage / 'aws-manifest.json', {'commit': git_text(dataset, 'rev-parse', 'HEAD'), 'objects': items})
    eligible = [item for item in items if item['candidates'] or annex_matches(cache / item['key'], item['key'])
                or annex_matches(cache / 'partial' / item['key'], item['key'])]
    stats = {'objects': len(items), 'eligible': len(eligible), 'downloaded': 0, 'downloaded_bytes': 0,
             'reused': 0, 'fallback': len(items) - len(eligible), 'imported': 0}
    log(f'eligible={len(eligible)} missing_objects={len(items)} jobs={jobs}', 'AWS_START')
    ready = []; ready_bytes = 0
    pool = ThreadPoolExecutor(max_workers=jobs, thread_name_prefix='aws')
    slots = threading.BoundedSemaphore(jobs)
    pending = {}; iterator = iter(eligible)
    last_report = time.monotonic(); last_bytes = 0

    def refill():
        while len(pending) < jobs and not stop.is_set():
            item = next(iterator, None)
            if item is None:
                break
            pending[pool.submit(aws_fetch, item, cache, stop, slots)] = item

    def inject():
        nonlocal ready_bytes
        if ready:
            run(['git', 'annex', 'reinject', '--guesskeys', *[str(cache / key) for key in ready]], dataset)
            stats['imported'] += len(ready)
            ready.clear(); ready_bytes = 0

    try:
        refill()
        while pending:
            if stop.is_set():
                raise InterruptedError('AWS download cancelled')
            completed, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
            for future in completed:
                pending.pop(future)
                result = future.result()
                if result['ok']:
                    stats['reused' if result['reused'] else 'downloaded'] += 1
                    if not result['reused']:
                        stats['downloaded_bytes'] += result['bytes']
                    ready.append(result['key']); ready_bytes += result['bytes']
                else:
                    stats['fallback'] += 1
                    log(f"key={result['key']} errors={result['errors']}; deferred to DataLad", 'AWS_FALLBACK')
            if len(ready) >= 32 or ready_bytes >= 1024**3:
                inject()
            refill()
            now = time.monotonic()
            if now - last_report >= 60:
                partial_bytes = 0
                for item in pending.values():
                    sizes = []
                    paths = [cache / 'partial' / item['key']]
                    chunk_root = cache / 'chunks' / item['key']
                    chunks = 0
                    for path in chunk_root.rglob('*'):
                        if path.suffix not in ('.part', '.tmp') or path.is_symlink():
                            continue
                        try:
                            chunks += path.stat().st_size
                        except FileNotFoundError:
                            pass
                    for path in paths:
                        try:
                            sizes.append(path.stat().st_size)
                        except FileNotFoundError:
                            pass
                    partial_bytes += max([chunks, *sizes])
                total = stats['downloaded_bytes'] + partial_bytes
                log(f'downloaded={stats["downloaded"]} reused={stats["reused"]} '
                    f'fallback={stats["fallback"]} net_bytes={total} '
                    f'net_MiB_s={(total - last_bytes) / (now - last_report) / 1024**2:.3f}', 'AWS_PROGRESS')
                write_json(stage / 'aws-summary.json', dict(stats, elapsed=now - started, complete=False))
                last_report, last_bytes = now, total
        inject()
    except BaseException:
        stop.set()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        write_json(stage / 'aws-summary.json', dict(stats, elapsed=time.monotonic() - started, complete=not stop.is_set()))
    log(f'downloaded={stats["downloaded"]} reused={stats["reused"]} '
        f'imported={stats["imported"]} fallback={stats["fallback"]} '
        f'elapsed={time.monotonic() - started:.1f}s; starting DataLad completion', 'AWS_DONE')

@dataset_logging
def download(spec, output, jobs, cli=CLI, backend="aws", stop=None):
    if stop is not None and stop.is_set():
        raise InterruptedError("Download cancelled")
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

        active_phase = state['phase']
        phase_started = time.monotonic()
        dataset_started = phase_started

        def phase(value, completed=True):
            nonlocal active_phase, phase_started
            next_state = dict(state, phase=value)
            write_json(state_path, next_state)
            state.update(next_state)
            if completed:
                log(f'phase={active_phase} elapsed={time.monotonic() - phase_started:.1f}s '
                    f'version={state["version"]}', 'DONE')
                active_phase = value
                phase_started = time.monotonic()
                if value != 'complete':
                    log(f'phase={value} version={state["version"]}', 'START')

        dataset = stage / accession
        log(f'phase={active_phase} version={state["version"]} state={state_path}', 'RESUME')
        if active_phase != 'complete':
            log(f'phase={active_phase} version={state["version"]}', 'START')
        else:
            log('Already completed; checking receipt only', 'SKIP')
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
                if backend == 'aws':
                    aws_prefetch(dataset, stage, accession, jobs, stop)
                run(['datalad', 'get', '-J', str(jobs), '.'], dataset)
                phase('verify')
            if state['phase'] == 'verify':
                check_repo(dataset, state)
                try:
                    run(['git', 'annex', 'fsck', '--numcopies=1'], dataset)
                except subprocess.CalledProcessError:
                    # fsck may quarantine a corrupt object. Retry get next time.
                    phase('get', completed=False)
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
            log(f"version={state['version']} output={destination} elapsed={time.monotonic() - dataset_started:.1f}s", 'COMPLETE')
        except BaseException as error:
            log(f'phase={active_phase} elapsed={time.monotonic() - phase_started:.1f}s '
                f'error={type(error).__name__}: {error}; resume_phase={state["phase"]}; state={stage}', 'FAILED')
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
    log(f'OpenNeuro CLI: {version} ({executable})')
    return runtime_cli

def download_batch(specs, output, jobs, dataset_jobs, cli, backend="aws"):
    """Bound the number of active dataset pipelines; each keeps its own state/lock."""
    if dataset_jobs < 1 or jobs < 1:
        raise ValueError('Concurrency values must be positive')
    specs = list(dict.fromkeys(specs))
    failed = set()
    stop = threading.Event()
    pool = ThreadPoolExecutor(max_workers=dataset_jobs, thread_name_prefix='dataset')
    futures = {}
    try:
        futures = {pool.submit(download, spec, output, jobs, cli=cli, backend=backend, stop=stop): spec for spec in specs}
        for future in as_completed(futures):
            spec = futures[future]
            try:
                future.result()
            except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
                log(str(error), 'DATASET_FAILED', spec=spec)
                failed.add(spec)
    except BaseException:
        stop.set()
        raise
    finally:
        # Do not start queued datasets after an interrupt/unexpected exception.
        pool.shutdown(wait=True, cancel_futures=True)
    return [spec for spec in specs if spec in failed]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--dataset-jobs', type=int, default=1)
    parser.add_argument('--backend', choices=('aws', 'datalad'), default='aws',
                        help='aws: prefetch versioned S3 objects then complete with DataLad (default)')
    parser.add_argument('datasets', nargs='+')
    args = parser.parse_args()
    if args.jobs < 1 or args.dataset_jobs < 1:
        parser.error('--jobs and --dataset-jobs must be positive')
    output = Path.cwd().resolve()
    cli = prepare_cli(output)

    log(f'Backend: {args.backend}; concurrency: {args.dataset_jobs} datasets, {args.jobs} file transfers per dataset', 'BATCH_START')
    failed = download_batch(args.datasets, output, args.jobs, args.dataset_jobs, cli, backend=args.backend)

    log(f'total={len(set(args.datasets))} succeeded={len(set(args.datasets)) - len(failed)} '
        f"failed={len(failed)} failed_ids={','.join(failed) or 'none'}", 'BATCH_DONE')
    return bool(failed)


if __name__ == '__main__':
    raise SystemExit(main())
