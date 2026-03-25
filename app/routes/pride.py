import configparser
import json
import logging
import logging.config
import os
import time
from math import ceil
from typing import List, Annotated, Union

import redis
import requests
from fastapi import APIRouter, Depends, status, Query, Path, UploadFile, File
from fastapi import HTTPException, Security
from fastapi.responses import FileResponse
from models.upload import Upload
from sqlalchemy import func, text
from sqlalchemy.orm import Session, defer, joinedload
import typing as t

# my ide gives warning for import from models, is something missing from top __init__.py in xi-mzidentml-converter
from models.analysiscollectionspectrumidentification import AnalysisCollectionSpectrumIdentification
from models.dbsequence import DBSequence
from models.enzyme import Enzyme
from models.modifiedpeptide import ModifiedPeptide
from models.peptideevidence import PeptideEvidence
from models.projectdetail import ProjectDetail
from models.projectsubdetail import ProjectSubDetail
from models.searchmodification import SearchModification
from models.spectrum import Spectrum
from models.spectradata import SpectraData
from models.match import Match
from models.spectrumidentificationprotocol import SpectrumIdentificationProtocol

from app.routes.shared import get_api_key
from db_config_parser import redis_config
from index import get_session
from parser.process_dataset import convert_pxd_accession_from_pride

logger = logging.getLogger(__name__)
pride_router = APIRouter()
config = configparser.ConfigParser()


class EndpointFilter(logging.Filter):
    def __init__(
        self,
        path: str,
        *args: t.Any,
        **kwargs: t.Any,
    ):
        super().__init__(*args, **kwargs)
        self._path = path

    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage().find(self._path) == -1


# Filter out /endpoint
logging.getLogger("uvicorn.access").addFilter(EndpointFilter(path="/health"))


@pride_router.get("/health", tags=["Admin"])
async def health(session: Session = Depends(get_session)):
    """
    A quick simple endpoint to test the API is working
    :return: Response with OK
    """

    sql_projects_count = text("""
                          SELECT
                              COUNT(id) AS "Number of Projects"
                          FROM
                              projectdetails p;
                      """)
    try:
        count = await get_projects_count(sql_projects_count, session)
        if count[0] is not None:
            db_status = "OK"
        else:
            db_status = "Failed"
    except Exception as error:
        logger.error(error)
        db_status = "Failed"
    return {'status': "OK",
            'db_status': db_status}

@pride_router.post("/parse", tags=["Admin"])
async def parse(px_accession: str, temp_dir: str | None = None, dont_delete: bool = False,
                api_key: str = Security(get_api_key)):
    """
    Parse a new project which contain MzIdentML file
    :param api_key: API KEY
    :param px_accession: ProteomXchange Project Accession
    :param temp_dir: If data needs to be saved in a temporary directory
    :param dont_delete: Boolean value to determine if the files needs to be deleted at the end
    :return: None
    """
    if temp_dir:
        temp_dir = os.path.expanduser(temp_dir)
    else:
        temp_dir = os.path.expanduser('~/mzId_convertor_temp')
    convert_pxd_accession_from_pride(px_accession, temp_dir, "db", dont_delete)
    # invalidate_cache()
    logger.info("Invalidated Cache")


