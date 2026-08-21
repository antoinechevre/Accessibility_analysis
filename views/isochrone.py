"""
Page Isochrone - Accessibilité en transport collectif depuis un arrêt
"""

from datetime import time as dt_time

import streamlit as st
import streamlit.components.v1 as components

from src.i18n import t
from src.isochrone import (
    DEFAULT_BUDGET_MIN,
    DEFAULT_MAX_TRANSFERS,
    DEFAULT_WALK_BUFFER_MIN,
    GEOPF_MAX_REQ_PER_SEC,
    GEOPF_MAX_STOPS_CALLED,
    build_map,
    calculer_arrets_atteignables,
    recuperer_buffers_marche,
    stop_ids_pour_station,
)
from views.arrets import obtenir_indicateurs_arrets


def isochrone_page(lang="fr"):
    st.markdown("---")

    if st.session_state.feed is None or st.session_state.active_service_ids is None:
        st.info(t("commun.veuillez_charger_gtfs", lang))
        return

    if st.session_state.indicateurs_arrets is None:
        with st.spinner(t("arrets.spinner_indicateurs", lang)):
            try:
                obtenir_indicateurs_arrets(lang)
            except Exception as e:
                st.error(t("isochrone.erreur_arrets", lang, erreur=e))
                return

    indicateurs = st.session_state.indicateurs_arrets
    if indicateurs is None or indicateurs.empty:
        st.info(t("commun.calcul_en_cours", lang))
        return

    st.info(t("isochrone.intro", lang))

    # Trié par nombre de passages décroissant : l'arrêt le plus fréquenté
    # (index 0) est présélectionné par défaut.
    arrets_tries = indicateurs.sort_values("nombre_passages", ascending=False).reset_index(drop=True)
    options_labels = [
        f"{row.stop_name} — {int(row.nombre_passages)} {t('isochrone.passages_suffix', lang)}"
        for row in arrets_tries.itertuples()
    ]

    col_choix, col_heure = st.columns([2, 1])
    with col_choix:
        index_choisi = st.selectbox(
            t("isochrone.label_arret_depart", lang),
            options=range(len(options_labels)),
            format_func=lambda i: options_labels[i],
            index=0,
        )
    with col_heure:
        heure_pointe = st.time_input(t("isochrone.label_heure", lang), value=dt_time(8, 0))

    col1, col2, col3 = st.columns(3)
    with col1:
        budget_min = st.slider(t("isochrone.label_budget", lang), 5, 90, DEFAULT_BUDGET_MIN, step=5)
    with col2:
        max_correspondances = st.slider(
            t("isochrone.label_correspondances", lang), 0, 2, DEFAULT_MAX_TRANSFERS,
            help=t("isochrone.aide_correspondances", lang),
        )
    with col3:
        rayon_marche_min = st.slider(
            t("isochrone.label_marche", lang), 0, 15, DEFAULT_WALK_BUFFER_MIN,
            help=t("isochrone.aide_marche", lang),
        )

    origine = arrets_tries.iloc[index_choisi]

    if st.button(t("isochrone.bouton_calculer", lang), type="primary"):
        depart_s = heure_pointe.hour * 3600 + heure_pointe.minute * 60
        origin_stop_ids = stop_ids_pour_station(st.session_state.feed, origine["stop_id"])
        with st.spinner(t("isochrone.spinner_calcul", lang)):
            arrets = calculer_arrets_atteignables(
                st.session_state.feed,
                st.session_state.active_service_ids,
                origin_stop_ids,
                depart_s,
                budget_min,
                max_correspondances,
            )
            cache = st.session_state.setdefault("_geopf_cache", {})
            buffers = (
                recuperer_buffers_marche(arrets, rayon_marche_min, cache)
                if rayon_marche_min > 0 and not arrets.empty
                else {}
            )
        st.session_state["isochrone_resultats"] = (
            origine["stop_id"], arrets, buffers, budget_min, rayon_marche_min,
        )

    resultats = st.session_state.get("isochrone_resultats")
    if resultats is None or resultats[0] != origine["stop_id"]:
        st.info(t("isochrone.attente_calcul", lang))
        return

    _, arrets, buffers, budget_min_affiche, rayon_marche_min_affiche = resultats

    col_map, col_stats = st.columns([3, 1])
    with col_map:
        m = build_map(
            origine, arrets, buffers, budget_min_affiche, rayon_marche_min_affiche,
            legende_duree=t("isochrone.legende_duree", lang),
        )
        components.html(m.get_root().render(), height=650)
    with col_stats:
        st.subheader(origine["stop_name"])
        if arrets.empty:
            st.warning(t("isochrone.aucun_atteignable", lang))
        else:
            st.metric(t("isochrone.metric_atteints", lang), f"{len(arrets):,}".replace(",", " "))
            st.metric(t("isochrone.metric_duree_mediane", lang), f"{arrets['duree_min'].median():.0f} min")
            st.metric(
                t("isochrone.metric_avec_correspondance", lang),
                f"{int((arrets['correspondances'] > 0).sum()):,}".replace(",", " "),
            )
            if len(arrets) > GEOPF_MAX_STOPS_CALLED:
                st.caption(
                    t("isochrone.note_limite_geopf", lang, n=GEOPF_MAX_STOPS_CALLED, req=GEOPF_MAX_REQ_PER_SEC)
                )
