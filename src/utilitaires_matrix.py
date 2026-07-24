import math

import numpy as np
import pandas as pd


def charger_ttm(ttm_path):
    """Charge une matrice de temps de trajet depuis un parquet (ttm_path)
    avec des dtypes compacts : from_id/to_id (identifiants de carreau INSEE,
    des chaînes comme "CRS3035RES200mN...") en category plutôt qu'en str, et
    travel_time en float32. À utiliser partout où un ttm déjà calculé est
    relu depuis le disque (cache local ou Hugging Face) — jamais via un
    simple pd.read_parquet(ttm_path).

    Sans ça : pd.read_parquet() décode par défaut les identifiants en objets
    Python un par un (str) et travel_time en float64. Le fichier reste petit
    sur disque (parquet compresse/dictionnarise déjà les identifiants
    répétés), mais une fois décompressé ainsi en mémoire ça explose — mesuré
    à 460 Mo sur disque -> 17,75 Go en mémoire pour Toulouse/Tisséo (211M
    lignes), largement de quoi dépasser à lui seul la limite mémoire du
    Space (32 Go), *avant* tout calcul d'indicateur ou rendu de carte. En
    passant les colonnes id par pyarrow.Table.to_pandas(categories=...)
    (qui les matérialise directement en category, sans jamais créer le
    tableau de chaînes intermédiaire) et en castant travel_time en float32
    avant la conversion pandas, le même fichier ne pèse plus que ~1,7 Go en
    mémoire — vérifié identique numériquement aux calculs en aval
    (cumulative_cutoff, calculer_index_benchmark, cost_to_closest...), qui
    n'exigent pas de dtype particulier sur from_id/to_id (isin/merge/map/
    groupby fonctionnent pareil en category qu'en str).
    """
    import pyarrow.parquet as pq

    table = pq.read_table(ttm_path)
    index_travel_time = table.column_names.index("travel_time")
    table = table.set_column(index_travel_time, "travel_time", table.column("travel_time").cast("float32"))
    return table.to_pandas(categories=["from_id", "to_id"])


