"""Lecture en direct des enregistrés Instagram, avec leurs collections.

L'export Meta de ce compte ne contient ni les enregistrements récents ni les
collections. La route `/api/v1/feed/saved/posts/` (celle de gallery-dl), appelée
avec la session Firefox, rend chaque enregistrement **avec les identifiants des
collections où il est rangé** (`saved_collection_ids`) : vérifié sur ce compte.
Une seule lecture donne donc l'adresse et la collection de chaque reel.

Les enregistrements sortent du plus récent au plus ancien : un passage
hebdomadaire s'arrête dès qu'une page ne contient plus rien de nouveau.

Ce que ce module ne fait pas, volontairement :

- connaître le *nom* d'une collection (aucune route testée ne le donne ; on
  décrit chaque identifiant et c'est l'utilisateur qui le nomme, une fois) ;
- insister après un 429. Instagram rate-limite vite : sur ce compte, une
  dizaine de requêtes d'essai ont suffi. On s'arrête et on le dit.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

COOKIES_UTILES = ("sessionid", "csrftoken", "ds_user_id", "mid", "ig_did")
APP_ID = "936619743392459"  # identifiant public de l'app web Instagram


class ErreurLive(Exception):
    def __init__(self, message: str, partiels: list[dict] | None = None):
        super().__init__(message)
        self.partiels = partiels or []  # ce qui avait été lu avant l'arrêt : à conserver


def identifiant_collection(texte: str) -> str:
    """Identifiant numérique d'une collection, depuis son URL ou l'identifiant seul."""
    texte = texte.strip()
    if texte.isdigit():
        return texte
    m = re.search(r"/saved/[^/?#]+/(\d+)", texte)
    if not m:
        raise ErreurLive("Identifiant de collection illisible : un nombre, ou l'URL .../saved/<nom>/<identifiant>/.")
    return m.group(1)


def cookies_firefox(profil: Path) -> dict[str, str]:
    """Cookies Instagram du profil Firefox. La base est copiée : Firefox n'a pas à être fermé."""
    copie = Path(tempfile.mkdtemp()) / "cookies.sqlite"
    try:
        shutil.copy(profil / "cookies.sqlite", copie)
        wal = profil / "cookies.sqlite-wal"
        if wal.exists():
            shutil.copy(wal, copie.with_name("cookies.sqlite-wal"))
        con = sqlite3.connect(copie)
        cookies = dict(con.execute("SELECT name, value FROM moz_cookies WHERE host LIKE '%instagram.com'"))
        con.close()
    finally:
        shutil.rmtree(copie.parent, ignore_errors=True)
    if "sessionid" not in cookies:
        raise ErreurLive("Pas de session Instagram dans ce profil Firefox : connecte-toi sur instagram.com.")
    return {k: v for k, v in cookies.items() if k in COOKIES_UTILES}


def url_du_post(media: dict) -> str:
    genre = "reel" if media.get("product_type") == "clips" else "p"
    return f"https://instagram.com/{genre}/{media['code']}"


def lire_enregistres(
    cookies: dict[str, str],
    *,
    connus: set[str] | None = None,
    pause: float = 5.0,
    ouvrir=urllib.request.urlopen,
) -> list[dict]:
    """Enregistrements du plus récent au plus ancien : url, collections, auteur, légende.

    Avec `connus` (URLs déjà vues), on s'arrête à la première page qui ne contient
    rien de nouveau : une lecture hebdomadaire coûte une requête, pas dix.
    """
    entetes = {
        "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
        "X-IG-App-ID": APP_ID,
        "X-CSRFToken": cookies.get("csrftoken", ""),
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.instagram.com/",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
    }
    trouves: list[dict] = []
    vus: set[str] = set()
    curseur = ""
    while True:
        adresse = "https://www.instagram.com/api/v1/feed/saved/posts/?count=50"
        if curseur:
            adresse += f"&max_id={curseur}"
        try:
            with ouvrir(urllib.request.Request(adresse, headers=entetes), timeout=30) as reponse:
                donnees = json.load(reponse)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise ErreurLive(
                    "Instagram rate-limite (429). Rien d'autre n'a été demandé : réessaie dans une heure.", trouves
                ) from exc
            if exc.code in (400, 401, 403):
                raise ErreurLive(f"Instagram refuse la session ({exc.code}) : reconnecte-toi sur instagram.com dans Firefox.", trouves) from exc
            raise ErreurLive(f"Instagram a répondu {exc.code}.", trouves) from exc

        nouveaux_sur_la_page = 0
        for element in donnees.get("items", []):
            media = element.get("media") or element
            if not media.get("code"):
                continue
            url = url_du_post(media)
            if url in vus:
                continue
            vus.add(url)
            if connus is None or url not in connus:
                nouveaux_sur_la_page += 1
            trouves.append(
                {
                    "url": url,
                    "collections": [str(c) for c in media.get("saved_collection_ids") or []],
                    "auteur": (media.get("user") or {}).get("username", ""),
                    "legende": ((media.get("caption") or {}).get("text") or "")[:160],
                }
            )
        print(f"  page lue : {len(trouves)} enregistrements, dont {nouveaux_sur_la_page} nouveaux", file=sys.stderr, flush=True)
        if not donnees.get("more_available") or not donnees.get("next_max_id"):
            return trouves
        if connus is not None and not nouveaux_sur_la_page:
            return trouves
        curseur = str(donnees["next_max_id"])
        time.sleep(pause)
