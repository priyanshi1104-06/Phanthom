"""Runs one job in a separate worker process and returns the worker's JSON result.

Two sandboxes:
  docker - a throwaway container: no network, memory cap, read-only filesystem, no privileges.
  local  - a plain child process (the worker still caps its own memory and CPU time).
"auto" picks docker when the sandbox image is built, else local.

The analysed code is untrusted: it never runs in this process.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "worker.py"
IMAGE = "phantom-sandbox"
DOCKER_FLAGS = [
    "--rm", "--interactive",
    "--network=none",
    "--memory=256m", "--memory-swap=256m",  # memory cap, no swap
    "--cpus=1", "--pids-limit=64",
    "--read-only", "--tmpfs=/tmp:rw,size=16m",  # read-only filesystem plus a small scratch dir
    "--cap-drop=ALL", "--security-opt=no-new-privileges",
    "--user=65534:65534",  # nobody
]


def docker_ready():
    """True when the docker command works and the sandbox image has been built."""
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True)
    return probe.returncode == 0


def choose_sandbox(requested="auto"):
    if requested == "auto":
        return "docker" if docker_ready() else "local"
    if requested == "docker" and not docker_ready():
        raise RuntimeError(f"Docker sandbox not available: start Docker and build the image "
                           f"(docker build -f sandbox/Dockerfile -t {IMAGE} .)")
    return requested


def worker_command(sandbox, container_name=None):
    if sandbox == "docker":
        # The current worker.py is mounted read-only, so the image never runs a stale copy.
        mount = f"--mount=type=bind,src={WORKER},dst=/app/worker.py,readonly"
        return ["docker", "run", f"--name={container_name}", mount, *DOCKER_FLAGS, IMAGE]
    # -I: ignore PYTHON* env vars and the user's site-packages; -B: write no .pyc files.
    return [sys.executable, "-I", "-B", str(WORKER)]


def worker_env(sandbox, workdir):
    """A local worker gets a bare environment: never the backend's, which holds secrets such as
    ANTHROPIC_API_KEY that the analysed code could read. (A Docker container never sees the host's.)"""
    if sandbox == "docker":
        return None  # the docker client itself needs the normal environment
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(workdir), "LANG": "C.UTF-8"}


def run_job(job, timeout=5.0, sandbox="local"):
    """Send `job` to a fresh worker; kill it after `timeout` seconds (reported as kind "timeout")."""
    name = f"phantom-{uuid.uuid4().hex[:12]}" if sandbox == "docker" else None
    # A local worker runs in a scratch dir, so nothing it writes (e.g. Hypothesis' cache) lands in the project.
    workdir = Path(tempfile.gettempdir()) / "phantom-work"
    workdir.mkdir(exist_ok=True)
    proc = subprocess.Popen(
        worker_command(sandbox, name), cwd=workdir, env=worker_env(sandbox, workdir),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,  # own process group, so a kill takes its children too
    )
    try:
        stdout, stderr = proc.communicate(json.dumps(job), timeout=timeout)
    except subprocess.TimeoutExpired:
        kill(proc, name)
        proc.communicate()
        return {"ok": False, "kind": "timeout", "error": "Timeout",
                "message": f"worker killed after {timeout:g} s (possible infinite loop)"}

    result = last_json_line(stdout)
    if result is not None:
        return result
    if proc.returncode in (137, -signal.SIGKILL):
        reason = "was killed (memory limit?)"
    else:
        reason = f"exited with code {proc.returncode} without a result"
    return {"ok": False, "kind": "crash", "error": "WorkerCrashed",
            "message": f"worker {reason}", "stderr": stderr[-2000:]}


def kill(proc, container_name):
    if container_name:  # killing the docker client alone would leave the container running
        subprocess.run(["docker", "kill", container_name], capture_output=True)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def last_json_line(stdout):
    """The worker's result is the last line of stdout that parses as a JSON object."""
    for line in reversed(stdout.splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None
