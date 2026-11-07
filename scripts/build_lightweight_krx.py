"""Create a separate development subset; source databases are attached read-only."""
from contextlib import closing
import argparse
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]


def refresh_daily(source, target):
    """Sync immutable snapshot IDs in place; reuse freed pages, no full DB clone."""
    if target.is_symlink() or target.resolve().parent != ROOT/'diagnostics/lightweight-20261105':
        raise ValueError('owned_lightweight_target_required')
    with closing(sqlite3.connect(target, uri=True)) as db:
        db.execute('ATTACH DATABASE ? AS source', (source.as_uri()+'?mode=ro',))
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TEMP TABLE keep_days AS SELECT DISTINCT day FROM source.daily_latest ORDER BY day DESC LIMIT 120')
        db.execute('CREATE TEMP TABLE keep_ids AS SELECT id FROM source.snapshots WHERE day >= (SELECT min(day) FROM keep_days)')
        db.execute('CREATE TEMP TABLE new_ids AS SELECT id FROM keep_ids EXCEPT SELECT id FROM snapshots')
        changed = (db.execute('SELECT count(*) FROM new_ids').fetchone()[0] or
                   db.execute('SELECT count(*) FROM snapshots WHERE id NOT IN (SELECT id FROM keep_ids)').fetchone()[0] or
                   db.execute('''SELECT count(*) FROM (SELECT * FROM market_days EXCEPT
                       SELECT * FROM source.market_days WHERE day >= (SELECT min(day) FROM keep_days))''').fetchone()[0] or
                   db.execute('''SELECT count(*) FROM (SELECT * FROM source.market_days WHERE day >= (SELECT min(day) FROM keep_days)
                       EXCEPT SELECT * FROM market_days)''').fetchone()[0])
        if not changed:
            db.rollback()
            return False
        for table in ('raw_rows','bars','attempts'):
            db.execute(f'DELETE FROM {table} WHERE snapshot_id NOT IN (SELECT id FROM keep_ids)')
        db.execute('DELETE FROM snapshots WHERE id NOT IN (SELECT id FROM keep_ids)')
        db.execute('INSERT INTO snapshots SELECT * FROM source.snapshots WHERE id IN (SELECT id FROM new_ids)')
        for table in ('raw_rows','bars'):
            db.execute(f'INSERT INTO {table} SELECT * FROM source.{table} WHERE snapshot_id IN (SELECT id FROM new_ids)')
        db.execute('DELETE FROM attempts')
        db.execute('INSERT INTO attempts SELECT * FROM source.attempts WHERE day IN (SELECT day FROM keep_days)')
        db.execute('DELETE FROM market_days')
        db.execute('INSERT INTO market_days SELECT * FROM source.market_days WHERE day >= (SELECT min(day) FROM keep_days)')
        db.execute('DELETE FROM missing_sessions')
        db.execute('INSERT INTO missing_sessions SELECT * FROM source.missing_sessions WHERE day >= (SELECT min(day) FROM keep_days)')
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        db.commit()
        assert db.execute('PRAGMA quick_check').fetchall() == [('ok',)]
    return True


def refresh_adr(source, target):
    with closing(sqlite3.connect(target, uri=True)) as db:
        db.execute('ATTACH DATABASE ? AS source', (source.as_uri()+'?mode=ro',))
        with db:
            for table in ('aq_meta','breadth_daily','adr20'):
                db.execute(f'DELETE FROM {table}')
                predicate = '' if table=='aq_meta' else ' WHERE day IN (SELECT DISTINCT day FROM source.breadth_daily ORDER BY day DESC LIMIT 60)'
                db.execute(f'INSERT INTO {table} SELECT * FROM source.{table}'+predicate)
        assert db.execute('PRAGMA quick_check').fetchall() == [('ok',)]


