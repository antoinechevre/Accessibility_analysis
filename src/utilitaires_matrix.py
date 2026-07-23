import numpy as np
import pandas as pd


def pct_poles_atteignables_par_carreau(land_use_data, ttm, domaine, cutoff):
    """DataFrame [id, population, pct_poles] : % (0-100) des pôles
    d'équipements majeurs du domaine (land_use_data[f"pole_equipements_{domaine}"],
    cf. SEUILS_DOMAINE dans src/pipeline_donnees.py) atteignables en <= cutoff
    minutes depuis chaque carreau.

    Calcul séparé de moyenne_ponderee_pct_poles() ci-dessous (plutôt qu'une
    seule fonction bornes+moyenne) pour ne filtrer ttm qu'une fois par
    (domaine, cutoff) même quand on a besoin de la moyenne sur plusieurs
    sous-ensembles de carreaux (ex: par décile de niveau de vie)."""
    poles = set(land_use_data.loc[land_use_data[f"pole_equipements_{domaine}"] == 1, "id"])
    base = land_use_data[["id", "population"]].copy()

    if not poles:
        base["pct_poles"] = float("nan")
        return base

    nb_poles_atteignables = (
        ttm.loc[ttm["to_id"].isin(poles) & (ttm["travel_time"] <= cutoff)]
        .groupby("from_id")["to_id"]
        .nunique()
    )

    base = base.merge(
        nb_poles_atteignables.rename("nb_poles").reset_index().rename(columns={"from_id": "id"}),
        on="id",
        how="left",
    )
    # Carreaux absents de nb_poles_atteignables : aucun pôle atteignable (0), pas NaN.
    base["nb_poles"] = base["nb_poles"].fillna(0)
    base["pct_poles"] = 100 * base["nb_poles"] / len(poles)
    return base.drop(columns="nb_poles")


def moyenne_ponderee_pct_poles(pct_par_carreau, carreaux_ids=None):
    """Moyenne pondérée par la population de pct_par_carreau["pct_poles"]
    (issu de pct_poles_atteignables_par_carreau), restreinte à carreaux_ids
    si fourni (sinon calculée sur toute la population)."""
    base = pct_par_carreau
    if carreaux_ids is not None:
        base = base[base["id"].isin(carreaux_ids)]
    population_totale = base["population"].sum()
    if population_totale == 0:
        return float("nan")
    return (base["pct_poles"] * base["population"]).sum() / population_totale


def deciles_niveau_vie(population_grid_agglo):
    """DataFrame [id, ind_snv, decile_niveau_vie] : décile de niveau de vie
    (ind_snv, Filosofi INSEE) par carreau. Carreaux sans donnée publiée
    (ind_snv <= 0, secret statistique) exclus : les inclure fausserait le
    décile le plus pauvre avec des carreaux "sans donnée"."""
    niveau_vie = population_grid_agglo.loc[population_grid_agglo["ind_snv"] > 0, ["id", "ind_snv"]].copy()
    niveau_vie["decile_niveau_vie"] = pd.qcut(niveau_vie["ind_snv"], 10, labels=False, duplicates="drop") + 1
    return niveau_vie


