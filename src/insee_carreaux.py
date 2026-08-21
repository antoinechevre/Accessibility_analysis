"""
Densité de population par carreaux INSEE 200m (Filosofi 2019), couche
optionnelle superposée aux cartes arrêts/tronçons — même source et principe
que github.com/antoinechevre/geofer_analysis (dataset HF partagé
antoinechevre/accessibility-data, fichier extracted/carreaux_200m_met.gpkg),
via recuperer_depuis_hf (hf_cache.py) qui garde une copie locale une fois
téléchargée.

INSEE_CARREAUX_LOCAL_PATH pointe vers data/extracted/ (pas data/Data_INSEE/
comme dans le projet sœur GTFS_analysis_fr) : ce fichier (~1,1 Go) est déjà
téléchargé à ce chemin par src/build_data_agglo.py (assurer_carreaux_200m_local)
pour le carroyage population du pipeline d'accessibilité — même chemin ici
pour réutiliser cette copie plutôt que d'en retélécharger une seconde.
"""

import os

import branca.colormap as cm
import folium
import geopandas as gpd
from shapely.geometry import box

from src.hf_cache import recuperer_depuis_hf
from src.i18n import t

INSEE_CARREAUX_HF_PATH = "extracted/carreaux_200m_met.gpkg"
INSEE_CARREAUX_LOCAL_PATH = os.path.join("data", "extracted", "carreaux_200m_met.gpkg")

# Dégradé rouge séquentiel (ColorBrewer "Reds") et gamma repris de
# geofer_analysis/app.py : la distribution de densité par carreau est très
# asymétrique (beaucoup de carreaux peu peuplés, peu de carreaux très
# denses) — un gamma < 1 pousse davantage de carreaux vers le rouge foncé
# pour un rendu plus contrasté qu'une interpolation linéaire min-max.
CARREAUX_COLOR_SCALE = ["#fff5f0", "#fee0d2", "#fcbba1", "#fc9272", "#fb6a4a", "#de2d26", "#a50f15", "#67000d"]
CARREAUX_COLOR_GAMMA = 0.45

# Marge (degrés) autour du bbox du réseau, pour ne pas couper les carreaux
# en bordure de la zone desservie.
MARGE_BBOX_DEG = 0.02

# Seuil de population sous lequel un carreau n'est pas affiché : Filosofi
# floute les petits effectifs (secret statistique), donc les carreaux à 0 ou
# 1 habitant sont surtout du bruit de redistribution plutôt que de la
# population réelle — les exclure allège aussi la carte HTML statique
# (embarquée en base pour tous les visiteurs, cf. components.html dans
# views/arrets.py et views/troncons.py) sans tronquer arbitrairement les
# zones peu denses mais bien peuplées.
SEUIL_POPULATION_CARREAU = 1

# Au-delà, ne garder que les carreaux les plus denses : même après le
# filtre de population ci-dessus, un grand réseau régional multi-agences
# (ex: 51_REGION_GRANDEST) peut couvrir plus de 150 000 carreaux — assez
# de polygones individuels pour que le navigateur bloque au rendu de la
# carte (page blanche), même couche décochée par défaut (elle est quand
# même construite en JS au chargement). Seuil nettement plus large que
# l'ancien plafond de 15 000 (jugé trop restrictif pour les réseaux
# urbains moyens) : les réseaux urbains restent affichés en intégralité,
# seuls les très grands réseaux régionaux sont tronqués aux zones les
# plus denses.
MAX_CARREAUX_RENDER = 30000

SURFACE_CARREAU_KM2 = 0.04  # carreaux de 200m x 200m


