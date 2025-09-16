import asyncio
import importlib
import os
import signal
import sys
import traceback
from contextlib import asynccontextmanager
from typing import AnyStr, Dict, List, Optional

import asyncpg
import pydash
import uvicorn
import yaml
from base4.schemas import DatabaseConfig
from base4.utilities.db.base import TORTOISE_ORM
from base4.utilities.files import get_project_config_folder
from base4.utilities.logging.setup import get_logger
from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import JSONResponse
from tortoise import Tortoise
from tortoise.contrib.fastapi import register_tortoise

_test: Optional[AnyStr] = os.getenv('TEST_MODE', None)
_database_to_use: Optional[AnyStr] = os.getenv('TEST_DATABASE', None)
_conn = None


@asynccontextmanager
async def lifespan(service: FastAPI) -> None:

    print("APPSTART")
    for route in service.routes:
        print(route.path)

    await startup_event()
    yield
    await shutdown_event()


async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler that logs full traceback for 500 errors"""
    logger = get_logger()
    
    # Get the full traceback
    tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
    tb_str = ''.join(tb)
    
    # Log the full error with traceback
    logger.error(f"Unhandled exception in {request.method} {request.url}: {str(exc)}\nTraceback:\n{tb_str}")
    
    # Return proper HTTP 500 response
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"}
    )


def get_service() -> FastAPI:
    if hasattr(get_service, 'service'):
        return get_service.service

    from base4 import configuration

    cfg = configuration('services')

    api_prefix = os.getenv('GENERAL_API_PREFIX', None)
    if not api_prefix:
        raise Exception('GENERAL_API_PREFIX is not set in environment variables')

    _docs = pydash.get(cfg, 'GENERAL_DOCS_URI', '/docs')
    _openapi = pydash.get(cfg, 'GENERAL_OPENAPI_URI', '/openapi.json')
    _redoc = pydash.get(cfg, 'GENERAL_REDOC_URI', '/redoc')

    docs_path = pydash.get(cfg, 'general.docs.docs_path', f'{api_prefix}{_docs}')
    openapi_path = pydash.get(cfg, 'general.docs.openapi_path', f'{api_prefix}{_openapi}')
    redoc_path = pydash.get(cfg, 'general.docs.redoc_path', f'{api_prefix}{_redoc}')
    service: FastAPI = FastAPI(lifespan=lifespan, openapi_url=openapi_path, docs_url=docs_path, redoc_url=redoc_path)
    
    # Add global exception handler
    service.add_exception_handler(Exception, global_exception_handler)
    
    get_service.service: FastAPI = service
    return service


async def startup_event(services: List[str] = None) -> None:
    """
    Code which is executed before starting application.
    If application is in test mode it'll execute specific
    startup method for test mode.

    :return: None
    """

    # TODO: ako nemas boljinacin da proguras ovo, ucitaj ih iz services.yaml fajla ako nisu dosli
    if not services:
        services = []

        with open(get_project_config_folder() / 'services.yaml') as f:
            services = yaml.safe_load(f)['services']
            services = [list(s.keys())[0] if type(s) == dict else s for s in services]

    if isinstance(_test, str):
        return await test_startup_event(services)

    for svc_name in services:
        DATABASE: DatabaseConfig = DatabaseConfig(
            svc_name=svc_name,
        )

        await _initialize_tortoise_models(conf=DATABASE, conn='conn_' + svc_name)

        ...


async def shutdown_event() -> None:
    if isinstance(_test, str):
        return await test_shutdown_event()


""" TEST MODE """


async def test_startup_event(services: List[str]) -> None:
    """
    Test startup events such as initializing db, models...

    :return: None
    """

    if _database_to_use == 'sqlite':
        await _initialize_tortoise_models(test_mode=True, conn='conn_test')
        return

    DATABASE: DatabaseConfig = DatabaseConfig(_testmode=True, db_name=os.getenv('DB_TEST', None), svc_name='test')

    await _delete_and_create_test_database(conf=DATABASE)

    await _initialize_tortoise_models(conf=DATABASE, conn='conn_test', test_mode=True, services=services)
    ...


async def test_shutdown_event() -> None:
    await Tortoise.close_connections()


async def _delete_and_create_test_database(conf: DatabaseConfig) -> None:
    """
    Droping test database if exists and creating new.

    :param conf: Database configuration.
    :return:
    """

    _connection: Dict = {
        'user': conf.db_postgres_user,
        'password': conf.db_postgres_password,
        'host': conf.db_postgres_host,
        'port': int(conf.db_postgres_port),
        'database': 'template1',
    }

    try:
        _conn: asyncpg.connection.Connection = await asyncpg.connect(**_connection)
    except Exception:
        raise

    try:
        exists: int = await _conn.fetchval('SELECT 1 FROM pg_database WHERE datname = $1', 'template1')

        if exists:

            while True:

                kill_attempt = 0
                try:
                    await _conn.execute(
                        """
                        SELECT pg_terminate_backend(pid)
                        FROM pg_stat_activity
                        WHERE datname = $1
                        """,
                        conf.db_name,
                    )

                    break
                except Exception:
                    kill_attempt += 1
                    if kill_attempt > 3:
                        raise NameError('Could not kill connections')
                    await asyncio.sleep(kill_attempt)

            await _conn.execute(f'DROP DATABASE {conf.db_name}')

        await _conn.execute(f'CREATE DATABASE {conf.db_name}')

    except Exception as e:
        raise

    finally:
        await _conn.close()


async def _initialize_tortoise_models(conf: Optional[DatabaseConfig] = None, conn: str = 'default', test_mode: bool = False, services=None) -> None:
    """
    Initialize Tortoise ORM with the provided configuration.

    Args:
        conf (Dict): Configuration dictionary containing database connection details.
    """

    _url: AnyStr = f'sqlite://:memory:'
    if conf:
        _url: AnyStr = f'postgres://{conf.db_postgres_user}:{conf.db_postgres_password}@{conf.db_postgres_host}:{conf.db_postgres_port}/{conf.db_name}'

    TORTOISE_ORM['connections'][conn] = _url

    if services:
        apps = {}
        for app in TORTOISE_ORM['apps']:
            if app in services:  # or app=='aerich':
                apps[app] = TORTOISE_ORM['apps'][app]

        TORTOISE_ORM['apps'] = apps

    if test_mode:
        for conn in TORTOISE_ORM['connections']:
            TORTOISE_ORM['connections'][conn] = TORTOISE_ORM['connections']['conn_test']
        ...
    
    # register_tortoise(
    #     service,
    #     config=TORTOISE_ORM,
    #     generate_schemas=True,
    #     add_exception_handlers=True,
    # )
    try:
        await Tortoise.init(config=TORTOISE_ORM)
        await Tortoise.generate_schemas()
    except Exception as e:
        print(e)
        raise
    return


class GracefulShutdown:
    def __init__(self):
        self.should_exit = False
        self.exit_code = 0
        signal.signal(signal.SIGTERM, self.handle_signal)
        signal.signal(signal.SIGINT, self.handle_signal)

    def handle_signal(self, signum, frame):
        print(f"Received signal {signum}. Initiating shutdown...")
        self.should_exit = True
        self.exit_code = signum  # Standard exit code for signals


def load_services(single_service=None):

    api_prefix = os.getenv('GENERAL_API_PREFIX', None)

    if not api_prefix:
        raise Exception('GENERAL_API_PREFIX is not set in environment variables')

    with open(get_project_config_folder() / 'services.yaml') as f:

        services = yaml.safe_load(f)
        for service in services['services']:
            if isinstance(service, str):
                svc_name = service
            else:
                svc_name = list(service.keys())[0]
            
            if not single_service:
                try:
                    importlib.import_module(f"services.{svc_name}.api.run")
                except Exception as e:
                    try:
                        importlib.import_module(f"services.{svc_name}.api")
                    except Exception as e:
                        raise
                        
            else:
                # todo, postoji sada problem kada pokrenem jedan servis

                # if service != single_service:
                #     continue
                try:
                    importlib.import_module(f"services.{svc_name}.api.run")
                except Exception as e:
                    try:
                        importlib.import_module(f"services.{svc_name}.api")
                    except Exception as e:
                        raise


def run_server(config, single_service=None):
    server = uvicorn.Server(config)
    shutdown_handler = GracefulShutdown()

    async def custom_on_tick():
        if shutdown_handler.should_exit:
            await server.shutdown()

    server.force_exit = False
    server.custom_on_tick = custom_on_tick

    load_services(single_service=single_service)

    try:
        server.run()
    except Exception as e:
        print(f"Server stopped unexpectedly: {e}")
    finally:
        print(f"Server has been shut down. Exit code: {shutdown_handler.exit_code}")
        sys.exit(shutdown_handler.exit_code)


service: FastAPI = get_service()
