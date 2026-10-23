"""Récolte hebdomadaire : l'adresse de chaque reel enregistré et sa collection.

    uv run reels-urls --dossier ~/Documents/Enregistres-Instagram

Rien n'est téléchargé ni transcrit : cette commande ne fait que tenir à jour la
liste des enregistrements, avec les collections où ils sont rangés. C'est elle que
la tâche hebdomadaire lance. Les vidéos, c'est `reels-sync`, séparément.

Écrit dans le dossier :

- `enregistres.json` : tout, source de vérité (url -> collections, auteur, légende, vu_le) ;
- `enregistres.csv`  : la même chose lisible dans un tableur ;
- `nouveaux.md`      : ce que la dernière récolte a trouvé de nouveau ;
- `collections.json` : nom de chaque collection (`{"1314…": "politique"}`), à remplir une fois.

Incrémental : la lecture s'arrête à la première page sans nouveauté, donc une récolte
hebdomadaire coûte une requête. Sur un 429, ce qui a été lu est conservé.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

from . import live
from .statut import lire_json
from .sync import session_instagram


def noms_de_collections(dossier: Path) -> dict[str, str]:
    return lire_json(dossier / "collections.json")


def recolter(dossier: Path, *, tout: bool = False, aujourdhui: str | None = None, lire=None) -> tuple[list[str], str | None]:
    """Met à jour les fichiers du dossier. Retourne (URLs nouvelles, message d'erreur éventuel)."""
    aujourdhui = aujourdhui or date.today().isoformat()
    dossier.mkdir(parents=True, exist_ok=True)
    magasin = lire_json(dossier / "enregistres.json")

    erreur = None
    try:
        lus = (lire or live.lire_enregistres)(session_instagram(), connus=None if tout else set(magasin))
    except live.ErreurLive as exc:
        lus, erreur = exc.partiels, str(exc)

    nouveaux = []
    for e in lus:
        if e["url"] not in magasin:
            nouveaux.append(e["url"])
            magasin[e["url"]] = {"vu_le": aujourdhui}
        magasin[e["url"]].update({k: e[k] for k in ("collections", "auteur", "legende")})

    (dossier / "enregistres.json").write_text(json.dumps(magasin, ensure_ascii=False, indent=1), encoding="utf-8")
    noms = noms_de_collections(dossier)
    with (dossier / "enregistres.csv").open("w", newline="", encoding="utf-8-sig") as f:
        ecrivain = csv.writer(f, delimiter=";")
        ecrivain.writerow(["url", "collections", "noms", "auteur", "vu_le", "legende"])
        for url, e in magasin.items():
            ids = e.get("collections", [])
            ecrivain.writerow([url, " ".join(ids), " + ".join(noms.get(i, "?") for i in ids), e.get("auteur", ""), e.get("vu_le", ""), e.get("legende", "").replace("\n", " ")])

    lignes = [f"# Nouveaux enregistrements — {aujourdhui}", "", f"{len(nouveaux)} nouveau(x), {len(magasin)} au total.", ""]
    for url in nouveaux:
        e = magasin[url]
        etiquettes = ", ".join(noms.get(i, i) for i in e.get("collections", [])) or "hors collection"
        lignes.append(f"- [{etiquettes}] {url} — {e.get('auteur', '')}")
    (dossier / "nouveaux.md").write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return nouveaux, erreur


def main() -> int:
    parseur = argparse.ArgumentParser(prog="reels-urls", description="Récolte les URL des reels enregistrés avec leurs collections.")
    parseur.add_argument("--dossier", required=True)
    parseur.add_argument("--tout", action="store_true", help="relire tous les enregistrements (première fois, ou collections réorganisées)")
    args = parseur.parse_args()

    dossier = Path(args.dossier).expanduser()
    nouveaux, erreur = recolter(dossier, tout=args.tout)
    total = len(lire_json(dossier / "enregistres.json"))
    print(f"{len(nouveaux)} nouveau(x) reel(s), {total} enregistrements au total → {dossier}")
    if erreur:
        print(f"Arrêt : {erreur}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
