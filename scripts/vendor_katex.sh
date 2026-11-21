#!/usr/bin/env sh
set -eu

VERSION="0.19.1"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
DESTINATION="${1:-$ROOT/app/vendor/katex}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT HUP INT TERM

npm pack --ignore-scripts --silent --pack-destination "$WORK" "katex@$VERSION" >/dev/null
ARCHIVE="$WORK/katex-$VERSION.tgz"
test -s "$ARCHIVE"
mkdir -p "$WORK/package" "$DESTINATION"
tar -xzf "$ARCHIVE" -C "$WORK/package" --strip-components=1

test -s "$WORK/package/dist/katex.min.js"
test -s "$WORK/package/dist/katex-swap.min.css"
test -d "$WORK/package/dist/fonts"
rm -rf "$DESTINATION/fonts"
mkdir -p "$DESTINATION/fonts"
cp "$WORK/package/dist/katex.min.js" "$DESTINATION/katex.min.js"
cp "$WORK/package/dist/katex-swap.min.css" "$DESTINATION/katex.min.css"
cp -R "$WORK/package/dist/fonts/." "$DESTINATION/fonts/"
cp "$WORK/package/LICENSE" "$DESTINATION/LICENSE"
printf '%s\n' "$VERSION" > "$DESTINATION/VERSION"
printf 'Installed KaTeX %s into %s\n' "$VERSION" "$DESTINATION"
