"""
Page Benchmark Villes Françaises - nuage de points comparant tous les réseaux
déjà enregistrés dans l'index de benchmark (cf. onglet Accessibilité, bouton
"Enregistrer les indicateurs de ce run", et la cellule "#sauvegarde index" du
notebook), avec le réseau actuellement chargé (GTFS sélectionné dans la barre
latérale, s'il y en a un) surligné en rouge parmi les autres en bleu.
"""

import os

import streamlit as st

from src.hf_cache import lire_csv_partage
from src.nuage_points_benchmark import generer_html_str

BASE_DIR = os.getcwd()
OUTPUT_DIR = os.path.join(BASE_DIR, "output")


def benchmark_reseaux_page():
    st.header("Benchmark Villes Françaises")
    st.caption(
        "% moyen d'équipements pondérés accessibles à 30/45/60 min et temps moyen pour en "
        "atteindre 25/50/75%, par domaine et décile de niveau de vie — axes, domaine et "
        "décile paramétrables directement dans le graphique."
    )

    # Pas besoin d'avoir lancé l'analyse d'accessibilité pour voir ce graphique :
    # seul un GTFS chargé (barre latérale) détermine le réseau à surligner, s'il y
    # en a un — sinon tous les réseaux sont affichés en bleu (mode autonome, cf.
    # generer_html_str).
    reseau_actuel = st.session_state.get("nom_reseau_str")
    if reseau_actuel:
        st.info(f"Réseau actuellement chargé : **{reseau_actuel}** — surligné en rouge ci-dessous.")
    else:
        st.info("Aucun GTFS chargé actuellement : tous les réseaux sont affichés en bleu.")

    chemin_local_benchmark = os.path.join(OUTPUT_DIR, "index_benchmark_reseaux.csv")
    tableau_benchmark_complet = lire_csv_partage("benchmark/index_benchmark_reseaux.csv", chemin_local_benchmark)
    if tableau_benchmark_complet is None or tableau_benchmark_complet.empty:
        st.info(
            "Aucun réseau n'a encore été enregistré dans l'index de benchmark — charge un GTFS et "
            "lance l'analyse dans l'onglet Accessibilité (enregistrement automatique en fin de run), "
            "ou utilise la cellule \"#sauvegarde index\" du notebook."
        )
        return

    html_benchmark = generer_html_str(tableau_benchmark_complet, reseau_actuel=reseau_actuel)
    st.components.v1.html(html_benchmark, height=760, scrolling=False)
