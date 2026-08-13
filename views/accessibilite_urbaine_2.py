"""
Page "Accessibilité urbaine" de app_2.py — vue dédiée à deux cartes côte à
côte par domaine d'équipement BPE : accessibilité à 45 min (nombre de pôles
d'équipements majeurs atteignables depuis chaque carreau) à gauche,
équipements pondérés (offre brute pondérée par gamme) à droite.

Réutilise le pipeline de données de views/accessibilite_index.py
(_construire_pipeline, identique — carroyage, BPE pondérée, extrait OSM,
matrice des temps de trajet) et la carte de pondération de
src/BPE_traitement.py (carte_ponderation_domaine, inchangée) : aucune
logique de calcul dupliquée ici, seulement l'assemblage de la page et une
variante à un seul cutoff (45 min, pas de DualMap 30/45) de
_carte_poles_accessibles_domaine.
"""

import datetime
import json
import os
import uuid

import folium
import pandas as pd
import streamlit as st

from src.BPE_traitement import carte_ponderation_domaine
from src.cartographie import echelle_continue_html, script_reajuster_si_masque, script_synchroniser_zoom, titre_carte_html
from src.pipeline_donnees import DOMAINES_BPE, HorsMetropoleError
from src.ponderation_bpe import GAMMES_POIDS_PAR_DOMAINE, SEUILS_DOMAINE
from src.ponderation_bpe_2 import reponderer_bpe_2
from src.utilitaires_matrix import cumulative_cutoff, deciles_niveau_vie, moyenne_ponderee_pct_poles, pct_poles_atteignables_par_carreau
from views.accessibilite_index import CUTOFFS_PCT_MOYEN_POLES, _construire_pipeline, _courbe_pct_moyen_poles

FONDS_CARTE = {
    "OpenStreetMap": "OpenStreetMap",
    "CartoDB Positron": "CartoDB positron",
    "CartoDB Dark Matter": "CartoDB dark_matter",
}

# Libellés explicites ("A - Services pour les particuliers"...) pour l'index
# des tableaux de pondération/seuils éditables (section "Tester d'autres
# pondérations") — affichés à la place des seuls codes de domaine (A, B...),
# et reconvertis vers ces codes (LABELS_VERS_DOMAINE) pour reponderer_bpe_2,
# qui attend les clés de src/ponderation_bpe.py (GAMMES_POIDS_PAR_DOMAINE,
# SEUILS_DOMAINE), pas ces libellés.
LABELS_DOMAINE = {d: f"{d} - {nom}" for d, nom in DOMAINES_BPE.items()}
LABELS_VERS_DOMAINE = {label: d for d, label in LABELS_DOMAINE.items()}

COULEUR_SURLIGNAGE_MODIFIE = "#0d9488"  # bleu sarcelles


def _tableau_surligne(edite, defaut):
    """Styler en lecture seule reprenant `edite`, cellules qui diffèrent de
    `defaut` surlignées en bleu sarcelles — affiché en aperçu sous chaque
    st.data_editor, qui ne supporte pas nativement la coloration de cellules
    (seul Styler.format(), pas Styler.apply()/.map(), y est respecté ; la
    coloration ne fonctionne qu'avec st.dataframe, en lecture seule).

    Retourne None si rien n'a été modifié (pas d'aperçu à afficher).
    """
    masque = edite.ne(defaut)
    if not masque.to_numpy().any():
        return None

    def _surligner_colonne(colonne):
        style = f"background-color: {COULEUR_SURLIGNAGE_MODIFIE}; color: white;"
        return [style if modifie else "" for modifie in masque[colonne.name]]

    return edite.style.apply(_surligner_colonne, axis=0)


