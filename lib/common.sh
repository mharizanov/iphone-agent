# Shared setup for all iphone-agent scripts. Source it; do not execute.
# Resolves the repo root (through symlinks such as /usr/local/bin/iphone-agent),
# loads config/iphone.env and points every tool at repo-local state in var/.

_src="${BASH_SOURCE[1]:-$0}"
while [ -L "$_src" ]; do
  _dir="$(cd -P "$(dirname "$_src")" && pwd)"
  _src="$(readlink "$_src")"
  case "$_src" in /*) ;; *) _src="$_dir/$_src" ;; esac
done
ROOT="$(cd -P "$(dirname "$_src")/.." && pwd)"
unset _src _dir

# shellcheck source=../config/iphone.env
# your own values first (git-ignored), then the generic defaults
[ -f "$ROOT/config/local.env" ] && . "$ROOT/config/local.env"
. "$ROOT/config/iphone.env"

VAR_DIR="${VAR_DIR:-$ROOT/var}"
LOG_DIR="$VAR_DIR/log"
DDI_DIR="$VAR_DIR/devimages"
WDA_SRC="${WDA_SRC:-$VAR_DIR/WebDriverAgent}"
WDA_BUNDLE_ID="$WDA_BUNDLE_PREFIX.xctrunner"
WDA_URL="${WDA_URL:-http://localhost:$WDA_PORT}"

# go-ios: explicit IOS_BIN, else the pinned copy from scripts/setup, else PATH
if [ -z "${IOS_BIN:-}" ]; then
  if [ -x "$VAR_DIR/bin/ios" ]; then
    IOS_BIN="$VAR_DIR/bin/ios"
  else
    IOS_BIN="$(command -v ios 2>/dev/null)"
  fi
fi
# Call "$IOS_BIN" directly, never through a shell function: a backgrounded
# function runs in a subshell, so $! would not be the go-ios PID.
require_ios() {
  [ -n "$IOS_BIN" ] && [ -x "$IOS_BIN" ] && return 0
  echo "go-ios not found — run scripts/setup" >&2
  exit 127
}

# go-ios processes we own, matched from the start of argv (optionally behind an
# interpreter, for the test stub) — a bare "ios tunnel" pattern also matched
# any shell/editor whose arguments merely contained that text
GOIOS_RE='^([^ ]+ )?([^ ]*/)?ios (tunnel|forward|runwda)'
GOIOS_RUNWDA_RE='^([^ ]+ )?([^ ]*/)?ios runwda'

# fills IPHONE_UDID when it is empty and exactly one device is attached
resolve_udid() {
  [ -n "$IPHONE_UDID" ] && return 0
  local ids
  ids=$("$IOS_BIN" list 2>/dev/null | python3 -c 'import json,sys; print("\n".join(sorted(set(json.load(sys.stdin)["deviceList"]))))' 2>/dev/null)
  case "$(printf '%s' "$ids" | grep -c .)" in
    1) IPHONE_UDID="$ids" ;;
    0) echo "no iPhone on USB" >&2; return 1 ;;
    *) echo "several iPhones attached — set IPHONE_UDID in config/iphone.env:" >&2
       echo "$ids" >&2; return 1 ;;
  esac
}

mkdir -p "$LOG_DIR"
