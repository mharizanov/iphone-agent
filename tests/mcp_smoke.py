"""MCP smoke test: runs bin/iphone-mcp as a real stdio client would.

  var/venv/bin/python tests/mcp_smoke.py            # protocol only: init + tool list (no phone)
  var/venv/bin/python tests/mcp_smoke.py --live     # + status, screen, Settings → General → back

A stray print on the server's stdout shows up here as a protocol error.
The --live run starts iphone-agent through the server (phone on USB, unlocked)
and stops it again when the server exits.
"""
import asyncio
import os
import sys
import time

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPECTED = {"phone_status", "screen", "tap", "type_text", "scroll", "navigate", "wait_for",
            "collect_list", "screenshot", "read_image_text", "handle_alert"}


def text_of(result):
    return "\n".join(getattr(c, "text", f"<{type(c).__name__}>") for c in result.content)


async def main(live):
    params = StdioServerParameters(command=os.path.join(ROOT, "bin", "iphone-mcp"), args=[],
                                   env={**os.environ, "IPHONE_MCP_IDLE_MIN": "5"})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            print("server:", init.server_info.name if hasattr(init, "server_info") else init)
            tools = {t.name: t for t in (await s.list_tools()).tools}
            missing = EXPECTED - set(tools)
            print(f"tools: {len(tools)} {'OK' if not missing else 'MISSING ' + str(missing)}")
            for n in sorted(tools):
                a = tools[n].annotations
                print(f"  {n:16s} readOnly={getattr(a, 'read_only_hint', None)} destructive={getattr(a, 'destructive_hint', None)}")
            if not live:
                return 0 if not missing else 1

            async def call(name, **args):
                t0 = time.time()
                res = await s.call_tool(name, args)
                body = text_of(res)
                print(f"\n== {name} {args}  {time.time() - t0:.1f}s  error={res.is_error if hasattr(res, 'is_error') else res.isError}")
                print("\n".join(body.splitlines()[:8]))
                return body

            await call("phone_status")
            await call("navigate", action="launch", app="Settings", fresh=True)
            await call("tap", text="General")
            scr = await call("screen")
            ref = next((l.split()[0] for l in scr.splitlines() if '"About"' in l), None)
            if ref:
                await call("tap", ref=ref)
                await call("navigate", action="back")
            await call("scroll", direction="down", until_text="Reset", max_pages=4)
            gated = await call("tap", text="Reset")          # confirmation gate, must not tap
            assert "NEEDS CONFIRMATION" in gated, gated
            missing = await call("tap", text="No Such Label XYZ")   # error text must reach the model
            assert "no element labelled" in missing, missing
            await call("navigate", action="home")
            return 0


sys.exit(asyncio.run(main("--live" in sys.argv)))