# Dossier des traces JSON de configurations de pondération testées (cf.
# section "Tester d'autres pondérations" en bas de page) — jamais data/ :
# ce dossier n'a pas vocation à être synchronisé avec le dataset HF partagé
# par l'app principale (accessibility-data), juste un historique local des
# essais faits sur ce Space dédié.
DOSSIER_PONDERATION_CUSTOM = os.path.join(os.getcwd(), "data_appli_jeu_ponderation_equi")


def _carte_accessibilite_45min_domaine(population_grid_agglo, land_use_data, ttm, domaine, fond_carte, carreaux_filtre_ids=None, canal_sync=None):
    """Carte HTML interactive (carte simple, pas de DualMap) du nombre de
    pôles d'équipements majeurs accessibles en 45 min pour un domaine BPE
    donné — variante à un seul cutoff de
    views.accessibilite_index._carte_poles_accessibles_domaine (qui calcule
    30 ET 45 min pour son DualMap 30/45 côte à côte) : ici seul 45 min est
    affiché, pas la peine de calculer 30 min pour rien.

    carreaux_filtre_ids : cf. _carte_poles_accessibles_domaine (même
    sémantique) — restreint les carreaux AFFICHÉS à cet ensemble d'id (ex:
    filtre par décile de niveau de vie), None = tous.

    canal_sync : cf. src.cartographie.script_synchroniser_zoom — si fourni,
    synchronise le zoom/centre de cette carte avec la carte de pondération
    du même domaine (views.accessibilite_urbaine_2.accessibilite_urbaine_page_2).

    Retourne (carte, bounds) — bounds = [[miny, minx], [maxy, maxx]] du
    cadrage initial de cette carte (limité aux carreaux d'ORIGINE de la
    matrice de temps de trajet ttm, donc plus restreint que l'étendue de
    population_grid_agglo), à réutiliser tel quel comme cadrage initial de la
    carte de pondération du même domaine (cf. _generer_cartes_domaines) :
    sans ce partage, les deux cartes démarrent sur des cadrages différents
    (population_grid_agglo comprend aussi des carreaux hors de la zone
    couverte par ttm), ce qui ne serait rattrapé par canal_sync qu'à la
    prochaine interaction de l'utilisateur, pas au chargement.
    (None, None) si le filtre ne laisse aucun carreau.
    """
    nom_domaine = DOMAINES_BPE.get(domaine, domaine)

    poles_domaine = land_use_data[["id", f"pole_equipements_{domaine}"]].rename(
        columns={f"pole_equipements_{domaine}": domaine}
    )

    cum_45 = cumulative_cutoff(ttm, land_use_data=poles_domaine, opportunity=domaine, travel_cost="travel_time", cutoff=45)

    carte_45 = population_grid_agglo[["id", "geometry"]].merge(cum_45, on="id")
    if carreaux_filtre_ids is not None:
        carte_45 = carte_45[carte_45["id"].isin(carreaux_filtre_ids)]
    if carte_45.empty:
        return None, None

    max_valeur = carte_45[domaine].max()
    carte = carte_45.explore(
        column=domaine,
        cmap="inferno",
        vmin=0,
        vmax=max_valeur,
        tiles=FONDS_CARTE[fond_carte],
        legend=False,  # légende maison ci-dessous (cf. echelle_continue_html)
        style_kwds={"weight": 0, "opacity": 0},
        prefer_canvas=True,
    )

    carte.get_root().html.add_child(
        folium.Element(titre_carte_html(f"Pôles d'équipements accessibles en 45 min – {nom_domaine}"))
    )
    carte.get_root().html.add_child(
        folium.Element(echelle_continue_html(0, max_valeur, "inferno", f"{nom_domaine} (pôles) – 45 min", cote="centre"))
    )

    minx, miny, maxx, maxy = carte_45.to_crs(epsg=4326).total_bounds
    bounds = [[miny, minx], [maxy, maxx]]
    carte.get_root().html.add_child(
        folium.Element(script_reajuster_si_masque(carte, bounds))
    )

    if canal_sync:
        carte.get_root().html.add_child(folium.Element(script_synchroniser_zoom(carte, canal_sync)))

    return carte, bounds


