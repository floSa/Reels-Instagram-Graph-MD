"""Tests de la lecture des collections Meta, de la reprise et du statut."""

import json
import zipfile
from datetime import datetime

import pytest

from reels_graph_md import meta, reprise, statut
from reels_graph_md.journal import Journal

# Format « label_values » (celui des exports récents, collections imbriquées).
EXPORT_IMBRIQUE = [
    {
        "timestamp": 1785605461,
        "label_values": [
            {"label": "Titre", "value": "Politique"},
            {
                "title": "Enregistrements",
                "dict": [
                    {"dict": [{"label": "URL", "value": "https://www.instagram.com/reel/AAA111/?igsh=zz"}]},
                    {"dict": [{"label": "URL", "value": "https://www.instagram.com/reel/BBB222/"}]},
                    {"dict": [{"label": "URL", "value": "https://www.instagram.com/explore/"}]},
                ],
            },
        ],
    },
    {
        "label_values": [
            {"label": "Titre", "value": "Cuisine"},
            {"title": "Enregistrements", "dict": [{"dict": [{"label": "URL", "value": "https://www.instagram.com/reel/CCC333/"}]}]},
        ]
    },
]

# Ancien format à plat : le nom de la collection est porté par chaque élément.
EXPORT_A_PLAT = {
    "saved_saved_collections": [
        {"title": "Télévision et cinéma", "string_map_data": {"Nom": {"href": "https://www.instagram.com/p/TTT999/"}}},
        {"title": "Télévision et cinéma", "string_map_data": {"Nom": {"href": "https://www.instagram.com/reel/UUU888/"}}},
    ]
}


def deposer(dossier, contenu, nom="saved_collections.json"):
    (dossier / nom).write_text(json.dumps(contenu), encoding="utf-8")


class TestCollections:
    def test_nom_insensible_casse_et_accents(self, tmp_path):
        deposer(tmp_path, EXPORT_IMBRIQUE)
        urls, _ = meta.liens_de_collection(tmp_path, " POLITIQUE ")
        assert urls == ["https://instagram.com/reel/AAA111", "https://instagram.com/reel/BBB222"]

    def test_ne_melange_pas_les_collections(self, tmp_path):
        deposer(tmp_path, EXPORT_IMBRIQUE)
        urls, _ = meta.liens_de_collection(tmp_path, "cuisine")
        assert urls == ["https://instagram.com/reel/CCC333"]

    def test_format_a_plat_et_mojibake(self, tmp_path):
        texte = json.dumps(EXPORT_A_PLAT, ensure_ascii=False).encode("utf-8").decode("latin-1")
        (tmp_path / "saved_collections.json").write_text(texte, encoding="utf-8")
        urls, _ = meta.liens_de_collection(tmp_path, "television et cinema")
        assert len(urls) == 2

    def test_collection_absente_dit_ce_qui_existe(self, tmp_path):
        deposer(tmp_path, EXPORT_IMBRIQUE)
        with pytest.raises(meta.ErreurExport, match="Cuisine"):
            meta.liens_de_collection(tmp_path, "sport")

    def test_saved_posts_seul_est_explique(self, tmp_path):
        deposer(tmp_path, EXPORT_IMBRIQUE, "saved_posts.json")
        with pytest.raises(meta.ErreurExport, match="collections"):
            meta.liens_de_collection(tmp_path, "politique")

    def test_zip_tel_quel(self, tmp_path):
        with zipfile.ZipFile(tmp_path / "export.zip", "w") as z:
            z.writestr("your_instagram_activity/saved/saved_collections.json", json.dumps(EXPORT_IMBRIQUE))
        urls, _ = meta.liens_de_collection(tmp_path / "export.zip", "politique")
        assert len(urls) == 2

    def test_dossier_sans_export(self, tmp_path):
        with pytest.raises(meta.ErreurExport):
            meta.lire_sources(tmp_path)

    def test_date_export_est_le_dernier_enregistrement(self, tmp_path):
        deposer(tmp_path, EXPORT_IMBRIQUE)
        assert meta.date_export(tmp_path) == datetime.fromtimestamp(1785605461)


def vault_source(tmp_path):
    """Un vault source avec un reel, un alias et une fiche déjà enrichie dans la cible."""
    src = tmp_path / "source"
    (src / "fiches").mkdir(parents=True)
    (src / "reels").mkdir()
    (src / "fiches" / "insta_AAA.md").write_text('---\nthemes: ["politique"]\n---\n# A\n', encoding="utf-8")
    (src / "reels" / "insta_AAA.mp4").write_bytes(b"video")
    carnet = Journal(src / "journal.json")
    carnet.succes("https://instagram.com/reel/AAA", fiche="insta_AAA.md", video="reels/insta_AAA.mp4", natif="instagram:AAA")
    carnet.succes("https://instagram.com/reel/ALIAS", alias_de="https://instagram.com/reel/AAA", fiche="insta_AAA.md")
    return src


