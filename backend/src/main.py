from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI
import uvicorn

from scripts.seed_demo import seed
from src.config.settings import engine
from src.middlewares.request import limit_body, request_headers
from src.models.tables import archive_rollup, schema
from src.services.archive_rollup import empty_report
from src.utils.time import utc_time
from src.routes.workflow import router


@asynccontextmanager
async def lifespan(app):
    schema.create_all(engine)
    with engine.begin() as conn:
        if conn.execute(archive_rollup.select().where(archive_rollup.c.id == 1)).first() is None:
            conn.execute(archive_rollup.insert().values(id=1, report=empty_report(), updated_at=utc_time()))
    if os.getenv('SEED_DEMO', 'true').lower() == 'true':
        try:
            seed()
        except Exception:
            logging.exception('Demo seeding failed')
    yield


app = FastAPI(title='Database business workflow', lifespan=lifespan)
app.middleware('http')(limit_body)
app.middleware('http')(request_headers)
app.include_router(router)

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8000)
