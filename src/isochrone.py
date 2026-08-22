"""
Isochrones GTFS : accessibilité en transport collectif depuis un arrêt, à
une heure de pointe donnée, dans un budget de temps donné (RAPTOR simplifié
limité aux correspondances au même arrêt), habillées d'un isochrone piéton
autour de chaque arrêt atteint (API Isochrone/Isodistance de la
Géoplateforme IGN, data.geopf.fr). Repris et adapté de app_isochrone.py
(app standalone du même auteur) pour s'intégrer à un GTFS déjà chargé par
cette appli (feed, active_service_ids, date_JOB déjà en session_state,
cf. views/isochrone.py) plutôt que de les recharger indépendamment.
"""

import time

import folium
import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import Point, shape
from shapely.ops import unary_union

from src.insee_carreaux import ajouter_couche_carreaux_insee

GEOPF_ISOCHRONE_URL = "https://data.geopf.fr/navigation/isochrone"
GEOPF_MAX_REQ_PER_SEC = 5  # limite documentée de l'API Géoplateforme
GEOPF_MAX_STOPS_CALLED = 150  # au-delà, repli sur un cercle approximatif (pas d'appel API)

TRANSFER_BUFFER_SECONDS = 120  # temps de correspondance minimal au même arrêt

DEFAULT_BUDGET_MIN = 30
DEFAULT_MAX_TRANSFERS = 1
DEFAULT_WALK_BUFFER_MIN = 5

# Bandes de 15 min (plutôt qu'un dégradé continu), calquées sur la légende
# "Transport en commun" des cartes isochrones Carto-sig/Targomo (outils de
# mobilité domicile-travail) : vert foncé = le plus rapide, rouge = le plus
# lent. Un value au-delà de 60 min reste dans la dernière bande (rouge).
DUREE_COLOR_BANDES = ["#1a6634", "#8bc34a", "#f39c12", "#c0392b"]
DUREE_COLOR_SEUILS = [0, 15, 30, 45, 60]


def to_seconds(hhmmss: str) -> int:
    h, m, s = hhmmss.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def to_hhmm(total_seconds: float) -> str:
    total_seconds = int(total_seconds) % 86400
    return f"{total_seconds // 3600:02d}h{(total_seconds % 3600) // 60:02d}"


def stop_ids_pour_station(feed, station_id):
    """
    Retourne les stop_id (quais) rattachés à une station, même logique de
    regroupement que calculer_indicateurs_arrets (src/arrets.py) : la
    station choisie comme origine de l'isochrone est un stop_id agrégé par
    parent_station, pas nécessairement un stop_id référencé dans
    stop_times.txt (seuls les quais y figurent souvent, pas la station).
    """
    if "parent_station" in feed.stops.columns:
        correspond = feed.stops["parent_station"].fillna(feed.stops["stop_id"]) == station_id
    else:
        correspond = feed.stops["stop_id"] == station_id
    stop_ids = feed.stops.loc[correspond, "stop_id"].tolist()
    return stop_ids if stop_ids else [station_id]


