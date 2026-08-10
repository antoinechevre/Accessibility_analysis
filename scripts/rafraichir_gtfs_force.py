"""
Force le retéléchargement/écrasement de tous les GTFS de data/gtfs_sources.json
(index de provenance transport.data.gouv.fr) — même ceux déjà à jour d'après
ressource_maj — plutôt que de se fier à la comparaison de dates habituelle
(scripts/rafraichir_gtfs.py sans --force). Utile en cas de doute sur
l'exactitude du cache local/HF (ex: fichier modifié/corrompu localement, ou
ressource_maj de la source jugée peu fiable pour un dataset donné).

Pas de logique propre : appelle scripts/rafraichir_gtfs.py avec --force,
pour ne jamais dupliquer la routine de téléchargement/invalidation des
caches dérivés — cf. ce fichier pour le détail (options --include/--exclude/
--journal/--json-resultat, toutes transmises telles quelles).

Usage :
    .venv/bin/python scripts/rafraichir_gtfs_force.py [--dry-run] [--include FICHIER ...]
"""

import sys

from rafraichir_gtfs import main as _main

if __name__ == "__main__":
    sys.argv.insert(1, "--force")
    _main()
