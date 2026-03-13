import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import logging

from app.routes.pride import pride_router
from app.routes.pdbdev import pdbdev_router
from app.routes.xiview import xiview_data_router
from app.routes.parse import parser_router
from app.routes.shared import init_db_pool, close_db_pool
from fastapi.middleware.gzip import GZipMiddleware
from db_config_parser import API_version

# Initialize logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
API_VERSION = API_version()


@asynccontextmanager
async def lifespan(app):
    # Startup: initialize asyncpg connection pool
    await init_db_pool()
    yield
    # Shutdown: close asyncpg connection pool
    await close_db_pool()


app = FastAPI(title="Crosslinking API",
              description="This is a REST API for accessing crosslinking data from the PRIDE Crosslinkling resource.",
              version="3.0.1",
                contact={
                  "name": "PRIDE Team",
                  "url": "https://www.ebi.ac.uk/pride/",
                  "email": "pride-support@ebi.ac.uk",
              },
              license_info={
                  "name": "Apache 2.0",
                  "url": "https://www.apache.org/licenses/LICENSE-2.0.html",
              },
              openapi_url="/pride/ws/archive/crosslinking/" + API_VERSION + "/openapi.json",
              docs_url="/pride/ws/archive/crosslinking/" + API_VERSION + "/docs",
              lifespan=lifespan)

# Set up CORS middleware
origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Handles GZip responses for any request that includes "gzip" in the "Accept-Encoding" header.
# The middleware will handle both standard and streaming responses.
# Do not GZip responses that are smaller than this minimum size in bytes,
# Tier 4 Network level compression, no need to worry at Tier 7(HTTPS) level
app.add_middleware(GZipMiddleware, minimum_size=1000)


# Middleware to log the time taken for each request
@app.middleware("http")
async def log_request_time(request: Request, call_next):
    start_time = time.time()
    try:
        response = await call_next(request)
    except Exception as e:
        process_time = time.time() - start_time
        logger.error(f"Request: {request.method} {request.url.path} raised an error in {process_time:.4f} seconds: {str(e)}")
        raise
    process_time = time.time() - start_time
    if not (request.url.path.startswith("/pride/ws/archive/crosslinking/" + API_VERSION + "/data/visualisations") or
            request.url.path.startswith("/pride/ws/archive/crosslinking/" + API_VERSION + "/health")):
        logger.info(f"Request: {request.method} {request.url.path} completed in {process_time:.4f} seconds")

    return response

app.include_router(pride_router, prefix="/pride/ws/archive/crosslinking/" + API_VERSION)
# app.include_router(pdbdev_router, prefix="/pride/ws/archive/crosslinking/" + API_VERSION + "/pdbdev", tags=["Deprecated: PDBDev"])
app.include_router(pdbdev_router, prefix="/pride/ws/archive/crosslinking/" + API_VERSION + "/pdbihm", tags=["PDB-IHM"])
app.include_router(xiview_data_router, prefix="/pride/ws/archive/crosslinking/" + API_VERSION + "/data")
app.include_router(parser_router, prefix="/pride/ws/archive/crosslinking/" + API_VERSION + "/parse")

