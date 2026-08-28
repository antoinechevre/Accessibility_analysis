"""
Extrait, depuis un GTFS régional agrégé (plusieurs opérateurs/agences —
ex: data/GTFS_Régionaux/Bretagne_KORRIGOBRET.gtfs.zip), le sous-GTFS d'un ou
plusieurs EPCI (intercommunalités) : tous les trips ayant au moins un arrêt
dans le contour de ces EPCI, peu importe l'agence — donc TER, cars
interurbains (Fluo/BreizhGo...) et réseaux urbains locaux inclus
automatiquement, sans liste d'agences à maintenir à la main. Même logique
que src/extraire_gtfs_departement.py, mais à l'échelle d'un ou plusieurs
EPCI plutôt que d'un département entier — utile quand la zone voulue est
plus fine qu'un département (ex: deux agglomérations voisines) ou à cheval
sur plusieurs départements.

Retrouver le code d'un EPCI (intercommunalité) par son nom :
    curl -s "https://geo.api.gouv.fr/epcis?nom=NOM&fields=nom,code,population"

Usage :
    .venv/bin/python -m src.extraire_gtfs_epci
    .venv/bin/python -m src.extraire_gtfs_epci --epci 200065928 200067981 \\
        --gtfs Bretagne_KORRIGOBRET.gtfs.zip --sortie data/GTFS_agrege/Lannion_Guingamp_gtfs.zip
"""

import argparse
import os
import shutil
import tempfile
from pathlib import Path

import geopandas as gpd
import gtfs_kit as gk  # noqa: F401  (déclenche l'import tôt, erreurs de dépendance visibles avant le chargement)
import pandas as pd

from src.build_data_agglo import session_avec_retries
from src.utils import charger_gtfs

BASE_DIR = Path(__file__).resolve().parent.parent

GEO_API_COMMUNES_URL = "https://geo.api.gouv.fr/communes"

# Lannion-Trégor Communauté (200065928) + Guingamp-Paimpol Agglomération
# (200067981) — valeurs par défaut de ce script pour l'usage initial
# (extraction de la zone Lannion/Guingamp depuis le GTFS Bretagne agrégé).
EPCI_DEFAUT = ["200065928", "200067981"]
GTFS_ENTREE_DEFAUT = "Bretagne_KORRIGOBRET.gtfs.zip"
NOM_SORTIE_DEFAUT = "Lannion_Guingamp_gtfs.zip"


def contour_epci(codes_epci, session=None, timeout=30):
    """GeoDataFrame (une ligne, géométrie unique) du contour dissous de l'union
    des communes d'un ou plusieurs EPCI (intercommunalités) — geo.api.gouv.fr
    ne publie pas de contour direct au niveau EPCI, seulement au niveau
    commune (même approche que contour_departement dans
    src/extraire_gtfs_departement.py).

    codes_epci : liste de codes EPCI (ex: ["200065928", "200067981"]).
    """
    session = session or session_avec_retries()
    toutes_communes = []
    for code_epci in codes_epci:
        reponse = session.get(
            GEO_API_COMMUNES_URL,
            params={"codeEpci": code_epci, "geometry": "contour", "format": "geojson"},
            timeout=timeout,
        )
        reponse.raise_for_status()
        communes = gpd.GeoDataFrame.from_features(reponse.json()["features"], crs="EPSG:4326")
        if communes.empty:
            raise ValueError(f"Aucune commune renvoyée par geo.api.gouv.fr pour l'EPCI {code_epci!r}")
        print(f"  EPCI {code_epci} : {len(communes)} commune(s)")
        toutes_communes.append(communes)

    communes = gpd.GeoDataFrame(pd.concat(toutes_communes, ignore_index=True), crs="EPSG:4326")
    communes.geometry = communes.geometry.buffer(0)  # corrige d'éventuelles géométries invalides avant dissolution
    contour = gpd.GeoDataFrame(geometry=[communes.union_all()], crs=communes.crs)
    print(f"✓ Contour de {len(codes_epci)} EPCI : {len(communes)} commune(s) au total, dissoutes")
    return contour


def extraire_gtfs_epci(chemin_zip_entree, codes_epci, chemin_zip_sortie):
    """
    Écrit dans chemin_zip_sortie le sous-ensemble de chemin_zip_entree
    (trips + tables associées) ayant au moins un arrêt dans le contour des
    EPCI codes_epci — cf. gtfs_kit.Feed.restrict_to_area.

    Le fichier source n'est jamais modifié en place : comme dans
    src/extraire_gtfs_departement.py, il est d'abord copié dans un fichier
    temporaire avant chargement (charger_gtfs() peut réécrire un zip en
    place).

    Returns
    -------
    gtfs_kit Feed object
        Le feed restreint aux EPCI.
    """
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        chemin_tmp = tmp.name
    shutil.copy(chemin_zip_entree, chemin_tmp)

    try:
        print(f"Chargement de {os.path.basename(chemin_zip_entree)}...")
        feed = charger_gtfs(chemin_tmp)
        print(f"  {len(feed.trips)} trip(s), {len(feed.agency)} agence(s) avant filtrage")

        area = contour_epci(codes_epci)

        print(f"Restriction aux EPCI {codes_epci}...")
        feed_epci = feed.restrict_to_area(area)
        nb_agences = feed_epci.routes["agency_id"].nunique() if "agency_id" in feed_epci.routes.columns else "?"
        print(
            f"  → {len(feed_epci.trips)} trip(s) conservé(s) "
            f"({len(feed_epci.routes)} route(s), {nb_agences} agence(s))"
        )

        os.makedirs(os.path.dirname(chemin_zip_sortie), exist_ok=True)
        feed_epci.to_file(chemin_zip_sortie)
        print(f"✓ GTFS restreint aux EPCI {codes_epci} enregistré dans : {chemin_zip_sortie}")
    finally:
        os.unlink(chemin_tmp)

    return feed_epci


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gtfs", default=GTFS_ENTREE_DEFAUT, help="Nom du fichier dans data/GTFS_agrégé/")
    parser.add_argument(
        "--epci", nargs="+", default=EPCI_DEFAUT,
        help="Code(s) EPCI à extraire (cf. https://geo.api.gouv.fr/epcis?nom=... pour les retrouver)",
    )
    parser.add_argument(
        "--sortie", default=None,
        help=f"Chemin complet du GTFS extrait (défaut : data/GTFS_agrégé/{NOM_SORTIE_DEFAUT} — cf. "
        "app.py, sélecteur \"GTFS modifié pour étude\", distinct du catalogue principal data/GTFS/)",
    )
    args = parser.parse_args()

    chemin_entree = os.path.join(BASE_DIR, "data", "GTFS_agrégé", args.gtfs)
    chemin_sortie = args.sortie or os.path.join(BASE_DIR, "data", "GTFS_agrégé", NOM_SORTIE_DEFAUT)

    extraire_gtfs_epci(chemin_entree, args.epci, chemin_sortie)
