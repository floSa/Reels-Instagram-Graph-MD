"""Ingestion : des exports de plateformes vers les fiches du vault.

    uv run reels-ingest exports/*.json --vault ~/Vault --cookies firefox --limite 10

Le script reprend où il s'est arrêté. On peut le couper à tout moment.

Ordre volontaire des opérations : le lot est filtré par le journal **avant** de
vérifier le serveur Whisper, et le serveur est vérifié **avant** le premier
téléchargement. Rien à faire → on ne dérange personne ; du travail mais pas de
serveur → on le dit tout de suite plutôt qu'après 40 minutes de téléchargements.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from . import fiche, inbox, liens, moteur, ytdlp
from .journal import Journal

PAUSE_DEFAUT = 3.0

# Un échec isolé est banal — post supprimé ou passé en privé, on en attend
# environ un tiers sur un stock ancien. Une série d'échecs consécutifs, elle,
# ne l'est pas : c'est un rate-limit, une session expirée ou le serveur de
# transcription tombé. Continuer ne ferait qu'aggraver le blocage et brûler la
# liste en la marquant en échec.
ECHECS_CONSECUTIFS_MAX = 5


def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def _preparer_vault(racine: Path) -> dict[str, Path]:
    dossiers = {
        "fiches": racine / "fiches",
        "reels": racine / "reels",
        "themes": racine / "themes",
        "entites": racine / "entites",
        "temp": racine / ".temp",
    }
    for chemin in dossiers.values():
        chemin.mkdir(parents=True, exist_ok=True)
    return dossiers


def _cle_native(plateforme: str, meta: dict) -> str | None:
    """Identité du reel telle que la plateforme la déclare, ou None si absente."""
    natif = str(meta.get("id") or "").strip()
    return f"{plateforme}:{natif}" if natif else None


def _traiter(
    url: str,
    dossiers: dict[str, Path],
    carnet: Journal,
    cookies: str | None,
    langue: str,
) -> dict:
    """Traite un reel de bout en bout. Lève en cas d'échec, le journal tranche."""
    nom = liens.identifiant(url)
    plateforme = liens.plateforme_de(url) or "inconnu"
    temp = dossiers["temp"] / nom
    temp.mkdir(parents=True, exist_ok=True)

    try:
        try:
            meta = ytdlp.metadonnees(url, cookies)
        except ytdlp.ErreurYtdlp as exc:
            # `--dump-json` échoue en entier sur un carrousel photo : yt-dlp
            # répond « No video formats found » pour chaque élément et ne rend
            # rien, pas même la légende. Rien à écrire, et rien à réessayer —
            # la cause est structurelle, pas transitoire.
            if "no video formats" not in str(exc).lower():
                raise
            return {"genre": "carrousel photo", "impossible": "aucune donnée exploitable"}

        # Deuxième passe de déduplication, celle qui compte : la canonicalisation
        # d'URL ne peut pas rapprocher un lien court de sa forme résolue. Ce
        # contrôle a lieu après l'appel aux métadonnées — le moins cher des
        # appels — et avant le téléchargement, qui est le poste coûteux.
        natif = _cle_native(plateforme, meta)
        if natif:
            deja = carnet.url_pour_natif(natif)
            if deja is not None and deja != url:
                return {
                    "natif": natif,
                    "alias_de": deja,
                    "fiche": carnet.entrees.get(deja, {}).get("fiche", ""),
                    "genre": "alias",
                }

        def sans_video(raison: str) -> dict:
            """Fiche réduite à la légende, pour un post dont la vidéo est hors d'atteinte.

            Sans analyse d'image (hors périmètre, décidé), il ne reste que la
            légende. On l'écrit quand même — elle est souvent substantielle sur
            du contenu informatif — mais la fiche est étiquetée `legende_seule`.
            """
            contenu = fiche.construire(
                identifiant=nom,
                url=url,
                plateforme=plateforme,
                meta=meta,
                transcript="",
                source_transcript=f"aucune ({raison})",
            )
            fiche.ecrire(dossiers["fiches"], nom, contenu)
            return {
                "fiche": f"{nom}.md",
                "genre": raison,
                "fiabilite": fiche.evaluer_fiabilite("", meta.get("description") or ""),
                "natif": natif,
            }

        # Pas de durée annoncée : c'est un carrousel photo.
        if not meta.get("duration"):
            return sans_video("carrousel")

        try:
            video_temp, sous_titres = ytdlp.telecharger(url, temp, nom, cookies)
        except ytdlp.ErreurYtdlp as exc:
            # Certains posts annoncent une durée sans exposer le moindre format
            # téléchargeable — un carrousel contenant une vidéo, typiquement.
            # yt-dlp répond « No video formats found ». Les compter en échec les
            # ferait retenter à chaque relance, éternellement et sans espoir :
            # on les traite comme des carrousels.
            if "no video formats" not in str(exc).lower():
                raise
            log("    aucun format vidéo exposé — fiche réduite à la légende")
            return sans_video("aucun format vidéo")

        # La vidéo est conservée : c'est elle qui permet de revoir le reel depuis
        # Obsidian même quand le post d'origine a disparu — ce qui arrive à environ
        # un tiers d'un stock ancien.
        video = dossiers["reels"] / f"{nom}{video_temp.suffix}"
        shutil.move(str(video_temp), str(video))

        segments = moteur.transcrire(video, temp / f"{nom}.mp3", langue=langue)
        transcript = fiche.formater_segments(segments)
        sources = ["whisper"]

        captions = ""
        if sous_titres is not None:
            try:
                captions = fiche.formater_segments(moteur.lire_sous_titres(sous_titres))
                if captions.strip():
                    sources.append("captions")
            except Exception as exc:  # noqa: BLE001 — une source secondaire ne bloque rien
                log(f"    sous-titres illisibles, ignorés : {exc}")

        contenu = fiche.construire(
            identifiant=nom,
            url=url,
            plateforme=plateforme,
            meta=meta,
            transcript=transcript,
            transcript_captions=captions,
            source_transcript=" + ".join(sources),
            chemin_video=f"reels/{video.name}",
        )
        fiche.ecrire(dossiers["fiches"], nom, contenu)

        return {
            "fiche": f"{nom}.md",
            "video": f"reels/{video.name}",
            "genre": "video",
            "fiabilite": fiche.evaluer_fiabilite(transcript, meta.get("description") or ""),
            "natif": natif,
        }
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def traiter_lot(
    a_faire: list[str],
    vault: Path,
    dossiers: dict[str, Path],
    carnet: Journal,
    *,
    cookies: str | None,
    langue: str,
    pause: float,
    lire_inbox: bool,
) -> int:
    """Télécharge, transcrit et écrit les fiches de `a_faire`. Code de sortie de la commande."""
    ytdlp.verifier_outils()

    if not moteur.serveur_disponible():
        log(f"Serveur Whisper injoignable sur {moteur.url_serveur()}.")
        log("Démarre-le avec claude-skills/local-whisper/speaches-up.sh, puis relance.")
        return 3

    reussites, echecs, alias, impossibles = 0, 0, 0, 0
    consecutifs, interrompu = 0, False
    fiabilites: dict[str, int] = {}

    for numero, url in enumerate(a_faire, 1):
        log(f"[{numero}/{len(a_faire)}] {url}")
        try:
            details = _traiter(url, dossiers, carnet, cookies, langue)
            consecutifs = 0
            if details.get("impossible"):
                carnet.impossible(url, f"{details['genre']} — {details['impossible']}")
                impossibles += 1
                log(f"    hors d'atteinte : {details['genre']}, ne sera plus retenté")
                if numero < len(a_faire):
                    time.sleep(pause)
                continue
            carnet.succes(url, **details)
            reussites += 1
            if details.get("alias_de"):
                alias += 1
                log(f"    doublon — même reel que {details['alias_de']}, rien téléchargé")
            else:
                niveau = details.get("fiabilite", "?")
                fiabilites[niveau] = fiabilites.get(niveau, 0) + 1
                log(f"    ok — {details['fiche']} (fiabilité : {niveau})")
        except KeyboardInterrupt:
            log("Interrompu. Le journal est à jour, relance pour reprendre.")
            return 130
        except Exception as erreur:  # noqa: BLE001 — un reel raté n'arrête pas le lot
            log(f"    échec : {erreur}")
            carnet.echec(url, str(erreur))
            echecs += 1
            consecutifs += 1
            if consecutifs >= ECHECS_CONSECUTIFS_MAX:
                log(f"{consecutifs} échecs consécutifs — arrêt du lot.")
                log("Signature d'un rate-limit, d'une session expirée ou du serveur")
                log("de transcription tombé. Attends, vérifie, puis relance : le")
                log("journal reprendra exactement ici.")
                interrompu = True
                break

        if numero < len(a_faire):
            time.sleep(pause)

    if lire_inbox:
        # Relecture de l'inbox au moment de l'archivage, jamais réécriture depuis
        # ce qu'on avait lu au départ : le raccourci mobile a pu y ajouter des
        # URLs pendant que le lot tournait.
        archivees = inbox.consommer(vault, {u for u in a_faire if carnet.est_fait(u)})
        if archivees:
            log(f"{archivees} ligne(s) déplacée(s) vers {inbox.NOM_ARCHIVE}.")

    log(f"Terminé — {reussites} réussites, {echecs} échecs.")
    if impossibles:
        log(f"Dont {impossibles} post(s) hors d'atteinte, retirés de la file.")
    if alias:
        log(f"Dont {alias} doublons détectés après résolution : aucun téléchargement refait.")
    if fiabilites:
        detail = ", ".join(f"{n} {niveau}" for niveau, n in sorted(fiabilites.items()))
        log(f"Fiabilité des fiches : {detail}")
    if fiabilites.get("vide") or fiabilites.get("legende_seule"):
        log("Les fiches `vide` / `legende_seule` n'ont pas de contenu parlé exploitable.")
    log(f"Fiches : {dossiers['fiches']}")
    if echecs:
        log("Relance la même commande pour retenter uniquement les échecs.")

    return 4 if interrompu else 0



