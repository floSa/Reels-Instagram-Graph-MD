"""Maillage du vault : notes de thème et d'entité, wikilinks, vue graphe.

    uv run reels-mailler --vault ~/Vault

Aucun appel à une IA ici. On relit le frontmatter des fiches — rempli à l'étape
d'enrichissement — et on en dérive mécaniquement le maillage Obsidian.

Différence avec le `graphe.py` de reels-vault, qui reparse un `index.md` rédigé
par le LLM et dépend donc strictement de son format de sortie : la source de
vérité est ici le frontmatter de la fiche elle-même. Si le LLM formate mal, c'est
la fiche qui est fausse, pas le maillage qui casse en silence.

Le script est idempotent : on peut le relancer après chaque lot d'enrichissement.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

MARQUEUR = "## Liens"

# Caractères interdits dans un nom de fichier Obsidian (Windows compris).
INTERDITS = re.compile(r'[\\/:*?"<>|#\^\[\]]')

ENTETE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


def lire_frontmatter(texte: str) -> dict[str, object]:
    """Parseur YAML minimal, limité à ce que nos fiches produisent.

    Volontairement pas de dépendance à pyyaml : on n'écrit que des scalaires et
    des listes plates, et on veut que le projet reste installable sans rien.
    """
    correspondance = ENTETE.match(texte)
    if not correspondance:
        return {}

    valeurs: dict[str, object] = {}
    for ligne in correspondance.group(1).splitlines():
        if not ligne.strip() or ligne.lstrip().startswith("#") or ":" not in ligne:
            continue
        cle, _, brut = ligne.partition(":")
        cle, brut = cle.strip(), brut.strip()
        if not cle:
            continue
        if brut.startswith("[") and brut.endswith("]"):
            interieur = brut[1:-1].strip()
            valeurs[cle] = [_dequoter(m) for m in _decouper(interieur) if _dequoter(m)]
        else:
            valeurs[cle] = _dequoter(brut)
    return valeurs


def _decouper(interieur: str) -> list[str]:
    """Découpe une liste YAML inline en respectant les guillemets."""
    morceaux, courant, dans_guillemets = [], [], False
    for caractere in interieur:
        if caractere == '"':
            dans_guillemets = not dans_guillemets
            courant.append(caractere)
        elif caractere == "," and not dans_guillemets:
            morceaux.append("".join(courant))
            courant = []
        else:
            courant.append(caractere)
    if courant:
        morceaux.append("".join(courant))
    return morceaux


def _dequoter(valeur: str) -> str:
    valeur = valeur.strip()
    if len(valeur) >= 2 and valeur[0] == valeur[-1] and valeur[0] in "\"'":
        valeur = valeur[1:-1]
    return valeur.replace('\\"', '"').replace("\\\\", "\\").strip()


def nom_de_note(libelle: str) -> str:
    """Nom de fichier sûr pour une note de thème ou d'entité."""
    propre = INTERDITS.sub("-", libelle).strip(" .-")
    return propre[:80] or "sans-nom"


def titre_de(texte: str, defaut: str) -> str:
    for ligne in texte.splitlines():
        if ligne.startswith("# "):
            return ligne[2:].strip() or defaut
    return defaut


def collecter(dossier_fiches: Path) -> list[dict]:
    """Lit toutes les fiches et en extrait ce qui sert au maillage."""
    fiches = []
    for chemin in sorted(dossier_fiches.glob("*.md")):
        texte = chemin.read_text(encoding="utf-8", errors="ignore")
        entete = lire_frontmatter(texte)
        fiches.append(
            {
                "chemin": chemin,
                "id": chemin.stem,
                "titre": titre_de(texte, chemin.stem),
                "themes": [t for t in _liste(entete.get("themes")) if t],
                "entites": [e for e in _liste(entete.get("entites")) if e],
                "texte": texte,
            }
        )
    return fiches


def _liste(valeur: object) -> list[str]:
    if isinstance(valeur, list):
        return [str(v).strip() for v in valeur]
    if isinstance(valeur, str) and valeur.strip():
        return [m.strip() for m in valeur.split(",") if m.strip()]
    return []


