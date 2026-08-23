"""
app_2.py — variante allégée de app.py, dédiée à la vue "Accessibilité
urbaine" (carte 45 min + carte équipements pondérés côte à côte, cf.
views/accessibilite_urbaine_2.py). Contrairement à app.py : pas d'upload
libre, pas de recherche transport.data.gouv.fr, pas de fusion multi-GTFS —
uniquement les GTFS déjà présents dans data/GTFS ou déjà indexés sur le
dataset Hugging Face (antoinechevre/accessibility-data), et un seul écran
(pas de navigation multi-pages).

Pensé pour un déploiement séparé (Space HF dédié, cf. conversation) — cf.
app.py pour la version complète avec upload/recherche/fusion/navigation.
"""

import os
import tempfile

import streamlit as st

from src.hf_cache import enregistrer_visite, lister_fichiers_hf, recuperer_depuis_hf
from src.info_reseau import dates_service, nom_reseau_str
from src.transport_data_gouv import charger_provenance, enregistrer_zone
from src.utils import charger_gtfs
from src.vacances_scolaires import departement_academie_zone_pour_feed
from views.accessibilite_urbaine_2 import accessibilite_urbaine_page_2


class TropAgencesError(Exception):
    """Levée quand le GTFS regroupe trop d'agences pour être traité par l'app."""


# Réseaux dont le nom ne peut pas être dérivé automatiquement des agences du
# GTFS (IDFM et Aix-Marseille regroupent plus de 4 agences chacun) — doit
# rester synchronisé avec GTFS_NOM_RESEAU_FORCE dans app.py et
# NOMS_RESEAU_FORCES dans scripts/rafraichir_gtfs.py : mêmes clés utilisées
# pour les chemins de cache HF (memory_ttm/memory_pbf/..., nommés d'après
# nom_reseau_str) — un nom différent ici manquerait le cache déjà construit
# par l'autre app pour ces réseaux.
GTFS_NOM_RESEAU_FORCE = {
    "IDFM-gtfs_metro-rer-bus-tram_paris-petite-couronne.zip": "IDFM",
    "IDFM-gtfs.zip": "IDFM",
    "Aix_Marseille_mamp_GTFS.zip": "Aix_Marseille",
    "51_REGION_GRANDEST.gtfs.zip": "51-Marne",
}

GTFS_DATA_DIR = os.path.join(os.getcwd(), "data", "GTFS")

st.set_page_config(page_title="Accessibilité urbaine — 45 min & équipements pondérés", page_icon="🚌", layout="wide")

# Réserve en permanence la place de la scrollbar verticale, affichée ou non :
# sur Windows/Chrome (scrollbar "classique", qui réduit la largeur du
# contenu quand elle apparaît — contrairement à macOS où elle flotte en
# overlay sans impact sur la largeur), une page dont la hauteur oscille tout
# juste autour du seuil d'apparition de la scrollbar entre dans une boucle :
# scrollbar apparaît -> largeur du contenu réduite -> les cartes
# (st.components.v1.html sans width= fixe, cf. cartographie.py) se
# redimensionnent en réponse -> repasse sous le seuil -> scrollbar disparaît
# -> largeur réaugmente -> etc. Observé sur PC Windows (jamais sur Mac) : les
# cartes de cette page "tremblent" en continu sans jamais se stabiliser — la
# scrollbar-gutter figée coupe cette boucle à la racine. Même règle que
# app.py (page distincte, pas de CSS partagé entre les deux).
st.markdown(
    """<style>
    html,
    body,
    [data-testid="stAppViewContainer"],
    [data-testid="stMain"],
    section.main {
        scrollbar-gutter: stable;
    }
    </style>""",
    unsafe_allow_html=True,
)

# Enregistre une visite (même dataset HF, même digest quotidien que app.py —
# cf. .github/workflows/notifier_visites.yml) dans un thread à part pour ne
# pas retarder le premier rendu de la page avec un appel réseau best-effort.
if "visite_enregistree" not in st.session_state:
    st.session_state.visite_enregistree = True
    import threading
    threading.Thread(target=enregistrer_visite, daemon=True).start()

st.title("Accessibilité urbaine — 45 min & équipements pondérés")

st.sidebar.header("📁 Paramètres d'analyse")

# Catalogue = GTFS déjà présents sur disque (data/GTFS, absent au premier
# démarrage d'un déploiement sans stockage persistant) ∪ déjà indexés sur le
# dataset HF (téléchargés à la demande, cf. charger_gtfs_2 plus bas) — même
# logique que app.py:340-345, sans file_uploader ni recherche PAN.
gtfs_locaux_disque = sorted(
    f for f in os.listdir(GTFS_DATA_DIR) if f.lower().endswith(".zip")
) if os.path.isdir(GTFS_DATA_DIR) else []
gtfs_locaux_hf = sorted(f for f in lister_fichiers_hf("GTFS") if f.lower().endswith(".zip"))
gtfs_locaux = sorted(set(gtfs_locaux_disque) | set(gtfs_locaux_hf))

nom_gtfs_choisi = st.sidebar.selectbox(
    "Choisir un GTFS déjà présent",
    options=gtfs_locaux,
    index=None,
    placeholder="Sélectionner un réseau...",
)

