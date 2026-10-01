# iphone-agent

Lets an LLM agent (Claude Code, or any MCP client) drive a physical iPhone: read the screen as text, tap, type, scroll, launch apps, take screenshots. It is built for jobs that only exist inside the phone – Settings has no export or API, and Shortcuts cannot read it – such as storage cleanup and auditing app permissions.

Why another one, when open-source tools exist: whoever's code drives the phone sees everything on it, banking and work apps included. This repo stays small enough to read end to end, pins every dependency, builds WebDriverAgent from source, sends no telemetry, and keeps all personal settings in one git-ignored file.

## How it works
```
Claude Code ──MCP (stdio)──▶ bin/iphone-mcp ─┐
you, a shell ──────────────▶ bin/wda ────────┼─HTTP─▶ localhost:8100 ─▶ WebDriverAgent on the iPhone
                                             │                        (XCTest UI automation)
bin/iphone-agent (supervisor) ── go-ios: USB tunnel, port forward, WDA launch, disk-image mount
```
- **WebDriverAgent (WDA)** is a small HTTP server built on Apple's UI-testing framework. It runs on the phone and does the tapping and reading.
- **go-ios** carries it over USB. **`iphone-agent`** keeps that chain alive and restarts whatever dies.
- **The MCP server** starts and stops the agent by itself.

## Requirements
- A Mac with Xcode, signed in with an Apple ID. A free "Personal Team" works, but it must re-sign every 7 days.
- An iPhone on a USB cable, with Developer Mode on.
- git, curl, python3. For the MCP server: the `claude` CLI. `setup` installs the Python MCP SDK into `var/venv`.

## Setup on a new Mac
```
git clone <repo-url> iphone-agent && cd iphone-agent
cp config/local.env.example config/local.env
$EDITOR config/local.env      # UDID (or empty = auto), Team ID, your own bundle prefix
scripts/setup --link --mcp    # pinned go-ios + WebDriverAgent → var/, symlinks, registers the MCP server
```
Then do the manual steps that `setup` prints:
1. Add the Apple ID in Xcode.
2. Trust the Mac on the phone.
3. Turn on Developer Mode; the phone restarts.
4. Run `scripts/wda-resign`, then trust the developer certificate on the phone: Settings → General → VPN & Device Management.

**Configuration:**
- `config/iphone.env` holds the generic defaults and pinned versions.
- `config/local.env` (git-ignored) holds your values.
- Environment variables override both.

## Use from Claude Code (MCP server `iphone`)
After `scripts/setup --mcp`, restart Claude Code and ask it to do something on the phone. Eleven tools:

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
| `read_image_text` | OCR of the screen, for screens without labels |
| `handle_alert` | read, accept, dismiss or press a button on a system alert |

Every action returns the settled new screen, so a step is one call. Logs go to `var/log/mcp.log`, with each call's duration. `CLAUDE.md` has the agent instructions.

## Use from a shell (`wda`)
```
iphone-agent                  # leave running in its own terminal; Ctrl-C stops it (and WDA on the phone)
wda status                    # "ready 16.1.7"
wda launch Settings --fresh --read   # open at the root, print the new screen
wda tap "General" --read      # tap by visible text; act + look in one call
wda collect                   # read a whole scrolling list
wda shot /tmp/s.png 800       # ~0.6 s screenshot (MJPEG stream)
wda keep-awake 30             # hold off the auto-lock during long tasks
```
Coordinates are points. The x,y column of `wda texts` gives each element's centre. `wda help` lists every command.

## Safety model
- **Confirmation gate.** Taps on send, pay, delete, call, post and similar labels need `confirm=true`. For labels in your language, set `IPHONE_MCP_EXTRA_RISKY` in `config/local.env`.
- **Screen text is untrusted data.** The server labels it so; a message on the phone is not an instruction.
- **No lingering access.**
  - The MCP server stops the agent after `IPHONE_MCP_IDLE_MIN` (default 10) idle minutes.
  - Stopping the agent kills WDA on the phone, which is gone from Wi-Fi 3–4 s after Ctrl-C.
  - Locking the phone ends an MCP session. This is implemented, not yet verified on a device.
- **WDA has no authentication.** While it runs, anyone on the phone's Wi-Fi can drive it. Binding it to USB only is not possible through go-ios (see `docs/NOTES.md`). Run it only while working, on a trusted network.
- **What the model sees, the model provider sees.** Keep the agent out of apps you would not show it.
- **Developer Mode is the real off switch.** With it off, nothing – this tool included – can drive the phone.
- **The go-ios pairing private key** lives in `var/selfIdentity.plist`, which is git-ignored. Never commit it.

## Limitations
- **Connection:** USB, or the same Wi-Fi when WDA is launched through Xcode (`docs/NOTES.md`). No remote access from another network.
- **Locked phone:** WDA cannot start on a locked phone and cannot get past Face ID or the passcode.
- **Auto-lock:** a short auto-lock policy needs `keep-awake`; WDA taps do not reset the timer.
- **Hidden screens:** screens stacked behind the current one can leak into `texts`. When in doubt, take a screenshot.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `wda-resign`: "Device is busy (Preparing …)" | Xcode is building support for a new iOS version. Keep the phone unlocked for a few minutes and retry. |
| `wda-resign`: "Developer App Certificate is not trusted" | On the phone: Settings → General → VPN & Device Management → your Apple ID → Trust. Then retry. |
| "WDA did not come up" | Unlock the phone. If it's been more than 7 days on a free account, run `scripts/wda-resign`. |
| Phone missing from `var/bin/ios list` | Re-plug it, ideally straight into the Mac (monitor USB hubs drop it after long locks). Tap "Trust" if asked. |
| Taps do nothing, `texts` shows the lock screen | After a Face ID unlock the lock screen can stay up. Swipe up on the phone. |
| "localhost:8100/9100 is already held by …" | Another process uses the port. Stop it, or stop the other agent. |

## Tests
- `tests/run-stub-scenarios.sh` is the offline supervisor test against a fake go-ios (~4.5 min).
- `var/venv/bin/python tests/mcp_smoke.py` is the MCP protocol check. Add `--live` to drive the phone.
- `var/venv/bin/python tests/mcp_lock_test.py` is the interactive lock and unlock test.
- `tools/bench.sh` times the read and act primitives on the current screen.

## Layout
| Path | What |
|---|---|
| `config/iphone.env` | generic defaults and pinned versions |
| `config/local.env.example` | template for your git-ignored `config/local.env` |
| `lib/common.sh` | shared by all scripts: repo root (through symlinks), config, `var/` paths, go-ios lookup, UDID auto-detect |
| `bin/iphone-agent` | supervisor for the tunnel, the `:8100`/`:9100` forward, WDA and the disk image |
| `bin/wda` → `lib/wda.py` | WDA client: tap by text, act-and-read, wait, collect, MJPEG screenshots, OCR, keep-awake |
| `bin/iphone-mcp` → `lib/iphone_mcp.py` | the MCP server (stdio) |
| `scripts/setup` | installs pinned go-ios, WebDriverAgent and the MCP SDK into `var/`; `--link`, `--mcp` |
| `scripts/wda-resign` | builds, signs and installs WDA with your team and bundle id; upstream stays unmodified |
| `tests/`, `tools/` | see Tests |
| `docs/NOTES.md` | how the stack behaves: traps, measurements, security findings, open checks |
| `CLAUDE.md` | instructions for an agent driving the phone |
| `var/` | created by setup, git-ignored: `bin/ios`, `bin/ocr`, `WebDriverAgent/`, `venv/`, `devimages/`, `log/` |
