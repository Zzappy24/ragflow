"""Recettes analytiques FAMAT — POC dérive process.

Module autonome : seules dépendances duckdb, matplotlib, pymysql.
Cuit dans l'image sandbox custom ET utilisé hors ligne pour les tests.
"""
import duckdb

_EVENTS_SCHEMA = "seq BIGINT, serial VARCHAR, chapter INTEGER, cle VARCHAR, value_num DOUBLE, ts TIMESTAMP"


def load_csv(path: str) -> duckdb.DuckDBPyConnection:
    """CSV webhook brut (séparateur ';', BOM utf-8) -> table `events`."""
    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE events AS
        SELECT
            webhookmesure_id AS seq,
            json_extract_string(webhookmesure_contenu, '$.Serial')  AS serial,
            TRY_CAST(json_extract_string(webhookmesure_contenu, '$.Chapter') AS INTEGER) AS chapter,
            json_extract_string(webhookmesure_contenu, '$.cle')     AS cle,
            TRY_CAST(json_extract_string(webhookmesure_contenu, '$.value') AS DOUBLE)   AS value_num,
            TRY_CAST(webhookmesure_datecreation AS TIMESTAMP)       AS ts
        FROM read_csv(?, delim=';', header=true, normalize_names=true)
        """,
        [path],
    )
    return con


def load_rows(rows) -> duckdb.DuckDBPyConnection:
    """Tuples (seq, serial, chapter, cle, value_num, ts) -> table `events`."""
    con = duckdb.connect()
    con.execute(f"CREATE TABLE events ({_EVENTS_SCHEMA})")
    con.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", rows)
    return con
