"""Consolide les comptes des communes (OFGL, 2012-2025) dans une base SQLite contrôlée.

Deux générations de fichiers décrivent les mêmes données :
- 2012-2017 : un CSV par an (colonnes en libellés, « ; », virgule décimale, accents parfois abîmés) ;
- 2018+    : l'API OFGL (colonnes en codes, point décimal).

Usage : python pipeline.py [ANNÉE ...]      (par défaut : toutes les années)
"""
import sqlite3
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

RACINE = Path(__file__).parent
RAW = RACINE / "data" / "raw"
BASE = RACINE / "data" / "communes.db"

ANNEES_ARCHIVE = range(2012, 2018)
ANNEES_API = range(2018, 2026)
URL_ARCHIVE = "https://data.ofgl.fr/api/v2/catalog/datasets/ofgl-base-communes/attachments/ofgl_base_communes_{annee}_pj_zip"
URL_API = "https://data.ofgl.fr/api/explore/v2.1/catalog/datasets/ofgl-base-communes/exports/csv?"

AGREGATS = [
    "Recettes de fonctionnement", "Dépenses de fonctionnement", "Frais de personnel",
    "Impôts locaux", "Dotation globale de fonctionnement", "Epargne brute",
    "Dépenses d'équipement", "Encours de dette",
]
COLONNES = ["annee", "code_insee", "commune", "departement", "population", "agregat", "montant"]
COLONNES_ARCHIVE = {
    "Exercice": "annee", "Code Insee 2025 Commune": "code_insee", "Nom 2025 Commune": "commune",
    "Code Insee 2025 Département": "departement", "Population totale": "population",
    "Agrégat": "agregat", "Montant": "montant", "Type de budget": "type_budget",
}
COLONNES_API = {
    "exer": "annee", "com_code": "code_insee", "com_name": "commune", "dep_code": "departement",
    "ptot": "population", "agregat": "agregat", "montant": "montant",
}


# ---------- Extraction ----------

def telecharger(url: str, cible: Path) -> Path:
    """Télécharge une fois ; un fichier interrompu (.part) n'est jamais pris pour le cache."""
    if not cible.exists():
        cible.parent.mkdir(parents=True, exist_ok=True)
        print(f"  téléchargement {cible.name}…")
        partiel = cible.with_suffix(cible.suffix + ".part")
        urllib.request.urlretrieve(url, partiel)
        partiel.replace(cible)
    return cible


def url_api(annee: int) -> str:
    agregats = ",".join(f'"{a}"' for a in AGREGATS).replace("'", "\\'")
    where = f'year(exer)={annee} and type_de_budget="Budget principal" and agregat in ({agregats})'
    return URL_API + urllib.parse.urlencode({"where": where, "select": ",".join(COLONNES_API)})


# ---------- Transformation ----------

def reparer_encodage(texte: str) -> str:
    """'DÃ©pÃ´ts' -> 'Dépôts' : texte UTF-8 relu par erreur en Windows-1252."""
    if not isinstance(texte, str) or "Ã" not in texte:
        return texte
    for codec in ("cp1252", "latin-1"):
        try:
            return texte.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return texte


def lire_archive(fichier) -> pd.DataFrame:
    """CSV annuel 2012-2017 (zip ou csv) -> lignes au format commun, budget principal uniquement."""
    morceaux = []
    for bloc in pd.read_csv(fichier, sep=";", decimal=",", usecols=list(COLONNES_ARCHIVE),
                            dtype=str, chunksize=500_000, encoding="utf-8"):
        bloc = bloc.rename(columns=COLONNES_ARCHIVE)
        bloc = bloc[bloc["type_budget"] == "Budget principal"].drop(columns="type_budget")
        bloc["agregat"] = bloc["agregat"].map(reparer_encodage)
        morceaux.append(bloc[bloc["agregat"].isin(AGREGATS)])
    df = pd.concat(morceaux, ignore_index=True)
    df["montant"] = df["montant"].str.replace(",", ".", regex=False)
    return typer(df)


def lire_api(fichier) -> pd.DataFrame:
    df = pd.read_csv(fichier, sep=";", dtype=str, encoding="utf-8-sig").rename(columns=COLONNES_API)
    return typer(df)


def typer(df: pd.DataFrame) -> pd.DataFrame:
    df = df[COLONNES].copy()
    df["annee"] = df["annee"].astype(int)
    df["population"] = pd.to_numeric(df["population"], errors="coerce").astype("Int64")
    df["montant"] = pd.to_numeric(df["montant"], errors="raise")  # un montant illisible doit arrêter le pipeline
    for col in ("code_insee", "departement"):
        df[col] = df[col].str.strip()
    return regrouper_communes_nouvelles(df)


def regrouper_communes_nouvelles(df: pd.DataFrame) -> pd.DataFrame:
    """Les fichiers rangent les anciennes communes sous leur code INSEE 2025 : on additionne
    montants et populations pour suivre chaque commune sur son territoire actuel."""
    cle = ["annee", "code_insee", "agregat"]
    df = df.groupby(cle, as_index=False).agg(
        commune=("commune", "first"), departement=("departement", "first"),
        population=("population", lambda p: p.sum(min_count=1)), montant=("montant", "sum"),
    )
    return df[COLONNES]


