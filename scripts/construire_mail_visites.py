"""
Construit, à partir de resultat.json (écrit par scripts/verifier_visites.py
--json-resultat), le sujet et le corps du mail de notification envoyé par
.github/workflows/notifier_visites.yml — et décide si un mail est
nécessaire (a_signaler=false quand nb_visites=0, pour ne jamais envoyer de
mail les jours sans visite).

Écrit trois clés dans $GITHUB_OUTPUT (a_signaler, sujet, corps), même
principe que .github/workflows/construire_mail_gtfs.py.
"""

import json
import os

with open("resultat.json", encoding="utf-8") as f:
    resultat = json.load(f)

nb_visites = resultat["nb_visites"]
a_signaler = nb_visites > 0

if a_signaler:
    corps = (
        f"{nb_visites} visite(s) de l'app enregistrée(s) depuis le dernier digest.\n\n"
        f"Première : {resultat['premiere']}\n"
        f"Dernière  : {resultat['derniere']}\n\n"
        "(Une visite = une nouvelle session Streamlit ouverte sur le Space — "
        "un même visiteur qui recharge la page ou l'app qui se réveille après "
        "mise en veille comptent chacun comme une visite.)"
    )
else:
    corps = ""

sujet = f"[Accessibility_analysis] {nb_visites} visite(s) de l'app" if a_signaler else ""

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
