"""
Associe chaque GTFS déjà présent dans data/GTFS/ mais absent de l'index de
provenance (data/gtfs_sources.json — page_url/ressource_url manquants : soit
jamais passé par la recherche en barre latérale de l'app, soit uploadé avant
l'existence de cette fonctionnalité) à son jeu de données
transport.data.gouv.fr, quand un candidat suffisamment fiable est trouvé
(cf. src.transport_data_gouv.associer_gtfs_a_pan pour le détail du
rapprochement — comparaison de agency.txt, jamais le seul nom de fichier,
potentiellement renommé depuis).

N'écrase JAMAIS le GTFS local (contrairement à rafraichir_gtfs.py) : associe
seulement page_url/ressource_url/ressource_maj/titre dans gtfs_sources.json,
pour que rafraichir_gtfs.py puisse ensuite en vérifier la fraîcheur.

Usage :
    env/bin/python scripts/indexer_gtfs_locaux.py [--dry-run] [--include FICHIER ...]
"""

import argparse
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.transport_data_gouv import (
    associer_gtfs_a_pan,
    charger_provenance,
    enregistrer_provenance,
    noms_agences_gtfs,
    recuperer_datasets_public_transit,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GTFS_DIR = os.path.join(BASE_DIR, "data", "GTFS")

# GTFS extraits manuellement d'une seule agence depuis un GTFS agrégé plus
# large (cf. src/extraire_gtfs_agence.py — ex: SQY.zip, extrait de
# IDFM-gtfs.zip) : jamais à associer à un dataset PAN, même si agency.txt
# partage une agence avec le GTFS source complet — ça les lierait au jeu de
# données AGRÉGÉ (SQY.zip -> IDFM), que rafraichir_gtfs.py écraserait alors
# par erreur avec le GTFS complet au lieu du sous-ensemble extrait. Protégé
# aussi de fait par le garde-fou "max 4 agences" d'associer_gtfs_a_pan (IDFM
# en a 64) mais gardé explicite ici pour ne pas en dépendre implicitement.
EXCLUS_ASSOCIATION_PAN = {"SQY.zip"}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Cherche et affiche les correspondances sans écrire dans gtfs_sources.json.")
    parser.add_argument("--include", nargs="*", default=None, help="Ne traiter que ces fichiers (basename dans data/GTFS/).")
    args = parser.parse_args()

    provenance = charger_provenance()
    fichiers_locaux = sorted(f for f in os.listdir(GTFS_DIR) if f.lower().endswith(".zip"))
    a_traiter = [
        f for f in fichiers_locaux
        if not provenance.get(f, {}).get("page_url") and f not in EXCLUS_ASSOCIATION_PAN
    ]
    if args.include is not None:
        a_traiter = [f for f in a_traiter if f in args.include]

    print(f"{len(a_traiter)} fichier(s) local(aux) sans lien PAN à traiter\n")
    print("Récupération du catalogue transport.data.gouv.fr...")
    datasets = recuperer_datasets_public_transit()

    lies, rejetes, sans_agence_locale, aucune_correspondance = [], [], [], []

    for nom_fichier in a_traiter:
        chemin_local = os.path.join(GTFS_DIR, nom_fichier)
        with open(chemin_local, "rb") as f:
            contenu_local = f.read()
        try:
            agences_locales = noms_agences_gtfs(contenu_local)
        except (KeyError, zipfile.BadZipFile) as e:
            print(f"▶ {nom_fichier} : ✗ agency.txt local illisible ({type(e).__name__})")
            sans_agence_locale.append(nom_fichier)
            continue

        print(f"▶ {nom_fichier} (agences locales : {', '.join(sorted(agences_locales)) or '?'})")
        resultat, motif_echec = associer_gtfs_a_pan(nom_fichier, contenu_local, datasets)

        if resultat is None:
            if motif_echec == "trop_agences":
                print("    ⛔ correspondance trouvée mais trop d'agences — rejetée (cf. TropAgencesError)")
                rejetes.append(nom_fichier)
            else:
                print("    ✗ aucun candidat PAN avec une agence en commun — à vérifier manuellement")
                aucune_correspondance.append(nom_fichier)
            continue

        print(f"    ✓ {resultat['title']} (màj {resultat['ressource_maj']})")
        lies.append(nom_fichier)
        if not args.dry_run:
            enregistrer_provenance(nom_fichier, resultat)

    print("\n" + "=" * 80)
    print("Résumé :")
    print(f"  liés{' (dry-run, pas écrit)' if args.dry_run else ''}          : {len(lies)} — {lies}")
    print(f"  rejetés (>4 agences)   : {len(rejetes)} — {rejetes}")
    print(f"  aucune correspondance  : {len(aucune_correspondance)} — {aucune_correspondance}")
    print(f"  agency.txt illisible   : {len(sans_agence_locale)} — {sans_agence_locale}")


if __name__ == "__main__":
    main()
