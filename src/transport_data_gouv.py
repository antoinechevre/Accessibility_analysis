"""
Recherche de jeux de données GTFS urbains sur le Point d'Accès National
(https://transport.data.gouv.fr), et suivi de leur provenance/fraîcheur par
rapport aux GTFS déjà présents sur le dataset HF antoinechevre/accessibility-data.

L'API du PAN (https://transport.data.gouv.fr/api/datasets) ne propose pas de
recherche par ville côté serveur : on récupère la liste complète des jeux de
données de type "public-transit" (un seul appel, mis en cache par l'appelant
— cf. st.cache_data côté app.py) puis on filtre côté client sur le titre et
les zones couvertes (covered_area).

Provenance : chaque téléchargement via ce module enregistre, dans un fichier
JSON partagé (gtfs_sources.json, même mécanisme de cache que le reste de
data/ — cf. src/hf_cache.py), la correspondance nom_fichier_gtfs -> dataset/
ressource source et sa date de mise à jour. Sert à la fois à afficher "déjà
à jour" dans la recherche, et de base pour un futur contrôle périodique
(cf. discussion "rafraîchissement mensuel").
"""

import csv
import io
import json
import os
import unicodedata
import zipfile

import requests

from src.hf_cache import envoyer_vers_hf, recuperer_depuis_hf

API_DATASETS_URL = "https://transport.data.gouv.fr/api/datasets"

GTFS_SOURCES_LOCAL_PATH = os.path.join("data", "gtfs_sources.json")
GTFS_SOURCES_HF_PATH = "gtfs_sources.json"


