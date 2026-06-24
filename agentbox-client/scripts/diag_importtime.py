"""Profile lemma_cli.cli import inside the sandbox to find the slow modules."""
from __future__ import annotations

import asyncio
import uuid

import httpx

from agentbox_client import AgentBoxClient

AGENTBOX_URL = "https://api-dev.agentbox.work"
AGENTBOX_KEY = "replace-with-a-long-random-token"
LEMMA_BASE_URL = "https://dc9c-2406-7400-54-39e6-00-100c.ngrok-free.app"


def get_token() -> str:
    r = httpx.post(
        f"{LEMMA_BASE_URL}/st/auth/signin",
        headers={"Content-Type": "application/json", "rid": "emailpassword"},
        json={"formFields": [{"id": "email", "value": "anukul@gappy.ai"}, {"id": "password", "value": "@nukulG@ppy786"}]},
        timeout=30,
    )
    r.raise_for_status()
    return r.headers["st-access-token"]


# Sort -X importtime output by cumulative microseconds, show worst 30 leaves.
CMD = (
    "python3 -X importtime -c 'import lemma_cli.cli' 2>/tmp/it.log; "
    "echo '--- TOP CUMULATIVE (us) ---'; "
    "grep 'import time:' /tmp/it.log | awk -F'|' '{gsub(/ /,\"\",$2); print $2\"\\t\"$3}' "
    "| sort -rn | head -30; "
    "echo '--- total wall ---'; "
    "python3 -c 'import time;t=time.time();import lemma_cli.cli;print(round(time.time()-t,2),\"s\")'"
)


async def main() -> None:
    token = get_token()
    env = {"LEMMA_TOKEN": token, "LEMMA_BASE_URL": LEMMA_BASE_URL, "LEMMA_AUTH_URL": LEMMA_BASE_URL + "/auth"}
    sid = f"itdiag-{uuid.uuid4().hex[:8]}"
    sess = "s"
    async with AgentBoxClient(base_url=AGENTBOX_URL, api_key=AGENTBOX_KEY) as c:
        await c.ensure_sandbox(sid, env=env)
        await c.create_session(sid, sess, env=env)
        try:
            r = await c.exec_command(sid, sess, cmd=CMD, timeout=120)
            print(r.stdout)
            if r.stderr:
                print("STDERR:", r.stderr[:500])
        finally:
            await c.delete_session(sid, sess)
            await c.delete_sandbox(sid)
    print("\nIT DONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
