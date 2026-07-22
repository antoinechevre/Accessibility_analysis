"""
Page Accessibilité - reprend le pipeline du notebook
index_accessibility_notebook_def.ipynb (GTFS -> réseau piéton/transport
collectif -> carroyage INSEE -> BPE pondérée -> matrice des temps de trajet
-> indicateur d'accessibilité à 30 min) pour l'exposer dans l'app Streamlit,
avec les cartes HTML interactives par domaine BPE.
"""

import os
import datetime

import folium
import pandas as pd
import streamlit as st
from folium.plugins import DualMap

from src.BPE_traitement import land_use_data_domaine
from src.build_data_agglo import osm_pbf_creator
from src.cartographie import script_reajuster_si_masque, titre_carte_html
from src.hf_cache import recuperer_depuis_hf
from src.pipeline_donnees import DOMAINES_BPE, chemins_reseau, construire_donnees_bpe
from src.utilitaires_matrix import cost_to_closest, cumulative_cutoff
from src.utils import preparer_gtfs_pour_r5py

BASE_DIR = os.getcwd()
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

FONDS_CARTE = {
    "OpenStreetMap": "OpenStreetMap",
    "CartoDB Positron": "CartoDB positron",
    "CartoDB Dark Matter": "CartoDB dark_matter",
}

# r5py est importé paresseusement (cf. _assurer_r5py_pret ci-dessous), pas au
# chargement du module : app.py importe ce module de façon inconditionnelle
# au démarrage (pour toutes les pages), et importer r5py démarre sa JVM
# immédiatement. Un import au niveau module ferait donc démarrer la JVM (et
# réserver sa mémoire) même pour un visiteur qui ne va jamais sur cette page.
r5py = None


def _assurer_r5py_pret():
    """Importe r5py et configure sa JVM au premier usage réel (calcul du
    réseau de transport / matrice des temps de trajet), pas avant."""
    global r5py
    if r5py is not None:
        return

    # Chemin JAVA_HOME du poste de développement (macOS/Temurin 21). Ne
    # s'applique que s'il existe réellement : sur un déploiement Linux (ex.
    # Streamlit Community Cloud, cf. packages.txt), ce chemin n'existe pas et
    # on laisse jpype détecter automatiquement le JDK installé par apt
    # (JAVA_HOME n'a pas besoin d'être positionné pour ça).
    _java_home_macos = "/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home"
    if "JAVA_HOME" not in os.environ and os.path.isdir(_java_home_macos):
        os.environ["JAVA_HOME"] = _java_home_macos

    # Même ordre d'import qu'en cellule 1 du notebook : importer rasterio
    # AVANT r5py initialise le contexte PROJ/GDAL avec le proj.db du venv,
    # avant que le démarrage de la JVM par r5py ne pollue PROJ_LIB avec un
    # chemin invalide.
    import rasterio  # noqa: F401
    import r5py as r5py_module
    import r5py.util.jvm

    # 512 Mo par défaut (sûr sur un hôte à RAM limitée, ex. Streamlit
    # Community Cloud ~1 Go total). Sur un tier payant avec plus de RAM (ex.
    # Hugging Face Spaces CPU upgrade), remonter via la variable d'env
    # R5PY_MAX_JVM_MEMORY_MB (cf. Dockerfile et README, section Déploiement)
    # plutôt qu'en modifiant ce fichier.
    r5py_module.util.jvm.MAX_JVM_MEMORY = int(os.environ.get("R5PY_MAX_JVM_MEMORY_MB", 512)) * 1024**2

    r5py = r5py_module


@st.cache_resource(show_spinner=False)
def _construire_reseau_transport(osm_pbf_path, gtfs_r5py_path):
    """Construit le TransportNetwork r5py (objet Java non sérialisable : mis
    en cache via st.cache_resource plutôt que st.session_state)."""
    _assurer_r5py_pret()
    return r5py.TransportNetwork(osm_pbf=osm_pbf_path, gtfs=[gtfs_r5py_path])


