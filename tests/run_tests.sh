#!/usr/bin/env bash
# Run every test. One line per file, a summary at the end, exit 1 if anything
# failed -- so it can be the last thing you do before building push.zip.
#
#   ./run_tests.sh              all of them, 4 at a time
#   ./run_tests.sh -j1          one at a time (each test's own output, in order)
#   ./run_tests.sh sport sample just the files whose name contains these
#   ./run_tests.sh -v pdf       show the failing lines for what you selected
#
# Each test starts its own copy of the app on its own port, so they can run in
# parallel -- but only up to a point: they are real browsers. -j4 is about
# right on a laptop.

set -uo pipefail
cd "$(dirname "$0")"

JOBS=4
VERBOSE=0
PATTERNS=()
while [ $# -gt 0 ]; do
  case "$1" in
    -j*) JOBS="${1#-j}"; [ -n "$JOBS" ] || { shift; JOBS="$1"; } ;;
    -v)  VERBOSE=1 ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)   PATTERNS+=("$1") ;;
  esac
  shift
done

FILES=()
for f in test_*.py; do
  if [ ${#PATTERNS[@]} -eq 0 ]; then
    FILES+=("$f")
  else
    for p in "${PATTERNS[@]}"; do
      case "$f" in *"$p"*) FILES+=("$f"); break ;; esac
    done
  fi
done

if [ ${#FILES[@]} -eq 0 ]; then
  echo "no test files matched: ${PATTERNS[*]}"; exit 1
fi

OUT=$(mktemp -d)
trap 'rm -rf "$OUT"' EXIT
START=$(date +%s)

run_one() {
  local f="$1"
  local log="$OUT/$f.log"
  local t0 t1
  t0=$(date +%s)
  timeout 600 python3 "$f" > "$log" 2>&1
  local rc=$?
  t1=$(date +%s)
  # every test file prints "ALL PASS", "ALL PASSED" or "<n> FAILED"
  local n
  n=$(grep -c "  PASS  " "$log")
  if [ $rc -eq 0 ]; then
    printf "  ok    %-26s %3s checks  %ss\n" "$f" "$n" "$((t1 - t0))"
  elif [ $rc -eq 124 ]; then
    printf "  TIME  %-26s timed out after 600s\n" "$f"
  else
    printf "  FAIL  %-26s %s\n" "$f" \
      "$(grep -c '  FAIL  ' "$log") failed of $((n + $(grep -c '  FAIL  ' "$log")))"
    grep "  FAIL  " "$log" | sed 's/^/          /'
  fi
  echo "$rc" > "$OUT/$f.rc"
}

echo "running ${#FILES[@]} test files, $JOBS at a time"
echo
i=0
for f in "${FILES[@]}"; do
  run_one "$f" &
  i=$((i + 1))
  if [ $((i % JOBS)) -eq 0 ]; then wait; fi
done
wait

FAILED=()
CHECKS=0
for f in "${FILES[@]}"; do
  CHECKS=$((CHECKS + $(grep -c "  PASS  " "$OUT/$f.log")))
  [ "$(cat "$OUT/$f.rc" 2>/dev/null || echo 1)" = "0" ] || FAILED+=("$f")
done

echo
echo "----------------------------------------------------------"
printf "%d files, %d checks passed, %ds\n" \
  "${#FILES[@]}" "$CHECKS" "$(( $(date +%s) - START ))"
if [ ${#FAILED[@]} -gt 0 ]; then
  echo "FAILED: ${FAILED[*]}"
  if [ "$VERBOSE" = "1" ]; then
    for f in "${FAILED[@]}"; do
      echo; echo "===== $f ====="; cat "$OUT/$f.log"
    done
  else
    echo "(run './run_tests.sh -v ${FAILED[0]%.py}' to see one in full)"
  fi
  exit 1
fi
echo "ALL PASS"
