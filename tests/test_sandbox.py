"""The Docker sandbox's lockdown, tested with hostile code. Skipped when the image isn't built."""
import unittest

from runner.executor import docker_ready, run_job


def call_in_docker(source, args=()):
    job = {"mode": "call", "source": source, "func_name": "attack", "args": list(args)}
    return run_job(job, timeout=20, sandbox="docker")


@unittest.skipUnless(docker_ready(), "Docker sandbox image not built")
class SandboxTest(unittest.TestCase):
    def test_runs_correct_code(self):
        self.assertEqual(call_in_docker("def attack(x): return x + 1", [1]), {"ok": True, "result": 2})

    def test_no_network(self):
        source = ("import socket\ndef attack():\n"
                  "    socket.create_connection(('1.1.1.1', 53), timeout=2)\n    return 'connected'\n")
        result = call_in_docker(source)
        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "exception")

    def test_read_only_filesystem(self):
        source = "def attack():\n    open('/app/worker.py', 'w').write('pwned')\n"
        result = call_in_docker(source)
        self.assertEqual(result["error"], "OSError")
        self.assertIn("Read-only file system", result["message"])

    def test_not_root(self):
        self.assertEqual(call_in_docker("import os\ndef attack(): return os.getuid()")["result"], 65534)

    def test_memory_cap(self):
        source = "def attack():\n    blocks = [bytearray(64 * 1024 ** 2) for _ in range(12)]\n    return len(blocks)\n"
        result = call_in_docker(source)  # 768 MB: over the container's 256 MB
        self.assertFalse(result["ok"])
        self.assertIn(result["kind"], ("crash", "exception"))


if __name__ == "__main__":
    unittest.main()
