#!/usr/bin/env bash
set -euo pipefail

# history_scan.sh
# Run in a local clone of the repository to list large historical objects.

OUTFILE="large_objects.txt"
THRESHOLD_BYTES=$((5 * 1024 * 1024)) # 5 MB

echo "Scanning repo history for objects larger than $THRESHOLD_BYTES bytes..."
git rev-list --objects --all \
  | git cat-file --batch-check='%(objectname) %(objecttype) %(objectsize) %(rest)' \
  | awk -v thresh=$THRESHOLD_BYTES '$3 >= thresh {print $0}' \
  | sort -k3 -n -r > "$OUTFILE"

echo "Saved large objects to $OUTFILE"
echo "To map objecthash -> paths, run:"
echo "  git rev-list --objects --all | grep <objecthash>"

git rev-list --objects --all \
  | git cat-file --batch-check='%(objectname) %(objecttype) %(objectsize) %(rest)' \
  | sort -k3 -n -r \
  | head -n 200 > "top_200_objects.txt"
echo "Top 200 objects -> top_200_objects.txt"
