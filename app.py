"""
Application d'analyse de l'accessibilité piétons / transports collectifs à 30 min des équipements - Interface principale

"""

import os
import sys
import tempfile
import urllib.parse

sys.path.append('..')

import streamlit as st

from src.utils import charger_gtfs
from src.info_reseau import dates_service, nom_fichier_valide, nom_reseau_str
from src.hf_cache import envoyer_vers_hf, lister_fichiers_hf, recuperer_depuis_hf
from src.merge_gtfs import fusionner_gtfs
from views.home import home_page
from views.accessibilite_index import GTFS_ANALYSE_URL, accessibilite_index_page
from views.ponderation_equipements import ponderation_equipements_page
from views.cartographie_insee import cartographie_insee_page
from views.benchmark_reseaux import benchmark_reseaux_page


class TropAgencesError(Exception):
    """Levée quand le GTFS regroupe trop d'agences pour être traité par l'app."""


# Exceptions au garde-fou "max 4 agences" (cf. TropAgencesError ci-dessous),
# par nom de fichier GTFS : réseaux régionaux dont on sait qu'ils fonctionnent
# malgré tout, avec un nom de réseau forcé plutôt que dérivé automatiquement
# de agency.txt (nom_reseau_str() concatène tous les noms d'agence avec
# " / " — pour l'IDFM, ça donne une chaîne de plusieurs centaines de
# caractères, invalide comme nom de fichier sur la plupart des OS). Île-de-
# France Mobilités a deux GTFS distincts, tous deux forcés vers le même
# nom_reseau_str "IDFM" (donc le même cache memory_ttm/memory_pbf sur le
# dataset HF — un run écrase le cache de l'autre) :
# - extrait Paris + petite couronne (75/92/93/94) — cf. l'avertissement
#   affiché dans l'onglet Accessibilité (RESOLUTIONS_GRILLE_SPECIALES,
#   src/pipeline_donnees.py) ;
# - IDFM-gtfs.zip, régional complet (~1,1 Go décompressé, ~11M habitants) —
#   déjà calculé une fois via le notebook (carreaux 800m) et son résultat
#   téléversé sur le dataset HF, donc un run app sur ce fichier retrouve le
#   cache plutôt que de tout recalculer.
GTFS_NOM_RESEAU_FORCE = {
    "IDFM-gtfs_metro-rer-bus-tram_paris-petite-couronne.zip": "IDFM",
    "IDFM-gtfs.zip": "IDFM",
    # Aix-Marseille-Provence (AMP) : GTFS agrégé de la métropole (RTM Marseille
    # + réseaux communaux d'Aix-en-Provence et alentours), qui regroupe plus de
    # 4 agences comme IDFM — même besoin de nom forcé pour éviter la
    # concaténation à rallonge de nom_reseau_str().
    "Aix_Marseille_mamp_GTFS.zip": "Aix_Marseille",
}


# Configuration de la page
st.set_page_config(page_title="Analyse accessibilite aux différents équipements d'agglomération piéton / transport collectif (GTFS)", page_icon="🚌", layout="wide")

# Titre principal
st.title("Application accessibilite équipements d'agglomération piéton / transport collectif ")


# --- CSS V1 (ancien style, conservé pour pouvoir revenir en arrière) -------
# Pour restaurer le rendu V1 : commenter le bloc "CSS V2" plus bas et
# décommenter cet appel (et supprimer/renommer .streamlit/config.toml pour
# retrouver le thème rouge par défaut de Streamlit).
#
# st.markdown(
#     """
# <style>
# .stButton button {
#     width: 100% !important;
#     margin: 0 !important;
# }
# h1 {
#     margin-bottom: 1.5rem !important;
# }
# </style>
# """,
#     unsafe_allow_html=True,
# )