def calculer_ttm_par_lots(
    r5py_module,
    transport_network,
    points,
    departure,
    transport_modes,
    max_time_walking,
    max_time,
    ttm_path,
    taille_lot=1500,
    on_step=None,
):
    """Calcule la TravelTimeMatrix r5py par lots d'origines plutôt qu'en un
    seul appel origins=tous x destinations=tous : le pic mémoire (JVM +
    DataFrame résultat côté Python) est alors borné à la taille d'un lot
    plutôt qu'à la matrice complète. Nécessaire pour les grosses agglomérations
    (ex: Lyon/TCL, dont le calcul en un seul bloc dépasse 32 Go de RAM même
    avec 16 Go dédiés à la JVM — cf. views/accessibilite_index.py).

    Écrit directement sur disque au fur et à mesure (pyarrow.parquet.ParquetWriter,
    un row group par lot) : un lot jamais ajouté au précédent en mémoire Python,
    contrairement à un accumulateur pandas (pd.concat) qui garderait la matrice
    complète en RAM au moment de l'écriture finale.

    r5py_module: module r5py déjà importé (passé en paramètre plutôt
        qu'importé ici, pour ne pas déclencher le démarrage de sa JVM par le
        seul fait d'importer ce module utilitaire — cf. _assurer_r5py_pret
        dans views/accessibilite_index.py).
    points: GeoDataFrame [id, geometry] (mêmes origines et destinations, comme
        pour un appel TravelTimeMatrix classique).
    on_step: callback optionnel appelé avec un message de progression avant
        chaque lot (ex. st.write côté Streamlit).
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    nb_lots = math.ceil(len(points) / taille_lot)
    writer = None
    try:
        for i in range(nb_lots):
            lot = points.iloc[i * taille_lot : (i + 1) * taille_lot]
            if on_step is not None:
                on_step(f"Calcul de la matrice des temps de trajet... lot {i + 1}/{nb_lots}")

            ttm_lot = r5py_module.TravelTimeMatrix(
                transport_network,
                origins=lot,
                destinations=points,
                transport_modes=transport_modes,
                departure=departure,
                max_time_walking=max_time_walking,
                max_time=max_time,
            )
            # float32 plutôt que le float64 par défaut : réduit la mémoire de
            # moitié sans changer le comportement NaN (r5py renvoie NaN pour
            # les paires non atteignables dans max_time — tout le reste du
            # pipeline compare ttm["travel_time"] <= cutoff en comptant sur
            # NaN <= cutoff -> False). Un entier ("Int16" nullable) casserait
            # ces comparaisons (NA <= cutoff lève une erreur au lieu de False)
            # — testé, cf. IntCastingNaNError rencontrée avec un int16 simple.
            ttm_lot["travel_time"] = ttm_lot["travel_time"].astype("float32")

            table = pa.Table.from_pandas(ttm_lot, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(ttm_path, table.schema)
            writer.write_table(table)
    finally:
        if writer is not None:
            writer.close()


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
    BPE_agglo,
    land_use_data,
    ttm,
    DOMAINES_BPE,
    niveau_vie,
    cutoffs=(30, 45, 60),
    seuils=(25, 50, 75),
    max_time=120,
    on_step=None,
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

    on_step: callback optionnel appelé avec un message de progression avant
        chaque domaine (ex. print côté notebook, ou pour retrouver dans les
        logs serveur — invisibles autrement — quel domaine était en cours
        lors d'un crash mémoire côté app).
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

    # Filtré une fois par cutoff, pas une fois par (domaine, cutoff) : le
    # filtre travel_time <= cutoff ne dépend pas du domaine, mais
    # cumulative_cutoff() (appelée ci-dessous par domaine) le refaisait sur
    # ttm en entier à chaque appel — 8 domaines x 3 cutoffs = 24 filtrages de
    # la matrice complète au lieu de 3. Sur une grosse agglomération (ex.
    # Toulouse), ttm est énorme : ce x8 de calcul/mémoire redondant a fait
    # dépasser la limite mémoire du Space (32 Go).
    ttm_par_cutoff = {cutoff: ttm.loc[ttm["travel_time"] <= cutoff, ["from_id", "to_id"]] for cutoff in cutoffs}

    lignes = []
    for d, nom_domaine in DOMAINES_BPE.items():
        if on_step is not None:
            on_step(f"Indicateurs de benchmark... domaine {d} ({nom_domaine})")
        land_use_data_d = land_use_data_domaine(BPE_agglo, land_use_data, d)
        total_equipement_d = land_use_data_d[d].sum()
        if total_equipement_d == 0:
            continue

        # % moyen des équipements pondérés accessibles par cutoff, à partir du
        # sous-ensemble déjà filtré par cutoff (ttm_par_cutoff) plutôt que de
        # rappeler cumulative_cutoff() sur ttm en entier — même résultat que
        # cumulative_cutoff(ttm, land_use_data=land_use_data_d, opportunity=d,
        # travel_cost="travel_time", cutoff=cutoff), sans refiltrer ttm.
        pct_par_carreau_cutoff = {}
        for cutoff in cutoffs:
            cum = (
                ttm_par_cutoff[cutoff]
                .merge(land_use_data_d[["id", d]], left_on="to_id", right_on="id", how="left")
                .groupby("from_id")[d]
                .sum()
                .reindex(land_use_data["id"])
                .fillna(0)
            )
            pct_par_carreau_cutoff[cutoff] = 100 * cum / total_equipement_d

        # Temps pour atteindre chaque seuil : cumul croissant par temps de
        # trajet croissant, par carreau d'origine.
        #
        # Filtre par isin()+map() plutôt qu'un merge (ttm.merge(..., on="to_id",
        # how="inner")) : un merge construit une jointure sur ttm en entier (non
        # borné par un cutoff, jusqu'à max_time=120 min — bien plus gros que les
        # ttm_par_cutoff ci-dessus), refait 8 fois (une par domaine). isin()+map()
        # ne scanne ttm qu'en O(n) sans construire de structure de jointure,
        # nettement moins coûteux en mémoire sur une grosse agglomération (même
        # cause que le fix ttm_par_cutoff plus haut : Toulouse dépassait encore
        # les 32 Go du Space après ce premier fix, précisément sur ce bloc-ci).
        equipement_par_destination = land_use_data_d.loc[land_use_data_d[d] > 0].set_index("id")[d]
        trajets_d = ttm.loc[ttm["to_id"].isin(equipement_par_destination.index), ["from_id", "to_id", "travel_time"]]
        trajets_d = trajets_d.assign(**{d: trajets_d["to_id"].map(equipement_par_destination)})
        trajets_d = trajets_d.sort_values(["from_id", "travel_time"])
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