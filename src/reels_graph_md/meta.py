"""Lecture des exports Meta : les collections d'enregistrés, par nom.

`liens.py` lit un export en texte brut et ne sait donc pas dans quelle collection
est rangé un reel. Or le périmètre d'un vault thématique, c'est une collection
(« politique »). Ce module lit l'export comme du JSON pour retrouver les liens
d'une collection nommée.

Le format des exports Meta varie selon la date et la langue du compte. On ne
suppose donc aucune structure : on cherche, à n'importe quelle profondeur, les
objets qui portent le nom de la collection (clé `title`, ou libellé `Nom` /
`Titre` / `Name` / `Title`), et on ramasse tous les liens de contenu qu'ils
contiennent. Si rien ne sort, l'erreur liste les collections trouvées plutôt que
de se taire.
"""

from __future__ import annotations

import json
import re
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path

from . import liens

LIBELLES_NOM = {"nom", "titre", "name", "title"}
# Intitulés de conteneurs de l'export, jamais des noms de collection.
TITRES_STRUCTURELS = {"enregistrements", "saves", "saved", "participant(e)s", "participants", "proprietaire", "hashtags"}
FICHIERS_UTILES = re.compile(r"(?:saved_(?:collections|posts)[^/]*|collections|your_saved_items)\.json$", re.IGNORECASE)


class ErreurExport(Exception):
    pass


def corriger(texte: str) -> str:
    """Les exports Meta encodent l'UTF-8 comme du latin-1 (`TÃ©lÃ©vision`) : on le défait."""
    try:
        return texte.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return texte


def clef(nom: str) -> str:
    """Forme de comparaison d'un nom de collection : sans accents, casse ni espaces en trop."""
    sans_accents = unicodedata.normalize("NFKD", corriger(nom)).encode("ascii", "ignore").decode()
    return " ".join(sans_accents.lower().split())


def lire_sources(source: Path) -> list[tuple[str, str]]:
    """(nom, texte) de chaque fichier JSON d'enregistrés trouvé dans un dossier, un zip ou un fichier."""
    if source.is_file() and source.suffix.lower() == ".zip":
        return _lire_zip(source)
    if source.is_file():
        return [(source.name, source.read_text(encoding="utf-8", errors="ignore"))]
    if not source.is_dir():
        raise ErreurExport(f"Introuvable : {source}")

    trouves: list[tuple[str, str]] = []
    for chemin in sorted(source.rglob("*")):
        if chemin.suffix.lower() == ".zip":
            trouves += _lire_zip(chemin)
        elif chemin.suffix.lower() == ".json" and FICHIERS_UTILES.search(chemin.name):
            trouves.append((chemin.name, chemin.read_text(encoding="utf-8", errors="ignore")))
    if not trouves:
        raise ErreurExport(
            f"Aucun fichier d'enregistrés (saved_collections.json, saved_posts.json, collections.json) dans {source}. "
            "Dépose-y l'export Meta (le zip tel quel, ou le dossier décompressé)."
        )
    return trouves


def _lire_zip(chemin: Path) -> list[tuple[str, str]]:
    with zipfile.ZipFile(chemin) as archive:
        return [
            (Path(nom).name, archive.read(nom).decode("utf-8", errors="ignore"))
            for nom in archive.namelist()
            if FICHIERS_UTILES.search(nom)
        ]


def _noms(noeud: dict) -> set[str]:
    noms: set[str] = set()
    if isinstance(noeud.get("title"), str) and noeud["title"].strip():
        noms.add(noeud["title"])
    if isinstance(noeud.get("label"), str) and noeud["label"].lower() in LIBELLES_NOM:
        if isinstance(noeud.get("value"), str) and noeud["value"].strip():
            noms.add(noeud["value"])
    for cle, valeur in (noeud.get("string_map_data") or {}).items():
        if cle.lower() in LIBELLES_NOM and isinstance(valeur, dict) and isinstance(valeur.get("value"), str):
            noms.add(valeur["value"])
    for sous in noeud.get("label_values") or []:
        if isinstance(sous, dict) and str(sous.get("label", "")).lower() in LIBELLES_NOM:
            if isinstance(sous.get("value"), str) and sous["value"].strip():
                noms.add(sous["value"])
    return noms


def _liens_de(noeud) -> list[str]:
    trouves: list[str] = []
    for brut in liens.MOTIF_LIEN.findall(json.dumps(noeud, ensure_ascii=False)):
        brut = brut.rstrip(liens.FIN_PARASITE)
        if liens.est_contenu(brut) and liens.normaliser(brut) not in trouves:
            trouves.append(liens.normaliser(brut))
    return trouves


def collections(texte_json: str) -> dict[str, list[str]]:
    """Collections de l'export : nom corrigé -> liens de contenu, dans l'ordre d'apparition."""
    try:
        racine = json.loads(texte_json)
    except json.JSONDecodeError as exc:
        raise ErreurExport(f"JSON illisible : {exc}") from exc

    resultat: dict[str, list[str]] = {}

    def parcourir(noeud) -> None:
        if isinstance(noeud, dict):
            urls = None
            for nom in _noms(noeud):
                if clef(nom) in TITRES_STRUCTURELS:
                    continue
                if urls is None:
                    urls = _liens_de(noeud)
                if urls:
                    groupe = resultat.setdefault(corriger(nom), [])
                    groupe += [u for u in urls if u not in groupe]
            for valeur in noeud.values():
                parcourir(valeur)
        elif isinstance(noeud, list):
            for valeur in noeud:
                parcourir(valeur)

    parcourir(racine)
    return resultat


def liens_de_collection(source: Path, nom: str) -> tuple[list[str], list[str]]:
    """Liens de la collection `nom` dans l'export `source`, et la liste des collections vues.

    Plusieurs fichiers ou plusieurs parties d'export sont fusionnés. Une
    collection absente lève une erreur qui dit ce qui existe.
    """
    cherchee = clef(nom)
    urls: list[str] = []
    vues: dict[str, int] = {}
    for fichier, texte in lire_sources(source):
        if "collection" not in fichier.lower():
            continue  # saved_posts.json n'a pas de collection : inutile de le fouiller
        for nom_trouve, liens_trouves in collections(texte).items():
            vues[nom_trouve] = vues.get(nom_trouve, 0) + len(liens_trouves)
            if clef(nom_trouve) == cherchee:
                urls += [u for u in liens_trouves if u not in urls]

    noms_vus = [f"{n} ({c})" for n, c in sorted(vues.items(), key=lambda kv: -kv[1])]
    if not urls:
        if any("collection" in f.lower() for f, _ in lire_sources(source)):
            raise ErreurExport(
                f"Collection « {nom} » introuvable. Collections avec des liens dans l'export : "
                + (", ".join(noms_vus[:30]) or "aucune")
            )
        raise ErreurExport(
            "L'export ne contient pas saved_collections.json : seul saved_posts.json est là, "
            "et il ne dit pas dans quelle collection est rangé un reel. "
            "Refais l'export en cochant les enregistrements ET leurs collections."
        )
    return urls, noms_vus


def date_export(source: Path) -> datetime | None:
    """Date du dernier enregistrement présent dans l'export : jusqu'où il est à jour."""
    derniere = 0
    for _, texte in lire_sources(source):
        derniere = max([derniere, *(int(m) for m in re.findall(r'"timestamp(?:_value)?":\s*(\d{9,11})', texte))])
    return datetime.fromtimestamp(derniere) if derniere else None
