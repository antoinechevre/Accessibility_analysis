import pandas as pd
import os
import geopandas as gpd


BASE_DIR = os.getcwd()  # Remonte d'un niveau depuis scripts/

BPE_PATH=os.path.join(BASE_DIR,'data',"BPE25.parquet") # base de données BPE https://catalogue-donnees.insee.fr/fr/catalogue/recherche/DS_BPE 2024


def filtre_BPE (DECOUPAGE_COM_PATH_CSV,population_grid_agglo):

    # BPE25.parquet (INSEE) est un parquet tabulaire classique (colonnes LONGITUDE/
    # LATITUDE, pas de géométrie WKB/métadonnées GeoParquet) : gpd.read_parquet()
    # échoue avec "Missing geo metadata". pd.read_parquet() est la bonne fonction ici.
    BPE_agglo = pd.read_parquet(BPE_PATH) #étape intermédiaire charge l'ensemble de la BDD

    # decoupage_agglo.csv est plus simple que le .geojson ici : il suffit d'une jointure
    # attributaire sur le code commune INSEE (pas besoin de jointure spatiale avec
    # reprojection). code_insee est un int (ex: 17300) ; DEPCOM dans le BPE est une
    # chaîne de 5 caractères (ex: "17300") : on caste et on zero-pad pour faire matcher.
    codes_insee_agglo = (pd.read_csv(DECOUPAGE_COM_PATH_CSV))["code_insee"].astype(str).str.zfill(5)

    BPE_agglo = BPE_agglo[BPE_agglo["DEPCOM"].isin(codes_insee_agglo)].copy() # sélectionne BDD BPE seulement sur découpage agglo.csv 

    print(f"{len(BPE_agglo)} équipements dans l'agglo (sur {codes_insee_agglo.size} communes)")

    # Liste des types d'équipements présents (TYPEQU = code le plus fin de la
    # nomenclature BPE, ex: "C1", "D201"... ; le parquet ne contient pas les libellés
    # associés, seulement les codes). DOM/SDOM donnent des catégories plus larges
    # (domaine / sous-domaine) si TYPEQU est trop détaillé pour ton usage.
    print(BPE_agglo["TYPEQU"].value_counts())

    # Rattachement de BDE_cda au carroyage population_grid_cda : jointure spatiale.
    # LAMBERT_X/LAMBERT_Y du BPE sont déjà en EPSG:2154 (vérifié : identique à la CRS
    # de population_grid_cda), donc pas besoin de reprojection.
    BPE_agglo = gpd.GeoDataFrame(
        BPE_agglo,
        geometry=gpd.points_from_xy(BPE_agglo["LAMBERT_X"], BPE_agglo["LAMBERT_Y"]),
        crs=population_grid_agglo.crs,
    )

    BPE_agglo = gpd.sjoin(
        BPE_agglo,
        population_grid_agglo[["id", "geometry"]],
        predicate="within",
        how="left",
    ).rename(columns={"id": "id_carreau"})
    
    return BPE_agglo


#analyse BPE 1.2

# Ne garder que les carreaux "actifs" pour l'analyse BPE : ceux qui ont de la
# population ou au moins un équipement pondéré (equipements_pondere, calculé en
# cellule 4). Les carreaux vides (ni habitants ni équipement) n'apportent rien
# aux cartes/calculs suivants.

def filtre_BPE_actifs (population_grid_agglo,land_use_data):

    total_carreaux = len(population_grid_agglo)
    carreaux_actifs = land_use_data.loc[
        (land_use_data["population"] > 0) | (land_use_data["equipements_pondere"] > 0),
        "id",
    ]
    population_grid_agglo = population_grid_agglo[population_grid_agglo["id"].isin(carreaux_actifs)].copy()

    print(
        f"{len(population_grid_agglo)} carreaux actifs conservés sur {total_carreaux} "
        "(population ou équipements)"
    )
    return population_grid_agglo

# Cartes de la pondération par gamme des équipements, par domaine BPE et par
# carreau (population_grid_cda) — pas l'accessibilité en temps de trajet, juste
# la donnée d'offre brute (land_use_data_domaine).

def carte_ponderation_domaine(DOMAINES_BPE,population_grid_agglo,BPE_agglo,land_use_data,domaine):
    """Carte interactive (fond OSM) de la pondération cumulée par gamme d'un domaine BPE, par carreau."""
    nom_domaine = DOMAINES_BPE.get(domaine, domaine)
    grille = population_grid_agglo[["id", "geometry"]].merge(land_use_data_domaine(BPE_agglo, land_use_data, domaine), on="id")

    # scheme="NaturalBreaks" plutôt qu'une échelle linéaire brute (défaut de
    # .explore()) : la pondération par domaine est très asymétrique (quelques
    # carreaux avec un score très supérieur au reste, ex: pôle universitaire
    # pour "C"), donc une échelle linéaire écrase la quasi-totalité des
    # carreaux dans une seule couleur sombre. "Quantiles" ne convient pas non
    # plus ici : trop de carreaux à 0 sur un domaine donné, mapclassify
    # réduirait k à 2 classes (0 vs tout le reste).
    # style_kwds : contours des carreaux transparents (weight=0, opacity=0),
    # comme pour overview_map_cda (cellule 2) — sinon le quadrillage noir
    # écrase le fond OSM.
    return grille.explore(
        column=domaine,
        cmap="inferno",
        scheme="NaturalBreaks",
        k=5,
        tiles="OpenStreetMap",
        legend=True,
        legend_kwds={"caption": f"{nom_domaine} (pondéré)"},
        style_kwds={"weight": 0, "opacity": 0},
    )


def mask_domaine_bpe(BPE_agglo,domaine):
    """Masque booléen sur BDE_cda pour un domaine donné. "O" = tous les équipements."""
    if domaine == "O":
        return pd.Series(True, index=BPE_agglo.index)
    return BPE_agglo["TYPEQU"].str.startswith(domaine)


def land_use_data_domaine(BPE_agglo, land_use_data,domaine):
    """
    land_use_data pour un domaine BPE donné (A-G, ou "O" pour tous), avec la
    pondération cumulée par gamme des équipements de ce domaine par carreau
    (poids_gamme, calculé plus haut) — pas un simple comptage.
    """
    ponderation_par_carreau = (
        BPE_agglo[mask_domaine_bpe(BPE_agglo, domaine)]
        .dropna(subset=["id_carreau", "poids_gamme"])
        .groupby("id_carreau")["poids_gamme"]
        .sum()
    )

    df = land_use_data[["id"]].copy()
    df[domaine] = df["id"].map(ponderation_par_carreau).fillna(0.0)
    return df

