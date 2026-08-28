"""
Extrait, depuis un GTFS régional agrégé (plusieurs opérateurs/agences), les
seuls trips ayant au moins un arrêt dans un département donné.

Cas d'usage : data/GTFS_Régionaux/REGION_GRANDEST.gtfs.zip agrège une
cinquantaine d'agences (TER, réseaux Fluo par département, réseaux urbains
Reims/Châlons-en-Champagne/Épernay/Nancy/Strasbourg...) sur toute la région
Grand Est — beaucoup trop volumineux et hétérogène pour être traité tel
quel par le pipeline (nom de réseau, ttm r5py...). Ce script en extrait un
sous-GTFS limité à un seul département (ex: la Marne, 51), en ne filtrant
que sur la géométrie des arrêts : peu importe l'agence, TOUT trip touchant
le département est conservé (donc TER, Fluo, réseaux urbains locaux...
inclus automatiquement, sans liste d'agences à maintenir à la main).

Contour du département récupéré via geo.api.gouv.fr (pas d'endpoint
contour au niveau département sur cette API : dissout ici l'union des
contours de ses communes, même logique que build_decoupage_agglo dans
src/build_data_agglo.py pour une agglo).

Usage :
    .venv/bin/python -m src.extraire_gtfs_departement
    .venv/bin/python -m src.extraire_gtfs_departement --departement 51 --gtfs REGION_GRANDEST.gtfs.zip
"""

import argparse
import os
import shutil
import tempfile
from pathlib import Path

import geopandas as gpd
import gtfs_kit as gk

from src.build_data_agglo import session_avec_retries
from src.utils import charger_gtfs

BASE_DIR = Path(__file__).resolve().parent.parent

GEO_API_COMMUNES_URL = "https://geo.api.gouv.fr/communes"


def contour_departement(code_departement, session=None, timeout=30):
    """GeoDataFrame (une ligne, géométrie unique) du contour d'un
    département français, par dissolution des contours de ses communes
    (geo.api.gouv.fr ne publie pas de contour au niveau département).

    code_departement : code INSEE du département (ex: "51" pour la Marne).
    """
    session = session or session_avec_retries()
    reponse = session.get(
        GEO_API_COMMUNES_URL,
        params={"codeDepartement": code_departement, "geometry": "contour", "format": "geojson"},
        timeout=timeout,
    )
    reponse.raise_for_status()
    communes = gpd.GeoDataFrame.from_features(reponse.json()["features"], crs="EPSG:4326")
    if communes.empty:
        raise ValueError(f"Aucune commune renvoyée par geo.api.gouv.fr pour le département {code_departement!r}")

    communes.geometry = communes.geometry.buffer(0)  # corrige d'éventuelles géométries invalides avant dissolution
    contour = gpd.GeoDataFrame(geometry=[communes.union_all()], crs=communes.crs)
    print(f"✓ Contour du département {code_departement} : {len(communes)} commune(s) dissoute(s)")
    return contour


def extraire_gtfs_departement(chemin_zip_entree, code_departement, chemin_zip_sortie):
    """
    Écrit dans chemin_zip_sortie le sous-ensemble de chemin_zip_entree
    (trips + tables associées) ayant au moins un arrêt dans le département
    code_departement — cf. gtfs_kit.Feed.restrict_to_area.

    Le fichier source n'est jamais modifié en place : comme dans
    scripts/run_benchmark_batch.py, il est d'abord copié dans un fichier
    temporaire avant chargement, car charger_gtfs() peut réécrire un zip en
    place (aplatissement de sous-dossier, tables vides, tabulations
    parasites...).

    Returns
    -------
    gtfs_kit Feed object
        Le feed restreint au département.
    """
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        chemin_tmp = tmp.name
    shutil.copy(chemin_zip_entree, chemin_tmp)

    try:
        print(f"Chargement de {os.path.basename(chemin_zip_entree)}...")
        feed = charger_gtfs(chemin_tmp)
        print(f"  {len(feed.trips)} trip(s), {len(feed.agency)} agence(s) avant filtrage")

        area = contour_departement(code_departement)

        print(f"Restriction au département {code_departement}...")
        feed_departement = feed.restrict_to_area(area)
        nb_agences = feed_departement.routes["agency_id"].nunique() if "agency_id" in feed_departement.routes.columns else "?"
        print(
            f"  → {len(feed_departement.trips)} trip(s) conservé(s) "
            f"({len(feed_departement.routes)} route(s), {nb_agences} agence(s))"
        )

        os.makedirs(os.path.dirname(chemin_zip_sortie), exist_ok=True)
        feed_departement.to_file(chemin_zip_sortie)
        print(f"✓ GTFS restreint au département {code_departement} enregistré dans : {chemin_zip_sortie}")
    finally:
        os.unlink(chemin_tmp)

    return feed_departement


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gtfs", default="REGION_GRANDEST.gtfs.zip", help="Nom du fichier dans data/GTFS_Régionaux/")
    parser.add_argument("--departement", default="51", help="Code INSEE du département (ex: 51 pour la Marne)")
    parser.add_argument(
        "--dossier-sortie", default="GTFS_agrege",
        help="Sous-dossier de data/ où écrire le GTFS extrait (défaut : GTFS_agrege — extraits prêts à "
        "l'étude, cf. app.py \"GTFS modifié pour étude\" ; passer GTFS pour le déposer directement dans "
        "le catalogue principal).",
    )
    parser.add_argument("--sortie", default=None, help="Chemin complet du GTFS extrait (prioritaire sur --dossier-sortie)")
    args = parser.parse_args()

    chemin_entree = os.path.join(BASE_DIR, "data", "GTFS_Régionaux", args.gtfs)
    chemin_sortie = args.sortie or os.path.join(
        BASE_DIR, "data", args.dossier_sortie, f"{args.departement}_{os.path.splitext(args.gtfs)[0]}.zip"
    )

    extraire_gtfs_departement(chemin_entree, args.departement, chemin_sortie)
