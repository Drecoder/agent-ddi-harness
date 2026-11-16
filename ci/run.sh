#!/usr/bin/env bash
# Run a command inside the pinned image: the checkout read-only at /work, no network, as the
# runner's own (non-root) uid so files written to /out are the runner's.
#   ci/run.sh [--in DIR] [--out DIR] [--workdir DIR] -- COMMAND...
set -euo pipefail
in="" ; out="" ; wd=/work
while [ $# -gt 0 ]; do
  case "$1" in
    --in) in=$2; shift 2 ;;
    --out) out=$2; shift 2 ;;
    --workdir) wd=$2; shift 2 ;;
    --) shift; break ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
args=(--rm --network none --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD:/work:ro" -w "$wd")
if [ -n "$in" ]; then
  args+=(-v "$in:/in:ro")
fi
if [ -n "$out" ]; then
  mkdir -p "$out"
  args+=(-v "$out:/out")
fi
exec docker run "${args[@]}" ddi-ci "$@"