@pride_router.post("/update-protein-metadata/{project_id}", tags=["Admin"])
async def update_metadata_by_project(project_id: str, session: Session = Depends(get_session),
                                     api_key: str = Security(get_api_key)):
    # Get total number of identifications (not spectra) passing Threshold including decoy identification
    sql_number_of_identifications = text("""
                        SELECT count(*)
                        FROM match
                        WHERE upload_id IN (
                            SELECT u.id
                            FROM upload u
                            WHERE u.upload_time = (
                                SELECT max(upload_time)
                                FROM upload
                                WHERE project_id = u.project_id AND identification_file_name = u.identification_file_name
                            )
                            AND u.project_id = :projectaccession
                        )
                        AND pass_threshold = True
                    """)

    # Get Total number of peptides identified passing Threshold including decoy identification(including non-crosslink)
    sql_number_of_peptides = text("""
                                SELECT COUNT(DISTINCT pep_id)
                                FROM (
                                    SELECT pep1_id AS pep_id
                                    FROM match si
                                    WHERE si.upload_id IN (
                                            SELECT u.id
                                            FROM upload u
                                            where u.upload_time =
                                                (select max(upload_time) from upload
                                                where project_id = u.project_id
                                                and identification_file_name = u.identification_file_name )
                                                and u.project_id = :projectaccession
                                    ) AND si.pass_threshold = TRUE

                                    UNION

                                    SELECT pep2_id AS pep_id
                                    FROM match si
                                    WHERE si.upload_id IN (
                                            SELECT u.id
                                            FROM upload u
                                            where u.upload_time =
                                                (select max(upload_time) from upload
                                                where project_id = u.project_id
                                                and identification_file_name = u.identification_file_name )
                                                and u.project_id = :projectaccession
                                    ) AND si.pass_threshold = TRUE
                                ) AS result;    
            """)

    # Total number of crosslinked proteins identified passing Threshold excluding decoy identification
    sql_number_of_proteins = text("""
                            SELECT COUNT(*) 
                            FROM (
                                SELECT DISTINCT dbs.accession 
                                FROM (
                                    SELECT pe1.dbsequence_id AS protein_id
                                    FROM match si
                                    INNER JOIN modifiedpeptide mp1 ON si.pep1_id = mp1.id AND si.upload_id = mp1.upload_id
                                    INNER JOIN peptideevidence pe1 ON mp1.id = pe1.peptide_id AND mp1.upload_id = pe1.upload_id
                                    INNER JOIN modifiedpeptide mp2 ON si.pep2_id = mp2.id AND si.upload_id = mp2.upload_id
                                    INNER JOIN peptideevidence pe2 ON mp2.id = pe2.peptide_id AND mp2.upload_id = pe2.upload_id
                                    INNER JOIN upload u ON u.id = si.upload_id
                                    WHERE u.id IN (
                                        SELECT u.id
                                        FROM upload u
                                        WHERE u.upload_time = (
                                            SELECT MAX(upload_time)
                                            FROM upload
                                            WHERE project_id = u.project_id
                                            AND identification_file_name = u.identification_file_name
                                        ) AND u.project_id = :projectaccession
                                    ) AND mp1.link_site1 > 0 AND mp2.link_site1 > 0 AND pe1.is_decoy = FALSE AND pe2.is_decoy = FALSE
                                    AND si.pass_threshold = TRUE

                                    UNION

                                    SELECT pe2.dbsequence_id AS protein_id
                                    FROM match si
                                    INNER JOIN modifiedpeptide mp1 ON si.pep1_id = mp1.id AND si.upload_id = mp1.upload_id
                                    INNER JOIN peptideevidence pe1 ON mp1.id = pe1.peptide_id AND mp1.upload_id = pe1.upload_id
                                    INNER JOIN modifiedpeptide mp2 ON si.pep2_id = mp2.id AND si.upload_id = mp2.upload_id
                                    INNER JOIN peptideevidence pe2 ON mp2.id = pe2.peptide_id AND mp2.upload_id = pe2.upload_id
                                    INNER JOIN upload u ON u.id = si.upload_id
                                    WHERE u.id IN (
                                        SELECT u.id
                                        FROM upload u
                                        WHERE u.upload_time = (
                                            SELECT MAX(upload_time)
                                            FROM upload
                                            WHERE project_id = u.project_id
                                            AND identification_file_name = u.identification_file_name
                                        ) AND u.project_id = :projectaccession
                                    ) AND mp1.link_site1 > 0 AND mp2.link_site1 > 0 AND pe1.is_decoy = FALSE AND pe2.is_decoy = FALSE
                                    AND si.pass_threshold = TRUE
                                ) AS protein_id 
                                INNER JOIN dbsequence AS dbs ON protein_id = id
                            ) AS accessions;

            """)

    # total number of peptides(crosslink and non crosslink) per protein
    sql_peptides_per_protein = text("""
        WITH result AS (
        SELECT
            pe1.dbsequence_id AS dbref1,
            pe1.peptide_id AS pepref1,
            pe2.dbsequence_id AS dbref2,
            pe2.peptide_id AS pepref2
        FROM
            match si
            INNER JOIN modifiedpeptide mp1 ON si.pep1_id = mp1.id AND si.upload_id = mp1.upload_id
            INNER JOIN peptideevidence pe1 ON mp1.id = pe1.peptide_id AND mp1.upload_id = pe1.upload_id
            INNER JOIN modifiedpeptide mp2 ON si.pep2_id = mp2.id AND si.upload_id = mp2.upload_id
            INNER JOIN peptideevidence pe2 ON mp2.id = pe2.peptide_id AND mp2.upload_id = pe2.upload_id
            INNER JOIN upload u ON u.id = si.upload_id
        WHERE
            u.id IN (
                SELECT u.id
                FROM upload u
                WHERE u.upload_time = (
                    SELECT MAX(upload_time)
                    FROM upload
                    WHERE project_id = u.project_id
                    AND identification_file_name = u.identification_file_name
                ) AND u.project_id = :projectaccession
            )
            AND pe1.is_decoy = FALSE
            AND pe2.is_decoy = FALSE
            AND si.pass_threshold = TRUE
    )
    SELECT
        dbref,
        COUNT(pepref) AS peptide_count
    FROM (
        SELECT dbref1 AS dbref, pepref1 AS pepref FROM result
        UNION
        SELECT dbref2 AS dbref, pepref2 AS pepref FROM result
    ) AS inner_result
    GROUP BY dbref;

        """)

    # Proteins and crosslink peptide counts
    sql_crosslinks_per_protein = text("""
     WITH result AS (
        SELECT
            pe1.dbsequence_id AS dbref1,
            pe1.peptide_id AS pepref1,
            pe2.dbsequence_id AS dbref2,
            pe2.peptide_id AS pepref2
        FROM
            match si
            INNER JOIN modifiedpeptide mp1 ON si.pep1_id = mp1.id AND si.upload_id = mp1.upload_id
            INNER JOIN peptideevidence pe1 ON mp1.id = pe1.peptide_id AND mp1.upload_id = pe1.upload_id
            INNER JOIN modifiedpeptide mp2 ON si.pep2_id = mp2.id AND si.upload_id = mp2.upload_id
            INNER JOIN peptideevidence pe2 ON mp2.id = pe2.peptide_id AND mp2.upload_id = pe2.upload_id
            INNER JOIN upload u ON u.id = si.upload_id
        WHERE
            u.id IN (
                SELECT u.id
                FROM upload u
                WHERE u.upload_time = (
                    SELECT MAX(upload_time)
                    FROM upload
                    WHERE project_id = u.project_id
                    AND identification_file_name = u.identification_file_name
                ) AND u.project_id = :projectaccession
            )
            AND mp1.link_site1 > 0
            AND mp2.link_site1 > 0
            AND pe1.is_decoy = FALSE
            AND pe2.is_decoy = FALSE
            AND si.pass_threshold = TRUE
    )
    SELECT
        dbref,
        COUNT(pepref) AS peptide_count
    FROM (
        SELECT DISTINCT *
        FROM (
            SELECT dbref1 AS dbref, pepref1 AS pepref FROM result
            UNION
            SELECT dbref2 AS dbref, pepref2 AS pepref FROM result
        ) AS inner_inner_result
    ) AS inner_result
    GROUP BY dbref;

           """)

    # Protein dbref to protein accession mapping
    sql_db_sequence_accession_mapping = text("""
        SELECT id, accession
        FROM dbsequence
        WHERE upload_id IN (
            SELECT u.id
            FROM upload u
            WHERE u.upload_time = (
                SELECT MAX(upload_time)
                FROM upload
                WHERE project_id = u.project_id
                AND identification_file_name = u.identification_file_name
            )
            AND u.project_id = :projectaccession
        );
        """)

    project_details = ProjectDetail()
    sql_values = {"projectaccession": project_id}

    # get project details from PRIDE API
    logger.info("Updating project level metadata")
    px_url = 'https://www.ebi.ac.uk/pride/ws/archive/v3/projects/' + project_id
    logger.debug('GET request to PRIDE API: ' + px_url)
    pride_response = requests.get(px_url)
    r = requests.get(px_url)
    if r.status_code == 200:
        logger.info('PRIDE API returned status code 200')
        pride_json = pride_response.json()
        if pride_json is not None:
            if len(pride_json['references']) > 0:
                project_details.pubmed_id = pride_json['references'][0]['pubmedID']
            if len(pride_json['title']) > 0:
                project_details.title = pride_json['title']
            if len(pride_json['projectDescription']) > 0:
                project_details.description = pride_json['projectDescription']
            if len(pride_json['organisms']) > 0:
                project_details.organism = pride_json['organisms'][0]['name']

    project_details.project_id = project_id

    project_details.number_of_spectra = await get_number_of_counts(sql_number_of_identifications, sql_values,
                                                                   session)
    project_details.number_of_peptides = await get_number_of_counts(sql_number_of_peptides, sql_values, session)
    project_details.number_of_proteins = await get_number_of_counts(sql_number_of_proteins, sql_values, session)

    peptide_counts_by_protein = await get_counts_table(sql_peptides_per_protein, sql_values, session)
    peptide_crosslinks_by_protein = await get_counts_table(sql_crosslinks_per_protein, sql_values, session)
    db_sequence_accession_mapping = await get_counts_table(sql_db_sequence_accession_mapping, sql_values,
                                                           session)

    list_of_project_sub_details = []

    # fill number of peptides
    for protein in peptide_counts_by_protein:
        project_sub_detail = ProjectSubDetail()
        project_sub_detail.project_detail = project_details
        project_sub_detail.protein_db_ref = protein['key']
        project_sub_detail.number_of_peptides = protein['value']
        list_of_project_sub_details.append(project_sub_detail)

    # fill number of crosslink
    for crosslinks in peptide_crosslinks_by_protein:
        for sub_details in list_of_project_sub_details:
            if sub_details.protein_db_ref == crosslinks['key']:
                sub_details.number_of_cross_links = crosslinks['value']

    # fill protein accessions
    for sub_details in list_of_project_sub_details:
        for dbseq in db_sequence_accession_mapping:
            if sub_details.protein_db_ref == dbseq['key']:
                sub_details.protein_accession = dbseq['value']

    logger.info("Updating protein level metadata")
    await update_protein_metadata(list_of_project_sub_details)

    logger.info("Saving medatadata...")
    conditions = {'project_id': project_id}
    existing_record = session.query(ProjectDetail).filter_by(**conditions).first()

    # If the record exists, update its attributes
    if existing_record:
        # Delete ProjectDetail and associated ProjectSubDetail records based on project_detail_id
        session.query(ProjectSubDetail).filter_by(project_detail_id=existing_record.id).delete()
        session.query(ProjectDetail).filter_by(**conditions).delete()
        session.commit()

    # add new record
    session.add(project_details)
    session.commit()
    logger.info("Saving medatadata COMPLETED")
    return None


