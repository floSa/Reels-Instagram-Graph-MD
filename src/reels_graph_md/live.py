"""Lecture en direct d'une collection d'enregistrés Instagram, sans passer par l'export.

Même route que celle qu'utilise gallery-dl (`/api/v1/feed/collection/<id>/posts/`),
avec la session Firefox de l'utilisateur. Vérifié : `/api/v1/feed/saved/posts/`
répond avec cette session et rend les enregistrements récents, que l'export Meta
ne contient pas encore.

Ce que ce module ne fait pas, volontairement :

- lister les collections (les routes testées répondent 404 ou « fail » ; l'identifiant
  se lit dans l'URL de la collection ouverte dans le navigateur) ;
- insister après un 429. Instagram rate-limite vite : sur ce compte, une
  dizaine de requêtes d'essai ont suffi. On s'arrête et on le dit.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

URL_COLLECTION = re.compile(r"instagram\.com/(?:(?P<compte>[\w.]+)/)?saved/(?P<nom>[^/?#]+)/(?P<id>\d+)")
COOKIES_UTILES = ("sessionid", "csrftoken", "ds_user_id", "mid", "ig_did")
APP_ID = "936619743392459"  # identifiant public de l'app web Instagram


class ErreurLive(Exception):
    pass


def identifiant_collection(texte: str) -> str:
    """Identifiant numérique d'une collection, depuis son URL ou l'identifiant seul."""
    texte = texte.strip()
    if texte.isdigit():
        return texte
    m = URL_COLLECTION.search(texte)
    if not m:
        raise ErreurLive(
            "Identifiant de collection illisible. Ouvre la collection dans Instagram (web) et "
            "colle son URL, de la forme instagram.com/<compte>/saved/<nom>/<identifiant>/"
        )
    return m.group("id")


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


def lire_collection(
    collection_id: str, cookies: dict[str, str], *, pause: float = 3.0, ouvrir=urllib.request.urlopen
) -> list[str]:
    """URLs des posts de la collection, du plus récent au plus ancien."""
    entetes = {
        "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
        "X-IG-App-ID": APP_ID,
        "X-CSRFToken": cookies.get("csrftoken", ""),
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.instagram.com/",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
    }
    urls: list[str] = []
    curseur = ""
    while True:
        adresse = f"https://www.instagram.com/api/v1/feed/collection/{collection_id}/posts/?count=50"
        if curseur:
            adresse += f"&max_id={curseur}"
        try:
            with ouvrir(urllib.request.Request(adresse, headers=entetes), timeout=30) as reponse:
                donnees = json.load(reponse)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise ErreurLive("Instagram rate-limite (429). Rien d'autre n'a été demandé : réessaie dans une heure.") from exc
            raise ErreurLive(f"Instagram a répondu {exc.code} pour la collection {collection_id}.") from exc
        for element in donnees.get("items", []):
            media = element.get("media") or element
            if media.get("code") and url_du_post(media) not in urls:
                urls.append(url_du_post(media))
        if not donnees.get("more_available") or not donnees.get("next_max_id"):
            return urls
        curseur = str(donnees["next_max_id"])
        time.sleep(pause)
