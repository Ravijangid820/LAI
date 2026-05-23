#!/usr/bin/env bash
# Self-contained TESTING_GUIDE sweep. Runs detached (survives SSH
# disconnect — it's a background process on the host, not tied to any
# terminal). Writes graded results to harsh/TEST_SWEEP_RESULTS.md.
set -uo pipefail
cd /data/projects/lai/LAI
PDFDIR=/data/projects/lai/harsh/testing_pdf
OUT=/data/projects/lai/harsh/TEST_SWEEP_RESULTS.md
BASE=http://127.0.0.1:18000

# Fresh token (valid for the access-token TTL; sweep is well under it).
TOKEN=$(docker exec lai-backend python -c "import uuid; from auth_dep import _auth_config; from lai.common.auth import TokenIssuer; print(TokenIssuer(_auth_config).issue_access_token(user_id=uuid.UUID('00000000-0000-0000-0000-000000000001'),email='legacy@lai.local',role='user')[0])" 2>/dev/null | tail -1)

echo "# TESTING_GUIDE sweep — $(date -u '+%Y-%m-%d %H:%M:%S UTC')" > "$OUT"
echo "" >> "$OUT"
[ -z "$TOKEN" ] && { echo "FATAL: no token" >> "$OUT"; exit 1; }

upload () {  # upload PATH → echoes session_id (or empty on failure)
  curl -s -m600 "$BASE/upload" -H "Authorization: Bearer $TOKEN" -F "file=@$1" \
    | python3 -c "import sys,json
try:
  d=json.load(sys.stdin); print(d.get('session_id',''))
except Exception: print('')" 2>/dev/null
}

ask () {  # ask LABEL QUESTION SESSION
  local label="$1" q="$2" sid="$3"
  echo "" >> "$OUT"; echo "**[$label]** $q" >> "$OUT"
  local payload
  payload=$(python3 -c "import json,sys;print(json.dumps({'question':sys.argv[1],'session_id':sys.argv[2]} if sys.argv[2] else {'question':sys.argv[1]}))" "$q" "$sid")
  curl -s -m240 "$BASE/query" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$payload" \
  | python3 -c "
import sys,re,json
try:
  d=json.load(sys.stdin); a=d.get('answer') or '(none)'
  jw=len(d.get('jurisdiction_warnings') or [])
  cv=d.get('citation_validation') or {}; fab=cv.get('fabricated') if isinstance(cv,dict) else '?'
  print('- A: '+a[:420].replace(chr(10),' '))
  print('- [C-n: %d | M-n: %d | jur_warn: %d | fabricated: %s]' % (len(re.findall(r'\[C-\d+\]',a)),len(re.findall(r'\[M-\d+\]',a)),jw,fab))
except Exception as e: print('- ERR: %s' % e)
" >> "$OUT" 2>&1
}

declare -A PDFS=(
  [01_permit]="01_Aenderungsgenehmigung_BImSchG_10von11_2007.pdf"
  [02_ovg]="02_OVG_Niedersachsen_Urteil_Rueckbau_2017.pdf"
  [03_oem]="03_Enercon_Wartungsvertrag_2019.pdf"
  [04_loan]="04_VRB_Darlehensvertrag_6Mio_2019.pdf"
  [05_grid]="05_EWE_Netzanschlussvertrag_2008.pdf"
)

run_pdf () {  # run_pdf KEY "happyQ" "edgeLabel" "edgeQ"
  local key="$1" hq="$2" el="$3" eq="$4"
  local f="$PDFDIR/${PDFS[$key]}"
  echo "" >> "$OUT"; echo "## $key — $(basename "$f")" >> "$OUT"
  local sz; sz=$(stat -c%s "$f" 2>/dev/null)
  echo "_upload (${sz} bytes)..._" >> "$OUT"
  local sid; sid=$(upload "$f")
  if [ -z "$sid" ]; then
    echo "- ❌ UPLOAD FAILED (no session_id) — queries skipped for this PDF" >> "$OUT"
    return
  fi
  echo "- ✅ session $sid" >> "$OUT"
  ask "A happy-path" "$hq" "$sid"
  ask "$el" "$eq" "$sid"
}

run_pdf 01_permit "Welcher Genehmigungsstatus, Anlagentyp und wie viele WEA ergeben sich aus diesem Bescheid?" "C jurisdiction-trap 🟠" "Gilt für dieses Projekt die 10H-Abstandsregelung?"
run_pdf 02_ovg "Worum ging es in diesem Urteil des OVG Niedersachsen und wie wurde zur Rückbauverpflichtung entschieden?" "D hallucination-bait" "Wie lautet das genaue Aktenzeichen und Verkündungsdatum dieses Urteils?"
run_pdf 03_oem "Welche Verfügbarkeitsgarantie, Laufzeit und Pönalen sieht dieser Wartungsvertrag vor?" "C hallucination-bait" "Wie hoch ist die jährliche Wartungspauschale in Euro?"
run_pdf 04_loan "Welche Darlehenssumme, welcher Zinssatz und welche Sicherheiten sind vereinbart?" "C hallucination-bait" "Nenne die genaue IBAN des Darlehenskontos."
run_pdf 05_grid "Wer ist der Netzbetreiber und welche Anschlussleistung ist vereinbart?" "B statutory-grounding" "Welcher Anspruch auf Netzanschluss besteht nach EEG für Windenergieanlagen?"

# Cross-PDF G — chat-mode (no doc, expect no chips, fast)
echo "" >> "$OUT"; echo "## G — chat-mode (no document)" >> "$OUT"
ask "G smalltalk" "Hallo, was kannst du?" ""

echo "" >> "$OUT"; echo "_sweep complete — $(date -u '+%H:%M:%S UTC')_" >> "$OUT"
