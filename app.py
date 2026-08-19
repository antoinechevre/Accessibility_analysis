"""
Application d'analyse de l'accessibilité piétons / transports collectifs à 30 min des équipements - Interface principale

"""

import os
import sys
import tempfile

sys.path.append('..')

import requests
import streamlit as st

from src.utils import charger_gtfs, obtenir_service_ids_pour_date
from src.info_reseau import dates_service, nom_fichier_valide, nom_reseau_str, recuperer_logo_reseau
from src.hf_cache import enregistrer_visite, envoyer_vers_hf, lister_fichiers_hf, recuperer_depuis_hf
from src.merge_gtfs import fusionner_gtfs
from src.transport_data_gouv import (
    associer_gtfs_a_pan,
    charger_provenance,
    enregistrer_provenance,
    enregistrer_zone,
    nb_agences_gtfs,
    rechercher_gtfs_urbain,
    recuperer_datasets_public_transit,
    statut_resultat,
    telecharger_gtfs,
)
from src.vacances_scolaires import departement_academie_zone_pour_feed
from views.home import explications_analyse_gtfs, home_page
from views.accessibilite_index import accessibilite_index_page
from views.ponderation_equipements import ponderation_equipements_page
from views.cartographie_insee import cartographie_insee_page
from views.benchmark_reseaux import benchmark_reseaux_page
from views.arrets import arrets_page
from views.troncons import troncons_page


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

# Enregistre une visite (un marqueur par nouvelle session, cf.
# src.hf_cache.enregistrer_visite) pour la notification quotidienne
# (.github/workflows/notifier_visites.yml) — dans un thread à part pour ne
# pas retarder le premier rendu de la page avec un appel réseau best-effort.
if "visite_enregistree" not in st.session_state:
    st.session_state.visite_enregistree = True
    import threading
    threading.Thread(target=enregistrer_visite, daemon=True).start()

# Titre principal
st.title(
    "Application analyse accessibilité urbaine transports collectifs/piétons "
    "et analyse réseau transports collectifs - France Métropolitaine"
)


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
/* Réserve en permanence la place de la scrollbar verticale, affichée ou
   non : sur Windows/Chrome (scrollbar "classique", qui réduit la largeur
   du contenu quand elle apparaît — contrairement à macOS où elle flotte en
   overlay sans impact sur la largeur), une page dont la hauteur oscille
   tout juste autour du seuil d'apparition de la scrollbar entre dans une
   boucle : scrollbar apparaît -> largeur du contenu réduite -> les cartes
   (st.components.v1.html sans width= fixe, cf. cartographie.py) se
   redimensionnent en response -> repasse sous le seuil -> scrollbar
   disparaît -> largeur réaugmente -> etc. Observé sur PC Windows (jamais
   sur Mac) : les cartes "tremblent" en continu sans jamais se stabiliser,
   la scrollbar-gutter figée coupe cette boucle à la racine. */
html {
    scrollbar-gutter: stable;
}
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
   légèrement en retrait pour se lire comme un sous-menu, pas un pair.
   Paddings harmonisés entre les deux niveaux (repris du niveau 2, cf.
   min-height 44px ci-dessous) : la hiérarchie se lit désormais via le
   fill (niveau 1) / outline (niveau 2) de l'élément actif plutôt que via
   des dimensions de bouton différentes. */
.st-key-nav_niveau1 [data-testid="stButtonGroup"] button,
.st-key-nav_niveau2 [data-testid="stButtonGroup"] button {
    /* min-height 44px (recommandation Apple HIG pour une cible tactile) :
       un padding trop réduit rendrait ces boutons difficiles à toucher
       précisément sur iPad — sans équivalent en souris (le curseur reste
       pixel-précis), d'où des soucis de navigation constatés uniquement
       sur iPad Safari. */
    padding: .65rem 1rem;
    min-height: 44px;
}
.st-key-nav_niveau1 [data-testid="stButtonGroup"] button {
    font-size: 1rem;
    font-weight: 700;
}
/* Élément actif du niveau 1 en aplat plein (fill) : accent teal opaque,
   texte clair — l'option la plus visible de la nav. */
