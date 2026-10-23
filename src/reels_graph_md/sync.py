"""Synchronisation d'un vault thématique avec une collection d'enregistrés Instagram.

Première fois : repérer la collection (une seule fois, c'est l'utilisateur qui la nomme).

    uv run reels-sync --vault ~/Documents/Vault-Politique --decouvrir

Puis, à chaque fois (et c'est ce que la tâche hebdomadaire lance) :

    uv run reels-sync --vault ~/Documents/Vault-Politique --cookies firefox

À chaque passage :

1. lit les nouveaux enregistrements en direct (session Firefox), avec leurs collections ;
2. retient ceux de la collection du vault ;
3. reprend ce qui est déjà traité dans l'ancien vault, sans rien refaire ;
4. télécharge et transcrit uniquement ce qui manque (le journal écarte les doublons) ;
5. régénère le maillage, puis écrit `STATUT.md`.

Les réglages sont mémorisés dans `theme.json`. L'export Meta ne sert plus que de
repli : celui de ce compte ne contient ni collections ni enregistrements récents.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import index, ingest, live, reprise, ytdlp
from .ingest import log
from .journal import Journal
from .mailler import mailler
from .meta import ErreurExport, date_export, liens_de_collection
from .statut import ecrire_statut, lire_json

EXPORT_DEFAUT = Path.home() / "reels-exports"


def code_de(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def fusionner_config(vault: Path, args: argparse.Namespace) -> dict:
    """Config mémorisée, surchargée par la ligne de commande, puis réécrite."""
    config = lire_json(vault / "theme.json")
    config.setdefault("nom", vault.name)
    for cle, valeur in (
        ("collection", args.collection),
        ("collection_id", live.identifiant_collection(args.collection_id) if args.collection_id else None),
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


def session_instagram() -> dict[str, str]:
    profils = ytdlp.profils_firefox_windows() if ytdlp.sous_wsl() else []
    if not profils:
        raise live.ErreurLive("Aucun profil Firefox trouvé pour lire la session Instagram.")
    return live.cookies_firefox(profils[0])


def actualiser_enregistres(vault: Path, *, tout: bool = False) -> dict[str, dict]:
    """Met à jour `enregistres.json` (url -> collections, auteur, légende) depuis Instagram.

    Incrémental par défaut : la lecture s'arrête à la première page sans nouveauté.
    `tout` force la lecture complète (première fois, ou collections réorganisées).
    """
    chemin = vault / "enregistres.json"
    magasin = lire_json(chemin)
    nouveaux = live.lire_enregistres(session_instagram(), connus=None if tout else set(magasin))
    for e in nouveaux:
        magasin[e["url"]] = {k: e[k] for k in ("collections", "auteur", "legende")}
    chemin.write_text(json.dumps(magasin, ensure_ascii=False, indent=1), encoding="utf-8")
    return magasin


def decrire_collections(magasin: dict[str, dict], deja_la: set[str]) -> str:
    """Un portrait par identifiant de collection, pour que l'utilisateur reconnaisse la sienne."""
    par_id: dict[str, list[tuple[str, dict]]] = {}
    for url, e in magasin.items():
        for c in e["collections"]:
            par_id.setdefault(c, []).append((url, e))
    lignes = [f"{len(magasin)} enregistrements, dont {sum(1 for e in magasin.values() if not e['collections'])} hors collection.", ""]
    for ident, membres in sorted(par_id.items(), key=lambda kv: -len(kv[1])):
        comptes = ", ".join(f"{a} ({n})" for a, n in Counter(e["auteur"] for _, e in membres).most_common(4))
        connus = sum(1 for u, _ in membres if code_de(u) in deja_la)
        lignes += [
            f"Collection {ident} — {len(membres)} reels, dont {connus} déjà dans ce vault",
            f"  comptes : {comptes}",
            *[f"  « {e['legende'][:90].replace(chr(10), ' ')} »" for _, e in membres[:2]],
            "",
        ]
    return "\n".join(lignes)


