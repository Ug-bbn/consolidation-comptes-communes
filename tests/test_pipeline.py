import io
import sqlite3

import pandas as pd
import pytest

import pipeline as p

ENTETE_ARCHIVE = ('"Exercice";"Code Insee 2025 Département";"Code Insee 2025 Commune";"Nom 2025 Commune";'
                  '"Type de budget";"Agrégat";"Montant";"Population totale"\n')

# Même contenu dans les deux formats : une commune simple (22302) et une commune nouvelle (01015)
# issue de deux anciennes communes. L'archive a des accents abîmés, une virgule décimale,
# un budget annexe et un agrégat hors périmètre, qui doivent disparaître.
ARCHIVE = ENTETE_ARCHIVE + "\n".join([
    '2012;"22";"22302";"Saint-Jacut-de-la-Mer";"Budget principal";"Encours de dette";1000,5;862',
    '2012;"22";"22302";"Saint-Jacut-de-la-Mer";"Budget principal";"DÃ©penses de fonctionnement";400,25;862',
    '2012;"22";"22302";"Saint-Jacut-de-la-Mer";"Budget annexe";"Encours de dette";999;862',
    '2012;"22";"22302";"Saint-Jacut-de-la-Mer";"Budget principal";"DÃ©pÃ´ts au TrÃ©sor";5;862',
    '2012;"01";"01015";"Arboys en Bugey";"Budget principal";"Encours de dette";300;483',
    '2012;"01";"01015";"Arboys en Bugey";"Budget principal";"Encours de dette";200;123',
])
API = "﻿exer;com_code;com_name;dep_code;ptot;agregat;montant\n" + "\n".join([
    "2012;22302;Saint-Jacut-de-la-Mer;22;862;Encours de dette;1000.5",
    "2012;22302;Saint-Jacut-de-la-Mer;22;862;Dépenses de fonctionnement;400.25",
    "2012;01015;Arboys en Bugey;01;483;Encours de dette;300",
    "2012;01015;Arboys en Bugey;01;123;Encours de dette;200",
])


def trier(df):
    return df.sort_values(["code_insee", "agregat"]).reset_index(drop=True)


@pytest.mark.parametrize("abime,attendu", [
    ("DÃ©pÃ´ts au TrÃ©sor", "Dépôts au Trésor"),
    ("CapacitÃ© ou besoin de financement", "Capacité ou besoin de financement"),
    ("Dépenses d'équipement", "Dépenses d'équipement"),  # déjà correct : inchangé
    (None, None),
])
def test_reparer_encodage(abime, attendu):
    assert p.reparer_encodage(abime) == attendu


def test_archive_filtre_repare_et_type():
    df = trier(p.lire_archive(io.StringIO(ARCHIVE)))
    assert list(df.columns) == p.COLONNES
    assert len(df) == 3  # budget annexe et agrégat hors périmètre exclus
    assert "Dépenses de fonctionnement" in set(df["agregat"])
    assert df.loc[df["code_insee"] == "22302", "montant"].tolist() == [400.25, 1000.5]


def test_commune_nouvelle_regroupee():
    ligne = p.lire_archive(io.StringIO(ARCHIVE)).query("code_insee == '01015'")
    assert len(ligne) == 1
    assert ligne["montant"].item() == 500 and ligne["population"].item() == 606


def test_les_deux_formats_donnent_le_meme_resultat():
    pd.testing.assert_frame_equal(trier(p.lire_archive(io.StringIO(ARCHIVE))), trier(p.lire_api(io.StringIO(API))))


def test_code_insee_garde_ses_zeros():
    assert "01015" in set(p.lire_api(io.StringIO(API))["code_insee"])


def test_montant_illisible_arrete_le_pipeline():
    with pytest.raises(ValueError):
        p.lire_api(io.StringIO(API.replace("1000.5", "mille")))


@pytest.fixture
def base():
    con = sqlite3.connect(":memory:")
    con.executescript(p.SCHEMA)
    yield con
    con.close()


def test_chargement_rejouable(base):
    df = p.lire_api(io.StringIO(API))
    p.charger(base, df, 2012)
    p.charger(base, df, 2012)
    assert base.execute("SELECT COUNT(*) FROM comptes").fetchone()[0] == len(df)


def test_controles_detectent_les_anomalies(base):
    df = p.lire_api(io.StringIO(API))
    p.charger(base, df, 2012)
    suivant = df[df["code_insee"] == "22302"].assign(annee=2013)
    suivant.loc[suivant["agregat"] == "Encours de dette", "montant"] = -50
    p.charger(base, suivant, 2013)
    rapport = p.controles(base).set_index(["controle", "annee"])["nb"]
    assert rapport["Commune présente l'année précédente mais absente", 2013] == 1  # 01015 a disparu
    assert rapport["Montant négatif impossible (recettes, dépenses, dette)", 2013] == 1
