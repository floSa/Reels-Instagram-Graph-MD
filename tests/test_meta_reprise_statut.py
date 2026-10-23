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


class Reponse:
    def __init__(self, donnees):
        import io

        self.flux = io.BytesIO(json.dumps(donnees).encode())

    def read(self, *a):
        return self.flux.read(*a)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def media(code, collections, produit="clips", auteur="un_compte"):
    return {"media": {"code": code, "product_type": produit, "saved_collection_ids": collections, "user": {"username": auteur}, "caption": {"text": "légende " + code}}}


class TestLive:
    def test_identifiant_depuis_l_url(self):
        from reels_graph_md import live

        assert live.identifiant_collection("https://www.instagram.com/moi/saved/politique/17912345678901234/") == "17912345678901234"
        assert live.identifiant_collection("123") == "123"
        with pytest.raises(live.ErreurLive):
            live.identifiant_collection("https://www.instagram.com/moi/")

    def test_lit_les_collections_de_chaque_enregistrement(self):
        from reels_graph_md import live

        pages = iter(
            [
                {"items": [media("AAA", ["11"]), media("BBB", [], "feed")], "more_available": True, "next_max_id": "9"},
                {"items": [media("AAA", ["11"]), media("CCC", ["11", "22"])]},
            ]
        )
        trouves = live.lire_enregistres({"sessionid": "x"}, pause=0, ouvrir=lambda r, timeout: Reponse(next(pages)))
        assert [(e["url"], e["collections"]) for e in trouves] == [
            ("https://instagram.com/reel/AAA", ["11"]),
            ("https://instagram.com/p/BBB", []),
            ("https://instagram.com/reel/CCC", ["11", "22"]),
        ]
        assert trouves[0]["auteur"] == "un_compte"

    def test_incremental_s_arrete_a_la_premiere_page_sans_nouveaute(self):
        from reels_graph_md import live

        appels = []

        def ouvrir(requete, timeout):
            appels.append(requete.full_url)
            return Reponse({"items": [media("AAA", ["11"])], "more_available": True, "next_max_id": "9"})

        trouves = live.lire_enregistres({"sessionid": "x"}, connus={"https://instagram.com/reel/AAA"}, pause=0, ouvrir=ouvrir)
        assert len(appels) == 1 and len(trouves) == 1

    def test_429_s_arrete_sans_insister(self):
        import urllib.error

        from reels_graph_md import live

        appels = []

        def refuse(requete, timeout):
            appels.append(1)
            raise urllib.error.HTTPError(requete.full_url, 429, "Too Many", {}, None)

        with pytest.raises(live.ErreurLive, match="429"):
            live.lire_enregistres({"sessionid": "x"}, pause=0, ouvrir=refuse)
        assert len(appels) == 1


class TestSync:
    def test_aligner_garde_la_forme_deja_connue(self):
        from reels_graph_md import sync

        connus = {"AAA": "https://instagram.com/p/AAA"}
        assert sync.aligner(["https://instagram.com/reel/AAA", "https://instagram.com/reel/ZZZ"], connus) == [
            "https://instagram.com/p/AAA",
            "https://instagram.com/reel/ZZZ",
        ]

    def test_description_des_collections(self):
        from reels_graph_md import sync

        magasin = {
            "https://instagram.com/reel/AAA": {"collections": ["11"], "auteur": "x", "legende": "vote à l'Assemblée"},
            "https://instagram.com/reel/BBB": {"collections": ["11"], "auteur": "x", "legende": "autre"},
            "https://instagram.com/reel/CCC": {"collections": [], "auteur": "y", "legende": "rien"},
        }
        texte = sync.decrire_collections(magasin, {"AAA"})
        assert "Collection 11 — 2 reels, dont 1 déjà dans ce vault" in texte
        assert "1 hors collection" in texte


# Structure réelle de saved_collections.json, relevée dans un importeur écrit contre
# un vrai export (danfox127-dotcom/TikTokAnalyzer) : nom dans label_values, reels dans un groupe « Media ».
def collection_reelle(libelle_nom):
    return [
        {
            "timestamp": 1785605461,
            "media": [],
            "label_values": [
                {"label": libelle_nom, "value": "politique"},
                {
                    "title": "Media",
                    "dict": [
                        {"dict": [{"label": "URL", "value": "https://www.instagram.com/reel/AAA111/", "href": "https://www.instagram.com/reel/AAA111/"}]},
                        {"dict": [{"label": "URL", "value": "https://www.instagram.com/reel/BBB222/"}, {"label": libelle_nom, "value": "un_compte"}]},
                    ],
                },
            ],
        }
    ]


@pytest.mark.parametrize("libelle", ["Name", "Nom"])
def test_structure_reelle_de_saved_collections(tmp_path, libelle):
    deposer(tmp_path, collection_reelle(libelle))
    urls, _ = meta.liens_de_collection(tmp_path, "Politique")
    assert urls == ["https://instagram.com/reel/AAA111", "https://instagram.com/reel/BBB222"]


class TestRecolte:
    def test_ajoute_les_nouveaux_et_ecrit_les_fichiers(self, tmp_path, monkeypatch):
        from reels_graph_md import live, recolte

        monkeypatch.setattr(recolte, "session_instagram", lambda: {})
        (tmp_path / "collections.json").write_text('{"11": "politique"}', encoding="utf-8")
        lus = [
            {"url": "https://instagram.com/reel/AAA", "collections": ["11"], "auteur": "x", "legende": "l"},
            {"url": "https://instagram.com/p/BBB", "collections": [], "auteur": "y", "legende": ""},
        ]
        nouveaux, erreur = recolte.recolter(tmp_path, aujourdhui="2026-10-23", lire=lambda c, connus: lus)
        assert len(nouveaux) == 2 and erreur is None
        assert "politique" in (tmp_path / "enregistres.csv").read_text(encoding="utf-8-sig")
        assert "[politique] https://instagram.com/reel/AAA" in (tmp_path / "nouveaux.md").read_text(encoding="utf-8")

        nouveaux, _ = recolte.recolter(tmp_path, aujourdhui="2026-10-30", lire=lambda c, connus: lus)
        assert nouveaux == []  # rien de nouveau, et vu_le garde la première date
        assert json.loads((tmp_path / "enregistres.json").read_text(encoding="utf-8"))["https://instagram.com/reel/AAA"]["vu_le"] == "2026-10-23"

    def test_un_429_conserve_ce_qui_a_ete_lu(self, tmp_path, monkeypatch):
        from reels_graph_md import live, recolte

        monkeypatch.setattr(recolte, "session_instagram", lambda: {})

        def lire(cookies, connus):
            raise live.ErreurLive("429", [{"url": "https://instagram.com/reel/AAA", "collections": ["11"], "auteur": "x", "legende": ""}])

        nouveaux, erreur = recolte.recolter(tmp_path, lire=lire)
        assert nouveaux == ["https://instagram.com/reel/AAA"] and erreur == "429"
