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
2. **Analyse réseau** (arrêts / tronçons / isochrones) : indicateurs de fréquentation par arrêt et par tronçon, cartes isochrones depuis un arrêt choisi — indépendants de l'analyse d'accessibilité, fonctionne avec n'importe quel GTFS, français ou non.

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


## Analyse réseau (arrêts / tronçons / isochrones)

Deuxième analyse proposée par l'application, indépendante de l'analyse d'accessibilité ci-dessus (pas besoin de découpage communal, de carroyage INSEE ni de BPE — seul le GTFS est nécessaire, ce qui la rend utilisable pour un réseau hors de France). Développée lors du [Hackathon TSNI 2025 du Cerema](https://colab.research.google.com/github/CEREMA/hackathon-gtfs/blob/main/gtfs_notebook.ipynb) (équipe Cerema : Patrick Gendre, Hugo De Luca et Maxence Liogier), reprise et adaptée ici par Antoine Chèvre (et Claude.ai). Application dédiée de référence, tenue en parallèle : [GTFS_analyse_fr](https://huggingface.co/spaces/antoinechevre/GTFS_analyse_fr) ([code source](https://github.com/antoinechevre/GTFS_analysis_fr)).

Détermine la plage de service fiable du GTFS et un jour ouvré de base (JOB, mardi ou jeudi le plus loin dans le temps sur cette plage, hors vacances scolaires de l'académie du réseau si connue), puis calcule :

- **Par arrêts** : nombre de passages par arrêt, carte interactive, statistiques détaillées (fiche exportable en HTML), export CSV.
- **Par tronçons** (bus / tram / métro / trolley / ferry, et train pour les réseaux avec agences dédiées comme IDFM — RER/Transilien/TER) : nombre de passages par tronçon, vitesse moyenne, carte interactive par mode avec couches superposables, export CSV par mode.
- **Isochrone d'arrêt à arrêt** (`views/isochrone.py`, `src/isochrone.py`) : accessibilité en transport collectif depuis un arrêt choisi, à une heure de pointe donnée (RAPTOR simplifié limité aux correspondances au même arrêt), habillée d'un isochrone piéton par arrêt atteint (API Isochrone/Isodistance de la Géoplateforme IGN, avec repli en cercle si l'API échoue) ; les zones de marche qui se chevauchent sont tronquées pour que l'arrêt le plus rapide l'emporte visuellement.
- **Isochrone de carreaux 200m** (`views/isochrone_ttm_test.py`, `src/isochrone_ttm.py`, expérimental) : même principe mais à partir de la matrice de temps de trajet r5py carreau à carreau déjà calculée par ce projet pour le réseau (`memory_ttm/ttm_<réseau>.parquet`, cf. section Déploiement) — correspondances et temps de marche réels plutôt qu'approximés, mais horaire de départ figé à celui du calcul du ttm (14h en JOB) et disponible seulement pour les réseaux déjà traités par l'analyse d'accessibilité.

Toutes les cartes de cette section (Arrêts, Tronçons, Isochrones) partagent le même sélecteur de fond de carte (OpenStreetMap / CartoDB Positron / CartoDB Dark Matter) et une couche optionnelle, décochée par défaut, de densité de population par carreau INSEE 200m (`src/insee_carreaux.py`).

Dans l'app, ces pages vivent sous l'onglet "Analyse réseau" (sous-pages "Arrêts", "Tronçons", "Isochrone", "Isochrone TTM"), avec une dernière sous-page "Explications" reprenant ce même texte.

## Structure du dépôt

```
index_accessibility_notebook_def.ipynb   # notebook principal : pipeline d'analyse complet
app.py                                    # application Streamlit (en cours de développement)
views/                                    # pages de l'app Streamlit
  accessibilite_index.py, ponderation_equipements.py, cartographie_insee.py, benchmark_reseaux.py
  home.py                                 # page Accueil (accessibilité + analyse réseau)
  arrets.py, troncons.py                  # analyse réseau (arrêts / tronçons), cf. section dédiée
  isochrone.py, isochrone_ttm_test.py     # analyse réseau (isochrones), cf. section dédiée
src/
  build_data_agglo.py                     # découpage communal, grille, extraction OSM (Overpass ou extrait OSM France)
  extraire_gtfs_departement.py, _epci.py, _agence.py   # sous-GTFS extrait d'un GTFS régional agrégé (département / EPCI / agence)
  BPE_traitement.py                       # filtrage/pondération BPE, cartes par domaine
  utilitaires_matrix.py                   # cumulative_cutoff, cost_to_closest, gravity, 2SFCA
  utils.py                                # chargement GTFS, exports CSV/GeoJSON, dir_tree
  info_reseau.py, i18n.py, ...            # utilitaires réseau / traductions app
  arrets.py, create_troncons_uniques.py, indicateurs_troncons.py   # calculs analyse réseau
  isochrone.py, isochrone_ttm.py          # calculs isochrones (RAPTOR simplifié, et via ttm r5py)
  insee_carreaux.py                       # couche densité de population (carreaux INSEE 200m)
  cartographie.py, export_html.py         # cartes Folium et fiches HTML (arrêts, accessibilité et réseau)
data/                                      # carroyage INSEE, BPE, fichiers générés (non versionné)
  GTFS/                                    # catalogue principal (téléchargements bruts, un GTFS = un réseau)
  GTFS_Régionaux/                          # sources multi-agences brutes (entrée des scripts d'extraction)
  GTFS_agrege/                             # extraits agence/EPCI/département prêts à l'étude (sortie des scripts d'extraction)
output/                                    # cartes et images exportées (non versionné)
requirements.txt
```

> ⚠️ `app.py` est fonctionnel : `views/accessibilite_index.py` (pipeline complet + r5py) et `views/ponderation_equipements.py` (cartes de pondération BPE, sans r5py) sont toutes deux implémentées, de même que `views/arrets.py`, `views/troncons.py`, `views/isochrone.py` et `views/isochrone_ttm_test.py` (analyse réseau). Le notebook reste la référence de calcul en cas de doute pour l'analyse d'accessibilité.

## Déploiement

- **Streamlit Community Cloud** : `packages.txt` installe Java (`default-jdk-headless`) et `osmium-tool` via apt. Le tier gratuit (~1 Go RAM) est cependant limite pour ce pipeline (JVM r5py + carroyage INSEE 1,1 Go).
- **Hugging Face Spaces (SDK Docker)** : `Dockerfile` fourni, plus adapté (tier gratuit ~16 Go RAM / 2 vCPU).
- **Tier payant (Hugging Face Spaces, hardware upgrade)** : aucun changement de code nécessaire, juste changer le hardware du Space dans ses paramètres (Settings → Space hardware). Pour que le calcul profite réellement de la RAM supplémentaire, remonter aussi la mémoire allouée à la JVM r5py via la variable d'environnement `R5PY_MAX_JVM_MEMORY_MB` (Settings → Variables and secrets), sans quoi elle reste plafonnée à 512 Mo par défaut (cf. `Dockerfile` et `views/accessibilite_index.py`). Penser aussi à activer le stockage persistant du Space pour conserver le cache disque (`data/decoupage_agglo.*`, `data/agglo.osm.pbf`, `data/ttm_<réseau>.parquet`) entre les redémarrages, sans quoi il est reconstruit à chaque fois.
- **Cache de secours Hugging Face (lecture + écriture)** : le contenu de `data/` (BPE, carroyage INSEE, GTFS, extraits OSM et matrices de temps de trajet déjà calculées par réseau) ainsi que les cartes HTML déjà générées (`output/<réseau>/*.html`, cf. `views/accessibilite_index._afficher_carte_avec_cache_hf`) sont sauvegardés dans le dataset privé [antoinechevre/accessibility-data](https://huggingface.co/datasets/antoinechevre/accessibility-data) (cf. `src/hf_cache.py`). Avant tout téléchargement/calcul/rendu coûteux, le pipeline regarde d'abord si le fichier existe déjà dans ce dataset (`recuperer_depuis_hf`) ; sans stockage persistant sur le Space, c'est ce qui évite de tout reconstruire (dont le calcul r5py, potentiellement long) à chaque redémarrage. Symétriquement, après le calcul d'un réseau **encore jamais traité**, le résultat est renvoyé vers ce même dataset (`envoyer_vers_hf`) pour que les déploiements suivants — y compris d'autres visiteurs du Space — en profitent aussi, sans avoir à repousser manuellement depuis un poste local. Nécessite un secret `HF_TOKEN` (Settings → Variables and secrets) avec accès **lecture et écriture** au dataset — sans lui, le pipeline se rabat silencieusement sur le calcul/téléchargement habituel (lecture) ou n'envoie simplement rien (écriture), sans jamais faire échouer le calcul en cours. Un GTFS uploadé spontanément par un visiteur (upload libre, pas choisi dans le catalogue) est sauvegardé à part, sous `GTFS_import_autre/` sur ce même dataset — jamais dans `GTFS/` (le catalogue principal proposé aux autres visiteurs) — silencieusement, simple filet de sécurité sans validation.

- **Fond de carte CartoDB (Positron/Dark Matter)** : CARTO a coupé l'accès anonyme à ses tuiles `basemaps.cartocdn.com` (tuiles servies filigranées "API KEY REQUIRED" sans clé) ; le repli sans clé (OpenStreetMap) peut lui aussi se faire bloquer (403 "not following the tile usage policy") en usage soutenu. Secret optionnel `CARTO_API_KEY` (Settings → Variables and secrets), obtenu gratuitement sur [carto.com/basemaps/apikey](https://carto.com/basemaps/apikey/) — utilisé par `fond_carte_kwargs`/`tile_layer_cartodb` (cartes Folium/HTML) et `source_contextily_cartodb` (cartes PNG statiques des notebooks, via contextily), les deux dans `src/cartographie.py`. En local (notebook/IDE, dont le kernel n'hérite pas forcément de l'environnement du shell), un fichier `.env` à la racine du dépôt (`CARTO_API_KEY=...`, jamais committé) sert de repli — cf. `_lire_cle_env_locale`.

## Rafraîchissement automatique des GTFS

- **Index de provenance (`data/gtfs_sources.json`, `src/transport_data_gouv.py`)** : associe chaque GTFS de `data/GTFS/` au jeu de données [transport.data.gouv.fr](https://transport.data.gouv.fr) (PAN) dont il provient (page_url/ressource_url/date de mise à jour), rempli automatiquement par la recherche en barre latérale de l'app, ou par `scripts/indexer_gtfs_locaux.py` pour les GTFS uploadés avant l'existence de cette fonctionnalité (le rapprochement se fait sur le contenu réel — `agency.txt` comparé entre le GTFS local et chaque candidat téléchargé — jamais sur le seul nom de fichier, potentiellement renommé depuis).
- **`scripts/rafraichir_gtfs.py`** : vérifie la fraîcheur de chaque GTFS déjà indexé, télécharge/écrase/pousse sur le dataset HF ceux qui ont une mise à jour disponible, et invalide leurs caches dérivés (découpage communal, carroyage, extrait OSM, matrice des temps de trajet) pour que l'app ne serve pas un résultat périmé. `--exclude` permet de signaler une mise à jour sans l'appliquer automatiquement (utilisé pour IDFM/Lyon TCL, dont le retraitement complet prend plusieurs heures). `--force` (ou directement `scripts/rafraichir_gtfs_force.py`, un simple appel avec ce drapeau) retélécharge même les GTFS déjà à jour d'après la source, pour resynchroniser sans se fier à la comparaison de dates. Le recalcul des indicateurs d'accessibilité lui-même n'a jamais lieu ici (r5py/Overpass, trop coûteux) — au prochain passage dans l'app ou le notebook.
- **`.github/workflows/rafraichir-gtfs.yml`** : exécute `rafraichir_gtfs.py` chaque lundi (et sur déclenchement manuel), journalise chaque mise à jour dans `data/journal_maj_gtfs.csv` (commité sur git), et envoie un mail de notification (uniquement s'il y a quelque chose à signaler) via SMTP Gmail. Secrets requis en plus de `HF_TOKEN` : `MAIL_USERNAME`/`MAIL_PASSWORD` (compte Gmail expéditeur + [mot de passe d'application](https://myaccount.google.com/apppasswords)) et `MAIL_DESTINATAIRE`.

## Cas particuliers (grosses agglomérations, réseaux multi-GTFS)

- **Résolution de grille dégradée (`RESOLUTIONS_GRILLE_SPECIALES`, `src/pipeline_donnees.py`)** : la matrice des temps de trajet grandit en O(n²) avec le nombre de carreaux — au-delà d'une certaine taille de réseau, elle ne tient plus en mémoire à la résolution standard de 200m. Carreaux fusionnés en blocs plus grossiers pour les réseaux concernés (résolution spatiale plus grossière sur les cartes/indicateurs de ces réseaux uniquement) :
  - **Lyon/TCL : blocs de 400m** — 92 741 carreaux à 200m produiraient un ttm de 1,22 milliard de lignes, qui dépasse la RAM disponible (32 Go) une fois chargé, même avec les dtypes compacts de `charger_ttm`.
  - **Aix_Marseille : blocs de 800m** — GTFS agrégé "mamp" couvrant une échelle régionale (cf. plus bas), 400m ne suffirait probablement pas.
  - **Lannion/Guingamp : blocs de 400m** — zone rurale de ~150x75km (cf. `src/extraire_gtfs_epci.py` plus bas) : 20 605 carreaux à 200m suffisaient à faire planter le Space en mémoire au chargement du ttm (848 Mo compressé) malgré une population bien plus modeste que Lyon — l'étendue géographique (donc le nombre de paires origine/destination sous 120 min) compte plus que la densité ici.
- **Carroyage 1km au lieu de 200m (`RESEAUX_GRILLE_1KM`, `src/pipeline_donnees.py` / `build_grid_agglo_1km`, `src/build_data_agglo.py`)** : pour les réseaux où même la fusion à 800m/1600m reste trop volumineuse.
  - **IDFM (Île-de-France)** : Paris + petite couronne, nettement plus grand que Lyon ; même 800m ne suffisait pas ("Memory limit exceeded" à 32 Go sur le Space, y compris avec un lot réduit pour `calculer_ttm_par_lots`). Passage en carreaux de 1km sur la base INSEE correspondante (carroyage Filosofi 1km, déjà le grillage publié par l'INSEE — pas de grille théorique à reconstruire ni de fusion à faire, contrairement au 200m) : ~8000 carreaux sur toute l'Île-de-France au lieu de plusieurs centaines de milliers à 200m. Fichier `Filosofi2017_carreaux_1km_met.gpkg` récupéré depuis le cache Hugging Face (`assurer_carreaux_1km_local`) ou à télécharger manuellement depuis insee.fr si absent des deux côtés (pas d'URL directe stable identifiée, contrairement au 200m). Contrepartie : résolution spatiale fixe à 1km (pas de palier intermédiaire), et millésime Filosofi 2017 (le 200m est peut-être plus récent) — décalage temporel mineur avec le BPE 2025.
- **Garde-fou "max 4 agences" et exceptions nommées (`GTFS_NOM_RESEAU_FORCE`, `app.py`)** : un GTFS national/régional regroupant de nombreuses agences ferait exploser les temps de calcul et n'a pas de sens pour les indicateurs proposés ici — bloqué par défaut (`TropAgencesError`), sauf pour les GTFS listés explicitement dans `GTFS_NOM_RESEAU_FORCE` (actuellement IDFM et Aix_Marseille_mamp_GTFS.zip), avec un nom de réseau forcé plutôt que dérivé automatiquement des agences (`nom_reseau_str()` concatène tous les noms d'agence avec " / ", ce qui donnerait une chaîne de plusieurs centaines de caractères pour IDFM — invalide comme nom de fichier).
- **Fusion de plusieurs GTFS (`src/merge_gtfs.py`, upload multi-fichiers dans `app.py`)** : certaines agglomérations ne sont pas couvertes par un seul GTFS (ex: **Aix-Marseille-Provence**, dont le GTFS "Marseille" ne couvre que le réseau RTM, pas celui d'Aix-en-Provence). La barre latérale accepte plusieurs fichiers à la fois (upload et/ou catalogue existant) : au-delà d'un seul fichier sélectionné, ils sont fusionnés (`fusionner_gtfs` — concaténation simple, identifiants préfixés par feed pour éviter les collisions, pas de déduplication d'entités qui se recouvriraient) avant d'entrer dans le pipeline standard. Un champ optionnel permet de forcer le nom du réseau fusionné (ex: `Aix_Marseille`), pour éviter le même problème de nom à rallonge que IDFM si on laisse `nom_reseau_str()` concaténer les agences des GTFS fusionnés.
- **Réseau extrait d'un GTFS régional agrégé (`src/extraire_gtfs_departement.py`, `_epci.py`, `_agence.py`)** : un GTFS régional (ex: Fluo Grand Est, Bretagne KorriGo) peut regrouper des dizaines d'agences sur un territoire bien plus grand qu'un seul réseau urbain — source dans `data/GTFS_Régionaux/`. Trois scripts, même principe (`gtfs_kit.Feed.restrict_to_area`/`restrict_to_agencies`), zone de filtrage différente :
  - `extraire_gtfs_departement.py` : tous les trips avec au moins un arrêt dans un département donné (contour dissous depuis les communes de geo.api.gouv.fr) — TER, réseaux Fluo et urbains locaux inclus automatiquement, filtre géographique plutôt qu'une liste d'agences à maintenir à la main. Utilisé pour la Marne (51), traitée à part via `index_accessibility_notebook_51.ipynb` (nom de réseau forcé à `51-Marne`) et exclue du benchmark inter-réseaux (`RESEAUX_EXCLUS_BENCHMARK`) comme IDFM.
  - `extraire_gtfs_epci.py` : même filtre géographique mais à l'échelle d'un ou plusieurs EPCI (intercommunalités) — zone plus fine qu'un département, ou à cheval sur plusieurs. Utilisé pour Lannion + Guingamp (deux agglomérations voisines) depuis le GTFS Bretagne agrégé.
  - `extraire_gtfs_agence.py` : filtre par `agency_id` plutôt que géographique — pour isoler une seule agence d'un GTFS agrégé (ex: SQY, Saint-Quentin-en-Yvelines, depuis IDFM-gtfs.zip).

  Les extraits produits vivent dans `data/GTFS_agrege/` (sans accent — distinct de `data/GTFS/`, le catalogue principal) et sont proposés dans l'app via la boîte "GTFS modifié pour étude" (sidebar, `gtfs_modifies_choisis` dans `app.py`) — union disque ∪ préfixe `GTFS_agrege/` sur le dataset HF, même mécanique que le catalogue principal mais isolée pour ne pas mélanger des extraits partiels aux GTFS bruts. Ces réseaux sont exemptés du garde-fou "max 4 agences" (peuvent légitimement regrouper TER + cars + réseaux urbains locaux, ex: 5 agences pour Lannion/Guingamp) et jamais traités par `scripts/run_benchmark_batch.py`/`run_GTFS_complet.py`, qui ne scannent que `data/GTFS/`.

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
