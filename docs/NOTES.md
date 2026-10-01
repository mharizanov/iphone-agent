# Stack notes

This file covers how WebDriverAgent (WDA), go-ios and the MCP servers behave together, and the traps found. Findings are dated. Anything specific to one machine or phone belongs in that owner's own notes.

## Why this stack
**Why not iPhone Mirroring:**
- iPhone Mirroring is region-blocked by Apple across the EU, so Mirroring-based tools (e.g. phone-harness) do not work there.
- WDA over USB works in any region.

**The pieces:**
- **WebDriverAgent** is an XCUITest runner on the phone. It serves an HTTP API on device port 8100.
- **go-ios** provides:
  - the iOS 17+ userspace tunnel;
  - a usbmux port-forward from `localhost:8100` to the phone;
  - `ios runwda` to launch the runner;
  - `ios image auto` to mount the developer disk image (DDI).

## The chain and how it dies
1. **Screen lock: behaviour changed.**
   - **2026-08-14 (iOS 26.5):** every lock tore down the phone's lockdown tunnel service. The go-ios tunnel then logged `framedIPv6Reader: failed to read IPv6 header: EOF` and exited, and WDA died with it.
   - **2026-09-30 (iOS 26, after re-pairing):** a manual ~3-minute lock did **not** do that.
     - Screenshots every 5 s show the always-on clock, a dark screen, the lock screen, then home.
     - `/status`, `/wda/activeAppInfo` and `/screenshot` answered OK throughout; WDA even captured the lock-screen frames.
     - No tunnel error, no restart.
   - An idle auto-lock was not tested.
   - Synthesized WDA taps do **not** reset the auto-lock timer; only real touches do.
   - `iphone-agent` restarts the chain after an unlock if it does die.
2. **Reboots and iOS updates unmount the DDI.**
   - `ios runwda` then fails with `testmanagerd.remote is not available in RSD`.
   - Fix: `ios image auto`. `iphone-agent` runs it on every cycle, caching in `var/devimages`.
3. **USB hubs are a failure point.** Through a monitor's USB2 hub, iOS can drop the data connection after long locks. The phone then disappears from `ios list` and needs a re-plug. A direct Mac port avoids this.
4. **The phone is listed twice.** With Wi-Fi sync on, `ios list` shows the same UDID twice (USB and network). All scripts pass `--udid`.
5. **A lock can leave WDA half-alive.** WDA stays up but answers "Not authorized for performing UI testing actions". `iphone-agent` catches this with a periodic `/screenshot` probe (`AUTH_PROBE`).

## Wi-Fi only, no cable (tested 2026-09-30)
**go-ios cannot do it.** go-ios 1.3.2 has no network pairing. Its tunnel agent starts but never builds a tunnel to a phone that is only on the network (`ConnectionType: Network`). Lockdown over Wi-Fi does work: the disk-image check reached the phone.

**Xcode can.** The phone advertises `_remotepairing._tcp` over Bonjour, and `xcrun devicectl list devices` shows it "available (paired)". Recipe:
1. Launch WDA wirelessly. It prints `ServerURLHere->http://<phone-ip>:8100<-`:
   ```
   cd var/WebDriverAgent && xcodebuild -project WebDriverAgent.xcodeproj -scheme WebDriverAgentRunner \
     -destination "id=$IPHONE_UDID" -allowProvisioningUpdates DEVELOPMENT_TEAM=… \
     PRODUCT_BUNDLE_IDENTIFIER=… test
   ```
2. Drive it directly, with no forward: `WDA_URL=http://<phone-ip>:8100 bin/wda …`. `bin/wda` takes the MJPEG host from `WDA_URL`.
3. Stop it with SIGINT to `xcodebuild`. WDA was off Wi-Fi 3 s later.

**Speed over Wi-Fi is about half of USB:**

| Operation | Wi-Fi | USB |
|---|---|---|
| `texts` | 1.8–2.3 s | 0.9 s |
| `shot` | 1.15 s | 0.64 s |
| tap + read | 6–7 s | 3–5 s |

**Real task over Wi-Fi:** a social app → search → open a profile → open the latest post → capture every item of a multi-photo post took **~3 min** from request to answer.

**Caveats:**
- The launch is Xcode's test run (`xcodebuild`), not the go-ios chain, so `iphone-agent`'s supervision (restarts, keep-alive checks) doesn't apply yet.
- WDA is reachable from the whole LAN, same as on USB.
- Away from home (5G) would need a VPN between phone and Mac. Untested.

## Talking to WDA directly (`bin/wda`)
**Sessions:**
- `POST /session` with **full** XCUITest capabilities:
  `{"capabilities":{"alwaysMatch":{"platformName":"iOS","appium:automationName":"XCUITest","appium:noReset":true,"appium:newCommandTimeout":600}}}`.
- A minimal `{"platformName":"iOS"}` body **crashed** WDA 16.1.7 on 2026-08-12. That did not reproduce on 2026-09-30.

