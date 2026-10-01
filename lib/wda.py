"""wda — drive the iPhone through WebDriverAgent. Invoked by bin/wda.

Speed rules (measured 2026-09-30, see docs/NOTES.md "Speed"):
  - read the screen with the LEAN /source (costly attributes excluded, ~1 s
    instead of 3-7 s); visibility is decided here from element rects
  - screenshots come from WDA's MJPEG stream (:9100, ~0.2-1 s, JPEG) instead
    of /screenshot (full-res PNG, 0.7-10 s); /screenshot is the fallback
  - the session is trusted from var/wda-session and re-created only when a
    call reports it invalid (a per-call check cost 0.65 s)
  - actions take --read: wait until the UI settles, then print the new
    screen — one call per step instead of act + look
HTTP goes through curl: urllib reads of large replies through the go-ios
forward were cut short (IncompleteRead) while curl got every byte.
"""
import json
import signal
import os
import re
import socket
import subprocess
import sys
import threading
import time

WDA = os.environ.get("WDA_URL", "http://localhost:8100")
VAR = os.environ["VAR_DIR"]
SID_FILE = os.path.join(VAR, "wda-session")
MJPEG_PORT = int(os.environ.get("WDA_MJPEG_PORT", "9100"))
OCR_BIN = os.path.join(VAR, "bin", "ocr")
IOS_BIN = os.environ.get("IOS_BIN", "")
APPS_CACHE = os.path.join(VAR, "apps-cache.json")
LEAN = ("visible,accessible,enabled,focused,frame,nativeFrame,traits,"
        "placeholderValue,nativeAccessibilityElement")
SETTINGS = {
    # defaults 10 s / 2 s / 50; per session, so set on every new session
    "waitForIdleTimeout": 1, "animationCoolOffTimeout": 0, "snapshotMaxDepth": 30,
    # MJPEG frames: half scale, quality 60 → ~200 KB JPEG
    "mjpegScalingFactor": 50, "mjpegServerScreenshotQuality": 60, "mjpegServerFramerate": 10,
}
TAPPABLE = ("Button", "Cell", "Link", "Icon", "Switch", "TextField", "SecureTextField",
            "SearchField", "MenuItem", "Tab", "SegmentedControl", "StaticText", "Other", "Image")


class WdaError(Exception):
    pass


