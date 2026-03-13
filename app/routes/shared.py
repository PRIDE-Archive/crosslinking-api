import asyncpg
import functools
import json
import logging
import re
import os
import orjson
import logging.config
import time

import redis
from configparser import ConfigParser
from fastapi import status, HTTPException, Security, Response
from fastapi.responses import ORJSONResponse
from fastapi.security import APIKeyHeader
from typing import Optional, List, Any

from db_config_parser import security_API_key, redis_config as get_redis_config

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Global asyncpg connection pool
_db_pool: Optional[asyncpg.Pool] = None


async def init_db_pool():
    """Initialize the asyncpg connection pool. Call at app startup."""
    global _db_pool
    if _db_pool is not None:
        return
    db_info = await _parse_database_info()
    _db_pool = await asyncpg.create_pool(
        min_size=10,
        max_size=30,
        **db_info,
    )
    logger.info("asyncpg connection pool initialized (min=10, max=30)")


async def close_db_pool():
    """Close the asyncpg connection pool. Call at app shutdown."""
    global _db_pool
    if _db_pool is not None:
        await _db_pool.close()
        _db_pool = None
        logger.info("asyncpg connection pool closed")


async def _parse_database_info():
    config = os.environ.get('DB_CONFIG', 'database.ini')
    parser = ConfigParser()
    parser.read(config)
    section = 'postgresql'
    db = {}
    if parser.has_section(section):
        params = parser.items(section)
        for param in params:
            db[param[0]] = param[1]
    else:
        raise Exception(f'Section {section} not found in the {config} file')
    return db


# Shared Redis connection pool (lazy-initialized)
_redis_pool: Optional[redis.ConnectionPool] = None
_XIVIEW_CACHE_TTL = 43200  # 12 hours in seconds


def _get_redis_client() -> Optional[redis.Redis]:
    """Get a Redis client using a shared connection pool."""
    global _redis_pool
    try:
        if _redis_pool is None:
            redis_cfg = get_redis_config()
            _redis_pool = redis.ConnectionPool(
                host=redis_cfg['host'],
                port=int(redis_cfg['port']),
                password=redis_cfg.get('password'),
                max_connections=20,
                decode_responses=False,
            )
        return redis.Redis(connection_pool=_redis_pool)
    except Exception as e:
        logger.warning(f"Redis unavailable, skipping cache: {e}")
        return None


def get_cached_response(cache_key: str) -> Optional[bytes]:
    """Try to get a cached response from Redis."""
    client = _get_redis_client()
    if client is None:
        return None
    try:
        data = client.get(cache_key)
        if data:
            logger.info(f"Cache HIT for {cache_key}")
        return data
    except Exception as e:
        logger.warning(f"Redis get failed for {cache_key}: {e}")
        return None


def set_cached_response(cache_key: str, data: bytes, ttl: int = _XIVIEW_CACHE_TTL):
    """Store a response in Redis with TTL."""
    client = _get_redis_client()
    if client is None:
        return
    try:
        client.setex(cache_key, ttl, data)
        logger.info(f"Cache SET for {cache_key} (TTL={ttl}s)")
    except Exception as e:
        logger.warning(f"Redis set failed for {cache_key}: {e}")


def build_xiview_cache_key(endpoint: str, project: str, file: Optional[str] = None) -> str:
    """Build a consistent cache key for xiVIEW endpoints."""
    if file:
        return f"xiview:{endpoint}:{project}:{file}"
    return f"xiview:{endpoint}:{project}"


def log_execution_time_async(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        result = await func(*args, **kwargs)
        end_time = time.time()
        execution_time = end_time - start_time
        logging.info(f'"{func.__name__}" executed in {execution_time:.2f} seconds')
        return result
    return wrapper


def log_json_size(json_bytes, name):
    json_size_mb = len(json_bytes) / (1024 * 1024)
    logger.info(f"uncompressed size of json {name}: {json_size_mb} Mb")

# @log_execution_time_async
async def get_most_recent_upload_ids(pxid, file=None):

    """
    Get the most recent upload ids for a project/file.

    :param pxid: identifier of a project,
        for ProteomeXchange projects this is the PXD****** accession
    :param file: name of the file
    :return: upload ids
    """
    if file:
        filename_clean = re.sub(r'[^0-9a-zA-Z-]+', '-', file)
        query = """SELECT id FROM upload 
                WHERE project_id = $1 AND identification_file_name_clean = $2
                ORDER BY upload_time DESC LIMIT 1;"""
        upload_ids = await execute_query(query, [pxid, filename_clean], fetch_one=True)
    else:
        query = """SELECT u.id
                    FROM upload u
                    where u.upload_time = 
                        (select max(upload_time) from upload 
                        where project_id = u.project_id 
                        and identification_file_name = u.identification_file_name )
                    and u.project_id = $1;"""
        upload_ids = await execute_query(query, [pxid])
    if upload_ids is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No upload found for the given project/file")
    return upload_ids


async def get_db_connection():
    """Get a connection from the pool. Prefer using execute_query() instead."""
    if _db_pool is None:
        await init_db_pool()
    conn = await _db_pool.acquire()
    await conn.set_type_codec(
        'json',
        encoder=json.dumps,
        decoder=json.loads,
        schema='pg_catalog'
    )
    return conn


def get_api_key(key: str = Security(api_key_header)) -> str:

    if key == security_API_key():
        return key
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API Key",
    )


async def execute_query(query: str, params: Optional[List[Any]] = None, fetch_one: bool = False):
    """
    Execute a query and return the result
    :param query: the query to execute
    :param params: the parameters to pass to the query
    :param fetch_one: whether to fetch one result or all
    :return: the result of the query
    """
    if _db_pool is None:
        await init_db_pool()
    try:
        async with _db_pool.acquire() as conn:
            await conn.set_type_codec(
                'json',
                encoder=json.dumps,
                decoder=json.loads,
                schema='pg_catalog'
            )
            if fetch_one:
                result = await conn.fetchrow(query, *(params or []))
            else:
                result = await conn.fetch(query, *(params or []))
            return result

    except Exception as e:
        logging.error(f"Database operation failed: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database operation failed")

async def fetch_json_response(query, params):
    """
    Fetch a JSON response from the database
    :param query: the query to execute
    :param params: the parameters to pass to the query
    :return: the JSON response
    """
    records = await execute_query(query, params)
    return ORJSONResponse([dict(r) for r in records])
