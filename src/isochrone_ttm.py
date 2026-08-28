"""
Isochrone "réelle" à partir d'une matrice de temps de trajet (ttm)
carreau->carreau pré-calculée par l'app sœur Accessibility_analysis
(github.com/antoinechevre/Accessibility_analysis, moteur r5py — routage
multimodal marche+transport combinant réellement OSM et GTFS), partagée
sur le dataset HF antoinechevre/accessibility-data sous
memory_ttm/ttm_<nom_reseau_str>.parquet.

Onglet expérimental (cf. views/isochrone_ttm_test.py) : n'existe que pour
les réseaux dont Accessibility_analysis a déjà calculé un ttm — le nom de
réseau doit correspondre exactement (ex: "Châlons-en-Champagne") — pas de
calcul à la volée ici, r5py (JVM, extraction OSM) n'est pas une dépendance
de cette appli. Contrairement à l'onglet Isochrone (RAPTOR simplifié, cf.
src/isochrone.py) : granularité carreau INSEE 200m plutôt qu'arrêt,
horaire de départ figé à celui du calcul du ttm (14h le jour JOB du réseau,
cf. Accessibility_analysis/views/accessibilite_index.py) plutôt que choisi
librement, mais correspondances et temps de marche réels (r5py) plutôt
qu'approximés (même arrêt uniquement / cercle de marche).
"""

import os

import branca.colormap as cm
import folium
import geopandas as gpd
from shapely.geometry import Point, box

from src.cartographie import tile_layer_cartodb
from src.hf_cache import recuperer_depuis_hf
from src.insee_carreaux import INSEE_CARREAUX_HF_PATH, INSEE_CARREAUX_LOCAL_PATH, ajouter_couche_carreaux_insee
from src.isochrone import DUREE_COLOR_BANDES, DUREE_COLOR_SEUILS

TTM_HF_DIR = "memory_ttm"
TTM_LOCAL_DIR = os.path.join("data", "memory_ttm")

# Large marge autour de l'arrêt de départ pour couvrir le ttm (échelle
# départementale) sans avoir à connaître son étendue exacte à l'avance.
MARGE_BBOX_CARREAUX_DEG = 0.3


def ttm_disponible(nom_reseau_str):
    """Télécharge (si besoin, best-effort) et charge le ttm carreau->carreau
    de nom_reseau_str, ou None si Accessibility_analysis n'en a pas calculé
    sous ce nom de réseau exact."""
    nom_fichier = f"ttm_{nom_reseau_str}.parquet"
    chemin_local = os.path.join(TTM_LOCAL_DIR, nom_fichier)
    nom_fichier_hf = f"{TTM_HF_DIR}/{nom_fichier}"

    if not recuperer_depuis_hf(nom_fichier_hf, chemin_local):
        return None

    import pyarrow.parquet as pq

    table = pq.read_table(chemin_local)
    index_travel_time = table.column_names.index("travel_time")
    table = table.set_column(index_travel_time, "travel_time", table.column("travel_time").cast("float32"))
    return table.to_pandas(categories=["from_id", "to_id"])


def trouver_carreau_origine(lat, lon):
    """Retrouve l'idcar_200m (même format que from_id/to_id du ttm,
    "CRS3035RES200mN...E...") du carreau INSEE contenant (lat, lon), ou
    None si hors grille (zone non peuplée, en mer...) ou si les carreaux
    INSEE sont indisponibles."""
    if not recuperer_depuis_hf(INSEE_CARREAUX_HF_PATH, INSEE_CARREAUX_LOCAL_PATH):
        return None

    bbox_geom = gpd.GeoSeries(
        [box(
            lon - MARGE_BBOX_CARREAUX_DEG, lat - MARGE_BBOX_CARREAUX_DEG,
            lon + MARGE_BBOX_CARREAUX_DEG, lat + MARGE_BBOX_CARREAUX_DEG,
        )],
        crs="EPSG:4326",
    )
    gdf = gpd.read_file(INSEE_CARREAUX_LOCAL_PATH, bbox=bbox_geom, columns=["idcar_200m", "geometry"])
    if gdf.empty:
        return None
    gdf = gdf.to_crs("EPSG:4326")
    match = gdf[gdf.geometry.contains(Point(lon, lat))]
    return match["idcar_200m"].iloc[0] if not match.empty else None