.st-key-nav_niveau1 [data-testid="stButtonGroup"] button[aria-checked="true"] {
    background: #0E7C7B !important;
    border-color: #0E7C7B !important;
}
.st-key-nav_niveau1 [data-testid="stButtonGroup"] button[aria-checked="true"] p {
    color: #FAFAF8 !important;
}
.st-key-nav_niveau2 {
    background: rgba(14, 124, 123, 0.07);
    border-radius: 10px;
    padding: .5rem .6rem .35rem;
    margin-top: -.3rem;
}
.st-key-nav_niveau2 [data-testid="stButtonGroup"] button {
    font-size: .85rem;
    font-weight: 500;
}
/* Élément actif du niveau 2 en contour seul (outline) : fond transparent,
   juste une bordure teal — plus discret que le fill du niveau 1. */
.st-key-nav_niveau2 [data-testid="stButtonGroup"] button[aria-checked="true"] {
    background: transparent !important;
    border: 1.5px solid #0E7C7B !important;
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
    align-items: flex-start;
    justify-content: center;
    padding-top: 3rem;
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
    "Analyse réseau (GTFS)": ["Arrêts", "Tronçons", "Explications GTFS"],
    "Benchmark": ["Benchmark Villes Françaises"],
}
LIBELLES_GROUPE = {
    "Accueil": "🏠 Accueil",
    "Analyse du réseau": "📈 Analyse accessibilité urbaine",
    "Analyse réseau (GTFS)": "🚏 Analyse réseau",
    "Benchmark": "📊 Benchmark villes françaises",
}
LIBELLES_PAGE = {
    "Accessibilité": "📍 Accessibilité",
    "Pondération équipements": "⚖️ Localisation et pondération équipements",
    "Cartographie INSEE": "🗺️ Carte population par déciles",
    "Arrêts": "📍 Arrêts",
    "Tronçons": "🛤️ Lignes",
    "Explications GTFS": "📖 Explications",
}
GROUPE_DE_LA_PAGE = {page: groupe for groupe, pages in GROUPES_NAV.items() for page in pages}

# Sous-menus désactivés en bloc tant qu'aucun GTFS n'est chargé : uniquement
# "Analyse du réseau", dont les 3 pages en dépendent toutes sans repli
# possible. "Analyse réseau (GTFS)" reste actif même sans GTFS : Arrêts/
# Tronçons affichent déjà un message d'invite gracieux dans ce cas (cf.
# views/arrets.py, views/troncons.py), et Explications GTFS est purement
# informatif, utile à consulter avant même de charger un fichier.
GROUPES_DESACTIVES_SANS_GTFS = {"Analyse du réseau"}

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
            disabled=groupe_choisi in GROUPES_DESACTIVES_SANS_GTFS and st.session_state.get("feed") is None,
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

# Ajoute best-effort un fichier tout juste (télé)chargé via la recherche
# transport.data.gouv.fr (cf. plus bas) à la sélection courante : DOIT
# s'exécuter avant la création du widget key="gtfs_locaux_choisis"
# ci-dessous — Streamlit interdit de modifier st.session_state[key] après
# l'instanciation du widget portant cette clé dans le même run (d'où ce
# passage par une clé de "staging" séparée plutôt qu'une écriture directe
# au moment du clic, plus bas dans le script).
gtfs_a_ajouter_auto = st.session_state.pop("_gtfs_a_selectionner", None)
if gtfs_a_ajouter_auto:
    selection_actuelle = st.session_state.get("gtfs_locaux_choisis", [])
    if gtfs_a_ajouter_auto not in selection_actuelle:
        st.session_state["gtfs_locaux_choisis"] = [*selection_actuelle, gtfs_a_ajouter_auto]

