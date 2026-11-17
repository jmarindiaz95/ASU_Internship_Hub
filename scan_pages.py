"""Manually read known career pages for keywords, optionally saving to PostGIS."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from hub.config import ROOT
from hub.pages import load_page_config, scan_source


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=ROOT/'config/known_pages.json',help='Known sites and editable keyword list.')
    parser.add_argument('--source',action='append',help='Read only this source ID. May be repeated.')
    parser.add_argument('--keyword',action='append',help='Replace configured keywords; repeat for alternatives, e.g. --keyword accounting --keyword finance.')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/latest_scan.json',help='Save extracted text, matches, evidence, and per-source results as JSON.')
    parser.add_argument('--save',action='store_true',help='Save extracted records to PostGIS; unresolved locations remain visible without a map point.')
    parser.add_argument('--list-sources',action='store_true',help='List configured sources without requesting any pages.')
    args=parser.parse_args(argv)
    try:
        settings=load_page_config(args.config)
    except (ValueError,OSError) as exc:
        parser.error(str(exc))
    if args.list_sources:
        for source in settings['sources']:
            print(f"{source['id']}: {source['name']} ({source['kind']})")
        return 0
    ids={s['id'] for s in settings['sources']}
    if args.source and set(args.source)-ids:
        parser.error('Unknown source ID: '+', '.join(sorted(set(args.source)-ids)))
    sources=[s for s in settings['sources'] if s['id'] in args.source] if args.source else [s for s in settings['sources'] if s['enabled']]
    if not sources:
        parser.error('No enabled sources are selected.')
    if args.keyword and any(not word.strip() for word in args.keyword):
        parser.error('Keywords cannot be blank.')
    if args.save and args.keyword:
        parser.error('Keyword overrides are report-only. Edit the config file to change keywords used for saved/scheduled collection.')
    results=[]
    if args.save:
        from hub.ingest import register_sources
        from hub.collectors import refresh_all
        register_sources(args.config)
        results=refresh_all(source_ids=[s['id'] for s in sources],include_records=True,include_disabled=bool(args.source))
        if not results:
            print('Another refresh is running. Try again after it finishes.',file=sys.stderr)
            return 2
    else:
        for source in sources:
            if args.keyword:
                source['config']['keywords']=[word.strip() for word in args.keyword]
                source['config']['filter_keywords']=True
                source['config']['job_scope']='keywords'
            print(f"Reading {source['name']}…",flush=True)
            report=scan_source(source)
            report.pop('contact_text',None)
            results.append(report)
    payload={'checked_at':datetime.now(timezone.utc).isoformat(),'saved_to_database':args.save,'sources':results,
        'totals':{'connected':sum(bool(r.get('connected')) for r in results),
            'listings':sum(r.get('matched',0) for r in results),
            'errors':sum(len(r.get('errors',[])) for r in results)}}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(payload,indent=2,ensure_ascii=False,default=lambda value:value.isoformat()),encoding='utf-8')
    for report in results:
        print(f"{report['source']}: {report['status']} · {report.get('message','Unexpected refresh error')}")
    print(f"Report saved: {args.output}")
    return 0 if all(r['status']=='ok' for r in results) else 1


if __name__=='__main__':
    raise SystemExit(main())
