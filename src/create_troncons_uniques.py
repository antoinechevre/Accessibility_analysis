"""
Création optimisée des tronçons uniques GTFS
Tronçons par mode de transport, deux sens confondus, à la station parent
"""

import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString


def creer_troncons_uniques(feed, route_type):
    """
    Crée un GeoDataFrame des tronçons uniques pour un type de route donné.

    Tronçons uniques = paires d'arrêts parents, tous sens confondus, sans distinction de route.

    Parameters:
    -----------
    feed : gtfs_kit Feed object
        Feed GTFS chargé
    route_type : int
        Type de route GTFS (0=tram, 3=bus, etc.)

    Returns:
    --------
    GeoDataFrame avec les tronçons uniques
    """
    print(f"\nCréation des tronçons uniques pour route_type={route_type}...")

    # 1. Préparer le mapping vers les parent_stations
    stops = feed.stops.copy()
    if "parent_station" not in stops.columns:
        stops["parent_station"] = stops["stop_id"]
    else:
        stops["parent_station"] = stops["parent_station"].fillna(stops["stop_id"])
        stops.loc[stops["parent_station"] == "", "parent_station"] = stops["stop_id"]

    # Mapping stop_id -> parent_station
    stop_to_parent = stops.set_index("stop_id")["parent_station"].to_dict()

    # Infos des parents (coords, noms)
    parent_info = (
        stops[stops["stop_id"] == stops["parent_station"]]
        .set_index("stop_id")[["stop_name", "stop_lat", "stop_lon"]]
        .to_dict("index")
    )

    # 2. Filtrer les trips du bon type de route
    routes_filtrees = feed.routes[feed.routes["route_type"] == route_type]["route_id"]
    trips_filtres = feed.trips[feed.trips["route_id"].isin(routes_filtrees)]

    # 3. Joindre stop_times avec les trips filtrés
    stop_times = feed.stop_times.merge(
        trips_filtres[["trip_id"]], on="trip_id", how="inner"
    )

    print(f"  → {len(trips_filtres)} trips, {len(stop_times)} stop_times")

    # 4. Mapper vers parent_stations
    stop_times["stop_parent"] = stop_times["stop_id"].map(stop_to_parent)

    # 5. Trier par trip et séquence
    stop_times = stop_times.sort_values(["trip_id", "stop_sequence"])

    # 6. Créer les paires d'arrêts consécutifs
    print("  → Création des paires d'arrêts consécutifs...")

    # Décalage pour obtenir l'arrêt suivant
    stop_times["stop_parent_suivant"] = stop_times.groupby("trip_id")[
        "stop_parent"
    ].shift(-1)

    # Supprimer les derniers arrêts de chaque trip (pas de suivant)
    paires = stop_times.dropna(subset=["stop_parent_suivant"]).copy()

    # 7. Créer une clé unique pour chaque paire (tous sens confondus)
    print("  → Normalisation des paires (tous sens confondus)...")

    paires["stop_pair"] = paires.apply(
        lambda row: tuple(sorted([row["stop_parent"], row["stop_parent_suivant"]])),
        axis=1,
    )

    # 8. Dédupliquer pour obtenir les tronçons uniques
    troncons_uniques = paires[["stop_pair"]].drop_duplicates().reset_index(drop=True)

    print(f"  → {len(troncons_uniques)} tronçons uniques identifiés")

    colonnes_finales = [
        "troncon_unique_id",
        "stop_depart_parent_id",
        "stop_arrivee_parent_id",
        "stop_depart_name",
        "stop_arrivee_name",
        "lat_depart_parent",
        "lon_depart_parent",
        "lat_arrivee_parent",
        "lon_arrivee_parent",
        "geometry",
    ]

    if troncons_uniques.empty:
        # Aucun trip pour ce route_type (ex : réseau sans trolleybus) : sur un
        # DataFrame à 0 ligne, .apply(axis=1) ne produit pas de colonne
        # exploitable, d'où le GeoDataFrame vide construit directement ici.
        gdf = gpd.GeoDataFrame(columns=colonnes_finales, geometry="geometry", crs="EPSG:4326")
        print(f"✓ {len(gdf)} tronçons uniques créés")
        return gdf

    # 9. Enrichir avec les informations des arrêts
    print("  → Enrichissement avec coordonnées et noms...")

    def enrichir_troncon(stop_pair):
        """Enrichit un tronçon avec les infos des deux arrêts"""
        stop1, stop2 = stop_pair

        # Infos du parent 1
        info1 = parent_info.get(stop1, {})
        # Infos du parent 2
        info2 = parent_info.get(stop2, {})

        return {
            "stop_depart_parent_id": stop1,
            "stop_arrivee_parent_id": stop2,
            "stop_depart_name": info1.get("stop_name", ""),
            "stop_arrivee_name": info2.get("stop_name", ""),
            "lat_depart_parent": info1.get("stop_lat", None),
            "lon_depart_parent": info1.get("stop_lon", None),
            "lat_arrivee_parent": info2.get("stop_lat", None),
            "lon_arrivee_parent": info2.get("stop_lon", None),
        }

    # Appliquer l'enrichissement
    infos_enrichies = troncons_uniques["stop_pair"].apply(enrichir_troncon)
    df_enrichi = pd.DataFrame(infos_enrichies.tolist())

    # Combiner avec l'index original
    troncons_uniques = pd.concat([troncons_uniques, df_enrichi], axis=1)

    # 10. Générer les identifiants et géométries
    print("  → Génération des identifiants et géométries...")

    # Identifiants uniques
    route_type_prefix = (
        "METRP" if route_type == 1 else "TRAM" if route_type == 0 else "BUS" if route_type == 3 else f"RT{route_type}"
    )
    troncons_uniques["troncon_unique_id"] = [
        f"TU_{route_type_prefix}_{i:06d}" for i in range(len(troncons_uniques))
    ]

    # Géométries LineString
    troncons_uniques["geometry"] = troncons_uniques.apply(
        lambda row: (
            LineString(
                [
                    (row["lon_depart_parent"], row["lat_depart_parent"]),
                    (row["lon_arrivee_parent"], row["lat_arrivee_parent"]),
                ]
            )
            if all(
                pd.notna(
                    [
                        row["lon_depart_parent"],
                        row["lat_depart_parent"],
                        row["lon_arrivee_parent"],
                        row["lat_arrivee_parent"],
                    ]
                )
            )
            else None
        ),
        axis=1,
    )

    # 11. Créer le GeoDataFrame
    gdf = gpd.GeoDataFrame(
        troncons_uniques[colonnes_finales], geometry="geometry", crs="EPSG:4326"
    )

    # Supprimer la colonne temporaire stop_pair
    if "stop_pair" in gdf.columns:
        gdf = gdf.drop(columns=["stop_pair"])

    print(f"✓ {len(gdf)} tronçons uniques créés")

    return gdf