@pride_router.post("/update-metadata", tags=["Admin"])
async def update_metadata(session: Session = Depends(get_session), api_key: str = Security(get_api_key)):
    """
    An endpoint to update the project details including title, description, PubmedID,
    Number of proteins, peptides and spectra identifications
    :param api_key: API KEY
    :param session: session connection to the database
    :return: None
    """

    sql_project_accession_list = text("""
    SELECT DISTINCT u.project_id FROM upload u
    """)

    try:
        sql_values = {}
        list_of_project_id = await get_accessions(sql_project_accession_list, sql_values, session)
        for project_id in list_of_project_id:
            await update_metadata_by_project(project_id, session, api_key)
        session.close()
    except Exception as error:
        logger.error(error)
        session.rollback()


@pride_router.put("/log/{level}", tags=["Admin"])
def change_log_level(level, api_key: str = Security(get_api_key)):
    level_upper = str(level).upper()
    logging.getLogger("uvicorn.error").setLevel(level_upper)
    logging.getLogger("uvicorn.access").setLevel(level_upper)
    logging.getLogger("uvicorn.asgi").setLevel(level_upper)
    logger.setLevel(level_upper)


@pride_router.delete("/delete/{project_id}", tags=["Admin"])
async def delete_dataset(project_id: str, session: Session = Depends(get_session),
                         api_key: str = Security(get_api_key)):
    logger.info("Deleting dataset: " + project_id)

    try:
        # Define the conditions for updating
        conditions = {'project_id': project_id}

        # Query for an existing record based on conditions
        existing_upload_records = session.query(Upload).filter_by(**conditions).all()

        upload_id_list = []
        # If the record exists, update its attributes
        if existing_upload_records:

            for existing_upload_record in existing_upload_records:
                upload_id_list.append(existing_upload_record.id)

        # Query for an existing project_details record based on conditions
        existing_project_details_records = session.query(ProjectDetail).filter_by(**conditions).all()

        project_details_id_list = []
        # If the record exists, update its attributes
        if existing_project_details_records is not None and len(existing_project_details_records) > 0:
            for existing_project_details_record in existing_project_details_records:
                project_details_id_list.append(existing_project_details_record.id)

        session.query(ProjectSubDetail).filter(ProjectSubDetail.project_detail_id.in_(project_details_id_list)).delete()
        logging.info("trying to delete records from ProjectSubDetail")
        session.query(ProjectDetail).filter_by(project_id=project_id).delete()
        logging.info("trying to delete records from Match")
        session.query(Match).filter(Match.upload_id.in_(upload_id_list)).delete()
        logging.info("trying to delete records from SearchModification")
        session.query(SearchModification).filter(SearchModification.upload_id.in_(upload_id_list)).delete()
        logging.info("trying to delete records from Enzyme")
        session.query(Enzyme).filter(Enzyme.upload_id.in_(upload_id_list)).delete()
        logging.info("trying to delete records from AnalysisCollectionSpectrumIdentification")
        session.query(AnalysisCollectionSpectrumIdentification).filter(
            AnalysisCollectionSpectrumIdentification.upload_id.in_(upload_id_list)).delete()
        logging.info("trying to delete records from SpectrumIdentificationProtocol")
        session.query(SpectrumIdentificationProtocol).filter(
            SpectrumIdentificationProtocol.upload_id.in_(upload_id_list)).delete()
        logging.info("trying to delete records from PeptideEvidence")
        session.query(PeptideEvidence).filter(PeptideEvidence.upload_id.in_(upload_id_list)).delete()
        logging.info("trying to delete records from ModifiedPeptide")
        session.query(ModifiedPeptide).filter(ModifiedPeptide.upload_id.in_(upload_id_list)).delete()
        logging.info("trying to delete records from DBSequence")
        session.query(DBSequence).filter(DBSequence.upload_id.in_(upload_id_list)).delete()
        logging.info("trying to delete records from Spectrum")
        session.query(Spectrum).filter(Spectrum.upload_id.in_(upload_id_list)).delete()
        logging.info("trying to delete records from SpectraData")
        session.query(SpectraData).filter(SpectraData.upload_id.in_(upload_id_list)).delete()
        logging.info("trying to delete records from Upload")
        session.query(Upload).filter_by(project_id=project_id).delete()
        session.commit()
        logger.info("*****Deleted dataset: " + project_id)
        # invalidate_cache()
        logger.info("Invalidated Cache")
    except Exception as error:
        logger.error(str(error))
        session.rollback()
    finally:
        session.close()


REPORT_DIR = os.environ.get(
    "REPORT_DIR",
    os.path.join(os.path.expanduser("~"), "mzId_convertor_temp", "reports"),
)
REPORT_FILENAME = "crosslinking_report.html"


@pride_router.post("/report/upload", tags=["Admin"])
async def upload_report(file: UploadFile = File(...),
                        api_key: str = Security(get_api_key)):
    """
    Upload the crosslinking HTML report.
    :param file: HTML report file
    :param api_key: API KEY
    :return: Success message
    """
    if not file.filename.endswith(".html"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only HTML files are accepted",
        )
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_DIR, REPORT_FILENAME)
    try:
        contents = await file.read()
        with open(report_path, "wb") as f:
            f.write(contents)
        logger.info(f"Report uploaded: {report_path} ({len(contents)} bytes)")
        return {"message": "Report uploaded successfully", "filename": REPORT_FILENAME}
    except Exception as e:
        logger.error(f"Failed to upload report: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save report: {e}",
        )


