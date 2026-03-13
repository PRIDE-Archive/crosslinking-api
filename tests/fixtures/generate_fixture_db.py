"""
One-time script to generate tests/fixtures/test_db.sql.

Usage (from repo root, with pipenv active):
    pipenv run python tests/fixtures/generate_fixture_db.py

Requires PostgreSQL access via ximzid_unittests user.
"""
import logging
import os
import subprocess
import sys

from sqlalchemy import create_engine, text

sys.path.insert(0, "/home/cc/work/mzidentml-reader")

from parser import MzIdParser
from parser.DatabaseWriter import DatabaseWriter
from parser.database.create_db_schema import create_db, create_schema, drop_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("fixture_gen")

CONN_STR = "postgresql://ximzid_unittests:ximzid_unittests@localhost:5432/crosslinking_fixture_gen"
MZID_DIR = "/home/cc/work/mzidentml-reader/tests/fixtures/mzid_parser"
PEAKLIST_DIR = os.path.join(MZID_DIR, "peaklist")
OUT_FILE = os.path.join(os.path.dirname(__file__), "test_db.sql")

FILES = [
    ("ECOLI_MZML", os.path.join(MZID_DIR, "mzml_ecoli_dsso.mzid"), PEAKLIST_DIR),
    ("ECOLI_MGF", os.path.join(MZID_DIR, "mgf_ecoli_dsso.mzid"), PEAKLIST_DIR),
    ("F002553", os.path.join(MZID_DIR, "F002553.mzid"), False),
    ("F002553_SS", os.path.join(MZID_DIR, "F002553_samesets.mzid"), False),
    ("EDC_1_3", os.path.join(MZID_DIR, "1.3.0", "Xlink_EDC_mzIdentML_1_3_0_draft.mzid"), False),
    ("MULTI_SPEC", os.path.join(MZID_DIR, "1.3.0", "multiple_spectra_per_id_1_3_0_draft.mzid"), False),
    ("NONCOV", os.path.join(MZID_DIR, "1.3.0", "noncovalently_assoc_1_3_0_draft.mzid"), False),
]

logger.info("Dropping and recreating fixture DB...")
try:
    drop_db(CONN_STR)
except Exception as e:
    logger.warning(f"drop_db: {e}")
create_db(CONN_STR)
create_schema(CONN_STR)
logger.info("Schema created.")

for pxid, mzid_file, peaklist in FILES:
    logger.info(f"Parsing {pxid} ({os.path.basename(mzid_file)})...")
    writer = DatabaseWriter(CONN_STR, pxid=pxid)
    parser = MzIdParser.MzIdParser(mzid_file, peaklist, writer, logger)
    parser.parse()
    if hasattr(writer, "engine"):
        writer.engine.dispose()
    logger.info(f"  done: {pxid}")

logger.info("Inserting projectdetails and projectsubdetails rows...")
engine = create_engine(CONN_STR)
with engine.connect() as conn:
    for pxid, _, _ in FILES:
        conn.execute(
            text("""
                INSERT INTO projectdetails (project_id, title, description, organism)
                VALUES (:pid, :title, 'Fixture data for crosslinking-api tests', 'Escherichia coli')
            """),
            {"pid": pxid, "title": f"{pxid} test project"},
        )
    conn.commit()

    # Fetch the ECOLI_MZML project detail id for sub-detail insertion
    row = conn.execute(
        text("SELECT id FROM projectdetails WHERE project_id = 'ECOLI_MZML' LIMIT 1")
    ).fetchone()
    ecoli_detail_id = row[0]

    # Insert one minimal projectsubdetails row so the /projects search endpoint works
    conn.execute(
        text("""
            INSERT INTO projectsubdetails
                (project_detail_id, protein_db_ref, protein_name, gene_name,
                 protein_accession, number_of_peptides, number_of_cross_links,
                 in_pdbe_kb, in_alpha_fold_db)
            VALUES
                (:did, 'sp|P0A9Q7|DNAK_ECOLI', 'Chaperone protein DnaK', 'dnaK',
                 'P0A9Q7', 5, 3, false, false)
        """),
        {"did": ecoli_detail_id},
    )
    conn.commit()
engine.dispose()
logger.info("projectdetails and projectsubdetails inserted.")

logger.info(f"Dumping to {OUT_FILE}...")
env = os.environ.copy()
env["PGPASSWORD"] = "ximzid_unittests"
result = subprocess.run(
    ["pg_dump", "-U", "ximzid_unittests", "-h", "localhost", "-d", "crosslinking_fixture_gen", "-f", OUT_FILE],
    env=env,
    capture_output=True,
    text=True,
)
if result.returncode != 0:
    logger.error(f"pg_dump failed:\n{result.stderr}")
    sys.exit(1)
logger.info(f"Dump written to {OUT_FILE}")

logger.info("Dropping fixture generation DB...")
try:
    drop_db(CONN_STR)
except Exception as e:
    logger.warning(f"drop_db cleanup: {e}")
logger.info("Done!")
