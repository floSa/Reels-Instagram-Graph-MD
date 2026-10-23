#!/usr/bin/env bash
# Récolte hebdomadaire des URL de reels enregistrés et de leurs collections.
# Lancé par la tâche planifiée Windows « Reels-Urls-Hebdo » (voir README).
set -u
cd "$(dirname "$0")/.."
DOSSIER="${REELS_URLS_DOSSIER:-/mnt/c/Users/FLORIAN/Documents/Enregistres-Instagram}"
{ echo "=== $(date '+%F %T')"; "$HOME/.local/bin/uv" run reels-urls --dossier "$DOSSIER"; echo "code $?"; } >> "$DOSSIER/recolte.log" 2>&1