# --- CSS V2 -----------------------------------------------------------------
# Identité visuelle plus marquée (cf. audit_ux_ui_accessibility.txt) : la
# palette elle-même vient de .streamlit/config.toml (thème natif Streamlit,
# s'applique aussi aux widgets natifs — selectbox, file_uploader, etc. —
# qu'une simple règle CSS ne pourrait pas cibler proprement). Ce bloc ne
# couvre que ce que le thème ne fait pas : la barre de nav (boutons pleine
# largeur, état actif) et l'espacement des titres.
st.markdown(
    """
<style>
.stButton button {
    width: 100% !important;
    margin: 0 !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    transition: transform .08s ease, box-shadow .08s ease;
}
.stButton button:hover {
    transform: translateY(-1px);
    box-shadow: 0 2px 8px rgba(14, 124, 123, 0.18);
}
/* Bouton de la page active (type="primary") : accent plein plutôt que la
   simple bordure grise du bouton "secondary" par défaut — répond au
   constat F1/A5 de l'audit (aucune indication visuelle de la page
   courante dans la nav). */
.stButton button[kind="primary"] {
    box-shadow: 0 2px 10px rgba(14, 124, 123, 0.28);
}
h1 {
    margin-bottom: .3rem !important;
    letter-spacing: -0.01em;
}
h1 + div hr {
    margin-top: .6rem !important;
    margin-bottom: 1.4rem !important;
    border-top: 2px solid #0E7C7B33 !important;
}
h2, h3 {
    letter-spacing: -0.01em;
}
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {
    letter-spacing: -0.01em;
}

/* Nav à deux niveaux (cf. plus bas, conteneurs st.container(key=...)) :
   niveau 1 (section) plus affirmé, niveau 2 (sous-page) plus discret et
   légèrement en retrait pour se lire comme un sous-menu, pas un pair. */
.st-key-nav_niveau1 [data-testid="stButtonGroup"] button {
    font-size: 1rem;
    font-weight: 700;
    padding: .6rem 1.3rem;
}
.st-key-nav_niveau2 {
    background: rgba(14, 124, 123, 0.07);
    border-radius: 10px;
    padding: .5rem .6rem .35rem;
    margin-top: -.3rem;
}
.st-key-nav_niveau2 [data-testid="stButtonGroup"] button {
    font-size: .8rem;
    font-weight: 500;
    padding: .3rem .8rem;
}

/* Overlay de chargement GTFS (cf. st.container(key="overlay_chargement_gtfs")
   autour de charger_donnees_gtfs()) : transforme le rendu par défaut de
   st.spinner (petite icône + texte en ligne) en un écran de chargement
   plein écran, le temps de récupérer/fusionner/parser le GTFS. */
.st-key-overlay_chargement_gtfs [data-testid="stSpinner"] {
    position: fixed;
    inset: 0;
    z-index: 9999;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(18, 51, 53, 0.55);
    backdrop-filter: blur(2px);
}
.st-key-overlay_chargement_gtfs [data-testid="stSpinner"] > div {
    display: inline-flex !important;
    width: auto !important;
    max-width: min(90vw, 26rem);
    align-items: center;
    background: #FAFAF8;
    font-weight: 600;
    font-size: 1.05rem;
    padding: 1.5rem 2.2rem;
    border-radius: 12px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
}
.st-key-overlay_chargement_gtfs [data-testid="stSpinner"] p {
    color: #182422 !important;
    margin: 0 !important;
}
</style>
""",
    unsafe_allow_html=True,
)

st.markdown("---")

# Initialiser la page sélectionnée si pas déjà fait
if "selected_page" not in st.session_state:
    st.session_state.selected_page = "Accueil"

# Navigation à deux niveaux plutôt que 4-5 boutons isolés à plat : les 3
# pages qui forment un seul parcours (Accessibilité, Localisation et
# pondération équipements, Carte population par déciles — toutes dérivées
# du même GTFS/carroyage) sont regroupées sous "Analyse du réseau", à côté
# d'Accueil et Benchmark qui n'ont pas de lien direct avec elles ni entre
# eux. st.segmented_control (plutôt que des st.button) rend ce groupement
# visuellement explicite : les options d'un même contrôle sont connectées
# entre elles, celles de deux contrôles différents ne le sont pas.
GROUPES_NAV = {
    "Accueil": ["Accueil"],
    "Analyse du réseau": ["Accessibilité", "Pondération équipements", "Cartographie INSEE"],
    "Benchmark": ["Benchmark Villes Françaises"],
}
LIBELLES_GROUPE = {
    "Accueil": "🏠 Accueil",
    "Analyse du réseau": "📈 Analyse du réseau",
    "Benchmark": "📊 Benchmark villes françaises",
}
LIBELLES_PAGE = {
    "Accessibilité": "📍 Accessibilité",
    "Pondération équipements": "⚖️ Localisation et pondération équipements",
    "Cartographie INSEE": "🗺️ Carte population par déciles",
}
GROUPE_DE_LA_PAGE = {page: groupe for groupe, pages in GROUPES_NAV.items() for page in pages}

