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