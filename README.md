---
title: Accessibility Analysis
emoji: 🚌
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# Application analyse accessibilité urbaine transports collectifs/piétons et analyse réseau transports collectifs - France Métropolitaine

Cette application regroupe deux analyses indépendantes basées sur le même jeu de données GTFS :

1. **Analyse d'accessibilité urbaine** aux équipements en transport collectif / piéton (< 30 min), à partir d'un GTFS et d'un découpage communal français quelconque.
2. **Analyse réseau** (arrêts / tronçons) : indicateurs de fréquentation par arrêt et par tronçon, indépendants de l'analyse d'accessibilité — fonctionne avec n'importe quel GTFS, français ou non.

Le projet s'inspire des travaux du livre *Introduction to urban accessibility* (Rafael H. M. Pereira et Daniel Herszenhut, Ipea - Institute for Applied Economic Research), notamment le chapitre [Calculating accessibility estimates in R](https://ipeagit.github.io/intro_access_book/3_calculando_acesso.en.html), réadaptés ici en Python pour un contexte français (carroyage INSEE, Base Permanente des Équipements).

**Concepteur :** Antoine Chèvre (avec Claude.ai)

## Analyse d'accessibilité urbaine

## Ce que fait le projet

À partir d'un GTFS et d'un découpage communal :

1. Construit le réseau multimodal piéton + transport collectif (`r5py`) à partir du GTFS pour une date JOB indiquée et le réseau viaire pour les cheminements piétons 
2. Récupère le carroyage population INSEE 200x200 2019 incluant les catégories socio économiques (Filosofi) et la Base Permanente des Équipements (BPE, INSEE) 
3. Pondère les équipements par gamme (proximité / intermédiaire / supérieure / hors gamme) et par domaine (santé, enseignement, commerces...) avec une pondération des équipements dans le fichier src/ponderation_bpe.py: 
  3.1 Liste des équipements BPE - cf https://vscode.dev/github/antoinechevre/Accessibility_analysis/blob/main/data/BPE25_anonymisee_dessin_fichier.html 
    "O": "Tout équipements pondérés",
    "A": "Services pour les particuliers",
    "B": "Commerces",
    "C": "Enseignement",
    "D": "Santé et action sociale",
    "E": "Transports et déplacements",
    "F": "Sports, loisirs et culture",
    "G": "Tourisme",
  3.2 pondération selon la classification
    "Gamme de proximité"
    "Gamme intermédiaire"
    "Gamme supérieure"
    "Hors Gamme"
  3.3 des seuils sur pondération par carreaux de 200x200 et par domaine par rapport à la moyenne de pondération des carreaux par domaines (objectif de filtrer les carreaux significatifs)  

4. Calcule la matrice des temps de trajet (`TravelTimeMatrix`) entre tous les carreaux avec GTFS / Piétons OSM à l'heure de pointe en JOB 
5. Calcule plusieurs indicateurs d'accessibilité : 
  5.1 opportunités cumulées: calcul pour chaque carreau le nombre d'opportunités / équipements dans un temps de trajet donné 
  5.2 coût au plus proche: calcul le temps minimum pour atteindre un certains nombres d'opportunités  
  5.3 gravité: le poids de chaque opportunité décroit à mesure que le temps de trajet augmente (exponetiel inversé) 
  5.4 compétition (Enhanced 2SFCA): calcul le niveau d'accessibilité en considérant la compétitions entre opportunités 
6. Exporte des cartes interactives (HTML/Folium) et statiques (PNG) par domaine d'équipement avec une déclinaison par déciles de population
7. propose un benchmark avec des indicateurs pour les différentes villes françaises en fonction des domaines d'équipements et des déciles de niveau de richesse 
  7.1 temps de trajet moyen pour atteindre 25%, 50%, 75% des opportunités/équipements 
  7.2 % opportunités/équipements pour un temps de trajet moyen de 30 min, 45 min, 60 min, 75 min  


## Analyse réseau (arrêts / tronçons)

Deuxième analyse proposée par l'application, indépendante de l'analyse d'accessibilité ci-dessus (pas besoin de découpage communal, de carroyage INSEE ni de BPE — seul le GTFS est nécessaire, ce qui la rend utilisable pour un réseau hors de France). Développée lors du [Hackathon TSNI 2025 du Cerema](https://colab.research.google.com/github/CEREMA/hackathon-gtfs/blob/main/gtfs_notebook.ipynb) (équipe Cerema : Patrick Gendre, Hugo De Luca et Maxence Liogier), reprise et adaptée ici par Antoine Chèvre (et Claude.ai). Application dédiée de référence : [GTFS_analyse_fr](https://huggingface.co/spaces/antoinechevre/GTFS_analyse_fr).

