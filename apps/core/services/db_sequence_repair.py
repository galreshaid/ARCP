from __future__ import annotations

from django.db import connections


def repair_postgres_sequences(*, using: str = "default") -> int:
    """
    Align all PostgreSQL serial/identity sequences to the next available ID.
    Returns the number of repaired sequences.
    """
    connection = connections[using]
    if connection.vendor != "postgresql":
        return 0

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                n.nspname AS schema_name,
                c.relname AS table_name,
                a.attname AS column_name,
                pg_get_serial_sequence(
                    format('%I.%I', n.nspname, c.relname),
                    a.attname
                ) AS sequence_name
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid
            WHERE c.relkind = 'r'
              AND a.attnum > 0
              AND NOT a.attisdropped
              AND pg_get_serial_sequence(
                    format('%I.%I', n.nspname, c.relname),
                    a.attname
                ) IS NOT NULL
            ORDER BY n.nspname, c.relname, a.attnum
            """
        )
        sequence_rows = cursor.fetchall()
        if not sequence_rows:
            return 0

        repaired_count = 0
        for schema_name, table_name, column_name, sequence_name in sequence_rows:
            safe_schema = schema_name.replace('"', '""')
            safe_table = table_name.replace('"', '""')
            safe_column = column_name.replace('"', '""')
            table_sql = f'"{safe_schema}"."{safe_table}"'
            column_sql = f'"{safe_column}"'

            cursor.execute(f"SELECT COALESCE(MAX({column_sql}), 0) FROM {table_sql}")
            max_id = int(cursor.fetchone()[0] or 0)
            next_id = max_id + 1
            cursor.execute("SELECT setval(%s, %s, false)", [sequence_name, next_id])
            repaired_count += 1

        return repaired_count
