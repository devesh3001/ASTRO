"""Bounded untrusted execution and symlink-safe access for the Linux sandbox."""
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

MAX_FILE_BYTES = 250_000
MAX_WORKSPACE_BYTES = 10_000_000
MAX_FILES = 256


def identity(name):
    if os.name != "posix":
        return None, None  # development tests; Compose always runs Linux
    if os.getuid() != 0:
        if os.environ.get("REQUIRE_SANDBOX_USERS") == "1":
            raise RuntimeError("production service must establish separate worker identities")
        return None, None
    import pwd
    account = pwd.getpwnam(name)  # never silently fall back to root
    return account.pw_uid, account.pw_gid


def kill_workers():
    """A detached descendant retains its worker UID after reparenting."""
    if os.name != "posix" or os.getuid() != 0:
        return
    uids = {identity(n)[0] for n in ("sandbox-agent", "sandbox-eval")}
    for _ in range(3):
        found = False
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                if entry.stat().st_uid in uids:
                    os.kill(int(entry.name), signal.SIGKILL)
                    found = True
            except (ProcessLookupError, FileNotFoundError):
                pass
        if not found:
            break
        time.sleep(0.01)


def files(root):
    count = 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        count += len(dirs) + len(names)
        if count > MAX_FILES * 4:
            raise ValueError("workspace has too many filesystem entries")
        if any((Path(directory) / d).is_symlink() for d in dirs):
            raise ValueError("workspace contains a symlink")
        dirs.sort()
        for name in sorted(names):
            path = Path(directory) / name
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("workspace contains a symlink, special file or hard link")
            yield path


def check_storage(root):
    paths = list(files(root))
    sizes = [p.stat().st_size for p in paths]
    if len(paths) > MAX_FILES or any(s > MAX_FILE_BYTES for s in sizes) or sum(sizes) > MAX_WORKSPACE_BYTES:
        raise ValueError("workspace storage quota exceeded")


@contextmanager
def open_file(root, name, write=False, append=False):
    parts = name.replace("\\", "/").split("/")
    if len(name) > 250 or len(parts) > 10 or any(p in ("", ".", "..") or ":" in p for p in parts):
        raise ValueError("path traversal or excessive path length/depth blocked")
    target = root.joinpath(*parts)
    if os.name != "posix":
        if any(p.is_symlink() for p in (target, *target.parents) if p != root):
            raise ValueError("symlink traversal blocked")
        if write:
            target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "ab" if append else "wb" if write else "rb") as stream:
            yield stream
        return
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            if write:
                try:
                    os.mkdir(part, 0o755, dir_fd=directory)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        flags = os.O_NOFOLLOW | os.O_NONBLOCK | ((os.O_WRONLY | os.O_CREAT) if write else os.O_RDONLY)
        if append:
            flags |= os.O_APPEND
        fd = os.open(parts[-1], flags, 0o644, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("only regular files with one link allowed")
            if write and not append:
                os.ftruncate(fd, 0)
            with os.fdopen(fd, "ab" if append else "wb" if write else "rb") as stream:
                fd = None
                yield stream
        finally:
            if fd is not None:
                os.close(fd)
    finally:
        os.close(directory)


def read(root, name):
    with open_file(root, name) as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("file exceeds storage limit")
    return data


def write(root, name, data, append=False):
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("file size exceeds maximum limit")
    with open_file(root, name, write=True, append=append) as stream:
        if append and os.fstat(stream.fileno()).st_size + len(data) > MAX_FILE_BYTES:
            raise ValueError("file exceeds storage limit")
        stream.write(data)
        if os.name == "posix" and os.getuid() == 0:
            os.fchown(stream.fileno(), *identity("sandbox-agent"))
    if os.name == "posix" and os.getuid() == 0:
        for parent in (root / name).parents:
            if parent == root:
                break
            os.chown(parent, *identity("sandbox-agent"), follow_symlinks=False)


WORKER = """
import os,sys,resource
resource.setrlimit(resource.RLIMIT_FSIZE,(250000,250000))
resource.setrlimit(resource.RLIMIT_CPU,(20,20))
resource.setrlimit(resource.RLIMIT_NOFILE,(64,64))
resource.setrlimit(resource.RLIMIT_NPROC,(32,32))
resource.setrlimit(resource.RLIMIT_AS,(268435456,268435456))
sys.path.insert(0,os.getcwd())
exec(compile(sys.argv[1],'<agent>','exec'),{'__name__':'__main__'})
"""


def execute(code, cwd, user="sandbox-agent", seconds=20, workspace=None):
    uid, gid = identity(user)
    kwargs = {"user": uid, "group": gid, "extra_groups": []} if uid is not None else {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    command = [sys.executable, "-I", "-c", WORKER, code] if os.name == "posix" else [sys.executable, "-I", "-c", "import os,sys;sys.path.insert(0,os.getcwd());" + code]
    env = {"PATH": os.defpath, "HOME": str(cwd), "TMPDIR": "/tmp", "PYTHONUNBUFFERED": "1",
           "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
    output, overflow = bytearray(), threading.Event()
    def drain():
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            room = 32768 - len(output)
            output.extend(chunk[:max(0, room)])
            if len(chunk) > room:
                overflow.set()
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    deadline = time.monotonic() + seconds
    error = None
    try:
        while proc.poll() is None:
            if time.monotonic() >= deadline:
                error = f"execution exceeded {seconds} seconds limit"
                break
            if overflow.is_set():
                error = "execution output limit exceeded"
                break
            if workspace is not None:
                check_storage(workspace)
            time.sleep(0.02)
    finally:
        if proc.poll() is None:
            proc.kill()
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        kill_workers()
        proc.wait(timeout=2)
        reader.join(timeout=2)
        proc.stdout.close()
    if error or overflow.is_set():
        raise TimeoutError(error or "execution output limit exceeded")
    if workspace is not None:
        check_storage(workspace)
    return proc.returncode, output.decode("utf-8", errors="replace")