**Endpoints:**
- `GET /screenshot` and `GET /source?format=json` work without a session.
- Taps and swipes: `/session/:id/wda/tap/0` returns "Unhandled endpoint". Use W3C `POST /session/:id/actions` with a touch pointer: `pointerMove` → `pointerDown` → `pause` → `pointerUp`. A swipe adds a second `pointerMove`.
- App launch: `POST /session/:id/wda/apps/launch` with `{"bundleId":…}`.
- `bin/wda texts` dumps visible labels with their positions. It is the cheapest way to read a screen as text.

**Coordinates:** they are **points** (`/window/size`, e.g. 375×812), not screenshot pixels (e.g. 1125×2436 at 3×). A wrong scale gives no error; the tap just lands in the wrong place.

**Speed settings:**
- Defaults: `waitForIdleTimeout` 10 s, `animationCoolOffTimeout` 2 s, `snapshotMaxDepth` 50.
- `bin/wda` lowers them to 1 s / 0 s / 30 through `/appium/settings` on each new session.
- A messaging app's `/source` went from 7.3 s to 3.2 s (2026-09-30).

**Screenshot cost:**
- `/screenshot` at the default `screenshotQuality` 3 captures HEIC, then re-encodes a full-resolution PNG and base64-encodes it.
- WDA serves one request at a time, so polling it slows everything else.

**Other traps:**
- Deep links (`App-prefs:…`) don't navigate Settings on iOS 26. Tap through the UI instead.
- `ios runwda` needs `--xctestconfig WebDriverAgentRunner.xctest`, **not** `.xctestconfig`.

## Security
**WDA listens on all interfaces.** While it runs, anyone on the same Wi-Fi can read the screen and drive the phone without authentication through `http://<phone-ip>:8100`. The phone's IP is in `/status` → `value.ios.ip`. Verified 2026-09-30: `/status` and `/screenshot` both answered on the phone's Wi-Fi address.

**Binding it doesn't work through go-ios (A/B test, 2026-09-30):**
- With `ios runwda --env USE_IP=127.0.0.1`, WDA never came up on USB or Wi-Fi.
- `USE_IP=0.0.0.0` failed the same way.
- The control runs without it came up in 14–17 s.
- So passing `--env` itself breaks the launch; the cause is unknown. Restricting the bind would need a WDA build that defaults to loopback, and it's unverified whether the usbmux forward even reaches loopback.

**Stopping is the mitigation:**
- Killing `ios runwda` alone left WDA running on the phone, still on Wi-Fi, for 30–40 s.
- `iphone-agent` now kills the runner explicitly on every stop and every WDA restart: `ios kill --process=WebDriverAgentRunner-Runner`. The process name is not the bundle id; killing by bundle id says "process not found".
- It runs with `set -m`, so a terminal Ctrl-C reaches only the script, and cleanup still has a live tunnel to do that kill. Without it, Ctrl-C killed go-ios too and WDA was orphaned.
- Measured: WDA is gone from Wi-Fi 3–4 s after Ctrl-C.
- Run the agent only while working, and on a trusted network.

**Pairing identity:** go-ios writes its host pairing identity `selfIdentity.plist`, **including a private key**, into its current directory. `iphone-agent` runs go-ios from `var/`, and `.gitignore` excludes the file. Never commit it.

## `iphone` MCP server (2026-09-30)
It lives in `lib/iphone_mcp.py` and uses the Python MCP SDK 2.2.0 (`mcp.server.mcpserver.MCPServer`; FastMCP was renamed in 2.x). The tool list and behaviour are in `README.md`. Findings from building it:

- **stdout is the protocol channel.** `lib/wda.py` code the server calls must not print. `collect` is a generator (`collect_labels`), and `keep_awake` takes a log callback.
- **Error text.** SDK 2.x shows the model only "Error executing tool X" for unexpected exceptions. Intentional messages must be raised as `ToolError`.
- **Tools are `async`** and run the blocking WDA calls through `asyncio.to_thread`. A lock guards session re-creation, because the keep-awake thread and a tool call can both hit an expired session.
- **App switches can block WDA for 60 s.** Reading the screen right after `home` (or a launch) can still target the app going to the background. WDA then asks it for accessibility attributes and waits out iOS's IPC timeout: `kAXErrorIPCTimeout`, "took 60.0 s", during which WDA answers nothing. Fix: `wait_active(bundle)` polls `/wda/activeAppInfo` until the expected app is in front. `navigate home` went from 63 s to 5 s.
- **Live smoke test over Wi-Fi passes:**

  | Step | Time |
  |---|---|
  | status | 0.7 s |
  | launch Settings by name | 6 s |
  | tap "General" (covered → revealed) | 11 s |
  | tap by ref | 4 s |
  | back | 5 s |
  | scroll until "Reset" | 14 s |
  | gate on "Transfer or Reset iPhone" | 1.2 s, no tap |
  | error text passes through | ✓ |
  | home | 5 s |

- **Not yet tested live:** starting and idle-stopping the agent on USB (the phone was only on Wi-Fi). `claude mcp list` health checks do not start the agent (verified).

## mobile-mcp (third-party; unregistered 2026-09-30, replaced by `iphone`)
Pinned to 1.0.5 (mobilecli 1.0.13) in `config/iphone.env`, and registered by `scripts/setup --mcp`.

