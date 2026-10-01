"""Interactive MCP test: lazy agent start, lock = revoke, restart after unlock.

  var/venv/bin/python tests/mcp_lock_test.py     # phone on USB, unlocked

1. phone_status  → the server starts iphone-agent itself (lazy start)
2. screen        → works while unlocked
3. waits (≤ 3 min) for the user to LOCK the phone; screen() must then refuse
   with "LOCKED", the agent must be gone and WDA off Wi-Fi
4. waits (≤ 3 min) for the user to UNLOCK; screen() must work again
   (the server starts a new agent)
Progress goes to stdout with timestamps.
"""
import asyncio
import os
import subprocess
import sys
import time

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IP_FILE = os.path.join(ROOT, "var", "phone-ip")


def say(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def agent_running():
    return subprocess.run(["pgrep", "-f", r"^/bin/bash .*bin/iphone-agent$"], capture_output=True).returncode == 0


def wda_on_wifi():
    if not os.path.exists(IP_FILE):
        return None
    ip = open(IP_FILE).read().strip()
    out = subprocess.run(["curl", "-s", "--max-time", "2", f"http://{ip}:8100/status"], capture_output=True).stdout
    return b'"ready"' in out


async def main():
    params = StdioServerParameters(command=os.path.join(ROOT, "bin", "iphone-mcp"), args=[],
                                   env={**os.environ, "WDA_URL": "http://localhost:8100"})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def call(name, **a):
                res = await s.call_tool(name, a)
                text = "\n".join(getattr(c, "text", "") for c in res.content)
                return res.is_error, text

            say("agent running before first call:", agent_running())
            t0 = time.time()
            err, text = await call("phone_status")
            say(f"phone_status ({time.time() - t0:.0f}s, error={err}):", text.replace("\n", " | ")[:200])
            if err:
                return 1
            err, text = await call("screen")
            say("screen while unlocked: error =", err, "|", text.splitlines()[1][:80] if not err else text[:120])

            say(">>> LOCK THE PHONE NOW (side button)")
            t0 = time.time()
            while time.time() - t0 < 180:
                err, text = await call("screen")
                if err and "LOCKED" in text:
                    say(f"screen refused after lock ({time.time() - t0:.0f}s): {text[:110]}")
                    break
                await asyncio.sleep(3)
            else:
                say("FAIL: no refusal within 3 min")
                return 1
            await asyncio.sleep(6)
            say("agent still running:", agent_running(), "| WDA on Wi-Fi:", wda_on_wifi())

            say(">>> UNLOCK THE PHONE NOW")
            t0 = time.time()
            while time.time() - t0 < 180:
                err, text = await call("screen")
                if not err:
                    say(f"screen works again after unlock ({time.time() - t0:.0f}s):", text.splitlines()[1][:80])
                    say("agent running:", agent_running())
                    return 0
                await asyncio.sleep(5)
            say("FAIL: screen did not work again within 3 min:", text[:150])
            return 1


sys.exit(asyncio.run(main()))