# ---------- Chargement ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS comptes (
    annee INTEGER NOT NULL, code_insee TEXT NOT NULL, commune TEXT, departement TEXT,
    population INTEGER, agregat TEXT NOT NULL, montant REAL NOT NULL,
    PRIMARY KEY (annee, code_insee, agregat)
);
DROP VIEW IF EXISTS indicateurs;
CREATE VIEW indicateurs AS
SELECT annee, code_insee, MAX(commune) AS commune, MAX(departement) AS departement,
       MAX(population) AS population,
       SUM(CASE WHEN agregat = 'Recettes de fonctionnement' THEN montant END) AS recettes_fonct,
       SUM(CASE WHEN agregat = 'Dépenses de fonctionnement' THEN montant END) AS depenses_fonct,
       SUM(CASE WHEN agregat = 'Epargne brute' THEN montant END) AS epargne_brute,
       SUM(CASE WHEN agregat = 'Dépenses d''équipement' THEN montant END) AS depenses_equipement,
       SUM(CASE WHEN agregat = 'Encours de dette' THEN montant END) AS encours_dette,
       SUM(CASE WHEN agregat = 'Encours de dette' THEN montant END) / NULLIF(MAX(population), 0) AS dette_par_habitant,
       SUM(CASE WHEN agregat = 'Epargne brute' THEN montant END)
         / NULLIF(SUM(CASE WHEN agregat = 'Recettes de fonctionnement' THEN montant END), 0) AS taux_epargne_brute,
       SUM(CASE WHEN agregat = 'Encours de dette' THEN montant END)
         / NULLIF(SUM(CASE WHEN agregat = 'Epargne brute' THEN montant END), 0) AS capacite_desendettement_ans
FROM comptes GROUP BY annee, code_insee;
"""


def charger(con: sqlite3.Connection, df: pd.DataFrame, annee: int) -> None:
    """Remplace l'année entière : relancer le pipeline ne crée jamais de doublons."""
    with con:
        con.execute("DELETE FROM comptes WHERE annee = ?", (annee,))
        df.to_sql("comptes", con, if_exists="append", index=False)


# ---------- Contrôles qualité ----------

def controles(con: sqlite3.Connection) -> pd.DataFrame:
    """Une ligne par (contrôle, année) en anomalie. Sauvé dans la table `controles`."""
    requetes = {
        "Commune présente l'année précédente mais absente": """
            SELECT a.annee + 1 AS annee, COUNT(DISTINCT a.code_insee) AS nb FROM comptes a
            WHERE a.annee + 1 IN (SELECT annee FROM comptes)
              AND NOT EXISTS (SELECT 1 FROM comptes b WHERE b.annee = a.annee + 1 AND b.code_insee = a.code_insee)
            GROUP BY a.annee""",
        "Agrégat absent (le plus souvent : aucune DGF perçue)": f"""
            SELECT annee, COUNT(*) AS nb FROM (
              SELECT annee, code_insee FROM comptes GROUP BY annee, code_insee HAVING COUNT(*) < {len(AGREGATS)})
            GROUP BY annee""",
        "Commune non rattachée à la géographie 2025 dans la source": """
            SELECT annee, COUNT(DISTINCT code_insee) AS nb FROM comptes
            WHERE commune IS NULL OR departement IS NULL GROUP BY annee""",
        "Population absente ou nulle": """
            SELECT annee, COUNT(DISTINCT code_insee) AS nb FROM comptes
            WHERE population IS NULL OR population = 0 GROUP BY annee""",
        "Montant négatif impossible (recettes, dépenses, dette)": """
            SELECT annee, COUNT(*) AS nb FROM comptes
            WHERE montant < 0 AND agregat IN ('Recettes de fonctionnement', 'Dépenses de fonctionnement', 'Encours de dette')
            GROUP BY annee""",
        # tolérance 10 € : additionner les anciennes communes d'une commune nouvelle additionne aussi leurs arrondis
        "Épargne brute ≠ recettes − dépenses de fonctionnement (écart > 10 €)": """
            SELECT annee, COUNT(*) AS nb FROM indicateurs
            WHERE ABS(epargne_brute - (recettes_fonct - depenses_fonct)) > 10 GROUP BY annee""",
        "Accents abîmés restants": """
            SELECT annee, COUNT(*) AS nb FROM comptes WHERE agregat LIKE '%Ã%' OR commune LIKE '%Ã%' GROUP BY annee""",
    }
    lignes = [
        {"controle": nom, "annee": annee, "nb": nb}
        for nom, sql in requetes.items()
        for annee, nb in con.execute(sql)
        if nb
    ]
    rapport = pd.DataFrame(lignes, columns=["controle", "annee", "nb"])
    rapport.to_sql("controles", con, if_exists="replace", index=False)
    return rapport


# ---------- Orchestration ----------

def traiter(annee: int, con: sqlite3.Connection) -> None:
    if annee in ANNEES_ARCHIVE:
        df = lire_archive(telecharger(URL_ARCHIVE.format(annee=annee), RAW / f"ofgl_{annee}.zip"))
    elif annee in ANNEES_API:
        df = lire_api(telecharger(url_api(annee), RAW / f"ofgl_api_{annee}.csv"))
    else:
        raise ValueError(f"année non disponible : {annee}")
    charger(con, df, annee)
    print(f"{annee} : {len(df):>7} lignes, {df['code_insee'].nunique():>6} communes")


def main(annees: list[int]) -> None:
    BASE.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(BASE)
    con.executescript(SCHEMA)
    for annee in annees:
        traiter(annee, con)
    rapport = controles(con)
    print("\nContrôles qualité :", "aucune anomalie" if rapport.empty else "")
    if not rapport.empty:
        print(rapport.pivot_table(index="controle", columns="annee", values="nb", fill_value=0).to_string())
    con.close()


if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]] or [*ANNEES_ARCHIVE, *ANNEES_API])