# =============================================================================
# EXEMPLE D'UTILISATION
# =============================================================================

if __name__ == "__main__":
    from utils import charger_gtfs, exporter_gdf_to_csv

    # Charger le feed GTFS
    feed = charger_gtfs()

    # Créer les tronçons uniques pour bus et tram
    print("=" * 70)
    print("CRÉATION DES TRONÇONS UNIQUES")
    print("=" * 70)

    # Bus (route_type = 3)
    troncons_bus = creer_troncons_uniques(feed, route_type=3)
    exporter_gdf_to_csv(troncons_bus, "output/troncons_uniques_bus.csv")
    # exporter_geojson(troncons_bus, 'output/troncons_uniques_bus.geojson')

    # Tram (route_type = 0)
    troncons_tram = creer_troncons_uniques(feed, route_type=0)
    exporter_gdf_to_csv(troncons_tram, "output/troncons_uniques_tram.csv")
    # exporter_geojson(troncons_tram, 'output/troncons_uniques_tram2.geojson')

    # Metro (route_type = 1)
    troncons_metro = creer_troncons_uniques(feed, route_type=1)
    exporter_gdf_to_csv(troncons_metro, "output/troncons_uniques_metro.csv")
    # exporter_geojson(troncons_metro, 'output/troncons_uniques_metro2.geojson')

    # Trolley (route_type = 11)
    troncons_trolley = creer_troncons_uniques(feed, route_type=11)
    exporter_gdf_to_csv(troncons_trolley, "output/troncons_uniques_trolley.csv")
    # exporter_geojson(troncons_trolley, 'output/troncons_uniques_trolley2.geojson')

    # Ferry (route_type = 4)
    troncons_ferry = creer_troncons_uniques(feed, route_type=4)
    exporter_gdf_to_csv(troncons_ferry, "output/troncons_uniques_ferry.csv")
    # exporter_geojson(troncons_trolley, 'output/troncons_uniques_ferry2.geojson')


    print("\n" + "=" * 70)
    print("✓ TRAITEMENT TERMINÉ")
    print("=" * 70)
