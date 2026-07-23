"""
Page Pondération équipements - reprend les cellules "analyse BPE 1.1/1.2" du
notebook index_accessibility_notebook_def.ipynb : carroyage INSEE, BPE
filtrée/pondérée par gamme et domaine, cartes HTML par domaine (offre brute,
pas l'accessibilité en temps de trajet - cf. views/accessibilite_index.py
pour ça).

Ne nécessite ni r5py ni osmium : seulement le découpage communal, le
carroyage population et la BPE.
"""

import os

import pandas as pd
import streamlit as st

from src.BPE_traitement import carte_ponderation_domaine
from src.pipeline_donnees import DOMAINES_BPE, construire_donnees_bpe
from src.ponderation_bpe import GAMMES_POIDS_PAR_DOMAINE, SEUILS_DOMAINE

BASE_DIR = os.getcwd()
OUTPUT_DIR = os.path.join(BASE_DIR, "output")


def ponderation_equipements_page():
    st.header("⚖️ Pondération des équipements par domaine")

    if st.session_state.get("feed") is None:
        st.info("👆 Veuillez charger un fichier GTFS dans la barre latérale.")
        return

    nom_reseau_str = st.session_state.nom_reseau_str
    zip_path = st.session_state.zip_path

    st.write(f"Réseau : **{nom_reseau_str}**")

    lancer = st.button("🚀 Lancer / recharger le calcul de pondération", use_container_width=True)

    if "reseau_pondere" not in st.session_state:
        st.session_state.reseau_pondere = None

    if lancer:
        statut = st.empty()
        try:
            population_grid_agglo, land_use_data, BPE_agglo = construire_donnees_bpe(
                zip_path, nom_reseau_str, on_step=lambda message: statut.info(message)
            )
        except Exception as e:
            st.error(f"Erreur pendant le calcul : {type(e).__name__}: {e}")
            return
        statut.empty()

        st.session_state.reseau_pondere = nom_reseau_str
        st.session_state.ponderation_data = (population_grid_agglo, land_use_data, BPE_agglo)

    if "ponderation_data" not in st.session_state or st.session_state.reseau_pondere != nom_reseau_str:
        st.info("Cliquez sur le bouton ci-dessus pour lancer le calcul.")
        return

    population_grid_agglo, land_use_data, BPE_agglo = st.session_state.ponderation_data

    st.success(f"✓ {len(population_grid_agglo)} carreaux actifs (population ou équipements).")

    st.markdown("### Cartes de pondération par domaine d'équipement")
    st.caption(
        "Pondération cumulée par gamme des équipements (proximité / intermédiaire / "
        "supérieure / hors gamme), par carreau — pas l'accessibilité en temps de "
        "trajet, juste la donnée d'offre brute."
    )

    onglets = st.tabs([f"{d} - {nom}" for d, nom in DOMAINES_BPE.items()])
    for onglet, domaine in zip(onglets, DOMAINES_BPE):
        with onglet:
            with st.spinner(f"Calcul de la carte {domaine}..."):
                carte = carte_ponderation_domaine(
                    DOMAINES_BPE, population_grid_agglo, BPE_agglo, land_use_data, domaine
                )
            st.components.v1.html(carte.get_root().render(), height=520, scrolling=False)

            html_path = os.path.join(OUTPUT_DIR, f"ponderation_{domaine}_{nom_reseau_str}.html")
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            carte.save(html_path)
            with open(html_path, "rb") as f:
                st.download_button(
                    f"💾 Télécharger la carte {domaine} (HTML)",
                    data=f,
                    file_name=os.path.basename(html_path),
                    mime="text/html",
                    key=f"download_ponderation_{domaine}",
                )

    st.markdown("### Hypothèses de pondération")
    st.caption(
        "Paramètres utilisés pour scorer la BPE par domaine (src/ponderation_bpe.py), "
        "communs à toutes les cartes ci-dessus."
    )

    st.markdown("#### Poids par gamme d'équipement, par domaine")
    st.caption(
        "Chaque équipement de la BPE est pondéré selon sa gamme (proximité / intermédiaire / "
        "supérieure / hors gamme), avec une grille de poids propre à chaque domaine — une même "
        "gamme peut compter différemment selon le domaine."
    )
    tableau_poids = pd.DataFrame(GAMMES_POIDS_PAR_DOMAINE).T
    tableau_poids.index = [f"{d} - {DOMAINES_BPE.get(d, d)}" for d in tableau_poids.index]
    tableau_poids.index.name = "Domaine"
    st.dataframe(tableau_poids, use_container_width=True)

    st.markdown("#### Seuil de détection des pôles d'équipements")
    st.caption(
        "Un carreau est considéré comme un pôle d'équipements majeur pour un domaine si sa "
        "pondération cumulée dépasse ce multiple de la moyenne des carreaux de ce domaine "
        "(utilisé par les cartes \"pôles\" de l'onglet Accessibilité)."
    )
    tableau_seuils = pd.DataFrame(
        [
            {"Domaine": f"{d} - {DOMAINES_BPE.get(d, d)}", "Seuil (x moyenne du domaine)": seuil}
            for d, seuil in SEUILS_DOMAINE.items()
        ]
    ).set_index("Domaine")
    st.dataframe(tableau_seuils, use_container_width=True)
