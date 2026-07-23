"""
Application d'analyse de l'accessibilité piétons / transports collectifs à 30 min des équipements - Interface principale

"""

import os
import sys
import tempfile

sys.path.append('..')

import streamlit as st

from src.utils import charger_gtfs, obtenir_service_ids_pour_date
from src.info_reseau import dates_service, recuperer_logo_reseau, nom_reseau
from src.hf_cache import lister_fichiers_hf, recuperer_depuis_hf
from views.home import home_page
from views.accessibilite_index import accessibilite_index_page
from views.ponderation_equipements import ponderation_equipements_page
from views.cartographie_insee import cartographie_insee_page


class TropAgencesError(Exception):
    """Levée quand le GTFS regroupe trop d'agences pour être traité par l'app."""


# Configuration de la page
st.set_page_config(page_title="Analyse accessibilite aux différents équipements d'agglomération piéton / transport collectif (GTFS)", page_icon="🚌", layout="wide")

# Titre principal
st.title("Analyse accessibilite aux différents équipements d'agglomération piéton / transport collectif (GTFS)")
st.caption("🚌 Bus · 🚊 Tramway · 🚇 Métro · ⛴️ Ferry · 🚶 Piétons")


# Navigation horizontale en haut
st.markdown(
    """
<style>
.stButton button {
    width: 100% !important;
    margin: 0 !important;
}
</style>
""",
    unsafe_allow_html=True,
)

st.markdown("---")
col1, col2, col3, col4, col5 = st.columns([1, 1, 1, 1, 2])  # colonnes pour équilibrer l'espace

with col1:
    if st.button("🏠 Accueil", use_container_width=True):
        st.session_state.selected_page = "Accueil"

with col2:
    if st.button("📍 Accessibilité", use_container_width=True):
        st.session_state.selected_page = "Accessibilité"

with col3:
    if st.button("⚖️ Pondération équipements", use_container_width=True):
        st.session_state.selected_page = "Pondération équipements"

with col4:
    if st.button("🗺️ Cartographie INSEE", use_container_width=True):
        st.session_state.selected_page = "Cartographie INSEE"

with col5:
    st.write("")  # Espace vide pour équilibrer


# Initialiser la page sélectionnée si pas déjà fait
if "selected_page" not in st.session_state:
    st.session_state.selected_page = "Accueil"

# Barre latérale pour les paramètres uniquement
st.sidebar.header("📁 Paramètres d'analyse")
uploaded_file = st.sidebar.file_uploader("Uploader le fichier GTFS (zip)", type="zip")

# Alternative à l'upload : choisir un GTFS déjà présent dans data/GTFS ou
# dans le catalogue du dataset HF (mêmes fichiers, téléversés une fois pour
# toutes, cf. src/hf_cache.py). Union des deux plutôt que l'un OU l'autre :
# data/GTFS n'est pas versionné par git (cf. .gitignore) donc vide sur un
# déploiement fraîchement démarré sans stockage persistant, mais peut aussi
# contenir 1-2 fichiers déjà téléchargés à la demande lors d'une sélection
# précédente (cf. charger_donnees_gtfs ci-dessous) — s'arrêter au premier
# non-vide masquerait alors silencieusement tout le reste du catalogue HF.
AUCUN_GTFS_LOCAL = "— aucun —"
GTFS_DATA_DIR = os.path.join(os.getcwd(), "data", "GTFS")
gtfs_locaux_disque = sorted(
    f for f in os.listdir(GTFS_DATA_DIR) if f.lower().endswith(".zip")
) if os.path.isdir(GTFS_DATA_DIR) else []
gtfs_locaux_hf = sorted(f for f in lister_fichiers_hf("GTFS") if f.lower().endswith(".zip"))
gtfs_locaux = sorted(set(gtfs_locaux_disque) | set(gtfs_locaux_hf))

gtfs_local_choisi = st.sidebar.selectbox(
    "...ou choisir un GTFS déjà présent",
    options=[AUCUN_GTFS_LOCAL] + gtfs_locaux,
)

# Variables globales pour stocker les résultats
if "feed" not in st.session_state:
    st.session_state.feed = None
if "active_service_ids" not in st.session_state:
    st.session_state.active_service_ids = None
if "date_str" not in st.session_state:
    st.session_state.date_str = None
if "indicateurs_arrets" not in st.session_state:
    st.session_state.indicateurs_arrets = None
if "indicateurs_bus" not in st.session_state:
    st.session_state.indicateurs_bus = None
if "indicateurs_tram" not in st.session_state:
    st.session_state.indicateurs_tram = None
if "indicateurs_metro" not in st.session_state:
    st.session_state.indicateurs_metro = None
if "indicateurs_trolley" not in st.session_state:
    st.session_state.indicateurs_trolley = None
if "indicateurs_ferry" not in st.session_state:
    st.session_state.indicateurs_ferry = None
if "total_vk_plage" not in st.session_state:
    st.session_state.total_vk_plage = None
if "modes_disponibles" not in st.session_state:
    st.session_state.modes_disponibles = None
if "last_date_str" not in st.session_state:
    st.session_state.last_date_str = None