def calculer_index_benchmark(
    BPE_agglo, land_use_data, ttm, DOMAINES_BPE, niveau_vie, cutoffs=(30, 45, 60), seuils=(25, 50, 75), max_time=120
):
    """Indicateurs de benchmark inter-réseaux, par domaine BPE et par
    groupe de carreaux ("Tous" + un par décile de niveau de vie) :

    - pct_equipement_pondere_<cutoff>min : % moyen (pondéré par la
      population du carreau d'origine) des équipements pondérés du domaine
      (poids_gamme — PAS restreint aux "pôles" majeurs, contrairement à
      pct_poles_atteignables_par_carreau ci-dessus) accessibles en <=
      cutoff minutes.
    - temps_atteinte_<seuil>pct_min : temps moyen (idem, pondéré population)
      pour atteindre au moins seuil % du total des équipements pondérés du
      domaine — inverse de cumulative_cutoff (qui fixe le temps et mesure
      la quantité atteinte) : ici on fixe la quantité et on mesure le
      temps. Carreaux qui n'atteignent jamais le seuil dans la limite de la
      matrice : plafonnés à max_time plutôt qu'exclus (comme TMISA, cf.
      notebook section 9.3), pour ne pas biaiser la moyenne vers le bas.

    Un DataFrame en sortie (une ligne par domaine x groupe), colonnes
    domaine/nom_domaine/decile + les indicateurs ci-dessus — sans colonnes
    de métadonnées de run (réseau, date, ville principale, population
    totale : à l'appelant de les ajouter avant sauvegarde, cf.
    src.hf_cache.fusionner_et_envoyer_csv). Pensé pour être appelé de la
    même façon depuis le notebook et depuis l'app Streamlit.
    """
    from src.BPE_traitement import land_use_data_domaine

    def _moyenne_ponderee_carreaux(valeurs_par_carreau, carreaux_ids=None, valeur_defaut=0.0):
        base = land_use_data[["id", "population"]].set_index("id")
        if carreaux_ids is not None:
            base = base[base.index.isin(carreaux_ids)]
        valeurs = valeurs_par_carreau.reindex(base.index).fillna(valeur_defaut)
        population_totale = base["population"].sum()
        if population_totale == 0:
            return float("nan")
        return (valeurs * base["population"]).sum() / population_totale

    lignes = []
    for d, nom_domaine in DOMAINES_BPE.items():
        land_use_data_d = land_use_data_domaine(BPE_agglo, land_use_data, d)
        total_equipement_d = land_use_data_d[d].sum()
        if total_equipement_d == 0:
            continue

        # % moyen des équipements pondérés accessibles par cutoff (réutilise
        # cumulative_cutoff, cf. plus haut dans ce module).
        pct_par_carreau_cutoff = {}
        for cutoff in cutoffs:
            cum = cumulative_cutoff(
                ttm, land_use_data=land_use_data_d, opportunity=d, travel_cost="travel_time", cutoff=cutoff
            )
            pct_par_carreau_cutoff[cutoff] = 100 * cum.set_index("id")[d] / total_equipement_d

        # Temps pour atteindre chaque seuil : cumul croissant par temps de
        # trajet croissant, par carreau d'origine.
        equipement_par_destination = land_use_data_d.loc[land_use_data_d[d] > 0, ["id", d]].rename(
            columns={"id": "to_id"}
        )
        trajets_d = ttm.merge(equipement_par_destination, on="to_id", how="inner").sort_values(
            ["from_id", "travel_time"]
        )
        trajets_d["cumule_pct"] = 100 * trajets_d.groupby("from_id")[d].cumsum() / total_equipement_d

        temps_par_carreau_seuil = {}
        for seuil in seuils:
            temps_par_carreau_seuil[seuil] = (
                trajets_d.loc[trajets_d["cumule_pct"] >= seuil].groupby("from_id")["travel_time"].min()
            )

        groupes_decile = {"Tous": None}
        for decile in sorted(niveau_vie["decile_niveau_vie"].unique()):
            groupes_decile[f"D{int(decile)}"] = niveau_vie.loc[niveau_vie["decile_niveau_vie"] == decile, "id"]

        for nom_groupe, carreaux_ids in groupes_decile.items():
            ligne = {"domaine": d, "nom_domaine": nom_domaine, "decile": nom_groupe}
            for cutoff in cutoffs:
                ligne[f"pct_equipement_pondere_{cutoff}min"] = _moyenne_ponderee_carreaux(
                    pct_par_carreau_cutoff[cutoff], carreaux_ids=carreaux_ids, valeur_defaut=0.0
                )
            for seuil in seuils:
                ligne[f"temps_atteinte_{seuil}pct_min"] = _moyenne_ponderee_carreaux(
                    temps_par_carreau_seuil[seuil], carreaux_ids=carreaux_ids, valeur_defaut=max_time
                )
            lignes.append(ligne)

    return pd.DataFrame(lignes)


def cumulative_cutoff(travel_time_matrix, land_use_data, opportunity, travel_cost, cutoff):
    """Équivalent minimal de accessibility::cumulative_cutoff()."""
    reachable = travel_time_matrix[travel_time_matrix[travel_cost] <= cutoff]

    merged = reachable.merge(
        land_use_data[["id", opportunity]],
        left_on="to_id",
        right_on="id",
        how="left",
    )

    result = merged.groupby("from_id")[opportunity].sum().reset_index()
    result = result.rename(columns={"from_id": "id"})

    # les origines sans aucune destination atteignable ont une accessibilité de 0
    result = land_use_data[["id"]].merge(result, on="id", how="left")
    result[opportunity] = result[opportunity].fillna(0).astype(int)

    return result