def aligner(urls: list[str], connus: dict[str, str]) -> list[str]:
    """Remplace une URL par la forme déjà connue du même post (/reel/ ou /p/), pour ne pas dédoubler."""
    return [connus.get(code_de(u), u) for u in urls]


def lire_cible(vault: Path, config: dict, connus: dict[str, str]) -> list[str]:
    """Liens de la collection, mémorisés dans `cible.json`.

    Source : lecture en direct si l'identifiant de collection est connu, sinon
    export Meta. Si tout échoue, on garde la cible mémorisée : l'erreur est
    affichée mais ne bloque pas le reste.
    """
    urls, origine, dernier = None, "", None
    if config.get("collection_id"):
        try:
            magasin = actualiser_enregistres(vault)
            urls = [u for u, e in magasin.items() if config["collection_id"] in e["collections"]]
            origine, dernier = "lecture en direct", datetime.now()
        except live.ErreurLive as erreur:
            log(f"Lecture en direct : {erreur}")
    if urls is None:
        export = Path(config["export"]).expanduser()
        try:
            urls, _ = liens_de_collection(export, config["collection"])
            origine, dernier = str(export), date_export(export)
        except ErreurExport as erreur:
            log(f"Export : {erreur}")
            return lire_json(vault / "cible.json").get("urls", [])

    urls = aligner(urls, connus)
    (vault / "cible.json").write_text(
        json.dumps(
            {
                "collection": config["collection"],
                "urls": urls,
                "export": origine,
                "dernier_enregistrement": dernier.isoformat(timespec="minutes") if dernier else None,
                "lu_le": datetime.now().isoformat(timespec="minutes"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log(f"Collection « {config['collection']} » : {len(urls)} reels ({origine}).")
    return urls


def main() -> int:
    parseur = argparse.ArgumentParser(
        prog="reels-sync",
        description="Synchronise un vault thématique avec une collection d'enregistrés Instagram.",
    )
    parseur.add_argument("--vault", required=True, help="vault thématique (créé s'il n'existe pas)")
    parseur.add_argument("--collection", help="nom donné à la collection (mémorisé)")
    parseur.add_argument(
        "--collection-id",
        dest="collection_id",
        help="identifiant de la collection, ou son URL instagram.com/<compte>/saved/<nom>/<id>/ (mémorisé)",
    )
    parseur.add_argument(
        "--decouvrir",
        action="store_true",
        help="lire tous les enregistrements et décrire chaque collection, pour repérer la sienne",
    )
    parseur.add_argument("--export", help=f"repli : dossier ou zip de l'export Meta (défaut : {EXPORT_DEFAUT})")
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
    dossiers = ingest._preparer_vault(vault)
    carnet = Journal(vault / "journal.json")

    source = Path(config["reprendre_de"]).expanduser() if config.get("reprendre_de") else None
    if source is not None and not (source / "journal.json").exists():
        log(f"Ancien vault introuvable : {source}")
        source = None

    # Les clés de journal du vault et de l'ancien vault : un post connu sous une forme
    # (/reel/ ou /p/) ne doit pas être refait sous l'autre.
    connus = {code_de(u): u for u in carnet.entrees}
    if source is not None:
        connus |= {code_de(u): u for u in Journal(source / "journal.json").entrees}

    if args.decouvrir:
        try:
            magasin = actualiser_enregistres(vault, tout=True)
        except live.ErreurLive as erreur:
            log(f"Lecture en direct : {erreur}")
            return 2
        print(decrire_collections(magasin, {code_de(u) for u in carnet.entrees}))
        print("Repère ta collection puis relance avec : --collection politique --collection-id <identifiant>")
        return 0

    if not config.get("collection"):
        print("Il manque --collection (nom de la collection). Lance d'abord --decouvrir.", file=sys.stderr)
        return 2

    urls_cible = lire_cible(vault, config, connus)

    if source is not None:
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
        log("Rien à télécharger : tout ce que la collection désigne est déjà traité.")

    if any((vault / "fiches").glob("*.md")):
        mailler(vault)
        index.construire_index(vault)

    print()
    print(ecrire_statut(vault))
    return code


if __name__ == "__main__":
    sys.exit(main())
