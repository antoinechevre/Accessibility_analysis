"""
Page d'accueil - Application d'analyse de l'accessibilité piétons / transports collectifs à 30 min des équipements
+ analyse réseau GTFS (arrêts / tronçons)
"""

import streamlit as st


def home_page():
    st.markdown(
        """

    Ce projet a été développé comme projet personnel pour un contexte français par Antoine Chevre (et claude.ai...) en s'inspirant: 
    - des travaux -Introduction to urban accessibility- et plus spécifiquement -3 Calculating accessibility estimates in R
    de Rafael H. M. Pereira et Daniel Herszenhut de Ipea - Institute for Applied Economic Research.
    - des travaux du Cerema lors du Hackathon TSNI 2025 sur l'analyse GTFS (arrêts / tronçons)

    **Concepteur :** Antoine Chèvre 🐐 (et claude.ai....)

    Cette application regroupe deux types d'analyses se basant sur le même jeu de données GTFS :
    l'analyse d'accessibilité urbaine aux équipements en transport collectif / piéton, et l'analyse
    du réseau de transport collectif en lui-même.
    
    """
    )

    st.markdown("---")
    st.markdown("## 📍 Analyse accessibilité")

    onglet_presentation, onglet_fonctionnalites, onglet_liens = st.tabs(
        ["🎯 Objectifs", "⚙️ Fonctionnalités", "🔗 Liens & Instructions"]
    )

    with onglet_presentation:
        st.markdown(
            """
    - **Offrir une chaîne de traitement** pour passer d'un jeu GTFS brut à l'analyse d'accessibilité de l'agglomération concernée :
        - en mode piéton/transports collectifs en JOB à l'heure de pointe
        - des équipements issus de la base BPE à 30 min de l'agglomération concernée
        - selon un carroyage de 200x200 m de l'INSEE
    - **Exporter des cartes HTML et PNG géolocalisées** d'accessibilité transport collectif et piétons à 30 min et 45 min, par domaine d'équipement
    - **Proposer un benchmark entre villes française** en comparant les indicateurs d'accessibilité aux équipements par domaine et par décile de population  
    """
        )

    with onglet_fonctionnalites:
        st.markdown("À partir d'un GTFS et d'un découpage communal :")

        st.markdown(
            "1. Construit le réseau multimodal piéton + transport collectif (`r5py`) à partir du GTFS "
            "pour une date JOB indiquée, calculer automatiquement hors période vacances scolaires et le réseau viaire pour les cheminements piétons"
        )
        st.markdown(
            "2. Récupère le carroyage population INSEE 200x200 2019 incluant les catégories socio des individus au sens fiscal du termes "
            "économiques (Filosofi) et la Base Permanente des Équipements (BPE, INSEE)"
        )

        st.markdown(
            "3. Pondère les équipements par gamme (proximité / intermédiaire / supérieure / hors gamme) "
            "et par domaine (santé, enseignement, commerces...) — cf. `src/ponderation_bpe.py`"
        )
        with st.expander("Détail de la pondération des équipements (BPE)"):
            st.markdown(
                """
    **3.1 — Domaines d'équipements** ([liste complète des équipements BPE](https://vscode.dev/github/antoinechevre/Accessibility_analysis/blob/main/data/BPE25_anonymisee_dessin_fichier.html))

    | Code | Domaine |
    |---|---|
    | O | Tout équipements pondérés |
    | A | Services pour les particuliers |
    | B | Commerces |
    | C | Enseignement |
    | D | Santé et action sociale |
    | E | Transports et déplacements |
    | F | Sports, loisirs et culture |
    | G | Tourisme |

    **3.2 — Pondération selon la classification**
    Gamme de proximité · Gamme intermédiaire · Gamme supérieure · Hors gamme

    **3.3 — Seuils de significativité**
    Seuils sur la pondération par carreau de 200x200 et par domaine, par rapport à la moyenne de pondération des carreaux du domaine (objectif : filtrer les carreaux significatifs).
    """
            )

        st.markdown(
            "4. Calcule la matrice des temps de trajet (`TravelTimeMatrix`) entre tous les carreaux "
            "avec GTFS / Piétons OSM à l'heure de pointe en JOB"
        )

        st.markdown("5. Calcule plusieurs indicateurs d'accessibilité")
        with st.expander("Détail des indicateurs d'accessibilité"):
            st.markdown(
                """
    - **5.1 Opportunités cumulées** : nombre d'opportunités / équipements atteignables depuis chaque carreau dans un temps de trajet donné
    - **5.2 Coût au plus proche** : temps minimum pour atteindre un certain nombre d'opportunités
    - **5.3 Gravité** : le poids de chaque opportunité décroît à mesure que le temps de trajet augmente (exponentiel inversé)
    - **5.4 Compétition (Enhanced 2SFCA)** : niveau d'accessibilité en considérant la compétition entre opportunités
    """
            )

        st.markdown(
            "6. Exporte des cartes interactives (HTML/Folium) et statiques (PNG) par domaine "
            "d'équipement avec une déclinaison par déciles de population"
        )

        st.markdown(
            "7. Propose un benchmark avec des indicateurs pour les différentes villes françaises en "
            "fonction des domaines d'équipements et des déciles de niveau de richesse"
        )
        with st.expander("Détail du benchmark inter-réseaux"):
            st.markdown(
                """
    - **7.1** Temps de trajet moyen pour atteindre 25 %, 50 %, 75 % des opportunités/équipements
    - **7.2** % d'opportunités/équipements atteignables pour un temps de trajet moyen de 30 min, 45 min, 60 min, 75 min
    """
            )

    with onglet_liens:
        st.markdown(
            """
    ### Liens rapides

    - **📓 Ouvrage de référence** : [Introduction to urban accessibility](https://ipeagit.github.io/intro_access_book/3_calculando_acesso.en.html) — chapitre adapté : [3. Calculating accessibility estimates in R](https://ipeagit.github.io/intro_access_book/s2_calculo.en.html)
    - **📓 Ce projet** : [Git analyse accessibilité](https://github.com/antoinechevre/Accessibility_analysis)

    Sources de données :
    - **📓 Équipements** : issus de la [base de données BPE INSEE 2025](https://www.insee.fr/fr/statistiques/8217525), déjà exposée par la [cartographie interactive infracommunale](https://www.insee.fr/fr/outil-interactif/7737357/map.html)

    ### Instructions

    1. **Chargez un fichier GTFS** dans la barre latérale (format ZIP)
    2. **Naviguez entre les pages** pour explorer les analyses

    > **⚠️ Limitation importante :** cette appli ne fonctionne qu'en France ; les données BPE datent de 2025, les données de population (carroyage 200x200 m) de 2019.
    > **⏱️ Temps de calcul important :** l'application calcule l'ensemble des origines / destinations pour l'agglomération selon un découpage en 200x200 m — les calculs peuvent être longs.
    """
        )

    st.markdown("---")
    explications_analyse_gtfs()

    st.markdown("---")
    st.markdown(
        """
    Antoine Chèvre [@antoine.chevre](https://github.com/antoinechevre) 🐐
    In we goat we trust
    """
    )


