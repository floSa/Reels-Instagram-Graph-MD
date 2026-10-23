"""Tests de l'index SQLite, de la recherche et de la carte de domaine."""

import os
import time

import pytest

from reels_graph_md import carte, chercher, index

FICHE = """---
source: "https://instagram.com/reel/ABC"
plateforme: "instagram"
auteur: "Un Compte"
date_publication: "2026-06-04"
duree_s: 90
fiabilite: "haute"
themes: ["politique", "histoire"]
entites: ["Marc Bloch", "Rassemblement national"]
---

# Vérification d'une accusation historique

> [!abstract] En une phrase
> Le reel conteste une filiation politique.

## Ce que dit le reel

Le reel affirme que l'accusé avait quitté le parti [00:17].

## Affirmations vérifiables

- [00:17] — le reel affirme que l'accusé avait quitté le parti avant 1944

## Entités citées

| Type | Nom |
|---|---|
| Personne | Marc Bloch |
| Parti | Rassemblement national |

## Légende du post

Une légende.

## Transcript

[00:00] Introduction sur la Gestapo.
[01:07] Anti-bolchevique, il est rattaché à deux ans de ses débuts.

## Liens

Thèmes : [[politique]]
"""

FICHE_VIDE = FICHE.replace("politique", "cuisine").replace("ABC", "XYZ").replace('"Un Compte"', '"Autre"')


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "fiches").mkdir()
    (tmp_path / "fiches" / "insta_ABC.md").write_text(FICHE, encoding="utf-8")
    (tmp_path / "fiches" / "insta_XYZ.md").write_text(FICHE_VIDE, encoding="utf-8")
    return tmp_path


class TestSecondes:
    def test_minutes_secondes(self):
        assert index.secondes("[01:07]") == 67

    def test_avec_heures(self):
        assert index.secondes("[1:02:03]") == 3723

    def test_pas_un_horodatage(self):
        assert index.secondes("rien") is None


class TestLireFiche:
    def test_extrait_ce_que_le_maillage_ignore(self, vault):
        f = index.lire_fiche(vault / "fiches" / "insta_ABC.md")
        assert f["phrase"] == "Le reel conteste une filiation politique."
        assert f["entites"] == [("Marc Bloch", "Personne"), ("Rassemblement national", "Parti")]
        assert f["affirmations"][0][0] == 17
        assert [s for s, _ in f["transcript"]] == [0, 67]

    def test_section_vide_n_est_pas_indexee(self):
        assert index._utile("*(rien d'exploitable)*") == ""


class TestRecherche:
    def test_trouve_le_passage_avec_son_repere(self, vault):
        con = index.ouvrir(vault)
        resultat = chercher.chercher(con, "gestapo")
        assert resultat[0]["reel"]["id"] == "insta_ABC"
        parole = [p for p in resultat[0]["passages"] if p["origine"] == "parole"]
        assert parole[0]["ts"] == 0

    def test_accents_et_casse_ignores(self, vault):
        con = index.ouvrir(vault)
        assert chercher.chercher(con, "VÉRIFICATION")

    def test_filtre_de_domaine(self, vault):
        con = index.ouvrir(vault)
        politique = index.reels_du_domaine(con, ("politique",))
        assert politique == {"insta_ABC"}
        assert {r["reel"]["id"] for r in chercher.chercher(con, "gestapo", politique)} == {"insta_ABC"}

    def test_requete_hostile_ne_casse_pas_fts(self, vault):
        con = index.ouvrir(vault)
        assert chercher.chercher(con, 'a" OR (b') is not None
        assert chercher.chercher(con, "!!!") == []

    def test_repli_sur_ou_quand_et_ne_trouve_rien(self, vault):
        con = index.ouvrir(vault)
        assert chercher.chercher(con, "gestapo inexistantmot")


class TestIndex:
    def test_reconstruit_quand_une_fiche_est_plus_recente(self, vault):
        index.ouvrir(vault).close()
        nouvelle = vault / "fiches" / "insta_NEW.md"
        nouvelle.write_text(FICHE.replace("ABC", "NEW"), encoding="utf-8")
        futur = time.time() + 10  # la résolution des mtime ne doit pas rendre le test aléatoire
        os.utime(nouvelle, (futur, futur))
        con = index.ouvrir(vault)
        assert con.execute("SELECT COUNT(*) FROM reels").fetchone()[0] == 3


class TestCarte:
    def test_ecrit_carte_et_comptes_idempotent(self, vault):
        carte.ecrire_carte(vault, "politique", ("politique",))
        premier = (vault / "cartes" / "politique.md").read_text(encoding="utf-8")
        assert "[[Marc Bloch]]" in premier and "### Parti" in premier
        assert (vault / "comptes" / "Un Compte.md").exists()
        assert not (vault / "comptes" / "Autre.md").exists()  # hors domaine

        carte.ecrire_carte(vault, "politique", ("politique",))
        assert (vault / "cartes" / "politique.md").read_text(encoding="utf-8") == premier

    def test_type_dominant(self):
        assert carte.type_dominant(["Personne", "Parti", "Personne"]) == "Personne"