def cost_to_closest(land_use_data_domaine,BPE_agglo,_land_use_data_global, DOMAINES_BPE,travel_time_matrix, opportunity, travel_cost, land_use_data=None, n=1):
    """
    Équivalent minimal de accessibility::cost_to_closest().

    Si land_use_data n'est pas fourni, "opportunity" est interprété comme un
    domaine BPE (A-G, ou "O" pour tous) : land_use_data est alors construit
    automatiquement via land_use_data_domaine(opportunity).
    """
    if land_use_data is None:
        land_use_data = land_use_data_domaine(BPE_agglo, _land_use_data_global, opportunity)

    has_opportunity = land_use_data.loc[land_use_data[opportunity] >= n, "id"]
    reachable = travel_time_matrix[travel_time_matrix["to_id"].isin(has_opportunity)]

    result = reachable.groupby("from_id")[travel_cost].min().reset_index()
    result = result.rename(columns={"from_id": "id"})

    result = land_use_data[["id"]].merge(result, on="id", how="left")
    result[travel_cost] = result[travel_cost].fillna(float("inf"))

    nom_opportunity = DOMAINES_BPE.get(opportunity, opportunity)
    print(f"min_time calculé pour accéder à : {nom_opportunity}")

    return result



def decay_exponential(decay_value):
    """Équivalent de accessibility::decay_exponential() : poids = exp(-decay * coût)."""

    def decay(travel_cost):
        return np.exp(-decay_value * travel_cost)

    return decay


def gravity(travel_time_matrix, land_use_data, opportunity, travel_cost, decay_function):
    """Équivalent minimal de accessibility::gravity()."""
    merged = travel_time_matrix.merge(
        land_use_data[["id", opportunity]],
        left_on="to_id",
        right_on="id",
        how="left",
    )
    merged["weighted_opportunity"] = decay_function(merged[travel_cost]) * merged[opportunity]

    result = merged.groupby("from_id")["weighted_opportunity"].sum().reset_index()
    result = result.rename(columns={"from_id": "id", "weighted_opportunity": opportunity})

    result = land_use_data[["id"]].merge(result, on="id", how="left")
    result[opportunity] = result[opportunity].fillna(0.0)

    return result



def enhanced_2sfca(travel_time_matrix, land_use_data, opportunity, travel_cost, demand, decay_function):
    """Enhanced 2SFCA (Luo & Qi, 2009)."""
    demand_col = land_use_data[["id", demand]].rename(columns={"id": "from_id", demand: "demand"})
    supply_col = land_use_data[["id", opportunity]].rename(columns={"id": "to_id", opportunity: "supply"})

    matrix = travel_time_matrix.merge(demand_col, on="from_id").merge(supply_col, on="to_id")
    matrix["weight"] = decay_function(matrix[travel_cost])

    # étape 1 : ratio offre/demande par destination, pondéré par la décroissance
    matrix["demand_weighted"] = matrix["demand"] * matrix["weight"]
    demande_ponderee = matrix.groupby("to_id")["demand_weighted"].sum()

    ratio = supply_col.set_index("to_id")["supply"] / demande_ponderee
    ratio = ratio.replace([np.inf, -np.inf], np.nan).fillna(0.0).rename("ratio")

    matrix = matrix.merge(ratio, on="to_id", how="left")
    matrix["ratio"] = matrix["ratio"].fillna(0.0)

    # étape 2 : accessibilité = somme des ratios pondérés par la décroissance depuis chaque origine
    matrix["accessibilite_ponderee"] = matrix["ratio"] * matrix["weight"]
    result = matrix.groupby("from_id")["accessibilite_ponderee"].sum().reset_index()
    result = result.rename(columns={"from_id": "id", "accessibilite_ponderee": opportunity})

    result = land_use_data[["id"]].merge(result, on="id", how="left")
    result[opportunity] = result[opportunity].fillna(0.0)

    return result