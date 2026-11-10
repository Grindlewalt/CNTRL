"""Tests on a machine that is not the server's Linux.

The server runs on Linux (KVM guests over AF_VSOCK, rlimits the kernel
enforces). A test that only needs a Linux NAME gets it filled in (the guest
shim below); a test that needs a Linux CAPABILITY skips where it is missing,
with that capability as its reason: the Mac shows a skip, not a failure, and
Linux still runs it. Each check probes the capability itself, never the OS
name or the CI.
"""
import functools
import subprocess
import sys

import pytest

# The guest package (guest/) reads socket.VMADDR_CID_HOST at import, and only
# Linux's socket module has the AF_VSOCK names. A test that runs guest code on
# the host (python -S, the package on PYTHONPATH) puts this first: a no-op on
# Linux, the same numbers elsewhere. Nothing it runs opens a vsock socket.
GUEST_SOCKET_SHIM = (
    "import socket\n"
    "socket.AF_VSOCK = getattr(socket, 'AF_VSOCK', 40)\n"
    "socket.VMADDR_CID_HOST = getattr(socket, 'VMADDR_CID_HOST', 2)\n"
    "socket.VMADDR_CID_ANY = getattr(socket, 'VMADDR_CID_ANY', 0xFFFFFFFF)\n")


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