@pride_router.get("/report/download", tags=["Admin"])
async def download_report(api_key: str = Security(get_api_key)):
    """
    Download the crosslinking HTML report.
    :param api_key: API KEY
    :return: HTML report file
    """
    report_path = os.path.join(REPORT_DIR, REPORT_FILENAME)
    if not os.path.exists(report_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No report has been uploaded yet",
        )
    return FileResponse(
        path=report_path,
        media_type="text/html",
        filename=REPORT_FILENAME,
    )


@pride_router.get("/projects", tags=["Projects"], response_model=None)
async def project_search(q: Union[str | None] = Query(default="",
                                                      alias="query",
                                                      max_length=20,
                                                      title="query",
                                                      description="Project accession, protein name or gene name",
                                                      examples={
                                                          "Project accession pattern": {
                                                              "value": "PXD035",
                                                              "description": "Project accession pattern"
                                                          },
                                                          "Project accession": {
                                                              "value": "PXD035519",
                                                              "description": "Protein accession"
                                                          },
                                                          "protein": {
                                                              "value": "membrane",
                                                              "description": "protein name"
                                                          }
                                                      }
                                                      ),
                         page: int = Query(1, description="Page number"),
                         page_size: int = Query(10, gt=5, lt=100, description="Number of items per page"),
                         session: Session = Depends(get_session)
                         ) -> list[ProjectDetail]:
    """
    This gives the high-level view of list of projects
    :param q: Query parameter
    :param page: page number
    :param page_size: records per page
    :return: List of ProjectDetails in JSON format
    :param session: connection to database
    """
    projects = None
    where_condition = ""

    if q and q != '*' and q != 'all':
        where_condition += """ WHERE p.project_id LIKE '%' || :query || '%' OR
          p.title LIKE '%' || :query || '%' OR
          p.description LIKE '%' || :query || '%' OR
          p.organism LIKE '%' || :query || '%' OR
          ps.protein_accession = :query OR
          ps.protein_name LIKE '%' || :query || '%' OR
          ps.gene_name LIKE '%' || :query || '%'
               """

    project_search_sql = text(f"""
                SELECT p.id FROM projectdetails p
                JOIN public.projectsubdetails ps ON p.id = ps.project_detail_id
                {where_condition}
               ORDER BY id
           """)

    sql_values = {"query": q}

    # project_search_sql = text("""
    #     SELECT p.id FROM projectdetails p
    # JOIN public.projectsubdetails ps ON p.id = ps.project_detail_id
    # WHERE p.project_id = :query OR
    #       p.title ILIKE '%' || :query || '%' OR
    #       p.description ILIKE '%' || :query || '%' OR
    #       p.organism ILIKE '%' || :query || '%' OR
    #       ps.protein_accession = :query OR
    #       ps.protein_name ILIKE '%' || :query || '%' OR
    #       ps.gene_name ILIKE '%' || :query || '%'
    #   """)
    matchig_ids = set(session.execute(project_search_sql, sql_values).fetchall())
    list_of_ids = [id for (id,) in matchig_ids]
    try:
        offset = (page - 1) * page_size
        projects = session.query(ProjectDetail).options(defer(ProjectDetail.xiview_url)).filter(ProjectDetail.id.in_(list_of_ids)).offset(offset).limit(
            page_size).all()
        total_elements = len(list_of_ids)
    except Exception as e:
        # Handle the exception here
        logging.error(f"Error occurred: {str(e)}")
    if projects is None or projects == []:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Projects not found")
    response = {
        "projects": projects,
        "page": {
            "page_no": page,
            "page_size": page_size,
            "total_elements": total_elements,
            "total_pages": ceil(total_elements / page_size)
        }
    }
    return response


@pride_router.get("/projects/{project_id}", tags=["Projects"], response_model=None)
def project_detail_view(project_id: str, session: Session = Depends(get_session)) -> List[ProjectDetail]:
    """
    Retrieve project detail by px_accession.
    :param project_id: identifier of a project, for ProteomeXchange projects this is the PXD****** accession
    :param session:
    :return:
    """
    try:
        project_detail = session.query(ProjectDetail) \
            .options(joinedload(ProjectDetail.project_sub_details)) \
            .filter(ProjectDetail.project_id == project_id) \
            .all()
    except Exception as e:
        logging.error(f"Error occurred: {str(e)}")
        project_detail = None

    if project_detail is None or project_detail == []:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project detail not found")

    return project_detail


@pride_router.get("/projects/{project_id}/proteins", tags=["Projects"], response_model=None)
async def protein_search(project_id: Annotated[str, Path(...,
                                                         title="Project ID",
                                                         pattern="^PXD\d{6}$",
                                                         example="PXD036833")],
                         q: Union[str | None] = Query(default=None,
                                                      alias="query",
                                                      max_length=20,
                                                      title="query",
                                                      description="Protein accession, protein name or gene name"),
                         page: int = Query(1, description="Page number"),
                         page_size: int = Query(10, gt=5, lt=100, description="Number of items per page"),
                         session: Session = Depends(get_session)) -> list[ProjectSubDetail]:
    """
    This gives the high-level view of a list of projects
    - **project_id**: PXD****** accession
    - **q**: query for Protein accession, protein name or gene name
    - **page**: page number
    - **page_size**: number of records per page
    :return: List of ProjectDetails in JSON format
    """
    try:
        where_condition = """ps.project_detail_id = pd.id AND pd.project_id = :project_id"""

        if q and q != '*' and q != 'all':
            where_condition += """ AND (ps.protein_accession LIKE '%' || :query || '%'
                     OR ps.gene_name LIKE '%' || :query || '%'
                     OR ps.protein_name LIKE '%' || :query || '%')
            """

        sql = text(f"""
            SELECT ps.* FROM projectsubdetails ps
            JOIN projectdetails pd ON ps.project_detail_id = pd.id
            WHERE {where_condition}
            ORDER BY ps.id
            LIMIT :limit OFFSET :offset
        """)

        # Set the SQL values
        sql_values = {
            "project_id": project_id,
            "query": q,
            "limit": page_size,
            "offset": (page - 1) * page_size
        }

        sql_total_count = text(f"""
                  SELECT COUNT(*) FROM projectsubdetails ps
                  JOIN projectdetails pd ON ps.project_detail_id = pd.id
                  WHERE {where_condition}
              """)

        # Set the SQL values
        sql_values_total_count = {
            "project_id": project_id,
            "query": q
        }

        # Execute the SQL query
        result = session.execute(sql, sql_values)
        total_elements = session.execute(sql_total_count, sql_values_total_count).scalar()
        proteins = result.fetchall()

        # Convert rows to a list of ProjectSubDetail objects
        proteins_list = []
        for row in proteins:
            protein = ProjectSubDetail(
                id=row.id,
                project_detail_id=row.project_detail_id,
                protein_db_ref=row.protein_db_ref,
                protein_name=row.protein_name,
                gene_name=row.gene_name,
                protein_accession=row.protein_accession,
                number_of_peptides=row.number_of_peptides,
                number_of_cross_links=row.number_of_cross_links,
                in_pdbe_kb=row.in_pdbe_kb,
                in_alpha_fold_db=row.in_alpha_fold_db
            )
            proteins_list.append(protein)

    except Exception as e:
        # Handle the exception here
        logging.error(f"Error occurred: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal Server Error")

    if not proteins_list and page == 1:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="proteins not found")

    response = {
        "proteins": proteins_list,
        "page": {
            "page_no": page,
            "page_size": page_size,
            "total_elements": total_elements,
            "total_pages": ceil(total_elements / page_size) if total_elements else 0,
        }
    }

    return response