def explications_analyse_gtfs():
    """Présentation de l'analyse réseau GTFS (arrêts/tronçons) : reprise à
    l'identique sur la page Accueil (section "Analyse GTFS") et sous
    l'onglet "Analyse réseau" (sous-page "Explications") — un seul texte à
    maintenir plutôt qu'une copie dupliquée à chaque endroit."""
    st.markdown("## 🚏 Analyse GTFS")
    st.markdown(
        """
    L'analyse GTFS a été développée lors du Hackathon TSNI 2025 du Cerema et reprise par Antoine Chèvre (et claude.ai...).

    **Équipe Cerema :** Patrick Gendre, Hugo De Luca et Maxence Liogier
    """
    )

    onglet_fonctionnalites_gtfs, onglet_liens_gtfs = st.tabs(["⚙️ Fonctionnalités", "🔗 Liens"])

    with onglet_fonctionnalites_gtfs:
        st.markdown(
            """
    Analyse des données GTFS (General Transit Feed Specification) pour extraire des indicateurs clés
    sur les transports en commun — indépendamment de l'analyse d'accessibilité aux équipements ci-dessus.
    Détermine la plage temporelle sur laquelle le GTFS est actif et identifie un JOB (jour ouvré de base,
    mardi ou jeudi le plus loin dans le temps sur cette plage). A cela a été ajouté deux types d'isochrones : isochrone d'arrêt à arrêt et isochrone de tronçon à tronçon.

    #### 📍 Analyse par arrêts
    - Nombre de passages par arrêt
    - Carte interactive des arrêts
    - Statistiques détaillées

    Fonctionne avec n'importe quel GTFS.

    #### 🛤️ Analyse par tronçons
    - Nombre de passages par tronçon (bus, tram, métro, trolley, ferry...)
    - Calcul des vitesses moyennes
    - Carte interactive des tronçons
    
    #### ⏱️ Isochrone d'arrêt à arrêt
    - Carte interactive des isochrones d'arrêt à arrêt à partir d'un arrêt sélectionné en sélectionnanr le temps de trajet souhaité (ex : 15 min, 30 min, 45 min)

    #### 🧪 Isochrone de carreaux 200x200 m
    - Carte interactive des isochrones de carreaux 200x200 m à partir d'un carreau sélectionné en sélectionnant le temps de trajet souhaité (ex : 15 min, 30 min, 45 min)
    
    
    
    """
    
    
    
    
        )

    with onglet_liens_gtfs:
        st.markdown(
            """
    - **📓 Hackathon Cerema** : [Notebook Google Colab](https://colab.research.google.com/github/CEREMA/hackathon-gtfs/blob/main/gtfs_notebook.ipynb) — prendre en main le code, exécuter les cellules et regarder les cartographies dynamiques
    - **📓 Application dédiée** : [GTFS_analyse_fr](https://huggingface.co/spaces/antoinechevre/GTFS_analyse_fr)

    Où trouver un GTFS :
    - En France : [point d'accès national](https://transport.data.gouv.fr/)
    - À l'international : [transit.land](https://www.transit.land/)
    """
        )
