from __future__ import annotations

import asyncio
import statistics
import time
import uuid

from agentbox_client import AgentBoxClient

BASE_URL = "https://api-dev.agentbox.work"
API_KEY = "replace-with-a-long-random-token"
N = 20


def stats(label: str, samples: list[float]) -> None:
    s = sorted(samples)
    p50 = statistics.median(s)
    p95 = s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]
    print(
        f"{label:18} n={len(s):2d}  "
        f"min={min(s):6.0f}ms  p50={p50:6.0f}ms  "
        f"p95={p95:6.0f}ms  max={max(s):6.0f}ms  mean={statistics.mean(s):6.0f}ms"
    )


async def main() -> None:
    sandbox_id = f"bench-{uuid.uuid4().hex[:8]}"
    session_id = f"sess-{uuid.uuid4().hex[:8]}"

    async with AgentBoxClient(base_url=BASE_URL, api_key=API_KEY) as client:
        # --- sandbox creation ---
        t0 = time.perf_counter()
        await client.ensure_sandbox(sandbox_id)
        ensure_ms = (time.perf_counter() - t0) * 1000
        print(f"ensure_sandbox     {ensure_ms:.0f}ms  (sandbox={sandbox_id})")

        t0 = time.perf_counter()
        await client.create_session(sandbox_id, session_id)
        sess_ms = (time.perf_counter() - t0) * 1000
        print(f"create_session     {sess_ms:.0f}ms")
        print(f"{'TOTAL cold start':18} {ensure_ms + sess_ms:.0f}ms\n")

        session = client.session(sandbox_id, session_id, delete_on_exit=True)

        try:
            # --- 20 shell commands ---
            cmd_times: list[float] = []
            for i in range(N):
                t0 = time.perf_counter()
                r = await client.exec_command(
                    sandbox_id, session_id, cmd=f"echo hello-{i}"
                )
                dt = (time.perf_counter() - t0) * 1000
                cmd_times.append(dt)
                print(f"  cmd  {i + 1:2d}/{N}  {dt:6.0f}ms")

            print()
            # --- 20 python executions ---
            py_times: list[float] = []
            for i in range(N):
                t0 = time.perf_counter()
                r = await client.execute_python(
                    sandbox_id, session_id, code=f"print(2 ** {i})"
                )
                dt = (time.perf_counter() - t0) * 1000
                py_times.append(dt)
                print(f"  py   {i + 1:2d}/{N}  {dt:6.0f}ms")

            print("\n===== SUMMARY =====")
            print(f"sandbox cold start (ensure+session): {ensure_ms + sess_ms:.0f}ms")
            stats("exec_command", cmd_times)
            stats("execute_python", py_times)
        finally:
            t0 = time.perf_counter()
            await client.delete_session(sandbox_id, session_id)
            await client.delete_sandbox(sandbox_id)
            print(f"\ncleanup            {(time.perf_counter() - t0) * 1000:.0f}ms")


if __name__ == "__main__":
    asyncio.run(main())
