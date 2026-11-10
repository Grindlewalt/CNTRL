"""What this machine can do, for tests that need Linux.

The server runs on Linux (KVM guests over AF_VSOCK, rlimits the kernel
enforces). A test that needs one of these skips where it is missing, with the
missing capability as its reason: the Mac shows a skip, not a failure, and
Linux still runs it. Each check probes the capability itself, never the OS
name or the CI.
"""
import functools
import socket
import subprocess
import sys

import pytest

# The guest package (guest/) is Linux code: it reads socket.VMADDR_CID_HOST at
# import. Python's socket module only has the AF_VSOCK names on Linux.
HAS_VSOCK_NAMES = hasattr(socket, "AF_VSOCK") and hasattr(socket, "VMADDR_CID_HOST")

needs_vsock_names = pytest.mark.skipif(
    not HAS_VSOCK_NAMES,
    reason="needs socket.AF_VSOCK / VMADDR_CID_HOST (Linux only): the guest "
           "package reads them at import")


@functools.lru_cache(maxsize=None)
def can_cap_address_space() -> bool:
    """backend/runner.py caps a run's memory with setrlimit(RLIMIT_AS). macOS
    refuses to lower it (ValueError), so every run fails there. Probed in a
    child: a lowered hard limit can't be raised again in this process."""
    probe = ("import resource; "
             "resource.setrlimit(resource.RLIMIT_AS, (1 << 32, 1 << 32))")
    try:
        return subprocess.run([sys.executable, "-c", probe], capture_output=True,
                              timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


needs_rlimit_as = pytest.mark.skipif(
    not can_cap_address_space(),
    reason="needs setrlimit(RLIMIT_AS) (this kernel refuses it): the host "
           "runner caps every run's memory with it")
