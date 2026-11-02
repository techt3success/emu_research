#!/usr/bin/env bash
# build_selfheal.sh — run `m` with automatic stub-generation for undefined modules.
# Called from the workflow build step; expects cwd=$HOME/aosp and lunch done.
# Loop: m -> harvest "depends on undefined module" from out/error.log ->
#       generate empty stub defaults -> retry (3 rounds).
set -o pipefail

ROUND_MAX=30
STUBS="$HOME/aosp/fortress_stubs"
mkdir -p "$STUBS"
if [ ! -s "$STUBS/Android.bp" ]; then
  printf '// auto-generated stubs for release-config modules absent in Lineage\n' > "$STUBS/Android.bp"
fi

BUILD_RC=1
round=1
while [ "$round" -le "$ROUND_MAX" ]; do
  echo "=== ninja round $round/${ROUND_MAX} ==="
  m -j4 > build.log 2>&1
  BUILD_RC=$?
  if [ "$BUILD_RC" -eq 0 ]; then
    echo "=== BUILD COMPLETE (round $round) ==="
    break
  fi

  # harvest undefined-module names from soong's error log
  UNDEFS=$(grep -hoE 'depends on undefined module "[^"]+"' out/error.log 2>/dev/null \
           | sed 's/.*"\(.*\)"/\1/' | sort -u)
  if [ -z "$UNDEFS" ]; then
    echo "no undefined-module errors — real compile failure, stopping loop"
    tail -40 build.log
    break
  fi

  NEW=0
  while IFS= read -r M; do
    [ -z "$M" ] && continue
    grep -q "name: \"$M\"" "$STUBS/Android.bp" && continue
    case "$M" in
      *rust|*_rust)         KIND="rust_defaults" ;;
      *java*|*Test*|*tests*) KIND="java_defaults" ;;
      *)                    KIND="cc_defaults" ;;
    esac
    printf '%s { name: "%s" }\n' "$KIND" "$M" >> "$STUBS/Android.bp"
    echo "stub added: $KIND $M"
    NEW=1
  done <<< "$UNDEFS"

  if [ "$NEW" -eq 0 ]; then
    echo "no new stubs generated — stopping loop"
    tail -40 build.log
    break
  fi
  round=$((round+1))
done

echo "BUILD_RC=$BUILD_RC"
exit "$BUILD_RC"
