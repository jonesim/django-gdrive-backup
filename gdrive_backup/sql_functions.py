from django.db import connection


def get_schemas():
    with connection.cursor() as cursor:
        # cursor.execute('SELECT schema.schema_name, Count(tables.table_name)'
        #                'FROM information_schema.schemata as schema '
        #        'Left Join information_schema.tables as tables on tables.table_schema = schema.schema_name '
        #                    'GROUP BY schema.schema_name')

        cursor.execute(
            'SELECT pg_catalog.pg_namespace.nspname, pg_size_pretty(SUM(pg_relation_size(pg_catalog.pg_class.oid))) '
            'FROM pg_catalog.pg_class '
            'LEFT JOIN pg_catalog.pg_namespace ON relnamespace = pg_catalog.pg_namespace.oid '
            'GROUP BY pg_catalog.pg_namespace.nspname'
        )
        return [schema for schema in cursor.fetchall() if not schema[0].startswith('pg_')
                and schema[0] not in ['information_schema']]


def get_schema_tables(schema):
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT table_name, pg_relation_size(table_schema||'.'||table_name)"
                       f"from information_schema.tables where table_schema='{schema}'")
        return cursor.fetchall()


def get_table_column_names(schema, table_name):
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT column_name from INFORMATION_SCHEMA.COLUMNS WHERE "
                       f"table_name='{table_name}' and table_schema='{schema}'")
        return [c[0] for c in cursor.fetchall()]


def get_table_data(schema, table_name):
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT * from {schema}.{table_name}")
        return cursor.fetchall()


def delete_table(schema, table_name):
    with connection.cursor() as cursor:
        cursor.execute(f'DELETE from {schema}.{table_name}')
