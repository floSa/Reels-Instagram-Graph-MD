"""Tableau de bord d'un vault thématique : où on en est, ce qui reste à faire.

    uv run reels-statut --vault ~/Documents/Vault-Politique

Affiché dans le terminal et écrit dans `STATUT.md` à la racine du vault, pour le
lire aussi depuis Obsidian. Ne touche à rien d'autre.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .journal import Journal
from .verifier import auditer

AGE_EXPORT_MAX_JOURS = 30


def lire_json(chemin: Path) -> dict:
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def calculer(vault: Path, maintenant: datetime | None = None) -> dict:
    """Les chiffres du tableau de bord, sans rien afficher."""
    maintenant = maintenant or datetime.now()
    config = lire_json(vault / "theme.json")
    cible = lire_json(vault / "cible.json")
    carnet = Journal(vault / "journal.json")
    audit = auditer(vault) if carnet.entrees else {}

    urls = cible.get("urls", [])
    etat = {"traites": 0, "a_faire": 0, "echecs": 0, "impossibles": 0}
    for url in urls:
        statut = carnet.entrees.get(url, {}).get("statut")
        cle = {"ok": "traites", "echec": "echecs", "impossible": "impossibles"}.get(statut, "a_faire")
        etat[cle] += 1

    dans_cible = set(urls)
    hors_cible = [
        u
        for u, e in carnet.entrees.items()
        if e.get("statut") == "ok" and not e.get("alias_de") and u not in dans_cible
    ]

    dernier = cible.get("dernier_enregistrement")
    age = (maintenant - datetime.fromisoformat(dernier)).days if dernier else None

    return {
        "theme": config.get("nom", vault.name),
        "collection": config.get("collection"),
        "cible_connue": bool(urls),
        "taille_cible": len(urls),
        **etat,
        "repris": sum(1 for e in carnet.entrees.values() if e.get("reprise") and e.get("statut") == "ok"),
        "hors_cible": len(hors_cible),
        "total_fiches": sum(1 for e in carnet.entrees.values() if e.get("statut") == "ok" and not e.get("alias_de")),
        "a_enrichir": len(audit.get("a_enrichir", [])),
        "a_verifier": len(audit.get("a_verifier", [])),
        "anomalies": sum(len(v) for k, v in audit.items() if k not in ("a_enrichir", "a_verifier")),
        "export": cible.get("export"),
        "dernier_enregistrement": dernier,
        "age_export_jours": age,
        "lu_le": cible.get("lu_le"),
        "erreurs_recentes": [
            f"{u} — {e.get('erreur', '')}" for u, e in carnet.entrees.items() if e.get("statut") == "echec"
        ][:5],
    }


def prochaines_actions(s: dict, vault: Path) -> list[str]:
    actions = []
    if not s["cible_connue"]:
        actions.append(
            "La collection n'a pas encore été lue : dépose l'export Meta (avec les collections) "
            f"puis `uv run reels-sync --vault {vault} --export <dossier ou zip>`."
        )
    elif s["age_export_jours"] is not None and s["age_export_jours"] > AGE_EXPORT_MAX_JOURS:
        actions.append(
            f"L'export date de {s['age_export_jours']} jours : les reels enregistrés depuis n'y sont pas. "
            "Refais un export Meta (Centre des comptes → Exporter vos informations → Enregistrés), "
            "puis relance reels-sync."
        )
    if s["a_faire"] or s["echecs"]:
        actions.append(
            f"{s['a_faire'] + s['echecs']} reel(s) à traiter : `uv run reels-sync --vault {vault} --cookies firefox`."
        )
    if s["a_enrichir"]:
        actions.append(
            f"{s['a_enrichir']} fiche(s) à enrichir : `uv run reels-verifier --vault {vault} --a-enrichir`, "
            "puis docs/ENRICHISSEMENT.md."
        )
    if s["anomalies"]:
        actions.append(f"{s['anomalies']} incohérence(s) journal/disque : `uv run reels-verifier --vault {vault}`.")
    return actions or ["Rien à faire : le vault est à jour avec l'export."]


def rendre(s: dict, vault: Path) -> str:
    cible = (
        f"{s['taille_cible']} reels dans la collection « {s['collection']} »"
        if s["cible_connue"]
        else "collection pas encore lue"
    )
    lignes = [
        f"# Statut — {s['theme']}",
        "",
        f"Cible : {cible}.",
        "",
        "| | |",
        "|---|---:|",
        f"| Fiches dans le vault | {s['total_fiches']} |",
        f"| … dont reprises d'un autre vault | {s['repris']} |",
        f"| Cible : traités | {s['traites']} / {s['taille_cible']} |",
        f"| Cible : à faire | {s['a_faire']} |",
        f"| Cible : échecs (retentés au prochain sync) | {s['echecs']} |",
        f"| Cible : hors d'atteinte (supprimés, privés) | {s['impossibles']} |",
        f"| Hors collection (repris par thème) | {s['hors_cible']} |",
        f"| Fiches à enrichir | {s['a_enrichir']} |",
        f"| Fiches à vérifier à la main | {s['a_verifier']} |",
        "",
    ]
    if s["export"]:
        fraicheur = f"dernier enregistrement vu le {s['dernier_enregistrement'][:10]}"
        if s["age_export_jours"] is not None:
            fraicheur += f" ({s['age_export_jours']} jours)"
        lignes += [f"Export : `{s['export']}` — {fraicheur}.", ""]
    if s["erreurs_recentes"]:
        lignes += ["Derniers échecs :", ""] + [f"- {e}" for e in s["erreurs_recentes"]] + [""]
    lignes += ["## Prochaines actions", ""] + [f"- {a}" for a in prochaines_actions(s, vault)]
    return "\n".join(lignes) + "\n"


def ecrire_statut(vault: Path) -> str:
    texte = rendre(calculer(vault), vault)
    (vault / "STATUT.md").write_text(texte, encoding="utf-8")
    return texte


def main() -> int:
    parseur = argparse.ArgumentParser(prog="reels-statut", description="Où en est un vault thématique.")
    parseur.add_argument("--vault", required=True)
    args = parseur.parse_args()

    vault = Path(args.vault).expanduser()
    if not vault.is_dir():
        print(f"Vault introuvable : {vault}", file=sys.stderr)
        return 2
    print(ecrire_statut(vault))
    return 0


if __name__ == "__main__":
    sys.exit(main())