# Conteneurs à clé stable (.st-key-nav_niveau1/2 générés par Streamlit à
# partir du key=), pour différencier le style des deux niveaux sans dépendre
# des classes st-emotion-cache-* (générées aléatoirement, changent d'une
# session/version à l'autre — cf. règles CSS plus haut).
with st.container(key="nav_niveau1"):
    groupe_choisi = st.segmented_control(
        "Section",
        options=list(GROUPES_NAV.keys()),
        format_func=lambda g: LIBELLES_GROUPE[g],
        default=GROUPE_DE_LA_PAGE.get(st.session_state.selected_page, "Accueil"),
        required=True,
        label_visibility="collapsed",
        key="nav_groupe",
    )

pages_du_groupe = GROUPES_NAV[groupe_choisi]
if len(pages_du_groupe) == 1:
    st.session_state.selected_page = pages_du_groupe[0]
else:
    # Sous-menu "Analyse du réseau" : désactivé tant qu'aucun GTFS n'est
    # chargé (les 3 pages en dépendent toutes), plutôt que de laisser
    # naviguer vers un message "veuillez charger un GTFS" sur chacune.
    with st.container(key="nav_niveau2"):
        st.session_state.selected_page = st.segmented_control(
            "Page",
            options=pages_du_groupe,
            format_func=lambda p: LIBELLES_PAGE[p],
            default=(
                st.session_state.selected_page
                if st.session_state.selected_page in pages_du_groupe
                else pages_du_groupe[0]
            ),
            required=True,
            disabled=st.session_state.get("feed") is None,
            label_visibility="collapsed",
            key="nav_sous_page",
        )

# Barre latérale pour les paramètres uniquement
st.sidebar.header("📁 Paramètres d'analyse")
# accept_multiple_files : permet de charger plusieurs GTFS pour un même
# réseau agglomérat non couvert par un seul GTFS (ex: Aix-Marseille, dont le
# GTFS "Marseille" ne couvre que le RTM — pas le réseau d'Aix-en-Provence) —
# fusionnés via src.merge_gtfs.fusionner_gtfs avant chargement (cf.
# charger_donnees_gtfs ci-dessous). Un seul fichier reste géré exactement
# comme avant (aucune fusion déclenchée).
uploaded_files = st.sidebar.file_uploader(
    "Uploader le(s) fichier(s) GTFS (zip) — plusieurs fichiers = réseaux fusionnés",
    type="zip",
    accept_multiple_files=True,
)

# Alternative à l'upload : choisir un GTFS déjà présent dans data/GTFS ou
# dans le catalogue du dataset HF (mêmes fichiers, téléversés une fois pour
# toutes, cf. src/hf_cache.py). Union des deux plutôt que l'un OU l'autre :
# data/GTFS n'est pas versionné par git (cf. .gitignore) donc vide sur un
# déploiement fraîchement démarré sans stockage persistant, mais peut aussi
# contenir 1-2 fichiers déjà téléchargés à la demande lors d'une sélection
# précédente (cf. charger_donnees_gtfs ci-dessous) — s'arrêter au premier
# non-vide masquerait alors silencieusement tout le reste du catalogue HF.
GTFS_DATA_DIR = os.path.join(os.getcwd(), "data", "GTFS")
gtfs_locaux_disque = sorted(
    f for f in os.listdir(GTFS_DATA_DIR) if f.lower().endswith(".zip")
) if os.path.isdir(GTFS_DATA_DIR) else []
gtfs_locaux_hf = sorted(f for f in lister_fichiers_hf("GTFS") if f.lower().endswith(".zip"))
gtfs_locaux = sorted(set(gtfs_locaux_disque) | set(gtfs_locaux_hf))

gtfs_locaux_choisis = st.sidebar.multiselect(
    "...ou choisir un/des GTFS déjà présent(s) — plusieurs = réseaux fusionnés",
    options=gtfs_locaux,
)