class TestReprise:
    def test_copie_fiche_video_et_journal(self, tmp_path):
        src, cible = vault_source(tmp_path), tmp_path / "cible"
        carnet = Journal(cible / "journal.json")
        bilan = reprise.reprendre(cible, src, ["https://instagram.com/reel/AAA", "https://instagram.com/reel/ZZZ"], carnet)
        assert bilan["repris"] == 1 and bilan["absents"] == 1
        assert (cible / "reels" / "insta_AAA.mp4").read_bytes() == b"video"
        assert carnet.est_fait("https://instagram.com/reel/AAA")

    def test_ne_reecrase_jamais_une_fiche_enrichie(self, tmp_path):
        src, cible = vault_source(tmp_path), tmp_path / "cible"
        carnet = Journal(cible / "journal.json")
        reprise.reprendre(cible, src, ["https://instagram.com/reel/AAA"], carnet)
        (cible / "fiches" / "insta_AAA.md").write_text("enrichie", encoding="utf-8")
        bilan = reprise.reprendre(cible, src, ["https://instagram.com/reel/AAA"], carnet)
        assert bilan["deja"] == 1
        assert (cible / "fiches" / "insta_AAA.md").read_text(encoding="utf-8") == "enrichie"

    def test_un_alias_amene_son_original(self, tmp_path):
        src, cible = vault_source(tmp_path), tmp_path / "cible"
        carnet = Journal(cible / "journal.json")
        reprise.reprendre(cible, src, ["https://instagram.com/reel/ALIAS"], carnet)
        assert carnet.est_fait("https://instagram.com/reel/AAA") and carnet.est_fait("https://instagram.com/reel/ALIAS")
        assert (cible / "fiches" / "insta_AAA.md").exists()

    def test_selection_par_themes(self, tmp_path):
        src = vault_source(tmp_path)
        assert reprise.urls_par_themes(src, ["politique"]) == ["https://instagram.com/reel/AAA"]
        assert reprise.urls_par_themes(src, ["cuisine"]) == []


class TestStatut:
    def test_compte_les_etats_de_la_cible(self, tmp_path):
        (tmp_path / "cible.json").write_text(
            json.dumps({"collection": "p", "urls": ["u1", "u2", "u3", "u4"], "dernier_enregistrement": "2026-01-01T00:00:00"}),
            encoding="utf-8",
        )
        carnet = Journal(tmp_path / "journal.json")
        carnet.succes("u1", fiche="a.md", reprise="vieux")
        carnet.echec("u2", "boom")
        carnet.impossible("u3", "supprimé")
        s = statut.calculer(tmp_path, datetime(2026, 3, 1))
        assert (s["traites"], s["echecs"], s["impossibles"], s["a_faire"]) == (1, 1, 1, 1)
        assert s["repris"] == 1 and s["age_export_jours"] == 59

    def test_export_vieux_declenche_une_action(self, tmp_path):
        (tmp_path / "cible.json").write_text(
            json.dumps({"urls": ["u1"], "dernier_enregistrement": "2026-01-01T00:00:00"}), encoding="utf-8"
        )
        s = statut.calculer(tmp_path, datetime(2026, 6, 1))
        assert any("export" in a.lower() for a in statut.prochaines_actions(s, tmp_path))

    def test_vault_a_jour(self, tmp_path):
        (tmp_path / "cible.json").write_text(json.dumps({"urls": []}), encoding="utf-8")
        s = statut.calculer(tmp_path)
        s["cible_connue"] = True
        assert statut.prochaines_actions(s, tmp_path) == ["Rien à faire : le vault est à jour avec l'export."]


class TestLive:
    def test_identifiant_depuis_l_url(self):
        from reels_graph_md import live

        assert live.identifiant_collection("https://www.instagram.com/moi/saved/politique/17912345678901234/") == "17912345678901234"
        assert live.identifiant_collection("123") == "123"
        with pytest.raises(live.ErreurLive):
            live.identifiant_collection("https://www.instagram.com/moi/")

    def test_pagination_et_forme_des_urls(self):
        import io

        from reels_graph_md import live

        pages = iter(
            [
                {"items": [{"media": {"code": "AAA", "product_type": "clips"}}], "more_available": True, "next_max_id": "9"},
                {"items": [{"media": {"code": "BBB", "product_type": "feed"}}, {"media": {"code": "AAA", "product_type": "clips"}}]},
            ]
        )

        class Reponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def faux_ouvrir(requete, timeout):
            return Reponse(json.dumps(next(pages)).encode())

        urls = live.lire_collection("1", {"sessionid": "x"}, pause=0, ouvrir=faux_ouvrir)
        assert urls == ["https://instagram.com/reel/AAA", "https://instagram.com/p/BBB"]

    def test_429_s_arrete_sans_insister(self):
        import urllib.error

        from reels_graph_md import live

        appels = []

        def refuse(requete, timeout):
            appels.append(1)
            raise urllib.error.HTTPError(requete.full_url, 429, "Too Many", {}, None)

        with pytest.raises(live.ErreurLive, match="429"):
            live.lire_collection("1", {"sessionid": "x"}, pause=0, ouvrir=refuse)
        assert len(appels) == 1
