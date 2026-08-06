#!/usr/bin/env bash
set -euo pipefail

# remove_tracked_sample_ttsmultimodel.sh
# Per project choice A, this script will NOT automatically remove personas from the index.
# Use the commented commands below if you decide to remove tracked persona files non-destructively.

cat <<'EOF'
# Example commands to remove specific personas from index (uncomment to use):
# git checkout -b clean/remove-personas
# git rm --cached personas/gf1.pt personas/旁白.pt personas/李老师.pt || true
# echo "personas/*.pt" >> .gitignore
# git add .gitignore
# git commit -m "chore: remove personas .pt from index and add to .gitignore"
# git push origin HEAD
EOF


echo "Per choice A, personas/*.pt are NOT removed by this branch. See CLEANUP_REPORT.md for recommendations."