def _sans_accents(texte):
    """Normalise pour une comparaison insensible aux accents/casse (ex:
    "Chateauroux" doit matcher "Châteauroux")."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", texte.lower()) if not unicodedata.combining(c)
    )


def recuperer_datasets_public_transit(timeout=30):
    """Récupère la liste brute (JSON) de tous les jeux de données de type
    "public-transit" du PAN. Un seul appel HTTP (~775 datasets début août
    2026, quelques Mo) — à l'appelant de mettre en cache (st.cache_data côté
    app.py) pour ne pas le refaire à chaque interaction.

    Lève requests.RequestException si le PAN est injoignable — laissé à
    l'appelant de décider comment l'afficher (pas de valeur de repli
    silencieuse : mieux vaut un message d'erreur clair qu'une recherche qui
    semble fonctionner mais ne renvoie jamais rien).
    """
    reponse = requests.get(API_DATASETS_URL, params={"type": "public-transit"}, timeout=timeout)
    reponse.raise_for_status()
    return reponse.json()


def _ressources_gtfs(dataset):
    """Toutes les ressources au format GTFS d'un dataset. Certains datasets
    ne couvrent qu'un seul réseau (une ressource, éventuellement plusieurs
    versions historiques — cf. TCL) ; d'autres agrègent plusieurs opérateurs
    d'une même métropole en un seul dataset PAN, une ressource par opérateur
    PLUS souvent une ressource "référentiel complet" qui les fusionne tous
    (ex: "Réseaux urbains de la Métropole Aix-Marseille-Provence" a une
    ressource "GTFS RTM" à côté d'un "Référentiel complet (tous les
    réseaux)" à 17 agences, que l'app ne peut pas charger — cf.
    TropAgencesError dans app.py). Choisir une seule ressource "la plus
    récente" comme avant reviendrait à piocher au hasard entre ces deux cas
    très différents ; les exposer toutes laisse l'appelant (recherche,
    vérification de fraîcheur) choisir la bonne."""
    return [r for r in dataset.get("resources", []) if (r.get("format") or "").upper() == "GTFS"]


def _resultat_depuis_ressource(dataset, ressource):
    """Construit le dict résultat (title, page_url, covered_area_noms,
    ressource_titre, ressource_url, ressource_maj) pour une ressource GTFS
    précise d'un dataset. Quand le dataset a plusieurs ressources GTFS
    (cf. _ressources_gtfs), le titre inclut celui de la ressource pour les
    distinguer dans la recherche (ex: "Réseaux urbains ... — GTFS RTM")."""
    noms_zones = [z.get("nom", "") for z in dataset.get("covered_area") or []]
    titre_dataset = dataset.get("title") or ""
    titre_ressource = ressource.get("title") or ""
    a_plusieurs_ressources = len(_ressources_gtfs(dataset)) > 1
    return {
        "title": f"{titre_dataset} — {titre_ressource}" if a_plusieurs_ressources else titre_dataset,
        "page_url": dataset.get("page_url"),
        "covered_area_noms": ", ".join(noms_zones[:3]) + ("…" if len(noms_zones) > 3 else ""),
        "ressource_titre": titre_ressource,
        "ressource_url": ressource.get("url"),
        "ressource_maj": ressource.get("updated"),
    }


def resultat_pour_page_url(page_url, ressource_url=None, datasets=None):
    """Retrouve, dans datasets (cf. recuperer_datasets_public_transit), le
    dataset dont page_url correspond exactement, et renvoie le résultat
    (même format que rechercher_gtfs_urbain) de sa ressource ressource_url —
    utilisé pour re-vérifier la fraîcheur d'un GTFS déjà associé (cf.
    scripts/rafraichir_gtfs.py), où provenance connaît déjà la ressource
    précise choisie au moment du téléchargement. Si ressource_url est None
    ou ne correspond à aucune ressource du dataset (ex: provenance créée
    avant l'ajout de ce paramètre), retombe sur la ressource GTFS la plus
    récente du dataset — comportement d'avant, gardé en repli uniquement.

    None si le dataset est introuvable (page supprimée/déplacée sur le PAN)
    ou sans ressource GTFS."""
    if datasets is None:
        datasets = recuperer_datasets_public_transit()
    for dataset in datasets:
        if dataset.get("page_url") != page_url:
            continue
        ressources = _ressources_gtfs(dataset)
        if not ressources:
            return None
        ressource = next((r for r in ressources if r.get("url") == ressource_url), None)
        if ressource is None:
            ressource = max(ressources, key=lambda r: r.get("updated") or "")
        return _resultat_depuis_ressource(dataset, ressource)
    return None


def rechercher_gtfs_urbain(nom_ville, datasets=None, limite=15):
    """Filtre les datasets (cf. recuperer_datasets_public_transit) dont le
    titre ou une zone couverte (covered_area) contient nom_ville, et qui ont
    au moins une ressource au format GTFS.

    datasets : liste déjà récupérée (évite un appel réseau si l'appelant l'a
    déjà en cache) ; sinon récupérée ici.

    Retourne une liste de dicts : title, page_url, covered_area_noms (str),
    ressource_titre, ressource_url, ressource_maj (date ISO ou None) — un
    élément par ressource GTFS (un dataset qui agrège plusieurs opérateurs,
    ex. une métropole, en fournit plusieurs — cf. _ressources_gtfs), triée
    par pertinence approximative (titre du dataset correspondant exactement
    d'abord, puis les ressources d'un même dataset par nombre d'agences
    croissant — une ressource par opérateur avant l'éventuel "référentiel
    complet" qui les fusionne tous et dépasse souvent la limite que l'app
    peut charger, cf. TropAgencesError dans app.py).
    """
    if datasets is None:
        datasets = recuperer_datasets_public_transit()

    cible = _sans_accents(nom_ville.strip())
    if not cible:
        return []

    resultats = []
    for dataset in datasets:
        titre = dataset.get("title") or ""
        covered_area = dataset.get("covered_area") or []
        noms_zones = [z.get("nom", "") for z in covered_area]

        correspond_titre = cible in _sans_accents(titre)
        correspond_zone = any(cible in _sans_accents(nom) for nom in noms_zones)
        if not (correspond_titre or correspond_zone):
            continue

        for ressource in _ressources_gtfs(dataset):
            resultats.append(_resultat_depuis_ressource(dataset, ressource))

    resultats.sort(
        key=lambda r: (
            0 if _sans_accents(r["title"]) == cible else 1,
            "complet" in _sans_accents(r["ressource_titre"] or ""),
        )
    )
    return resultats[:limite]


def charger_provenance():
    """Charge gtfs_sources.json (cache local, à défaut dataset HF) : dict
    nom_fichier_gtfs -> {page_url, ressource_url, ressource_maj, titre}.
    Dict vide si absent des deux côtés (première utilisation)."""
    if not os.path.exists(GTFS_SOURCES_LOCAL_PATH):
        recuperer_depuis_hf(GTFS_SOURCES_HF_PATH, GTFS_SOURCES_LOCAL_PATH)
    if not os.path.exists(GTFS_SOURCES_LOCAL_PATH):
        return {}
    with open(GTFS_SOURCES_LOCAL_PATH, encoding="utf-8") as f:
        return json.load(f)


def enregistrer_provenance(nom_fichier_gtfs, dataset_resultat):
    """Enregistre/actualise, pour nom_fichier_gtfs, la provenance issue d'un
    résultat de rechercher_gtfs_urbain (page_url/ressource_url/ressource_maj/
    title) — local + dataset HF (best-effort, comme les autres écritures de
    src/hf_cache.py)."""
    provenance = charger_provenance()
    provenance[nom_fichier_gtfs] = {
        "page_url": dataset_resultat["page_url"],
        "ressource_url": dataset_resultat["ressource_url"],
        "ressource_maj": dataset_resultat["ressource_maj"],
        "titre": dataset_resultat["title"],
    }
    os.makedirs(os.path.dirname(GTFS_SOURCES_LOCAL_PATH), exist_ok=True)
    with open(GTFS_SOURCES_LOCAL_PATH, "w", encoding="utf-8") as f:
        json.dump(provenance, f, ensure_ascii=False, indent=2, sort_keys=True)
    envoyer_vers_hf(GTFS_SOURCES_LOCAL_PATH, GTFS_SOURCES_HF_PATH)


def statut_resultat(dataset_resultat, provenance):
    """Compare dataset_resultat (issu de rechercher_gtfs_urbain) à la
    provenance enregistrée (cf. charger_provenance) : renvoie un tuple
    (statut, nom_fichier_local_ou_None).

    statut : "a_jour" (une entrée de provenance pointe vers cette même
    ressource, à la même date de màj ou plus récente — ne devrait pas
    arriver en pratique, mais couvre le cas d'une horloge source qui recule),
    "maj_disponible" (une entrée de provenance pointe vers cette même
    ressource mais à une date de màj antérieure), ou "nouveau" (aucune
    entrée de provenance ne référence cette ressource)."""
    for nom_fichier, info in provenance.items():
        if info.get("ressource_url") != dataset_resultat["ressource_url"]:
            continue
        maj_connue = info.get("ressource_maj") or ""
        maj_source = dataset_resultat.get("ressource_maj") or ""
        if maj_connue >= maj_source:
            return "a_jour", nom_fichier
        return "maj_disponible", nom_fichier
    return "nouveau", None


def telecharger_gtfs(dataset_resultat, timeout=60):
    """Télécharge le contenu (bytes) de la ressource GTFS d'un résultat de
    rechercher_gtfs_urbain. Ne l'enregistre pas sur disque — à l'appelant de
    choisir le nom de fichier final et d'appeler enregistrer_provenance."""
    reponse = requests.get(dataset_resultat["ressource_url"], timeout=timeout)
    reponse.raise_for_status()
    return reponse.content


def nb_agences_gtfs(contenu_zip):
    """Nombre d'agences (lignes d'agency.txt) d'un GTFS téléchargé (bytes).
    Ne lit que agency.txt (pas gtfs_kit.read_feed, coûteux) — juste assez
    pour appliquer le même garde-fou "max 4 agences" que app.py
    (TropAgencesError) avant d'enregistrer un GTFS trouvé via la recherche :
    une recherche par nom de ville peut remonter un jeu de données régional
    agrégeant de nombreux opérateurs (ex: "Marseille" -> GTFS métropolitain
    Aix-Marseille, 17 agences) que l'app ne peut de toute façon pas charger."""
    with zipfile.ZipFile(io.BytesIO(contenu_zip)) as archive:
        with archive.open("agency.txt") as f:
            lignes = list(csv.reader(io.TextIOWrapper(f, encoding="utf-8-sig")))
    return max(len(lignes) - 1, 0)