nb_sources_gtfs = len(uploaded_files) + len(gtfs_locaux_choisis)

# nom_reseau_str() concatène les noms de toutes les agences des GTFS
# fusionnés (via " / ") : pour 2+ GTFS distincts, souvent long/peu lisible
# comme nom de réseau — même problème que documenté pour IDFM dans
# GTFS_NOM_RESEAU_FORCE ci-dessus, mais ici la combinaison de fichiers n'est
# pas connue à l'avance (upload libre), donc pas figeable dans un dict :
# champ optionnel plutôt qu'une nouvelle entrée GTFS_NOM_RESEAU_FORCE par
# combinaison.
nom_reseau_force_saisi = None
if nb_sources_gtfs > 1:
    nom_reseau_force_saisi = st.sidebar.text_input(
        "Nom du réseau fusionné (optionnel — sinon dérivé des agences)",
        placeholder="ex: Aix_Marseille",
    ).strip() or None

# Variables globales pour stocker les résultats. Uniquement celles
# effectivement lues ailleurs (views/*.py, charger_donnees_gtfs ci-dessous) :
# indicateurs_arrets/bus/tram/metro/trolley/ferry, total_vk_plage,
# modes_disponibles, last_date_str, active_service_ids,
# decoupage_reference_path_reseau, decoupage_agglo et chemin_logo étaient
# initialisées (et pour certaines calculées) sans jamais être lues nulle
# part — vestiges d'une fonctionnalité "indicateurs tronçons/arrêts" jamais
# branchée à une page (cf. src/indicateurs_troncons.py, script autonome non
# utilisé par l'app).
if "feed" not in st.session_state:
    st.session_state.feed = None
if "date_str" not in st.session_state:
    st.session_state.date_str = None
if "nom_reseau_str" not in st.session_state:
    st.session_state.nom_reseau_str = None
if "zip_path" not in st.session_state:
    st.session_state.zip_path = None
if "last_uploaded_name" not in st.session_state:
    st.session_state.last_uploaded_name = None


