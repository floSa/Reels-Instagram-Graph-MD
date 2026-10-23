"""Carte d'un domaine : la porte d'entrée pour se balader dans le graphe.

    uv run reels-carte politique --vault ~/Vault

Écrit dans le vault, depuis l'index (`graphe.db`) :

- `cartes/<domaine>.md` : entités par type, qui est cité avec qui, qui parle
  (comptes), chronologie. Les liens pointent vers les notes de `reels-mailler`.
- `comptes/<compte>.md` : un nœud par compte, relié à ses reels du domaine.

Les comptes sont un nœud à part : savoir *qui* dit quelque chose compte autant que
ce qui est dit, surtout sur du contenu politique.

Idempotent : les notes générées sont réécrites à chaque passage.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

from reels_graph_md.index import DOMAINES, ouvrir, reels_du_domaine
from reels_graph_md.mailler import nom_de_note

SEUIL_PAIRE = 2


def _lien(libelle: str) -> str:
    cible = nom_de_note(libelle)
    return f"[[{cible}]]" if cible == libelle else f"[[{cible}|{libelle}]]"


def type_dominant(types: list[str]) -> str:
    """Une même entité peut être typée différemment d'une fiche à l'autre : le plus fréquent l'emporte."""
    return Counter(types).most_common(1)[0][0]


def ecrire_carte(vault: Path, domaine: str, themes: tuple[str, ...]) -> tuple[int, int]:
    """Écrit la carte et les notes de compte. Retourne (nb reels, nb comptes)."""
    con = ouvrir(vault)
    ids = reels_du_domaine(con, themes)
    reels = [r for r in con.execute("SELECT * FROM reels ORDER BY date DESC, id") if r["id"] in ids]

    types: dict[str, list[str]] = defaultdict(list)
    cites: dict[str, set[str]] = defaultdict(set)
    par_reel: dict[str, list[str]] = defaultdict(list)
    for e in con.execute("SELECT reel, nom, type FROM reel_entite"):
        if e["reel"] in ids:
            types[e["nom"]].append(e["type"])
            cites[e["nom"]].add(e["reel"])
            par_reel[e["reel"]].append(e["nom"])

    paires: Counter = Counter()
    for noms in par_reel.values():
        paires.update(combinations(sorted(set(noms)), 2))

    par_type: dict[str, list[str]] = defaultdict(list)
    for nom, ts in types.items():
        par_type[type_dominant(ts)].append(nom)

    comptes: dict[str, list] = defaultdict(list)
    for r in reels:
        comptes[r["auteur"] or "(inconnu)"].append(r)

    lignes = [
        "---",
        'type: "carte"',
        f'domaine: "{domaine}"',
        f"nb_reels: {len(reels)}",
        "---",
        "",
        f"# Carte — {domaine}",
        "",
        f"{len(reels)} reels, {len(types)} entités, {len(comptes)} comptes. "
        f"Thèmes retenus : {', '.join(themes)}.",
        "",
        "## Entités par type",
        "",
    ]
    for genre, noms in sorted(par_type.items(), key=lambda kv: -sum(len(cites[n]) for n in kv[1])):
        lignes += [f"### {genre}", ""]
        for nom in sorted(noms, key=lambda n: (-len(cites[n]), n)):
            lignes.append(f"- {_lien(nom)} — {len(cites[nom])}")
        lignes.append("")

    frequentes = [(p, n) for p, n in paires.most_common() if n >= SEUIL_PAIRE]
    if frequentes:
        lignes += [f"## Cités ensemble (au moins {SEUIL_PAIRE} reels)", ""]
        lignes += [f"- {_lien(a)} + {_lien(b)} — {n}" for (a, b), n in frequentes]
        lignes.append("")

    lignes += ["## Qui parle", ""]
    for compte, groupe in sorted(comptes.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        lignes.append(f"- {_lien(compte)} — {len(groupe)}")
    lignes += ["", "## Chronologie", ""]
    lignes += [f"- {r['date'] or 's. d.'} — [[{r['id']}|{r['titre']}]]" for r in reels]

    (vault / "cartes").mkdir(exist_ok=True)
    (vault / "cartes" / f"{domaine}.md").write_text("\n".join(lignes) + "\n", encoding="utf-8")

    (vault / "comptes").mkdir(exist_ok=True)
    for compte, groupe in comptes.items():
        corps = [
            "---",
            'type: "compte"',
            f'libelle: "{compte}"',
            f"nb_reels: {len(groupe)}",
            "---",
            "",
            f"# {compte}",
            "",
            f"{len(groupe)} reel{'s' if len(groupe) > 1 else ''} dans « {domaine} ».",
            "",
        ]
        corps += [f"- [[{r['id']}|{r['titre']}]]" for r in groupe]
        (vault / "comptes" / f"{nom_de_note(compte)}.md").write_text("\n".join(corps) + "\n", encoding="utf-8")

    return len(reels), len(comptes)


def main() -> int:
    parseur = argparse.ArgumentParser(prog="reels-carte", description="Génère la carte d'un domaine du vault.")
    parseur.add_argument("domaine", help=f"un domaine connu ({', '.join(sorted(DOMAINES))}) ou un nom libre avec --themes")
    parseur.add_argument("--vault", default=str(Path.home() / "Vault"))
    parseur.add_argument("--themes", help="thèmes séparés par des virgules (remplace la définition du domaine)")
    args = parseur.parse_args()

    themes = tuple(t.strip() for t in args.themes.split(",")) if args.themes else DOMAINES.get(args.domaine)
    if not themes:
        print(f"Domaine inconnu : {args.domaine}. Donner --themes.", file=sys.stderr)
        return 2

    vault = Path(args.vault).expanduser()
    if not (vault / "fiches").is_dir():
        print(f"Aucun dossier de fiches dans {vault}.", file=sys.stderr)
        return 2

    nb_reels, nb_comptes = ecrire_carte(vault, args.domaine, themes)
    print(f"cartes/{args.domaine}.md : {nb_reels} reels, {nb_comptes} comptes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
