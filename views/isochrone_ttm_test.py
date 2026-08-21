"""
Page Isochrone (test) - Isochrone "réelle" à partir d'une matrice de temps
de trajet (ttm) précalculée par Accessibility_analysis, quand elle existe
pour ce réseau. Onglet expérimental, cf. src/isochrone_ttm.py pour le
détail des différences avec l'onglet Isochrone (RAPTOR).
"""

import streamlit as st
import streamlit.components.v1 as components

from src.isochrone_ttm import (
    build_map_carreaux,
    carreaux_atteignables,
    charger_geometrie_carreaux,
    trouver_carreau_origine,
    ttm_disponible,
)
from views.arrets import obtenir_indicateurs_arrets


def isochrone_ttm_test_page(lang="fr"):
    st.markdown("---")
    st.caption(
        "🧪 Onglet expérimental : isochrone basé sur une matrice de temps de trajet (ttm) "
        "carreau à carreau, précalculée par l'app sœur "
        "[Accessibility_analysis](https://github.com/antoinechevre/Accessibility_analysis) via r5py "
        "(routage multimodal marche+transport réel sur OSM+GTFS), quand elle existe déjà pour ce réseau "
        "sur le dataset HF partagé. Départ figé à 14h le jour JOB du réseau (heure du calcul du ttm), "
        "contrairement à l'onglet Isochrone qui laisse choisir l'heure mais approxime les temps de marche."
    )

    if st.session_state.feed is None or st.session_state.active_service_ids is None:
        st.info("👆 Veuillez charger un fichier GTFS.")
        return

    nom_reseau_str = st.session_state.nom_reseau_str
    with st.spinner(f"Recherche d'un ttm pour {nom_reseau_str}..."):
        ttm = ttm_disponible(nom_reseau_str)

    if ttm is None:
        st.warning(
            f"Aucun ttm trouvé pour \"{nom_reseau_str}\" sur le dataset partagé "
            "(memory_ttm/ttm_{nom}.parquet) — Accessibility_analysis n'a pas encore calculé cette "
            "matrice pour ce nom de réseau exact. Essayez par exemple avec le GTFS "
            "\"Châlons-en-Champagne\" seul, déjà couvert."
        )
        return

    st.success(f"✅ ttm trouvé pour {nom_reseau_str} : {ttm['from_id'].nunique():,} carreaux.".replace(",", " "))

    if st.session_state.indicateurs_arrets is None:
        with st.spinner("Calcul des indicateurs d'arrêts..."):
            try:
                obtenir_indicateurs_arrets(lang)
            except Exception as e:
                st.error(f"Erreur lors du calcul des arrêts : {e}")
                return

    indicateurs = st.session_state.indicateurs_arrets
    if indicateurs is None or indicateurs.empty:
        st.info("🔄 Calcul des indicateurs en cours...")
        return

    arrets_tries = indicateurs.sort_values("nombre_passages", ascending=False).reset_index(drop=True)
    options_labels = [
        f"{row.stop_name} — {int(row.nombre_passages)} passages" for row in arrets_tries.itertuples()
    ]

    col_choix, col_budget = st.columns([2, 1])
    with col_choix:
        index_choisi = st.selectbox(
            "Arrêt de départ", options=range(len(options_labels)),
            format_func=lambda i: options_labels[i], index=0, key="isochrone_ttm_arret",
        )
    with col_budget:
        budget_min = st.slider("Budget de trajet (minutes)", 5, 90, 30, step=5, key="isochrone_ttm_budget")

    origine = arrets_tries.iloc[index_choisi]

    if st.button("🚀 Calculer l'isochrone (ttm)", type="primary"):
        with st.spinner("Recherche du carreau de départ et des carreaux atteignables..."):
            origin_id = trouver_carreau_origine(origine["stop_lat"], origine["stop_lon"])
            if origin_id is None:
                st.session_state["isochrone_ttm_resultats"] = (origine["stop_id"], None, None, budget_min)
            else:
                atteignables = carreaux_atteignables(ttm, origin_id, budget_min)
                gdf = charger_geometrie_carreaux(
                    atteignables["idcar_200m"], origine["stop_lat"], origine["stop_lon"]
                )
                gdf = gdf.merge(atteignables, on="idcar_200m", how="inner")
                st.session_state["isochrone_ttm_resultats"] = (origine["stop_id"], origin_id, gdf, budget_min)

    resultats = st.session_state.get("isochrone_ttm_resultats")
    if resultats is None or resultats[0] != origine["stop_id"]:
        st.info("Réglez les paramètres puis cliquez sur \"Calculer l'isochrone (ttm)\".")
        return

    _, origin_id, gdf, budget_min_affiche = resultats

    if origin_id is None:
        st.warning(
            "Ce point de départ ne correspond à aucun carreau INSEE connu (hors grille, zone non "
            "peuplée...) — impossible de le relier au ttm."
        )
        return

    col_map, col_stats = st.columns([3, 1])
    with col_map:
        m = build_map_carreaux(origine, gdf, budget_min_affiche)
        components.html(m.get_root().render(), height=650)
    with col_stats:
        st.subheader(origine["stop_name"])
        if gdf.empty:
            st.warning("Aucun carreau atteignable avec ce budget.")
        else:
            st.metric("Carreaux atteints", f"{len(gdf):,}".replace(",", " "))
            st.metric("Temps médian", f"{gdf['travel_time'].median():.0f} min")
