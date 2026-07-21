"""
Page Accessibilité - reprend le pipeline du notebook
index_accessibility_notebook_def.ipynb (GTFS -> réseau piéton/transport
collectif -> carroyage INSEE -> BPE pondérée -> matrice des temps de trajet
-> indicateur d'accessibilité à 30 min) pour l'exposer dans l'app Streamlit,
avec les cartes HTML interactives par domaine BPE.
"""

import os

# Chemin JAVA_HOME du poste de développement (macOS/Temurin 21). Ne s'applique
# que s'il existe réellement : sur un déploiement Linux (ex. Streamlit
# Community Cloud, cf. packages.txt), ce chemin n'existe pas et on laisse
# jpype détecter automatiquement le JDK installé par apt (JAVA_HOME n'a pas
# besoin d'être positionné pour ça).
_JAVA_HOME_MACOS = "/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home"
if "JAVA_HOME" not in os.environ and os.path.isdir(_JAVA_HOME_MACOS):
    os.environ["JAVA_HOME"] = _JAVA_HOME_MACOS

# Même ordre d'import qu'en cellule 1 du notebook : importer rasterio AVANT
# r5py initialise le contexte PROJ/GDAL avec le proj.db du venv, avant que le
# démarrage de la JVM par r5py ne pollue PROJ_LIB avec un chemin invalide.
import rasterio  # noqa: F401

import r5py
import r5py.util.jvm

r5py.util.jvm.MAX_JVM_MEMORY = 2 * 1024**3  # 2 Go, cf. notebook cellule 1

import datetime

import folium
import pandas as pd
import streamlit as st

from src.BPE_traitement import land_use_data_domaine
from src.build_data_agglo import osm_pbf_creator
from src.pipeline_donnees import DOMAINES_BPE, chemins_reseau, construire_donnees_bpe
from src.utilitaires_matrix import cumulative_cutoff
from src.utils import preparer_gtfs_pour_r5py

BASE_DIR = os.getcwd()
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

CUTOFF_MINUTES = 30

FONDS_CARTE = {
    "OpenStreetMap": "OpenStreetMap",
    "CartoDB Positron": "CartoDB positron",
    "CartoDB Dark Matter": "CartoDB dark_matter",
}


@st.cache_resource(show_spinner=False)
def _construire_reseau_transport(osm_pbf_path, gtfs_r5py_path):
    """Construit le TransportNetwork r5py (objet Java non sérialisable : mis
    en cache via st.cache_resource plutôt que st.session_state)."""
    return r5py.TransportNetwork(osm_pbf=osm_pbf_path, gtfs=[gtfs_r5py_path])


def _construire_pipeline(zip_path, nom_reseau_str, date_JOB):
    """Reconstruit (ou recharge depuis le cache disque) toutes les données
    nécessaires : découpage communal, carroyage population, BPE pondérée
    (via src.pipeline_donnees, partagé avec views/ponderation_equipements.py),
    puis extrait OSM, réseau de transport et matrice des temps de trajet.
    """
    statut = st.empty()
    population_grid_agglo, land_use_data, BPE_agglo = construire_donnees_bpe(
        zip_path, nom_reseau_str, on_step=lambda message: statut.info(message)
    )
    statut.empty()

    chemins = chemins_reseau(nom_reseau_str)
    osm_pbf_path = chemins["osm_pbf"]
    ttm_path = chemins["ttm"]

    if not os.path.exists(osm_pbf_path):
        with st.spinner("Extraction des données OSM (Overpass)... peut prendre plusieurs minutes"):
            osm_pbf_creator(chemins["decoupage_geojson"], output_pbf_path=osm_pbf_path)

    if not os.path.exists(ttm_path):
        with st.spinner(
            "Calcul de la matrice des temps de trajet (r5py)... "
            "premier lancement pour ce réseau, peut prendre plusieurs minutes"
        ):
            points = population_grid_agglo[["id", "geometry"]].copy()
            points["geometry"] = points.geometry.centroid

            gtfs_r5py = preparer_gtfs_pour_r5py(zip_path)
            transport_network = _construire_reseau_transport(osm_pbf_path, gtfs_r5py)

            departure_datetime = datetime.datetime.strptime(date_JOB, "%Y%m%d").replace(hour=14, minute=0, second=0)

            ttm = r5py.TravelTimeMatrix(
                transport_network,
                origins=points,
                destinations=points,
                transport_modes=[r5py.TransportMode.WALK, r5py.TransportMode.TRANSIT],
                departure=departure_datetime,
                max_time_walking=datetime.timedelta(minutes=30),
                max_time=datetime.timedelta(minutes=120),
            )
            ttm.to_parquet(ttm_path, index=False)

    ttm = pd.read_parquet(ttm_path)

    return population_grid_agglo, land_use_data, BPE_agglo, ttm


