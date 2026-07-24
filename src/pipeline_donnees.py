"""
Construction des données partagées par les pages Streamlit d'accessibilité :
découpage communal, carroyage population INSEE et BPE filtrée/pondérée.
Reprend les cellules "analyse BPE 1.1/1.2" du notebook
index_accessibility_notebook_def.ipynb, avec mise en cache disque par réseau
(nom_reseau_str) pour ne pas tout relancer à chaque rerun Streamlit.

Ne construit PAS le réseau de transport ni la matrice des temps de trajet
(r5py/osmium) : cf. views/accessibilite_index.py pour la suite du pipeline.
"""

import os

import geopandas as gpd
import pandas as pd
import requests

from src.build_data_agglo import (
    build_decoupage_agglo,
    decoupage_agglo_geojson,
    build_grid_agglo,
    fusionner_grille_resolution,
)
from src.BPE_traitement import filtre_BPE, filtre_BPE_actifs, land_use_data_domaine
from src.hf_cache import HF_DATA_REPO_ID, envoyer_vers_hf, recuperer_depuis_hf
from src.ponderation_bpe import GAMMES_POIDS_PAR_DOMAINE, SEUILS_DOMAINE
from src.utils import exporter_df_to_csv

BASE_DIR = os.getcwd()
DATA_DIR = os.path.join(BASE_DIR, "data")
MEMORY_CSV_AGGLO_DIR = os.path.join(DATA_DIR, "memory_csv_agglo")
BPE_PATH = os.path.join(DATA_DIR, "BPE25.parquet")
BPE_XLS_PATH = os.path.join(DATA_DIR, "BPE_gammes_equipements_2025.xlsx")

# Fichier détail BPE25 (géolocalisé, LAMBERT_X/LAMBERT_Y) : cf. cellule
# "#import BPE" du notebook pour comment cette URL a été trouvée.
BPE_URL = "https://www.insee.fr/fr/statistiques/fichier/8217525/BPE25.parquet"

DOMAINES_BPE = {
    "O": "Tout équipements pondérés",
    "A": "Services pour les particuliers",
    "B": "Commerces",
    "C": "Enseignement",
    "D": "Santé et action sociale",
    "E": "Transports et déplacements",
    "F": "Sports, loisirs et culture",
    "G": "Tourisme",
}

# Réseaux dont la grille 200m est trop grosse pour tenir en mémoire une fois
# la matrice des temps de trajet chargée (cf. src.utilitaires_matrix.charger_ttm) :
# carreaux fusionnés en blocs de resolution mètres avant tout calcul (cf.
# fusionner_grille_resolution), au prix d'une résolution spatiale plus
# grossière sur ces réseaux.
# - Lyon/TCL (400m) : 92 741 carreaux à 200m -> ttm de 1,22 milliard de
#   lignes, qui fait planter le calcul (mémoire) même à 32 Go de RAM
#   disponible une fois chargé en mémoire — observé à plusieurs reprises
#   avant ce contournement.
# - IDFM (800m) : Île-de-France, nettement plus grande que Lyon ; 400m ne
#   suffirait probablement pas. Ce GTFS (Paris + petite couronne 75/92/93/94
#   uniquement, pas la grande couronne) est aussi une exception au garde-fou
#   "max 4 agences" de l'app — cf. GTFS_NOM_RESEAU_FORCE dans app.py.
RESOLUTIONS_GRILLE_SPECIALES = {"TCL": 400, "IDFM": 800}

def chemins_reseau(nom_reseau_str):
    """Chemins de cache disque (par réseau) utilisés par le pipeline."""
    return {
        "decoupage_csv": os.path.join(DATA_DIR, f"decoupage_agglo_{nom_reseau_str}.csv"),
        "decoupage_geojson": os.path.join(DATA_DIR, f"decoupage_agglo_{nom_reseau_str}.geojson"),
        "osm_pbf": os.path.join(DATA_DIR, f"agglo_{nom_reseau_str}.osm.pbf"),
        "gpkg": os.path.join(DATA_DIR, f"population_grid_agglo_{nom_reseau_str}.gpkg"),
        "ttm": os.path.join(DATA_DIR, f"ttm_{nom_reseau_str}.parquet"),
    }


