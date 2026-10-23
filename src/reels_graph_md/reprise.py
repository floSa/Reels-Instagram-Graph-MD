"""Reprise : réutiliser ce qui a déjà été traité dans un autre vault.

Télécharger et transcrire un reel coûte ~10 s et 12 Mo. Quand un reel est déjà
dans un autre vault (le grand `ReelsVault` d'avant les vaults thématiques), on
copie sa fiche, sa vidéo et son entrée de journal au lieu de le refaire. C'est
aussi une déduplication : un reel repris est ensuite « fait » pour le journal, et
n'est jamais retéléchargé.

Rien n'est déplacé ni supprimé dans le vault source.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path

from .journal import Journal
from .mailler import lire_frontmatter


def urls_par_themes(source: Path, themes: Iterable[str]) -> list[str]:
    """URLs du vault source dont la fiche porte au moins un des thèmes."""
    themes = set(themes)
    carnet = Journal(source / "journal.json")
    retenues = []
    for url, entree in carnet.entrees.items():
        if entree.get("statut") != "ok" or entree.get("alias_de") or not entree.get("fiche"):
            continue
        chemin = source / "fiches" / entree["fiche"]
        if not chemin.exists():
            continue
        entete = lire_frontmatter(chemin.read_text(encoding="utf-8", errors="ignore"))
        if themes & set(entete.get("themes") or []):
            retenues.append(url)
    return retenues


def reprendre(cible: Path, source: Path, urls: Iterable[str], carnet: Journal) -> dict[str, int]:
    """Copie dans `cible` les reels de `urls` déjà traités dans `source`.

    `carnet` est le journal de la cible, mis à jour au fil de l'eau.
    Retourne les compteurs : repris, deja (déjà dans la cible), absents (pas dans la source).
    """
    amont = Journal(source / "journal.json")
    compte = {"repris": 0, "deja": 0, "absents": 0, "sans_video": 0}

    def copier(url: str) -> bool:
        if carnet.est_fait(url):
            return True  # jamais d'écrasement : la fiche de la cible a pu être enrichie depuis
        entree = amont.entrees.get(url, {})
        if entree.get("statut") != "ok":
            return False
        details = {k: v for k, v in entree.items() if k not in ("statut", "le")}
        if entree.get("alias_de"):
            if not copier(entree["alias_de"]) and not carnet.est_fait(entree["alias_de"]):
                return False
        else:
            fiche = source / "fiches" / entree.get("fiche", "")
            if not fiche.is_file():
                return False
            (cible / "fiches").mkdir(parents=True, exist_ok=True)
            shutil.copy2(fiche, cible / "fiches" / fiche.name)
            if entree.get("video"):
                video = source / entree["video"]
                if video.is_file():
                    (cible / "reels").mkdir(parents=True, exist_ok=True)
                    shutil.copy2(video, cible / "reels" / video.name)
                else:
                    compte["sans_video"] += 1
        if not carnet.est_fait(url):
            carnet.succes(url, **details, reprise=source.name)
            compte["repris"] += 1
        return True

    for url in dict.fromkeys(urls):
        if carnet.est_fait(url):
            compte["deja"] += 1
        elif not copier(url):
            compte["absents"] += 1
    return compte
