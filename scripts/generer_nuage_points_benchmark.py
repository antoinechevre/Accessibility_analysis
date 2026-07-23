"""
Génère un nuage de points HTML interactif (autonome, sans serveur) à partir
de output/index_benchmark_reseaux.csv — logique partagée avec l'onglet
Accessibilité de l'app, cf. src/nuage_points_benchmark.py pour le détail
des axes/filtres disponibles.

Usage :
    .venv/bin/python scripts/generer_nuage_points_benchmark.py
    .venv/bin/python scripts/generer_nuage_points_benchmark.py --fichier chemin.csv --sortie chemin.html
    .venv/bin/python scripts/generer_nuage_points_benchmark.py --reseau-actuel TCL
"""

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.nuage_points_benchmark import generer_html_str

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PAR_DEFAUT = os.path.join(BASE_DIR, "output", "index_benchmark_reseaux.csv")
SORTIE_PAR_DEFAUT = os.path.join(BASE_DIR, "output", "nuage_points_benchmark.html")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fichier", default=CSV_PAR_DEFAUT, help="CSV source (défaut : output/index_benchmark_reseaux.csv)")
    parser.add_argument("--sortie", default=SORTIE_PAR_DEFAUT, help="Fichier HTML à générer")
    parser.add_argument(
        "--reseau-actuel", default=None,
        help="Surligne ce réseau (valeur de la colonne 'reseau') en rouge parmi les autres en bleu.",
    )
    args = parser.parse_args()

    if not os.path.exists(args.fichier):
        raise SystemExit(
            f"Fichier introuvable : {args.fichier} — lancer d'abord la cellule \"#sauvegarde index\" du "
            f"notebook, ou le bouton \"Enregistrer les indicateurs de ce run\" de l'onglet Accessibilité."
        )

    df = pd.read_csv(args.fichier)
    html = generer_html_str(df, reseau_actuel=args.reseau_actuel)

    os.makedirs(os.path.dirname(args.sortie), exist_ok=True)
    with open(args.sortie, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✓ {len(df)} ligne(s), {df['reseau'].nunique()} réseau(x) -> {args.sortie}")


if __name__ == "__main__":
    main()
