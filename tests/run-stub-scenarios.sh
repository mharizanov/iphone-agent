#!/bin/bash
# Offline supervisor test: runs bin/iphone-agent against tests/stub/ios (a fake
# go-ios: python listeners stand in for the tunnel and for WDA on :8100) and
# injects failures. Takes ~4.5 min. Needs localhost:8100 and :60199 free, so
# stop the real iphone-agent first.
#
# Expected (verified 2026-09-30):
#   T1 WDA exit          → "restarting WDA only" within ~1 s
#   T3 hang              → detected in ~35 s, WDA-only restart
#   T4 tunnel death      → full restart in 5 s
#   T2 persistent zombie → WDA-only restart, then full restart on the 2nd
#                          death within 60 s
#   T5 SIGTERM           → "stopping..." at once, no leftover processes

HERE="$(cd "$(dirname "$0")" && pwd)"
W="$(mktemp -d "${TMPDIR:-/tmp}/iphone-agent-test.XXXXXX")"
OUT="$W/out.log"

if lsof -nP -iTCP:8100 -sTCP:LISTEN >/dev/null 2>&1 || pgrep -f '^([^ ]+ )?([^ ]*/)?ios (tunnel|forward|runwda)' >/dev/null; then
  echo "localhost:8100 or go-ios in use — stop iphone-agent first" >&2
  exit 1
fi

# fake go-ios, throwaway state dir, shorter authorization-probe interval
export IOS_BIN="$HERE/stub/ios" VAR_DIR="$W/var" IPHONE_UDID="00000000-STUB" AUTH_EVERY=10 \
  STUBLOG="$W/stub.log" WDAMODE="$W/mode"
mark() { echo "### $(date +%T) $*" >>"$OUT"; }

"$HERE/../bin/iphone-agent" >>"$OUT" 2>&1 &
A=$!
sleep 70; mark "T1 kill runwda after 70 s healthy"; pkill -f "sleep 99999"
sleep 15; mark "T3 hang (40 s) on a running WDA"; echo hang >"$WDAMODE"
sleep 40; echo ok >"$WDAMODE"; mark "hang cleared"
sleep 70; mark "T4 kill tunnel after >60 s healthy"; pkill -f "60199"
sleep 15; mark "T2 persistent zombie (50 s)"; echo zombie >"$WDAMODE"
sleep 50; echo ok >"$WDAMODE"; mark "zombie cleared"
sleep 10; mark "T5 SIGTERM"; kill -TERM "$A"; sleep 2
mark "agent alive after 2 s? $(kill -0 "$A" 2>/dev/null && echo YES || echo no)"
mark "leftover: $(pgrep -f '60199|sleep 99999|serve_forever' | tr '\n' ' ')"
pkill -f 60199; pkill -f "sleep 99999"; pkill -f serve_forever

cat "$OUT"
echo "work dir: $W"