def _construire_pipeline(zip_path, nom_reseau_str, date_JOB):
    """Reconstruit (ou recharge depuis le cache disque) toutes les données
    nécessaires : découpage communal, carroyage population, BPE pondérée
    (via src.pipeline_donnees, partagé avec views/ponderation_equipements.py),
    puis extrait OSM, réseau de transport et matrice des temps de trajet.

    Toutes les valeurs ici sont locales : elles ne servent qu'à construire le
    tuple final renvoyé, seul élément que l'appelant (accessibilite_index_page)
    stocke en session_state. Les étapes sont affichées dans un st.status()
    qui reste visible (dépliable) une fois le calcul terminé, plutôt qu'un
    message transitoire qui disparaît.
    """
    with st.status("Préparation des données d'accessibilité...", expanded=True) as status:
        population_grid_agglo, land_use_data, BPE_agglo = construire_donnees_bpe(
            zip_path, nom_reseau_str, on_step=lambda message: st.write(message)
        )

        chemins = chemins_reseau(nom_reseau_str)
        osm_pbf_path = chemins["osm_pbf"]
        ttm_path = chemins["ttm"]

        if not os.path.exists(osm_pbf_path):
            if recuperer_depuis_hf(f"memory_pbf/agglo_osm_pbf_{nom_reseau_str}.osm.pbf", osm_pbf_path):
                st.write("✓ Extrait OSM récupéré depuis le cache Hugging Face")
            else:
                st.write("Extraction des données OSM (Overpass)... peut prendre plusieurs minutes")
                osm_pbf_creator(chemins["decoupage_geojson"], output_pbf_path=osm_pbf_path)
                st.write("✓ Extrait OSM prêt")

        if not os.path.exists(ttm_path) and recuperer_depuis_hf(
            f"memory_ttm/ttm_{nom_reseau_str}.parquet", ttm_path
        ):
            st.write("✓ Matrice des temps de trajet récupérée depuis le cache Hugging Face")

        if not os.path.exists(ttm_path):
            st.write(
                "Calcul de la matrice des temps de trajet (r5py)... "
                "premier lancement pour ce réseau, peut prendre plusieurs minutes"
            )
            _assurer_r5py_pret()
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
            st.write("✓ Matrice des temps de trajet prête")

        ttm = pd.read_parquet(ttm_path)
        status.update(label="Données d'accessibilité prêtes", state="complete", expanded=False)

    return population_grid_agglo, land_use_data, BPE_agglo, ttm


def _carte_temps_acces_pole_domaine(population_grid_agglo, land_use_data, BPE_agglo, ttm, domaine, fond_carte):
    """Carte HTML interactive du temps d'accès au pôle d'équipements le plus
    proche pour un domaine BPE donné (cost_to_closest, restreint aux carreaux
    "pôles" pole_equipements_{domaine}). Équivalent de la section 9.1 du
    notebook."""
    nom_domaine = DOMAINES_BPE.get(domaine, domaine)

    poles_domaine = land_use_data[["id", f"pole_equipements_{domaine}"]].rename(
        columns={f"pole_equipements_{domaine}": domaine}
    )

    min_time = cost_to_closest(
        land_use_data_domaine,
        BPE_agglo,
        land_use_data,
        DOMAINES_BPE,
        ttm,
        opportunity=domaine,
        travel_cost="travel_time",
        land_use_data=poles_domaine,
    )

    carte_temps = population_grid_agglo[["id", "geometry"]].merge(min_time, on="id")
    # Comme TMISA dans le livre (cf. notebook 9.1) : plafonné à 60 min pour la
    # lisibilité de la carte, au-delà seule l'idée de "trop loin" compte.
    carte_temps["travel_time_plafonne"] = carte_temps["travel_time"].clip(upper=60)

    carte = carte_temps.explore(
        column="travel_time_plafonne",
        cmap="cividis_r",
        tiles=FONDS_CARTE[fond_carte],
        legend=True,
        legend_kwds={"caption": "Temps (min)"},
        style_kwds={"weight": 0, "opacity": 0},
    )

    carte.get_root().html.add_child(
        folium.Element(titre_carte_html(f"Temps d'accès au pôle d'équipements le plus proche – {nom_domaine}"))
    )

    minx, miny, maxx, maxy = carte_temps.to_crs(epsg=4326).total_bounds
    carte.get_root().html.add_child(
        folium.Element(script_reajuster_si_masque(carte, [[miny, minx], [maxy, maxx]]))
    )

    return carte


def _carte_poles_accessibles_domaine(population_grid_agglo, land_use_data, ttm, domaine, fond_carte):
    """Carte HTML interactive (DualMap 30 min / 45 min) du nombre de pôles
    d'équipements accessibles pour un domaine BPE donné, restreint aux
    carreaux "pôles" pole_equipements_{domaine}. Équivalent de la section
    9.2 du notebook."""
    nom_domaine = DOMAINES_BPE.get(domaine, domaine)

    poles_domaine = land_use_data[["id", f"pole_equipements_{domaine}"]].rename(
        columns={f"pole_equipements_{domaine}": domaine}
    )

    cum_30 = cumulative_cutoff(ttm, land_use_data=poles_domaine, opportunity=domaine, travel_cost="travel_time", cutoff=30)
    cum_45 = cumulative_cutoff(ttm, land_use_data=poles_domaine, opportunity=domaine, travel_cost="travel_time", cutoff=45)

    limite_commune = max(cum_30[domaine].max(), cum_45[domaine].max())

    carte_30 = population_grid_agglo[["id", "geometry"]].merge(cum_30, on="id")
    carte_45 = population_grid_agglo[["id", "geometry"]].merge(cum_45, on="id")

    dual_map = DualMap(tiles=FONDS_CARTE[fond_carte], layout="horizontal")
    carte_30.explore(
        column=domaine,
        cmap="inferno",
        vmin=0,
        vmax=limite_commune,
        legend=True,
        legend_kwds={"caption": f"{nom_domaine} (pôles) – 30 min"},
        style_kwds={"weight": 0, "opacity": 0},
        m=dual_map.m1,
    )
    carte_45.explore(
        column=domaine,
        cmap="inferno",
        vmin=0,
        vmax=limite_commune,
        legend=True,
        legend_kwds={"caption": f"{nom_domaine} (pôles) – 45 min"},
        style_kwds={"weight": 0, "opacity": 0},
        m=dual_map.m2,
    )

    # .explore(m=...) ajoute la couche à une carte existante sans recentrer
    # dessus (contrairement à .explore() sans m=, qui fait un fit_bounds
    # automatique) : DualMap() démarre donc sur sa vue par défaut ([0, 0],
    # zoom 1) sans ce fit_bounds explicite sur les deux volets.
    minx, miny, maxx, maxy = carte_30.to_crs(epsg=4326).total_bounds
    bounds = [[miny, minx], [maxy, maxx]]
    dual_map.m1.fit_bounds(bounds)
    dual_map.m2.fit_bounds(bounds)

    dual_map.get_root().html.add_child(
        folium.Element(titre_carte_html(f"Pôles d'équipements accessibles – {nom_domaine} (30 min / 45 min)"))
    )
    dual_map.get_root().html.add_child(folium.Element(script_reajuster_si_masque(dual_map.m1, bounds)))
    dual_map.get_root().html.add_child(folium.Element(script_reajuster_si_masque(dual_map.m2, bounds)))

    return dual_map


