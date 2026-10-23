"""Index du vault : les fiches deviennent une base SQLite interrogeable.

Les fiches Markdown restent la source de vérité. `graphe.db` est entièrement
dérivé : on le reconstruit à volonté, on peut le supprimer sans rien perdre.

Il sert deux choses que les wikilinks seuls ne font pas :

- chercher dans la parole (FTS5), avec l'horodatage du passage qui répond ;
- calculer le graphe typé (qui est cité avec qui, quel compte parle de quoi).

Stdlib uniquement : sqlite3 embarque FTS5.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from reels_graph_md.mailler import lire_frontmatter, titre_de

# Un domaine est un ensemble de thèmes du vault. Les thèmes sont émergents : cette
# liste suit ce que le vault contient, elle ne le prescrit pas. Surchargeable avec
# `--themes` sur les commandes.
DOMAINES: dict[str, tuple[str, ...]] = {
    "politique": (
        "politique",
        "extrême droite",
        "sociologie électorale",
        "violences policières",
        "conflit israélo-palestinien",
        "géopolitique",
        "racisme",
        "immigration",
        "laïcité",
        "identité nationale",
        "lobbying",
        "fiscalité",
        "justice",
        "terrorisme",
    ),
}

HORODATAGE = re.compile(r"\[(?:(\d+):)?(\d{1,2}):(\d{2})\]")
SECTION = re.compile(r"^## (.+?)\s*$", re.MULTILINE)
SCHEMA = """
CREATE TABLE reels (
    id TEXT PRIMARY KEY, plateforme TEXT, auteur TEXT, date TEXT, duree_s INTEGER,
    titre TEXT, phrase TEXT, fiabilite TEXT, source TEXT
);
CREATE TABLE reel_theme (reel TEXT, theme TEXT);
CREATE TABLE reel_entite (reel TEXT, nom TEXT, type TEXT);
CREATE TABLE affirmations (reel TEXT, ts INTEGER, texte TEXT);
CREATE INDEX i_theme ON reel_theme(theme);
CREATE INDEX i_entite ON reel_entite(nom);
CREATE VIRTUAL TABLE recherche USING fts5(
    reel UNINDEXED, ts UNINDEXED, origine UNINDEXED, texte,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


def secondes(horodatage: str) -> int | None:
    """`[01:07]` ou `[1:02:03]` -> secondes. None si ce n'est pas un horodatage."""
    m = HORODATAGE.search(horodatage)
    if not m:
        return None
    heures, minutes, sec = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    return heures * 3600 + minutes * 60 + sec


def _sections(texte: str) -> dict[str, str]:
    reperes = list(SECTION.finditer(texte))
    return {
        m.group(1): texte[m.end() : reperes[i + 1].start() if i + 1 < len(reperes) else None].strip()
        for i, m in enumerate(reperes)
    }


def _utile(texte: str) -> str:
    """Vide les sections laissées en `*(rien d'exploitable)*` : rien à indexer."""
    texte = texte.strip()
    return "" if texte.startswith("*(") else texte


def _phrase(texte: str) -> str:
    m = re.search(r"\[!abstract\][^\n]*\n((?:>[^\n]*\n?)+)", texte)
    return re.sub(r"^>\s?", "", m.group(1), flags=re.MULTILINE).strip() if m else ""


def _entites(section: str) -> list[tuple[str, str]]:
    lignes = []
    for ligne in section.splitlines():
        cellules = [c.strip() for c in ligne.strip().strip("|").split("|")]
        if len(cellules) == 2 and cellules[0] not in ("Type", "") and not set(cellules[0]) <= {"-", ":"}:
            lignes.append((cellules[1], cellules[0]))
    return lignes


def _passages(section: str) -> list[tuple[int, str]]:
    """Lignes `[MM:SS] texte` d'une section -> (secondes, texte). Les lignes sans repère sont ignorées."""
    passages = []
    for ligne in section.splitlines():
        ligne = ligne.lstrip("-* ").strip()
        m = HORODATAGE.match(ligne)
        if m:
            reste = ligne[m.end() :].lstrip(" —-:").strip()
            if reste:
                passages.append((secondes(m.group(0)), reste))
    return passages


def lire_fiche(chemin: Path) -> dict:
    texte = chemin.read_text(encoding="utf-8", errors="ignore")
    entete = lire_frontmatter(texte)
    sections = _sections(texte)
    duree = str(entete.get("duree_s") or "0")
    return {
        "id": chemin.stem,
        "plateforme": entete.get("plateforme", ""),
        "auteur": entete.get("auteur", ""),
        "date": entete.get("date_publication", ""),
        "duree_s": int(duree) if duree.isdigit() else 0,
        "titre": titre_de(texte, chemin.stem),
        "phrase": _phrase(texte),
        "fiabilite": entete.get("fiabilite", ""),
        "source": entete.get("source", ""),
        "themes": entete.get("themes") or [],
        "entites": _entites(sections.get("Entités citées", "")),
        "ce_que_dit": _utile(sections.get("Ce que dit le reel", "")),
        "affirmations": _passages(_utile(sections.get("Affirmations vérifiables", ""))),
        "legende": _utile(sections.get("Légende du post", "")),
        "transcript": _passages(sections.get("Transcript", "")),
    }


def construire_index(vault: Path) -> Path:
    """Reconstruit `graphe.db` depuis les fiches. Retourne son chemin."""
    base = vault / "graphe.db"
    base.unlink(missing_ok=True)
    con = sqlite3.connect(base)
    con.executescript(SCHEMA)

    for chemin in sorted((vault / "fiches").glob("*.md")):
        f = lire_fiche(chemin)
        i = f["id"]
        con.execute(
            "INSERT INTO reels VALUES (?,?,?,?,?,?,?,?,?)",
            (i, f["plateforme"], f["auteur"], f["date"], f["duree_s"], f["titre"], f["phrase"], f["fiabilite"], f["source"]),
        )
        con.executemany("INSERT INTO reel_theme VALUES (?,?)", [(i, t) for t in f["themes"]])
        con.executemany("INSERT INTO reel_entite VALUES (?,?,?)", [(i, n, t) for n, t in f["entites"]])
        con.executemany("INSERT INTO affirmations VALUES (?,?,?)", [(i, s, t) for s, t in f["affirmations"]])

        lignes = [(i, 0, "synthese", f["titre"] + ". " + f["phrase"])]
        if f["ce_que_dit"]:
            lignes.append((i, 0, "synthese", f["ce_que_dit"]))
        lignes += [(i, s, "affirmation", t) for s, t in f["affirmations"]]
        lignes += [(i, s, "parole", t) for s, t in f["transcript"]]
        if f["legende"]:
            lignes.append((i, 0, "legende", f["legende"]))
        con.executemany("INSERT INTO recherche VALUES (?,?,?,?)", lignes)

    con.commit()
    con.close()
    return base


def ouvrir(vault: Path) -> sqlite3.Connection:
    """Ouvre l'index, le reconstruit s'il manque ou si une fiche est plus récente."""
    base = vault / "graphe.db"
    fiches = list((vault / "fiches").glob("*.md"))
    if not base.exists() or any(f.stat().st_mtime > base.stat().st_mtime for f in fiches):
        construire_index(vault)
    con = sqlite3.connect(base)
    con.row_factory = sqlite3.Row
    return con


def reels_du_domaine(con: sqlite3.Connection, themes: tuple[str, ...]) -> set[str]:
    marques = ",".join("?" * len(themes))
    return {r[0] for r in con.execute(f"SELECT DISTINCT reel FROM reel_theme WHERE theme IN ({marques})", themes)}