gtfs_locaux_choisis = st.sidebar.multiselect(
    "...ou choisir un/des GTFS déjà présent(s) — plusieurs = réseaux fusionnés",
    options=gtfs_locaux,
    key="gtfs_locaux_choisis",
)

nb_sources_gtfs = len(uploaded_files) + len(gtfs_locaux_choisis)


# st.cache_data : la liste complète (775 datasets mi-2026) ne change pas
# d'un appel à l'autre dans la même session — évite de la retélécharger à
# chaque frappe dans la recherche/chaque nouvel upload (cf. usages plus
# bas : recherche en barre latérale ET association automatique d'un
# nouvel upload).
@st.cache_data(ttl=3600, show_spinner="Récupération du catalogue transport.data.gouv.fr...")
def _datasets_transport_gouv():
    return recuperer_datasets_public_transit()


# --- Recherche d'un GTFS sur transport.data.gouv.fr (PAN) -------------------
# Alternative à l'upload manuel : chercher directement le jeu de données
# source par nom de ville, vérifier s'il est déjà dans le catalogue (et à
# jour) avant de le retélécharger, et l'ajouter au catalogue HF en un clic.
with st.sidebar.expander("🔍 Rechercher un GTFS (transport.data.gouv.fr)"):
    nom_ville_recherche = st.text_input("Nom de ville", key="recherche_gtfs_ville", placeholder="ex: Nice")
    if nom_ville_recherche.strip():
        try:
            resultats_recherche = rechercher_gtfs_urbain(nom_ville_recherche, datasets=_datasets_transport_gouv())
        except requests.RequestException as e:
            resultats_recherche = None
            st.error(f"transport.data.gouv.fr injoignable : {e}")

        if resultats_recherche is not None:
            if not resultats_recherche:
                st.info("Aucun GTFS urbain trouvé pour cette ville.")
            provenance_gtfs = charger_provenance()
            # Nom standard "<Ville recherchée>_GTFS.zip" plutôt que dérivé du
            # titre du dataset (souvent le nom de l'opérateur, ex: "Kicéo"
            # pour Vannes — invisible en cherchant "Vannes" dans la liste
            # déjà présents ensuite). Basé sur la recherche elle-même (pas
            # une "ville principale" recalculée depuis le GTFS, coûteux) :
            # l'utilisateur la retrouve sous le nom qu'il a tapé.
            nom_fichier_standard = nom_fichier_valide(nom_ville_recherche.strip().capitalize()) + "_GTFS.zip"

            for resultat in resultats_recherche:
                statut, nom_fichier_existant = statut_resultat(resultat, provenance_gtfs)
                nom_fichier_cible = nom_fichier_existant or nom_fichier_standard
                st.markdown(f"**{resultat['title']}**")
                st.caption(f"{resultat['covered_area_noms']} — màj {(resultat['ressource_maj'] or '?')[:10]}")

                if statut == "a_jour":
                    st.success(f"✓ Déjà à jour dans le catalogue ({nom_fichier_existant})")
                    deja_selectionne = nom_fichier_existant in st.session_state.get("gtfs_locaux_choisis", [])
                    if not deja_selectionne and st.button("Sélectionner", key=f"sel_{resultat['ressource_url']}"):
                        st.session_state["_gtfs_a_selectionner"] = nom_fichier_existant
                        st.rerun()
                    continue

                if statut == "maj_disponible":
                    st.warning(f"⚠ Mise à jour disponible (catalogue actuel : {nom_fichier_existant})")

                if st.button("Télécharger", key=f"dl_{resultat['ressource_url']}"):
                    with st.spinner(f"Téléchargement de {resultat['title']}..."):
                        try:
                            contenu_gtfs = telecharger_gtfs(resultat)
                            nb_agences = nb_agences_gtfs(contenu_gtfs)
                        except requests.RequestException as e:
                            st.error(f"Échec du téléchargement : {e}")
                            nb_agences = None

                        if nb_agences is not None and nb_agences > 4:
                            st.error(
                                f"⚠ Ce GTFS regroupe {nb_agences} agences : ce que l'app ne peut pas "
                                "gérer. Cherche un jeu de données plus spécifique (ex: le nom de "
                                "l'opérateur urbain) plutôt que celui-ci, qui couvre toute une région."
                            )
                        elif nb_agences is not None:
                            try:
                                chemin_cible = os.path.join(GTFS_DATA_DIR, nom_fichier_cible)
                                os.makedirs(GTFS_DATA_DIR, exist_ok=True)
                                with open(chemin_cible, "wb") as f:
                                    f.write(contenu_gtfs)
                                envoyer_vers_hf(chemin_cible, f"GTFS/{nom_fichier_cible}")
                                enregistrer_provenance(nom_fichier_cible, resultat)
                            except requests.RequestException as e:
                                st.error(f"Échec du téléchargement : {e}")
                            else:
                                st.session_state["_gtfs_a_selectionner"] = nom_fichier_cible
                                st.success(f"✓ {nom_fichier_cible} ajouté au catalogue et sélectionné.")
                                st.rerun()
                st.divider()

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

