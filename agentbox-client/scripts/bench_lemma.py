"""Benchmark lemma-cli execution inside agentbox sandboxes.

Measures:
  1. Raw shell baseline (echo) — pure agentbox exec overhead.
  2. Sequential lemma-cli commands — realistic single-agent latency.
  3. Concurrent same-session — one agent firing parallel tool calls.
  4. Concurrent multi-session — multiple agents in parallel.

Token is fetched fresh from the auth signin endpoint at startup.
"""
from __future__ import annotations

import asyncio
import statistics
import time
import uuid

import httpx

from agentbox_client import AgentBoxClient

AGENTBOX_URL = "https://api-dev.agentbox.work"
AGENTBOX_KEY = "replace-with-a-long-random-token"

LEMMA_BASE_URL = "https://dc9c-2406-7400-54-39e6-00-100c.ngrok-free.app"
SIGNIN_URL = f"{LEMMA_BASE_URL}/st/auth/signin"
EMAIL = "anukul@gappy.ai"
PASSWORD = "@nukulG@ppy786"
ORG_ID = "019e90f2-efb4-72c8-85a4-e9978537251c"
POD_ID = "019ea060-3203-722e-853e-d933051925b0"  # Sales CRM

# Read-only lemma commands an agent would realistically run.
LEMMA_CMDS = [
    "lemma --json orgs list",
    "lemma --json pods list",
    "lemma --json agents list",
    "lemma --json functions list",
    "lemma --json conversations list",
    "lemma --json files list",
    "lemma --json tables list",
    "lemma --json me get",
]
N = 20  # commands per test


def get_token() -> str:
    r = httpx.post(
        SIGNIN_URL,
        headers={"Content-Type": "application/json", "rid": "emailpassword"},
        json={"formFields": [{"id": "email", "value": EMAIL}, {"id": "password", "value": PASSWORD}]},
        timeout=30,
    )
    r.raise_for_status()
    tok = r.headers.get("st-access-token")
    if not tok:
        raise RuntimeError("no st-access-token in signin response")
    return tok


def env_for(token: str) -> dict[str, str]:
    return {
        "LEMMA_TOKEN": token,
        "LEMMA_BASE_URL": LEMMA_BASE_URL,
        "LEMMA_AUTH_URL": LEMMA_BASE_URL + "/auth",
        "LEMMA_ORG_ID": ORG_ID,
        "LEMMA_POD_ID": POD_ID,
    }


def summarize(label: str, samples: list[float], fails: int = 0) -> None:
    if not samples:
        print(f"{label:28} NO SUCCESSFUL SAMPLES (fails={fails})")
        return
    s = sorted(samples)
    p95 = s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]
    print(
        f"{label:28} n={len(s):2d} fail={fails}  "
        f"min={min(s):6.0f}  p50={statistics.median(s):6.0f}  "
        f"p95={p95:6.0f}  max={max(s):6.0f}  mean={statistics.mean(s):6.0f}ms"
    )


async def timed_exec(client, sid, sess, cmd, timeout=120):
    t0 = time.perf_counter()
    ok = False
    try:
        r = await client.exec_command(sid, sess, cmd=cmd, timeout=timeout)
        ok = r.exit_code == 0
    except Exception as e:  # noqa: BLE001
        return (time.perf_counter() - t0) * 1000, False, repr(e)[:120]
    return (time.perf_counter() - t0) * 1000, ok, None


async def main() -> None:
    token = get_token()
    print(f"token acquired (len={len(token)})\n")
    env = env_for(token)

    sid = f"lbench-{uuid.uuid4().hex[:8]}"
    async with AgentBoxClient(base_url=AGENTBOX_URL, api_key=AGENTBOX_KEY) as client:
        t0 = time.perf_counter()
        await client.ensure_sandbox(sid, env=env)
        cold = (time.perf_counter() - t0) * 1000
        print(f"sandbox cold start (ensure): {cold:.0f}ms  ({sid})\n")

        main_sess = "s-main"
        await client.create_session(sid, main_sess, env=env)

        try:
            # ---- 1. raw shell baseline ----
            base = []
            for i in range(N):
                dt, ok, err = await timed_exec(client, sid, main_sess, f"echo hi-{i}")
                base.append(dt)
            summarize("1. raw echo (baseline)", base)

            # ---- 2. sequential lemma cli ----
            print("\n-- 2. sequential lemma-cli --")
            seq, seq_fail = [], 0
            seq_t0 = time.perf_counter()
            for i in range(N):
                cmd = LEMMA_CMDS[i % len(LEMMA_CMDS)]
                dt, ok, err = await timed_exec(client, sid, main_sess, cmd)
                if ok:
                    seq.append(dt)
                else:
                    seq_fail += 1
                print(f"  {i+1:2d}/{N} {dt:7.0f}ms {'OK ' if ok else 'FAIL'} {cmd}{'  '+err if err else ''}")
            seq_wall = (time.perf_counter() - seq_t0) * 1000

            # ---- 3. concurrent same-session (parallel tool calls, one agent) ----
            print("\n-- 3. concurrent same-session (1 agent, parallel tool calls) --")
            con_t0 = time.perf_counter()
            tasks = [
                timed_exec(client, sid, main_sess, LEMMA_CMDS[i % len(LEMMA_CMDS)])
                for i in range(N)
            ]
            res = await asyncio.gather(*tasks)
            con_wall = (time.perf_counter() - con_t0) * 1000
            con = [dt for dt, ok, _ in res if ok]
            con_fail = sum(1 for _, ok, _ in res if not ok)
            for (dt, ok, err) in res:
                if err:
                    print(f"    err: {err}")

            # ---- 4. concurrent multi-session (multiple agents) ----
            print("\n-- 4. concurrent multi-session (5 agents x 4 cmds) --")
            n_sess, per = 5, 4
            sessions = [f"s-agent{j}" for j in range(n_sess)]
            for sj in sessions:
                await client.create_session(sid, sj, env=env)

            async def agent_run(sj, base_idx):
                out = []
                for k in range(per):
                    dt, ok, err = await timed_exec(client, sid, sj, LEMMA_CMDS[(base_idx + k) % len(LEMMA_CMDS)])
                    out.append((dt, ok, err))
                return out

            multi_t0 = time.perf_counter()
            grouped = await asyncio.gather(*[agent_run(sj, j) for j, sj in enumerate(sessions)])
            multi_wall = (time.perf_counter() - multi_t0) * 1000
            multi = [dt for g in grouped for (dt, ok, _) in g if ok]
            multi_fail = sum(1 for g in grouped for (_, ok, _) in g if not ok)
            for sj in sessions:
                await client.delete_session(sid, sj)

            # ---- summary ----
            print("\n========== SUMMARY ==========")
            print(f"sandbox cold start: {cold:.0f}ms")
            summarize("1. raw echo baseline", base)
            summarize("2. lemma sequential", seq, seq_fail)
            print(f"   -> wall time for {N} sequential: {seq_wall:.0f}ms")
            summarize("3. lemma concurrent 1-sess", con, con_fail)
            print(f"   -> wall time for {N} concurrent: {con_wall:.0f}ms  (speedup {seq_wall/con_wall:.1f}x vs sequential)")
            summarize("4. lemma concurrent 5-sess", multi, multi_fail)
            print(f"   -> wall time for {n_sess*per} across {n_sess} sessions: {multi_wall:.0f}ms")
        finally:
            await client.delete_session(sid, main_sess)
            await client.delete_sandbox(sid)
            print("\ncleanup done")


if __name__ == "__main__":
    asyncio.run(main())