def carreaux_atteignables(ttm, origin_id, budget_min):
    """Sous-ensemble du ttm (idcar_200m, travel_time) atteignable depuis
    origin_id dans budget_min minutes (travel_time du ttm déjà en
    minutes)."""
    sous_ensemble = ttm[
        (ttm["from_id"] == origin_id) & ttm["travel_time"].notna() & (ttm["travel_time"] <= budget_min)
    ]
    return sous_ensemble[["to_id", "travel_time"]].rename(columns={"to_id": "idcar_200m"})


def charger_geometrie_carreaux(carreau_ids, lat, lon):
    """Charge la géométrie (polygones) des carreaux dont l'id est dans
    carreau_ids, dans une zone autour de (lat, lon)."""
    if not recuperer_depuis_hf(INSEE_CARREAUX_HF_PATH, INSEE_CARREAUX_LOCAL_PATH):
        return gpd.GeoDataFrame(columns=["idcar_200m", "geometry"])

    bbox_geom = gpd.GeoSeries(
        [box(
            lon - MARGE_BBOX_CARREAUX_DEG, lat - MARGE_BBOX_CARREAUX_DEG,
            lon + MARGE_BBOX_CARREAUX_DEG, lat + MARGE_BBOX_CARREAUX_DEG,
        )],
        crs="EPSG:4326",
    )
    gdf = gpd.read_file(INSEE_CARREAUX_LOCAL_PATH, bbox=bbox_geom, columns=["idcar_200m", "geometry"])
    gdf = gdf[gdf["idcar_200m"].isin(carreau_ids)]
    return gdf.to_crs("EPSG:4326")


def build_map_carreaux(origine, gdf_carreaux, budget_min, legende_duree="Temps de trajet (min)"):
    center = [origine["stop_lat"], origine["stop_lon"]]
    m = folium.Map(location=center, zoom_start=12, tiles=None, prefer_canvas=True, control_scale=True)
    # Fonds de carte empilés (rasters opaques) : le dernier ajouté est celui
    # visible par défaut. tile_layer_cartodb retombe sur OpenStreetMap si
    # CARTO_API_KEY n'est pas définie (cf. fond_carte_kwargs) — CartoDB
    # Positron en dernier pour rester le fond par défaut dans le cas normal.
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap", cross_origin=True).add_to(m)
    tile_layer_cartodb("CartoDB dark_matter", "CartoDB Dark Matter", cross_origin=True).add_to(m)
    tile_layer_cartodb("CartoDB positron", "CartoDB Positron", cross_origin=True).add_to(m)

    # Couche optionnelle (décochée par défaut) de densité de population par
    # carreau INSEE 200m — même mécanisme que create_carte_arrets/
    # creer_carte_troncons (src/cartographie.py). Bbox des carreaux
    # atteignables si non vide, sinon une petite marge autour du départ.
    if not gdf_carreaux.empty:
        bbox_isochrone = gdf_carreaux.to_crs("EPSG:4326").total_bounds
    else:
        bbox_isochrone = (
            origine["stop_lon"] - 0.02, origine["stop_lat"] - 0.02,
            origine["stop_lon"] + 0.02, origine["stop_lat"] + 0.02,
        )
    ajouter_couche_carreaux_insee(m, bbox_isochrone)

    if not gdf_carreaux.empty:
        colormap = cm.StepColormap(
            colors=DUREE_COLOR_BANDES, index=DUREE_COLOR_SEUILS,
            vmin=0, vmax=max(budget_min, DUREE_COLOR_SEUILS[-1]), caption=legende_duree,
        )
        colormap.add_to(m)

        def style_carreau(feature):
            return {
                "fillColor": colormap(feature["properties"]["travel_time"]),
                "color": "#334",
                "weight": 0,
                "fillOpacity": 0.75,
            }

        folium.GeoJson(
            gdf_carreaux[["geometry", "travel_time"]],
            name="Carreaux atteignables",
            style_function=style_carreau,
            tooltip=folium.GeoJsonTooltip(fields=["travel_time"], aliases=[legende_duree], localize=True),
        ).add_to(m)

    folium.Marker(
        center,
        tooltip=f"Départ : {origine['stop_name']}",
        icon=folium.Icon(color="darkred", icon="play", prefix="fa"),
    ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    return m
