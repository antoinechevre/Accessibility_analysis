"""
Page Cartographie interactive infracommunale INSEE - lien vers l'outil INSEE
(pas d'intégration en iframe possible : le site renvoie l'en-tête
X-Frame-Options: SAMEORIGIN, qui bloque son affichage dans un iframe sur un
autre domaine, quel que soit le code utilisé côté app).
"""

import streamlit as st

INSEE_URL = "https://www.insee.fr/fr/outil-interactif/7737357/map.html"


def cartographie_insee_page():
    st.header("🗺️ Cartographie interactive infracommunale INSEE")

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
