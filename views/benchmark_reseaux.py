"""
Page Benchmark Villes Françaises - deux nuages de points comparant tous les
réseaux déjà enregistrés dans l'index de benchmark (cf. onglet Accessibilité,
bouton "Enregistrer les indicateurs de ce run", et la cellule "#sauvegarde
index" du notebook), avec le réseau actuellement chargé (GTFS sélectionné
dans la barre latérale, s'il y en a un) surligné en rouge parmi les autres
en bleu :
- Accessibilité aux équipements (src/nuage_points_benchmark.py) : axes,
  domaine et décile paramétrables.
- Véhicules.km & arrêts (src/nuage_points_reseau.py) : population en
  abscisse, ordonnée paramétrable (bus/km, métro+tram/km, tout véh.km,
  nombre d'arrêts).
"""

import os

import streamlit as st

from src.hf_cache import lire_csv_partage
from src.nuage_points_benchmark import generer_html_str
from src.nuage_points_reseau import generer_html_str as generer_html_reseau_str

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

    # Filtre commun aux deux graphiques ci-dessous : les grandes agglomérations
    # (Paris, Marseille...) peuvent écraser visuellement les réseaux plus
    # modestes sur les deux nuages de points — même logique que
    # RESEAUX_EXCLUS_BENCHMARK (IDFM), mais ajustable par l'utilisateur plutôt
    # que figé dans le code.
    SEUIL_GRANDES_VILLES = 500_000
    exclure_grandes_villes = st.checkbox(
        f"Exclure les villes de plus de {SEUIL_GRANDES_VILLES:,} habitants".replace(",", " "),
        value=False,
        help="S'applique aux deux graphiques ci-dessous.",
    )
    tableau_filtre = tableau_benchmark_complet
    if exclure_grandes_villes and "population_totale" in tableau_benchmark_complet.columns:
        tableau_filtre = tableau_benchmark_complet[tableau_benchmark_complet["population_totale"] <= SEUIL_GRANDES_VILLES]
        if tableau_filtre.empty:
            st.info(f"Aucun réseau de l'index n'a une population ≤ {SEUIL_GRANDES_VILLES:,} habitants.".replace(",", " "))
            return

    html_benchmark = generer_html_str(tableau_filtre, reseau_actuel=reseau_actuel)
    st.components.v1.html(html_benchmark, height=760, scrolling=False)

    st.markdown("---")
    st.markdown("### Véhicules.km & arrêts")
    st.caption(
        "Population totale en abscisse, un point par réseau — ordonnée paramétrable "
        "(bus/km, métro+tram/km, tout véhicules.km, nombre d'arrêts, ou ces quatre "
        "indicateurs rapportés à 1000 habitants) directement dans le graphique."
    )
    colonnes_reseau = {"bus_km_JOB", "metro_km_JOB", "tram_km_JOB", "vehicules_km_JOB", "nombre_arrets"}
    if not colonnes_reseau & set(tableau_filtre.columns):
        st.info(
            "Aucun réseau de l'index n'a encore ces indicateurs (bus/km, métro+tram/km, "
            "nombre d'arrêts) — relance l'analyse dans l'onglet Accessibilité pour les "
            "calculer et les enregistrer."
        )
    else:
        html_reseau = generer_html_reseau_str(tableau_filtre, reseau_actuel=reseau_actuel)
        st.components.v1.html(html_reseau, height=760, scrolling=False)
