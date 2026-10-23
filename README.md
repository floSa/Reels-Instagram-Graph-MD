# reels-graph-md

**Transforme des reels sauvegardés (Instagram, TikTok, Facebook) en vault Obsidian consultable : une fiche Markdown par reel, la vidéo lisible dans la note, et une navigation par thème et par entité.**

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![uv](https://img.shields.io/badge/uv-package_manager-DE5FE9?logo=uv&logoColor=white)
![yt-dlp](https://img.shields.io/badge/yt--dlp-2026.07-FF0000?logo=youtube&logoColor=white)
![Whisper](https://img.shields.io/badge/Whisper-large--v3_local-412991?logo=openai&logoColor=white)

## Sommaire

- [Le problème](#le-problème)
- [Architecture](#architecture)
- [Documentation](#documentation)
- [Démarrage](#démarrage)
- [Configuration](#configuration)
- [Tests](#tests)
- [Structure du projet](#structure-du-projet)
- [Licences & composants](#licences--composants)
- [Étape 0 — récupérer ses reels sauvegardés](#étape-0--récupérer-ses-reels-sauvegardés)
- [Le vault produit](#le-vault-produit)
- [Dépannage](#dépannage)
- [Limites connues](#limites-connues)

## Le problème

Plusieurs centaines de reels sauvegardés, dont le contenu est enfermé dans de la
vidéo, donc introuvable. Impossible de retrouver *ce reel qui parlait du vote à
l'Assemblée nationale*.

**Périmètre.** Métadonnées du post (dont la légende), transcription audio locale,
vidéo conservée, maillage Obsidian.

**Hors périmètre.** Aucune analyse visuelle : pas de capture de frames, pas d'OCR,
pas de modèle de vision. Ce qui n'est ni dit à voix haute ni écrit dans la légende
n'entrera pas dans le vault — les fiches concernées sont **étiquetées** comme
telles plutôt que de laisser croire à un traitement réussi.

## Architecture

Deux commandes, aucun service tournant, aucune base de données. Le stockage est le
vault Obsidian lui-même ; le seul état structuré est `journal.json`.

| Module | Rôle |
|---|---|
| [`liens.py`](src/reels_graph_md/liens.py) | Extraction, filtrage et normalisation des URLs des exports |
| [`journal.py`](src/reels_graph_md/journal.py) | Reprise après interruption, écriture atomique |
| [`ytdlp.py`](src/reels_graph_md/ytdlp.py) | Métadonnées et téléchargement, avec détection réelle des échecs |
| [`moteur.py`](src/reels_graph_md/moteur.py) | Pont vers le moteur de transcription du skill `watch` |
| [`fiche.py`](src/reels_graph_md/fiche.py) | Assemblage du Markdown, évaluation de fiabilité |
| [`inbox.py`](src/reels_graph_md/inbox.py) | Boîte de réception du flux continu |
| [`ingest.py`](src/reels_graph_md/ingest.py) | Orchestration — commande `reels-ingest` |
| [`mailler.py`](src/reels_graph_md/mailler.py) | Notes de thème et d'entité — commande `reels-mailler` |
| [`verifier.py`](src/reels_graph_md/verifier.py) | Audit et réparation du vault — commande `reels-verifier` |
| [`sync.py`](src/reels_graph_md/sync.py) | **Point d'entrée d'un vault thématique** : lit la collection, reprend l'existant, traite le manquant — commande `reels-sync` |
| [`meta.py`](src/reels_graph_md/meta.py) | Lecture des collections d'enregistrés dans l'export Meta (dossier ou zip) |
| [`reprise.py`](src/reels_graph_md/reprise.py) | Réutilise fiches et vidéos d'un ancien vault, sans écraser |
| [`statut.py`](src/reels_graph_md/statut.py) | Tableau de bord et prochaines actions — commande `reels-statut`, écrit `STATUT.md` |
| [`index.py`](src/reels_graph_md/index.py) | Index SQLite dérivé des fiches (`graphe.db`), recherche plein texte FTS5 |
| [`chercher.py`](src/reels_graph_md/chercher.py) | Recherche dans la parole, avec l'horodatage du passage — commande `reels-chercher` |
| [`carte.py`](src/reels_graph_md/carte.py) | Carte d'un domaine (entités typées, comptes, chronologie) — commande `reels-carte` |

```mermaid
flowchart LR
  subgraph Entree[Entrees]
    exp[Exports plateformes]
  end
  subgraph Traitement
    ydl[yt-dlp] --> ff[ffmpeg]
    ff --> wh[Whisper local]
  end
  subgraph VaultObsidian[Vault Obsidian]
    fic[fiches/]
    ree[reels/]
    the[themes/ et entites/]
  end
  exp --> ydl
  ydl --> ree
  wh --> fic
  fic -->|frontmatter| the
```

> Détails : [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Documentation

| Document | Contenu |
|---|---|
| [docs/GUIDE.md](docs/GUIDE.md) | **Commence par là** — installation et utilisation de A à Z, questions fréquentes, dépannage |
| [docs/ENRICHISSEMENT.md](docs/ENRICHISSEMENT.md) | L'étape 8 : procédure, règles de rédaction, nommage des thèmes et entités |
| [docs/CADRAGE.md](docs/CADRAGE.md) | Le pourquoi : objectifs, périmètre, hypothèses, décisions, roadmap |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Le comment : composants, flux, décisions techniques, sécurité |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Règles de contribution du dépôt |

## Démarrage

**Prérequis** : Python ≥ 3.12 et uv, plus trois briques externes.

| Outil | Rôle | Installation |
|---|---|---|
| `yt-dlp` | Métadonnées et téléchargement | `uv tool install yt-dlp` |
| `ffmpeg` / `ffprobe` | Extraction audio | `apt install ffmpeg` |
| Serveur Whisper local | Transcription | `claude-skills/local-whisper/speaches-up.sh` |

```bash
uv sync
```

Le moteur de transcription vient du skill `watch`. Il est **importé depuis son
emplacement réel, jamais recopié**, pour continuer à profiter de ses corrections.

```bash
# voir ce qui serait traité, sans rien télécharger
uv run reels-ingest exports/*.json --vault ~/Vault --lister

# tester sur 10 reels avant d'en lancer 300
uv run reels-ingest exports/*.json --vault ~/Vault --cookies firefox --limite 10

# traiter le flux continu seul (inbox.txt du vault)
uv run reels-ingest --vault ~/Vault --cookies firefox

# ce qui reste à enrichir
uv run reels-verifier --vault ~/Vault --a-enrichir

# puis le maillage, après enrichissement des fiches
uv run reels-mailler --vault ~/Vault

# audit : le journal concorde-t-il avec le disque ?
uv run reels-verifier --vault ~/Vault

# chercher dans ce qui est dit, avec l'horodatage du passage
uv run reels-chercher "Marc Bloch Gestapo" --vault ~/Vault --domaine politique

# la carte d'un domaine : entités par type, comptes, chronologie
uv run reels-carte politique --vault ~/Vault
```

Marche à suivre complète : [docs/GUIDE.md](docs/GUIDE.md).

Le script **reprend où il s'est arrêté**. On peut le couper à tout moment : le
journal est réécrit après chaque reel. Relancer la même commande retente uniquement
ce qui a échoué.

### Cycle de vie : rien à éteindre côté projet

`reels-ingest` et `reels-mailler` sont des **traitements par lots** : ils démarrent,
travaillent, rendent la main. Aucun démon, aucun port ouvert, aucun processus
résiduel. `Ctrl+C` est un moyen d'arrêt légitime — le journal permet de reprendre.

Le **seul service permanent** de la chaîne est le serveur Whisper, et il vit
**hors de ce dépôt**, en conteneur Docker, dans `claude-skills/local-whisper/`.
Il porte `restart: unless-stopped` : il redémarre donc tout seul à chaque
démarrage de Docker tant qu'on ne l'a pas explicitement arrêté.

| Action | Commande |
|---|---|
| Démarrer | `local-whisper/speaches-up.sh up` |
| **Arrêter durablement** | `local-whisper/speaches-up.sh down` |
| Voir l'état et l'endpoint | `local-whisper/speaches-up.sh status` |
| Suivre les logs | `local-whisper/speaches-up.sh logs` |

`down` supprime le conteneur : c'est le seul moyen de le couper pour de bon, un
simple `docker stop` étant annulé par la politique de redémarrage. Le conteneur
occupe le **port 8000** et, en mode GPU, la mémoire vidéo du modèle — le couper
est utile avant tout autre travail sur GPU.

## Vault thématique (le chemin simple)

Un vault par thème, alimenté par une **collection d'enregistrés** (ex. « politique »).

```bash
# premier lancement : tout est mémorisé dans <vault>/theme.json
uv run reels-sync --vault ~/Documents/Vault-Politique \
    --collection politique --export ~/reels-exports \
    --reprendre-de ~/Documents/ReelsVault --cookies firefox

# ensuite, après chaque nouvel export Meta déposé dans ~/reels-exports
uv run reels-sync --vault ~/Documents/Vault-Politique --cookies firefox

# où on en est, sans rien lancer (écrit aussi STATUT.md dans le vault)
uv run reels-statut --vault ~/Documents/Vault-Politique
```

À chaque `reels-sync` : lecture de la collection dans l'export (dossier ou zip,
tel que Meta le livre) → reprise de ce qui est déjà traité dans l'ancien vault →
téléchargement et transcription **des seuls reels manquants** (doublons écartés
par le journal, par URL canonique puis par identifiant natif) → maillage → statut.

**L'export Meta est le seul point d'entrée** : il n'existe pas d'API pour les
enregistrés. Il faut cocher les enregistrés **et leurs collections** (le fichier
`saved_collections.json`). Meta propose un export récurrent (Centre des comptes →
Exporter vos informations), à déposer ensuite dans le dossier d'exports.

## Configuration

Options de `reels-ingest` :

| Option | Défaut | Effet |
|---|---|---|
| `--vault` | `~/Vault` | Racine du vault Obsidian produit |
| `--cookies` | aucun | Navigateur (`firefox`) ou chemin d'un `cookies.txt`. **Obligatoire** pour Instagram et Facebook |
| `--limite` | `0` (tout) | Ne traiter que les N premiers reels restants |
| `--pause` | `3.0` | Secondes entre deux reels, anti rate-limit |
| `--langue` | `fr` | Langue **forcée** pour Whisper |
| `--sans-inbox` | — | Ne pas lire `inbox.txt` du vault |
| `--lister` | — | Afficher ce qui serait traité, sans rien télécharger |

L'argument positionnel est facultatif : sans lui, seule l'inbox du vault est lue.

Options de `reels-verifier` :

| Option | Effet |
|---|---|
| `--reparer` | Redéclasser en échec les entrées que le disque contredit, pour qu'elles repassent |
| `--a-enrichir` | Lister les chemins des fiches restant à enrichir, un par ligne |

Variables d'environnement :

| Variable | Défaut | Effet |
|---|---|---|
| `REELS_GRAPH_MD_WATCH_SCRIPTS` | `~/mes_projets/claude-skills/watch/scripts` | Emplacement du moteur de transcription |
| `WHISPER_BASE_URL` | `http://localhost:8000/v1` | Serveur Whisper local (lu par le moteur) |
| `WHISPER_MODEL` | modèle par défaut du serveur | Modèle de transcription |

Codes de sortie de `reels-ingest` : `0` terminé, `2` fichier d'export introuvable,
`3` serveur Whisper injoignable, `130` interruption clavier.

## Tests

```bash
uv run pytest
```

**141 tests** sur les fonctions pures : extraction et normalisation des liens,
déduplication native, audit et réparation du vault, boîte de réception,
échappement YAML, évaluation de fiabilité, maillage et son idempotence. Les étapes
réseau ne sont pas testées automatiquement — voir
[docs/CADRAGE.md](docs/CADRAGE.md).

## Structure du projet

```text
reels-graph-md/
├── src/reels_graph_md/       # le package, sept modules
├── tests/                    # tests des fonctions pures
├── docs/
│   ├── CADRAGE.md            # le pourquoi
│   └── ARCHITECTURE.md       # le comment
├── CONTRIBUTING.md           # règles de contribution
├── pyproject.toml            # uv, Python 3.12, zéro dépendance de production
└── README.md
```

## Licences & composants

| Composant | Rôle | Licence |
|---|---|---|
| yt-dlp | Métadonnées et téléchargement | Unlicense |
| ffmpeg | Extraction audio | LGPL-2.1+ / GPL selon le build |
| faster-whisper (CTranslate2) | Moteur de transcription | MIT |
| speaches | Serveur Whisper compatible OpenAI | MIT |
| pytest | Tests | MIT |
| hatchling | Build | MIT |
| Obsidian | Lecture du vault | Propriétaire, gratuit pour usage personnel |
| **Ce projet** | Code applicatif | MIT — Copyright (c) 2026 floSa |

Le fichier `LICENSE` n'est pas encore présent dans le dépôt. Basé sur les idées de
[sosoj92/reels-vault](https://github.com/sosoj92/reels-vault) (MIT) pour la couche
batch, et sur le skill `watch` (MIT, dérivé de
[claude-video](https://github.com/bradautomates/claude-video)) pour le moteur de
transcription.

---

## Étape 0 — récupérer ses reels sauvegardés

Aucune plateforme n'expose d'API pour les contenus sauvegardés. Le seul chemin
fiable est l'export officiel des données. C'est **manuel et asynchrone** (quelques
heures à quelques jours), et ça se fait une fois.

| Plateforme | Chemin | Fichier attendu |
|---|---|---|
| Instagram | Centre des comptes → Vos informations et autorisations → Télécharger vos informations → **JSON** | `saved_posts.json` |
| TikTok | Paramètres → Compte → Télécharger vos données → *Custom data* → **Likes and Favorites** → JSON | `user_data_tiktok.json` |
| Facebook | Paramètres → Vos informations Facebook → Télécharger vos informations → JSON | *Éléments enregistrés et collections* |

TikTok distingue **Favoris** (le signet — ce qu'on veut) et **J'aime** (autre
volume, beaucoup moins pertinent).

Le format des fichiers n'a aucune importance : ils sont lus **en texte brut** et
attaqués à la regex. txt, csv, json ou html, un seul code les lit tous.

## Le vault produit

```text
Vault/
├── fiches/     insta_DXabc123.md        une fiche par reel
├── reels/      insta_DXabc123.mp4       la vidéo, conservée
├── themes/     politique.md             généré, wikilinks
├── entites/    Assemblée nationale.md   généré, wikilinks
├── inbox.txt                            flux continu, alimenté par le mobile
├── inbox-traite.txt                     archive des lignes déjà traitées
└── journal.json
```

La vidéo est conservée délibérément : c'est elle qui permet de revoir le reel
depuis Obsidian même quand le post d'origine a disparu — ce qui arrive à environ
**un tiers** d'un stock ancien. Compter **1 à 3 Go** pour 300 reels.

Sur les horodatages : contrairement à YouTube, Instagram, TikTok et Facebook
n'honorent pas de paramètre `?t=` dans leurs URLs. Les repères `[MM:SS]` renvoient
donc à la vidéo locale embarquée en tête de fiche, pas au post d'origine.

### Le pipeline

| # | Étape | Automatique |
|---|---|---|
| 1 | Extraction des liens depuis les exports | oui |
| 2 | Filtrage par le journal | oui |
| 3 | Métadonnées (`yt-dlp --dump-json`) | oui |
| 4 | Téléchargement de la vidéo, **une seule passe** | oui |
| 5 | Extraction audio locale (ffmpeg) | oui |
| 6 | Transcription Whisper, **langue forcée** | oui |
| 7 | Écriture de la fiche | oui |
| 8 | Synthèse, thèmes, entités | **LLM, par lots** |
| 9 | Maillage Obsidian (`reels-mailler`) | oui |

Les étapes 1 à 7 tournent sans personne. L'étape 8 est la seule qui consomme du
quota. L'étape 9 est du texte, instantanée.

### Règle éditoriale de l'étape 8

Séparation stricte entre **ce que dit le reel** et **ce qui est établi**. Style
indirect obligatoire (« le reel affirme que… »), jamais de voix assertive. Sur du
contenu politique, une synthèse d'apparence neutre qui reprend une affirmation
militante est un défaut, pas un détail. Une fiche trop pauvre pour un vrai résumé
doit être signalée comme telle, pas comblée.

Les thèmes sont **émergents** : aucune liste prédéfinie, ils se déduisent de ce qui
est lu, en réutilisant les mêmes libellés d'une fiche à l'autre.

## Dépannage

**Problème** : `Failed to decrypt with DPAPI` au moment de lire les cookies.
**Cause** : Chrome 127+ chiffre ses cookies d'une façon que yt-dlp ne sait pas
déchiffrer sous Windows. **Solution** : utiliser Firefox. Il n'y a pas de
contournement.

**Problème** : `could not find firefox cookies database`.
**Cause** : yt-dlp ne cherche le profil que dans les emplacements Linux, alors que
Firefox tourne côté Windows. **Solution** : le projet le détecte tout seul sous
WSL. Sinon, passer le chemin du profil à `--cookies`.

**Problème** : `reels-ingest` sort avec le code `3`.
**Cause** : le serveur Whisper local ne répond pas. **Solution** : le démarrer avec
`speaches-up.sh`, puis relancer — le journal reprendra où il en était.

**Problème** : beaucoup de fiches en `fiabilite: vide`.
**Cause** : les reels traités n'ont pas de voix off exploitable. **Solution** :
c'est une limite du périmètre, pas un bug. Vérifier sur un lot de 10 avant de
lancer le stock complet.

**Problème** : une part importante des reels échoue avec une erreur de la
plateforme. **Cause** : posts supprimés ou passés en privé, fréquent sur un stock
ancien. **Solution** : aucun recours ; les échecs restent dans `journal.json` et
sont retentés à chaque relance.

## Limites connues

- **Un reel sans voix off produit une fiche vide.** Ce n'est pas de la magie, c'est
  de la transcription. D'où le `--limite 10` avant de lancer le stock.
- **Les carrousels photo** n'ont ni audio ni transcript : la fiche se réduit à la
  légende, et porte `fiabilite: legende_seule`.
- **Les sous-titres de plateforme** sont rares sur Instagram, parfois présents sur
  TikTok. Quand ils existent, ils sont conservés comme seconde source à croiser —
  mais on ne peut pas compter dessus.
- **Instagram rate-limite sévèrement** : procéder par lots de ~50 étalés sur
  plusieurs jours.
- **Instagram, TikTok et Facebook sont validés sur un stock réel** (340 fiches
  dans le vault d'origine : 293 Instagram, 47 Facebook).
- **Les exports doivent rester disponibles.** La liste de travail est recalculée
  à chaque lancement ; le journal enregistre ce qui est fait, pas ce qui reste.
- **L'étape 8 reste manuelle** — c'est la seule qui consomme du quota. Elle est
  outillée (`reels-enrichir`) et documentée dans
  [docs/ENRICHISSEMENT.md](docs/ENRICHISSEMENT.md).
- **Environ 13 % des fiches sont marquées `a_verifier`** : vidéo muette, langue
  non couverte, ou information affichée à l'écran seulement. Elles gardent leur
  fiche et leur vidéo, et sont regroupées pour reprise à la main.
- **La recherche sémantique n'est pas là.** La navigation par thème et par entité
  fonctionne ; retrouver un reel à partir d'une question en langage naturel reste à
  concevoir — voir [docs/CADRAGE.md](docs/CADRAGE.md).
