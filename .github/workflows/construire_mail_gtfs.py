"""
Construit, à partir de resultat.json (écrit par scripts/rafraichir_gtfs.py
--json-resultat), le sujet et le corps du mail de notification envoyé par
.github/workflows/rafraichir-gtfs.yml — et décide si un mail est nécessaire
(a_signaler=false quand tout est déjà à jour, pour ne pas spammer chaque
semaine sans rien à dire).

Écrit trois clés dans $GITHUB_OUTPUT (a_signaler, sujet, corps) plutôt que
d'imbriquer la construction du texte dans le YAML — plus lisible, et
testable en local (cf. exécution directe ci-dessous).
"""

import json
import os

with open("resultat.json", encoding="utf-8") as f:
    resultat = json.load(f)

a_jour = resultat["a_jour"]
mis_a_jour = resultat["mis_a_jour"]
a_traiter = resultat["a_traiter_manuellement"]
introuvable = resultat["introuvable"]
erreur = resultat["erreur"]

a_signaler = bool(mis_a_jour or a_traiter or introuvable or erreur)

lignes = [
    f"Vérification GTFS hebdomadaire — {len(a_jour)} à jour, {len(mis_a_jour)} mis à jour "
    f"automatiquement, {len(a_traiter)} à traiter manuellement.",
    "",
]

if mis_a_jour:
    lignes.append(f"Mis à jour automatiquement ({len(mis_a_jour)}) :")
    lignes += [f"  - {f}" for f in mis_a_jour]
    lignes.append(
        "  -> relance l'analyse (onglet Accessibilité de l'app, ou scripts/run_benchmark_batch.py) "
        "pour recalculer leurs indicateurs avec le GTFS à jour."
    )
    lignes.append("")

if a_traiter:
    lignes.append(f"À traiter manuellement — IDFM / Lyon TCL ({len(a_traiter)}) :")
    lignes += [f"  - {f}" for f in a_traiter]
    lignes.append(
        "  -> mise à jour disponible mais pas appliquée automatiquement (retraitement r5py trop "
        "coûteux pour ces réseaux) : lance scripts/rafraichir_gtfs.py --include <fichier> en local, "
        "puis l'analyse."
    )
    lignes.append("")

if introuvable:
    lignes.append(f"Dataset introuvable sur transport.data.gouv.fr ({len(introuvable)}) :")
    lignes += [f"  - {f}" for f in introuvable]
    lignes.append("  -> page supprimée/déplacée sur le PAN — à réassocier via la recherche de l'app.")
    lignes.append("")

if erreur:
    lignes.append(f"Erreurs pendant la vérification ({len(erreur)}) :")
    lignes += [f"  - {f}" for f in erreur]
    lignes.append("  -> voir les logs du run GitHub Actions pour le détail.")
    lignes.append("")

lignes.append("Journal complet : data/journal_maj_gtfs.csv")

corps = "\n".join(lignes)
sujet = (
    f"[Accessibility_analysis] GTFS : {len(mis_a_jour)} maj, {len(a_traiter)} à traiter, "
    f"{len(introuvable) + len(erreur)} problème(s)"
)

chemin_sortie = os.environ.get("GITHUB_OUTPUT")
if chemin_sortie:
    with open(chemin_sortie, "a", encoding="utf-8") as f:
        f.write(f"a_signaler={'true' if a_signaler else 'false'}\n")
        f.write(f"sujet={sujet}\n")
        delimiteur = "EOF_CORPS_MAIL"
        f.write(f"corps<<{delimiteur}\n{corps}\n{delimiteur}\n")
else:
    # Exécution en local (hors CI) : affiche plutôt que d'écrire dans un
    # fichier $GITHUB_OUTPUT inexistant.
    print(f"a_signaler={a_signaler}\nsujet={sujet}\n\n{corps}")