def _carte_accessibilite_domaine(population_grid_agglo, land_use_data, BPE_agglo, ttm, domaine, fond_carte):
    """Carte HTML interactive de l'accessibilité (opportunités cumulées à
    CUTOFF_MINUTES min) pour un domaine BPE donné. Équivalent de la cellule
    3.3.1 du notebook."""
    nom_domaine = DOMAINES_BPE.get(domaine, domaine)

    cum_cutoff = cumulative_cutoff(
        ttm,
        land_use_data=land_use_data_domaine(BPE_agglo, land_use_data, domaine),
        opportunity=domaine,
        travel_cost="travel_time",
        cutoff=CUTOFF_MINUTES,
    )

    spatial_access = population_grid_agglo[["id", "geometry"]].merge(cum_cutoff, on="id")

    carte = spatial_access.explore(
        column=domaine,
        cmap="inferno",
        tiles=FONDS_CARTE[fond_carte],
        legend=True,
        legend_kwds={"caption": f"{nom_domaine} (pondéré)"},
        style_kwds={"weight": 0, "opacity": 0},
    )

    titre_html = (
        f'<h3 align="center" style="font-size:16px">'
        f"<b>Accessibilité {nom_domaine} à {CUTOFF_MINUTES} min</b></h3>"
    )
    carte.get_root().html.add_child(folium.Element(titre_html))

    return carte


def arrets_page():
    st.header("♿ Accessibilité aux équipements (30 min)")

    if st.session_state.get("feed") is None:
        st.info("👆 Veuillez charger un fichier GTFS dans la barre latérale.")
        return

    nom_reseau_str = st.session_state.nom_reseau_str
    date_str = st.session_state.date_str
    zip_path = st.session_state.zip_path

    st.write(f"Réseau : **{nom_reseau_str}** — jour de référence : {date_str}")

    st.warning(
        "⚠️ Premier lancement pour ce réseau : extraction OSM puis calcul de la "
        "matrice des temps de trajet (r5py), potentiellement long (plusieurs "
        "minutes) et gourmand en mémoire. Les résultats sont mis en cache sur "
        "disque pour les lancements suivants."
    )

    lancer = st.button("🚀 Lancer / recharger l'analyse d'accessibilité", use_container_width=True)

    if "reseau_calcule" not in st.session_state:
        st.session_state.reseau_calcule = None

    if lancer:
        try:
            population_grid_agglo, land_use_data, BPE_agglo, ttm = _construire_pipeline(
                zip_path, nom_reseau_str, date_str
            )
        except Exception as e:
            st.error(f"Erreur pendant le calcul : {e}")
            return

        st.session_state.reseau_calcule = nom_reseau_str
        st.session_state.pipeline_data = (population_grid_agglo, land_use_data, BPE_agglo, ttm)

    if "pipeline_data" not in st.session_state or st.session_state.reseau_calcule != nom_reseau_str:
        st.info("Cliquez sur le bouton ci-dessus pour lancer l'analyse.")
        return

    population_grid_agglo, land_use_data, BPE_agglo, ttm = st.session_state.pipeline_data

    st.success(f"✓ {len(population_grid_agglo)} carreaux actifs — matrice des temps de trajet prête.")

    fond_carte = st.selectbox("Fond de carte", options=list(FONDS_CARTE.keys()))

    st.markdown("### Cartes d'accessibilité par domaine d'équipement")

    onglets = st.tabs([f"{d} - {nom}" for d, nom in DOMAINES_BPE.items()])
    for onglet, domaine in zip(onglets, DOMAINES_BPE):
        with onglet:
            with st.spinner(f"Calcul de la carte {domaine}..."):
                carte = _carte_accessibilite_domaine(
                    population_grid_agglo, land_use_data, BPE_agglo, ttm, domaine, fond_carte
                )
            st.components.v1.html(carte.get_root().render(), height=520, scrolling=False)

            html_path = os.path.join(OUTPUT_DIR, f"accessibilite_spatiale_{domaine}_{nom_reseau_str}.html")
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            carte.save(html_path)
            with open(html_path, "rb") as f:
                st.download_button(
                    f"💾 Télécharger la carte {domaine} (HTML)",
                    data=f,
                    file_name=os.path.basename(html_path),
                    mime="text/html",
                    key=f"download_{domaine}",
                )