if "feed" not in st.session_state:
    st.session_state.feed = None
if "date_str" not in st.session_state:
    st.session_state.date_str = None
if "date_debut" not in st.session_state:
    st.session_state.date_debut = None
if "date_fin" not in st.session_state:
    st.session_state.date_fin = None
if "nom_reseau_str" not in st.session_state:
    st.session_state.nom_reseau_str = None
if "zip_path" not in st.session_state:
    st.session_state.zip_path = None
if "last_uploaded_name" not in st.session_state:
    st.session_state.last_uploaded_name = None


def charger_gtfs_2(nom_gtfs):
    """Charge le GTFS nom_gtfs (déjà sur disque ou récupéré depuis le
    dataset HF) dans st.session_state — version réduite de
    charger_donnees_gtfs() dans app.py (pas d'upload, pas de fusion, pas de
    recherche/association PAN : le fichier est déjà indexé, cf. sidebar
    ci-dessus)."""
    nouveau_fichier = nom_gtfs != st.session_state.last_uploaded_name
    if not nouveau_fichier and st.session_state.feed is not None:
        return True

    chemin_gtfs_local = os.path.join(GTFS_DATA_DIR, nom_gtfs)
    if not os.path.exists(chemin_gtfs_local):
        with st.spinner(f"Récupération de {nom_gtfs} depuis Hugging Face..."):
            if not recuperer_depuis_hf(f"GTFS/{nom_gtfs}", chemin_gtfs_local):
                st.error(f"Impossible de récupérer {nom_gtfs} depuis Hugging Face.")
                return False

    # Copie dans un fichier temporaire plutôt que d'opérer directement sur
    # data/GTFS/<fichier> : charger_gtfs() peut réécrire le zip en place si
    # calendar_dates.txt est vide (cf. src.utils._retirer_table_vide_du_zip)
    # — sur le fichier original, ça modifierait silencieusement la source
    # versionnée sur le dataset HF.
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
        with open(chemin_gtfs_local, "rb") as source:
            tmp_file.write(source.read())
        GTFS_PATH = tmp_file.name

    try:
        with st.spinner("Chargement du GTFS..."):
            feed = charger_gtfs(GTFS_PATH)

            # Même garde-fou que app.py : un GTFS régional/national
            # regroupant de nombreuses agences ferait exploser les temps de
            # calcul et n'a pas de sens pour les indicateurs proposés ici.
            nb_agences = len(feed.agency)
            exception_valide = nom_gtfs in GTFS_NOM_RESEAU_FORCE
            if nb_agences > 4 and not exception_valide:
                raise TropAgencesError(nb_agences)

            # Académie/zone de vacances scolaires du réseau : réutilise
            # l'académie déjà connue dans gtfs_sources.json pour un GTFS
            # déjà indexé (pas de nouvel appel réseau à chaque chargement),
            # sinon la (re)détermine ici (reverse géocodage sur le
            # barycentre des arrêts, cf. src/vacances_scolaires.py) et
            # complète l'index dès que ça réussit — même logique que
            # app.py:599-610.
            provenance_connue = charger_provenance().get(nom_gtfs, {})
            academie_fraichement_resolue = False
            if provenance_connue.get("academie"):
                code_departement = provenance_connue.get("code_departement")
                academie = provenance_connue["academie"]
                zone_vacances = provenance_connue.get("zone")
            else:
                try:
                    code_departement, academie, zone_vacances = departement_academie_zone_pour_feed(feed)
                except Exception:
                    code_departement, academie, zone_vacances = None, None, None
                academie_fraichement_resolue = academie is not None

            # Plage de service fiable et jour ouvré de base (dernier
            # mardi/jeudi hors vacances scolaires de l'académie si connue,
            # cf. src/info_reseau.dates_service)
            _, date_debut, date_fin, date_JOB = dates_service(feed, academie=academie)

            # nom_reseau_str() (pas nom_reseau()) : sanitize les noms
            # d'agences pour un usage sûr dans un chemin de fichier (cf.
            # chemins_reseau).
            if nom_gtfs in GTFS_NOM_RESEAU_FORCE:
                reseau_str = GTFS_NOM_RESEAU_FORCE[nom_gtfs]
            else:
                reseau_str = str(nom_reseau_str(feed))

        st.session_state.feed = feed
        st.session_state.date_str = date_JOB
        st.session_state.date_debut = date_debut
        st.session_state.date_fin = date_fin
        st.session_state.zip_path = GTFS_PATH
        st.session_state.nom_reseau_str = reseau_str
        st.session_state.last_uploaded_name = nom_gtfs

        if academie_fraichement_resolue:
            try:
                enregistrer_zone(nom_gtfs, code_departement, academie, zone_vacances)
            except Exception:
                pass

        return True

    except TropAgencesError as e:
        st.error(f"⚠ Ce GTFS regroupe {e.args[0]} agences : ce que l'app ne peut pas gérer.")
        os.unlink(GTFS_PATH)
        st.stop()

    except Exception as e:
        st.error(f"Erreur lors du chargement : {e}")
        os.unlink(GTFS_PATH)
        return False


if nom_gtfs_choisi:
    charger_gtfs_2(nom_gtfs_choisi)

accessibilite_urbaine_page_2()
