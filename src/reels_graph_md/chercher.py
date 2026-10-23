"""Recherche dans la parole des reels.

    uv run reels-chercher "Marc Bloch Gestapo" --vault ~/Vault --domaine politique

Cherche dans le transcript, la synthèse, les affirmations et la légende, et rend
pour chaque reel le passage qui répond avec son horodatage : on sait où sauter
dans la vidéo.

La recherche est littérale (FTS5, accents et casse ignorés, préfixes acceptés).
Elle ne relie pas « vote » à « adopté » ; c'est la question sémantique laissée
ouverte dans docs/CADRAGE.md.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

from reels_graph_md.index import DOMAINES, ouvrir, reels_du_domaine

ORDRE_ORIGINE = {"affirmation": 0, "parole": 1, "synthese": 2, "legende": 3}


def requete_fts(texte: str, operateur: str = "AND") -> str:
    """Texte libre -> requête FTS5 sûre : chaque mot entre guillemets, en préfixe."""
    mots = re.findall(r"\w+", texte)
    return f" {operateur} ".join(f'"{m}"*' for m in mots)


def chercher(con: sqlite3.Connection, texte: str, reels: set[str] | None = None, limite: int = 10) -> list[dict]:
    """Reels classés par pertinence, avec leurs meilleurs passages."""
    lignes = []
    for operateur in ("AND", "OR"):
        requete = requete_fts(texte, operateur)
        if not requete:
            return []
        lignes = con.execute(
            "SELECT reel, ts, origine, snippet(recherche, 3, '«', '»', '…', 24) AS extrait, bm25(recherche) AS score "
            "FROM recherche WHERE recherche MATCH ? ORDER BY score LIMIT 600",
            (requete,),
        ).fetchall()
        if reels is not None:
            lignes = [r for r in lignes if r["reel"] in reels]
        if lignes:  # le OR n'est qu'un repli quand le AND ne trouve rien
            break

    groupes: dict[str, list[sqlite3.Row]] = {}
    for r in lignes:
        groupes.setdefault(r["reel"], []).append(r)

    resultats = []
    for reel, passages in list(groupes.items())[:limite]:
        fiche = con.execute("SELECT * FROM reels WHERE id = ?", (reel,)).fetchone()
        # Un passage parlé ou une affirmation vaut mieux qu'une légende : il a un repère.
        passages.sort(key=lambda p: (ORDRE_ORIGINE[p["origine"]], p["score"]))
        resultats.append({"reel": fiche, "passages": passages[:2]})
    return resultats


def _repere(secondes: int) -> str:
    return f"{secondes // 60:02d}:{secondes % 60:02d}"


def main() -> int:
    parseur = argparse.ArgumentParser(prog="reels-chercher", description="Cherche dans la parole des reels du vault.")
    parseur.add_argument("requete", help="mots à chercher")
    parseur.add_argument("--vault", default=str(Path.home() / "Vault"))
    parseur.add_argument("--domaine", choices=sorted(DOMAINES), help="restreindre à un domaine")
    parseur.add_argument("--themes", help="thèmes séparés par des virgules (remplace --domaine)")
    parseur.add_argument("--limite", type=int, default=10)
    args = parseur.parse_args()

    vault = Path(args.vault).expanduser()
    if not (vault / "fiches").is_dir():
        print(f"Aucun dossier de fiches dans {vault}.", file=sys.stderr)
        return 2

    con = ouvrir(vault)
    themes = tuple(t.strip() for t in args.themes.split(",")) if args.themes else DOMAINES.get(args.domaine or "")
    reels = reels_du_domaine(con, themes) if themes else None

    resultats = chercher(con, args.requete, reels, args.limite)
    if not resultats:
        print("Rien trouvé.")
        return 1

    for r in resultats:
        f = r["reel"]
        print(f"\n{f['titre']}")
        print(f"  {f['id']} · {f['auteur']} · {f['date']} · fiabilité {f['fiabilite']}")
        for p in r["passages"]:
            repere = f"[{_repere(p['ts'])}] " if p["origine"] in ("parole", "affirmation") else ""
            print(f"  {p['origine']:<11} {repere}{p['extrait']}")
        print(f"  vidéo : {vault / 'reels' / (f['id'] + '.mp4')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
