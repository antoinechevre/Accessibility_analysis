"""
Déploie l'état actuel du dépôt (HEAD) vers le Space Hugging Face
antoinechevre/accessibility, via l'API HTTP de huggingface_hub plutôt qu'un
push git — cf. le commentaire en tête de sync-to-hf-space.yml pour pourquoi
(protocole git peu fiable contre le serveur des Spaces HF, dans les deux
versions v0/v2).

Nécessite HF_TOKEN dans l'environnement (droit d'écriture sur le Space) et
d'être exécuté depuis la racine du dépôt (working-directory du job GitHub
Actions qui l'appelle).
"""

import os
import subprocess

from huggingface_hub import HfApi

REPO_ID = "antoinechevre/accessibility"

# Notebooks de prototypage (cf. docstring des vues sous views/) : pas
# nécessaires à l'exécution de l'app Streamlit, et leurs sorties de cellules
# (cartes intégrées) sont volumineuses sans intérêt pour le Space.
IGNORE_PATTERNS = [
    ".git",
    ".git/**",
    "**/.git/**",
    ".github/**",
    "index_accessibility_notebook_def.ipynb",
    "index_accessibility_notebook_IDF_1km.ipynb",
    "index_accessibility_notebook_IDF_400x400.ipynb",
]


def _git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def main():
    sha = _git("rev-parse", "--short", "HEAD")
    subject = _git("log", "-1", "--format=%s", "HEAD")

    HfApi(token=os.environ["HF_TOKEN"]).upload_folder(
        folder_path=".",
        repo_id=REPO_ID,
        repo_type="space",
        ignore_patterns=IGNORE_PATTERNS,
        commit_message=f"Deploy snapshot of {sha} — {subject}",
    )


if __name__ == "__main__":
    main()