@pride_router.get("/proteins", tags=["Proteins"], response_model=None)
async def all_protein_accessions(
        q: Union[str | None] = Query(default=None,
                                     alias="query",
                                     max_length=20,
                                     title="query",
                                     description="Filter by protein accession"),
        page: int = Query(1, description="Page number"),
        page_size: int = Query(1000, gt=5, lt=10000, description="Number of items per page"),
        session: Session = Depends(get_session)):
    """
    Retrieve all unique protein accessions across all projects.
    :param q: Optional filter by protein accession
    :param page: page number
    :param page_size: records per page
    :param session: connection to database
    :return: Paginated list of unique protein accessions with total count
    """
    try:
        query = session.query(
            ProjectSubDetail.protein_accession
        ).distinct()

        if q and q != '*' and q != 'all':
            query = query.filter(
                ProjectSubDetail.protein_accession.ilike(f'%{q}%')
            )

        total_elements = query.count()

        offset = (page - 1) * page_size
        protein_accessions = query.order_by(
            ProjectSubDetail.protein_accession
        ).offset(offset).limit(page_size).all()

        proteins_list = [row.protein_accession for row in protein_accessions]

    except Exception as e:
        logging.error(f"Error occurred: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal Server Error")

    if not proteins_list and page == 1:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Protein accessions not found")

    response = {
        "protein_accessions": proteins_list,
        "page": {
            "page_no": page,
            "page_size": page_size,
            "total_elements": total_elements,
            "total_pages": ceil(total_elements / page_size) if total_elements else 0,
        }
    }

    return response


@pride_router.get("/statistics-count", tags=["Statistics"])
async def statistics_count(session: Session = Depends(get_session)):
    try:
        sql_statistics_count = text("""
                      SELECT
                          COUNT(id) AS "Number of Projects",
                          SUM(number_of_proteins) AS "Number of proteins",
                          SUM(number_of_peptides) AS "Number of peptides",
                          SUM(number_of_spectra) AS "Number of spectra",
                          COUNT(DISTINCT organism) AS "Number of species"
                      FROM
                          projectdetails p;
                  """)
        values = await get_statistics_count(sql_statistics_count, session)
    except Exception as error:
        logger.error(error)
    return values


@pride_router.get("/projects-per-species", tags=["Statistics"])
async def project_per_species(session: Session = Depends(get_session)):
    """
    Number of projects per species
    :param session: session connection to the database
    :return:  Number of projects per species as a Dictionary
    """
    try:
        sql_projects_per_species = text("""
        SELECT organism, COUNT(organism) AS organism_count
        FROM projectdetails
        GROUP BY organism
        ORDER BY COUNT(organism) ASC;
""")
        values = await project_per_species_counts(sql_projects_per_species, None, session)
    except Exception as error:
        logger.error(error)
    return values


@pride_router.get("/peptide-per-protein", tags=["Statistics"])
async def peptide_per_protein(session: Session = Depends(get_session),
                              redis_config_param=Depends(redis_config)):
    """
    Get the number of peptides per protein frequency
    :param session: session connection to the database
    :param redis_config_param: Redis in-memory database configurations
    :return:  Number of peptides per protein frequency as a dictionary
    """
    try:
        key = redis_config_param['peptide_per_protein']
        redis_client = redis.Redis(host=redis_config_param['host'],
                                   port=redis_config_param['port'],
                                   password=redis_config_param['password'],
                                   decode_responses=False)
        if redis_client is not None and redis_client.exists(key):
            # If data exists in Redis, retrieve it
            values = redis_client.get(key)
            return json.loads(values)
        else:
            # If data doesn't exist in Redis, fetch it from the database
            sql_peptides_per_protein = text("""
                            WITH frequencytable AS (
                        WITH result AS (
                            SELECT
                                pe1.dbsequence_id AS dbref1,
                                pe1.peptide_id AS pepref1,
                                pe2.dbsequence_id AS dbref2,
                                pe2.peptide_id AS pepref2
                            FROM
                                match si
                                INNER JOIN modifiedpeptide mp1 ON si.pep1_id = mp1.id AND si.upload_id = mp1.upload_id
                                INNER JOIN peptideevidence pe1 ON mp1.id = pe1.peptide_id AND mp1.upload_id = pe1.upload_id
                                INNER JOIN modifiedpeptide mp2 ON si.pep2_id = mp2.id AND si.upload_id = mp2.upload_id
                                INNER JOIN peptideevidence pe2 ON mp2.id = pe2.peptide_id AND mp2.upload_id = pe2.upload_id
                                INNER JOIN upload u ON u.id = si.upload_id
                            WHERE
                                u.id IN (
                                    SELECT
                                        u.id
                                    FROM
                                        upload u
                                    WHERE
                                        u.upload_time = (
                                            SELECT
                                                MAX(upload_time)
                                            FROM
                                                upload
                                            WHERE
                                                project_id = u.project_id
                                                AND identification_file_name = u.identification_file_name
                                        )
                                )
                                AND pe1.is_decoy = FALSE
                                AND pe2.is_decoy = FALSE
                                AND si.pass_threshold = TRUE
                        )
                        SELECT
                            dbref,
                            COUNT(pepref) AS peptide_count
                        FROM
                            (
                                SELECT
                                    dbref1 AS dbref,
                                    pepref1 AS pepref
                                FROM
                                    result
                                UNION
                                SELECT
                                    dbref2 AS dbref,
                                    pepref2 AS pepref
                                FROM
                                    result
                            ) AS inner_result
                        GROUP BY
                            dbref
                    )
                    SELECT
                        frequencytable.peptide_count,
                        COUNT(*)
                    FROM
                        frequencytable
                    GROUP BY
                        frequencytable.peptide_count
                    ORDER BY
                        frequencytable.peptide_count;

                    """)
            values = await peptide_per_protein_counts(sql_peptides_per_protein, None, session)

            # Store the data in Redis for future use
            if values:
                redis_client.set(key, json.dumps(values))
                return values
            else:
                return None
    except Exception as error:
        logger.error(error)
    return values