Détermine la plage de service fiable du GTFS et un jour ouvré de base (JOB, mardi ou jeudi tiré au hasard dans cette plage), puis calcule :

- **Par arrêts** : nombre de passages par arrêt, carte interactive, statistiques détaillées (fiche exportable en HTML), export CSV.
- **Par tronçons** (bus / tram / métro / trolley / ferry, et train pour les réseaux avec agences dédiées comme IDFM — RER/Transilien/TER) : nombre de passages par tronçon, vitesse moyenne, carte interactive par mode avec couches superposables, export CSV par mode.

Dans l'app, ces deux pages vivent sous l'onglet "Analyse réseau" (sous-pages "Arrêts" et "Tronçons"), avec une troisième sous-page "Explications" reprenant ce même texte.

## Structure du dépôt

```
index_accessibility_notebook_def.ipynb   # notebook principal : pipeline d'analyse complet
app.py                                    # application Streamlit (en cours de développement)
views/                                    # pages de l'app Streamlit
  accessibilite_index.py, ponderation_equipements.py, cartographie_insee.py, benchmark_reseaux.py
  home.py                                 # page Accueil (accessibilité + analyse réseau)
  arrets.py, troncons.py                  # analyse réseau (arrêts / tronçons), cf. section dédiée
src/
  build_data_agglo.py                     # découpage communal, grille, extraction OSM
  BPE_traitement.py                       # filtrage/pondération BPE, cartes par domaine
  utilitaires_matrix.py                   # cumulative_cutoff, cost_to_closest, gravity, 2SFCA
  utils.py                                # chargement GTFS, exports CSV/GeoJSON, dir_tree
  info_reseau.py, i18n.py, ...            # utilitaires réseau / traductions app
  arrets.py, create_troncons_uniques.py, indicateurs_troncons.py   # calculs analyse réseau
  cartographie.py, export_html.py         # cartes Folium et fiches HTML (arrêts, accessibilité et réseau)
data/                                      # GTFS, carroyage INSEE, BPE, fichiers générés (non versionné)
output/                                    # cartes et images exportées (non versionné)
requirements.txt
```

> ⚠️ `app.py` est fonctionnel : `views/accessibilite_index.py` (pipeline complet + r5py) et `views/ponderation_equipements.py` (cartes de pondération BPE, sans r5py) sont toutes deux implémentées, de même que `views/arrets.py` et `views/troncons.py` (analyse réseau). Le notebook reste la référence de calcul en cas de doute pour l'analyse d'accessibilité.

## Déploiement

