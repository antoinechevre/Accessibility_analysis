"""
Calcule la surface (km²) de l'agglomération de chaque réseau déjà présent
dans l'index de benchmark (benchmark/index_benchmark_reseaux.csv) à partir
de son découpage communal (data/memory_csv_agglo/decoupage_agglo_<reseau>.csv,
colonne "geojson" — mêmes géométries communales que celles utilisées pour
construire le carroyage population, cf. src/build_data_agglo.py) et ajoute
une colonne surface_km2 à l'index.

N'a besoin d'aucun recalcul coûteux (r5py, Overpass, BPE...) : le découpage
communal est déjà calculé et mis en cache pour chaque réseau du benchmark —
seule une agrégation géométrique (union des communes, reprojection en
Lambert-93 pour une surface en mètres) est faite ici.

Usage :
    .venv/bin/python scripts/ajouter_surface_benchmark.py [--dry-run]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.build_data_agglo import surface_km2_decoupage
from src.hf_cache import envoyer_vers_hf, lire_csv_partage, recuperer_depuis_hf

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
MEMORY_CSV_AGGLO_DIR = os.path.join(BASE_DIR, "data", "memory_csv_agglo")


def surface_km2_reseau(nom_reseau_str):
    """Surface (km²) de l'agglomération de nom_reseau_str (cf.
    src.build_data_agglo.surface_km2_decoupage), à partir du découpage
    communal récupéré depuis le cache local puis, à défaut, le dataset HF
    (même repli que le reste du pipeline, cf. src/hf_cache.py). None si le
    découpage est introuvable des deux côtés (réseau jamais traité par
    l'onglet Accessibilité)."""
    chemin_local = os.path.join(MEMORY_CSV_AGGLO_DIR, f"decoupage_agglo_{nom_reseau_str}.csv")
    if not os.path.exists(chemin_local):
        recuperer_depuis_hf(f"memory_csv_agglo/decoupage_agglo_{nom_reseau_str}.csv", chemin_local)
    if not os.path.exists(chemin_local):
        return None
    return surface_km2_decoupage(chemin_local)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Calcule et affiche les surfaces sans modifier/pousser l'index.")
    args = parser.parse_args()

    chemin_local_benchmark = os.path.join(OUTPUT_DIR, "index_benchmark_reseaux.csv")
    tableau = lire_csv_partage("benchmark/index_benchmark_reseaux.csv", chemin_local_benchmark)
    if tableau is None or tableau.empty:
        print("benchmark/index_benchmark_reseaux.csv est vide — rien à faire.")
        return

    reseaux = sorted(tableau["reseau"].unique())
    print(f"{len(reseaux)} réseau(x) dans l'index de benchmark\n")

    surfaces = {}
    echecs = []
    for reseau in reseaux:
        try:
            surface = surface_km2_reseau(reseau)
        except Exception as e:
            print(f"✗ {reseau} : {type(e).__name__}: {e}")
            echecs.append(reseau)
            continue
        if surface is None:
            print(f"⚠ {reseau} : découpage communal introuvable (local et HF)")
            echecs.append(reseau)
            continue
        surfaces[reseau] = surface
        print(f"✓ {reseau} : {surface:,.1f} km²".replace(",", " "))

    if args.dry_run:
        print(f"\n[dry-run] {len(surfaces)} réseau(x) calculé(s), index non modifié.")
        return

    tableau["surface_km2"] = tableau["reseau"].map(surfaces)
    if "population_totale" in tableau.columns:
        tableau.insert(
            tableau.columns.get_loc("population_totale") + 1,
            "surface_km2",
            tableau.pop("surface_km2"),
        )

    tableau.to_csv(chemin_local_benchmark, index=False)
    envoyer_vers_hf(chemin_local_benchmark, "benchmark/index_benchmark_reseaux.csv")

    print(
        f"\n{len(surfaces)}/{len(reseaux)} réseau(x) avec surface_km2 renseignée — "
        f"index mis à jour localement et poussé sur HF."
    )
    if echecs:
        print(f"Échecs ({len(echecs)}) : {echecs}")


if __name__ == "__main__":
    main()
