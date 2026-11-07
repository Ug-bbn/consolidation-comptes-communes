# Consolidation des comptes des communes 2012-2025

![tests](https://github.com/Ug-bbn/consolidation-comptes-communes/actions/workflows/tests.yml/badge.svg)

> 🇬🇧 *An ETL pipeline in Python that merges 14 years of French municipal accounts, published in two different file formats, into one clean, quality-checked SQLite database (3.9M rows, ~35,000 municipalities). It shows how I turn scattered, inconsistent files into reliable data.*

## Le problème

L'Observatoire des finances et de la gestion publique locales (OFGL) publie les comptes des 35 000 communes françaises sur [data.gouv.fr](https://www.data.gouv.fr/datasets/comptes-des-communes-2017-2024). Pour suivre une commune sur la durée, il faut pourtant assembler **deux générations de fichiers** qui ne se ressemblent pas :

| | 2012-2017 | 2018-2025 |
|---|---|---|
| Source | un CSV d'environ 1 Go par an, dans un zip | API OFGL |
| Colonnes | en toutes lettres (`Code Insee 2025 Commune`) | en codes (`com_code`) |
| Nombres | virgule décimale (`1000,5`) | point décimal (`1000.5`) |
| Encodage | accents abîmés en 2012 (`DÃ©pÃ´ts au TrÃ©sor`) | correct |

À cela s'ajoutent des pièges métier. Les **communes nouvelles** (issues de fusions) apparaissent sur plusieurs lignes dans les années anciennes, et certaines communes ne sont **pas rattachées** à la géographie actuelle.

C'est le quotidien de beaucoup d'équipes : les mêmes données arrivent dans des formats qui changent selon l'année, le service ou le fournisseur.

## La solution

`pipeline.py` télécharge, harmonise, contrôle et charge le tout dans une base **SQLite** unique :

1. **Extraction :** télécharge chaque année une seule fois, sans jamais prendre un téléchargement interrompu pour un fichier complet.
2. **Transformation :** lit les deux formats et les ramène au même schéma, répare les accents, garde le budget principal et 8 agrégats clés, et additionne les anciennes communes d'une commune nouvelle pour la suivre sur son territoire actuel.
3. **Chargement :** chaque année remplace la précédente, donc relancer le pipeline ne crée jamais de doublons. Une vue `indicateurs` calcule les ratios d'analyse financière : dette par habitant, taux d'épargne brute, capacité de désendettement.
4. **Contrôles qualité :** un rapport des anomalies par année, enregistré dans la table `controles`.

C'est la même démarche que mon pipeline à la Direction de la Sécurité sociale, qui normalisait les fichiers Excel de 15 caisses de retraite et les chargeait en base.

## Ce que révèlent les contrôles qualité

| Contrôle | Résultat | Explication |
|---|---|---|
| Accents abîmés | 0 après réparation | Tous les libellés de 2012 sont corrigés. |
| Communes absentes en 2025 | **97** | Correspond exactement aux 97 communes que l'OFGL annonce comme pas encore publiées. |
| Communes « disparues » en 2018 | 57 | Ce sont les 57 communes que le fichier 2017 ne rattache pas à la géographie 2025 : l'anomalie est dans la source. |
| Dette ou recettes négatives | 8 à 19 par an | Vraies anomalies comptables à remonter (par exemple −28 483 € de dette en 2012 à Saint-Ouen-de-Thouberville). |
| Agrégat absent | 3 à 521 par an | Surtout des communes sans dotation globale de fonctionnement (DGF) : la ligne est absente au lieu de valoir 0. |
| Épargne brute ≠ recettes − dépenses | 0 au-delà de 10 € | Le recalcul confirme la cohérence comptable. Les écarts d'1 à 2 € viennent des arrondis cumulés des communes nouvelles. |

## Quelques résultats

Évolution nationale (budget principal des communes, en milliards d'euros) :

| Année | Dette | Épargne brute | Dépenses d'équipement |
|---|---|---|---|
| 2012 | 61,4 | 12,6 | 21,5 |
| 2015 | 64,3 | 11,1 | 17,1 |
| 2020 | 64,9 | 12,0 | 18,9 |
| 2025 | 68,5 | 14,6 | 28,8 |

On voit le creux d'investissement de 2014-2016, après la baisse des dotations de l'État, puis la reprise jusqu'en 2025.

Exemples de requêtes sur la base :

```sql
-- Communes de plus de 10 000 habitants les plus longues à se désendetter en 2025
SELECT commune, departement, population, ROUND(capacite_desendettement_ans, 1) AS annees
FROM indicateurs
WHERE annee = 2025 AND population >= 10000 AND epargne_brute > 0
ORDER BY capacite_desendettement_ans DESC LIMIT 10;

-- Trajectoire d'une commune
SELECT annee, encours_dette, epargne_brute, ROUND(dette_par_habitant) AS dette_hab
FROM indicateurs WHERE commune = 'Vierzon' ORDER BY annee;
```

## Lancer le projet

```bash
pip install -r requirements.txt
pytest                         # tests sur de petits échantillons des deux formats (quelques secondes)
python pipeline.py 2024 2025   # deux années, quelques minutes
python pipeline.py             # tout 2012-2025 : environ 1,3 Go à télécharger, 15-20 min, base de 430 Mo
```

La base est créée dans `data/communes.db` et s'ouvre avec n'importe quel outil SQLite (DB Browser for SQLite, DBeaver, ou pandas).

## Tests

Les tests (`tests/test_pipeline.py`) reproduisent les deux formats en miniature et vérifient notamment que :
- les deux formats donnent **exactement** le même résultat ;
- les accents sont réparés, et un texte déjà correct n'est pas modifié ;
- une commune nouvelle est bien regroupée (montants et populations additionnés) ;
- les codes INSEE gardent leur zéro initial (`01015`) ;
- un montant illisible arrête le pipeline au lieu de passer inaperçu ;
- relancer le chargement ne duplique rien ;
- les contrôles détectent une commune disparue et une dette négative.

## Sources

- [Comptes des communes 2018-2025 — OFGL, data.gouv.fr](https://www.data.gouv.fr/datasets/comptes-des-communes-2017-2024)
- [Méthode de calcul des agrégats financiers — OFGL](https://data.ofgl.fr/pages/methodologie-agregats-financiers/)

---
Hugues Barbier, data scientist Python freelance : automatisation Excel, ETL et fiabilisation de données.
