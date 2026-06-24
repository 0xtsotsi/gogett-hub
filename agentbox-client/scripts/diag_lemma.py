"""Isolate where lemma-cli per-command time goes inside the sandbox.

Timed from the client; agentbox exec overhead (~200ms) is negligible vs the
multi-second startup we're chasing, and a raw `echo` probe quantifies it.
"""
from __future__ import annotations

import asyncio
import time
import uuid

import httpx

from agentbox_client import AgentBoxClient

AGENTBOX_URL = "https://api-dev.agentbox.work"
AGENTBOX_KEY = "replace-with-a-long-random-token"
LEMMA_BASE_URL = "https://dc9c-2406-7400-54-39e6-00-100c.ngrok-free.app"
EMAIL = "anukul@gappy.ai"
PASSWORD = "@nukulG@ppy786"
ORG_ID = "019e90f2-efb4-72c8-85a4-e9978537251c"
POD_ID = "019ea060-3203-722e-853e-d933051925b0"


def get_token() -> str:
    r = httpx.post(
        f"{LEMMA_BASE_URL}/st/auth/signin",
        headers={"Content-Type": "application/json", "rid": "emailpassword"},
        json={"formFields": [{"id": "email", "value": EMAIL}, {"id": "password", "value": PASSWORD}]},
        timeout=30,
    )
    r.raise_for_status()
    return r.headers["st-access-token"]


PROBES = [
    ("echo (agentbox overhead)", "echo hi"),
    ("python no-op", "python3 -c 'pass'"),
    ("import lemma_cli.cli", "python3 -c 'import lemma_cli.cli'"),
    ("lemma --help (no net)", "lemma --help"),
    ("curl /organizations direct", f"curl -s -o /dev/null -w '%{{time_total}}' {LEMMA_BASE_URL}/organizations -H \"Authorization: Bearer $LEMMA_TOKEN\""),
    ("lemma me get (startup+1 api)", "lemma --json me get"),
    ("lemma agents list (startup+1 api)", "lemma --json agents list"),
    ("lemma me get (2nd, warm?)", "lemma --json me get"),
]


async def main() -> None:
    token = get_token()
    env = {
        "LEMMA_TOKEN": token,
        "LEMMA_BASE_URL": LEMMA_BASE_URL,
        "LEMMA_AUTH_URL": LEMMA_BASE_URL + "/auth",
        "LEMMA_ORG_ID": ORG_ID,
        "LEMMA_POD_ID": POD_ID,
    }
    sid = f"diag-{uuid.uuid4().hex[:8]}"
    sess = "s-diag"
    async with AgentBoxClient(base_url=AGENTBOX_URL, api_key=AGENTBOX_KEY) as c:
        await c.ensure_sandbox(sid, env=env)
        await c.create_session(sid, sess, env=env)
        try:
            for label, cmd in PROBES:
                t0 = time.perf_counter()
                r = await c.exec_command(sid, sess, cmd=cmd, timeout=120)
                dt = (time.perf_counter() - t0) * 1000
                tail = (r.stdout or r.stderr or "").strip().replace("\n", " ")[:90]
                print(f"{dt:8.0f}ms  exit={r.exit_code}  {label:34} | {tail}", flush=True)
        finally:
            await c.delete_session(sid, sess)
            await c.delete_sandbox(sid)
    print("\nDIAG DONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