**Live result, 2026-09-30:**
- `launch_app` works.
- `list_elements_on_screen` and `take_screenshot` fail with `unexpected end of JSON input`.
- WDA stayed up, and the same endpoints work over curl.
- Root cause unknown. Use `bin/wda`.

**Code read of 1.0.5:**
- **Default path:** every tool call spawns `mobilecli` through `execFileSync`, blocking Node, and runs `device info` + `POST /session`.
- **`MOBILEMCP_LEGACY_ROBOT=1`** switches to a direct-HTTP robot. It still spawns `ios info` on every call and creates and deletes a session per action. It also returns full-size PNGs. Untested here.
- **Either path:** no session lives long enough to keep lowered WDA settings.

## Speed (measured 2026-09-30, iOS 26, `tools/bench.sh`)
The phone is not the main bottleneck. The number of steps and LLM round-trips is.

| Primitive | Measured | Notes |
|---|---|---|
| `/source?format=json` (full) | 3.6–7.3 s | costly per-element attributes, `visible` above all |
| `/source?format=json&excluded_attributes=visible,accessible,enabled,focused,frame,nativeFrame,traits,placeholderValue,nativeAccessibilityElement` | **0.95–1.5 s** | labels and rects kept; visibility can be computed client-side from rects |
| `/screenshot` | 0.7–10 s, 0.6–3.3 MB | always re-encoded to a full-resolution PNG on the phone; `screenshotQuality` 0–3 doesn't fix it |
| MJPEG frame (port 9100, `mjpegScalingFactor` 50, quality 60) | ~1.0 s, 210 KB JPEG | needs a second forward (`ios forward 9100 9100`); a persistent reader would be faster still |
| OCR on the Mac (`tools/ocr.swift`, Apple Vision) | 1.1–1.5 s | needs full-resolution input (a 230 px image gives nothing); only for screens without labels |
| tap (`POST /actions`) | 0.75–0.95 s | `waitForIdleTimeout` 1 vs 0 makes no difference |
| swipe (400 ms drag) | ~1.5 s | |
| `bin/wda` session check per call | 0.65 s | avoidable: trust the cached session, retry on error |

**After the speed work** (`lib/wda.py`, measured 2026-09-30 on the phone):

| Task | Before | After |
|---|---|---|
| `wda texts` | 3.6–7.3 s | **0.87 s** |
| `wda shot` | 0.7–10 s | **0.64 s** (MJPEG, forwarded with 8100 by one go-ios process) |
| launch app + read the new screen | 2 calls, ~6–9 s | 1 call, 3.9 s (`--read`) |
| tap by text + read the new screen | look + compute + tap + look, ~10 s | **1 call, 3–5 s** |
| full "iPhone Storage" app list (282 entries) | 382 s | **93 s** |
| settings check (search → open → read) | ~3 min | **4 calls, 20 s** |

The remaining time per step is the settle check (two identical lean reads) plus the LLM's own turn. The LLM turn now dominates.

**Keep-awake:** volume up and then down every 60 s kept the phone unlocked for 9+ minutes past its 5-minute auto-lock policy (`wda keep-awake`). It wasn't isolated from the concurrent synthesized taps, which the 2026-08-14 notes say do not reset the timer.

**Traps found while measuring:**
- **Screens stacked behind the current one can leak into `texts`.** On one app's search screen, the feed grid behind it was still listed. The lean tree has no visibility flag, and they are on-screen by rect. When `texts` and the screen disagree, trust a `shot`.
- **Overlays cover rows (fixed in `wda`).** Rows under a floating bar or keyboard are marked `(covered — scroll first)`. `tap "<label>"` scrolls such a row clear, waits for the list to settle, then taps. Before this fix, `tap "General"` in iOS 26 Settings hit the bottom search bar.
- **Through the go-ios forward, Python `urllib` reads of a ~0.5 MB reply were cut short** (IncompleteRead) three times in a row, while curl got every byte. `wda collect` uses curl for that reason; the cause is unconfirmed.
- **The lock screen can cover apps while the phone is unlocked.** After a Face ID unlock the cover sheet stays until you swipe up. `/wda/locked` correctly says `false`, but taps land on the lock screen. `/wda/activeAppInfo` says SpringBoard, the same as on the home screen.
- **Tree-kill stop:** a stop that kills the whole process tree also kills go-ios. The agent's cleanup then brings up a temporary tunnel to kill WDA; it was gone from Wi-Fi at 13 s in the test.

## Open checks
1. Find why mobilecli fails `dump ui` / `screenshot` when curl works, and try `MOBILEMCP_LEGACY_ROBOT=1`.
2. Test an idle auto-lock (5 min) and a lock of 10 minutes or more against the 2026-09-30 result.
3. `/wda/activeAppInfo` as a cheaper `AUTH_PROBE`: it stayed OK through a lock, like `/screenshot`, but the half-alive state never occurred, so whether it catches that state is still unknown.
4. Find why `ios runwda --env …` keeps WDA from starting.
