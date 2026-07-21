import json
import pathlib
import shutil
import subprocess
import time

import geopandas as gpd
import gtfs_kit as gk
import pandas as pd
import requests
import shapely
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import shapely.geometry
import os


BASE_DIR = os.getcwd()  # Remonte d'un niveau depuis scripts/
DATA_DIR = os.path.join(BASE_DIR,"data")


def session_avec_retries(methods=("GET",), total=5, backoff_factor=1):
    """Session HTTP tolérante aux lenteurs/coupures ponctuelles d'une API distante."""
    session = requests.Session()
    retries = Retry(
        total=total,
        backoff_factor=backoff_factor,  # 1s, 2s, 4s, 8s, 16s... entre les tentatives
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=list(methods),
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def codes_communes_via_api(stops_gdf, session, pause=0.05, timeout=30):
    """Reverse-géocode chaque arrêt (lat/lon) en code INSEE via geo.api.gouv.fr."""
    codes = set()
    for lat, lon in stops_gdf[["stop_lat", "stop_lon"]].itertuples(index=False):
        r = session.get(
            "https://geo.api.gouv.fr/communes",
            params={"lat": lat, "lon": lon, "fields": "code"},
            timeout=timeout,
        )
        r.raise_for_status()
        codes.update(c["code"] for c in r.json())
        time.sleep(pause)
    return codes


def details_communes(codes, session, pause=0.05, timeout=30):
    """Récupère nom/centre/contour de chaque commune (même schéma que decoupage_cda.csv)."""
    lignes = []
    for code in sorted(codes):
        r = session.get(
            f"https://geo.api.gouv.fr/communes/{code}",
            params={"fields": "nom,centre,contour"},
            timeout=timeout,
        )
        r.raise_for_status()
        commune = r.json()
        lon, lat = commune["centre"]["coordinates"]
        lignes.append({
            "code_insee": commune["code"],
            "nom_commune": commune["nom"],
            "coordinates": f"{lat:.3f},{lon:.3f}",
            "geojson": json.dumps(commune["contour"], separators=(",", ":")),
        })
        time.sleep(pause)
    return lignes


def build_decoupage_agglo(gtfs_path, output_path, decoupage_reference_path=None, coord_round=4):
    """
    Construit un CSV des communes desservies par un GTFS, au même format que
    decoupage_cda.csv (id, code_insee, nom_commune, coordinates, geojson).

    gtfs_path: chemin vers n'importe quel zip GTFS.
    decoupage_reference_path: CSV existant du même format (optionnel), utilisé
        comme cache local pour éviter de géocoder les arrêts qui tombent dans
        des communes déjà connues (ex. decoupage_cda.csv pour le réseau CDA).
    """
    feed = gk.read_feed(gtfs_path, dist_units="km")
    stops = feed.stops[["stop_lat", "stop_lon"]].dropna().round(coord_round).drop_duplicates()
    stops_gdf = gpd.GeoDataFrame(
        stops,
        geometry=gpd.points_from_xy(stops["stop_lon"], stops["stop_lat"]),
        crs="EPSG:4326",
    )

    codes_connus = set()
    communes_connues = pd.DataFrame(columns=["code_insee", "nom_commune", "coordinates", "geojson"])

    if decoupage_reference_path is not None:
        reference = pd.read_csv(decoupage_reference_path, dtype={"code_insee": str})
        reference_gdf = gpd.GeoDataFrame(
            reference,
            geometry=reference["geojson"].apply(lambda g: shapely.geometry.shape(json.loads(g))),
            crs="EPSG:4326",
        )
        joined = gpd.sjoin(stops_gdf, reference_gdf[["code_insee", "geometry"]], how="left", predicate="within")
        codes_connus = set(joined.loc[joined["code_insee"].notna(), "code_insee"])
        stops_gdf = stops_gdf.loc[joined["code_insee"].isna()]
        communes_connues = reference[reference["code_insee"].isin(codes_connus)][
            ["code_insee", "nom_commune", "coordinates", "geojson"]
        ]

    print(f"{len(codes_connus)} commune(s) déjà connue(s), {len(stops_gdf)} arrêt(s) à géocoder")

    with session_avec_retries() as session:
        codes_a_geocoder = codes_communes_via_api(stops_gdf, session) if len(stops_gdf) else set()
        nouveaux_codes = codes_a_geocoder - codes_connus
        print(f"{len(nouveaux_codes)} nouvelle(s) commune(s) identifiée(s) : {sorted(nouveaux_codes)}")
        nouvelles_lignes = details_communes(nouveaux_codes, session)

    decoupage_agglo = (
        pd.concat([communes_connues, pd.DataFrame(nouvelles_lignes)], ignore_index=True)
        .drop_duplicates(subset="code_insee")
        .sort_values("nom_commune")
        .reset_index(drop=True)
    )
    decoupage_agglo.insert(0, "id", range(1, len(decoupage_agglo) + 1))

    decoupage_agglo.to_csv(output_path, index=False)
    print(f"✓ {len(decoupage_agglo)} commune(s) écrite(s) dans {output_path}")
    return decoupage_agglo


def decoupage_agglo_geojson(csv_path="data/decoupage_agglo.csv", output_path="data/decoupage_agglo.geojson"):
    """
    Convertit decoupage_agglo.csv en GeoJSON, au même format que decoupage_cda.geojson
    (une Feature par commune, propriétés id/code_insee/nom_commune/coordinates).
    """
    decoupage_agglo = pd.read_csv(csv_path, dtype={"code_insee": str})
    gdf = gpd.GeoDataFrame(
        decoupage_agglo[["id", "code_insee", "nom_commune", "coordinates"]],
        geometry=decoupage_agglo["geojson"].apply(lambda g: shapely.geometry.shape(json.loads(g))),
        crs="EPSG:4326",
    )
    gdf.to_file(output_path, driver="GeoJSON")
    print(f"✓ {len(gdf)} commune(s) écrite(s) dans {output_path}")
    return gdf

def _tuiles_bbox(min_lon, min_lat, max_lon, max_lat, taille_deg):
    """Découpe une bbox en tuiles carrées d'au plus `taille_deg` degrés de côté.

    Pour les grandes agglomérations, interroger Overpass sur toute l'emprise en une
    seule requête dépasse vite les limites de taille/temps du service public. On
    découpe donc en tuiles plus petites, récupérées séparément puis fusionnées.
    """
    tuiles = []
    lat = min_lat
    while lat < max_lat:
        haut = min(lat + taille_deg, max_lat)
        lon = min_lon
        while lon < max_lon:
            droite = min(lon + taille_deg, max_lon)
            tuiles.append((lon, lat, droite, haut))
            lon = droite
        lat = haut
    return tuiles


def _telecharger_tuile_overpass(bbox, output_path, session, overpass_url, timeout):
    """Télécharge les données OSM d'une tuile (bbox) via Overpass, au format XML."""
    min_lon, min_lat, max_lon, max_lat = bbox
    query = (
        f"[out:xml][timeout:{timeout}];"
        f"(node({min_lat},{min_lon},{max_lat},{max_lon});"
        f"way({min_lat},{min_lon},{max_lat},{max_lon});"
        f"relation({min_lat},{min_lon},{max_lat},{max_lon}););"
        "out body;"  # tags + géométrie seulement (pas d'historique d'édition : ~3-4x plus léger que "out meta")
    )
    headers = {"User-Agent": "Dossier_index_def/1.0 (build_data_agglo.py)"}
    response = session.post(
        overpass_url, data={"data": query}, headers=headers, timeout=timeout + 30
    )
    response.raise_for_status()
    with open(output_path, "wb") as f:
        f.write(response.content)


def osm_pbf_creator(
    decoupage_agglo_path,
    output_pbf_path=None,
    tile_size_deg=0.3,
    overpass_url="https://overpass-api.de/api/interpreter",
    timeout=180,
    pause=1.0,
):
    """Build agglo.osm.pbf: données OSM découpées sur l'emprise de decoupage_agglo_path.

    Équivalent de r5py.sampledata.helsinki.osm_pbf, mais pour n'importe quelle agglo :
    au lieu de dépendre d'un extrait régional Geofabrik pré-découpé (qui ne couvre
    qu'une zone géographique fixe), les données OSM sont téléchargées directement sur
    l'emprise du GeoJSON fourni via l'API Overpass, puis découpées précisément sur le
    contour réel de l'agglo avec osmium. Fonctionne donc pour n'importe quelle
    géographie dans le monde.

    Pour les grandes agglomérations, l'emprise est découpée en tuiles d'au plus
    `tile_size_deg` degrés de côté (0.3° ≈ 30 km) afin de rester sous les limites de
    taille/temps de l'API Overpass publique ; les tuiles sont téléchargées une par une
    (avec retries automatiques et une pause de `pause` secondes entre chacune, pour ne
    pas surcharger le service public) puis fusionnées avant le découpage final.

    Requires osmium-tool (macOS: `brew install osmium-tool`).

    decoupage_agglo_path: chemin vers un GeoJSON de communes (ex. decoupage_agglo.geojson).
    output_pbf_path: chemin du .osm.pbf en sortie (par défaut : "agglo.osm.pbf" à côté
        de decoupage_agglo_path).
    tile_size_deg: taille max d'une tuile Overpass, en degrés. Réduire cette valeur
        (ex. 0.15) si Overpass renvoie des erreurs de timeout/taille sur une très
        grande agglomération.
    overpass_url: instance Overpass à utiliser (changer en cas de limitation de débit
        sur l'instance publique par défaut, ex. "https://overpass.kumi.systems/api/interpreter").
    timeout: timeout Overpass par tuile, en secondes.
    """
    output_dir = pathlib.Path(decoupage_agglo_path).parent
    if output_pbf_path is None:
        output_pbf_path = output_dir / "agglo.osm.pbf"
    BOUNDARY_GEOJSON = output_dir / "agglo_boundary.geojson"
    OUTPUT_PBF = output_pbf_path

    if shutil.which("osmium") is None:
        raise SystemExit(
            "osmium-tool is required but not found. Install it with: brew install osmium-tool"
        )

    # 1. Dissolve the agglo communes into a single boundary polygon for osmium extract
    agglo = gpd.read_file(decoupage_agglo_path)
    agglo = agglo.set_crs("EPSG:4326") if agglo.crs is None else agglo
    agglo.geometry = agglo.geometry.buffer(0)  # fix invalid geometries before dissolving

    boundary = gpd.GeoDataFrame(geometry=[agglo.union_all()], crs=agglo.crs)
    boundary.to_file(BOUNDARY_GEOJSON, driver="GeoJSON")
    print(f"wrote {BOUNDARY_GEOJSON}, bounds: {boundary.total_bounds}")

    # 2. Télécharger les données OSM couvrant l'emprise via Overpass, tuile par tuile —
    # ne dépend d'aucun découpage régional préexistant, marche pour n'importe quelle zone
    min_lon, min_lat, max_lon, max_lat = boundary.total_bounds
    tuiles = _tuiles_bbox(min_lon, min_lat, max_lon, max_lat, tile_size_deg)
    print(f"emprise découpée en {len(tuiles)} tuile(s) de {tile_size_deg}° pour Overpass")

    fichiers_tuiles = []
    with session_avec_retries(methods=("GET", "POST"), total=8, backoff_factor=2) as session:
        for i, bbox in enumerate(tuiles, start=1):
            tuile_path = output_dir / f"agglo_tuile_{i}.osm"
            print(f"téléchargement tuile {i}/{len(tuiles)} (bbox {bbox}) ...")
            _telecharger_tuile_overpass(bbox, tuile_path, session, overpass_url, timeout)
            fichiers_tuiles.append(tuile_path)
            time.sleep(pause)

    # 3. Fusionner les tuiles (si plusieurs) puis découper précisément sur le contour
    # réel de l'agglo (les tuiles Overpass sont rectangulaires, plus larges que le contour)
    if len(fichiers_tuiles) > 1:
        fusion_path = output_dir / "agglo_fusion.osm.pbf"
        subprocess.run(
            ["osmium", "merge", *fichiers_tuiles, "-o", fusion_path, "--overwrite"],
            check=True,
        )
        print(f"wrote {fusion_path} (fusion de {len(fichiers_tuiles)} tuiles)")
    else:
        fusion_path = fichiers_tuiles[0]

    subprocess.run(
        [
            "osmium",
            "extract",
            "-p",
            BOUNDARY_GEOJSON,
            "-o",
            OUTPUT_PBF,
            "--overwrite",
            fusion_path,
        ],
        check=True,
    )
    print(f"wrote {OUTPUT_PBF}")

    # 4. Clean up the intermediate files, no longer needed
    for f in fichiers_tuiles:
        pathlib.Path(f).unlink()
    if len(fichiers_tuiles) > 1:
        pathlib.Path(fusion_path).unlink()
    print("removed intermediate OSM files")




def build_grid_agglo(path):


    """Build population_grid_cda: full 200m grid clipped to the CDA La Rochelle boundary.

    Includes cells not published by INSEE Filosofi (population too low to satisfy
    statistical secrecy, generally < 11 households) with population=0, rather than
    only the sparse subset of cells that Filosofi publishes.
    """
    agglo = gpd.read_file(path)
    agglo = agglo.set_crs("EPSG:4326") if agglo.crs is None else agglo
    agglo.geometry = agglo.geometry.buffer(0)
    agglo_boundary = gpd.GeoDataFrame(
        geometry=[agglo.union_all()], crs=agglo.crs
    ).to_crs("EPSG:2154")

    # Grille Filosofi publiée par l'INSEE (uniquement les carreaux avec assez de
    # ménages pour respecter le secret statistique) : sert à récupérer les données
    # démographiques là où elles existent.
    minx, miny, maxx, maxy = agglo_boundary.total_bounds
    grid_publiee = gpd.read_file(
        f"{DATA_DIR}/extracted/carreaux_200m_met.gpkg",
        bbox=(minx, miny, maxx, maxy),
    )

    # La grille Filosofi 200m est définie nativement en EPSG:3035 (ETRS89-LAEA) :
    # idcar_200m encode le coin sud-ouest du carreau dans ce système, ex:
    # "CRS3035RES200mN2607600E3467800" -> N=2607600, E=3467800 (vérifié : reconstruire
    # le carreau à partir de ces coordonnées puis reprojeter en EPSG:2154 reproduit
    # exactement la géométrie fournie par l'INSEE). Pour générer TOUS les carreaux
    # théoriques de la zone (y compris ceux non publiés), on construit donc la
    # grille dans ce système natif, puis on la reprojette.
    RESOLUTION = 200
    agglo_boundary_3035 = agglo_boundary.to_crs("EPSG:3035")
    minx3035, miny3035, maxx3035, maxy3035 = agglo_boundary_3035.total_bounds

    n_start = int(miny3035 // RESOLUTION) * RESOLUTION
    n_end = int(maxy3035 // RESOLUTION + 1) * RESOLUTION
    e_start = int(minx3035 // RESOLUTION) * RESOLUTION
    e_end = int(maxx3035 // RESOLUTION + 1) * RESOLUTION

    ids = []
    cells = []
    for n in range(n_start, n_end, RESOLUTION):
        for e in range(e_start, e_end, RESOLUTION):
            ids.append(f"CRS3035RES200mN{n}E{e}")
            cells.append(shapely.geometry.box(e, n, e + RESOLUTION, n + RESOLUTION))

    grille_theorique = gpd.GeoDataFrame(
        {"idcar_200m": ids}, geometry=cells, crs="EPSG:3035"
    ).to_crs("EPSG:2154")

    # Ne garder que les carreaux théoriques dont le centroïde tombe dans la CDA
    centroids = grille_theorique.geometry.centroid
    within_mask = centroids.within(agglo_boundary.geometry.iloc[0])
    population_grid_agglo = grille_theorique.loc[within_mask].copy()
    population_grid_agglo["centroid_x"] = centroids.loc[within_mask].x
    population_grid_agglo["centroid_y"] = centroids.loc[within_mask].y

    # Rattachement des données Filosofi publiées (population, revenus, etc.) sur
    # les carreaux théoriques : les carreaux non publiés (secret statistique)
    # n'ont pas de correspondance et restent à combler.
    colonnes_filosofi = [c for c in grid_publiee.columns if c not in ("idcar_200m", "geometry")]
    population_grid_agglo = population_grid_agglo.merge(
        grid_publiee[["idcar_200m", *colonnes_filosofi]],
        on="idcar_200m",
        how="left",
        indicator="publie",
    )
    population_grid_agglo["publie"] = population_grid_agglo["publie"] == "both"

    # Les colonnes numériques (population, revenus, logements...) valent 0 là où
    # l'INSEE n'a rien publié. Les colonnes identifiantes/catégorielles (idcar_1km,
    # lcog_geo...) ne sont pas dérivables sans le référentiel INSEE et restent vides.
    colonnes_numeriques = population_grid_agglo[colonnes_filosofi].select_dtypes("number").columns
    population_grid_agglo[colonnes_numeriques] = population_grid_agglo[colonnes_numeriques].fillna(0)

    population_grid_agglo["population"] = population_grid_agglo["ind"]
    population_grid_agglo["id"] = population_grid_agglo["idcar_200m"]  # required by r5py.TravelTimeMatrix

    output_path = f"{DATA_DIR}/population_grid_agglo.gpkg"
    population_grid_agglo.to_file(output_path, driver="GPKG")

    print(f"carreaux dans l'agglo (grille complète): {len(population_grid_agglo)}")
    print(f"dont publiés par l'INSEE: {population_grid_agglo['publie'].sum()}")
    print(f"dont non publiés (secret statistique, population mise à 0): {(~population_grid_agglo['publie']).sum()}")
    print(f"population totale (ind): {population_grid_agglo['ind'].sum():.0f}")
    print(f"ecrit dans: {output_path}")



