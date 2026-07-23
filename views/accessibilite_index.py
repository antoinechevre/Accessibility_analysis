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
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
from folium.plugins import DualMap

from src.BPE_traitement import land_use_data_domaine
from src.build_data_agglo import osm_pbf_creator, ville_principale
from src.cartographie import echelle_continue_html, script_reajuster_si_masque, titre_carte_html
from src.hf_cache import envoyer_vers_hf, fusionner_et_envoyer_csv, recuperer_depuis_hf
from src.pipeline_donnees import MEMORY_CSV_AGGLO_DIR, DOMAINES_BPE, chemins_reseau, construire_donnees_bpe
from src.utilitaires_matrix import (
    calculer_index_benchmark,
    cost_to_closest,
    cumulative_cutoff,
    deciles_niveau_vie,
    moyenne_ponderee_pct_poles,
    pct_poles_atteignables_par_carreau,
)
from src.utils import km_par_ligne_jour, longueur_lignes, preparer_gtfs_pour_r5py

BASE_DIR = os.getcwd()
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

FONDS_CARTE = {
    "OpenStreetMap": "OpenStreetMap",
    "CartoDB Positron": "CartoDB positron",
    "CartoDB Dark Matter": "CartoDB dark_matter",
}

# Durées utilisées pour les courbes "% moyen de pôles atteignables" (vue
# d'ensemble par domaine + déclinaison par décile de niveau de vie).
CUTOFFS_PCT_MOYEN_POLES = [15, 30, 45, 60]

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
            nom_pbf_hf = f"memory_pbf/agglo_osm_pbf_{nom_reseau_str}.osm.pbf"
            if recuperer_depuis_hf(nom_pbf_hf, osm_pbf_path):
                st.write("✓ Extrait OSM récupéré depuis le cache Hugging Face")
            else:
                st.write("Extraction des données OSM (Overpass)... peut prendre plusieurs minutes")
                osm_pbf_creator(chemins["decoupage_geojson"], output_pbf_path=osm_pbf_path)
                st.write("✓ Extrait OSM prêt")
                st.write("Envoi de l'extrait OSM vers le cache Hugging Face...")
                if envoyer_vers_hf(osm_pbf_path, nom_pbf_hf):
                    st.write("✓ Extrait OSM envoyé vers Hugging Face (réutilisable aux prochains déploiements)")
                else:
                    st.write("⚠ Envoi vers Hugging Face échoué (pas bloquant, disponible seulement sur ce Space)")

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
            st.write("Envoi de la matrice des temps de trajet vers le cache Hugging Face...")
            nom_ttm_hf = f"memory_ttm/ttm_{nom_reseau_str}.parquet"
            if envoyer_vers_hf(ttm_path, nom_ttm_hf):
                st.write("✓ Matrice envoyée vers Hugging Face (réutilisable aux prochains déploiements)")
            else:
                st.write("⚠ Envoi vers Hugging Face échoué (pas bloquant, disponible seulement sur ce Space)")

        ttm = pd.read_parquet(ttm_path)
        status.update(label="Données d'accessibilité prêtes", state="complete", expanded=False)

    return population_grid_agglo, land_use_data, BPE_agglo, ttm


def _carte_temps_acces_pole_domaine(
    population_grid_agglo, land_use_data, BPE_agglo, ttm, domaine, fond_carte, carreaux_filtre_ids=None
):
    """Carte HTML interactive du temps d'accès au pôle d'équipements le plus
    proche pour un domaine BPE donné (cost_to_closest, restreint aux carreaux
    "pôles" pole_equipements_{domaine}). Équivalent de la section 9.1 du
    notebook.

    carreaux_filtre_ids: si fourni, restreint les carreaux AFFICHÉS à cet
    ensemble d'id (ex: filtre par décile de niveau de vie) — n'affecte pas
    la détection des pôles ni le calcul des temps de trajet (basés sur tous
    les carreaux), seulement ce qui est montré sur la carte. Retourne None
    si le filtre ne laisse aucun carreau.
    """
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
    if carreaux_filtre_ids is not None:
        carte_temps = carte_temps[carte_temps["id"].isin(carreaux_filtre_ids)]
    if carte_temps.empty:
        return None

    # Comme TMISA dans le livre (cf. notebook 9.1) : plafonné à 60 min pour la
    # lisibilité de la carte, au-delà seule l'idée de "trop loin" compte.
    carte_temps["travel_time_plafonne"] = carte_temps["travel_time"].clip(upper=60)

    carte = carte_temps.explore(
        column="travel_time_plafonne",
        cmap="cividis_r",
        tiles=FONDS_CARTE[fond_carte],
        legend=False,  # légende maison ci-dessous (cf. echelle_continue_html)
        style_kwds={"weight": 0, "opacity": 0},
    )

    carte.get_root().html.add_child(
        folium.Element(titre_carte_html(f"Temps d'accès au pôle d'équipements le plus proche – {nom_domaine}"))
    )
    carte.get_root().html.add_child(
        folium.Element(echelle_continue_html(
            carte_temps["travel_time_plafonne"].min(),
            carte_temps["travel_time_plafonne"].max(),
            "cividis_r",
            "Temps (min)",
            cote="centre",
        ))
    )

    minx, miny, maxx, maxy = carte_temps.to_crs(epsg=4326).total_bounds
    carte.get_root().html.add_child(
        folium.Element(script_reajuster_si_masque(carte, [[miny, minx], [maxy, maxx]]))
    )

    return carte


