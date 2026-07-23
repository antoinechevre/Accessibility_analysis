import os
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from datetime import datetime
import sys
sys.path.append('..')

from src.utils import longueur_lignes
from src.i18n import t

# fonction pour charger les données du GTFS 


def nom_reseau(feed):
    """
    Extrait le nom du réseau de transport à partir du GTFS.

    Se base sur agency_name (agency.txt), un champ obligatoire du standard
    GTFS : cette fonction fonctionne donc pour n'importe quel feed, pas
    seulement TBM. S'il y a plusieurs agences dans le feed, leurs noms sont
    concaténés.

    Parameters
    ----------
    feed : gtfs_kit.Feed
        Le feed GTFS chargé.

    Returns
    -------
    str
        Nom du réseau (ex: "TBM"), ou "Réseau" si agency.txt est vide/absent.
    """
    noms = feed.agency["agency_name"].dropna().unique()
    if len(noms) == 0:
        return "Réseau"
    return " / ".join(noms)


def dates_service (feed):

    dates_service = feed.get_dates() # attention cela dépasse la plage temporelle fiable 
    
    liste_active_trips=[]

    for d in dates_service:
        active_trips = feed.get_trips(date=d)
        len_active_trips=len(active_trips)
        liste_active_trips.append((d,len_active_trips))

    max_services_trips = max(t[1] for t in liste_active_trips) #max du nombre de trips / jour 
    seuil = 0.7 * max_services_trips # pour filtrer les dates avec GTFS pas à jour par hypothèse <70% max nombre de trips jour 
    liste_active_trips = [t for t in liste_active_trips if t[1] >= seuil]

    dates_service = [t[0] for t in liste_active_trips]  # dates fiables uniquement
    date_debut = min(dates_service)
    date_fin = max(dates_service)
    
    #Sélection d'un jour au hasard un mardi ou un jeudi JOB au hasard
    # parmi les dates de service effectivement présentes dans le GTFS
    # (l'année est déduite de dates_service, pas codée en dur)

    dates_parsees = [datetime.strptime(d, "%Y%m%d") for d in dates_service]

    dates__mar_jeu = [
        d.strftime("%Y%m%d") for d in dates_parsees
        if d.weekday() in (1, 3)  # 1=mardi, 3=jeudi
    ]
    date_JOB = max(dates__mar_jeu) if dates__mar_jeu else max(dates_service)
    
    return dates_service, date_debut, date_fin, date_JOB


MOIS = {
    "fr": {
        1: "janvier", 2: "février", 3: "mars", 4: "avril", 5: "mai", 6: "juin",
        7: "juillet", 8: "août", 9: "septembre", 10: "octobre", 11: "novembre", 12: "décembre",
    },
    "en": {
        1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
        7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
    },
}


def formater_date_fr(date_str, lang="fr"):
    d = datetime.strptime(date_str, "%Y%m%d")
    mois = MOIS.get(lang, MOIS["fr"])
    return f"{d.day} {mois[d.month]} {d.year}"


def date_str(date_debut, date_fin, date_JOB, lang="fr"):
    date_service_str = t(
        "commun.periode_service",
        lang,
        debut=formater_date_fr(date_debut, lang),
        fin=formater_date_fr(date_fin, lang),
    )
    date_JOB_text = formater_date_fr(date_JOB, lang)

    return date_service_str, date_JOB_text

    #charge GTFS en feed, longueurs lignes et nom du réseau  

def longueur_par_lignes(feed):

    # Calcul de la longueur des shapes une seule fois, en dehors de la boucle
    return longueur_lignes(feed)

def nom_fichier_valide(texte):
    """
    Remplace les caractères invalides dans un nom de fichier/dossier
    (/, \\, :, *, ?, ", <, >, |) par des tirets, pour que le nom du
    réseau (qui peut contenir des "/" quand plusieurs agences sont
    concaténées) puisse être utilisé sans risque dans un chemin.
    """
    return re.sub(r'[\\/:*?"<>|]', '-', texte).strip()


def nom_reseau_str(feed):
    #cherche nom réseau
    nom_reseau_str = nom_fichier_valide(str(nom_reseau(feed)))
    return nom_reseau_str



def recuperer_logo_reseau(feed, dossier_sortie="output"):
    """
    Va chercher le logo du réseau sur le site officiel de l'agence
    (agency_url dans agency.txt) et le télécharge localement.

    Fonctionne pour n'importe quel GTFS : agency_url est un champ
    obligatoire du standard GTFS. La fonction essaie, dans l'ordre :
    la balise <meta property="og:image">, l'icône apple-touch-icon,
    l'icône favicon <link rel="icon">, puis /favicon.ico en dernier
    recours.

    Parameters
    ----------
    feed : gtfs_kit.Feed
        Le feed GTFS chargé.
    dossier_sortie : str
        Dossier où enregistrer le logo téléchargé.

    Returns
    -------
    str or None
        Chemin local du fichier logo téléchargé, ou None si aucun
        logo n'a pu être trouvé/téléchargé.
    """
    try:
        if "agency_url" not in feed.agency.columns:
            print("⚠ Pas d'agency_url dans le GTFS, impossible de chercher un logo")
            return None

        urls_agence = feed.agency["agency_url"].dropna().unique()
        if len(urls_agence) == 0:
            print("⚠ Pas d'agency_url dans le GTFS, impossible de chercher un logo")
            return None

        url_site = urls_agence[0]
        entetes = {"User-Agent": "Mozilla/5.0 (compatible; gtfs-analysis-bot/1.0)"}

        try:
            reponse = requests.get(url_site, headers=entetes, timeout=10)
            reponse.raise_for_status()
        except requests.RequestException as e:
            print(f"⚠ Impossible de charger {url_site} : {e}")
            return None

        soup = BeautifulSoup(reponse.text, "html.parser")

        url_logo = None
        balise_og = soup.find("meta", property="og:image")
        if balise_og and balise_og.get("content"):
            url_logo = balise_og["content"]
        else:
            icone = soup.find("link", rel=lambda v: v and "apple-touch-icon" in v)
            if not icone:
                icone = soup.find("link", rel=lambda v: v and "icon" in v)
            if icone and icone.get("href"):
                url_logo = icone["href"]

        if url_logo is None:
            # Dernier recours : favicon.ico à la racine du site
            racine = f"{urlparse(url_site).scheme}://{urlparse(url_site).netloc}"
            url_logo = urljoin(racine, "/favicon.ico")
        else:
            url_logo = urljoin(url_site, url_logo)

        try:
            reponse_logo = requests.get(url_logo, headers=entetes, timeout=10)
            reponse_logo.raise_for_status()
        except requests.RequestException as e:
            print(f"⚠ Impossible de télécharger le logo ({url_logo}) : {e}")
            return None

        os.makedirs(dossier_sortie, exist_ok=True)
        extension = os.path.splitext(urlparse(url_logo).path)[1] or ".png"
        nom_fichier = f"logo_{nom_fichier_valide(nom_reseau(feed)).replace(' ', '_')}{extension}"
        chemin_logo = os.path.join(dossier_sortie, nom_fichier)

        with open(chemin_logo, "wb") as f:
            f.write(reponse_logo.content)

        print(f"✓ Logo téléchargé : {chemin_logo}")
        return chemin_logo
    except Exception as e:
        print(f"⚠ Impossible de récupérer le logo du réseau : {e}")
        return None


def chemin_logo(feed):
    #cherche nom réseau
    try:
        return recuperer_logo_reseau(feed, dossier_sortie="output")
    except Exception as e:
        print(f"⚠ Impossible de récupérer le chemin du logo : {e}")
        return None