def invalidate_cache(redis_config_param=Depends(redis_config)):
    redis_client = redis.Redis(host=redis_config_param['host'],
                               port=redis_config_param['port'],
                               password=redis_config_param['password'],
                               decode_responses=False)
    key = redis_config_param['peptide_per_protein']
    redis_client.delete(key)
    return None


async def _recalculate_project_counts(project_id: str, session: Session):
    """Recalculate only number_of_proteins, number_of_peptides, number_of_spectra
    for a given project and update the existing projectdetails record."""
    sql_values = {"projectaccession": project_id}

    sql_number_of_spectra = text("""
        SELECT count(*) FROM match
        WHERE upload_id IN (
            SELECT u.id FROM upload u
            WHERE u.upload_time = (
                SELECT max(upload_time) FROM upload
                WHERE project_id = u.project_id AND identification_file_name = u.identification_file_name
            ) AND u.project_id = :projectaccession
        ) AND pass_threshold = True
    """)

    sql_number_of_peptides = text("""
        SELECT COUNT(DISTINCT pep_id) FROM (
            SELECT pep1_id AS pep_id FROM match si
            WHERE si.upload_id IN (
                SELECT u.id FROM upload u
                WHERE u.upload_time = (
                    SELECT max(upload_time) FROM upload
                    WHERE project_id = u.project_id AND identification_file_name = u.identification_file_name
                ) AND u.project_id = :projectaccession
            ) AND si.pass_threshold = TRUE
            UNION
            SELECT pep2_id AS pep_id FROM match si
            WHERE si.upload_id IN (
                SELECT u.id FROM upload u
                WHERE u.upload_time = (
                    SELECT max(upload_time) FROM upload
                    WHERE project_id = u.project_id AND identification_file_name = u.identification_file_name
                ) AND u.project_id = :projectaccession
            ) AND si.pass_threshold = TRUE
        ) AS result
    """)

    sql_number_of_proteins = text("""
        SELECT COUNT(*)
        FROM (
            SELECT DISTINCT dbs.accession
            FROM (
                SELECT pe1.dbsequence_id AS protein_id
                FROM match si
                INNER JOIN modifiedpeptide mp1 ON si.pep1_id = mp1.id AND si.upload_id = mp1.upload_id
                INNER JOIN peptideevidence pe1 ON mp1.id = pe1.peptide_id AND mp1.upload_id = pe1.upload_id
                INNER JOIN modifiedpeptide mp2 ON si.pep2_id = mp2.id AND si.upload_id = mp2.upload_id
                INNER JOIN peptideevidence pe2 ON mp2.id = pe2.peptide_id AND mp2.upload_id = pe2.upload_id
                INNER JOIN upload u ON u.id = si.upload_id
                WHERE u.id IN (
                    SELECT u.id FROM upload u
                    WHERE u.upload_time = (
                        SELECT MAX(upload_time) FROM upload
                        WHERE project_id = u.project_id AND identification_file_name = u.identification_file_name
                    ) AND u.project_id = :projectaccession
                ) AND mp1.link_site1 > 0 AND mp2.link_site1 > 0 AND pe1.is_decoy = FALSE AND pe2.is_decoy = FALSE
                AND si.pass_threshold = TRUE
                UNION
                SELECT pe2.dbsequence_id AS protein_id
                FROM match si
                INNER JOIN modifiedpeptide mp1 ON si.pep1_id = mp1.id AND si.upload_id = mp1.upload_id
                INNER JOIN peptideevidence pe1 ON mp1.id = pe1.peptide_id AND mp1.upload_id = pe1.upload_id
                INNER JOIN modifiedpeptide mp2 ON si.pep2_id = mp2.id AND si.upload_id = mp2.upload_id
                INNER JOIN peptideevidence pe2 ON mp2.id = pe2.peptide_id AND mp2.upload_id = pe2.upload_id
                INNER JOIN upload u ON u.id = si.upload_id
                WHERE u.id IN (
                    SELECT u.id FROM upload u
                    WHERE u.upload_time = (
                        SELECT MAX(upload_time) FROM upload
                        WHERE project_id = u.project_id AND identification_file_name = u.identification_file_name
                    ) AND u.project_id = :projectaccession
                ) AND mp1.link_site1 > 0 AND mp2.link_site1 > 0 AND pe1.is_decoy = FALSE AND pe2.is_decoy = FALSE
                AND si.pass_threshold = TRUE
            ) AS protein_id
            INNER JOIN dbsequence AS dbs ON protein_id = id
        ) AS accessions
    """)

    num_spectra = await get_number_of_counts(sql_number_of_spectra, sql_values, session)
    num_peptides = await get_number_of_counts(sql_number_of_peptides, sql_values, session)
    num_proteins = await get_number_of_counts(sql_number_of_proteins, sql_values, session)

    existing = session.query(ProjectDetail).filter_by(project_id=project_id).first()
    if existing:
        existing.number_of_spectra = num_spectra
        existing.number_of_peptides = num_peptides
        existing.number_of_proteins = num_proteins
        session.commit()
        logger.info(f"Updated projectdetails for {project_id}: spectra={num_spectra}, peptides={num_peptides}, proteins={num_proteins}")
    else:
        logger.warning(f"No projectdetails record found for {project_id}, skipping count update")


