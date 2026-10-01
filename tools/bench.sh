#!/bin/bash
# bench — time the screen-read and action primitives on the CURRENT screen.
# usage: tools/bench.sh [label]   (iphone-agent running, phone unlocked)
R="$(cd "$(dirname "$0")/.." && pwd)"; W=http://localhost:8100; T=$(mktemp -d)
LEAN="visible,accessible,enabled,focused,frame,nativeFrame,traits,placeholderValue,nativeAccessibilityElement"
t() { local s e; s=$(python3 -c 'import time;print(time.time())'); "$@" >/dev/null 2>&1; e=$(python3 -c 'import time;print(time.time())'); python3 -c "print(f'{$e-$s:5.2f}')"; }
row() { local name=$1; shift; printf '%-34s' "$name"; for i in 1 2 3; do printf ' %s' "$(t "$@")"; done; echo; }
echo "== ${1:-screen}: seconds per run (3 runs)"
row "source json (full)"              curl -s --max-time 60 -o $T/full.json "$W/source?format=json"
row "source json (lean attrs)"        curl -s --max-time 60 -o $T/lean.json "$W/source?format=json&excluded_attributes=$LEAN"
row "screenshot"                      curl -s --max-time 30 -o $T/shot.json "$W/screenshot"
python3 -c 'import json,base64,sys;open(sys.argv[2],"wb").write(base64.b64decode(json.load(open(sys.argv[1]))["value"]))' $T/shot.json $T/shot.png
row "  + OCR on Mac (Vision)"         "$R/var/bin/ocr" $T/shot.png
printf 'sizes: full %s KB, lean %s KB, screenshot %s KB; OCR lines: %s; tree labels: %s\n' \
  $(( $(wc -c <$T/full.json)/1024 )) $(( $(wc -c <$T/lean.json)/1024 )) $(( $(wc -c <$T/shot.json)/1024 )) \
  "$("$R/var/bin/ocr" $T/shot.png | wc -l | tr -d ' ')" \
  "$(python3 -c '
import json,sys
n=0
def w(x):
    global n
    if (x.get("label") or "").strip(): n+=1
    for c in x.get("children") or []: w(c)
w(json.load(open(sys.argv[1]))["value"]); print(n)' $T/full.json)"
rm -rf $T
