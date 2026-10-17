"""Resumable all-source OpenAlex extraction for OJF, citation years 2022-2026.

The caller supplies the explicitly configured public NF/ANS run, whose input
manifests and Norwegian list are checked by opindx-replicate.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import duckdb
import pyarrow.parquet as pq
from .. import pipeline as nf

def sql_literal(value: str | Path) -> str:
    return "'" + str(value).replace("\\", "/").replace("'", "''") + "'"

def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def hash_json(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with partial.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(partial, path)

def completed_stage(con, path: Path, sql: str, contract: str) -> dict:
    receipt_path = path.with_suffix(".receipt.json")
    query_hash = hashlib.sha256(sql.encode()).hexdigest()
    if path.exists() != receipt_path.exists():
        raise RuntimeError(f"Orphan output or receipt: {path}")
    partial = path.with_name(path.name + ".partial")
    if partial.exists():
        raise RuntimeError(f"Inspect interrupted partial before resuming: {partial}")
    if path.exists():
        receipt = read_json(receipt_path)
        if (receipt["contract_sha256"] != contract or receipt["sql_sha256"] != query_hash
                or receipt["bytes"] != path.stat().st_size
                or receipt["sha256"] != hash_file(path)):
            raise RuntimeError(f"Changed completed checkpoint: {path}")
        return {**receipt, "path": path.as_posix(), "reused": True}
    path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    con.execute(f"COPY ({sql}) TO {sql_literal(partial)} (FORMAT PARQUET, COMPRESSION ZSTD)")
    os.replace(partial, path)
    rows = pq.ParquetFile(path).metadata.num_rows
    receipt = {"contract_sha256": contract, "sql_sha256": query_hash,
               "bytes": path.stat().st_size, "sha256": hash_file(path),
               "rows": rows, "seconds": time.monotonic() - start}
    atomic_json(receipt_path, receipt)
    return {**receipt, "path": path.as_posix(), "reused": False}

def checkpoint_sql(files):
    paths = '[' + ','.join(sql_literal(p) for p in files) + ']'
    return f"""
      SELECT id AS work_id, primary_location.source.id AS source_id,
        primary_location.source.issn_l AS raw_issn_l,
        primary_location.source.type AS source_type,
        publication_year AS year, publication_date, type AS work_type,
        type IN ('article','review') AS is_ar,
        coalesce(referenced_works_count,0)::BIGINT AS refs_count_reported,
        coalesce(list_count(referenced_works),0)::BIGINT AS observed_references,
        coalesce(referenced_works_count,0)>0 AS has_refs,
        CASE WHEN publication_year BETWEEN 2022 AND 2026 THEN referenced_works ELSE NULL END AS refs,
        coalesce(is_paratext,false) AS is_paratext,
        coalesce(is_retracted,false) AS is_retracted, doi, updated_date,
        coalesce(nullif(primary_topic.field.display_name,''),'Unknown') AS primary_field,
        coalesce(nullif(primary_topic.domain.display_name,''),'Unknown') AS primary_domain,
        filename AS source_file, file_row_number AS source_row
      FROM read_parquet({paths},filename=true,file_row_number=true,hive_partitioning=false,union_by_name=true)
      WHERE publication_year BETWEEN 2020 AND 2026
    """

def run(nfans_run, output, *, chunk_size=40, threads=4, memory_limit='8GB'):
    output=Path(output).resolve()
    nfans_run=Path(nfans_run).resolve()
    config=read_json(nfans_run/'config/run.json')
    if config['snapshot_date'] != '2026-09-23':raise ValueError('Snapshot differs from this pinned release')
    if output.is_relative_to(Path(config['raw_snapshot_root']).resolve()):raise ValueError('Keep outputs outside the raw snapshot')
    output.mkdir(parents=True,exist_ok=True)
    if list(output.rglob('*.partial')):raise ValueError('Inspect partial output before resuming')
    inventory=nf.extract._snapshot(nfans_run, config)
    # Validate the additional nested topic fields in every schema before data extraction.
    import pyarrow.parquet as pq
    for rec in inventory['files']:
        schema=pq.ParquetFile(rec['path']).schema_arrow
        schema.field('primary_topic').type.field('field').type.field('display_name')
        schema.field('primary_topic').type.field('domain').type.field('display_name')
    contract=dict(version='ojf-wide-2020-2026-v1',snapshot=inventory,years=list(range(2020,2027)),
        reference_array_years=list(range(2022,2027)),chunk_size=chunk_size,
        extractor_sha256=hash_file(Path(__file__)),
        duckdb_version=duckdb.__version__,raw_filters='publication year only; all sources, types and paratext',
        dedup='latest updated_date; source_file DESC; raw_issn_l ASC NULLS LAST; source_row ASC')
    cp=output/'contract.json'
    if cp.exists():
        if read_json(cp)!=contract:raise ValueError('Changed extraction contract; preserve and use a new version')
    else:atomic_json(cp,contract)
    digest=hash_json(contract)
    print({'status':'PREFLIGHT_PASS','files':len(inventory['files']),'scope':'2020-2026'},flush=True)
    lock=output/'extraction.lock'
    with lock.open('x') as stream:stream.write(str(os.getpid()))
    start=time.monotonic()
    try:
        with duckdb.connect() as con:
            con.execute(f'SET threads={int(threads)}')
            con.execute(f'SET memory_limit={sql_literal(memory_limit)}')
            con.execute(f'SET temp_directory={sql_literal(output/"duckdb_temp")}')
            con.execute('SET preserve_insertion_order=false')
            files=[r['path'] for r in inventory['files']];stages=[]
            for i,offset in enumerate(range(0,len(files),chunk_size)):
                rec=completed_stage(con,output/'chunks'/f'works_{i:04d}.parquet',checkpoint_sql(files[offset:offset+chunk_size]),digest)
                stages.append(rec)
                print({'files_completed':min(offset+chunk_size,len(files)),'rows':rec['rows'],'seconds':round(time.monotonic()-start,1),'reused':rec['reused']},flush=True)
            paths='['+','.join(sql_literal(r['path']) for r in stages)+']'
            query=f"""SELECT * FROM read_parquet({paths}) QUALIFY row_number() OVER(PARTITION BY work_id
              ORDER BY updated_date DESC NULLS LAST,source_file DESC,raw_issn_l ASC NULLS LAST,source_row ASC)=1"""
            works=completed_stage(con,output/'works.parquet',query,digest)
            diagnostic=con.execute(f"""SELECT year,count(*) AS works,count(*) FILTER(work_id IS NULL) AS null_ids,
              count(*) FILTER(refs_count_reported<>observed_references) AS ref_count_disagreement,
              count(*) FILTER(primary_field='Unknown') AS unknown_field
              FROM read_parquet({sql_literal(output/'works.parquet')}) GROUP BY year ORDER BY year""").df().to_dict('records')
            if any(r['null_ids'] for r in diagnostic):raise ValueError('Null Work ID')
            if hash_file(Path(__file__))!=contract['extractor_sha256']:raise ValueError('Extractor changed during execution')
            report=dict(status='PASS',version='ojf-wide-2020-2026-v1',completed_utc=datetime.now(timezone.utc).isoformat(),
                contract_sha256=digest,works=works,chunk_stages=stages,diagnostics=diagnostic,
                raw_rows_selected=sum(r['rows'] for r in stages),duplicates_removed=sum(r['rows'] for r in stages)-works['rows'])
            atomic_json(output/'report.json',report)
            print({'status':'PASS','works':works['rows'],'seconds':round(time.monotonic()-start,1)},flush=True)
            return report
    finally:lock.unlink(missing_ok=True)