# Fonction pour charger les données. La date d'analyse (date_JOB) n'est
# pas choisie par l'utilisateur : elle est déterminée automatiquement à
# partir du GTFS (le dernier mardi ou jeudi de la plage de service fiable,
# toujours le même pour un GTFS donné — voir src/info_reseau.dates_service).
def charger_donnees_gtfs():
    # Une "source" = (nom, fonction de lecture des octets du zip). uploaded_files
    # (upload libre) et gtfs_locaux_choisis (catalogue disque/HF) sont combinables
    # (ex: un GTFS uploadé + un GTFS du catalogue) : concaténés en une seule
    # liste de sources plutôt que traités comme deux chemins exclusifs.
    sources = [(f.name, f.read) for f in uploaded_files]
    for nom in gtfs_locaux_choisis:
        chemin_gtfs_local = os.path.join(GTFS_DATA_DIR, nom)
        # recuperer_depuis_hf() ne fait rien si déjà présent en local (cas
        # gtfs_locaux_disque) : pas besoin de distinguer les deux sources ici.
        if not os.path.exists(chemin_gtfs_local):
            with st.spinner(f"Récupération de {nom} depuis Hugging Face..."):
                if not recuperer_depuis_hf(f"GTFS/{nom}", chemin_gtfs_local):
                    st.error(f"Impossible de récupérer {nom} depuis Hugging Face.")
                    return False
        sources.append((nom, lambda chemin=chemin_gtfs_local: open(chemin, "rb").read()))

    if not sources:
        return False

    fusion = len(sources) > 1
    # Nom stable et déterministe (ordre alphabétique, pas l'ordre de sélection
    # dans l'UI) pour que nouveau_fichier ci-dessous ne redéclenche pas un
    # rechargement à chaque rerun Streamlit juste parce que l'ordre affiché a
    # changé.
    noms_tries = sorted(nom for nom, _ in sources)
    nom_gtfs = "+".join(noms_tries) if fusion else noms_tries[0]

    # Ne recharger le GTFS que si la sélection a changé, pas à chaque interaction
    nouveau_fichier = nom_gtfs != st.session_state.last_uploaded_name

    if not nouveau_fichier and st.session_state.feed is not None:
        return True

    # Copie dans un/des fichier(s) temporaire(s) (le résultat final GTFS_PATH
    # est conservé pour toute la session : create_carte_arrets recharge le feed
    # depuis ce chemin pour tracer les lignes) plutôt que d'opérer directement
    # sur data/GTFS/<fichier> : entre autres, charger_gtfs() peut réécrire le
    # zip en place si calendar_dates.txt est vide (cf.
    # src/utils._retirer_table_vide_du_zip) — sur le fichier original de
    # data/GTFS, ça modifierait silencieusement la source versionnée sur le
    # dataset HF.
    chemins_temp = []
    for nom, lire_gtfs in sources:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
            tmp_file.write(lire_gtfs())
            chemins_temp.append(tmp_file.name)

    if fusion:
        with st.spinner(f"Fusion de {len(sources)} GTFS ({', '.join(noms_tries)})..."):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
                GTFS_PATH = tmp_file.name
            fusionner_gtfs(chemins_temp, GTFS_PATH)
        for chemin in chemins_temp:
            os.unlink(chemin)
    else:
        GTFS_PATH = chemins_temp[0]

    try:
        # Charger le GTFS
        with st.spinner("Chargement du fichier GTFS..."):
            feed = charger_gtfs(GTFS_PATH)

        # L'app ne sait traiter que des GTFS urbains (un GTFS national/régional
        # regroupant de nombreuses agences ferait exploser les temps de calcul
        # et n'a pas de sens pour les indicateurs arrêts/tronçons proposés ici)
        # — sauf exception nommée explicitement (cf. GTFS_NOM_RESEAU_FORCE),
        # jamais pour une fusion (nom_gtfs y est une concaténation de plusieurs
        # noms, jamais une clé du dict). Vérifiée sur le NOM DE FICHIER exact
        # (upload ou catalogue) : GTFS_NOM_RESEAU_FORCE n'est de toute façon
        # peuplé qu'à la main avec des noms de fichiers déjà connus/vérifiés
        # (IDFM, Aix-Marseille...), donc pas de risque à l'appliquer aussi au
        # premier upload d'un de ces fichiers précis.
        nb_agences = len(feed.agency)
        exception_valide = not fusion and nom_gtfs in GTFS_NOM_RESEAU_FORCE
        if nb_agences > 4 and not exception_valide:
            raise TropAgencesError(nb_agences)

        # Plage de service fiable et jour ouvré de base (dernier mardi/jeudi,
        # cf. src/info_reseau.dates_service)
        _, _, _, date_JOB = dates_service(feed)
        date_str = date_JOB

        # nom_reseau_str() (pas nom_reseau()) : sanitize les noms d'agences pour
        # un usage sûr dans un chemin de fichier (cf. chemins_reseau) — nom_reseau()
        # seul joint les agences par " / ", qui casse la construction des chemins
        # pour un GTFS multi-agences (ex: Valenciennes, OSError "non-existent
        # directory" car chaque "/" est lu comme un séparateur de répertoire).
        # Priorité : nom saisi dans nom_reseau_force_saisi (fusion, cf. sa
        # définition ci-dessus) > GTFS_NOM_RESEAU_FORCE (exceptions nommées,
        # ex: IDFM, où nom_reseau_str() produirait aussi un nom bien trop
        # long) > dérivé automatiquement des agences.
        if nom_reseau_force_saisi:
            reseau_str = nom_fichier_valide(nom_reseau_force_saisi)
        elif exception_valide:
            reseau_str = GTFS_NOM_RESEAU_FORCE[nom_gtfs]
        else:
            reseau_str = str(nom_reseau_str(feed))

        # Stocker dans session_state
        st.session_state.feed = feed
        st.session_state.date_str = date_str
        st.session_state.zip_path = GTFS_PATH
        st.session_state.nom_reseau_str = reseau_str
        st.session_state.last_uploaded_name = nom_gtfs

        # GTFS uploadé (pas choisi dans le catalogue existant) et jamais vu,
        # SEUL (pas une fusion — le zip fusionné n'a pas vocation à réapparaître
        # tel quel dans le catalogue) : renvoyé vers le dataset HF pour que les
        # prochains déploiements/visiteurs le retrouvent dans "...ou choisir un
        # GTFS déjà présent" sans avoir à le réuploader — même principe que les
        # caches dérivés (extrait OSM, matrice des temps de trajet, découpage
        # communal, cf. src/hf_cache.py et src/pipeline_donnees.py). Best-effort,
        # comme les autres appels à envoyer_vers_hf : n'empêche jamais le run
        # en cours.
        if not fusion and uploaded_files and nom_gtfs not in gtfs_locaux:
            if envoyer_vers_hf(GTFS_PATH, f"GTFS/{nom_gtfs}"):
                st.toast(f"✓ {nom_gtfs} envoyé vers Hugging Face (réutilisable aux prochains déploiements)")

        return True

    except TropAgencesError as e:
        st.error(f"⚠ Ce GTFS regroupe {e.args[0]} agences : ce que l'app ne peut pas gérer. Charger un GTFS urbain uniquement.")
        os.unlink(GTFS_PATH)
        st.stop()

    except Exception as e:
        st.error(f"Erreur lors du chargement : {e}")
        os.unlink(GTFS_PATH)
        return False


