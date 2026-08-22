"""
Page Arrêts - Analyse GTFS Indicateurs par Arrêt
"""

import os
import tempfile

import streamlit as st
import streamlit.components.v1 as components

from src.cartographie import create_carte_arrets
from src.info_reseau import charger_ou_calculer_dates_service, date_str, nom_reseau_str
from src.arrets import calculer_indicateurs_arrets
from src.export_html import exporter_statistiques_html
from src.hf_cache import charger_ou_calculer_avec_cache_hf
from src.i18n import t


def obtenir_indicateurs_arrets(lang="fr"):
    """
    Calcule les indicateurs par arrêt pour le GTFS chargé, ou les recharge
    depuis le cache (disque local puis dataset Hugging Face) s'ils y ont
    déjà été calculés pour ce réseau, et les stocke dans
    st.session_state.indicateurs_arrets. Partagé entre la page Arrêts et la
    page Isochrone (cf. views/isochrone.py, qui a besoin de la liste des
    arrêts et de leur nombre de passages pour présélectionner l'arrêt le
    plus fréquenté) — évite un recalcul si l'une a déjà été visitée.

    Suppose st.session_state.feed et .active_service_ids déjà chargés
    (vérifié par l'appelant). Lève toute exception du calcul plutôt que de
    l'avaler, pour que l'appelant choisisse comment l'afficher.

    Nom de fichier daté par date_str (== date_JOB, cf. app.py) plutôt que
    fixe : sans ça, un GTFS rafraîchi (nouveaux stop_id, service modifié)
    reste indéfiniment associé aux indicateurs de l'ANCIENNE version,
    jamais invalidés (charger_ou_calculer_avec_cache_hf ne vérifie que la
    présence du fichier, pas sa fraîcheur). Le stop_id du plus fréquenté
    dans ce cache périmé peut ne plus correspondre à rien dans le feed
    frais rechargé à côté — observé concrètement sur Reims (rafraîchi
    entre-temps) : l'onglet Isochrone (qui recombine ce stop_id avec le
    feed frais pour le calcul RAPTOR) tombait sur "aucun arrêt atteignable"
    faute de correspondre encore au feed courant, alors qu'Arrêts/Tronçons
    (qui n'utilisent le cache que pour l'affichage, jamais recombiné à un
    feed séparé) ne laissaient rien paraître.
    """
    if st.session_state.indicateurs_arrets is None:
        nom_fichier = f"indicateurs_arrets_{st.session_state.date_str}.csv"
        nom_reseau = st.session_state.nom_reseau_str
        chemin_cache = os.path.join("data", "memory_troncons", nom_reseau, nom_fichier)
        nom_fichier_hf = f"memory_troncons/{nom_reseau}/{nom_fichier}"
        st.session_state.indicateurs_arrets = charger_ou_calculer_avec_cache_hf(
            chemin_cache,
            nom_fichier_hf,
            lambda: calculer_indicateurs_arrets(
                st.session_state.feed,
                st.session_state.date_str,
                active_service_ids=st.session_state.active_service_ids,
            ),
        )
    return st.session_state.indicateurs_arrets


def arrets_page(lang="fr"):
    st.markdown("---")

    # Vérifier si les données sont chargées
    if (
        st.session_state.feed is not None
        and st.session_state.active_service_ids is not None
    ):
        # afficher infos réseau
           #cherche nom réseau
        nom_reseau_valeur = nom_reseau_str(st.session_state.feed)
        st.info(t("commun.reseau_info", lang, reseau=nom_reseau_valeur))

        _, date_debut, date_fin, date_JOB = charger_ou_calculer_dates_service(
            st.session_state.feed, st.session_state.nom_reseau_str,
            academie=st.session_state.get("academie_reseau"),
        )

        date_service_str, date_JOB_text = date_str(date_debut, date_fin, date_JOB, lang=lang)

        st.info(t("commun.plage_info", lang, plage=date_service_str, job=date_JOB_text))


        # Calculer les indicateurs automatiquement si pas déjà fait, ou les
        # recharger depuis le cache (disque local puis dataset Hugging
        # Face) s'ils y ont déjà été calculés pour ce réseau — sûr d'une
        # exécution à l'autre car date_JOB est déterministe pour un GTFS
        # donné (cf. dates_service, info_reseau.py).
        if st.session_state.indicateurs_arrets is None:
            with st.spinner(t("arrets.spinner_indicateurs", lang)):
                try:
                    obtenir_indicateurs_arrets(lang)
                except Exception as e:
                    st.error(t("arrets.erreur_indicateurs", lang, erreur=e))
                    return
            st.success(t("arrets.succes", lang))

        if st.session_state.indicateurs_arrets is not None:
            indicateurs = st.session_state.indicateurs_arrets

            # Statistiques globales
            st.header(t("arrets.header_stats", lang))
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric(t("arrets.metric_nb_arrets", lang), len(indicateurs))
            with col2:
                st.metric(
                    t("arrets.metric_arrets_actifs", lang),
                    len(indicateurs[indicateurs["nombre_passages"] > 0]),
                )
            with col3:
                total_passages = int(indicateurs["nombre_passages"].sum())
                st.metric(t("arrets.metric_total_passages", lang), total_passages)

            # Top 10 arrêts
            st.header(t("arrets.header_top10", lang))
            actifs = indicateurs[indicateurs["nombre_passages"] > 0].copy()
            if not actifs.empty:
                actifs = actifs.sort_values("nombre_passages", ascending=False)
                st.dataframe(actifs.drop(columns=["stop_lon", "stop_lat"]).head(10))
            else:
                st.info(t("arrets.aucun_actif", lang))

            # Fiche statistiques (export HTML)
            st.header(t("arrets.header_fiche", lang))
            output_stats = os.path.join(tempfile.gettempdir(), "statistiques_arrets_streamlit.html")
            exporter_statistiques_html(
                indicateurs,
                t("commun.analyse_du", lang, date=st.session_state.date_str),
                st.session_state.date_str,
                output_stats,
                nom_reseau_str=st.session_state.nom_reseau_str,
                lang=lang,
            )
            with open(output_stats, "r", encoding="utf-8") as f:
                components.html(f.read(), height=600, scrolling=True)

            # Carte
            st.header(t("arrets.header_carte", lang))
            output_map = os.path.join(tempfile.gettempdir(), "stops_map_streamlit.html")
            m = create_carte_arrets(
                indicateurs,
                st.session_state.nom_reseau_str,
                t("commun.analyse_du", lang, date=st.session_state.date_str),
                st.session_state.date_str,
                st.session_state.zip_path,
                output_map,
                chemin_logo=st.session_state.chemin_logo,
                lang=lang,
                active_service_ids=st.session_state.active_service_ids,
            )
            # get_root().render() (le HTML complet, celui écrit par .save())
            # plutôt que _repr_html_() : cette dernière enveloppe la carte
            # dans un wrapper "responsive" (padding-bottom en %) pensé pour
            # Jupyter, qui impose son propre ratio hauteur/largeur et ignore
            # le height/width demandés ici.
            components.html(m.get_root().render(), height=1000, width=1000)

            # Télécharger les résultats
            st.header(t("commun.header_telechargement", lang))
            csv = indicateurs.to_csv(index=False).encode("utf-8")
            st.download_button(
                label=t("arrets.telecharger_csv", lang),
                data=csv,
                file_name=f"indicateurs_arrets_{st.session_state.date_str}.csv",
                mime="text/csv",
            )
        else:
            st.info(t("commun.calcul_en_cours", lang))
    else:
        st.info(t("commun.veuillez_charger_gtfs", lang))
