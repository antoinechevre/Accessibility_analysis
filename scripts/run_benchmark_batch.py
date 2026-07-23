"""
Exécute index_accessibility_notebook_def.ipynb pour chaque GTFS de data/GTFS/
pas encore présent dans output/index_benchmark_reseaux.csv (sauf exclusions
explicites, cf. EXCLUS ci-dessous), pour constituer un benchmark multi-agglos
(cf. cellule "#sauvegarde index" du notebook, qui alimente ce CSV — partagé
avec l'app via le dataset HF, cf. src/hf_cache.fusionner_et_envoyer_csv).

Chaque GTFS est exécuté dans un noyau Jupyter frais et indépendant (pas de
fuite d'état entre réseaux, chaque run repart de zéro) : le notebook source
n'est jamais modifié sur disque, une copie en mémoire a sa cellule
"#chemin GTFS" patchée avec le fichier ciblé avant exécution. Le fichier GTFS
lui-même est copié dans un temp file avant chargement — charger_gtfs() peut
réécrire un zip en place si calendar_dates.txt est vide (cf.
src.utils._retirer_table_vide_du_zip), ce qui modifierait sinon silencieusement
les fichiers source de data/GTFS.

Usage :
    .venv/bin/python scripts/run_benchmark_batch.py [--dry-run] [--limit N]
                                                      [--include FICHIER ...]

Un run complet peut prendre plusieurs heures (r5py, Overpass...) pour les
GTFS pas encore mis en cache : à lancer en arrière-plan, ex.
    nohup .venv/bin/python scripts/run_benchmark_batch.py > output/batch_logs/run.log 2>&1 &
"""

import argparse
import copy
import os
import shutil
import sys
import tempfile
import time
import traceback

import gtfs_kit as gk  # noqa: F401  (déclenche l'import tôt, erreurs de dépendance visibles avant la boucle)
import nbformat
import pandas as pd
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.info_reseau import nom_reseau_str as _nom_reseau_str
from src.utils import charger_gtfs

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOTEBOOK_PATH = os.path.join(BASE_DIR, "index_accessibility_notebook_def.ipynb")
GTFS_DIR = os.path.join(BASE_DIR, "data", "GTFS")
BENCHMARK_CSV = os.path.join(BASE_DIR, "output", "index_benchmark_reseaux.csv")
LOG_DIR = os.path.join(BASE_DIR, "output", "batch_logs")

# Réseaux volontairement exclus du batch, même s'ils ne sont pas encore dans
# le benchmark (ex: Lyon, réseau très lourd à traiter en routine ; le fichier
# _r5py de Toulouse est une copie nettoyée du GTFS principal, pas un réseau
# distinct à benchmarker séparément).
EXCLUS = {"Lyon_GTFS_TCL.ZIP", "Toulouse_tisseo_gtfs_v2_r5py.zip"}

MARQUEUR_CELLULE_GTFS = "#chemin GTFS"


def reseaux_deja_benchmarkes():
    if not os.path.exists(BENCHMARK_CSV):
        return set()
    return set(pd.read_csv(BENCHMARK_CSV)["reseau"].unique())


def copier_vers_temp(chemin_gtfs):
    """Copie chemin_gtfs vers un fichier temporaire, à l'appelant de le
    supprimer (cf. nettoyage dans la boucle principale)."""
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        chemin_tmp = tmp.name
    shutil.copy(chemin_gtfs, chemin_tmp)
    return chemin_tmp


def patcher_cellule_gtfs_path(nb_original, chemin_gtfs_tmp):
    """Retourne une copie du notebook dont la cellule "#chemin GTFS" pointe
    vers chemin_gtfs_tmp (remplace la ligne GTFS_PATH=... par une valeur
    littérale, garde le reste de la cellule intact)."""
    nb = copy.deepcopy(nb_original)
    for cell in nb.cells:
        if cell.cell_type == "code" and cell.source.startswith(MARQUEUR_CELLULE_GTFS):
            lignes = cell.source.splitlines(keepends=True)
            lignes = [
                f"GTFS_PATH={chemin_gtfs_tmp!r}\n" if l.strip().startswith("GTFS_PATH=") else l
                for l in lignes
            ]
            cell.source = "".join(lignes)
            return nb
    raise RuntimeError(f"Cellule '{MARQUEUR_CELLULE_GTFS}' introuvable dans le notebook.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Liste ce qui serait exécuté, sans rien lancer.")
    parser.add_argument("--limit", type=int, default=None, help="Ne traiter que les N premiers fichiers retenus.")
    parser.add_argument(
        "--include", nargs="*", default=None,
        help="Ne traiter que ces fichiers (basename), même s'ils sont exclus/déjà benchmarkés.",
    )
    args = parser.parse_args()

    os.makedirs(LOG_DIR, exist_ok=True)

    fichiers_gtfs = sorted(f for f in os.listdir(GTFS_DIR) if f.lower().endswith(".zip"))
    deja_benchmarkes = reseaux_deja_benchmarkes()
    print(f"Réseaux déjà dans {BENCHMARK_CSV} : {sorted(deja_benchmarkes) or '(aucun)'}")

    a_traiter = []
    for nom_fichier in fichiers_gtfs:
        if args.include is not None:
            if nom_fichier in args.include:
                a_traiter.append(nom_fichier)
            continue
        if nom_fichier in EXCLUS:
            print(f"⏭  {nom_fichier} : exclu explicitement")
            continue

        chemin_gtfs = os.path.join(GTFS_DIR, nom_fichier)
        chemin_tmp = copier_vers_temp(chemin_gtfs)
        try:
            nom_reseau = _nom_reseau_str(charger_gtfs(chemin_tmp))
        except Exception as e:
            print(f"⚠️  {nom_fichier} : impossible de déterminer le réseau ({type(e).__name__}: {e}), traité quand même")
            a_traiter.append(nom_fichier)
            continue
        finally:
            os.unlink(chemin_tmp)

        if nom_reseau in deja_benchmarkes:
            print(f"⏭  {nom_fichier} : réseau '{nom_reseau}' déjà dans le benchmark")
            continue
        a_traiter.append(nom_fichier)

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
