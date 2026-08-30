"""
Relève les visites de l'app enregistrées depuis le dernier passage de ce
script (fichiers marqueurs sous visites/ sur le dataset HF
antoinechevre/accessibility-data, un par visite — cf.
src.hf_cache.enregistrer_visite, appelé par app.py à chaque nouvelle
session) : compte, horodatage de la première/dernière, écrit le résumé dans
--json-resultat, puis VIDE visites/ sur le dataset (les fichiers traités ici
n'ont plus lieu d'y rester — pas de fenêtre de temps à gérer, "tout ce qui
est dans visites/" = "nouveau depuis le dernier passage").

Exécuté chaque jour par .github/workflows/notifier_visites.yml, qui envoie
ensuite un mail de digest si au moins une visite a été enregistrée (via
scripts/construire_mail_visites.py) — jamais plus d'un mail par jour, même
si l'app a été visitée plusieurs fois.

Usage :
    env/bin/python scripts/verifier_visites.py [--dry-run] [--json-resultat CHEMIN.json]
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.hf_cache import HF_DATA_REPO_ID, lister_fichiers_hf


def _horodatage_depuis_nom(nom_fichier):
    """Parse le nom d'un marqueur de visite ("20260812T101533123456Z.marker")
    en datetime UTC. None si le format est inattendu (fichier étranger
    déposé à la main sous visites/, par ex.) — ignoré plutôt que de faire
    échouer tout le run pour un seul nom mal formé."""
    base = nom_fichier.rsplit(".", 1)[0]
    try:
        return datetime.datetime.strptime(base, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Compte/affiche sans vider visites/ sur HF.")
    parser.add_argument("--json-resultat", default=None, help="Chemin où écrire le résumé JSON de ce run.")
    args = parser.parse_args()

    fichiers = lister_fichiers_hf("visites")
    print(f"{len(fichiers)} marqueur(s) de visite trouvé(s) sous visites/ sur le dataset HF.")

    horodatages = sorted(h for h in (_horodatage_depuis_nom(f) for f in fichiers) if h is not None)

    resultat = {
        "nb_visites": len(fichiers),
        "premiere": horodatages[0].isoformat() if horodatages else None,
        "derniere": horodatages[-1].isoformat() if horodatages else None,
    }
    print(json.dumps(resultat, indent=2))

    if fichiers and not args.dry_run:
        from huggingface_hub import HfApi

        api = HfApi()
        for nom_fichier in fichiers:
            try:
                api.delete_file(
                    path_in_repo=f"visites/{nom_fichier}",
                    repo_id=HF_DATA_REPO_ID,
                    repo_type="dataset",
                    token=os.environ.get("HF_TOKEN"),
                )
            except Exception as e:
                print(f"  ⚠ échec suppression visites/{nom_fichier} : {type(e).__name__}: {e}")
        print(f"visites/ vidé ({len(fichiers)} marqueur(s)).")
    elif fichiers:
        print("[dry-run] visites/ non vidé.")

    if args.json_resultat:
        os.makedirs(os.path.dirname(args.json_resultat) or ".", exist_ok=True)
        with open(args.json_resultat, "w", encoding="utf-8") as f:
            json.dump(resultat, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
