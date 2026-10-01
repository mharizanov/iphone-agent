# iphone-agent

Lets an LLM agent (Claude Code) drive a physical iPhone over USB using WebDriverAgent (WDA) and go-ios. You can take screenshots, read the screen as text, tap, swipe, type and launch apps. It works in the EU, where iPhone Mirroring is blocked.

The folder is self-contained. Clone it on any Mac and run `scripts/setup`. Pinned dependencies install into `var/`, which is gitignored, and every setting lives in `config/iphone.env`.

## Requirements
- A Mac with Xcode, signed in with an Apple ID. A free "Personal Team" works, but it must re-sign every 7 days.
- An iPhone on a USB cable. Developer Mode is enabled during setup.
- git, curl, python3. For the MCP server: the `claude` CLI; `setup` installs the Python MCP SDK into `var/venv`.

## Setup on a new Mac
```
git clone <repo-url> iphone-agent && cd iphone-agent
$EDITOR config/local.env      # your IPHONE_UDID (or empty = auto), DEVELOPMENT_TEAM, WDA_BUNDLE_PREFIX (git-ignored)
scripts/setup --link          # checks tools, pinned go-ios + WebDriverAgent → var/, symlinks into /usr/local/bin
```
Then do the manual steps that `setup` prints:
- add the Apple ID in Xcode;
- trust the Mac on the phone;
- turn on Developer Mode;
- run `scripts/wda-resign`, then trust the developer certificate on the phone.

## Daily use
```
scripts/wda-resign            # when the last one is >7 days old (free Apple ID); phone unlocked
iphone-agent                  # leave running in its own terminal; Ctrl-C stops it (and WDA on the phone)
wda status                    # "ready 16.1.7"
wda launch com.apple.Preferences --fresh --read   # open at the root, print the new screen
wda tap "General" --read      # tap by visible text; act + look in one call
wda collect                   # read a whole scrolling list
wda shot /tmp/s.png 800       # ~0.6 s screenshot (MJPEG stream)
wda keep-awake 30             # hold off the 5-min auto-lock during long tasks
```
Coordinates are points. The x,y column of `wda texts` gives each element's centre. Every command costs about 1 s on the phone. The full list is in `wda help`, and `CLAUDE.md` has agent instructions.

## MCP server (`iphone`)
`scripts/setup --mcp` registers `bin/iphone-mcp` in Claude Code at user scope. It exposes 11 tools:

| Tool | What it does |
|---|---|
| `phone_status` | reachability, lock state, foreground app, battery, connection |
| `screen` | visible elements as `e<ref> type "label" (x,y)`, optionally with a screenshot |
| `tap` | by ref, visible text or points |
| `type_text` | type into a field, optionally submit |
| `scroll` | one page, or until a text is visible |
| `navigate` | launch by app name, open a URL, back, home |
| `wait_for` | wait until a text appears |
| `collect_list` | read a whole scrolling list |
| `screenshot` | JPEG image |
| `read_image_text` | OCR of the screen |
| `handle_alert` | read, accept, dismiss or press a button on a system alert |

How it behaves:
- **One call per step.** Every action returns the settled new screen.
- **Confirmation gate.** Taps on send, pay, delete, call, post and similar labels need `confirm=true`. Labels in other languages: `IPHONE_MCP_EXTRA_RISKY` in `config/local.env`.
- **Screen text is marked as untrusted data.**
- **Lifecycle, with local WDA (USB):**
  - the server starts `iphone-agent` on the first tool call, not when the server starts;
  - it presses volume every 60 s while in use, against the auto-lock;
  - it stops the agent after `IPHONE_MCP_IDLE_MIN` (default 10) idle minutes, which also takes WDA off the LAN.
- **Wi-Fi:** set `WDA_URL=http://<phone-ip>:8100` and start WDA yourself (see `docs/NOTES.md`).
- **Logs:** `var/log/mcp.log`, including each call's duration.
- **Test:** `var/venv/bin/python tests/mcp_smoke.py [--live]`.

## Layout
| Path | What |
|---|---|
| `config/iphone.env` | UDID, signing team, bundle id, pinned versions. Any value can be overridden with an env var. |
| `lib/common.sh` | Shared by all scripts: repo root (resolved through symlinks), config, `var/` paths, go-ios lookup, UDID auto-detect. |
| `bin/iphone-agent` | Supervisor. Runs the tunnel, the `:8100` forward, WDA and the DDI mount, and restarts only what died. |
| `bin/wda` → `lib/wda.py` | WDA client: tap by text, act-and-read (`--read`), wait, collect, MJPEG screenshots, OCR, keep-awake. |
| `scripts/setup` | Installs pinned go-ios and WebDriverAgent into `var/`. `--link` adds the symlinks; `--mcp` registers the `iphone` MCP server. |
| `scripts/wda-resign` | Builds, signs and installs WDA. The team and bundle id are passed on the command line, so upstream stays unmodified. |
| `tests/run-stub-scenarios.sh` | Offline supervisor test against `tests/stub/ios` (~4.5 min). |
| `bin/iphone-mcp` → `lib/iphone_mcp.py` | MCP server (stdio), Python MCP SDK in `var/venv`. |
| `tests/mcp_smoke.py` | MCP client smoke test; `--live` drives the phone. |
| `tools/ocr.swift`, `tools/bench.sh` | Mac-side OCR (Apple Vision; built by setup into `var/bin/ocr`) and a benchmark of the read/act primitives on the current screen. |
| `docs/NOTES.md` | How the stack behaves, the traps, security, and open checks. |
| `CLAUDE.md` | Instructions for an agent driving the phone. |
| `var/` | Created by setup: `bin/ios`, `WebDriverAgent/`, `devimages/`, `log/`, `wda-session`. |

## Security
While WDA runs, it accepts connections from anyone on the phone's Wi-Fi network, without authentication. It can't be bound to USB only through go-ios; see `docs/NOTES.md`. `iphone-agent` kills WDA on the phone when it stops: it's gone from Wi-Fi 3–4 s after Ctrl-C. Run it only while working, and only on a trusted network.

go-ios keeps a pairing private key in `var/selfIdentity.plist`. It's gitignored; never commit it.
