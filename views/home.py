"""
Page d'accueil - Application d'analyse de l'accessibilité piétons / transports collectifs à 30 min des équipements 
"""

import streamlit as st

def home_page():
    st.markdown("---")
    ## Application d'analyse de l'accessibilité piétons / transports collectifs à 30 min des équipements
    
    st.markdown(
        """
    ## Projet personnel 2026
           
    Ce projet a été développé pour un contexte français par Antoine Chevre (et claude.ai...) en s'inspirant des travaux -Introduction to urban accessibility- et plus spécifiquement -3  Calculating accessibility estimates in R 
    de Rafael H. M. Pereira et Daniel Herszenhut  de Ipea - Institute for Applied Economic Research. Il complète le projet d'analyse GTFS (cf lien ci dessous).
    
    **Concepteur :** Antoine Chèvre 🐐 (et claude.ai....)
    """
    )
    st.markdown("---") 
    
    # Liens rapides
    st.markdown(
        """
    ## 🔗 Liens rapides
    
    L'ouvrage de référence  **📓 [Introduction to urban accessibility](https://ipeagit.github.io/intro_access_book/3_calculando_acesso.en.html)
    Le chapitre adaptée **📓[3  Calculating accessibility estimates in R](https://ipeagit.github.io/intro_access_book/s2_calculo.en.html)

    - ** 📓 [Git analyse accessibilité ](https://github.com/antoinechevre/Accessibility_analysis)
   
    Pour analyser les GTFS 
    - **📓 Cerema [Notebook Google Colab](https://colab.research.google.com/github/CEREMA/hackathon-gtfs/blob/main/gtfs_notebook.ipynb)** : Prendre en main le code, exécuter les cellules et regarder les cartographies dynamiques
    - **📓 Antoine Chèvre [Git analyse GTFS](https://github.com/antoinechevre/gtfs_analysis_app)** et l'[application associée](https://gtfsanalysisme3zaa.streamlit.app/) 
        
    
    Les sources de données: 
    -** 📓 GTFS aller chercher dans le [point d'accès national](https://transport.data.gouv.fr/)
    -** 📓 équipements issus de la [base de données BPE INSEE2025](https://www.insee.fr/fr/statistiques/8217525) qui est derrière la [cartograhie interactive infracommunale](https://www.insee.fr/fr/outil-interactif/7737357/map.html) 
        
        """
    
    )
    
    # Objectifs
    st.markdown(
        """
    ## Objectifs: 
    - **Offrir une chaîne de traitement** pour passer d'un jeu GTFS brut à l'analyse d'accessibilité de l'agglomération concernée:
      
        - en mode piéton/transports collectifs en JOB à l'heure de pointe  
    
        - des équipements issus de la base BPE à 30 min de l'agglomération concernée   
    
        - selon un carroyage de 200x200 m de l'INSEE  
    
    - **Exporter des cartes HTML et PNG géolocalisées** d'accessibilité transport collectif et piétons à 30 min, par domaine d'équipement
    - **Proposer à la fois des scripts utilisables en local**, une interface web conviviale (via Streamlit) pour les utilisateurs non-techniques, et un notebook d'exemple pour tester / explorer les résultats
    """
    )
    st.markdown("---")

    # Fonctionnalités disponibles
    st.markdown(
        """
    
    ### Fonctionnalités disponibles:  
    À partir d'un GTFS:
    À partir d'un GTFS et d'un découpage communal :

    1. Construit le réseau multimodal piéton + transport collectif (`r5py`) à partir du GTFS pour une date JOB indiquée et le réseau viaire pour les cheminements piétons 
    
    2. Récupère le carroyage population INSEE 200x200 2019 incluant les catégories socio économiques (Filosofi) et la Base Permanente des Équipements (BPE, INSEE) 
    
    3. Pondère les équipements par gamme (proximité / intermédiaire / supérieure / hors gamme) et par domaine (santé, enseignement, commerces...) avec une pondération des équipements dans le fichier src/ponderation_bpe.py: 
    
    2.1 Liste des équipements BPE - cf https://vscode.dev/github/antoinechevre/Accessibility_analysis/blob/main/data/BPE25_anonymisee_dessin_fichier.html 
    
        "O": "Tout équipements pondérés",  
        
        "A": "Services pour les particuliers",
        
        "B": "Commerces",
        
        "C": "Enseignement",
        
        "D": "Santé et action sociale",
        
        "E": "Transports et déplacements",
        
        "F": "Sports, loisirs et culture",
        
        "G": "Tourisme",
    
    2.2 pondération selon la classification
        
        "Gamme de proximité"
        
        "Gamme intermédiaire"
        
        "Gamme supérieure"
        
        "Hors Gamme"
    
    2.3 des seuils sur pondération par carreaux de 200x200 et par domaine par rapport à la moyenne de pondération des carreaux par domaines (objectif de filtrer les carreaux significatifs)  

    4. Calcule la matrice des temps de trajet (`TravelTimeMatrix`) entre tous les carreaux avec GTFS / Piétons OSM à l'heure de pointe en JOB 
    
    5. Calcule plusieurs indicateurs d'accessibilité : 
    
        5.1 opportunités cumulées, 
    
        5.2 coût au plus proche, 
    
        5.3 gravité, 
    
        5.4 compétition (Enhanced 2SFCA).
    
    6. Exporte des cartes interactives (HTML/Folium) et statiques (PNG) par domaine d'équipement avec une déclinaison par déciles de population
    
    7. produit des indicateurs agrégés par décile et par type d'équipements du % accessible en fonction du temps de trajet  
    
    """
    )
    
        ### Instructions :
    
    st.markdown(
        """ 
    ### Instructions:  
        
    1. **Chargez un fichier GTFS** dans la barre latérale (format ZIP)
    2. **Naviguez entre les pages** pour explorer les analyses

    > **⚠️ Limitation importante :** Cette appli ne fonctionne qu'en France, les données BPE datent de 2025, les données de population avec un carroyage de 200mx200m datent de 2019. 
    > **Temps de calcul important":** L'application calcule l'ensemble des Origines / Destinations pour l'agglomération selon un découpage en 200x200 m, les calculs peuvent être longs....  
      """
    )    
    
    st.markdown("---")

    # Section Auteurs
    st.markdown(
    """
    Contributeur Antoine Chèvre [@antoine.chevre](https://github.com/antoinechevre) 🐐
    In we goat we trust 
    """
    ) 