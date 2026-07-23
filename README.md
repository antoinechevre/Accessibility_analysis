---
title: Accessibility Analysis
emoji: 🚌
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# Accessibility Analysis

Analyse de l'accessibilité piétonne / transports collectifs (< 30 min) aux équipements d'une agglomération française, à partir d'un GTFS quelconque.

Le projet s'inspire des travaux du livre *Introduction to urban accessibility* (Rafael H. M. Pereira et Daniel Herszenhut, Ipea - Institute for Applied Economic Research), notamment le chapitre [Calculating accessibility estimates in R](https://ipeagit.github.io/intro_access_book/3_calculando_acesso.en.html), réadaptés ici en Python pour un contexte français (carroyage INSEE, Base Permanente des Équipements).

**Concepteur :** Antoine Chèvre (avec Claude.ai)

## Ce que fait le projet

À partir d'un GTFS et d'un découpage communal :

1. Construit le réseau multimodal piéton + transport collectif (`r5py`) à partir du GTFS pour une date JOB indiquée et le réseau viaire pour les cheminements piétons 
2. Récupère le carroyage population INSEE 200x200 2019 incluant les catégories socio économiques (Filosofi) et la Base Permanente des Équipements (BPE, INSEE) 
3. Pondère les équipements par gamme (proximité / intermédiaire / supérieure / hors gamme) et par domaine (santé, enseignement, commerces...) avec une pondération des équipements dans le fichier src/ponderation_bpe.py: 
  2.1 Liste des équipements BPE - cf https://vscode.dev/github/antoinechevre/Accessibility_analysis/blob/main/data/BPE25_anonymisee_dessin_fichier.html 
    "O": "Tout équipements pondérés",
    "A": "Services pour les particuliers",
    "B": "Commerces",
    "C": "Enseignement",
    "D": "Santé et action sociale",
    "E": "Transports et déplacements",
    "F": "Sports, loisirs et culture",
    "G": "Tourisme",
  2.2 pondération selon la classification
    "Gamme de proximité"
    "Gamme intermédiaire"
    "Gamme supérieure"
    "Hors Gamme"
  2.3 des seuils sur pondération par carreaux de 200x200 et par domaine par rapport à la moyenne de pondération des carreaux par domaines (objectif de filtrer les carreaux significatifs)  

4. Calcule la matrice des temps de trajet (`TravelTimeMatrix`) entre tous les carreaux avec GTFS / Piétons OSM à l'heure de pointe en JOB 
5. Calcule plusieurs indicateurs d'accessibilité : 
  5.1 opportunités cumulées, 
  5.2 coût au plus proche, 
  5.3 gravité, 
  5.4 compétition (Enhanced 2SFCA).
6. Exporte des cartes interactives (HTML/Folium) et statiques (PNG) par domaine d'équipement avec une déclinaison par déciles de population


## Structure du dépôt

```
index_accessibility_notebook_def.ipynb   # notebook principal : pipeline d'analyse complet
app.py                                    # application Streamlit (en cours de développement)
views/                                    # pages de l'app Streamlit
src/
  build_data_agglo.py                     # découpage communal, grille, extraction OSM
  BPE_traitement.py                       # filtrage/pondération BPE, cartes par domaine
  utilitaires_matrix.py                   # cumulative_cutoff, cost_to_closest, gravity, 2SFCA
  utils.py                                # chargement GTFS, exports CSV/GeoJSON, dir_tree
  info_reseau.py, i18n.py, ...            # utilitaires réseau / traductions app
data/                                      # GTFS, carroyage INSEE, BPE, fichiers générés (non versionné)
output/                                    # cartes et images exportées (non versionné)
requirements.txt
```

> ⚠️ `app.py` est fonctionnel : `views/accessibilite_index.py` (pipeline complet + r5py) et `views/ponderation_equipements.py` (cartes de pondération BPE, sans r5py) sont toutes deux implémentées. Le notebook reste la référence de calcul en cas de doute.

## Déploiement

- **Streamlit Community Cloud** : `packages.txt` installe Java (`default-jdk-headless`) et `osmium-tool` via apt. Le tier gratuit (~1 Go RAM) est cependant limite pour ce pipeline (JVM r5py + carroyage INSEE 1,1 Go).
- **Hugging Face Spaces (SDK Docker)** : `Dockerfile` fourni, plus adapté (tier gratuit ~16 Go RAM / 2 vCPU).
- **Tier payant (Hugging Face Spaces, hardware upgrade)** : aucun changement de code nécessaire, juste changer le hardware du Space dans ses paramètres (Settings → Space hardware). Pour que le calcul profite réellement de la RAM supplémentaire, remonter aussi la mémoire allouée à la JVM r5py via la variable d'environnement `R5PY_MAX_JVM_MEMORY_MB` (Settings → Variables and secrets), sans quoi elle reste plafonnée à 512 Mo par défaut (cf. `Dockerfile` et `views/accessibilite_index.py`). Penser aussi à activer le stockage persistant du Space pour conserver le cache disque (`data/decoupage_agglo.*`, `data/agglo.osm.pbf`, `data/ttm_<réseau>.parquet`) entre les redémarrages, sans quoi il est reconstruit à chaque fois.
- **Cache de secours Hugging Face (lecture + écriture)** : le contenu de `data/` (BPE, carroyage INSEE, GTFS, extraits OSM et matrices de temps de trajet déjà calculées par réseau) est sauvegardé dans le dataset privé [antoinechevre/accessibility-data](https://huggingface.co/datasets/antoinechevre/accessibility-data) (cf. `src/hf_cache.py`). Avant tout téléchargement/calcul coûteux, le pipeline regarde d'abord si le fichier existe déjà dans ce dataset (`recuperer_depuis_hf`) ; sans stockage persistant sur le Space, c'est ce qui évite de tout reconstruire (dont le calcul r5py, potentiellement long) à chaque redémarrage. Symétriquement, après le calcul d'un réseau **encore jamais traité** (découpage communal, extrait OSM, matrice des temps de trajet), le résultat est renvoyé vers ce même dataset (`envoyer_vers_hf`) pour que les déploiements suivants — y compris d'autres visiteurs du Space — en profitent aussi, sans avoir à repousser manuellement depuis un poste local. Nécessite un secret `HF_TOKEN` (Settings → Variables and secrets) avec accès **lecture et écriture** au dataset — sans lui, le pipeline se rabat silencieusement sur le calcul/téléchargement habituel (lecture) ou n'envoie simplement rien (écriture), sans jamais faire échouer le calcul en cours.

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

Projet personnel en développement actif (2026). Le notebook constitue la référence fonctionnelle ; l'application Streamlit (`app.py`) est une interface en construction pour rendre l'analyse accessible sans passer par le notebook.