- **Streamlit Community Cloud** : `packages.txt` installe Java (`default-jdk-headless`) et `osmium-tool` via apt. Le tier gratuit (~1 Go RAM) est cependant limite pour ce pipeline (JVM r5py + carroyage INSEE 1,1 Go).
- **Hugging Face Spaces (SDK Docker)** : `Dockerfile` fourni, plus adapté (tier gratuit ~16 Go RAM / 2 vCPU).
- **Tier payant (Hugging Face Spaces, hardware upgrade)** : aucun changement de code nécessaire, juste changer le hardware du Space dans ses paramètres (Settings → Space hardware). Pour que le calcul profite réellement de la RAM supplémentaire, remonter aussi la mémoire allouée à la JVM r5py via la variable d'environnement `R5PY_MAX_JVM_MEMORY_MB` (Settings → Variables and secrets), sans quoi elle reste plafonnée à 512 Mo par défaut (cf. `Dockerfile` et `views/accessibilite_index.py`). Penser aussi à activer le stockage persistant du Space pour conserver le cache disque (`data/decoupage_agglo.*`, `data/agglo.osm.pbf`, `data/ttm_<réseau>.parquet`) entre les redémarrages, sans quoi il est reconstruit à chaque fois.
- **Cache de secours Hugging Face (lecture + écriture)** : le contenu de `data/` (BPE, carroyage INSEE, GTFS, extraits OSM et matrices de temps de trajet déjà calculées par réseau) est sauvegardé dans le dataset privé [antoinechevre/accessibility-data](https://huggingface.co/datasets/antoinechevre/accessibility-data) (cf. `src/hf_cache.py`). Avant tout téléchargement/calcul coûteux, le pipeline regarde d'abord si le fichier existe déjà dans ce dataset (`recuperer_depuis_hf`) ; sans stockage persistant sur le Space, c'est ce qui évite de tout reconstruire (dont le calcul r5py, potentiellement long) à chaque redémarrage. Symétriquement, après le calcul d'un réseau **encore jamais traité** (découpage communal, extrait OSM, matrice des temps de trajet), le résultat est renvoyé vers ce même dataset (`envoyer_vers_hf`) pour que les déploiements suivants — y compris d'autres visiteurs du Space — en profitent aussi, sans avoir à repousser manuellement depuis un poste local. Nécessite un secret `HF_TOKEN` (Settings → Variables and secrets) avec accès **lecture et écriture** au dataset — sans lui, le pipeline se rabat silencieusement sur le calcul/téléchargement habituel (lecture) ou n'envoie simplement rien (écriture), sans jamais faire échouer le calcul en cours.

## Rafraîchissement automatique des GTFS

- **Index de provenance (`data/gtfs_sources.json`, `src/transport_data_gouv.py`)** : associe chaque GTFS de `data/GTFS/` au jeu de données [transport.data.gouv.fr](https://transport.data.gouv.fr) (PAN) dont il provient (page_url/ressource_url/date de mise à jour), rempli automatiquement par la recherche en barre latérale de l'app, ou par `scripts/indexer_gtfs_locaux.py` pour les GTFS uploadés avant l'existence de cette fonctionnalité (le rapprochement se fait sur le contenu réel — `agency.txt` comparé entre le GTFS local et chaque candidat téléchargé — jamais sur le seul nom de fichier, potentiellement renommé depuis).
- **`scripts/rafraichir_gtfs.py`** : vérifie la fraîcheur de chaque GTFS déjà indexé, télécharge/écrase/pousse sur le dataset HF ceux qui ont une mise à jour disponible, et invalide leurs caches dérivés (découpage communal, carroyage, extrait OSM, matrice des temps de trajet) pour que l'app ne serve pas un résultat périmé. `--exclude` permet de signaler une mise à jour sans l'appliquer automatiquement (utilisé pour IDFM/Lyon TCL, dont le retraitement complet prend plusieurs heures). `--force` (ou directement `scripts/rafraichir_gtfs_force.py`, un simple appel avec ce drapeau) retélécharge même les GTFS déjà à jour d'après la source, pour resynchroniser sans se fier à la comparaison de dates. Le recalcul des indicateurs d'accessibilité lui-même n'a jamais lieu ici (r5py/Overpass, trop coûteux) — au prochain passage dans l'app ou le notebook.
- **`.github/workflows/rafraichir-gtfs.yml`** : exécute `rafraichir_gtfs.py` chaque lundi (et sur déclenchement manuel), journalise chaque mise à jour dans `data/journal_maj_gtfs.csv` (commité sur git), et envoie un mail de notification (uniquement s'il y a quelque chose à signaler) via SMTP Gmail. Secrets requis en plus de `HF_TOKEN` : `MAIL_USERNAME`/`MAIL_PASSWORD` (compte Gmail expéditeur + [mot de passe d'application](https://myaccount.google.com/apppasswords)) et `MAIL_DESTINATAIRE`.

## Cas particuliers (grosses agglomérations, réseaux multi-GTFS)

- **Résolution de grille dégradée (`RESOLUTIONS_GRILLE_SPECIALES`, `src/pipeline_donnees.py`)** : la matrice des temps de trajet grandit en O(n²) avec le nombre de carreaux — au-delà d'une certaine taille de réseau, elle ne tient plus en mémoire à la résolution standard de 200m. Carreaux fusionnés en blocs plus grossiers pour les réseaux concernés (résolution spatiale plus grossière sur les cartes/indicateurs de ces réseaux uniquement) :
  - **Lyon/TCL : blocs de 400m** — 92 741 carreaux à 200m produiraient un ttm de 1,22 milliard de lignes, qui dépasse la RAM disponible (32 Go) une fois chargé, même avec les dtypes compacts de `charger_ttm`.
  - **Aix_Marseille : blocs de 800m** — GTFS agrégé "mamp" couvrant une échelle régionale (cf. plus bas), 400m ne suffirait probablement pas.
- **Carroyage 1km au lieu de 200m (`RESEAUX_GRILLE_1KM`, `src/pipeline_donnees.py` / `build_grid_agglo_1km`, `src/build_data_agglo.py`)** : pour les réseaux où même la fusion à 800m/1600m reste trop volumineuse.
  - **IDFM (Île-de-France)** : Paris + petite couronne, nettement plus grand que Lyon ; même 800m ne suffisait pas ("Memory limit exceeded" à 32 Go sur le Space, y compris avec un lot réduit pour `calculer_ttm_par_lots`). Passage en carreaux de 1km sur la base INSEE correspondante (carroyage Filosofi 1km, déjà le grillage publié par l'INSEE — pas de grille théorique à reconstruire ni de fusion à faire, contrairement au 200m) : ~8000 carreaux sur toute l'Île-de-France au lieu de plusieurs centaines de milliers à 200m. Fichier `Filosofi2017_carreaux_1km_met.gpkg` récupéré depuis le cache Hugging Face (`assurer_carreaux_1km_local`) ou à télécharger manuellement depuis insee.fr si absent des deux côtés (pas d'URL directe stable identifiée, contrairement au 200m). Contrepartie : résolution spatiale fixe à 1km (pas de palier intermédiaire), et millésime Filosofi 2017 (le 200m est peut-être plus récent) — décalage temporel mineur avec le BPE 2025.
- **Garde-fou "max 4 agences" et exceptions nommées (`GTFS_NOM_RESEAU_FORCE`, `app.py`)** : un GTFS national/régional regroupant de nombreuses agences ferait exploser les temps de calcul et n'a pas de sens pour les indicateurs proposés ici — bloqué par défaut (`TropAgencesError`), sauf pour les GTFS listés explicitement dans `GTFS_NOM_RESEAU_FORCE` (actuellement IDFM et Aix_Marseille_mamp_GTFS.zip), avec un nom de réseau forcé plutôt que dérivé automatiquement des agences (`nom_reseau_str()` concatène tous les noms d'agence avec " / ", ce qui donnerait une chaîne de plusieurs centaines de caractères pour IDFM — invalide comme nom de fichier).
- **Fusion de plusieurs GTFS (`src/merge_gtfs.py`, upload multi-fichiers dans `app.py`)** : certaines agglomérations ne sont pas couvertes par un seul GTFS (ex: **Aix-Marseille-Provence**, dont le GTFS "Marseille" ne couvre que le réseau RTM, pas celui d'Aix-en-Provence). La barre latérale accepte plusieurs fichiers à la fois (upload et/ou catalogue existant) : au-delà d'un seul fichier sélectionné, ils sont fusionnés (`fusionner_gtfs` — concaténation simple, identifiants préfixés par feed pour éviter les collisions, pas de déduplication d'entités qui se recouvriraient) avant d'entrer dans le pipeline standard. Un champ optionnel permet de forcer le nom du réseau fusionné (ex: `Aix_Marseille`), pour éviter le même problème de nom à rallonge que IDFM si on laisse `nom_reseau_str()` concaténer les agences des GTFS fusionnés.

## Installation

Prérequis :
- Python 3.12
- Java 21 (r5py embarque une JVM ; testé avec Temurin 21)

```bash
python3.12 -m venv env
source env/bin/activate
pip install -r requirements.txt
```

## Utilisation

1. Placer un GTFS (zip) dans `data/GTFS/`.
2. Ouvrir `index_accessibility_notebook_def.ipynb` et exécuter les cellules dans l'ordre depuis le début (le pipeline dépend de variables globales définies au fil des cellules : `feed`, `nom_reseau_str`, `land_use_data`, `BPE_agglo`, `ttm`...).
3. Les cellules chronophages (extraction OSM, calcul de la matrice de temps de trajet `r5py.TravelTimeMatrix`) mettent en cache leurs résultats sur disque (`data/decoupage_agglo.*`, `data/agglo.osm.pbf`, `data/ttm_<réseau>.parquet`) pour éviter de tout relancer après un redémarrage du kernel.
4. Les cartes et images sont exportées dans `output/`.

## Données requises (non versionnées)

- GTFS du réseau étudié (`data/GTFS/`)
- Carroyage population INSEE 200m (Filosofi) au format gpkg
- BPE (Base Permanente des Équipements, INSEE) au format parquet + nomenclature des gammes (xlsx)

## Statut

Projet personnel en développement actif (2026). Le notebook constitue la référence fonctionnelle pour l'analyse d'accessibilité ; l'application Streamlit (`app.py`) rend les deux analyses (accessibilité urbaine et analyse réseau) accessibles sans passer par le notebook.
