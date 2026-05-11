#!/usr/bin/env python3
import argparse
import sqlite3
from pathlib import Path


IGNORED_RESOLUTIONS = {"ignore", "ignored", "approve"}
PENALTY_RESOLUTIONS = {"warn", "strike", "delete", "deleted", "delete_message", "banned"}


def pct(part, total):
    if not total:
        return "n/a"
    return f"{(part / total) * 100:.1f}%"


def table_exists(cur, table):
    row = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def column_exists(cur, table, column):
    return column in [row[1] for row in cur.execute(f"PRAGMA table_info({table})")]


def print_case_precision(cur):
    rows = cur.execute(
        """
        SELECT type, status, COALESCE(resolution, '') AS resolution, COUNT(*) AS total
        FROM cases
        GROUP BY type, status, resolution
        ORDER BY type, total DESC
        """
    ).fetchall()

    by_type = {}
    for row in rows:
        case_type = row["type"] or "unknown"
        stats = by_type.setdefault(case_type, {
            "total": 0,
            "pending": 0,
            "ignored": 0,
            "penalized": 0,
            "other": 0,
        })
        stats["total"] += row["total"]
        if row["status"] in {"pending", "in_review"}:
            stats["pending"] += row["total"]
        elif row["resolution"] in IGNORED_RESOLUTIONS:
            stats["ignored"] += row["total"]
        elif row["resolution"] in PENALTY_RESOLUTIONS:
            stats["penalized"] += row["total"]
        else:
            stats["other"] += row["total"]

    print("\nPrecision por tipo de caso")
    for case_type, stats in by_type.items():
        decided = stats["ignored"] + stats["penalized"]
        print(
            f"- {case_type}: {stats['penalized']}/{decided} aciertos utiles "
            f"({pct(stats['penalized'], decided)}), "
            f"{stats['ignored']} falsos positivos, {stats['pending']} pendientes"
        )


def print_classification_accuracy(cur):
    has_category = column_exists(cur, "messages", "category_label")
    has_review = column_exists(cur, "messages", "reviewed_category_label")
    if not has_category or not has_review:
        print("\nClasificacion: la base todavia no tiene columnas de categoria/revision.")
        print("Arranca la API una vez para ejecutar ensure_sqlite_schema().")
        return

    reviewed = cur.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN category_label = reviewed_category_label THEN 1 ELSE 0 END) AS category_hits,
               SUM(CASE WHEN intent_label = reviewed_intent_label THEN 1 ELSE 0 END) AS intent_hits
        FROM messages
        WHERE reviewed_category_label IS NOT NULL OR reviewed_intent_label IS NOT NULL
        """
    ).fetchone()
    total = reviewed["total"] or 0
    print("\nExactitud por revision manual")
    print(f"- mensajes revisados: {total}")
    print(f"- categoria: {reviewed['category_hits'] or 0}/{total} ({pct(reviewed['category_hits'] or 0, total)})")
    print(f"- intencion: {reviewed['intent_hits'] or 0}/{total} ({pct(reviewed['intent_hits'] or 0, total)})")


def main():
    parser = argparse.ArgumentParser(description="Reporte local de analitica del bot moderador.")
    parser.add_argument("--db", default="bot.db", help="Ruta al archivo SQLite.")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"No existe la base: {db_path}")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print(f"Base: {db_path}")
    for table in ["users", "messages", "cases", "user_actions", "moderators"]:
        if table_exists(cur, table):
            total = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"- {table}: {total}")

    if table_exists(cur, "messages"):
        print("\nMensajes por tipo")
        for row in cur.execute(
            """
            SELECT message_type, flagged, deleted, COUNT(*) AS total
            FROM messages
            GROUP BY message_type, flagged, deleted
            ORDER BY total DESC
            """
        ):
            print(
                f"- {row['message_type']}: {row['total']} "
                f"(flagged={row['flagged']}, deleted={row['deleted']})"
            )

    if table_exists(cur, "cases"):
        print_case_precision(cur)

    if table_exists(cur, "messages"):
        print_classification_accuracy(cur)

    print("\nLectura rapida")
    print("- Si precision es baja, el bot genera mucho trabajo manual.")
    print("- Si hay pocos revisados, primero etiqueta una muestra antes de confiar en porcentajes.")
    print("- Las preguntas y quejas frecuentes son buen material para cargar en knowledge_base.")


if __name__ == "__main__":
    main()