def _carte_poles_accessibles_domaine(population_grid_agglo, land_use_data, ttm, domaine, fond_carte, carreaux_filtre_ids=None):
    """Carte HTML interactive (DualMap 30 min / 45 min) du nombre de pôles
    d'équipements accessibles pour un domaine BPE donné, restreint aux
    carreaux "pôles" pole_equipements_{domaine}. Équivalent de la section
    9.2 du notebook.

    carreaux_filtre_ids: si fourni, restreint les carreaux AFFICHÉS à cet
    ensemble d'id (ex: filtre par décile de niveau de vie) — la légende
    (limite_commune) reste calculée sur tous les carreaux, pour une échelle
    de couleur comparable quel que soit le filtre. Retourne None si le
    filtre ne laisse aucun carreau.
    """
    nom_domaine = DOMAINES_BPE.get(domaine, domaine)

    poles_domaine = land_use_data[["id", f"pole_equipements_{domaine}"]].rename(
        columns={f"pole_equipements_{domaine}": domaine}
    )

    cum_30 = cumulative_cutoff(ttm, land_use_data=poles_domaine, opportunity=domaine, travel_cost="travel_time", cutoff=30)
    cum_45 = cumulative_cutoff(ttm, land_use_data=poles_domaine, opportunity=domaine, travel_cost="travel_time", cutoff=45)

    limite_commune = max(cum_30[domaine].max(), cum_45[domaine].max())

    carte_30 = population_grid_agglo[["id", "geometry"]].merge(cum_30, on="id")
    carte_45 = population_grid_agglo[["id", "geometry"]].merge(cum_45, on="id")
    if carreaux_filtre_ids is not None:
        carte_30 = carte_30[carte_30["id"].isin(carreaux_filtre_ids)]
        carte_45 = carte_45[carte_45["id"].isin(carreaux_filtre_ids)]
    if carte_30.empty or carte_45.empty:
        return None

    dual_map = DualMap(tiles=FONDS_CARTE[fond_carte], layout="horizontal")
    # legend=False sur les deux : branca cible tous les colorbars de la page
    # via un sélecteur CSS non isolé par carte (d3.select(".legend.leaflet-control"),
    # premier match seulement) — le second colorbar s'empile dans le premier
    # au lieu de s'afficher sur son propre panneau. Légendes maison à la
    # place (cf. echelle_continue_html ci-dessous), une par côté.
    carte_30.explore(
        column=domaine,
        cmap="inferno",
        vmin=0,
        vmax=limite_commune,
        legend=False,
        style_kwds={"weight": 0, "opacity": 0},
        m=dual_map.m1,
    )
    carte_45.explore(
        column=domaine,
        cmap="inferno",
        vmin=0,
        vmax=limite_commune,
        legend=False,
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
    dual_map.get_root().html.add_child(
        folium.Element(echelle_continue_html(0, limite_commune, "inferno", f"{nom_domaine} (pôles) – 30 min", cote="gauche"))
    )
    dual_map.get_root().html.add_child(
        folium.Element(echelle_continue_html(0, limite_commune, "inferno", f"{nom_domaine} (pôles) – 45 min", cote="droite"))
    )
    dual_map.get_root().html.add_child(folium.Element(script_reajuster_si_masque(dual_map.m1, bounds)))
    dual_map.get_root().html.add_child(folium.Element(script_reajuster_si_masque(dual_map.m2, bounds)))

    return dual_map


def _courbe_pct_moyen_poles(tableau, titre, titre_legende, cmap=None):
    """Figure matplotlib : une courbe par colonne de tableau.T (durées en
    index après transposition), affichée via st.pyplot. Partagée par la vue
    d'ensemble par domaine et la déclinaison par décile de niveau de vie
    (même tableau Domaine/Décile x durée, juste la source qui change)."""
    fig, ax = plt.subplots(figsize=(8, 5))
    tableau.T.plot(marker="o", ax=ax, cmap=cmap)
    ax.set_xlabel("Durée de trajet (min)")
    ax.set_ylabel("% moyen de pôles atteignables")
    ax.set_ylim(0, 100)
    ax.set_title(titre)
    ax.legend(title=titre_legende, bbox_to_anchor=(1.02, 1), loc="upper left")
    st.pyplot(fig)
    plt.close(fig)


def accessibilite_index_page():
    st.header("Accessibilité aux équipements (30 min / 45 min)")
    st.caption("🚌 Bus · 🚊 Tramway · 🚇 Métro · ⛴️ Ferry · 🚶 Piétons")

    if st.session_state.get("feed") is None:
        st.info("👆 Veuillez charger un fichier GTFS dans la barre latérale.")
        return

    nom_reseau_str = st.session_state.nom_reseau_str
    date_str = st.session_state.date_str
    zip_path = st.session_state.zip_path

    # Ville principale : seulement si le découpage communal de ce réseau est
    # déjà disponible quelque part sur disque — pas de géocodage à la volée
    # ici, ce n'est qu'un affichage. Deux sources possibles : le chemin de
    # travail (calculé lors d'un run précédent DANS CETTE SESSION) ou, à
    # défaut, le cache mémoire par réseau (memory_csv_agglo, committé sur git
    # donc déjà présent même sur un déploiement tout juste démarré, cf.
    # src/pipeline_donnees.py) — sans ce repli, un réseau déjà connu (ex.
    # IRIGO) n'affichait sa ville qu'après avoir cliqué "Lancer l'analyse"
    # au moins une fois dans la session en cours.
    # st.cache_data évite de rappeler l'API geo.api.gouv.fr (ville_principale)
    # à chaque interaction pour un même réseau déjà résolu.
    @st.cache_data(show_spinner=False)
    def _ville_principale_affichage(chemin_decoupage):
        codes_insee = pd.read_csv(chemin_decoupage, dtype={"code_insee": str})["code_insee"]
        return ville_principale(codes_insee)

    chemin_decoupage_travail = chemins_reseau(nom_reseau_str)["decoupage_csv"]
    chemin_decoupage_memoire = os.path.join(MEMORY_CSV_AGGLO_DIR, f"decoupage_agglo_{nom_reseau_str}.csv")
    if os.path.exists(chemin_decoupage_travail):
        chemin_decoupage_cache = chemin_decoupage_travail
    elif os.path.exists(chemin_decoupage_memoire):
        chemin_decoupage_cache = chemin_decoupage_memoire
    else:
        chemin_decoupage_cache = None
    ville_reseau = _ville_principale_affichage(chemin_decoupage_cache) if chemin_decoupage_cache else None

    date_affichage = datetime.datetime.strptime(date_str, "%Y%m%d").strftime("%d/%m/%Y")
    reseau_affichage = f"{nom_reseau_str} ({ville_reseau})" if ville_reseau else nom_reseau_str
    st.write(f"Réseau : **{reseau_affichage}** — jour de référence : {date_affichage}")

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
    if "benchmark_a_enregistrer" not in st.session_state:
        st.session_state.benchmark_a_enregistrer = False
    if "analyse_detaillee" not in st.session_state:
        st.session_state.analyse_detaillee = False

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
        # Enregistrement dans l'index de benchmark déclenché automatiquement à
        # la fin de ce run (cf. bouton "Enregistrer les indicateurs" plus bas) :
        # un run "oublié" (bouton jamais cliqué) n'apparaissait sinon jamais
        # dans le graphique de benchmark, piège rencontré plusieurs fois.
        st.session_state.benchmark_a_enregistrer = True
        # Nouveau run : repart sur la vue rapide (domaine "Tout équipements
        # pondérés" seul, déciles fusionnés) plutôt que de garder le mode
        # détaillé d'un run précédent — cf. analyse_detaillee ci-dessous.
        st.session_state.analyse_detaillee = False

    if "pipeline_data" not in st.session_state or st.session_state.reseau_calcule != nom_reseau_str:
        st.info("Cliquez sur le bouton ci-dessus pour lancer l'analyse.")
        return

    population_grid_agglo, land_use_data, BPE_agglo, ttm = st.session_state.pipeline_data

    st.success(f"✓ {len(population_grid_agglo)} carreaux actifs — matrice des temps de trajet prête.")

    # Vue rapide par défaut (juste après "Lancer l'analyse") : un seul domaine
    # ("O" - Tout équipements pondérés) et les déciles fusionnés, pour ne pas
    # payer d'entrée de jeu le coût des 8 domaines x cartes + déclinaison par
    # décile. L'analyse complète (tous domaines, filtre par décile) n'est
    # calculée que si l'utilisateur la demande explicitement (bouton plus bas)
    # — cf. analyse_detaillee, remis à False à chaque nouveau run.
    domaines_a_afficher = DOMAINES_BPE if st.session_state.analyse_detaillee else {"O": DOMAINES_BPE["O"]}

    with st.spinner("Calcul de la vue d'ensemble..."):
        # Calculé une seule fois par (domaine, durée), réutilisé ci-dessous pour
        # la vue d'ensemble et pour la déclinaison par décile dans chaque onglet
        # (évite de refiltrer ttm à chaque décile) — seulement pour les domaines
        # affichés (domaines_a_afficher), pas les 8 systématiquement.
        pct_par_carreau_domaine_cutoff = {
            (d, c): pct_poles_atteignables_par_carreau(land_use_data, ttm, d, c)
            for d in domaines_a_afficher
            for c in CUTOFFS_PCT_MOYEN_POLES
        }

        tableau_pct_moyen_poles = pd.DataFrame(
            [
                {
                    "Domaine": nom,
                    **{
                        f"{c} min": moyenne_ponderee_pct_poles(pct_par_carreau_domaine_cutoff[(d, c)])
                        for c in CUTOFFS_PCT_MOYEN_POLES
                    },
                }
                for d, nom in domaines_a_afficher.items()
            ]
        ).set_index("Domaine")

        niveau_vie = deciles_niveau_vie(population_grid_agglo)

    fond_carte = st.selectbox(
        "Fond de carte", options=list(FONDS_CARTE.keys()), index=list(FONDS_CARTE.keys()).index("CartoDB Positron")
    )

    if not st.session_state.analyse_detaillee:
        st.info(
            "Vue rapide : domaine \"Tout équipements pondérés\" uniquement, déciles de niveau de "
            "vie fusionnés — pour ne pas attendre le calcul des 8 domaines à chaque run."
        )
        if st.button(
            "📊 Voulez-vous analyser avec les déciles de niveau de revenu et les différents "
            "domaines d'équipement avec la sortie actuelle ?",
            use_container_width=True,
        ):
            st.session_state.analyse_detaillee = True
            st.rerun()

    if st.session_state.analyse_detaillee:
        deciles_disponibles = sorted(niveau_vie["decile_niveau_vie"].unique().astype(int))
        deciles_selectionnes = st.multiselect(
            "Filtrer les cartes par décile de niveau de vie (D1 = plus modeste, D10 = plus aisé) — "
            "les carreaux hors sélection n'apparaissent pas sur les cartes ci-dessous",
            options=deciles_disponibles,
            default=deciles_disponibles,
            format_func=lambda d: f"D{d}",
        )

        if not deciles_selectionnes:
            st.warning("Sélectionnez au moins un décile pour afficher les cartes.")
            return

        # None (plutôt qu'un ensemble reprenant tous les déciles) quand rien n'est
        # exclu : garde aussi les carreaux sans donnée ind_snv publiée (secret
        # statistique, absents de niveau_vie donc d'aucun décile), qui seraient
        # sinon exclus des cartes même sans filtre actif de la part de l'utilisateur.
        if set(deciles_selectionnes) == set(deciles_disponibles):
            carreaux_filtre_ids = None
        else:
            carreaux_filtre_ids = set(
                niveau_vie.loc[niveau_vie["decile_niveau_vie"].isin(deciles_selectionnes), "id"]
            )
    else:
        # Vue rapide : pas de filtre par décile, tous les déciles fusionnés.
        carreaux_filtre_ids = None

    st.markdown("### Cartes d'accessibilité par domaine d'équipement")

    onglets = st.tabs([f"{d} - {nom}" for d, nom in domaines_a_afficher.items()])
    for onglet, domaine in zip(onglets, domaines_a_afficher):
        with onglet:
            st.markdown("#### Temps d'accès au pôle d'équipements le plus proche")
            with st.spinner(f"Calcul du temps d'accès {domaine}..."):
                carte_temps = _carte_temps_acces_pole_domaine(
                    population_grid_agglo, land_use_data, BPE_agglo, ttm, domaine, fond_carte,
                    carreaux_filtre_ids=carreaux_filtre_ids,
                )
            if carte_temps is None:
                st.info("Aucun carreau dans les déciles sélectionnés.")
            else:
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
                    population_grid_agglo, land_use_data, ttm, domaine, fond_carte,
                    carreaux_filtre_ids=carreaux_filtre_ids,
                )
            if carte_poles is None:
                st.info("Aucun carreau dans les déciles sélectionnés.")
            else:
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

            if st.session_state.analyse_detaillee:
                st.markdown("#### % moyen de pôles atteignables par décile de niveau de vie")
                lignes_decile = [
                    {
                        "Décile": int(decile),
                        **{
                            f"{c} min": moyenne_ponderee_pct_poles(
                                pct_par_carreau_domaine_cutoff[(domaine, c)],
                                carreaux_ids=niveau_vie.loc[niveau_vie["decile_niveau_vie"] == decile, "id"],
                            )
                            for c in CUTOFFS_PCT_MOYEN_POLES
                        },
                    }
                    for decile in sorted(niveau_vie["decile_niveau_vie"].unique())
                ]
                tableau_decile_poles = pd.DataFrame(lignes_decile).set_index("Décile")
                _courbe_pct_moyen_poles(
                    tableau_decile_poles,
                    f"% moyen de pôles atteignables par décile de niveau de vie – {DOMAINES_BPE[domaine]}",
                    "Décile (D1=modeste, D10=aisé)",
                    cmap="viridis",
                )

    st.markdown("### Indicateurs de benchmark inter-réseaux")
    st.caption(
        "Enregistre, pour ce réseau, le % moyen d'équipements pondérés accessibles à "
        "30/45/60 min et le temps moyen pour en atteindre 25/50/75%, par domaine et par "
        "décile de niveau de vie, dans un fichier CSV unique partagé (local + dataset "
        "Hugging Face) pour comparer plusieurs réseaux entre eux — même fichier que celui "
        "alimenté par le notebook. Enregistré automatiquement à la fin de chaque run ; "
        "le bouton ci-dessous permet de ré-enregistrer manuellement si besoin."
    )
    if st.button("💾 Réenregistrer les indicateurs de ce run") or st.session_state.benchmark_a_enregistrer:
        st.session_state.benchmark_a_enregistrer = False
        with st.spinner("Calcul des indicateurs de benchmark..."):
            tableau_benchmark = calculer_index_benchmark(BPE_agglo, land_use_data, ttm, DOMAINES_BPE, niveau_vie)

            chemin_decoupage = chemins_reseau(nom_reseau_str)["decoupage_csv"]
            codes_insee_reseau = pd.read_csv(chemin_decoupage, dtype={"code_insee": str})["code_insee"]
            ville_principale_reseau = ville_principale(codes_insee_reseau)

            longueur_par_ligne = longueur_lignes(st.session_state.feed)
            vkm_par_ligne_job = km_par_ligne_jour(st.session_state.feed, longueur_par_ligne, date_str)
            total_vkm_job = vkm_par_ligne_job["total_km"].sum()

            population_totale_reseau = land_use_data["population"].sum()

            tableau_benchmark.insert(0, "population_totale", population_totale_reseau)
            tableau_benchmark.insert(0, "vehicules_km_JOB", total_vkm_job)
            tableau_benchmark.insert(0, "date_JOB", date_str)
            tableau_benchmark.insert(0, "ville_principale", ville_principale_reseau)
            tableau_benchmark.insert(0, "date_run", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
            tableau_benchmark.insert(0, "reseau", nom_reseau_str)

            chemin_local_benchmark = os.path.join(OUTPUT_DIR, "index_benchmark_reseaux.csv")
            tableau_benchmark_complet = fusionner_et_envoyer_csv(
                tableau_benchmark,
                "benchmark/index_benchmark_reseaux.csv",
                chemin_local_benchmark,
                colonne_cle="reseau",
                valeur_cle=nom_reseau_str,
            )

        st.success(
            f"✓ {len(tableau_benchmark)} ligne(s) enregistrée(s) pour {nom_reseau_str} "
            f"(ville principale : {ville_principale_reseau}) — "
            f"{tableau_benchmark_complet['reseau'].nunique()} réseau(x) au total dans l'index."
        )

    st.markdown("### % moyen de pôles d'équipements majeurs atteignables (pondéré par la population)")
    st.caption(
        "Moyenne, pondérée par la population de chaque carreau, du % de pôles majeurs "
        "d'un domaine atteignables depuis ce carreau — une mesure continue plutôt qu'un "
        "seuil (cf onglet pondérations équipements pour comprendre l'analyse des équipements)."
    )
    _courbe_pct_moyen_poles(
        tableau_pct_moyen_poles,
        f"% moyen de pôles d'équipements majeurs atteignables, par domaine – {nom_reseau_str}",
        "Domaine",
    )
