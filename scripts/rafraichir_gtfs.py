"""
Vérifie chaque GTFS déjà associé à un jeu de données transport.data.gouv.fr
(cf. data/gtfs_sources.json, alimenté automatiquement par l'app à chaque
nouveau chargement, ou manuellement via la recherche en barre latérale —
src/transport_data_gouv.py) : télécharge, écrase localement et pousse sur le
dataset HF (antoinechevre/accessibility-data) ceux dont une mise à jour est
disponible.

Invalide aussi (supprime du dataset HF) les caches dérivés — découpage
communal, carroyage population, extrait OSM, matrice des temps de trajet —
d'un réseau mis à jour : sans ça, l'app continuerait à servir un résultat
d'accessibilité périmé (calculé sur l'ancien GTFS) malgré le nouveau
fichier. Le recalcul lui-même n'a PAS lieu ici (coûteux : r5py, Overpass) —
c'est au prochain passage dans l'app ou dans le notebook qu'il aura lieu,
avec le GTFS à jour.

Un GTFS jamais associé à un dataset (absent de gtfs_sources.json) n'est pas
vérifiable ici sans intervention humaine : plusieurs jeux de données
pourraient correspondre, ou aucun. Ouvrir l'app et utiliser la recherche en
barre latérale pour l'associer une première fois.

Usage :
    .venv/bin/python scripts/rafraichir_gtfs.py [--dry-run] [--include FICHIER ...]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.hf_cache import envoyer_vers_hf
from src.info_reseau import nom_reseau_str as calculer_nom_reseau_str
from src.transport_data_gouv import (
    charger_provenance,
    enregistrer_provenance,
    recuperer_datasets_public_transit,
    resultat_pour_page_url,
    statut_resultat,
    telecharger_gtfs,
)
from src.utils import charger_gtfs

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GTFS_DIR = os.path.join(BASE_DIR, "data", "GTFS")

# Doit rester synchronisé avec GTFS_NOM_RESEAU_FORCE dans app.py : réseaux
# dont le nom ne peut pas être dérivé automatiquement des agences du GTFS
# (IDFM et Aix-Marseille regroupent plus de 4 agences chacun).
NOMS_RESEAU_FORCES = {
    "IDFM-gtfs_metro-rer-bus-tram_paris-petite-couronne.zip": "IDFM",
    "IDFM-gtfs.zip": "IDFM",
    "Aix_Marseille_mamp_GTFS.zip": "Aix_Marseille",
}

# Caches dérivés à invalider sur le dataset HF pour un réseau mis à jour
# (mêmes chemins que src/pipeline_donnees.chemins_reseau et
# src/hf_cache.HF_DATA_REPO_ID) — %s remplacé par nom_reseau_str.
CHEMINS_CACHE_HF_A_INVALIDER = [
    "memory_csv_agglo/decoupage_agglo_%s.csv",
    "memory_gpkg/population_grid_agglo_%s.gpkg",
    "memory_pbf/agglo_osm_pbf_%s.osm.pbf",
    "memory_ttm/ttm_%s.parquet",
]


def _nom_reseau_pour_fichier(nom_fichier, chemin_gtfs):
    if nom_fichier in NOMS_RESEAU_FORCES:
        return NOMS_RESEAU_FORCES[nom_fichier]
    return str(calculer_nom_reseau_str(charger_gtfs(chemin_gtfs)))


def _invalider_caches_derives(nom_reseau, dry_run):
    from huggingface_hub import HfApi

    from src.hf_cache import HF_DATA_REPO_ID

    api = HfApi()
    for gabarit in CHEMINS_CACHE_HF_A_INVALIDER:
        chemin_hf = gabarit % nom_reseau
        if dry_run:
            print(f"    [dry-run] supprimerait {chemin_hf} du dataset HF")
            continue
        try:
            api.delete_file(
                path_in_repo=chemin_hf,
                repo_id=HF_DATA_REPO_ID,
                repo_type="dataset",
                token=os.environ.get("HF_TOKEN"),
            )
            print(f"    ✓ cache invalidé : {chemin_hf}")
        except Exception as e:
            # Le fichier n'existe simplement peut-être pas (réseau jamais
            # traité) — pas une erreur en soi, juste rien à invalider.
            print(f"    (rien à invalider pour {chemin_hf} : {type(e).__name__})")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Liste ce qui serait fait, sans rien télécharger/écraser.")
    parser.add_argument(
        "--include", nargs="*", default=None,
        help="Ne vérifier que ces fichiers (basename dans data/GTFS/), même sous-ensemble de gtfs_sources.json.",
    )
    args = parser.parse_args()

    provenance = charger_provenance()
    if not provenance:
        print("data/gtfs_sources.json est vide — aucun GTFS associé à vérifier.")
        print("Utilise la recherche transport.data.gouv.fr en barre latérale de l'app pour en associer.")
        return

    a_verifier = sorted(provenance) if args.include is None else [f for f in args.include if f in provenance]
    print(f"{len(a_verifier)} GTFS à vérifier : {a_verifier}\n")

    print("Récupération du catalogue transport.data.gouv.fr...")
    datasets = recuperer_datasets_public_transit()

    resultats = {"a_jour": [], "mis_a_jour": [], "introuvable": [], "erreur": []}

    for nom_fichier in a_verifier:
        info = provenance[nom_fichier]
        print(f"▶ {nom_fichier} ({info.get('titre')})")

        resultat_actuel = resultat_pour_page_url(info["page_url"], info.get("ressource_url"), datasets)
        if resultat_actuel is None:
            print("  ⚠ dataset introuvable sur transport.data.gouv.fr (page supprimée/déplacée ?) — à vérifier manuellement")
            resultats["introuvable"].append(nom_fichier)
            continue

        statut, _ = statut_resultat(resultat_actuel, provenance)
        if statut == "a_jour":
            print(f"  ✓ à jour (màj source : {info.get('ressource_maj')})")
            resultats["a_jour"].append(nom_fichier)
            continue

        print(f"  ⬇ mise à jour disponible : {info.get('ressource_maj')} -> {resultat_actuel['ressource_maj']}")
        if args.dry_run:
            print("    [dry-run] téléchargement/écrasement non effectué")
            resultats["mis_a_jour"].append(nom_fichier)
            continue

        try:
            contenu = telecharger_gtfs(resultat_actuel)
            chemin_local = os.path.join(GTFS_DIR, nom_fichier)
            os.makedirs(GTFS_DIR, exist_ok=True)
            with open(chemin_local, "wb") as f:
                f.write(contenu)
            envoyer_vers_hf(chemin_local, f"GTFS/{nom_fichier}")
            enregistrer_provenance(nom_fichier, resultat_actuel)
            print(f"    ✓ {nom_fichier} écrasé localement + poussé sur HF (GTFS/{nom_fichier})")

            nom_reseau = _nom_reseau_pour_fichier(nom_fichier, chemin_local)
            print(f"    Invalidation des caches dérivés pour '{nom_reseau}'...")
            _invalider_caches_derives(nom_reseau, args.dry_run)
        except Exception as e:
            print(f"    ✗ échec : {type(e).__name__}: {e}")
            resultats["erreur"].append(nom_fichier)
            continue

        resultats["mis_a_jour"].append(nom_fichier)

    print("\n" + "=" * 80)
    print("Résumé :")
    print(f"  à jour        : {len(resultats['a_jour'])}")
    print(f"  mis à jour    : {len(resultats['mis_a_jour'])} — {resultats['mis_a_jour']}")
    print(f"  introuvables  : {len(resultats['introuvable'])} — {resultats['introuvable']}")
    print(f"  erreurs       : {len(resultats['erreur'])} — {resultats['erreur']}")
    if resultats["mis_a_jour"] and not args.dry_run:
        print(
            "\nRéseau(x) mis à jour : relance l'analyse dans l'onglet Accessibilité de l'app "
            "(ou scripts/run_benchmark_batch.py) pour recalculer leurs indicateurs avec le GTFS à jour."
        )


if __name__ == "__main__":
    main()