@pride_router.post("/admin/recalculate-stats", tags=["Admin"])
async def recalculate_stats(
        project_id: str = Query(None, description="Optional project ID to refresh xiVIEW cache for. If omitted, refreshes all cached projects."),
        session: Session = Depends(get_session),
        redis_config_param=Depends(redis_config),
        api_key: str = Security(get_api_key)):
    """
    Clear all Redis caches and recalculate statistics.
    - Clears peptide_per_protein and labhead_count stats caches
    - Clears xiVIEW data caches (matches, peptides, proteins)
    - Triggers recalculation by calling the stats endpoints
    """
    redis_client = redis.Redis(host=redis_config_param['host'],
                               port=redis_config_param['port'],
                               password=redis_config_param['password'],
                               decode_responses=False)

    cleared_keys = []
    errors = []

    # 1. Clear statistics caches
    stats_keys = [
        redis_config_param.get('peptide_per_protein', 'peptide_per_protein'),
        redis_config_param.get('labhead_count', 'labhead_count'),
    ]
    for key in stats_keys:
        try:
            redis_client.delete(key)
            cleared_keys.append(str(key))
        except Exception as e:
            errors.append(f"Failed to delete key {key}: {e}")

    # 2. Clear xiVIEW caches
    try:
        if project_id:
            # Clear only the specified project
            for endpoint in ['matches', 'peptides', 'proteins']:
                pattern = f"xiview:{endpoint}:{project_id}*"
                for key in redis_client.scan_iter(match=pattern):
                    redis_client.delete(key)
                    cleared_keys.append(key.decode() if isinstance(key, bytes) else str(key))
        else:
            # Clear all xiVIEW caches
            for key in redis_client.scan_iter(match="xiview:*"):
                redis_client.delete(key)
                cleared_keys.append(key.decode() if isinstance(key, bytes) else str(key))
    except Exception as e:
        errors.append(f"Failed to clear xiVIEW cache: {e}")

    # 3. Recalculate projectdetails (number_of_proteins, number_of_peptides, number_of_spectra)
    recalculated = []
    updated_projects = []

    try:
        if project_id:
            project_ids = [project_id]
        else:
            sql_project_accession_list = text("SELECT DISTINCT u.project_id FROM upload u")
            project_ids = await get_accessions(sql_project_accession_list, {}, session)

        for pid in project_ids:
            try:
                await _recalculate_project_counts(pid, session)
                updated_projects.append(pid)
            except Exception as e:
                errors.append(f"Failed to update projectdetails for {pid}: {e}")
        recalculated.append("projectdetails")
    except Exception as e:
        errors.append(f"Failed to recalculate projectdetails: {e}")

    # 4. Recalculate Redis-cached statistics
    # Recalculate peptide_per_protein
    try:
        await peptide_per_protein(session=session, redis_config_param=redis_config_param)
        recalculated.append("peptide_per_protein")
    except Exception as e:
        errors.append(f"Failed to recalculate peptide_per_protein: {e}")

    # Recalculate labhead_count
    try:
        await labhead_count(session=session, redis_config_param=redis_config_param)
        recalculated.append("labhead_count")
    except Exception as e:
        errors.append(f"Failed to recalculate labhead_count: {e}")

    return {
        "status": "completed",
        "cleared_keys": cleared_keys,
        "updated_projects": updated_projects,
        "recalculated": recalculated,
        "errors": errors if errors else None,
        "message": f"Cleared {len(cleared_keys)} cache keys, updated projectdetails for {len(updated_projects)} projects, recalculated {len(recalculated)} statistics."
    }


def _extract_labheads_from_proxi(proxi_json):
    """Extract lab head names from a ProteomeXchange PROXI dataset response."""
    labheads = []
    for contact in proxi_json.get("contacts", []):
        terms = contact.get("terms", [])
        is_labhead = any(t.get("accession") == "MS:1002332" for t in terms)
        if is_labhead:
            for t in terms:
                if t.get("accession") == "MS:1000586" and t.get("value"):
                    labheads.append(t["value"])
    return labheads


@pride_router.get("/labhead-count", tags=["Statistics"])
async def labhead_count(session: Session = Depends(get_session),
                        redis_config_param=Depends(redis_config)):
    values = None
    try:
        key = redis_config_param.get('labhead_count', 'labhead_count')
        redis_client = redis.Redis(host=redis_config_param['host'],
                                   port=redis_config_param['port'],
                                   password=redis_config_param['password'],
                                   decode_responses=False)
        if redis_client is not None and redis_client.exists(key):
            values = redis_client.get(key)
            return json.loads(values)
        else:
            sql_project_accession_list = text("""
                SELECT DISTINCT u.project_id FROM upload u
            """)
            list_of_project_id = await get_accessions(sql_project_accession_list, {}, session)

            labhead_set = set()
            proxi_base_url = "https://proteomecentral.proteomexchange.org/api/proxi/v0.1/datasets/"
            for project_id in list_of_project_id:
                try:
                    response = requests.get(proxi_base_url + project_id, timeout=30)
                    if response.status_code == 200:
                        names = _extract_labheads_from_proxi(response.json())
                        labhead_set.update(names)
                except Exception as e:
                    logger.warning(f"Failed to fetch PROXI data for {project_id}: {e}")

            values = {
                "labhead_count": len(labhead_set)
            }

            if values:
                redis_client.set(key, json.dumps(values))
                return values
            else:
                return None
    except Exception as error:
        logger.error(error)
    return values


async def update_protein_metadata(list_of_project_sub_details):
    # 1. metadata from Uniprot
    logger.info("Updating protein level metadata from Uniprot API...")
    uniprot_records = await find_uniprot_data(list_of_project_sub_details)
    list_of_project_sub_details = await extract_uniprot_data(list_of_project_sub_details, uniprot_records)
    logger.info("Updating protein level metadata from Uniprot API COMPLETED")

    # 2. metadata from PDBe
    logger.info("Updating protein level metadata from PDBe API...")
    base_in_URL = "https://www.ebi.ac.uk/pdbe/api/mappings/best_structures/"
    list_of_project_sub_details = await find_data_availability(list_of_project_sub_details, base_in_URL, "PDBe")
    logger.info("Updating protein level metadata from PDBe API COMPLETED")

    # 3. metadata from AlphaFold
    logger.info("Updating protein level metadata from AlphaFold API...")
    base_in_URL = "https://alphafold.ebi.ac.uk/api/prediction/"
    list_of_project_sub_details = await find_data_availability(list_of_project_sub_details, base_in_URL, "AlphaFold")
    logger.info("Updating protein level metadata from AlphaFold API COMPLETED")
    logger.info("Updating protein level metadata COMPLETED 100%")
    return list_of_project_sub_details


async def find_uniprot_data(list_of_project_sub_details):
    i = 1
    batch_size = 4
    logging.info("Uniprot Batch size: " + str(batch_size))
    base_in_URL = "https://rest.uniprot.org/uniprotkb/search?query=accession:"
    fields_in_URL = "&fields=protein_name,gene_primary&size=50"
    seperator = "%20OR%20"
    accessions = []
    uniprot_records = []
    for sub_details in list_of_project_sub_details:
        accession = sub_details.protein_accession
        if '|' in accession:
            parts = accession.split('|')
            if len(parts) > 1:
                extracted_id = parts[1]
                print("Extracted ID:", extracted_id)
                accession = extracted_id
        accessions.append(accession)
        # batch size or last one in the list
        if i % batch_size == 0 or i == len(list_of_project_sub_details):
            complete_URL = ""
            try:
                # extra check not to submit plenty of accessions
                if len(accessions) <= batch_size:
                    accessions_in_URL = seperator.join(accessions)
                    complete_URL = base_in_URL + accessions_in_URL + fields_in_URL
                    logging.info("Calling Uniprot API: " + complete_URL)
                    uniprot_response = requests.get(complete_URL).json()
                    if uniprot_response is not None:
                        logging.info("Number of results found for the query: " + str(len(uniprot_response["results"])))
                        for result in uniprot_response["results"]:
                            uniprot_records.append(result)
                else:
                    raise Exception("Number of accessions are greater than the batch size!")
            except Exception as error:
                logger.error(str(error))
                logger.error(complete_URL + " failed to get data from Uniprot:" + str(error))
            finally:
                accessions = []
        i += 1
    return uniprot_records