def calculer_arrets_atteignables(feed, active_service_ids, origin_stop_ids, depart_s, budget_min, max_correspondances):
    """
    RAPTOR simplifié : à chaque tour, ne considère que les trajets passant
    par les arrêts atteints au tour précédent (frontière), correspondances
    au même arrêt uniquement (pas de correspondance piétonne entre deux
    arrêts distincts proches).

    active_service_ids : déjà calculé par l'appelant (obtenir_service_ids_
    pour_date, plus repli d'offre éventuel pour une agence — cf. app.py et
    GTFS_AGENCES_OFFRE_REPLI) plutôt que redérivé ici depuis une date : sur
    un GTFS multi-agences avec repli, dériver depuis la date perdrait les
    agences en repli (même bug que celui corrigé dans arrets.py/cartographie.py).

    origin_stop_ids : plusieurs stop_id (les quais d'une même station, cf.
    stop_ids_pour_station) considérés atteints dès depart_s.
    """
    trip_ids_actifs = set(feed.trips.loc[feed.trips["service_id"].isin(active_service_ids), "trip_id"])

    st_df = feed.stop_times[feed.stop_times["trip_id"].isin(trip_ids_actifs)].copy()
    st_df = st_df.dropna(subset=["departure_time", "arrival_time"])
    st_df["dep_s"] = st_df["departure_time"].map(to_seconds)
    st_df["arr_s"] = st_df["arrival_time"].map(to_seconds)
    st_df = st_df.sort_values(["trip_id", "stop_sequence"])

    trajets = {
        trip_id: list(zip(grp["stop_id"], grp["dep_s"], grp["arr_s"]))
        for trip_id, grp in st_df.groupby("trip_id")
    }
    trips_par_arret = st_df.groupby("stop_id")["trip_id"].unique().to_dict()

    max_arrivee = depart_s + budget_min * 60
    arrivee_au_plus_tot = {sid: depart_s for sid in origin_stop_ids}
    correspondances_utilisees = {sid: 0 for sid in origin_stop_ids}
    frontiere = set(origin_stop_ids)

    for tour in range(max_correspondances + 1):
        trips_a_scanner: set = set()
        for arret in frontiere:
            trips_a_scanner.update(trips_par_arret.get(arret, []))

        nouvelle_frontiere = set()
        for trip_id in trips_a_scanner:
            embarque = False
            for stop_id, dep_s, arr_s in trajets[trip_id]:
                if not embarque:
                    if stop_id in frontiere:
                        tampon = TRANSFER_BUFFER_SECONDS if correspondances_utilisees[stop_id] > 0 else 0
                        if dep_s >= arrivee_au_plus_tot[stop_id] + tampon:
                            embarque = True
                    continue
                if arr_s <= max_arrivee and arr_s < arrivee_au_plus_tot.get(stop_id, max_arrivee + 1):
                    arrivee_au_plus_tot[stop_id] = arr_s
                    correspondances_utilisees[stop_id] = tour
                    nouvelle_frontiere.add(stop_id)

        frontiere = nouvelle_frontiere
        if not frontiere:
            break

    for sid in origin_stop_ids:
        arrivee_au_plus_tot.pop(sid, None)
    if not arrivee_au_plus_tot:
        return pd.DataFrame(columns=["stop_id", "stop_name", "stop_lat", "stop_lon", "arrivee_s", "correspondances", "duree_min"])

    resultat = pd.DataFrame({
        "stop_id": list(arrivee_au_plus_tot.keys()),
        "arrivee_s": list(arrivee_au_plus_tot.values()),
    })
    resultat["correspondances"] = resultat["stop_id"].map(correspondances_utilisees)
    resultat["duree_min"] = (resultat["arrivee_s"] - depart_s) / 60
    return resultat.merge(feed.stops[["stop_id", "stop_name", "stop_lat", "stop_lon"]], on="stop_id")


def fetch_geopf_isochrone(lon: float, lat: float, minutes: float):
    """Isochrone piéton Géoplateforme (geometry GeoJSON) autour d'un point,
    ou None en cas d'échec (réseau, timeout, arrêt hors zone couverte...)."""
    try:
        r = requests.get(
            GEOPF_ISOCHRONE_URL,
            params={
                "resource": "bdtopo-valhalla",
                "point": f"{lon},{lat}",
                "direction": "departure",
                "costType": "time",
                "costValue": max(60, round(minutes * 60)),
                "profile": "pedestrian",
                "geometryFormat": "geojson",
            },
            timeout=10,
        )
        r.raise_for_status()
        return r.json()["geometry"]
    except Exception:
        return None


