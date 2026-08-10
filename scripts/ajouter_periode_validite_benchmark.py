"""
Calcule la période de validité (date_debut/date_fin) et actualise date_JOB
de chaque réseau déjà présent dans l'index de benchmark
(benchmark/index_benchmark_reseaux.csv), à partir de son GTFS local
(data/GTFS/) — cf. src.info_reseau.dates_service, académie comprise pour
éviter les vacances scolaires.

Backfill ponctuel (comme scripts/ajouter_surface_benchmark.py) : plus léger
qu'un recalcul complet (pas de r5py/Overpass), mais charge chaque GTFS en
entier et itère ses jours de service — quelques secondes à ~1 min par
réseau selon sa taille.

Usage :
    .venv/bin/python scripts/ajouter_periode_validite_benchmark.py [--dry-run]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.hf_cache import envoyer_vers_hf, lire_csv_partage
from src.info_reseau import dates_service, nom_reseau_str as _nom_reseau_str
from src.utils import charger_gtfs
from src.vacances_scolaires import departement_academie_zone_pour_feed

BASE_DIR = os.path.dirname(os.path.abspath(__file__)).rsplit(os.sep + "scripts", 1)[0]
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
GTFS_DIR = os.path.join(BASE_DIR, "data", "GTFS")

# Réseaux dont le nom ne peut pas être dérivé automatiquement des agences
# du GTFS — cf. GTFS_NOM_RESEAU_FORCE dans app.py, même liste.
NOMS_RESEAU_FORCES = {
    "IDFM-gtfs_metro-rer-bus-tram_paris-petite-couronne.zip": "IDFM",
    "IDFM-gtfs.zip": "IDFM",
    "Aix_Marseille_mamp_GTFS.zip": "Aix_Marseille",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Calcule et affiche sans modifier/pousser l'index.")
    args = parser.parse_args()

    chemin_local_benchmark = os.path.join(OUTPUT_DIR, "index_benchmark_reseaux.csv")
    tableau = lire_csv_partage("benchmark/index_benchmark_reseaux.csv", chemin_local_benchmark)
    if tableau is None or tableau.empty:
        print("benchmark/index_benchmark_reseaux.csv est vide — rien à faire.")
        return

    reseaux_benchmarkes = set(tableau["reseau"].unique())
    print(f"{len(reseaux_benchmarkes)} réseau(x) dans l'index de benchmark\n")

    fichiers_gtfs = sorted(f for f in os.listdir(GTFS_DIR) if f.lower().endswith(".zip"))
    periodes = {}

    for nom_fichier in fichiers_gtfs:
        chemin_gtfs = os.path.join(GTFS_DIR, nom_fichier)
        try:
            feed = charger_gtfs(chemin_gtfs)
            nom_reseau = NOMS_RESEAU_FORCES.get(nom_fichier) or str(_nom_reseau_str(feed))
        except Exception as e:
            print(f"⚠ {nom_fichier} : impossible de charger ({type(e).__name__}: {e})")
            continue

        if nom_reseau not in reseaux_benchmarkes or nom_reseau in periodes:
            continue

        try:
            _, academie, _ = departement_academie_zone_pour_feed(feed)
        except Exception:
            academie = None
        try:
            _, date_debut, date_fin, date_job = dates_service(feed, academie=academie)
        except Exception as e:
            print(f"⚠ {nom_reseau} ({nom_fichier}) : échec dates_service ({type(e).__name__}: {e})")
            continue

        periodes[nom_reseau] = (date_debut, date_fin, date_job)
        print(f"✓ {nom_reseau} : {date_debut} -> {date_fin} (JOB {date_job})")

    manquants = reseaux_benchmarkes - set(periodes)
    if manquants:
        print(f"\n⚠ {len(manquants)} réseau(x) du benchmark sans GTFS local correspondant : {sorted(manquants)}")

    if args.dry_run:
        print(f"\n[dry-run] {len(periodes)} réseau(x) calculé(s), index non modifié.")
        return

    tableau["date_debut"] = tableau["reseau"].map(lambda r: periodes[r][0] if r in periodes else None)
    tableau["date_fin"] = tableau["reseau"].map(lambda r: periodes[r][1] if r in periodes else None)
    # date_JOB n'est PAS écrasée ici : reflète la date effectivement utilisée
    # pour calculer les indicateurs déjà enregistrés (véh.km, accessibilité) —
    # la mettre à jour sans recalculer ces indicateurs les rendrait
    # incohérents entre eux. La colonne date_JOB "actuelle" recalculée reste
    # visible via ce script en dry-run / son log, pas écrite dans l'index.

    if "population_totale" in tableau.columns:
        idx = tableau.columns.get_loc("population_totale")
        tableau.insert(idx, "date_fin", tableau.pop("date_fin"))
        tableau.insert(idx, "date_debut", tableau.pop("date_debut"))

    tableau.to_csv(chemin_local_benchmark, index=False)
    envoyer_vers_hf(chemin_local_benchmark, "benchmark/index_benchmark_reseaux.csv")

    print(f"\n{len(periodes)}/{len(reseaux_benchmarkes)} réseau(x) avec période de validité renseignée — index mis à jour et poussé sur HF.")


if __name__ == "__main__":
    main()