async def extract_uniprot_data(list_of_project_sub_details, uniprot_records):
    for sub_details in list_of_project_sub_details:
        for uniprot_result in uniprot_records:
            try:
                if sub_details.protein_accession == uniprot_result["primaryAccession"]:
                    if not uniprot_result["entryType"] == "Inactive":
                        if uniprot_result["proteinDescription"]["recommendedName"] is not None:
                            sub_details.protein_name = \
                                uniprot_result["proteinDescription"]["recommendedName"]["fullName"]["value"]
                        elif uniprot_result["proteinDescription"]["submissionNames"] is not None \
                                and len(uniprot_result["proteinDescription"]["submissionNames"]) > 0:
                            sub_details.protein_name = \
                                uniprot_result["proteinDescription"]["submissionNames"][0]["fullName"]["value"]
                        logger.debug(uniprot_result["primaryAccession"] + " protein name: " + sub_details.protein_name)
                        if uniprot_result["genes"] is None or len(uniprot_result["genes"]) == 0:
                            logger.error("\t" + sub_details.protein_accession + " has no genes section")
                        elif len(uniprot_result["genes"]) > 0:
                            sub_details.gene_name = uniprot_result["genes"][0]["geneName"]["value"]
                            logger.debug(uniprot_result["primaryAccession"] + " gene name   : " + sub_details.gene_name)
                        else:
                            raise Exception("Error in matching genes section of uniprot response")
                    else:
                        logger.warning(uniprot_result["primaryAccession"] + "is Inactive")
            except Exception as error:
                logger.error(str(error))
                logger.error(
                    sub_details.protein_accession + " has an error when trying to match uniprot response:" + str(error))
    return list_of_project_sub_details


async def find_data_availability(list_of_project_sub_details, base_in_URL, resourse):
    for sub_details in list_of_project_sub_details:
        try:
            accession_in_URL = sub_details.protein_accession
            complete_URL = base_in_URL + accession_in_URL
            logging.debug("Calling API: " + complete_URL)
            response = requests.get(complete_URL).json()
            if resourse == "PDBe":
                sub_details.in_pdbe_kb = response is not None and len(response) > 0
            elif resourse == "AlphaFold":
                sub_details.in_alpha_fold_db = response is not None and len(response) > 0
        except Exception as error:
            logger.error(str(error))
            logger.error(complete_URL + " failed to get data from Uniprot:" + str(error))
    return list_of_project_sub_details


async def get_number_of_counts(sql, sql_values, session):
    """
    Get total number of counts (protein, peptide, spectra Identification) for a given project
    :param sql: sql statements for counts
    :param sql_values: Values for SQl(i.e Project accession or protein accession)
    :param session: database session
    :return: total number of counts
    """
    number_of_counts = 0
    try:
        result = session.execute(sql, sql_values)
        number_of_counts = result.scalar()
    except Exception as error:
        logger.error(error)
    finally:
        session.close()
        logger.debug('Database session is closed.')
    return number_of_counts


async def get_accessions(sql, sql_values, session):
    """
    Get all the Unique accessions(project, protein) in the database according to the SQL
    :param sql_values: SQl Values
    :param sql: SQL to get project accessions
    :param session: database session
    :return: List of unique project accessions
    """
    try:
        result = session.execute(sql, sql_values)
        # Fetch the list of accessions
        list_of_accessions = [row[0] for row in result]
    except Exception as error:
        logger.error(error)
    finally:
        session.close()
        logger.debug('Database session is closed.')
    return list_of_accessions


async def get_counts_table(sql, sql_values, session):
    """
    Get table of data in the database according to the SQL
    :param sql_values: SQl Values
    :param sql: SQL to get project accessions
    :param session: database session
    :return: List of key value pairs
    """
    try:
        with session:
            result = session.execute(sql, sql_values)
            result_list = [
                {'key': row[0], 'value': row[1]} for row in result if len(row) >= 2
            ]
    except Exception as error:
        logger.error(f"Error type: {type(error)}, Error message: {str(error)}")
    finally:
        logger.debug('Database session is closed.')
    return result_list


async def project_per_species_counts(sql, sql_values, session):
    """
    Get table of data in the database according to the SQL
    :param sql_values: SQl Values
    :param sql: SQL to get project accessions
    :param session: database session
    :return: List of key value pairs
    """
    try:
        with session:
            result = session.execute(sql, sql_values)
            result_list = [
                {'organism': row[0], 'count': row[1]} for row in result if len(row) >= 2
            ]
    except Exception as error:
        logger.error(f"Error type: {type(error)}, Error message: {str(error)}")
    finally:
        logger.debug('Database session is closed.')
    return result_list


async def peptide_per_protein_counts(sql, sql_values, session):
    """
    Get table of data in the database according to the SQL
    :param sql_values: SQl Values
    :param sql: SQL to get project accessions
    :param session: database session
    :return: List of key value pairs
    """
    try:
        with session:
            result = session.execute(sql, sql_values)
            result_list = [
                {'protein_frequency': row[0], 'peptide_count': row[1]} for row in result if len(row) >= 2
            ]
    except Exception as error:
        logger.error(f"Error type: {type(error)}, Error message: {str(error)}")
    finally:
        logger.debug('Database session is closed.')
    return result_list


async def get_statistics_count(sql, session):
    """
    Get all the Unique accessions(project, protein) in the database according to the SQL
    :param sql: SQL to get project accessions
    :param session: database session
    :return: List of unique project accessions
    """
    values = None
    try:
        result = session.execute(sql)
        # Fetch the values from the result
        row = result.fetchone()

        statistics_counts = {'Number of Projects': row[0],
                             'Number of proteins': row[1],
                             'Number of peptides': row[2],
                             'Number of spectra': row[3],
                             'Number of species': row[4]}
        print(statistics_counts)
    except Exception as error:
        logger.error(error)
    finally:
        session.close()
        logger.debug('Database session is closed.')
    return statistics_counts


async def get_projects_count(sql, session):
    """
    Get project counts for health check
    :param sql: SQL to get project accessions
    :param session: database session
    :return: count
    """
    try:
        result = session.execute(sql)
        # Fetch the values from the result
        count = result.fetchone()
    except Exception as error:
        logger.error(error)
    finally:
        session.close()
        logger.debug('Database session is closed.')
    return count
