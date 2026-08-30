"""
Exécute index_accessibility_notebook_def.ipynb pour TOUS les GTFS de
data/GTFS/, sauf IDFM et Lyon/TCL (EXCLUS ci-dessous) — sans tenir compte de
leur statut dans le benchmark (contrairement à scripts/run_benchmark_batch.py,
qui saute un réseau déjà présent avec une date_JOB à jour). Pensé pour forcer
une régénération complète (ex: rafraîchir le cache HTML des cartes après un
changement affectant leur rendu — cf. commit "Cache HF pour les cartes HTML
de l'appli" — plutôt que pour ajouter seulement les réseaux manquants).

Même mécanique d'exécution que run_benchmark_batch.py (réutilisée par
import) : notebook exécuté dans un noyau Jupyter frais par réseau, jamais
modifié sur disque, GTFS copié dans un temp file avant chargement.

Usage :
    env/bin/python scripts/run_GTFS_complet.py [--dry-run] [--limit N]

Un run complet peut prendre plusieurs heures (r5py, Overpass...) pour les
~65 réseaux concernés : à lancer en arrière-plan, ex.
    nohup env/bin/python scripts/run_GTFS_complet.py > output/batch_logs/run_complet.log 2>&1 &
"""

import argparse
import os
import sys
import time
import traceback

import nbformat
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.run_benchmark_batch import (
    GTFS_DIR,
    LOG_DIR,
    MARQUEUR_CELLULE_GTFS,
    NOTEBOOK_PATH,
    copier_vers_temp,
    patcher_cellule_gtfs_path,
)

# Seuls réseaux exclus : IDFM et Lyon/TCL, lancés à part (notebooks dédiés,
# résolutions spéciales — cf. commentaire EXCLUS dans run_benchmark_batch.py
# pour le détail des raisons). Volontairement PAS les mêmes exclusions que
# run_benchmark_batch.py (51_REGION_GRANDEST, réseaux hors métropole...) :
# ce script traite tout ce que le pipeline standard sait gérer.
EXCLUS = {"IDFM-gtfs.zip", "Lyon_GTFS_TCL.zip"}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Liste ce qui serait exécuté, sans rien lancer.")
    parser.add_argument("--limit", type=int, default=None, help="Ne traiter que les N premiers fichiers retenus.")
    args = parser.parse_args()

    os.makedirs(LOG_DIR, exist_ok=True)

    fichiers_gtfs = sorted(f for f in os.listdir(GTFS_DIR) if f.lower().endswith(".zip"))
    a_traiter = [f for f in fichiers_gtfs if f not in EXCLUS]
    for nom_fichier in fichiers_gtfs:
        if nom_fichier in EXCLUS:
            print(f"⏭  {nom_fichier} : exclu explicitement")

    if args.limit is not None:
        a_traiter = a_traiter[: args.limit]

    print(f"\n{len(a_traiter)} fichier(s) à traiter : {a_traiter}\n")

    if args.dry_run:
        return

    nb_original = nbformat.read(NOTEBOOK_PATH, as_version=4)

    resultats = []
    for nom_fichier in a_traiter:
        chemin_gtfs = os.path.join(GTFS_DIR, nom_fichier)
        chemin_tmp = copier_vers_temp(chemin_gtfs)

        print(f"\n{'=' * 80}\n▶ {nom_fichier}\n{'=' * 80}")
        debut = time.time()
        statut, erreur = "OK", None
        nb = patcher_cellule_gtfs_path(nb_original, chemin_tmp)
        client = NotebookClient(nb, timeout=3600, kernel_name="python3")
        try:
            client.execute()
        except CellExecutionError as e:
            statut, erreur = "ERREUR", f"{e.ename}: {e.evalue}"
        except Exception as e:
            statut, erreur = "ERREUR", f"{type(e).__name__}: {e}"
            traceback.print_exc()
        finally:
            os.unlink(chemin_tmp)

        duree_min = (time.time() - debut) / 60
        chemin_log = os.path.join(LOG_DIR, f"{os.path.splitext(nom_fichier)[0]}.ipynb")
        nbformat.write(nb, chemin_log)

        if statut == "OK":
            print(f"✓ {nom_fichier} terminé en {duree_min:.1f} min — log : {chemin_log}")
        else:
            print(f"✗ {nom_fichier} échoué après {duree_min:.1f} min : {erreur} — log : {chemin_log}")

        resultats.append({"fichier": nom_fichier, "statut": statut, "duree_min": round(duree_min, 1), "erreur": erreur})

    print("\n" + "=" * 80)
    print("Résumé :")
    for r in resultats:
        ligne = f"  {r['statut']:7s} {r['fichier']:55s} {r['duree_min']:>6.1f} min"
        if r["erreur"]:
            ligne += f"  — {r['erreur']}"
        print(ligne)


if __name__ == "__main__":
    main()