def die(msg, code=1):
    print(f"wda: {msg}", file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------- HTTP

def http(method, path, body=None, timeout=60):
    cmd = ["curl", "-s", "--max-time", str(timeout), "-X", method, WDA + path]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    last = None
    for _ in range(3):
        out = subprocess.run(cmd, capture_output=True).stdout
        try:
            return json.loads(out)
        except json.JSONDecodeError as e:
            last = e
    raise WdaError(f"{method} {path}: no valid reply ({last})")


def value(reply):
    v = reply.get("value")
    if isinstance(v, dict) and "error" in v:
        raise WdaError(f"{v.get('error')}: {str(v.get('message', ''))[:160]}")
    return v


# ---------------------------------------------------------------- session

_session_lock = threading.Lock()


def new_session():
    caps = {"capabilities": {"alwaysMatch": {
        "platformName": "iOS", "appium:automationName": "XCUITest",
        "appium:noReset": True, "appium:newCommandTimeout": 600}}}
    sid = value(http("POST", "/session", caps, 30))["sessionId"]
    http("POST", f"/session/{sid}/appium/settings", {"settings": SETTINGS}, 10)
    with open(SID_FILE, "w") as f:
        f.write(sid)
    return sid


def session_call(method, path, body=None, timeout=60):
    """POST/GET under /session/<id>; re-create the session once if WDA
    restarted (invalid session id) instead of checking it on every call."""
    def current():
        return open(SID_FILE).read().strip() if os.path.exists(SID_FILE) else ""
    sid = current()
    if not sid:
        with _session_lock:          # keep-awake thread + a tool call must not both create one
            sid = current() or new_session()
    for attempt in (1, 2):
        reply = http(method, f"/session/{sid}{path}", body, timeout)
        v = reply.get("value")
        if isinstance(v, dict) and v.get("error") in ("invalid session id", "no such session") and attempt == 1:
            with _session_lock:
                fresh = current()
                sid = fresh if fresh and fresh != sid else new_session()
            continue
        return value(reply)


# ---------------------------------------------------------------- screen

def tree():
    return value(http("GET", f"/source?format=json&excluded_attributes={LEAN}"))


OVERLAYS = ("Toolbar", "TabBar", "Keyboard")


def screen_elements(root=None):
    """Visible labelled elements: (type, cx, cy, label, rect, covered).
    Visibility = rect intersects the screen (the lean tree has no isVisible).
    covered = the centre lies under an overlay (toolbar, tab bar, keyboard —
    e.g. the floating search bar at the bottom of iOS 26 Settings) that the
    element is not part of: a tap there hits the overlay instead."""
    root = root or tree()
    W, H = root["rect"]["width"], root["rect"]["height"]
    raw, overlays = [], []

    def walk(n, in_overlay):
        r = n.get("rect") or {}
        x, y, w, h = r.get("x", 0), r.get("y", 0), r.get("width", 0), r.get("height", 0)
        t = n.get("type", "?")
        on = w > 0 and h > 0 and x < W and y < H and x + w > 0 and y + h > 0
        if t == "Key":                       # 30+ keys cost tokens and are never the target
            return
        if t in OVERLAYS and on and w * h < W * H * 0.6:
            overlays.append((x, y, w, h))
            in_overlay = True
        lab = (n.get("label") or n.get("value") or "")
        lab = lab.strip() if isinstance(lab, str) else ""
        if lab and t == "Switch" and n.get("value") in ("0", "1"):
            lab += " [on]" if n["value"] == "1" else " [off]"
        if lab and on and "scroll bar" not in lab:
            cx = int(max(0, x) + min(w, W - max(0, x)) / 2)
            cy = int(max(0, y) + min(h, H - max(0, y)) / 2)
            raw.append((t, cx, cy, lab.replace("\n", " / "), (x, y, w, h), in_overlay))
        for c in n.get("children") or []:
            walk(c, in_overlay)

    walk(root, False)
    out, seen = [], set()
    for t, cx, cy, lab, rect, in_overlay in raw:
        covered = (not in_overlay) and any(
            ox <= cx <= ox + ow and oy <= cy <= oy + oh for ox, oy, ow, oh in overlays)
        key = (lab, cx, cy)
        if key not in seen:
            seen.add(key)
            out.append((t, cx, cy, lab, rect, covered))
    out.sort(key=lambda e: (e[2], e[1]))
    return out, (W, H)


def snapshot():
    """One lean read → elements, screen size, scroll position, keyboard flag."""
    root = tree()
    els, (W, H) = screen_elements(root)
    kb = False

    def walk(n):
        nonlocal kb
        if n.get("type") == "Keyboard":
            r = n.get("rect") or {}
            kb = kb or (r.get("height", 0) > 0 and r.get("y", H) < H)
        for c in n.get("children") or []:
            walk(c)

    walk(root)
    return {"elements": els, "size": (W, H), "scroll": scroll_pos(root), "keyboard": kb}


def scroll_pos(root):
    pos = None

    def walk(n):
        nonlocal pos
        lab = n.get("label") or ""
        if "scroll bar" in lab and isinstance(n.get("value"), str):
            m = re.match(r"(\d+)%", n["value"])
            if m:
                pos = int(m.group(1))
        for c in n.get("children") or []:
            walk(c)

    walk(root)
    return pos


def print_screen(elements):
    for t, cx, cy, lab, _, covered in elements:
        print(f"{t}\t{cx},{cy}\t{lab[:200]}" + ("\t(covered — scroll first)" if covered else ""))


def signature(elements):
    # notification banners come and go on their own; they are not "the screen"
    return tuple((lab, cx // 4, cy // 4) for t, cx, cy, lab, _, _ in elements
                 if not lab.startswith("Notification") and t != "Notification")


def wait_active(bundle, timeout=6.0):
    """Wait until `bundle` is the foreground app. Reading the screen while the
    previous app is still going to the background can block WDA for 60 s
    (accessibility IPC timeout on the suspending app, seen 2026-09-30)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if value(http("GET", "/wda/activeAppInfo", timeout=5)).get("bundleId") == bundle:
                time.sleep(0.3)          # let the switch animation finish
                return True
        except WdaError:
            pass
        time.sleep(0.2)
    return False


def settle_snapshot(max_wait=8.0):
    """Like settle(), returning the full snapshot of the settled screen."""
    t0, prev = time.time(), None
    while True:
        snap = snapshot()
        sig = signature(snap["elements"])
        if sig == prev or time.time() - t0 > max_wait:
            return snap
        prev = sig
        time.sleep(0.2)


def settle(max_wait=8.0):
    """Read the screen until two consecutive reads agree (UI settled)."""
    t0 = time.time()
    prev = None
    while True:
        els, _ = screen_elements()
        sig = signature(els)
        if sig == prev or time.time() - t0 > max_wait:
            return els
        prev = sig
        time.sleep(0.2)


# ---------------------------------------------------------------- screenshots

def mjpeg_frame(timeout=5):
    host = re.sub(r"^https?://", "", WDA).split("/")[0].rsplit(":", 1)[0].strip("[]") or "127.0.0.1"
    s = socket.create_connection((host, MJPEG_PORT), timeout=timeout)
    try:
        s.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        buf, t0 = b"", time.time()
        while time.time() - t0 < timeout:
            d = s.recv(65536)
            if not d:
                break
            buf += d
            a = buf.find(b"\xff\xd8")
            b = buf.find(b"\xff\xd9", a + 2) if a >= 0 else -1
            if a >= 0 and b > 0:
                return buf[a:b + 2]
    finally:
        s.close()
    raise WdaError("no MJPEG frame")


def screenshot(path, maxpx=None, full=False):
    """JPEG from the MJPEG stream (half scale) unless --full or it fails, then
    /screenshot (full-res PNG). Converted to the extension the caller asked for."""
    data, fmt = None, None
    if not full:
        try:
            data, fmt = mjpeg_frame(), "jpeg"
        except (OSError, WdaError):
            data = None
    if data is None:
        import base64
        data, fmt = base64.b64decode(value(http("GET", "/screenshot", timeout=30))), "png"
    want = "png" if path.lower().endswith(".png") else "jpeg"
    tmp = path if want == fmt else path + "." + fmt
    with open(tmp, "wb") as f:
        f.write(data)
    if tmp != path or maxpx:
        cmd = ["sips", "-s", "format", want, tmp, "--out", path]
        if maxpx:
            cmd[1:1] = ["-Z", str(maxpx)]
        subprocess.run(cmd, capture_output=True)
        if tmp != path:
            os.remove(tmp)
    return path


# ---------------------------------------------------------------- actions

def pointer(actions):
    session_call("POST", "/actions", {"actions": [{
        "type": "pointer", "id": "f1", "parameters": {"pointerType": "touch"},
        "actions": actions}]})


def tap_xy(x, y):
    pointer([{"type": "pointerMove", "duration": 0, "x": int(x), "y": int(y)},
             {"type": "pointerDown", "button": 0}, {"type": "pause", "duration": 80},
             {"type": "pointerUp", "button": 0}])


def drag(x1, y1, x2, y2, ms=400):
    pointer([{"type": "pointerMove", "duration": 0, "x": int(x1), "y": int(y1)},
             {"type": "pointerDown", "button": 0}, {"type": "pause", "duration": 100},
             {"type": "pointerMove", "duration": int(ms), "x": int(x2), "y": int(y2)},
             {"type": "pointerUp", "button": 0}])


def find(text, nth=1, elements=None):
    """Element whose label equals text (case-insensitive), else one whose label
    contains it; tappable types first, top to bottom."""
    els = elements if elements is not None else screen_elements()[0]
    t = text.lower()
    exact = [e for e in els if e[3].lower() == t]
    part = [e for e in els if t in e[3].lower() and e not in exact]
    rank = lambda e: (e[5], TAPPABLE.index(e[0]) if e[0] in TAPPABLE else len(TAPPABLE))
    cands = sorted(exact, key=rank) + sorted(part, key=rank)
    # the same control often appears twice (cell + its label): keep the first per position
    uniq, pos = [], set()
    for e in cands:
        k = (e[1] // 10, e[2] // 10)
        if k not in pos:
            pos.add(k)
            uniq.append(e)
    if len(uniq) < nth:
        return None, uniq
    return uniq[nth - 1], uniq


def reveal(el, label, nth=1):
    """If el sits under an overlay, scroll it towards the middle and return it
    as found again on the settled screen (None if it stays covered)."""
    if not el[5]:
        return el
    _, (W, H) = screen_elements()
    dy = max(-H * 0.4, min(H * 0.4, el[2] - H * 0.45))
    drag(W / 2, H * 0.6, W / 2, H * 0.6 - dy, 500)
    found, _ = find(label, nth, settle())
    return found if found is not None and not found[5] else None


def installed_apps(refresh=False):
    """[(bundleId, name)] of user apps via go-ios, cached in var/apps-cache.json."""
    if not refresh and os.path.exists(APPS_CACHE):
        with open(APPS_CACHE) as f:
            return [tuple(x) for x in json.load(f)]
    if not IOS_BIN:
        raise WdaError("IOS_BIN not set")
    out = subprocess.run([IOS_BIN, "apps", "--list"], capture_output=True, text=True).stdout
    apps = []
    for line in out.splitlines():
        parts = line.strip().split(" ")
        if len(parts) >= 2 and "." in parts[0] and not line.startswith("{"):
            name = " ".join(parts[1:-1]) if len(parts) > 2 else parts[1]
            apps.append((parts[0], name))
    if apps:
        with open(APPS_CACHE, "w") as f:
            json.dump(apps, f)
    return apps


SYSTEM_APPS = {
    "settings": "com.apple.Preferences", "photos": "com.apple.mobileslideshow",
    "camera": "com.apple.camera", "safari": "com.apple.mobilesafari", "messages": "com.apple.MobileSMS",
    "mail": "com.apple.mobilemail", "phone": "com.apple.mobilephone", "app store": "com.apple.AppStore",
    "calendar": "com.apple.mobilecal", "notes": "com.apple.mobilenotes", "maps": "com.apple.Maps",
    "clock": "com.apple.mobiletimer", "files": "com.apple.DocumentsApp", "health": "com.apple.Health",
    "wallet": "com.apple.Passbook", "contacts": "com.apple.MobileAddressBook", "music": "com.apple.Music",
    "reminders": "com.apple.reminders", "weather": "com.apple.weather", "shortcuts": "com.apple.shortcuts",
}


def bundle_for(app):
    """Bundle id for an app name or bundle id (exact name, then substring)."""
    if re.fullmatch(r"[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+", app):
        return app
    key = app.lower().strip()
    if key in SYSTEM_APPS:
        return SYSTEM_APPS[key]
    for refresh in (False, True):
        apps = installed_apps(refresh)
        if refresh and not apps:
            raise WdaError("cannot list installed apps (phone not connected?) — pass a bundle id")
        exact = [b for b, n in apps if n.lower() == key]
        part = [b for b, n in apps if key in n.lower()]
        if exact or part:
            return (exact or part)[0]
    raise WdaError(f'no installed app named "{app}"')


def ocr(path):
    if not os.access(OCR_BIN, os.X_OK):
        die("no var/bin/ocr — run scripts/setup")
    return subprocess.run([OCR_BIN, path], capture_output=True, text=True).stdout


# ---------------------------------------------------------------- CLI

USAGE = """usage: wda <command> [args]   (iphone-agent must be running)
  status                         WDA ready + version, locked state
  session                        new session with fast settings
  texts                          visible labels: type, centre x,y (points), label
  tap <x> <y> [--read]           tap at points
  tap "<text>" [--nth N] [--read]  tap the element labelled <text> (exact, then contains)
  swipe <x1> <y1> <x2> <y2> [ms] [--read]
  scroll up|down [--read]        one page, no momentum
  keys "<text>" [--read]         type into the focused field
  launch <app name|bundleId> [--fresh] [--read]   --fresh: terminate first → app opens at its root
  back [--read]                  tap the navigation bar's back button (top-left)
  home                           press the Home button (go to the home screen)
  wait "<text>" [seconds]        until a label containing <text> is on screen (default 15 s)
  shot <file.jpg|png> [maxpx] [--full]   MJPEG frame (half scale); --full = /screenshot
  ocr                            screenshot + Apple Vision OCR (screens without labels)
  source <file.json>             full UI tree (slow)
  collect                        scroll the current list top→bottom, print every label once
  keep-awake [minutes]           press volume up+down every 60 s so the auto-lock
                                 doesn't fire (default: until interrupted); net-zero volume
--read: after the action, wait for the UI to settle and print the new screen."""


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0 if argv else 1
    cmd, args = argv[0], argv[1:]
    read = "--read" in args
    args = [a for a in args if a != "--read"]

    def after():
        if read:
            time.sleep(0.3)
            print_screen(settle())

    if cmd == "status":
        v = value(http("GET", "/status", timeout=5))
        locked = value(http("GET", "/wda/locked", timeout=5))
        print(("ready " if v.get("ready") else "not ready ") + v["build"]["version"],
              "(locked)" if locked else "")
    elif cmd == "session":
        print(new_session())
    elif cmd == "texts":
        print_screen(screen_elements()[0])
    elif cmd == "tap":
        nth = 1
        if "--nth" in args:
            i = args.index("--nth")
            nth = int(args[i + 1])
            del args[i:i + 2]
        if len(args) == 2 and all(re.fullmatch(r"-?\d+(\.\d+)?", a) for a in args):
            tap_xy(float(args[0]), float(args[1]))
        elif len(args) == 1:
            el, cands = find(args[0], nth)
            if el is None:
                die(f'no element labelled "{args[0]}" on screen'
                    + (f" ({len(cands)} match(es), asked for #{nth})" if cands else ""))
            if el[5]:   # under an overlay: scroll it clear first
                el = reveal(el, args[0], nth)
                if el is None:
                    die(f'"{args[0]}" is covered by a toolbar/keyboard and could not be revealed')
            tap_xy(el[1], el[2])
            print(f"tapped {el[0]} \"{el[3][:80]}\" at {el[1]},{el[2]}", file=sys.stderr)
        else:
            die('usage: wda tap <x> <y> | tap "<text>" [--nth N] [--read]')
        after()
    elif cmd == "swipe":
        if len(args) < 4:
            die("usage: wda swipe <x1> <y1> <x2> <y2> [ms] [--read]")
        drag(*map(float, args[:4]), ms=float(args[4]) if len(args) > 4 else 400)
        after()
    elif cmd == "scroll":
        _, (W, H) = screen_elements()
        if args[:1] == ["down"]:
            drag(W / 2, H * 0.75, W / 2, H * 0.25, 600)
        elif args[:1] == ["up"]:
            drag(W / 2, H * 0.25, W / 2, H * 0.75, 600)
        else:
            die("usage: wda scroll up|down [--read]")
        after()
    elif cmd == "keys":
        if not args:
            die('usage: wda keys "<text>"')
        session_call("POST", "/wda/keys", {"value": list(" ".join(args))})
        after()
    elif cmd == "launch":
        fresh = "--fresh" in args
        args = [a for a in args if a != "--fresh"]
        if len(args) != 1:
            die("usage: wda launch <bundleId> [--fresh] [--read]")
        bundle = bundle_for(args[0])       # app name or bundle id
        if fresh:   # a running app resumes where it was left; start it clean
            session_call("POST", "/wda/apps/terminate", {"bundleId": bundle})
        session_call("POST", "/wda/apps/launch", {"bundleId": bundle})
        wait_active(bundle)
        after()
    elif cmd == "back":
        els, (W, H) = screen_elements()
        nav = [e for e in els if e[0] == "Button" and e[1] < W * 0.3 and e[2] < 110]
        if not nav:
            die("no back button in the navigation bar")
        tap_xy(nav[0][1], nav[0][2])
        print(f'back: "{nav[0][3][:60]}"', file=sys.stderr)
        after()
    elif cmd == "home":
        value(http("POST", "/wda/homescreen", {}, 10))
        wait_active("com.apple.springboard")
    elif cmd == "wait":
        if not args:
            die('usage: wda wait "<text>" [seconds]')
        limit = float(args[1]) if len(args) > 1 else 15
        t0 = time.time()
        while True:
            el, _ = find(args[0])
            if el:
                print(f"{el[0]}\t{el[1]},{el[2]}\t{el[3][:200]}")
                break
            if time.time() - t0 > limit:
                die(f'"{args[0]}" not on screen after {limit:.0f} s', 2)
            time.sleep(0.3)
    elif cmd == "shot":
        if not args:
            die("usage: wda shot <file> [maxpx] [--full]")
        full = "--full" in args
        args = [a for a in args if a != "--full"]
        print(screenshot(args[0], int(args[1]) if len(args) > 1 else None, full))
    elif cmd == "ocr":
        p = os.path.join(VAR, "ocr-shot.png")
        screenshot(p, full=True)   # Vision needs full resolution
        sys.stdout.write(ocr(p))
    elif cmd == "source":
        if len(args) != 1:
            die("usage: wda source <file.json>")
        with open(args[0], "w") as f:
            json.dump(value(http("GET", "/source?format=json")), f)
        print(args[0])
    elif cmd == "collect":
        collect()
    elif cmd == "keep-awake":
        keep_awake(float(args[0]) if args else 0)
    else:
        print(USAGE, file=sys.stderr)
        return 1
    return 0


def keep_awake(minutes, log=lambda m: print(m, flush=True), stop=None):
    """Hardware-button presses reset the auto-lock timer; synthesized taps are
    believed not to (2026-08-14, not isolated). Verified 2026-09-30: ~9 min
    unlocked past a 5-min auto-lock policy with presses every 60 s."""
    end = time.time() + minutes * 60 if minutes else None
    while (end is None or time.time() < end) and not (stop and stop.is_set()):
        try:
            if not value(http("GET", "/wda/locked", timeout=5)):
                session_call("POST", "/wda/pressButton", {"name": "volumeUp"}, 5)
                session_call("POST", "/wda/pressButton", {"name": "volumeDown"}, 5)
                log(time.strftime("%H:%M:%S") + " pressed")
            else:
                log(time.strftime("%H:%M:%S") + " phone is locked — needs a human")
        except WdaError as e:
            log(time.strftime("%H:%M:%S") + f" WDA unavailable: {e}")
        if stop:
            stop.wait(60)
        else:
            time.sleep(60)


def collect():
    for label in collect_labels():
        print(label, flush=True)


def collect_labels(max_pages=300):
    """Scroll the current list to the top, then down one page at a time until
    the scroll bar says 100 % (or, with no scroll bar, the screen stops
    changing); print every label once."""
    root = tree()
    W, H = root["rect"]["width"], root["rect"]["height"]
    pos = scroll_pos(root)
    for _ in range(40):                                   # to the top
        if pos == 0:
            break
        before = signature(screen_elements(root)[0])
        drag(W / 2, H * 0.25, W / 2, H * 0.85, 150)
        root = tree()
        pos = scroll_pos(root)
        if pos is None and signature(screen_elements(root)[0]) == before:
            break                                         # no scroll bar and nothing moved
    seen, prev, still = set(), None, 0
    for _ in range(max_pages):                            # down, a page per drag
        els = screen_elements(root)[0]
        for e in els:
            if e[3] not in seen:
                seen.add(e[3])
                yield e[3]
        sig = signature(els)
        if pos is not None and pos >= 100:
            break
        # unchanged twice in a row = the end; once may be a swallowed drag
        # (e.g. while a notification banner is up)
        still = still + 1 if sig == prev else 0
        if still >= 2:
            break
        prev = sig
        drag(W / 2, H * 0.80, W / 2, H * 0.20, 900)       # slow: no momentum, ~60 % overlap-free page
        root = tree()
        pos = scroll_pos(root)


if __name__ == "__main__":
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)   # `wda texts | head` must not traceback
    try:
        sys.exit(main(sys.argv[1:]))
    except WdaError as e:
        die(str(e))