def accessibilite_index_page():
    st.header("♿ Accessibilité aux équipements (30 min / 45 min)")

    if st.session_state.get("feed") is None:
        st.info("👆 Veuillez charger un fichier GTFS dans la barre latérale.")
        return

    nom_reseau_str = st.session_state.nom_reseau_str
    date_str = st.session_state.date_str
    zip_path = st.session_state.zip_path

    st.write(f"Réseau : **{nom_reseau_str}** — jour de référence : {date_str}")

    # La matrice des temps de trajet (ttm) est mise en cache sur disque par
    # réseau (cf. _construire_pipeline) : si le fichier existe déjà, ce n'est
    # pas le premier lancement pour ce réseau, pas besoin d'avertir sur le
    # temps de calcul.
    premier_lancement = not os.path.exists(chemins_reseau(nom_reseau_str)["ttm"])

    if premier_lancement:
        st.warning(
            "⚠️ Premier lancement pour ce réseau : extraction OSM puis calcul de la "
            "matrice des temps de trajet (r5py), potentiellement long (plusieurs "
            "minutes) et gourmand en mémoire. Les résultats sont mis en cache sur "
            "disque pour les lancements suivants."
        )
    else:
        st.info("✓ Résultats déjà en cache pour ce réseau : le calcul sera quasi instantané.")

    lancer = st.button("🚀 Lancer / recharger l'analyse d'accessibilité", use_container_width=True)

    if "reseau_calcule" not in st.session_state:
        st.session_state.reseau_calcule = None

    if lancer:
        try:
            population_grid_agglo, land_use_data, BPE_agglo, ttm = _construire_pipeline(
                zip_path, nom_reseau_str, date_str
            )
        except Exception as e:
            # str(e) peut être vide (ex. MemoryError() par défaut) : le type
            # de l'exception donne l'info utile dans ce cas.
            st.error(f"Erreur pendant le calcul : {type(e).__name__}: {e}")
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
            st.markdown("#### Temps d'accès au pôle d'équipements le plus proche")
            with st.spinner(f"Calcul du temps d'accès {domaine}..."):
                carte_temps = _carte_temps_acces_pole_domaine(
                    population_grid_agglo, land_use_data, BPE_agglo, ttm, domaine, fond_carte
                )
            st.components.v1.html(carte_temps.get_root().render(), height=520, scrolling=False)

            html_path_temps = os.path.join(OUTPUT_DIR, f"accessibilite_temps_{domaine}_{nom_reseau_str}.html")
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            carte_temps.save(html_path_temps)
            with open(html_path_temps, "rb") as f:
                st.download_button(
                    f"💾 Télécharger la carte temps d'accès {domaine} (HTML)",
                    data=f,
                    file_name=os.path.basename(html_path_temps),
                    mime="text/html",
                    key=f"download_temps_{domaine}",
                )

            st.markdown("#### Pôles d'équipements accessibles (30 min vs 45 min)")
            st.caption(
                "Pôles majeurs d'équipements accessible depuis chaque carreau à 30 min et 45 min "
                "(cf onglet pondérations équipements pour comprendre l'analyse des équipements)."
            )
            with st.spinner(f"Calcul des pôles accessibles {domaine}..."):
                carte_poles = _carte_poles_accessibles_domaine(
                    population_grid_agglo, land_use_data, ttm, domaine, fond_carte
                )
            st.components.v1.html(carte_poles.get_root().render(), height=520, scrolling=False)

            html_path_poles = os.path.join(OUTPUT_DIR, f"accessibilite_poles_{domaine}_{nom_reseau_str}.html")
            carte_poles.save(html_path_poles)
            with open(html_path_poles, "rb") as f:
                st.download_button(
                    f"💾 Télécharger la carte pôles accessibles {domaine} (HTML)",
                    data=f,
                    file_name=os.path.basename(html_path_poles),
                    mime="text/html",
                    key=f"download_poles_{domaine}",
                )