def _generer_cartes_domaines(population_grid_agglo, land_use_data, BPE_agglo, ttm, fond_carte, carreaux_filtre_ids, population_grid_agglo_filtre, id_session_carte, niveau_vie):
    """Construit les cartes (45 min + pondération) et le tableau par décile
    des 7 domaines BPE, pour l'état courant des paramètres (fond de carte,
    filtre déciles, pondération) — appelée uniquement au clic sur "✅ Valider
    et recalculer les cartes" (cf. accessibilite_urbaine_page_2), jamais à
    chaque rerun Streamlit : ces cartes sont coûteuses (cumulative_cutoff +
    rendu Leaflet x 2 x 7) et st.tabs garde tous les onglets dans le DOM en
    même temps, la reconstruction inconditionnelle à chaque interaction
    (changement de fond de carte, de filtre décile...) serait donc refaite
    7x2 fois pour rien à chaque clic.

    canal_sync (un par domaine ET par session, cf.
    src.cartographie.script_synchroniser_zoom) lie la carte 45 min et la
    carte pondérée d'un même domaine : zoomer/déplacer l'une déplace l'autre
    à l'identique. Les deux cartes partagent aussi le même cadrage initial
    (bounds retournés par _carte_accessibilite_45min_domaine) — sinon
    canal_sync ne rattraperait le cadrage de la carte de pondération
    (calculé sur une étendue plus large, cf. carte_ponderation_domaine) qu'à
    la première interaction de l'utilisateur, pas dès le chargement.

    Le tableau par décile (% moyen de pôles atteignables, une colonne par
    durée de CUTOFFS_PCT_MOYEN_POLES) n'est PAS filtré par
    carreaux_filtre_ids/population_grid_agglo_filtre — décliner par décile
    EST le filtre, un filtre décile supplémentaire n'aurait pas de sens ici
    (cf. views.accessibilite_index, même logique).

    Retourne {domaine: (carte_45 ou None, carte_ponderation ou None,
    tableau_decile_poles)}.
    """
    cartes = {}
    deciles = sorted(niveau_vie["decile_niveau_vie"].unique())
    for domaine in DOMAINES_BPE:
        canal_sync = f"zoom_{id_session_carte}_{domaine}"

        carte_45, bounds_45 = _carte_accessibilite_45min_domaine(
            population_grid_agglo, land_use_data, ttm, domaine, fond_carte,
            carreaux_filtre_ids=carreaux_filtre_ids, canal_sync=canal_sync,
        )

        if population_grid_agglo_filtre.empty:
            carte_ponderation = None
        else:
            carte_ponderation = carte_ponderation_domaine(
                DOMAINES_BPE, population_grid_agglo_filtre, BPE_agglo, land_use_data, domaine,
                tiles=FONDS_CARTE[fond_carte], canal_sync=canal_sync, bounds=bounds_45,
            )

        # Un seul calcul de pct_poles_atteignables_par_carreau par
        # (domaine, cutoff), réutilisé pour les 10 déciles (cf. docstring de
        # pct_poles_atteignables_par_carreau : filtrer ttm est le plus coûteux).
        pct_par_cutoff = {
            cutoff: pct_poles_atteignables_par_carreau(land_use_data, ttm, domaine, cutoff)
            for cutoff in CUTOFFS_PCT_MOYEN_POLES
        }
        lignes_decile = [
            {
                "Décile": int(decile),
                **{
                    f"{cutoff} min": moyenne_ponderee_pct_poles(
                        pct_par_cutoff[cutoff],
                        carreaux_ids=niveau_vie.loc[niveau_vie["decile_niveau_vie"] == decile, "id"],
                    )
                    for cutoff in CUTOFFS_PCT_MOYEN_POLES
                },
            }
            for decile in deciles
        ]
        tableau_decile_poles = pd.DataFrame(lignes_decile).set_index("Décile")

        cartes[domaine] = (carte_45, carte_ponderation, tableau_decile_poles)

    return cartes


