"""
Page Cartographie interactive infracommunale INSEE - aperçu local (carroyage
population, réutilisé depuis une autre page déjà lancée) au-dessus d'un lien
vers l'outil INSEE (pas d'intégration en iframe possible : le site renvoie
l'en-tête X-Frame-Options: SAMEORIGIN, qui bloque son affichage dans un
iframe sur un autre domaine, quel que soit le code utilisé côté app).
"""

import streamlit as st

from src.cartographie import carte_population_infracommunale
from src.utilitaires_matrix import deciles_niveau_vie

INSEE_URL = "https://www.insee.fr/fr/outil-interactif/7737357/map.html"


def _population_grid_disponible():
    """Récupère population_grid_agglo déjà calculé par l'onglet Accessibilité
    ou Pondération équipements, sans relancer de pipeline ici — cette page
    n'a besoin que du carroyage population, pas de la BPE ni de la matrice
    des temps de trajet."""
    if st.session_state.get("pipeline_data") is not None:
        return st.session_state.pipeline_data[0]
    if st.session_state.get("ponderation_data") is not None:
        return st.session_state.ponderation_data[0]
    return None


def cartographie_insee_page():
    st.header("🗺️ Cartographie interactive infracommunale INSEE")

    population_grid_agglo = _population_grid_disponible()

    if population_grid_agglo is None:
        st.info(
            "Lancez d'abord une analyse dans l'onglet Accessibilité ou Pondération "
            "équipements pour afficher ici un aperçu de la population infracommunale."
        )
    else:
        st.markdown("#### Population infracommunale (carroyage INSEE 200x200, Filosofi 2019)")

        niveau_vie = deciles_niveau_vie(population_grid_agglo)
        deciles_disponibles = sorted(niveau_vie["decile_niveau_vie"].unique().astype(int))
        deciles_selectionnes = st.multiselect(
            "Filtrer par décile de niveau de vie (D1 = plus modeste, D10 = plus aisé) — "
            "les carreaux hors sélection n'apparaissent pas sur la carte",
            options=deciles_disponibles,
            default=deciles_disponibles,
            format_func=lambda d: f"D{d}",
        )

        if not deciles_selectionnes:
            st.warning("Sélectionnez au moins un décile pour afficher la carte.")
        else:
            # None (plutôt qu'un ensemble reprenant tous les déciles) quand rien
            # n'est exclu : garde aussi les carreaux sans donnée ind_snv publiée
            # (secret statistique, absents de niveau_vie donc d'aucun décile).
            if set(deciles_selectionnes) == set(deciles_disponibles):
                carreaux_filtre_ids = None
            else:
                carreaux_filtre_ids = set(
                    niveau_vie.loc[niveau_vie["decile_niveau_vie"].isin(deciles_selectionnes), "id"]
                )

            carte = carte_population_infracommunale(population_grid_agglo, carreaux_filtre_ids=carreaux_filtre_ids)
            if carte is None:
                st.info("Aucun carreau dans les déciles sélectionnés.")
            else:
                st.components.v1.html(carte.get_root().render(), height=520, scrolling=False)

    st.markdown("---")

    st.markdown(
        """
        Outil cartographique officiel de l'INSEE, à l'échelle infracommunale, qui
        permet d'explorer la Base Permanente des Équipements (BPE) — la même
        source de données que celle utilisée par l'onglet **Pondération
        équipements** de cette application.
        """
    )

    st.info(
        "Cet outil ne peut pas être intégré directement dans la page (l'INSEE "
        "bloque son affichage en iframe sur un autre site) : il s'ouvre dans un "
        "nouvel onglet du navigateur."
    )

    st.link_button("🔗 Ouvrir la cartographie INSEE", INSEE_URL, use_container_width=True)
