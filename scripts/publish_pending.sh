#!/usr/bin/env bash
# One-shot: publish the ESPN + tennis listings once Apify's daily publication limit
# (5 per 24 h) resets. Safe to re-run: exits 0 once both are public.
set -uo pipefail
export PATH="$HOME/.nvm/versions/node/v22.22.0/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
T="$(apify auth token 2>/dev/null)"
LOG="$(dirname "$0")/../../spec/publish_pending.log"
ok=0
for id in kspHEgiVbbygCTYgm 5yuG1HwxAE6evpjMW; do
  r=$(curl -s -X PUT -H "Authorization: Bearer $T" -H "Content-Type: application/json" \
       -d '{"isPublic":true}' "https://api.apify.com/v2/acts/$id")
  echo "$(date -u +%FT%TZ) $id $(echo "$r" | head -c 200)" >> "$LOG"
  # Herestring, not a pipe: `grep -q` exits on the first match and SIGPIPEs the
  # writer, which `set -o pipefail` then reports as a failed pipeline (the reason
  # this check reported "not yet" on runs that had actually published).
  grep -q '"isPublic": *true' <<<"$r" && ok=$((ok+1))
done
[ "$ok" -eq 2 ] && echo "both public" && exit 0
echo "not yet ($ok/2) — see $LOG"; exit 1
