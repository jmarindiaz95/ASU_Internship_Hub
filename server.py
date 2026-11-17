import logging
import os
from waitress import serve
from hub.api import create_app
from hub.collectors import start_scheduler
from hub.config import PORT

app=create_app()

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    if os.environ.get('INITIALIZE_DATABASE')=='1':
        from hub.db import initialize
        from hub.ingest import register_sources
        initialize()
        register_sources()
    start_scheduler()
    print(f'ASU Internship Hub: http://127.0.0.1:{PORT}',flush=True)
    serve(app,host=os.environ.get('HOST','127.0.0.1'),port=PORT,threads=6)