def main() -> int:
    parseur = argparse.ArgumentParser(
        prog="reels-ingest",
        description="Transforme des exports Instagram / TikTok / Facebook en fiches Markdown.",
    )
    parseur.add_argument(
        "fichiers",
        nargs="*",
        help="exports de données (json, txt, csv, html). "
        "Facultatif : sans argument, seule l'inbox du vault est lue.",
    )
    parseur.add_argument("--vault", default=str(Path.home() / "Vault"))
    parseur.add_argument(
        "--cookies",
        default=None,
        help="navigateur (firefox) ou chemin d'un cookies.txt. "
        "Obligatoire pour Instagram et Facebook.",
    )
    parseur.add_argument("--limite", type=int, default=0, help="ne traiter que les N premiers")
    parseur.add_argument("--pause", type=float, default=PAUSE_DEFAUT, help="secondes entre deux reels")
    parseur.add_argument("--langue", default="fr", help="langue forcée pour Whisper")
    parseur.add_argument(
        "--sans-inbox",
        action="store_true",
        help=f"ne pas lire {inbox.NOM_INBOX} du vault",
    )
    parseur.add_argument(
        "--lister",
        action="store_true",
        help="afficher ce qui serait traité, sans rien télécharger",
    )
    args = parseur.parse_args()

    vault = Path(args.vault).expanduser()
    dossiers = _preparer_vault(vault)
    carnet = Journal(vault / "journal.json")

    manquants = [f for f in args.fichiers if not Path(f).exists()]
    if manquants:
        log("Fichier(s) introuvable(s) : " + ", ".join(manquants))
        return 2

    sources = [Path(f) for f in args.fichiers]
    boite = inbox.chemin_inbox(vault)
    if not args.sans_inbox and boite.exists():
        sources.append(boite)
        log(f"Boîte de réception lue : {boite}")

    if not sources:
        log(f"Aucune source. Passe un export en argument, ou alimente {boite}.")
        return 2

    trouves = liens.extraire_plusieurs(sources)
    a_faire = carnet.a_traiter(trouves)
    if args.limite:
        a_faire = a_faire[: args.limite]

    log(f"{len(trouves)} liens de contenu trouvés, {len(a_faire)} à traiter.")
    if not a_faire:
        log("Rien à faire.")
        return 0

    if args.lister:
        for url in a_faire:
            print(f"{liens.identifiant(url):32s}  {url}")
        return 0

    return traiter_lot(
        a_faire,
        vault,
        dossiers,
        carnet,
        cookies=args.cookies,
        langue=args.langue,
        pause=args.pause,
        lire_inbox=not args.sans_inbox,
    )


if __name__ == "__main__":
    sys.exit(main())