def accessibilite_urbaine_page_2():
    st.header("test sur les pondérations équipements")
    st.caption("🚌 Bus · 🚊 Tramway · 🚇 Métro · ⛴️ Ferry · 🚶 Piétons")

    if st.session_state.get("feed") is None:
        st.info("👆 Veuillez choisir un GTFS dans la barre latérale.")
        return

    nom_reseau_str = st.session_state.nom_reseau_str
    date_str = st.session_state.date_str
    zip_path = st.session_state.zip_path

    st.write(f"Réseau : **{nom_reseau_str}**")

    if "reseau_calcule_2" not in st.session_state:
        st.session_state.reseau_calcule_2 = None

    # Lancement automatique dès qu'un GTFS est chargé/changé, comme
    # accessibilite_index_page — ne recalcule que si ce réseau n'a pas déjà
    # été traité dans cette session.
    if st.session_state.reseau_calcule_2 != nom_reseau_str:
        try:
            population_grid_agglo, land_use_data, BPE_agglo, ttm = _construire_pipeline(
                zip_path, nom_reseau_str, date_str
            )
        except HorsMetropoleError as e:
            st.error(f"⚠ {e}")
            return
        except Exception as e:
            # str(e) peut être vide (ex. MemoryError() par défaut) : le type
            # de l'exception donne l'info utile dans ce cas.
            st.error(f"Erreur pendant le calcul : {type(e).__name__}: {e}")
            return

        st.session_state.reseau_calcule_2 = nom_reseau_str
        st.session_state.pipeline_data_2 = (population_grid_agglo, land_use_data, BPE_agglo, ttm)
        # Nouveau réseau : une pondération custom validée pour l'ancien
        # réseau n'a pas de sens ici (BPE_agglo différent) — repart sur la
        # pondération par défaut tant que rien n'est validé pour celui-ci.
        st.session_state.ponderation_custom_2 = None
        st.session_state.ponderation_custom_valeurs_2 = None
        # Cartes générées pour l'ancien réseau obsolètes — repart sur le
        # message "cliquez sur Valider et recalculer les cartes" ci-dessous
        # tant que l'utilisateur n'a pas cliqué ce bouton pour ce réseau (cf.
        # accessibilite_urbaine_page_2 : rien ne se calcule automatiquement).
        st.session_state.cartes_generees_2 = None

    population_grid_agglo, land_use_data_defaut, BPE_agglo_defaut, ttm = st.session_state.pipeline_data_2

    # Pondération custom validée (cf. section "Tester d'autres pondérations"
    # en bas de page) si présente pour ce réseau, sinon pondération par
    # défaut — les cartes ci-dessous reflètent donc la dernière config
    # validée par l'utilisateur, la pondération officielle tant que rien ne
    # l'a été.
    if st.session_state.get("ponderation_custom_2") is not None:
        BPE_agglo, land_use_data = st.session_state.ponderation_custom_2
    else:
        BPE_agglo, land_use_data = BPE_agglo_defaut, land_use_data_defaut

    st.success(f"✓ {len(population_grid_agglo)} carreaux actifs — matrice des temps de trajet prête.")

    fond_carte = st.selectbox(
        "Fond de carte", options=list(FONDS_CARTE.keys()), index=list(FONDS_CARTE.keys()).index("CartoDB Positron")
    )

    niveau_vie = deciles_niveau_vie(population_grid_agglo)
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

    # None (plutôt qu'un ensemble reprenant tous les déciles) quand rien
    # n'est exclu : garde aussi les carreaux sans donnée ind_snv publiée
    # (secret statistique, absents de niveau_vie donc d'aucun décile), qui
    # seraient sinon exclus des cartes même sans filtre actif.
    if set(deciles_selectionnes) == set(deciles_disponibles):
        carreaux_filtre_ids = None
        population_grid_agglo_filtre = population_grid_agglo
    else:
        carreaux_filtre_ids = set(
            niveau_vie.loc[niveau_vie["decile_niveau_vie"].isin(deciles_selectionnes), "id"]
        )
        population_grid_agglo_filtre = population_grid_agglo[population_grid_agglo["id"].isin(carreaux_filtre_ids)]

    st.markdown("### Accessibilité (45 min) et équipements pondérés, par domaine")

    # Identifiant unique à cette session Streamlit (pas au réseau ni à la
    # page) : les canaux BroadcastChannel qui lient chaque paire de cartes
    # (cf. _generer_cartes_domaines) sont visibles par tout document
    # same-origin, y compris d'autres onglets du navigateur ouverts sur ce
    # même Space — sans cet identifiant, deux sessions utilisateur
    # distinctes ouvertes en parallèle synchroniseraient leurs cartes entre
    # elles par erreur.
    if "id_session_carte_2" not in st.session_state:
        st.session_state.id_session_carte_2 = uuid.uuid4().hex

    # Aucun calcul automatique ici : les cartes ne sont (re)générées que par
    # le bouton "✅ Valider et recalculer les cartes" (section "Tester
    # d'autres pondérations" plus bas), seul déclencheur — y compris pour un
    # changement de fond de carte ou de filtre décile ci-dessus, qui ne
    # prend donc effet sur les cartes qu'au prochain clic sur ce bouton.
    cartes_deja_generees = st.session_state.get("cartes_generees_2") is not None
    cartes_par_domaine = st.session_state.get("cartes_generees_2") or {}
    if not cartes_deja_generees:
        st.info(
            "Cliquez sur \"✅ Valider et recalculer les cartes\" (section \"Tester d'autres "
            "pondérations\" en bas de page) pour générer les cartes ci-dessous — rien n'est "
            "calculé automatiquement."
        )

    onglets = st.tabs([f"{d} - {nom}" for d, nom in DOMAINES_BPE.items()])
    for onglet, domaine in zip(onglets, DOMAINES_BPE):
        with onglet:
            carte_45, carte_ponderation, _ = cartes_par_domaine.get(domaine, (None, None, None))
            colonne_gauche, colonne_droite = st.columns(2)

            # "Aucun carreau dans les déciles sélectionnés." seulement si des
            # cartes ont bien été générées (carte_45/carte_ponderation à None
            # dans ce cas précis à cause du filtre décile) — pas quand rien
            # n'a encore été généré du tout pour ce réseau (message déjà
            # affiché ci-dessus dans ce cas, pas la peine de le répéter par
            # domaine avec un message qui laisserait croire à un filtre trop
            # restrictif).
            message_vide = "Aucun carreau dans les déciles sélectionnés." if cartes_deja_generees else None

            with colonne_gauche:
                st.markdown("#### Accessibilité à 45 min")
                if carte_45 is None:
                    if message_vide:
                        st.info(message_vide)
                else:
                    st.components.v1.html(carte_45.get_root().render(), height=520, scrolling=False)

            with colonne_droite:
                st.markdown("#### Équipements pondérés")
                if carte_ponderation is None:
                    if message_vide:
                        st.info(message_vide)
                else:
                    st.components.v1.html(carte_ponderation.get_root().render(), height=520, scrolling=False)

    st.markdown("### Tester d'autres pondérations")
    st.caption(
        "Modifie les poids par gamme d'équipement et les seuils de détection des pôles "
        "puis valide pour recalculer les cartes ci-dessus de tous les domaines avec ces "
        "nouvelles valeurs — un seul jeu de pondération pour tous les onglets, pas un par "
        "domaine."
    )

    # Valeurs de référence pré-remplies dans les tableaux ci-dessous = celles
    # RÉELLEMENT utilisées en ce moment par l'appli pour ce réseau (celles
    # qui ont produit les cartes affichées plus haut) : la pondération
    # custom déjà validée cette session (ponderation_custom_valeurs_2,
    # stockée par le bouton "Valider et recalculer les cartes" plus bas) si
    # présente, sinon les valeurs par défaut de src/ponderation_bpe.py —
    # jamais figées sur ces dernières comme avant, qui pouvaient différer de
    # ce qui est effectivement affiché après une première validation.
    gammes_poids_actuelles, seuils_actuels = st.session_state.get("ponderation_custom_valeurs_2") or (
        GAMMES_POIDS_PAR_DOMAINE, SEUILS_DOMAINE
    )

    # st.data_editor conserve les éditions de l'utilisateur d'un run à
    # l'autre via sa key (editeur_poids_2/editeur_seuils_2) tant que la
    # session dure — repasser le tableau par défaut ici à chaque script run
    # ne réinitialise donc pas ce qui a déjà été édité.
    #
    # Index = libellés explicites (LABELS_DOMAINE, ex: "A - Services pour les
    # particuliers") plutôt que les seuls codes de domaine — reconvertis vers
    # ces codes (LABELS_VERS_DOMAINE) plus bas, au moment de reconstruire
    # gammes_poids_par_domaine_custom/seuils_domaine_custom.
    tableau_poids_defaut = pd.DataFrame(gammes_poids_actuelles).T
    tableau_poids_defaut.index = [LABELS_DOMAINE[d] for d in tableau_poids_defaut.index]
    tableau_poids_defaut.index.name = "Domaine"
    poids_edites = st.data_editor(tableau_poids_defaut, key="editeur_poids_2", use_container_width=True)

    apercu_poids = _tableau_surligne(poids_edites, tableau_poids_defaut)
    if apercu_poids is not None:
        st.caption("Aperçu en lecture seule — cellules modifiées par rapport aux valeurs actuelles de l'appli :")
        st.dataframe(apercu_poids, use_container_width=True)

    tableau_seuils_defaut = pd.DataFrame(
        [{"Domaine": LABELS_DOMAINE[d], "Seuil (x moyenne du domaine)": s} for d, s in seuils_actuels.items()]
    ).set_index("Domaine")
    seuils_edites = st.data_editor(tableau_seuils_defaut, key="editeur_seuils_2", use_container_width=True)

    apercu_seuils = _tableau_surligne(seuils_edites, tableau_seuils_defaut)
    if apercu_seuils is not None:
        st.caption("Aperçu en lecture seule — cellules modifiées par rapport aux valeurs actuelles de l'appli :")
        st.dataframe(apercu_seuils, use_container_width=True)

    colonne_valider, colonne_reset = st.columns(2)
    with colonne_valider:
        if st.button("✅ Valider et recalculer les cartes", use_container_width=True, type="primary"):
            # .to_dict() sur un DataFrame issu de st.data_editor renvoie des
            # scalaires numpy (int64...), pas nativement sérialisables en
            # JSON (contrairement à float64, sous-classe de float — mais pas
            # int64 de int) : .item() les reconvertit en types Python natifs.
            gammes_poids_par_domaine_custom = {
                LABELS_VERS_DOMAINE[label]: {gamme: poids.item() if hasattr(poids, "item") else poids for gamme, poids in poids_par_gamme.items()}
                for label, poids_par_gamme in poids_edites.to_dict(orient="index").items()
            }
            seuils_domaine_custom = {
                LABELS_VERS_DOMAINE[label]: seuil.item() if hasattr(seuil, "item") else seuil
                for label, seuil in seuils_edites["Seuil (x moyenne du domaine)"].to_dict().items()
            }

            with st.spinner("Recalcul de la pondération..."):
                BPE_agglo_custom, land_use_data_custom = reponderer_bpe_2(
                    BPE_agglo_defaut, land_use_data_defaut,
                    gammes_poids_par_domaine_custom, seuils_domaine_custom,
                )
            st.session_state.ponderation_custom_2 = (BPE_agglo_custom, land_use_data_custom)
            # Valeurs (pas seulement le résultat calculé) gardées à part :
            # c'est ce qui permet aux tableaux ci-dessus de se pré-remplir
            # avec la pondération réellement active plutôt que de toujours
            # repartir des valeurs par défaut de src/ponderation_bpe.py.
            st.session_state.ponderation_custom_valeurs_2 = (gammes_poids_par_domaine_custom, seuils_domaine_custom)

            # Seul point du fichier où les cartes sont (re)générées : avec
            # BPE_agglo_custom/land_use_data_custom fraîchement calculés
            # ci-dessus (pas encore dans st.session_state.ponderation_custom_2
            # au moment où accessibilite_urbaine_page_2 a lu BPE_agglo/
            # land_use_data plus haut) et le fond de carte/filtre décile
            # actuellement affichés.
            with st.spinner("Génération des cartes (accessibilité 45 min + équipements pondérés) pour les 7 domaines..."):
                st.session_state.cartes_generees_2 = _generer_cartes_domaines(
                    population_grid_agglo, land_use_data_custom, BPE_agglo_custom, ttm, fond_carte,
                    carreaux_filtre_ids, population_grid_agglo_filtre, st.session_state.id_session_carte_2,
                    niveau_vie,
                )

            os.makedirs(DOSSIER_PONDERATION_CUSTOM, exist_ok=True)
            chemin_json = os.path.join(DOSSIER_PONDERATION_CUSTOM, f"ponderation_custom_{nom_reseau_str}.json")
            with open(chemin_json, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "horodatage": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "reseau": nom_reseau_str,
                        "gammes_poids_par_domaine": gammes_poids_par_domaine_custom,
                        "seuils_domaine": seuils_domaine_custom,
                    },
                    f, ensure_ascii=False, indent=2,
                )
            st.rerun()

    with colonne_reset:
        if st.button("↩️ Réinitialiser aux valeurs par défaut", use_container_width=True):
            st.session_state.ponderation_custom_2 = None
            st.session_state.ponderation_custom_valeurs_2 = None
            # Ne touche pas cartes_generees_2 : les cartes affichées restent
            # celles de la dernière validation tant que "✅ Valider et
            # recalculer les cartes" n'est pas recliqué (avec, cette fois,
            # les tableaux ci-dessus revenus aux valeurs par défaut) — ce
            # bouton ne fait que réinitialiser le formulaire, il ne
            # recalcule rien lui-même (seul "Valider et recalculer les
            # cartes" le fait, cf. accessibilite_urbaine_page_2).
            st.rerun()

    st.markdown("### Répartition par décile de niveau de vie")
    st.caption(
        "Pour chaque domaine, % moyen de pôles d'équipements majeurs atteignables (pondéré "
        "par la population du décile), par décile de niveau de vie (D1 = plus modeste, D10 = "
        "plus aisé) et par durée de trajet — même graphique que l'onglet Accessibilité de "
        "l'application complète."
    )

    onglets_decile = st.tabs([f"{d} - {nom}" for d, nom in DOMAINES_BPE.items()])
    for onglet, domaine in zip(onglets_decile, DOMAINES_BPE):
        with onglet:
            _, _, tableau_decile_poles = cartes_par_domaine.get(domaine, (None, None, None))
            if tableau_decile_poles is None:
                st.info(
                    "Cliquez sur \"✅ Valider et recalculer les cartes\" ci-dessus pour générer "
                    "ce graphique."
                )
            else:
                _courbe_pct_moyen_poles(
                    tableau_decile_poles,
                    f"% moyen de pôles atteignables par décile de niveau de vie – {DOMAINES_BPE[domaine]}",
                    "Décile (D1=modeste, D10=aisé)",
                    cmap="viridis",
                )
