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

from src.build_data_agglo import build_decoupage_agglo, decoupage_agglo_geojson, build_grid_agglo
from src.BPE_traitement import filtre_BPE, filtre_BPE_actifs, land_use_data_domaine
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

# Seuil (en multiple de la moyenne du domaine) au-delà duquel un carreau est
# considéré comme un "pôle d'équipements" pour ce domaine (cf. notebook
# "analyse BPE 1.1" et section 9.1/9.2). Utilisé par les cartes "temps d'accès
# au pôle le plus proche" et "pôles accessibles" de views/accessibilite_index.py.
SEUILS_DOMAINE = {
    "A": 1,
    "B": 1,
    "C": 1,
    "D": 1,
    "E": 1,
    "F": 1,
    "G": 1,
    "O": 1.5,
}

GAMMES_POIDS_PAR_DOMAINE = {
    "A": {"Gamme de proximité": 2, "Gamme intermédiaire": 3, "Gamme supérieure": 4, "Hors Gamme": 3},
    "B": {"Gamme de proximité": 2, "Gamme intermédiaire": 4, "Gamme supérieure": 6, "Hors Gamme": 8},
    "C": {"Gamme de proximité": 4, "Gamme intermédiaire": 6, "Gamme supérieure": 8, "Hors Gamme": 10},
    "D": {"Gamme de proximité": 2, "Gamme intermédiaire": 4, "Gamme supérieure": 6, "Hors Gamme": 8},
    "E": {"Gamme de proximité": 2, "Gamme intermédiaire": 4, "Gamme supérieure": 6, "Hors Gamme": 8},
    "F": {"Gamme de proximité": 2, "Gamme intermédiaire": 4, "Gamme supérieure": 6, "Hors Gamme": 8},
    "G": {"Gamme de proximité": 2, "Gamme intermédiaire": 4, "Gamme supérieure": 6, "Hors Gamme": 8},
}


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
    """Télécharge le fichier détail BPE25 depuis insee.fr si absent en local (~160 Mo)."""
    if os.path.exists(BPE_PATH):
        return
    os.makedirs(os.path.dirname(BPE_PATH), exist_ok=True)
    with requests.get(BPE_URL, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(BPE_PATH, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)


def ponderer_bpe(BPE_agglo):
    """Ajoute la colonne poids_gamme à BPE_agglo (cf. notebook "#analyse BPE 1.1")."""
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
        exporter_df_to_csv(
            pd.read_csv(chemins["decoupage_csv"], dtype={"code_insee": str}),
            os.path.join(MEMORY_CSV_AGGLO_DIR, f"decoupage_agglo_{nom_reseau_str}.csv"),
        )
        _step("✓ Découpage communal prêt")

    if not os.path.exists(chemins["decoupage_geojson"]):
        decoupage_agglo_geojson(csv_path=chemins["decoupage_csv"], output_path=chemins["decoupage_geojson"])

    if not os.path.exists(chemins["gpkg"]):
        _step("Construction du carroyage population 200x200 (INSEE)...")
        # build_grid_agglo() écrit toujours dans data/population_grid_agglo.gpkg
        # (chemin fixe côté src/) : on renomme ensuite vers le chemin par réseau.
        build_grid_agglo(chemins["decoupage_geojson"])
        os.replace(os.path.join(DATA_DIR, "population_grid_agglo.gpkg"), chemins["gpkg"])
        _step("✓ Carroyage population prêt")

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
