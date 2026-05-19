import asyncio
import logging.config
import struct
import time

from fastapi import APIRouter, Depends, Request, Response
import orjson
from sqlalchemy.orm import Session

from models.upload import Upload
from app.routes.shared import (
    log_execution_time_async, execute_query, fetch_json_response,
)
from index import get_session
from db_config_parser import get_xiview_base_url
from xi2annotator.annotation import annotate_request

xiview_xi2_data_router = APIRouter(include_in_schema=False)


logger = logging.getLogger(__name__)


@xiview_xi2_data_router.get('/get_peaklist', tags=["xiVIEW"])
async def get_peaklist(id, sd_ref, upload_id):
    query = "SELECT intensity, mz FROM spectrum WHERE id = $1 AND spectra_data_id = $2 AND upload_id = $3"
    data = await execute_query(query, [id, int(sd_ref), int(upload_id)], fetch_one=True)
    # Create a new dictionary to store the unpacked values
    unpacked_data = {
        "intensity": struct.unpack('%sd' % (len(data['intensity']) // 8), data['intensity']),
        "mz": struct.unpack('%sd' % (len(data['mz']) // 8), data['mz'])
    }
    return Response(orjson.dumps(unpacked_data), media_type='application/json')


@xiview_xi2_data_router.post('/get_annotated_peaklist', tags=["xiVIEW"])
async def get_annotated_peaklist(request: Request, id: str, sd_ref: str, upload_id: str):
    # 1. Fetch peaks from DB (same query as get_peaklist)
    query = "SELECT intensity, mz FROM spectrum WHERE id = $1 AND spectra_data_id = $2 AND upload_id = $3"
    data = await execute_query(query, [id, int(sd_ref), int(upload_id)], fetch_one=True)
    mz = struct.unpack('%sd' % (len(data['mz']) // 8), data['mz'])
    intensity = struct.unpack('%sd' % (len(data['intensity']) // 8), data['intensity'])
    peaks = [{"mz": m, "intensity": i} for m, i in zip(mz, intensity)]

    # 2. Inject peaks into annotation body
    body = await request.json()
    body["peaks"] = peaks

    # 3. Call annotator in-process (CPU-bound — offload to thread pool)
    result = await asyncio.to_thread(annotate_request, body)
    return Response(orjson.dumps(result, option=orjson.OPT_SERIALIZE_NUMPY), media_type='application/json')


@xiview_xi2_data_router.get('/get_xiview_mzidentml_files', summary="mzIdentML File Info", tags=["xiVIEW"])
async def get_xiview_mzidentml_files():
    """Stub: not implemented for xi2 schema. Returns empty list."""
    return Response(content=b'[]', media_type='application/json')


@xiview_xi2_data_router.get('/get_xiview_analysis_collection_spectrum_identifications', tags=["xiVIEW"])
async def get_xiview_analysis_collection_spectrum_identifications():
    """Stub: not implemented for xi2 schema. Returns empty list."""
    return Response(content=b'[]', media_type='application/json')


@log_execution_time_async
@xiview_xi2_data_router.get('/get_xiview_spectrum_identification_protocols', tags=["xiVIEW"])
async def get_xiview_spectrum_identification_protocols(project):
    """
    Get search config info (resultset + search metadata) for the given resultset UUIDs.

    :return: json mapping resultset_id -> resultset/search metadata
    """
    logger.info(f"get_xiview_spectrum_identification_protocols for {project}")

    resultset_ids = [project] if isinstance(project, str) else project

    query = """SELECT rs.name AS rs_name, rs.note AS rs_note, rs.config AS rs_config,
                    rs.main_score AS rs_main_score, rst.name AS resultset_type,
                    rs.id AS id, s.name AS s_name, s.config AS s_config, s.note AS s_note, s.id AS s_id
                FROM resultset AS rs
                    LEFT JOIN resultsettype AS rst ON rs.rstype_id = rst.id
                    LEFT JOIN ResultSearch AS result_search ON rs.id = result_search.resultset_id
                    LEFT JOIN Search AS s ON result_search.search_id = s.id
                WHERE rs.id = ANY($1::uuid[]);"""

    records = await execute_query(query, [resultset_ids])

    resultsets = {}
    for row in records:
        row_dict = dict(row)
        if isinstance(row_dict.get('s_config'), (str, bytes)):
            row_dict['s_config'] = orjson.loads(row_dict['s_config'])
        resultsets[str(row_dict['id'])] = row_dict

    return Response(orjson.dumps(resultsets, default=str), media_type='application/json')


@xiview_xi2_data_router.get('/get_xiview_spectra_data', tags=["xiVIEW"])
async def get_xiview_spectra_data():
    """Stub: not implemented for xi2 schema. Returns empty list."""
    return Response(content=b'[]', media_type='application/json')


@xiview_xi2_data_router.get('/get_xiview_enzymes', tags=["xiVIEW"])
async def get_xiview_enzymes():
    """Stub: not implemented for xi2 schema. Returns empty list."""
    return Response(content=b'[]', media_type='application/json')


@xiview_xi2_data_router.get('/get_xiview_search_modifications', tags=["xiVIEW"])
async def get_xiview_search_modifications():
    """Stub: not implemented for xi2 schema. Returns empty list."""
    return Response(content=b'[]', media_type='application/json')


@log_execution_time_async
@xiview_xi2_data_router.get('/get_xiview_matches', tags=["xiVIEW"])
async def get_xiview_matches(project):
    """
    Get the passing matches.

    :return: json of the matches
    """
    logger.info(f"get_xiview_matches for {project}")

    resultset_ids = [project] if isinstance(project, str) else project

    score_query = """SELECT sn.name AS score_name, sn.score_id AS score_index, sn.higher_is_better AS higher_better
                FROM scorename AS sn
                WHERE sn.resultset_id = ANY($1::uuid[]) AND sn.primary_score = TRUE
                LIMIT 1;"""
    main_score = await execute_query(score_query, [resultset_ids], fetch_one=True)
    main_score_index = main_score['score_index']

    query = """SELECT m.id AS id, m.pep1_id AS pi1, m.pep2_id AS pi2,
                    CASE WHEN rm.site1 IS NOT NULL THEN rm.site1 ELSE m.site1 END AS s1,
                    CASE WHEN rm.site2 IS NOT NULL THEN rm.site2 ELSE m.site2 END AS s2,
                    rm.scores[$1 + array_lower(rm.scores, 1)] AS sc,
                    m.crosslinker_id AS cl,
                    m.search_id AS si, m.calc_mass AS cm, m.assumed_prec_charge AS pc_c, m.assumed_prec_mz AS pc_mz,
                    ms.spectrum_id AS sp, rm.resultset_id AS rs_id,
                    s.precursor_intensity AS pc_i,
                    s.scan_number AS sn, s.scan_index AS sc_i,
                    s.retention_time AS rt, r.name AS run, s.peaklist_id AS plf
                FROM ResultMatch AS rm
                    JOIN match AS m ON rm.search_id = m.search_id AND rm.match_id = m.id
                    JOIN matchedspectrum as ms ON rm.match_id = ms.match_id
                    JOIN spectrum as s ON ms.spectrum_id = s.id
                    JOIN run as r ON s.run_id = r.id
                    WHERE rm.resultset_id = ANY($2::uuid[])
                    AND m.site1 >0 AND m.site2 >0
                    AND rm.top_ranking = TRUE;"""

    params = [main_score_index, resultset_ids]
    t0 = time.time()
    records = await execute_query(query, params)
    logger.info(f"get_xiview_matches: query took {time.time()-t0:.2f}s, {len(records)} rows")
    t1 = time.time()
    json_bytes = orjson.dumps([dict(r) for r in records], default=str)
    logger.info(f"get_xiview_matches: json conversion took {time.time()-t1:.2f}s, {len(json_bytes)/1024:.0f}KB")
    return Response(content=json_bytes, media_type='application/json')


@log_execution_time_async
@xiview_xi2_data_router.get('/get_xiview_peptides', tags=["xiVIEW"])
async def get_xiview_peptides(project):
    """
    Get the peptides referenced by top-ranking matches for the given resultset UUIDs.

    :return: json of the peptides
    """
    logger.info(f"get_xiview_peptides for {project}")

    query = """WITH submatch AS (
                    SELECT m.pep1_id, m.pep2_id, m.search_id
                    FROM ResultMatch rm
                    JOIN match m ON rm.search_id = m.search_id AND rm.match_id = m.id
                    WHERE rm.resultset_id = ANY($1::uuid[])
                      AND m.site1 > 0 AND m.site2 > 0
                      AND rm.top_ranking = TRUE
                ),
                pep_ids AS (
                    SELECT search_id, pep1_id AS pep_id FROM submatch
                    UNION
                    SELECT search_id, pep2_id FROM submatch
                )
                SELECT mp.id,
                       mp.search_id AS search_id,
                       mp.sequence AS seq_mods,
                       mp.modification_ids AS mod_ids,
                       mp.modification_position AS mod_pos,
                       array_agg(p.accession) AS prt,
                       array_agg(pp.start) AS pos,
                       array_agg(p.is_decoy) AS dec
                FROM pep_ids pi
                INNER JOIN modifiedpeptide mp ON mp.search_id = pi.search_id AND mp.id = pi.pep_id
                JOIN peptideposition pp ON pp.mod_pep_id = mp.id AND pp.search_id = mp.search_id
                JOIN protein p ON p.id = pp.protein_id AND p.search_id = pp.search_id
                GROUP BY mp.id, mp.search_id, mp.sequence, mp.modification_ids, mp.modification_position;"""

    params = [[project] if isinstance(project, str) else project]
    t0 = time.time()
    records = await execute_query(query, params)
    logger.info(f"get_xiview_peptides: query took {time.time()-t0:.2f}s, {len(records)} rows")
    t1 = time.time()
    json_bytes = orjson.dumps([dict(r) for r in records], default=str)
    logger.info(f"get_xiview_peptides: json conversion took {time.time()-t1:.2f}s, {len(json_bytes)/1024:.0f}KB")
    return Response(content=json_bytes, media_type='application/json')


@log_execution_time_async
@xiview_xi2_data_router.get('/get_xiview_proteins', tags=["xiVIEW"])
async def get_xiview_proteins(project):
    """
    Get the proteins referenced by top-ranking matches for the given resultset UUIDs.

    :return: json of the proteins
    """
    logger.info(f"get_xiview_proteins for {project}")

    query = """WITH submatch AS (
                    SELECT m.pep1_id, m.pep2_id, m.search_id
                    FROM ResultMatch rm
                    JOIN match m ON rm.search_id = m.search_id AND rm.match_id = m.id
                    WHERE rm.resultset_id = ANY($1::uuid[])
                      AND m.site1 > 0 AND m.site2 > 0
                      AND rm.top_ranking = TRUE
                ),
                pep_ids AS (
                    SELECT search_id, pep1_id AS pep_id FROM submatch
                    UNION
                    SELECT search_id, pep2_id FROM submatch
                ),
                protein_ids AS (
                    SELECT DISTINCT pp.search_id, pp.protein_id
                    FROM peptideposition pp
                    JOIN pep_ids pi ON pi.search_id = pp.search_id AND pi.pep_id = pp.mod_pep_id
                )
                SELECT p.id,
                       p.name,
                       p.accession,
                       p.sequence,
                       p.search_id,
                       p.is_decoy
                FROM protein p
                JOIN protein_ids x ON p.search_id = x.search_id AND p.id = x.protein_id;"""

    params = [[project] if isinstance(project, str) else project]
    t0 = time.time()
    records = await execute_query(query, params)
    logger.info(f"get_xiview_proteins: query took {time.time()-t0:.2f}s, {len(records)} rows")
    t1 = time.time()
    json_bytes = orjson.dumps([dict(r) for r in records], default=str)
    logger.info(f"get_xiview_proteins: json conversion took {time.time()-t1:.2f}s, {len(json_bytes)/1024:.0f}KB")
    return Response(content=json_bytes, media_type='application/json')



@log_execution_time_async
@xiview_xi2_data_router.get('/get_matches_by_multiple_spectra_id', tags=["xiVIEW"])
async def get_matches_by_multiple_spectra_id(upload_id: int, multiple_spectra_id: int):
    """
    Get all matches associated with a specific multiple_spectra_identification_id for a given upload.

    Parameters:
    - upload_id: The upload ID to filter matches
    - multiple_spectra_id: The multiple_spectra_identification_id to filter matches

    Returns:
    - JSON array of match records
    """
    logger.info(f"get_matches_by_multiple_spectra_id for upload_id: {upload_id}, multiple_spectra_id: {multiple_spectra_id}")

    query = """SELECT si.id AS id,
                    si.pep1_id AS pi1,
                    si.pep2_id AS pi2,
                    si.scores AS sc,
                    si.upload_id AS ui,
                    si.calc_mz AS c_mz,
                    si.charge_state AS pc_c,
                    si.exp_mz AS pc_mz,
                    si.spectrum_id AS sp,
                    si.spectra_data_id AS sd,
                    si.pass_threshold AS p,
                    si.rank AS r,
                    si.sip_id AS sip,
                    si.multiple_spectra_identification_id AS msi_id,
                    si.multiple_spectra_identification_pc AS msi_pc
                FROM match si
                WHERE si.upload_id = $1
                AND si.multiple_spectra_identification_id = $2
                ORDER BY si.rank, si.id"""

    return await fetch_json_response(query, [upload_id, multiple_spectra_id])


@log_execution_time_async
@xiview_xi2_data_router.get('/get_datasets', tags=["xiVIEW"])
async def get_datasets():
    query = """SELECT DISTINCT project_id, identification_file_name FROM upload;"""
    data = await execute_query(query)
    return Response(orjson.dumps([dict(r) for r in data]), media_type='application/json')