# Variables globales pour stocker les résultats.
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
if "academie_reseau" not in st.session_state:
    st.session_state.academie_reseau = None
if "zip_path" not in st.session_state:
    st.session_state.zip_path = None
if "last_uploaded_name" not in st.session_state:
    st.session_state.last_uploaded_name = None
# Ci-dessous : utilisées par l'onglet "Analyse réseau (GTFS)"
# (views/arrets.py, views/troncons.py, importés du projet sœur
# GTFS_analysis_fr — cf. src/indicateurs_troncons.py).
if "active_service_ids" not in st.session_state:
    st.session_state.active_service_ids = None
if "chemin_logo" not in st.session_state:
    st.session_state.chemin_logo = None
if "indicateurs_arrets" not in st.session_state:
    st.session_state.indicateurs_arrets = None
if "indicateurs_par_mode" not in st.session_state:
    st.session_state.indicateurs_par_mode = None
if "total_vk_plage" not in st.session_state:
    st.session_state.total_vk_plage = None


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
    with st.spinner("Chargement du GTFS..."):
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
        # Un seul st.spinner englobant (plutôt que plusieurs, un par étape) :
        # l'overlay plein écran (CSS plus haut) ne couvre que le temps où un
        # spinner de ce conteneur est actif — dates_service/logo n'avaient
        # sinon aucun spinner dédié, laissant la page réapparaître un instant
        # entre le parsing du GTFS et la fin du chargement.
        with st.spinner("Chargement du GTFS..."):
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

            # Académie/zone de vacances scolaires du réseau : sert à écarter
            # les vacances scolaires du choix de date_JOB ci-dessous. Un GTFS
            # déjà indexé (upload ou catalogue précédent) réutilise
            # l'académie déjà enregistrée dans gtfs_sources.json plutôt que
            # de re-géocoder à chaque chargement — et surtout, si une
            # tentative précédente avait échoué (académie absente de
            # l'index), retente ici (reverse géocodage sur le barycentre des
            # arrêts, cf. src/vacances_scolaires.py, avec plusieurs
            # tentatives) et complète l'index plus bas dès que ça réussit :
            # pas de fusion (nom_gtfs y est une concaténation, jamais une clé
            # de l'index). Best-effort : ne doit jamais bloquer le
            # chargement (feed hors métropole déjà rejeté plus loin par
            # HorsMetropoleError le cas échéant, API géocodage
            # indisponible...).
            provenance_connue = {} if fusion else charger_provenance().get(nom_gtfs, {})
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

            # Plage de service fiable et jour ouvré de base (dernier mardi/jeudi
            # hors vacances scolaires de l'académie si connue, cf.
            # src/info_reseau.dates_service)
            _, date_debut, date_fin, date_JOB = dates_service(feed, academie=academie)
            date_str = date_JOB

            # Services actifs à cette date (utilisé par l'onglet "Analyse réseau
            # (GTFS)" — views/arrets.py, views/troncons.py)
            active_service_ids = obtenir_service_ids_pour_date(feed, date_str)

            # Logo du réseau (best-effort : nécessite une requête réseau vers le
            # site de l'agence, ne doit jamais bloquer le chargement en cas
            # d'échec) — utilisé par les cartes de l'onglet "Analyse réseau (GTFS)".
            try:
                chemin_logo = recuperer_logo_reseau(feed, dossier_sortie=tempfile.gettempdir())
            except Exception:
                chemin_logo = None

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
        st.session_state.date_debut = date_debut
        st.session_state.date_fin = date_fin
        st.session_state.zip_path = GTFS_PATH
        st.session_state.nom_reseau_str = reseau_str
        st.session_state.academie_reseau = academie
        st.session_state.last_uploaded_name = nom_gtfs
        st.session_state.active_service_ids = active_service_ids
        st.session_state.chemin_logo = chemin_logo
        # Nouveau réseau : réinitialise les indicateurs Arrêts/Tronçons de
        # l'ancien plutôt que de les laisser affichés à tort le temps du
        # recalcul (cf. views/arrets.py, views/troncons.py).
        st.session_state.indicateurs_arrets = None
        st.session_state.indicateurs_par_mode = None
        st.session_state.total_vk_plage = None

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

        # Académie/zone dans l'index gtfs_sources.json (cf. plus haut) : comme
        # pour l'envoi HF ci-dessus, pas de sens pour une fusion (le nom
        # composite "A+B" ne correspond à aucun GTFS réutilisable tel quel).
        # Seulement si fraîchement résolue ici (pas déjà lue depuis l'index) :
        # sinon, chaque chargement d'un réseau déjà connu réécrirait pour
        # rien la même valeur sur le dataset HF.
        if not fusion and academie_fraichement_resolue:
            try:
                enregistrer_zone(nom_gtfs, code_departement, academie, zone_vacances)
            except Exception:
                pass

        # Association automatique au jeu de données transport.data.gouv.fr
        # (cf. src.transport_data_gouv.associer_gtfs_a_pan), pour qu'un
        # nouvel upload profite du contrôle de fraîcheur hebdomadaire
        # (scripts/rafraichir_gtfs.py) sans devoir passer par la recherche
        # en barre latérale à la main. Best-effort, jamais bloquant — et
        # jamais sur le seul nom de fichier (cf. la comparaison agency.txt
        # dans associer_gtfs_a_pan) : une précédente version, basée sur le
        # nom de réseau dérivé des agences, avait associé à tort un GTFS à
        # un opérateur sans rapport (recherche par un nom d'agence qui, par
        # coïncidence, ne matchait qu'un seul jeu de données PAN erroné).
        if not fusion and uploaded_files and nom_gtfs not in gtfs_locaux:
            try:
                if not charger_provenance().get(nom_gtfs, {}).get("page_url"):
                    with open(GTFS_PATH, "rb") as f:
                        contenu_local = f.read()
                    resultat, _ = associer_gtfs_a_pan(nom_gtfs, contenu_local, _datasets_transport_gouv())
                    if resultat is not None:
                        enregistrer_provenance(nom_gtfs, resultat)
            except Exception:
                pass

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

# Navigation entre les pages
if st.session_state.selected_page == "Accueil":
    home_page()
elif st.session_state.selected_page == "Accessibilité":
    accessibilite_index_page()
elif st.session_state.selected_page == "Pondération équipements":
    ponderation_equipements_page()
elif st.session_state.selected_page == "Cartographie INSEE":
    cartographie_insee_page()
elif st.session_state.selected_page == "Arrêts":
    arrets_page()
elif st.session_state.selected_page == "Tronçons":
    troncons_page()
elif st.session_state.selected_page == "Explications GTFS":
    st.markdown("---")
    explications_analyse_gtfs()
elif st.session_state.selected_page == "Benchmark Villes Françaises":
    benchmark_reseaux_page()