def recuperer_buffers_marche(arrets: pd.DataFrame, minutes: float, cache: dict) -> dict:
    """Isochrone piéton par arrêt atteint, avec cache fourni par l'appelant
    (persisté en session_state côté vue, cf. views/isochrone.py) et
    limitation à GEOPF_MAX_REQ_PER_SEC requêtes/s (limite documentée de
    l'API)."""
    buffers = {}
    arrets_a_appeler = arrets.head(GEOPF_MAX_STOPS_CALLED)
    for _, arret in arrets_a_appeler.iterrows():
        cle = (round(arret["stop_lon"], 5), round(arret["stop_lat"], 5), minutes)
        if cle not in cache:
            cache[cle] = fetch_geopf_isochrone(arret["stop_lon"], arret["stop_lat"], minutes)
            time.sleep(1 / GEOPF_MAX_REQ_PER_SEC)
        buffers[arret["stop_id"]] = cache[cle]
    return buffers


def _ne_garder_que_polygones(geom):
    """Filtre un résultat de différence/union shapely aux seules parties
    polygonales : une différence entre polygones peut renvoyer une
    GeometryCollection contenant en plus des restes dégénérés (points,
    segments) là où les bords se touchent tangentiellement, invalides pour
    un rendu GeoJson."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    if geom.geom_type == "GeometryCollection":
        polygones = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
        return unary_union(polygones) if polygones else None
    return None


def zones_marche_tronquees(arrets: pd.DataFrame, buffers: dict, rayon_marche_min: int) -> gpd.GeoDataFrame:
    """Géométrie de la zone de marche de chaque arrêt atteint (isochrone
    piéton API si disponible, cf. buffers/recuperer_buffers_marche, sinon
    cercle de rayon_marche_min*80 mètres), tronquée de la portion déjà
    couverte par un arrêt plus rapide : là où deux zones se chevauchent,
    l'arrêt le plus proche en durée "gagne" la zone commune plutôt que de
    superposer les couleurs. Retourne un GeoDataFrame [colonnes de arrets +
    geometry] en EPSG:4326, trié du plus lent au plus rapide (ordre correct
    pour l'ajout à la carte : le plus rapide, ajouté en dernier, se
    retrouve visuellement au-dessus)."""
    arrets_croissant = arrets.sort_values("duree_min", ascending=True).reset_index(drop=True)

    geometries = [
        shape(buffers[stop_id]) if buffers.get(stop_id) is not None else Point(lon, lat)
        for stop_id, lon, lat in zip(arrets_croissant["stop_id"], arrets_croissant["stop_lon"], arrets_croissant["stop_lat"])
    ]
    gdf = gpd.GeoDataFrame(arrets_croissant, geometry=geometries, crs="EPSG:4326")

    # Web Mercator (mètres) pour un rayon de cercle et des opérations de
    # troncature corrects — reprojeté en EPSG:4326 avant retour pour le
    # rendu GeoJson/Leaflet.
    gdf_m = gdf.to_crs(epsg=3857)
    sans_geometrie_api = gdf.geometry.geom_type == "Point"
    gdf_m.loc[sans_geometrie_api, "geometry"] = gdf_m.loc[sans_geometrie_api, "geometry"].buffer(
        rayon_marche_min * 80  # ~80 m/min à pied, repli grossier si l'API a échoué pour cet arrêt
    )

    deja_couvert = None
    geometries_tronquees = []
    for geom in gdf_m.geometry:
        visible = _ne_garder_que_polygones(geom.difference(deja_couvert)) if deja_couvert is not None else geom
        geometries_tronquees.append(visible)
        deja_couvert = geom if deja_couvert is None else unary_union([deja_couvert, geom])

    gdf_m["geometry"] = geometries_tronquees
    # Le plus lent en premier (dessous), le plus rapide en dernier (dessus)
    return gdf_m[gdf_m.geometry.notna()].to_crs(epsg=4326).iloc[::-1]


def build_map(origine, arrets: pd.DataFrame, buffers: dict, budget_min: int, rayon_marche_min: int, legende_duree="Durée de trajet depuis l'arrêt de départ (min)") -> folium.Map:
    center = [origine["stop_lat"], origine["stop_lon"]]
    m = folium.Map(location=center, zoom_start=13, tiles=None, prefer_canvas=True, control_scale=True)
    # Fonds de carte empilés (rasters opaques) : le dernier ajouté est celui
    # visible par défaut, donc CartoDB Positron en dernier.
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap", cross_origin=True).add_to(m)
    folium.TileLayer("CartoDB dark_matter", name="CartoDB Dark Matter", cross_origin=True).add_to(m)
    folium.TileLayer("CartoDB positron", name="CartoDB Positron", cross_origin=True).add_to(m)

    # Couche optionnelle (décochée par défaut) de densité de population par
    # carreau INSEE 200m — même mécanisme que create_carte_arrets/
    # creer_carte_troncons (src/cartographie.py). Bbox des arrêts atteints
    # si non vide, sinon une petite marge autour du seul point de départ.
    if not arrets.empty:
        bbox_isochrone = (
            min(arrets["stop_lon"].min(), origine["stop_lon"]),
            min(arrets["stop_lat"].min(), origine["stop_lat"]),
            max(arrets["stop_lon"].max(), origine["stop_lon"]),
            max(arrets["stop_lat"].max(), origine["stop_lat"]),
        )
    else:
        bbox_isochrone = (
            origine["stop_lon"] - 0.02, origine["stop_lat"] - 0.02,
            origine["stop_lon"] + 0.02, origine["stop_lat"] + 0.02,
        )
    ajouter_couche_carreaux_insee(m, bbox_isochrone)

    if not arrets.empty:
        colormap = folium.StepColormap(
            colors=DUREE_COLOR_BANDES, index=DUREE_COLOR_SEUILS,
            vmin=0, vmax=max(budget_min, DUREE_COLOR_SEUILS[-1]),
            caption=legende_duree,
        )
        colormap.add_to(m)

        # Tri décroissant par durée avant d'ajouter les couches : les
        # éléments ajoutés en dernier se retrouvent visuellement au-dessus
        # (empilement SVG/Leaflet par ordre d'ajout), donc les zones/arrêts
        # les plus rapides (verts) ajoutés en dernier passent devant les
        # plus lents (orange, puis rouge) là où ils se chevauchent.
        arrets_du_plus_lent_au_plus_rapide = arrets.sort_values("duree_min", ascending=False)

        buffers_layer = folium.FeatureGroup(name="Zones de marche autour des arrêts atteints")
        for _, zone in zones_marche_tronquees(arrets, buffers, rayon_marche_min).iterrows():
            couleur = colormap(zone["duree_min"])
            folium.GeoJson(
                zone["geometry"].__geo_interface__,
                style_function=lambda _f, c=couleur: {"fillColor": c, "color": c, "weight": 1, "fillOpacity": 0.35},
            ).add_to(buffers_layer)
        buffers_layer.add_to(m)

        arrets_layer = folium.FeatureGroup(name="Arrêts atteints")
        for _, arret in arrets_du_plus_lent_au_plus_rapide.iterrows():
            folium.CircleMarker(
                [arret["stop_lat"], arret["stop_lon"]],
                radius=4,
                color="#334",
                weight=1,
                fill=True,
                fillColor=colormap(arret["duree_min"]),
                fillOpacity=0.9,
                tooltip=(
                    f"{arret['stop_name']}<br>{arret['duree_min']:.0f} min "
                    f"({int(arret['correspondances'])} correspondance{'s' if arret['correspondances'] > 1 else ''})"
                ),
            ).add_to(arrets_layer)
        arrets_layer.add_to(m)

    folium.Marker(
        center,
        tooltip=f"Départ : {origine['stop_name']}",
        icon=folium.Icon(color="darkred", icon="play", prefix="fa"),
    ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    return m