def subset(source, target, daily=False):
    if target.exists():
        raise FileExistsError(target)
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)) as src:
        schema = src.execute("SELECT type,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'").fetchall()
    with closing(sqlite3.connect(target, uri=True)) as db:
        db.execute('ATTACH DATABASE ? AS source', (source.as_uri()+'?mode=ro',))
        for kind, sql in schema:
            if kind == 'table':
                db.execute(sql)
        if daily:
            db.execute('CREATE TEMP TABLE keep_days AS SELECT DISTINCT day FROM source.daily_latest ORDER BY day DESC LIMIT 120')
            predicates = dict(meta='1', snapshots='day >= (SELECT min(day) FROM keep_days)',
                attempts='day IN (SELECT day FROM keep_days)',
                raw_rows='snapshot_id IN (SELECT id FROM snapshots)',
                bars='day IN (SELECT day FROM keep_days)',
                market_days='day >= (SELECT min(day) FROM keep_days)',
                missing_sessions='day >= (SELECT min(day) FROM keep_days)')
        else:
            db.execute('CREATE TEMP TABLE keep_days AS SELECT DISTINCT day FROM source.breadth_daily ORDER BY day DESC LIMIT 60')
            predicates = dict(aq_meta='1', breadth_daily='day IN (SELECT day FROM keep_days)',
                              adr20='day IN (SELECT day FROM keep_days)')
        for table, predicate in predicates.items():
            db.execute(f'INSERT INTO main.{table} SELECT * FROM source.{table} WHERE {predicate}')
        for kind, sql in schema:
            if kind != 'table':
                db.execute(sql)
        if daily:
            db.execute('''CREATE VIEW adr_source_latest AS SELECT * FROM daily_latest
                WHERE day IN (SELECT DISTINCT day FROM daily_latest ORDER BY day DESC LIMIT 60)''')
        else:
            db.execute('INSERT INTO aq_meta VALUES (?,?)', ('lightweight_policy', json.dumps(
                {'retained_sessions':60,'source':str(source),'stored_adr_values_preserved':True})))
        db.commit()
        assert db.execute('PRAGMA quick_check').fetchall() == [('ok',)]
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh',action='store_true',help='Manually update existing lightweight DBs only')
    args=parser.parse_args()
    output = ROOT/'diagnostics/lightweight-20261105'
    if output.resolve()!=output or output.is_symlink():
        raise ValueError('regular_development_output_required')
    if not args.refresh:
        output.mkdir(exist_ok=False)
    source = ROOT/'diagnostics/krx-daily/as-incremental.db'
    pointer = json.loads((ROOT/'diagnostics/krx-as/latest-adr.json').read_text(encoding='utf8'))
    adr = Path(pointer['output'])/'adr20.db'
    assert adr.resolve().is_relative_to(ROOT)
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as a, closing(sqlite3.connect(adr.as_uri()+'?mode=ro',uri=True)) as b:
        if a.execute('SELECT max(day) FROM daily_latest').fetchone()!=b.execute('SELECT max(day) FROM adr20').fetchone():
            raise ValueError('Full ADR output is stale; rebuild it with the existing collector first')
    if args.refresh:
        for name in ('daily-120.db','adr-60.db','theme-master.sqlite'):
            target=output/name
            if not target.is_file() or target.is_symlink() or target.resolve()!=target:
                raise ValueError('existing_regular_lightweight_files_required')
        changed=refresh_daily(source,output/'daily-120.db')
        if changed:
            refresh_adr(adr,output/'adr-60.db')
    else:
        subset(source, output/'daily-120.db', daily=True)
        subset(adr, output/'adr-60.db')
    theme = Path(r'C:\Users\dada1\Downloads\stock1\data\reference\ARVIS_Theme_Master.sqlite')
    if not args.refresh:
        with closing(sqlite3.connect(theme.as_uri()+'?mode=ro', uri=True)) as src:
            with closing(sqlite3.connect(output/'theme-master.sqlite')) as dest:
                src.backup(dest)
    result = {}
    if args.refresh:
        result['refreshed']=bool(changed)
    with closing(sqlite3.connect((output/'daily-120.db').as_uri()+'?mode=ro', uri=True)) as db:
        for view in ('daily_latest','adr_source_latest'):
            result[view] = db.execute(f'SELECT min(day),max(day),count(distinct day),count(*) FROM {view}').fetchone()
    with closing(sqlite3.connect((output/'adr-60.db').as_uri()+'?mode=ro', uri=True)) as db:
        for table in ('breadth_daily','adr20'):
            result[table] = db.execute(f'SELECT min(day),max(day),count(distinct day),count(*) FROM {table}').fetchone()
    result['bytes'] = {p.name:p.stat().st_size for p in output.iterdir() if p.is_file()}
    (output/'summary.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
