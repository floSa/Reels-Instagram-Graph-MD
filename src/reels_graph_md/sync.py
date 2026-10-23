"""Synchronisation d'un vault thématique avec une collection d'enregistrés Meta.

    uv run reels-sync --vault ~/Documents/Vault-Politique \\
        --collection politique --export ~/reels-exports \\
        --reprendre-de ~/Documents/ReelsVault --cookies firefox

Une seule commande, rejouable à volonté. À chaque passage :

1. lit la collection dans l'export Meta le plus récent (dossier ou zip) ;
2. reprend ce qui est déjà traité dans l'ancien vault, sans rien refaire ;
3. télécharge et transcrit uniquement ce qui manque (le journal écarte les doublons) ;
4. régénère le maillage, puis affiche l'état (`STATUT.md`).

Les réglages sont mémorisés dans `theme.json` : après le premier lancement,
`reels-sync --vault <vault> --cookies firefox` suffit.

Meta n'expose aucune API pour les enregistrés : le seul point d'entrée est
l'export officiel, à refaire pour voir passer les nouveaux reels.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import index, ingest, reprise
from .ingest import log
from .journal import Journal
from .mailler import mailler
from .meta import ErreurExport, date_export, liens_de_collection
from .statut import ecrire_statut, lire_json

EXPORT_DEFAUT = Path.home() / "reels-exports"


def fusionner_config(vault: Path, args: argparse.Namespace) -> dict:
    """Config mémorisée, surchargée par la ligne de commande, puis réécrite."""
    config = lire_json(vault / "theme.json")
    config.setdefault("nom", vault.name)
    for cle, valeur in (
        ("collection", args.collection),
        ("export", args.export),
        ("reprendre_de", args.reprendre_de),
        ("themes", [t.strip() for t in args.themes.split(",")] if args.themes else None),
    ):
        if valeur:
            config[cle] = valeur
    config.setdefault("themes", list(index.DOMAINES.get(config.get("collection", ""), ())))
    config.setdefault("export", str(EXPORT_DEFAUT))
    (vault / "theme.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return config


def lire_cible(vault: Path, config: dict) -> list[str]:
    """Liens de la collection d'après l'export, mémorisés dans `cible.json`.

    Si l'export est illisible ou sans collection, on garde la cible mémorisée :
    l'erreur est affichée mais ne bloque pas le reste.
    """
    export = Path(config["export"]).expanduser()
    try:
        urls, _ = liens_de_collection(export, config["collection"])
    except ErreurExport as erreur:
        log(f"Export : {erreur}")
        return lire_json(vault / "cible.json").get("urls", [])

    dernier = date_export(export)
    (vault / "cible.json").write_text(
        json.dumps(
            {
                "collection": config["collection"],
                "urls": urls,
                "export": str(export),
                "dernier_enregistrement": dernier.isoformat() if dernier else None,
                "lu_le": datetime.now().isoformat(timespec="minutes"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log(f"Collection « {config['collection']} » : {len(urls)} reels dans l'export.")
    return urls


def main() -> int:
    parseur = argparse.ArgumentParser(
        prog="reels-sync",
        description="Synchronise un vault thématique avec une collection d'enregistrés Meta.",
    )
    parseur.add_argument("--vault", required=True, help="vault thématique (créé s'il n'existe pas)")
    parseur.add_argument("--collection", help="nom de la collection d'enregistrés (mémorisé)")
    parseur.add_argument("--export", help=f"dossier ou zip de l'export Meta (défaut : {EXPORT_DEFAUT})")
    parseur.add_argument("--reprendre-de", dest="reprendre_de", help="ancien vault dont réutiliser les reels déjà traités")
    parseur.add_argument("--themes", help="thèmes à reprendre de l'ancien vault, séparés par des virgules")
    parseur.add_argument("--cookies", default=None, help="navigateur (firefox) ou cookies.txt, requis pour télécharger")
    parseur.add_argument("--limite", type=int, default=0, help="ne traiter que N reels")
    parseur.add_argument("--pause", type=float, default=ingest.PAUSE_DEFAUT)
    parseur.add_argument("--langue", default="fr")
    parseur.add_argument("--sans-telechargement", action="store_true", help="lire, reprendre et faire le point, sans télécharger")
    args = parseur.parse_args()

    vault = Path(args.vault).expanduser()
    vault.mkdir(parents=True, exist_ok=True)
    config = fusionner_config(vault, args)
    if not config.get("collection"):
        print("Il manque --collection (nom de la collection d'enregistrés).", file=sys.stderr)
        return 2

    dossiers = ingest._preparer_vault(vault)
    carnet = Journal(vault / "journal.json")

    urls_cible = lire_cible(vault, config)

    if config.get("reprendre_de"):
        source = Path(config["reprendre_de"]).expanduser()
        if not (source / "journal.json").exists():
            log(f"Ancien vault introuvable : {source}")
        else:
            candidats = urls_cible + reprise.urls_par_themes(source, config["themes"])
            bilan = reprise.reprendre(vault, source, candidats, carnet)
            log(
                f"Reprise de {source.name} : {bilan['repris']} repris, {bilan['deja']} déjà là, "
                f"{bilan['absents']} jamais traités."
            )
            if bilan["sans_video"]:
                log(f"  {bilan['sans_video']} vidéo(s) absente(s) de l'ancien vault (fiche reprise seule).")

    a_faire = carnet.a_traiter(urls_cible)
    if args.limite:
        a_faire = a_faire[: args.limite]
    code = 0
    if a_faire and not args.sans_telechargement:
        if not args.cookies:
            log(f"{len(a_faire)} reel(s) à télécharger, mais --cookies manque (ex. --cookies firefox).")
            code = 2
        else:
            log(f"{len(a_faire)} reel(s) à traiter.")
            code = ingest.traiter_lot(
                a_faire, vault, dossiers, carnet,
                cookies=args.cookies, langue=args.langue, pause=args.pause, lire_inbox=False,
            )
    elif not urls_cible:
        log("Aucune cible : la collection n'a pas pu être lue, rien à télécharger.")
    elif not a_faire:
        log("Rien à télécharger : tout ce que l'export désigne est déjà traité.")

    if any((vault / "fiches").glob("*.md")):
        mailler(vault)
        index.construire_index(vault)

    print()
    print(ecrire_statut(vault))
    return code


if __name__ == "__main__":
    sys.exit(main())
