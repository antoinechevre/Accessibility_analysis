"""
Extrait, depuis un GTFS régional agrégé multi-agences (ex: IDFM-gtfs.zip,
Île-de-France Mobilités — RATP, SNCF Transilien, et tous les réseaux de bus
locaux dont Saint-Quentin-en-Yvelines), le sous-GTFS d'une seule agence :
routes/trips/stops/calendriers... restreints à cette agence uniquement (cf.
gtfs_kit.Feed.restrict_to_agencies), pour un réseau traitable par le
pipeline (ex: run_benchmark_batch.py) sans l'échelle disproportionnée du
GTFS complet (cf. IDFM-gtfs.zip exclu du benchmark dans
run_benchmark_batch.py — échelle de population incomparable aux autres
réseaux).

Même précaution que src/extraire_gtfs_departement.py : le fichier source
n'est jamais modifié en place (copié dans un temp file avant chargement, car
charger_gtfs() peut réécrire un zip en place).

Usage :
    .venv/bin/python -m src.extraire_gtfs_agence
    .venv/bin/python -m src.extraire_gtfs_agence --agency-id "IDFM:1042" --sortie data/GTFS_agrégé/AutreReseau.zip

Pour retrouver l'agency_id d'un autre réseau dans un GTFS agrégé :
    python -c "
    import zipfile, pandas as pd
    with zipfile.ZipFile('data/GTFS/IDFM-gtfs.zip').open('agency.txt') as f:
        df = pd.read_csv(f)
    print(df[df['agency_name'].str.contains('nom du réseau', case=False)])
    "
"""

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Insertion nécessaire pour que `from src...` fonctionne aussi quand ce
# fichier est lancé directement (python src/extraire_gtfs_agence.py), pas
# seulement via `python -m src.extraire_gtfs_agence` depuis la racine — même
# correctif que scripts/run_benchmark_batch.py.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils import charger_gtfs

BASE_DIR = Path(__file__).resolve().parent.parent

# Saint-Quentin-en-Yvelines dans IDFM-gtfs.zip (agency.txt, agency_id
# "IDFM:1068", agency_name "Saint-Quentin-en-Yvelines") — valeurs par défaut
# de ce script, jamais garanties stables d'un export IDFM à l'autre (id
# ré-attribué en cas de changement d'exploitant) : à revérifier si
# l'extraction ne renvoie soudain plus aucun trip.
GTFS_ENTREE_DEFAUT = "IDFM-gtfs.zip"
AGENCY_ID_DEFAUT = "IDFM:1068"
NOM_SORTIE_DEFAUT = "SQY.zip"


def extraire_gtfs_agence(chemin_zip_entree, agency_id, chemin_zip_sortie):
    """
    Écrit dans chemin_zip_sortie le sous-ensemble de chemin_zip_entree
    (trips + tables associées) restreint à l'agence agency_id.

    Returns
    -------
    gtfs_kit Feed object
        Le feed restreint à l'agence.
    """
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        chemin_tmp = tmp.name
    shutil.copy(chemin_zip_entree, chemin_tmp)

    try:
        print(f"Chargement de {os.path.basename(chemin_zip_entree)}...")
        feed = charger_gtfs(chemin_tmp)
        print(f"  {len(feed.trips)} trip(s), {len(feed.agency)} agence(s) avant filtrage")

        if agency_id not in feed.agency["agency_id"].values:
            agences_disponibles = feed.agency[["agency_id", "agency_name"]].to_string(index=False)
            raise ValueError(
                f"agency_id {agency_id!r} introuvable dans {chemin_zip_entree!r}.\n"
                f"Agences disponibles :\n{agences_disponibles}"
            )

        print(f"Restriction à l'agence {agency_id}...")
        feed_agence = feed.restrict_to_agencies([agency_id])
        print(f"  → {len(feed_agence.trips)} trip(s) conservé(s) ({len(feed_agence.routes)} route(s))")

        os.makedirs(os.path.dirname(chemin_zip_sortie), exist_ok=True)
        feed_agence.to_file(chemin_zip_sortie)
        print(f"✓ GTFS restreint à l'agence {agency_id} enregistré dans : {chemin_zip_sortie}")
    finally:
        os.unlink(chemin_tmp)

    return feed_agence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gtfs", default=GTFS_ENTREE_DEFAUT, help="Nom du fichier dans data/GTFS/")
    parser.add_argument("--agency-id", default=AGENCY_ID_DEFAUT, help="agency_id à extraire (cf. agency.txt du GTFS source)")
    parser.add_argument(
        "--sortie", default=None,
        help=f"Chemin complet du GTFS extrait (défaut : data/GTFS_agrégé/{NOM_SORTIE_DEFAUT} — cf. "
        "app.py, sélecteur \"GTFS modifié pour étude\", distinct du catalogue principal data/GTFS/)",
    )
    args = parser.parse_args()

    chemin_entree = os.path.join(BASE_DIR, "data", "GTFS", args.gtfs)
    chemin_sortie = args.sortie or os.path.join(BASE_DIR, "data", "GTFS_agrégé", NOM_SORTIE_DEFAUT)

    extraire_gtfs_agence(chemin_entree, args.agency_id, chemin_sortie)
