import argparse
import json
from hub.db import initialize
from hub.ingest import import_workbook,register_sources
from hub.collectors import refresh_all

parser=argparse.ArgumentParser(description='Manage the local ASU Internship Hub database and collectors.')
commands=parser.add_subparsers(dest='command',required=True)
commands.add_parser('init-db',help='Create the dedicated internship_hub schema; existing geographic tables are preserved.')
commands.add_parser('enrich-locations',help='Automatically resolve collected job worksites and company-address references.')
workbook=commands.add_parser('import-workbook',help='Import original research without advancing source-check dates.')
workbook.add_argument('path')
collect=commands.add_parser('collect',help='Refresh configured public sources once.')
collect.add_argument('--source',action='append',help='Only refresh this source ID. May be repeated.')
args=parser.parse_args()
if args.command=='init-db':
    initialize()
    register_sources()
    print('Database initialized.')
elif args.command=='import-workbook':
    print(json.dumps(import_workbook(args.path)))
elif args.command=='enrich-locations':
    from hub.locations import enrich_existing
    print(json.dumps(enrich_existing(),indent=2))
else:
    print(json.dumps(refresh_all(source_ids=args.source),indent=2))
