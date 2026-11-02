"""Quitting never waits on a background job (2026-11-02): a DNS lookup that hangs or a long
/local grep sat in asyncio's default executor, so after the screen was gone the client
waited (asyncio's 300 s, then Python's join with no timeout) until ctrl+c."""
import subprocess
import sys
import textwrap
import time

from cli_fake import CLI, load_client

jav3 = load_client("jav3cli_exit")

# the app's loop: the executor on_mount installs, a job still running, then the loop closes
# the way App.run closes it (asyncio.Runner), then the interpreter exits
SCRIPT = textwrap.dedent("""
    import asyncio, importlib.machinery, importlib.util, sys, time
    loader = importlib.machinery.SourceFileLoader("jav3x", sys.argv[1])
    spec = importlib.util.spec_from_loader("jav3x", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)

    async def main():
        loop = asyncio.get_running_loop()
        if sys.argv[2] == "fixed":
            loop.set_default_executor(mod._daemon_executor())
        loop.run_in_executor(None, time.sleep, 30)
        await asyncio.sleep(0.2)

    with asyncio.Runner() as r:
        r.run(main())
    print("out")
""")


def _run(mode: str, limit: float) -> tuple[float, str]:
    t = time.monotonic()
    try:
        p = subprocess.run([sys.executable, "-c", SCRIPT, str(CLI), mode],
                           capture_output=True, text=True, timeout=limit)
        return time.monotonic() - t, p.stdout
    except subprocess.TimeoutExpired:
        return time.monotonic() - t, "timeout"


def test_a_running_job_does_not_hold_the_exit():
    took, out = _run("fixed", 15)
    assert out.strip() == "out" and took < 8, (took, out)


def test_without_it_the_exit_waits():
    took, out = _run("stock", 3)          # the bug, so the test above means something
    assert out == "timeout"


async def test_the_executor_runs_jobs_and_hands_back_errors():
    import asyncio
    loop = asyncio.get_running_loop()
    ex = jav3._daemon_executor()
    assert await loop.run_in_executor(ex, lambda: 6 * 7) == 42
    try:
        await loop.run_in_executor(ex, lambda: 1 / 0)
        raise AssertionError("no error")
    except ZeroDivisionError:
        pass
    ex.shutdown(wait=True)                # returns at once