# Charger les données automatiquement si nécessaire. Conteneur à clé stable
# (.st-key-overlay_chargement_gtfs, cf. CSS plus haut) : les st.spinner()
# appelés à l'intérieur de charger_donnees_gtfs() (récupération HF, fusion,
# parsing GTFS) héritent de ce conteneur comme point d'insertion — la règle
# CSS transforme leur rendu par défaut (petit texte + icône) en overlay
# plein écran le temps du chargement, sans toucher aux spinners du reste de
# l'app (calcul BPE, cartes...).
with st.container(key="overlay_chargement_gtfs"):
    charger_donnees_gtfs()

# Bouton vers l'app GTFS_analyse_fr, une fois un GTFS chargé (sidebar, pas
# l'onglet Accessibilité : ce lien est utile quelle que soit la page visitée).
#
# last_uploaded_name est le nom de fichier GTFS exact (catalogue partagé sur
# le dataset HF antoinechevre/accessibility-data, cf. src/hf_cache.py) — sauf
# en cas de fusion de plusieurs GTFS, où il concatène leurs noms avec "+" et
# ne correspond donc à aucun fichier réel : dans ce cas on ne peut pas
# présélectionner de GTFS côté GTFS_analyse_fr.
if st.session_state.last_uploaded_name:
    if "+" not in st.session_state.last_uploaded_name:
        gtfs_analyse_url = f"{GTFS_ANALYSE_URL}?{urllib.parse.urlencode({'gtfs': st.session_state.last_uploaded_name})}"
    else:
        gtfs_analyse_url = GTFS_ANALYSE_URL
    # Séparateur visible entre le choix du GTFS (uploader/dropdown ci-dessus)
    # et ce lien vers une autre appli, pour bien distinguer les deux blocs.
    st.sidebar.divider()
    # Couleur distincte (bleu clair) pour ce lien précis : st.link_button ne
    # propose pas de paramètre de couleur, la clé du conteneur (classe CSS
    # st-key-... générée par Streamlit) permet de cibler uniquement ce bouton
    # sans affecter les autres boutons de l'app.
    st.sidebar.markdown(
        """
        <style>
        .st-key-lien_gtfs_analyse a {
            background-color: #ADD8E6 !important;
            border-color: #ADD8E6 !important;
            color: #000000 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.sidebar.container(key="lien_gtfs_analyse"):
        # icon="material/open_in_new" : pictogramme "lien externe" standard,
        # signale que ce bouton quitte l'appli vers GTFS_analyse_fr.
        st.link_button(
            "Pour analyser le réseau à partir du GTFS",
            gtfs_analyse_url,
            icon=":material/open_in_new:",
        )

# Navigation entre les pages
if st.session_state.selected_page == "Accueil":
    home_page()
elif st.session_state.selected_page == "Accessibilité":
    accessibilite_index_page()
elif st.session_state.selected_page == "Pondération équipements":
    ponderation_equipements_page()
elif st.session_state.selected_page == "Cartographie INSEE":
    cartographie_insee_page()
elif st.session_state.selected_page == "Benchmark Villes Françaises":
    benchmark_reseaux_page()
