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

Exécuté aussi automatiquement chaque semaine par
.github/workflows/rafraichir-gtfs.yml (--exclude IDFM/Lyon TCL — trop
coûteux à retraiter en CI, cf. commentaire du workflow — --journal et
--json-resultat pour le journal git et le mail de notification).

Usage :
    .venv/bin/python scripts/rafraichir_gtfs.py [--dry-run] [--include FICHIER ...]
        [--exclude FICHIER ...] [--journal CHEMIN.csv] [--json-resultat CHEMIN.json]
"""

import argparse
import csv
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.hf_cache import envoyer_vers_hf
from src.info_reseau import nom_reseau_str as calculer_nom_reseau_str
from src.transport_data_gouv import (
    charger_provenance,
    enregistrer_provenance,
    nb_agences_gtfs,
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


def _ajouter_au_journal(chemin_journal, lignes):
    """Ajoute lignes (liste de dicts date/nom_fichier/reseau/statut/
    ancienne_maj/nouvelle_maj) à chemin_journal — un CSV commité sur git
    (cf. .github/workflows/rafraichir-gtfs.yml, pas synchronisé via HF comme
    le reste de data/) qui garde un historique des mises à jour détectées,
    au-delà de ce que gtfs_sources.json (juste l'état courant) retient.
    N'écrit l'en-tête que si le fichier n'existe pas encore."""
    if not lignes:
        return
    nouveau = not os.path.exists(chemin_journal)
    os.makedirs(os.path.dirname(chemin_journal), exist_ok=True)
    with open(chemin_journal, "a", newline="", encoding="utf-8") as f:
        colonnes = ["date", "nom_fichier", "reseau", "statut", "ancienne_maj", "nouvelle_maj"]
        writer = csv.DictWriter(f, fieldnames=colonnes)
        if nouveau:
            writer.writeheader()
        writer.writerows(lignes)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Liste ce qui serait fait, sans rien télécharger/écraser.")
    parser.add_argument(
        "--include", nargs="*", default=None,
        help="Ne vérifier que ces fichiers (basename dans data/GTFS/), même sous-ensemble de gtfs_sources.json.",
    )
    parser.add_argument(
        "--exclude", nargs="*", default=[],
        help="Vérifier la fraîcheur de ces fichiers sans jamais les télécharger/écraser automatiquement "
        "(une mise à jour détectée est journalisée/signalée comme 'à traiter manuellement') — pour les "
        "réseaux trop coûteux à retraiter automatiquement (IDFM, Lyon TCL : plusieurs heures de calcul r5py).",
    )
    parser.add_argument(
        "--journal", default=None,
        help="Chemin d'un CSV où ajouter une ligne par mise à jour détectée (date, nom_fichier, reseau, "
        "statut, ancienne_maj, nouvelle_maj) — historique cumulatif, pensé pour être commité sur git.",
    )
    parser.add_argument(
        "--json-resultat", default=None,
        help="Chemin où écrire un résumé JSON de ce run (comptes + listes par catégorie) — pensé pour "
        "être relu par un step de notification (mail) dans une CI, plutôt que de reparser la sortie texte.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Retélécharge/écrase même les GTFS déjà à jour d'après ressource_maj (cf. statut_resultat) — "
        "pour forcer une resynchronisation complète (ex: doute sur l'exactitude du cache local/HF) plutôt "
        "que de se fier à la comparaison de dates. cf. scripts/rafraichir_gtfs_force.py, qui appelle "
        "juste ce script avec ce drapeau.",
    )
    args = parser.parse_args()

    provenance = charger_provenance()
    # Un fichier peut être dans gtfs_sources.json seulement pour son
    # académie/zone (cf. src.vacances_scolaires, enregistrer_zone dans
    # app.py) sans jamais avoir été associé à un dataset transport.data.gouv.fr
    # (upload manuel, jamais passé par la recherche) — pas vérifiable ici.
    provenance_pan = {f: info for f, info in provenance.items() if info.get("page_url")}
    if not provenance_pan:
        print("Aucun GTFS associé à un dataset transport.data.gouv.fr dans data/gtfs_sources.json.")
        print("Utilise la recherche transport.data.gouv.fr en barre latérale de l'app pour en associer.")
        return

    a_verifier = sorted(provenance_pan) if args.include is None else [f for f in args.include if f in provenance_pan]
    print(f"{len(a_verifier)} GTFS à vérifier : {a_verifier}\n")

    print("Récupération du catalogue transport.data.gouv.fr...")
    datasets = recuperer_datasets_public_transit()

    resultats = {"a_jour": [], "mis_a_jour": [], "a_traiter_manuellement": [], "introuvable": [], "erreur": []}
    aujourdhui = datetime.date.today().isoformat()
    lignes_journal = []

    for nom_fichier in a_verifier:
        info = provenance_pan[nom_fichier]
        print(f"▶ {nom_fichier} ({info.get('titre')})")

        resultat_actuel = resultat_pour_page_url(info["page_url"], info.get("ressource_url"), datasets)
        if resultat_actuel is None:
            print("  ⚠ dataset introuvable sur transport.data.gouv.fr (page supprimée/déplacée ?) — à vérifier manuellement")
            resultats["introuvable"].append(nom_fichier)
            continue

        statut, _ = statut_resultat(resultat_actuel, provenance)
        if statut == "a_jour" and not args.force:
            print(f"  ✓ à jour (màj source : {info.get('ressource_maj')})")
            resultats["a_jour"].append(nom_fichier)
            continue

        if statut == "a_jour":
            print(f"  ⬇ à jour d'après ressource_maj ({info.get('ressource_maj')}) mais --force : retéléchargé quand même")
        else:
            print(f"  ⬇ mise à jour disponible : {info.get('ressource_maj')} -> {resultat_actuel['ressource_maj']}")

        if nom_fichier in args.exclude:
            print("    ⛔ exclu du rafraîchissement automatique — à télécharger/retraiter manuellement")
            resultats["a_traiter_manuellement"].append(nom_fichier)
            lignes_journal.append({
                "date": aujourdhui, "nom_fichier": nom_fichier, "reseau": "",
                "statut": "a_traiter_manuellement",
                "ancienne_maj": info.get("ressource_maj"), "nouvelle_maj": resultat_actuel["ressource_maj"],
            })
            continue

        if args.dry_run:
            print("    [dry-run] téléchargement/écrasement non effectué")
            resultats["mis_a_jour"].append(nom_fichier)
            continue

        try:
            contenu = telecharger_gtfs(resultat_actuel)

            # Même garde-fou que la recherche en barre latérale de l'app
            # (nb_agences_gtfs, cf. app.py) : un dataset PAN peut regrouper
            # plusieurs ressources par opérateur PLUS un "référentiel
            # complet" qui les fusionne toutes (ex: Aix-Marseille, 17
            # agences) — ici, sans validation humaine au moment du
            # téléchargement, mieux vaut refuser et signaler que d'écraser
            # un GTFS urbain valide par un fichier que l'app ne peut pas
            # charger.
            nb_agences = nb_agences_gtfs(contenu)
            if nb_agences > 4:
                print(f"    ⛔ {nb_agences} agences dans la ressource trouvée — rejeté, à vérifier manuellement via la recherche de l'app")
                resultats["a_traiter_manuellement"].append(nom_fichier)
                lignes_journal.append({
                    "date": aujourdhui, "nom_fichier": nom_fichier, "reseau": "",
                    "statut": f"rejete_{nb_agences}_agences",
                    "ancienne_maj": info.get("ressource_maj"), "nouvelle_maj": resultat_actuel["ressource_maj"],
                })
                continue

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
            lignes_journal.append({
                "date": aujourdhui, "nom_fichier": nom_fichier, "reseau": nom_reseau,
                "statut": "mis_a_jour",
                "ancienne_maj": info.get("ressource_maj"), "nouvelle_maj": resultat_actuel["ressource_maj"],
            })
        except Exception as e:
            print(f"    ✗ échec : {type(e).__name__}: {e}")
            resultats["erreur"].append(nom_fichier)
            continue

        resultats["mis_a_jour"].append(nom_fichier)

    print("\n" + "=" * 80)
    print("Résumé :")
    print(f"  à jour               : {len(resultats['a_jour'])}")
    print(f"  mis à jour           : {len(resultats['mis_a_jour'])} — {resultats['mis_a_jour']}")
    print(f"  à traiter manuellement : {len(resultats['a_traiter_manuellement'])} — {resultats['a_traiter_manuellement']}")
    print(f"  introuvables         : {len(resultats['introuvable'])} — {resultats['introuvable']}")
    print(f"  erreurs              : {len(resultats['erreur'])} — {resultats['erreur']}")
    if resultats["mis_a_jour"] and not args.dry_run:
        print(
            "\nRéseau(x) mis à jour : relance l'analyse dans l'onglet Accessibilité de l'app "
            "(ou scripts/run_benchmark_batch.py) pour recalculer leurs indicateurs avec le GTFS à jour."
        )

    if args.journal and not args.dry_run:
        _ajouter_au_journal(args.journal, lignes_journal)
        if lignes_journal:
            print(f"\n{len(lignes_journal)} ligne(s) ajoutée(s) au journal {args.journal}")

    if args.json_resultat:
        os.makedirs(os.path.dirname(args.json_resultat) or ".", exist_ok=True)
        with open(args.json_resultat, "w", encoding="utf-8") as f:
            json.dump(resultats, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
