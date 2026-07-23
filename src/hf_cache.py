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


def envoyer_vers_hf(chemin_local, nom_fichier_hf):
    """Envoie chemin_local vers le dataset HF sous nom_fichier_hf (chemin
    relatif, ex: "memory_ttm/ttm_TAM.parquet") — pendant du fallback
    recuperer_depuis_hf() : après un calcul neuf (réseau jamais traité), le
    résultat est renvoyé vers le dataset pour que les prochains
    déploiements/redémarrages en profitent aussi, plutôt que de rester
    disponible seulement en local sur ce Space (perdu au redémarrage suivant
    sans stockage persistant).

    Best-effort, comme recuperer_depuis_hf : échec silencieux (retourne
    False) si HF_TOKEN absent/sans droit d'écriture, dataset inaccessible,
    etc. Ne doit jamais faire échouer le calcul lui-même, seulement son
    enregistrement à distance — appelé après coup, jamais dans le chemin
    critique.
    """
    try:
        from huggingface_hub import HfApi
    except ImportError:
        return False

    try:
        HfApi().upload_file(
            path_or_fileobj=chemin_local,
            path_in_repo=nom_fichier_hf,
            repo_id=HF_DATA_REPO_ID,
            repo_type="dataset",
            token=os.environ.get("HF_TOKEN"),
        )
    except Exception:
        return False
    return True


def _telecharger_dernier_csv(nom_fichier_hf, chemin_local):
    """Lit un CSV partagé entre plusieurs machines/déploiements via le
    dataset HF (ex: index de benchmark inter-réseaux) : contrairement à
    recuperer_depuis_hf (qui garde la copie locale si déjà présente), on
    retélécharge ici TOUJOURS la version la plus récente — ce fichier est
    modifié depuis plusieurs sources, la copie locale peut être en retard
    sur des lignes ajoutées ailleurs. Retombe sur la copie locale si HF est
    inaccessible, puis sur None si aucune des deux n'existe.
    """
    import pandas as pd

    try:
        from huggingface_hub import hf_hub_download

        chemin_distant = hf_hub_download(
            repo_id=HF_DATA_REPO_ID,
            repo_type="dataset",
            filename=nom_fichier_hf,
            token=os.environ.get("HF_TOKEN"),
            force_download=True,
        )
        return pd.read_csv(chemin_distant)
    except Exception:
        pass

    if os.path.exists(chemin_local):
        return pd.read_csv(chemin_local)
    return None


def lire_csv_partage(nom_fichier_hf, chemin_local):
    """Version lecture seule de _telecharger_dernier_csv, pour un affichage
    (ex: nuage de points benchmark) sans vouloir y fusionner de nouvelles
    lignes. Retourne None si introuvable sur HF et en local."""
    return _telecharger_dernier_csv(nom_fichier_hf, chemin_local)


def fusionner_et_envoyer_csv(nouvelles_lignes, nom_fichier_hf, chemin_local, colonne_cle, valeur_cle):
    """Fusionne nouvelles_lignes (DataFrame) dans un CSV partagé entre
    plusieurs machines/déploiements via le dataset HF (ex: index de
    benchmark inter-réseaux, alimenté aussi bien depuis le notebook en
    local que depuis l'app sur un Space) — cf. _telecharger_dernier_csv.

    Les lignes existantes où colonne_cle == valeur_cle sont retirées avant
    d'ajouter nouvelles_lignes (une relance remplace plutôt que duplique).
    Sauvegarde en local puis renvoie vers HF (best-effort, cf.
    envoyer_vers_hf — un échec d'envoi n'empêche pas la sauvegarde locale).

    Retourne le DataFrame fusionné (celui effectivement écrit en local).
    """
    import pandas as pd

    index_existant = _telecharger_dernier_csv(nom_fichier_hf, chemin_local)
    if index_existant is not None:
        index_existant = index_existant[index_existant[colonne_cle] != valeur_cle]
        tableau_final = pd.concat([index_existant, nouvelles_lignes], ignore_index=True)
    else:
        tableau_final = nouvelles_lignes

    os.makedirs(os.path.dirname(chemin_local), exist_ok=True)
    tableau_final.to_csv(chemin_local, index=False)
    envoyer_vers_hf(chemin_local, nom_fichier_hf)
    return tableau_final


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
