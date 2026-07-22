"""
Cache de secours sur Hugging Face pour les fichiers volumineux de data/
(carroyage INSEE, extraits OSM, matrices de temps de trajet r5py...) : ces
fichiers sont trop gros pour git (cf. .gitignore) et donc absents d'un
déploiement fraîchement cloné ou redémarré sans stockage persistant.

Avant de relancer un calcul ou téléchargement coûteux (Overpass, r5py,
INSEE...), le pipeline regarde d'abord si le résultat existe déjà dans le
dataset HF antoinechevre/accessibility-data, où l'ensemble de data/ a été
téléversé une première fois pour les réseaux déjà traités.
"""

import os
import shutil

HF_DATA_REPO_ID = "antoinechevre/accessibility-data"


def recuperer_depuis_hf(nom_fichier_hf, destination_locale):
    """Télécharge nom_fichier_hf (chemin relatif dans le dataset HF, ex.
    "memory_ttm/ttm_TAM.parquet") vers destination_locale s'il n'existe pas
    déjà en local. Retourne True si destination_locale est disponible après
    l'appel (déjà présent ou téléchargé avec succès), False sinon — auquel
    cas l'appelant doit se rabattre sur son calcul/téléchargement habituel.

    Le dataset étant privé, un token HF (variable d'environnement HF_TOKEN,
    droits lecture suffisants) doit être configuré dans les secrets du
    déploiement (cf. README, section Déploiement).
    """
    if os.path.exists(destination_locale):
        return True

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        return False

    try:
        chemin_telecharge = hf_hub_download(
            repo_id=HF_DATA_REPO_ID,
            repo_type="dataset",
            filename=nom_fichier_hf,
            token=os.environ.get("HF_TOKEN"),
        )
    except Exception:
        return False

    os.makedirs(os.path.dirname(destination_locale), exist_ok=True)
    shutil.copy(chemin_telecharge, destination_locale)
    return True


def lister_fichiers_hf(sous_dossier):
    """Liste les fichiers du dataset HF sous sous_dossier/ (ex: "GTFS"),
    noms de fichiers (basename, sans le préfixe de dossier) triés.

    Liste vide si le dataset est inaccessible (token absent, hors ligne,
    huggingface_hub non installé...) — l'appelant doit alors se rabattre sur
    sa source habituelle plutôt que planter."""
    try:
        from huggingface_hub import HfApi
    except ImportError:
        return []

    try:
        fichiers = HfApi().list_repo_files(
            repo_id=HF_DATA_REPO_ID,
            repo_type="dataset",
            token=os.environ.get("HF_TOKEN"),
        )
    except Exception:
        return []

    prefixe = f"{sous_dossier}/"
    return sorted(f[len(prefixe):] for f in fichiers if f.startswith(prefixe) and f != prefixe)