def assurer_bpe_local():
    """Récupère le fichier détail BPE25 (~160 Mo) si absent en local : d'abord
    depuis le cache HF (plus rapide, déjà téléversé), sinon depuis insee.fr."""
    if os.path.exists(BPE_PATH):
        return
    if recuperer_depuis_hf("BPE25.parquet", BPE_PATH):
        return
    os.makedirs(os.path.dirname(BPE_PATH), exist_ok=True)
    with requests.get(BPE_URL, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(BPE_PATH, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)


def assurer_bpe_xls_local():
    """Récupère depuis le cache HF le fichier des gammes d'équipements
    BPE_gammes_equipements_2025.xlsx si absent en local : fichier propre à ce
    projet (pas de source publique équivalente à télécharger)."""
    if os.path.exists(BPE_XLS_PATH):
        return
    if not recuperer_depuis_hf("BPE_gammes_equipements_2025.xlsx", BPE_XLS_PATH):
        raise FileNotFoundError(
            f"{BPE_XLS_PATH} introuvable en local et absent du dataset HF {HF_DATA_REPO_ID}."
        )


def ponderer_bpe(BPE_agglo):
    """Ajoute la colonne poids_gamme à BPE_agglo (cf. notebook "#analyse BPE 1.1")."""
    assurer_bpe_xls_local()
    gamme_typequ = pd.read_excel(
        BPE_XLS_PATH,
        sheet_name="Gammes 2025 1 ligne 1 Typequ",
        header=4,
    )[["TYPEQU", "GAMME"]]

    BPE_agglo = BPE_agglo.merge(gamme_typequ, on="TYPEQU", how="left")

    table_poids_domaine_gamme = pd.DataFrame(
        [
            {"domaine": domaine, "GAMME": gamme, "poids_gamme": poids}
            for domaine, poids_par_gamme in GAMMES_POIDS_PAR_DOMAINE.items()
            for gamme, poids in poids_par_gamme.items()
        ]
    )
    BPE_agglo["domaine"] = BPE_agglo["TYPEQU"].str[0]
    BPE_agglo = BPE_agglo.merge(table_poids_domaine_gamme, on=["domaine", "GAMME"], how="left")
    return BPE_agglo


def construire_donnees_bpe(zip_path, nom_reseau_str, on_step=None):
    """Construit (ou recharge depuis le cache disque) le découpage communal,
    le carroyage population INSEE et la BPE filtrée/pondérée pour ce réseau.

    on_step: callback optionnel appelé avec un message avant chaque étape
        potentiellement longue (ex. st.spinner côté appelant Streamlit).

    Ne construit pas le réseau de transport ni la matrice des temps de trajet
    (osmium/r5py) : cf. views/accessibilite_index.py pour la suite.
    """
    def _step(message):
        if on_step is not None:
            on_step(message)

    chemins = chemins_reseau(nom_reseau_str)

    decoupage_reference_path = os.path.join(MEMORY_CSV_AGGLO_DIR, f"decoupage_agglo_{nom_reseau_str}.csv")
    recuperer_depuis_hf(f"memory_csv_agglo/decoupage_agglo_{nom_reseau_str}.csv", decoupage_reference_path)
    if not os.path.exists(decoupage_reference_path):
        decoupage_reference_path = None

    if not os.path.exists(chemins["decoupage_csv"]):
        _step("Identification des communes desservies par le GTFS...")
        build_decoupage_agglo(
            gtfs_path=zip_path,
            output_path=chemins["decoupage_csv"],
            decoupage_reference_path=decoupage_reference_path,
        )
        os.makedirs(MEMORY_CSV_AGGLO_DIR, exist_ok=True)
        chemin_memoire_decoupage = os.path.join(MEMORY_CSV_AGGLO_DIR, f"decoupage_agglo_{nom_reseau_str}.csv")
        exporter_df_to_csv(
            pd.read_csv(chemins["decoupage_csv"], dtype={"code_insee": str}),
            chemin_memoire_decoupage,
        )
        envoyer_vers_hf(chemin_memoire_decoupage, f"memory_csv_agglo/decoupage_agglo_{nom_reseau_str}.csv")
        _step("✓ Découpage communal prêt")

    if not os.path.exists(chemins["decoupage_geojson"]):
        decoupage_agglo_geojson(csv_path=chemins["decoupage_csv"], output_path=chemins["decoupage_geojson"])

    if not os.path.exists(chemins["gpkg"]):
        _step("Construction du carroyage population 200x200 (INSEE)...")
        # Chemin de sortie par réseau explicite (pas le chemin générique par
        # défaut de build_grid_agglo) : un run concurrent pour un autre réseau
        # (app ou notebook tournant en parallèle sur la même machine) ne doit
        # jamais pouvoir écrire/renommer le même fichier partagé.
        grille = build_grid_agglo(chemins["decoupage_geojson"], output_path=chemins["gpkg"])
        _step("✓ Carroyage population prêt")

        resolution_speciale = RESOLUTIONS_GRILLE_SPECIALES.get(nom_reseau_str)
        if resolution_speciale is not None:
            # chemins["gpkg"] est déjà scopé par réseau (pas le chemin
            # générique) : l'écraser ici par la version fusionnée ne risque
            # aucune collision avec un autre run, et les prochains lancements
            # pour ce réseau retrouveront directement la version fusionnée en
            # cache (le test os.path.exists ci-dessus ne distingue pas la
            # résolution, juste la présence du fichier).
            grille = fusionner_grille_resolution(grille, resolution=resolution_speciale)
            grille.to_file(chemins["gpkg"], driver="GPKG")

    resolution_speciale = RESOLUTIONS_GRILLE_SPECIALES.get(nom_reseau_str)
    if resolution_speciale is not None:
        _step(
            f"⚠ {nom_reseau_str} : réseau trop grand pour une analyse à 200m (matrice des "
            "temps de trajet trop volumineuse pour tenir en mémoire) — carreaux fusionnés "
            f"en blocs de {resolution_speciale}m, résolution spatiale plus grossière sur les "
            "cartes et indicateurs de ce réseau."
        )

    population_grid_agglo = gpd.read_file(chemins["gpkg"])
    land_use_data = population_grid_agglo[["id", "population"]].copy()

    _step("Vérification de la base BPE (téléchargement si absente)...")
    assurer_bpe_local()
    _step("✓ Base BPE disponible")

    _step("Filtrage et pondération de la base BPE...")
    BPE_agglo = filtre_BPE(chemins["decoupage_csv"], population_grid_agglo)
    BPE_agglo = ponderer_bpe(BPE_agglo)

    equipements_pondere_par_carreau = (
        BPE_agglo.dropna(subset=["id_carreau", "poids_gamme"])
        .groupby("id_carreau")["poids_gamme"]
        .sum()
    )
    land_use_data["equipements_pondere"] = (
        land_use_data["id"].map(equipements_pondere_par_carreau).fillna(0.0)
    )

    population_grid_agglo = filtre_BPE_actifs(population_grid_agglo, land_use_data)
    _step(f"✓ BPE pondérée — {len(population_grid_agglo)} carreaux actifs")

    # Restreint aux mêmes carreaux actifs que population_grid_agglo : sans ça,
    # les seuils "pôles" ci-dessous seraient tirés vers le bas par les carreaux
    # vides (cf. notebook "analyse BPE 1.1").
    land_use_data = land_use_data[land_use_data["id"].isin(population_grid_agglo["id"])].reset_index(drop=True)

    # Colonnes pole_equipements_{domaine} : carreau au-dessus de
    # SEUILS_DOMAINE[domaine] fois la moyenne du domaine (cf. notebook
    # "analyse BPE 1.1" et sections 9.1/9.2).
    for domaine, seuil_pct in SEUILS_DOMAINE.items():
        valeurs_domaine = land_use_data_domaine(BPE_agglo, land_use_data, domaine)
        seuil = seuil_pct * valeurs_domaine[domaine].mean()
        land_use_data[f"pole_equipements_{domaine}"] = (valeurs_domaine[domaine] > seuil).astype(int)

    return population_grid_agglo, land_use_data, BPE_agglo
