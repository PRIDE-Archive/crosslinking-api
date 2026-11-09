import base64
import configparser
import traceback
from enum import Enum
from typing import Annotated, Union

from parser.APIWriter import APIWriter
from typing_extensions import Doc

from fastapi import APIRouter, Depends, Query, Body
from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from sqlalchemy.orm import Session
from models.upload import Upload

from app.routes.pride import invalidate_cache
from app.routes.shared import get_api_key
from db_config_parser import get_conn_str
from index import get_session
from sqlalchemy import Table, MetaData, create_engine
import logging.config

logger = logging.getLogger(__name__)
# writer = APIWriter(get_conn_str())
engine = create_engine(get_conn_str())
meta = MetaData()

parser_router = APIRouter()
config = configparser.ConfigParser()
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class TableNamesEnum(str, Enum):
    """Enumeration of Table names"""
    # todo - alphebetize list of tables here and in other places, for consistency
    analysiscollectionspectrumidentification = "analysiscollectionspectrumidentification"
    dbsequence = "dbsequence"
    enzyme = "enzyme"
    modifiedpeptide = "modifiedpeptide"
    peptideevidence = "peptideevidence"
    searchmodification = "searchmodification"
    spectrum = "spectrum"
    match = "match"
    spectradata = "spectradata"
    spectrumidentificationprotocol = "spectrumidentificationprotocol"
    upload = "upload"
    projectdetail = "projectdetail"
    projectsubdetail = "projectsubdetail"


@parser_router.post("/write_data", tags=["Parser"], response_model=None)
async def write_data(
        table: TableNamesEnum = Body(..., description="table name", embeded=True),
        data=Body(..., description="table data", embeded=True),
        api_key: str = Security(get_api_key),
        session: Session = Depends(get_session)) -> None:
    """
        Insert data into table.

        :param session:
        :param api_key:
        :param table: (str) Table name
        :param data: (list dict) data to insert.
        """
    try:
        if table == TableNamesEnum.spectrum:
            print("spectrum table")
            i = 0
            for spectra in data:
                spectra["mz"] = base64.b64decode(spectra["mz"])
                spectra["intensity"] = base64.b64decode(spectra["intensity"])
                data[i] = spectra
                i += 1

        table = Table(table.name, meta, autoload_with=engine)
        with engine.connect() as conn:
            statement = table.insert().values(data)
            result = conn.execute(statement)
            conn.commit()
            conn.close()
        # invalidate_cache()
        logger.info("Invalidated Cache")
        return None

    except Exception as e:
        print(f"Caught an exception: {e}")
        traceback.print_exc()
    finally:
        session.close()
    return None


@parser_router.post("/write_new_upload", tags=["Parser"])
def write_new_upload(
        data: dict = Body(..., description="table data", embeded=True),
        api_key: str = Security(get_api_key),
        session: Session = Depends(get_session)):
    try:
        new_upload = Upload(
            identification_file_name=data['identification_file_name'],
            identification_file_name_clean=data['identification_file_name_clean'],
            project_id=data['project_id']
        )
        session.add(new_upload)
        session.commit()
        session.close()
        return new_upload.id
    except Exception as e:
        session.rollback() 
        # Log the full traceback server-side and surface the real cause to the
        # caller (internal, API-key-protected endpoint) instead of a bare 500.
        logger.exception("write_new_upload failed for %s", data.get('identification_file_name'))
        raise HTTPException(status_code=500, detail=f"write_new_upload failed: {type(e).__name__}: {e}")


@parser_router.post("/write_mzid_info", tags=["Parser"])
def write_mzid_info(analysis_software_list=Body(..., embeded=True),
                    spectra_formats=Body(..., embeded=True),
                    provider=Body(..., embeded=True),
                    audits=Body(..., embeded=True),
                    samples=Body(..., embeded=True),
                    bib=Body(..., embeded=True),
                    upload_id: int = Query(default=None),
                    api_key: str = Security(get_api_key),
                    session: Session = Depends(get_session)):
    """
    Update Upload row with mzid info.
    :param upload_id:
    :param analysis_software_list:
    :param session:
    :param api_key:
    :param spectra_formats:
    :param provider:
    :param audits:
    :param samples:
    :param bib:
    :return:
    """
    upload = Table(TableNamesEnum.upload.name, meta, autoload_with=engine, quote=False)
    stmt = upload.update().where(upload.c.id == str(upload_id)).values(
        analysis_software_list=analysis_software_list,
        spectra_formats=spectra_formats,
        provider=provider,
        audit_collection=audits,
        analysis_sample_collection=samples,
        bib=bib
    )
    with engine.connect() as conn:
        conn.execute(stmt)
        conn.commit()


@parser_router.post("/write_other_info", tags=["Parser"])
def write_other_info(contains_crosslinks=Body(..., description="contains_crosslinks", embeded=True),
                     upload_warnings=Body(..., description="upload_warnings", embeded=True),
                     upload_id: int = None,
                     api_key: str = Security(get_api_key),
                     session: Session = Depends(get_session)):
    """
    Update Upload row with remaining info.

    :param session:
    :param upload_id:
    :param api_key:
    :param contains_crosslinks:
    :param upload_warnings:
    :return:
    """

    upload = Table(TableNamesEnum.upload.name, meta, autoload_with=engine, quote=False)
    with engine.connect() as conn:
        stmt = upload.update().where(upload.c.id == str(upload_id)).values(
            contains_crosslinks=contains_crosslinks,
            upload_warnings=upload_warnings,
        )
        conn.execute(stmt)
        conn.commit()