if "nom_reseau_str" not in st.session_state:
    st.session_state.nom_reseau_str = None
if "zip_path" not in st.session_state:
    st.session_state.zip_path = None
if "chemin_logo" not in st.session_state:
    st.session_state.chemin_logo = None
if "last_uploaded_name" not in st.session_state:
    st.session_state.last_uploaded_name = None


# Fonction pour charger les données. La date d'analyse (date_JOB) n'est
# pas choisie par l'utilisateur : elle est déterminée automatiquement à
# partir du GTFS (un mardi ou un jeudi tiré au hasard dans la plage de
# service fiable, voir src/info_reseau.dates_service).
def charger_donnees_gtfs():
    if uploaded_file is not None:
        nom_gtfs = uploaded_file.name
        lire_gtfs = uploaded_file.read
    elif gtfs_local_choisi != AUCUN_GTFS_LOCAL:
        nom_gtfs = gtfs_local_choisi
        chemin_gtfs_local = os.path.join(GTFS_DATA_DIR, gtfs_local_choisi)
        # recuperer_depuis_hf() ne fait rien si déjà présent en local (cas
        # gtfs_locaux_disque) : pas besoin de distinguer les deux sources ici.
        if not os.path.exists(chemin_gtfs_local):
            with st.spinner(f"Récupération de {gtfs_local_choisi} depuis Hugging Face..."):
                if not recuperer_depuis_hf(f"GTFS/{gtfs_local_choisi}", chemin_gtfs_local):
                    st.error(f"Impossible de récupérer {gtfs_local_choisi} depuis Hugging Face.")
                    return False
        lire_gtfs = lambda: open(chemin_gtfs_local, "rb").read()
    else:
        return False

    # Ne recharger le GTFS (et le logo, qui nécessite une requête réseau)
    # que si un nouveau fichier a été sélectionné, pas à chaque interaction
    nouveau_fichier = nom_gtfs != st.session_state.last_uploaded_name

    if not nouveau_fichier and st.session_state.feed is not None:
        return True

    # Copie dans un fichier temporaire (conservé pour toute la session :
    # create_carte_arrets recharge le feed depuis ce chemin pour tracer les
    # lignes) plutôt que d'opérer directement sur data/GTFS/<fichier> : entre
    # autres, charger_gtfs() peut réécrire le zip en place si calendar_dates.txt
    # est vide (cf. src/utils._retirer_table_vide_du_zip) — sur le fichier
    # original de data/GTFS, ça modifierait silencieusement la source versionnée
    # sur le dataset HF.
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
        tmp_file.write(lire_gtfs())
        GTFS_PATH = tmp_file.name

    try:
        # Charger le GTFS
        with st.spinner("Chargement du fichier GTFS..."):
            feed = charger_gtfs(GTFS_PATH)

        # L'app ne sait traiter que des GTFS urbains (un GTFS national/régional
        # regroupant de nombreuses agences ferait exploser les temps de calcul
        # et n'a pas de sens pour les indicateurs arrêts/tronçons proposés ici)
        nb_agences = len(feed.agency)
        if nb_agences > 4:
            raise TropAgencesError(nb_agences)

        # Plage de service fiable et jour ouvré de base (mardi/jeudi au hasard)
        _, _, _, date_JOB = dates_service(feed)
        date_str = date_JOB

        # Obtenir les services actifs
        active_service_ids = obtenir_service_ids_pour_date(feed, date_str)

        # Nom du réseau et logo (best-effort : le logo nécessite une
        # requête réseau vers le site de l'agence, ne doit pas bloquer
        # l'appli en cas d'échec)
        reseau_str = str(nom_reseau(feed))
        try:
            chemin_logo = recuperer_logo_reseau(feed, dossier_sortie=tempfile.gettempdir())
        except Exception:
            chemin_logo = None

        # Stocker dans session_state
        st.session_state.feed = feed
        st.session_state.active_service_ids = active_service_ids
        st.session_state.date_str = date_str
        st.session_state.zip_path = GTFS_PATH
        st.session_state.nom_reseau_str = reseau_str
        st.session_state.last_uploaded_name = nom_gtfs
        st.session_state.decoupage_reference_path_reseau = None
        st.session_state.decoupage_agglo = None
                
        return True

    except TropAgencesError as e:
        st.error(f"⚠ Ce GTFS regroupe {e.args[0]} agences : ce que l'app ne peut pas gérer. Charger un GTFS urbain uniquement.")
        os.unlink(GTFS_PATH)
        st.stop()

    except Exception as e:
        st.error(f"Erreur lors du chargement : {e}")
        os.unlink(GTFS_PATH)
        return False


# Charger les données automatiquement si nécessaire
charger_donnees_gtfs()

# Navigation entre les pages
if st.session_state.selected_page == "Accueil":
    home_page()
elif st.session_state.selected_page == "Accessibilité":
    accessibilite_index_page()
elif st.session_state.selected_page == "Pondération équipements":
    ponderation_equipements_page()
elif st.session_state.selected_page == "Cartographie INSEE":
    cartographie_insee_page()