def charger_carreaux_insee(bbox_wgs84):
    """Charge les carreaux INSEE 200m couvrant bbox_wgs84 (minx, miny, maxx,
    maxy en EPSG:4326), avec une colonne densite_hab_km2. None si le
    fichier est indisponible (HF_TOKEN absent, hors ligne...) ou si la
    lecture échoue ; GeoDataFrame vide si aucun carreau dans la zone."""
    if not recuperer_depuis_hf(INSEE_CARREAUX_HF_PATH, INSEE_CARREAUX_LOCAL_PATH):
        print("[insee_carreaux] carreaux INSEE indisponibles (ni local, ni HF) — couche ignorée")
        return None

    minx, miny, maxx, maxy = bbox_wgs84
    bbox_geom = gpd.GeoSeries(
        [box(minx - MARGE_BBOX_DEG, miny - MARGE_BBOX_DEG, maxx + MARGE_BBOX_DEG, maxy + MARGE_BBOX_DEG)],
        crs="EPSG:4326",
    )

    try:
        gdf = gpd.read_file(INSEE_CARREAUX_LOCAL_PATH, bbox=bbox_geom)
    except Exception as e:
        print(f"[insee_carreaux] lecture de {INSEE_CARREAUX_LOCAL_PATH} a échoué : {e!r}")
        return None

    if gdf.empty:
        return gdf

    gdf = gdf[gdf["ind"] > SEUIL_POPULATION_CARREAU]
    if gdf.empty:
        return gdf

    gdf = gdf.to_crs("EPSG:4326")
    gdf["densite_hab_km2"] = (gdf["ind"] / SURFACE_CARREAU_KM2).round().astype(int)

    if len(gdf) > MAX_CARREAUX_RENDER:
        gdf = gdf.nlargest(MAX_CARREAUX_RENDER, "densite_hab_km2")

    return gdf


def ajouter_couche_carreaux_insee(m, bbox_wgs84, lang="fr"):
    """Ajoute à la folium.Map m une couche optionnelle (décochée par défaut,
    activable via le contrôle des couches déjà présent sur les deux cartes
    de l'app) de densité de population par carreau INSEE 200m. Best-effort :
    n'ajoute rien si les carreaux sont indisponibles ou vides pour cette
    zone, plutôt que de faire échouer la génération de la carte."""
    try:
        gdf = charger_carreaux_insee(bbox_wgs84)
    except Exception as e:
        print(f"[insee_carreaux] couche carreaux INSEE ignorée : {e!r}")
        return

    if gdf is None or gdf.empty:
        return

    vmin = float(gdf["densite_hab_km2"].min())
    vmax = float(gdf["densite_hab_km2"].max())
    if vmax <= vmin:
        return

    colormap = cm.LinearColormap(
        colors=CARREAUX_COLOR_SCALE, vmin=vmin, vmax=vmax,
        caption=t("carto.legende_densite_pop", lang),
    )

    def style_carreau(feature):
        valeur = feature["properties"]["densite_hab_km2"]
        frac = ((valeur - vmin) / (vmax - vmin)) ** CARREAUX_COLOR_GAMMA
        valeur_ajustee = vmin + frac * (vmax - vmin)
        return {"fillColor": colormap(valeur_ajustee), "color": "#581012", "weight": 0, "fillOpacity": 1.0}

    # Pane dédiée avec un z-index sous le overlayPane par défaut de Leaflet
    # (400, où vivent arrêts et tronçons) : décochée par défaut (show=False
    # ci-dessous), cette couche n'est attachée au DOM que quand le visiteur
    # la coche, donc après que arrêts/tronçons soient déjà affichés — sans
    # pane dédiée, elle se retrouverait alors au-dessus d'eux (dernier
    # élément ajouté au DOM = au-dessus en SVG) au lieu de rester sous
    # l'affichage du réseau.
    PANE_CARREAUX = "carreaux_insee"
    folium.map.CustomPane(PANE_CARREAUX, z_index=350).add_to(m)

    folium.GeoJson(
        gdf[["geometry", "densite_hab_km2"]],
        name=t("carto.couche_densite_pop", lang),
        style_function=style_carreau,
        tooltip=folium.GeoJsonTooltip(
            fields=["densite_hab_km2"],
            aliases=[t("carto.tooltip_densite_pop", lang)],
            localize=True,
        ),
        pane=PANE_CARREAUX,
        show=False,
    ).add_to(m)
    colormap.add_to(m)