def ecrire_note_index(dossier: Path, libelle: str, fiches: list[dict], genre: str) -> Path:
    """Une note qui liste tous les reels portant ce thème / citant cette entité."""
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / f"{nom_de_note(libelle)}.md"

    lignes = [
        "---",
        f'type: "{genre}"',
        f'libelle: "{libelle}"',
        f"nb_reels: {len(fiches)}",
        "---",
        "",
        f"# {libelle}",
        "",
        f"{len(fiches)} reel{'s' if len(fiches) > 1 else ''}.",
        "",
    ]
    for f in sorted(fiches, key=lambda f: f["titre"].lower()):
        lignes.append(f"- [[{f['id']}|{f['titre']}]]")

    chemin.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return chemin


def poser_liens(f: dict) -> bool:
    """Ajoute (ou remplace) la section `## Liens` de la fiche. True si modifiée."""
    if not f["themes"] and not f["entites"]:
        return False

    bloc = [MARQUEUR, ""]
    if f["themes"]:
        bloc.append("Thèmes : " + " ".join(f"[[{t}]]" for t in f["themes"]))
    if f["entites"]:
        bloc.append("Entités : " + " ".join(f"[[{e}]]" for e in f["entites"]))
    nouveau = "\n".join(bloc) + "\n"

    texte = f["texte"]
    position = texte.find(f"\n{MARQUEUR}\n")
    if position != -1:
        # Idempotence : on remplace la section précédente au lieu d'empiler.
        ancien = texte[position + 1 :]
        if ancien == nouveau:
            return False
        texte = texte[: position + 1] + nouveau
    else:
        texte = texte.rstrip() + "\n\n" + nouveau

    f["chemin"].write_text(texte, encoding="utf-8")
    f["texte"] = texte
    return True


def mailler(vault: Path) -> tuple[int, int, int, int, int]:
    """Génère les notes de thème et d'entité et pose les liens.

    Retourne (fiches lues, thèmes, entités, fiches modifiées, fiches sans métadonnées).
    """
    fiches = collecter(vault / "fiches")
    par_theme: dict[str, list[dict]] = defaultdict(list)
    par_entite: dict[str, list[dict]] = defaultdict(list)
    for f in fiches:
        for theme in f["themes"]:
            par_theme[theme].append(f)
        for entite in f["entites"]:
            par_entite[entite].append(f)

    for libelle, groupe in par_theme.items():
        ecrire_note_index(vault / "themes", libelle, groupe, "theme")
    for libelle, groupe in par_entite.items():
        ecrire_note_index(vault / "entites", libelle, groupe, "entite")

    modifiees = sum(1 for f in fiches if poser_liens(f))
    sans_metadonnees = sum(1 for f in fiches if not f["themes"] and not f["entites"])
    return len(fiches), len(par_theme), len(par_entite), modifiees, sans_metadonnees


def main() -> int:
    parseur = argparse.ArgumentParser(
        prog="reels-mailler",
        description="Génère les notes de thème et d'entité du vault, et pose les wikilinks.",
    )
    parseur.add_argument("--vault", default=str(Path.home() / "Vault"))
    args = parseur.parse_args()

    vault = Path(args.vault).expanduser()
    dossier_fiches = vault / "fiches"
    if not dossier_fiches.is_dir():
        print(f"Aucun dossier de fiches dans {vault}.", file=sys.stderr)
        return 2

    if not any(dossier_fiches.glob("*.md")):
        print(f"Aucune fiche dans {dossier_fiches}.", file=sys.stderr)
        return 2

    lues, themes, entites, modifiees, sans_metadonnees = mailler(vault)
    print(f"{lues} fiches lues.")
    print(f"{themes} thèmes, {entites} entités.")
    print(f"{modifiees} fiches mises à jour.")
    if sans_metadonnees:
        print(
            f"{sans_metadonnees} fiches sans thème ni entité — "
            "elles n'ont pas encore été enrichies (étape 8)."
        )
    return 0

if __name__ == "__main__":
    sys.exit(main())
