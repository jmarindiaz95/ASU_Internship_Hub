import logging
import math
import threading
from datetime import date, datetime
from decimal import Decimal

import psycopg
from flask import Flask, jsonify, redirect, render_template_string, request, send_from_directory

from .collectors import REFRESH_LOCK, refresh_all
from .config import AUTO_REFRESH, CAMPUS, MAX_AGE, ROOT
from .db import connect
from .domain import safe_url

LOG=logging.getLogger(__name__)

BASE_QUERY='''WITH evaluated AS (
 SELECT o.*, c.name AS company, cat.label AS category, web.first_found_at,
   o.deadline < (now() AT TIME ZONE 'America/Phoenix')::date AS deadline_passed,
   COALESCE(o.review_note ~* 'HTTP (404|410)|Listing page unavailable',false) AS source_missing,
   COALESCE(l.worksites,'[]'::jsonb) AS worksites, l.distance_miles,
   array_remove(ARRAY[
     CASE WHEN o.review_note ~* 'HTTP (404|410)|Listing page unavailable' THEN 'Source page no longer available' END,
     CASE WHEN o.status<>'open' THEN CASE WHEN o.status='closed' THEN 'Closed listing' WHEN o.status='program' THEN 'Current intake not confirmed' ELSE 'Availability not confirmed' END END,
     CASE WHEN o.deadline < (now() AT TIME ZONE 'America/Phoenix')::date THEN 'Deadline passed' END,
     CASE WHEN o.review_state<>'verified' THEN COALESCE(o.review_note,'Needs review') END,
     CASE WHEN o.source_checked_at IS NULL OR o.source_checked_at < now()-make_interval(days => %s) THEN 'Source check needed' END,
     CASE WHEN l.distance_miles IS NULL THEN 'Exact worksite not verified' END
   ],NULL) AS review_reasons
 FROM internship_hub.effective_opportunities o
 JOIN internship_hub.companies c ON c.id=o.company_id
 JOIN internship_hub.categories cat ON cat.id=o.category_id
 JOIN LATERAL (
   SELECT min(sl.first_seen_at) AS first_found_at
   FROM internship_hub.source_listings sl JOIN internship_hub.sources s ON s.id=sl.source_id
   WHERE sl.opportunity_id=o.id AND s.kind<>'manual' AND sl.fetched_at IS NOT NULL
   HAVING count(*)>0
 ) web ON true
 LEFT JOIN LATERAL (
   SELECT jsonb_agg(jsonb_build_object('id',loc.id,'address',loc.address,'city',loc.city,'state',loc.state,
     'longitude',ST_X(loc.geom),'latitude',ST_Y(loc.geom),
     'distance_miles',ST_Distance(loc.geom::geography,ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography,false)/1609.344,
     'evidence_url',ol.evidence_url,'basis',ol.basis) ORDER BY loc.id) AS worksites,
     min(ST_Distance(loc.geom::geography,ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography,false)/1609.344) AS distance_miles
   FROM internship_hub.opportunity_locations ol JOIN internship_hub.locations loc ON loc.id=ol.location_id
   WHERE ol.opportunity_id=o.id AND ol.verified AND ol.active
 ) l ON true
) '''
BASE_PARAMS=(MAX_AGE,CAMPUS['longitude'],CAMPUS['latitude'],CAMPUS['longitude'],CAMPUS['latitude'])
AVAILABLE="status='open' AND NOT COALESCE(deadline_passed,false) AND NOT source_missing AND source_checked_at >= now()-make_interval(days => "+str(MAX_AGE)+")"
LOCAL="COALESCE(location_resolution->>'region','unknown')<>'outside' AND (distance_miles IS NULL OR distance_miles<=30)"
CURRENT=AVAILABLE+' AND '+LOCAL+' AND distance_miles IS NOT NULL'


def json_ready(value):
    if isinstance(value,dict):
        return {k:json_ready(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):
        return [json_ready(v) for v in value]
    if isinstance(value,(date,datetime)):
        return value.isoformat()
    if isinstance(value,Decimal):
        return float(value)
    return value


def number_arg(name,default,low,high,integer=False):
    try:
        value=float(request.args.get(name,default))
    except (ValueError,TypeError):
        raise ValueError(f'{name} must be a number.')
    if not math.isfinite(value) or not low<=value<=high or (integer and not value.is_integer()):
        raise ValueError(f'{name} must be between {low} and {high}.')
    return int(value) if integer else value


def create_app():
    app=Flask(__name__,static_folder=str(ROOT/'static'),static_url_path='/static')
    app.config.update(TRUSTED_HOSTS=['localhost','127.0.0.1'],MAX_CONTENT_LENGTH=1024)

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' https://unpkg.com; style-src 'self' 'unsafe-inline' https://unpkg.com; img-src 'self' data: https://*.tile.openstreetmap.org https://unpkg.com; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        if request.path.startswith('/api/'):
            response.headers['Cache-Control']='no-store'
        return response

    @app.get('/')
    def index():
        initial_data=None
        initial_meta=None
        try:
            initial_data=opportunities().get_json()
            initial_meta=metadata().get_json()
        except (psycopg.Error,ValueError):
            LOG.exception('Initial page data unavailable')
        labels={'internship':'Internship','job':'Other employment','program':'Career program',
            'unknown':'Not stated','full_time':'Full-time','part_time':'Part-time',
            'hybrid':'Hybrid','on_site':'On-site','remote':'Remote'}
        response=app.make_response(render_template_string((ROOT/'hub/templates/index.html').read_text(),
            initial_data=initial_data,initial_meta=initial_meta,labels=labels,safe_url=safe_url,
            search_text=request.args.get('q',''),
            asset_version=max(int((ROOT/name).stat().st_mtime_ns) for name in ('static/js/app.js','static/css/style.css'))))
        response.headers['Cache-Control']='no-store'
        return response

    @app.get('/index.html')
    def index_alias():
        return redirect('/'+('?' + request.query_string.decode('utf-8',errors='replace') if request.query_string else ''))

    @app.get('/<filename>')
    def asset(filename):
        aliases={'style.css':'css/style.css','app.js':'js/app.js','open-hub.js':'js/open-hub.js','favicon.svg':'images/favicon.svg'}
        if filename not in aliases:
            return jsonify(error='Not found'),404
        return send_from_directory(ROOT/'static',aliases[filename])

    @app.get('/vendor/leaflet/<path:filename>')
    def leaflet_asset(filename):
        return send_from_directory(ROOT / 'vendor/leaflet',filename)

    @app.errorhandler(psycopg.Error)
    def database_error(exc):
        LOG.exception('Database unavailable')
        return jsonify(error='The job database is temporarily unavailable. Please try again.'),503

    @app.errorhandler(ValueError)
    def validation_error(exc):
        return jsonify(error=str(exc)),400

    @app.get('/api/health')
    def health():
        with connect() as conn:
            conn.execute('SELECT 1 FROM internship_hub.categories LIMIT 1').fetchone()
        return jsonify(status='ok',database='connected')

    @app.get('/api/meta')
    def metadata():
        with connect() as conn:
            categories=conn.execute('SELECT id,label FROM internship_hub.categories ORDER BY sort_order,label').fetchall()
            counts=conn.execute(BASE_QUERY+f'''SELECT count(*) FILTER(WHERE {CURRENT}) AS current_count,
                count(*) FILTER(WHERE distance_miles IS NULL AND {AVAILABLE} AND {LOCAL}) AS review_count,
                count(*) FILTER(WHERE {AVAILABLE} AND {LOCAL}) AS total_count FROM evaluated''',BASE_PARAMS).fetchone()
            sources=conn.execute('''SELECT id,name,kind,enabled,interval_hours,last_attempt_at,last_success_at,last_status,last_message,
                CASE WHEN enabled AND last_attempt_at IS NOT NULL THEN last_attempt_at+make_interval(secs => interval_hours::double precision*3600) END AS next_refresh_at
                FROM internship_hub.sources WHERE kind<>'manual' ORDER BY name''').fetchall()
            runs=conn.execute('''SELECT s.name,r.status,r.updated,r.discovered,r.finished_at,r.message FROM internship_hub.collection_runs r
                JOIN internship_hub.sources s ON s.id=r.source_id WHERE s.kind<>'manual' ORDER BY r.id DESC LIMIT 8''').fetchall()
            collecting=conn.execute("SELECT EXISTS(SELECT 1 FROM internship_hub.collection_runs WHERE status='running' AND started_at>now()-interval '30 minutes') AS running").fetchone()['running']
        return jsonify(json_ready(dict(categories=categories,counts=counts,sources=sources,recent_runs=runs,campus=CAMPUS,
            refreshing=REFRESH_LOCK.locked() or collecting,automatic_refresh=AUTO_REFRESH,max_verification_age_days=MAX_AGE)))

    @app.get('/api/opportunities')
    def opportunities():
        radius=number_arg('radius',30,0,30)
        limit=number_arg('limit',200,1,500,True)
        offset=number_arg('offset',0,0,1000000,True)
        view=request.args.get('view','all')
        view={'current':'all','review':'unlocated'}.get(view,view)
        if view not in ('all','mapped','unlocated'):
            raise ValueError('Unknown view.')
        region=request.args.get('region','phoenix')
        if region not in ('phoenix','all'):
            raise ValueError('Unknown region.')
        conditions=[AVAILABLE]
        if region=='phoenix':
            conditions.extend(["COALESCE(location_resolution->>'region','unknown')<>'outside'",'(distance_miles IS NULL OR distance_miles<=%s)'])
        params=list(BASE_PARAMS)
        if region=='phoenix':
            params.append(radius)
        if view=='mapped':
            conditions.append('distance_miles IS NOT NULL')
        elif view=='unlocated':
            conditions.append('distance_miles IS NULL')
        enums={
            'type':('opportunity_type',{'internship','job','program','unknown'}),
            'commitment':('commitment',{'full_time','part_time','unknown'}),
            'arrangement':('arrangement',{'on_site','hybrid','remote','unknown'}),
            'stage':('career_stage',{'early','mid','senior','unknown'}),
            'paid':('paid_status',{'paid','unpaid','unknown'}),
        }
        for name,(column,allowed) in enums.items():
            values=request.args.getlist(name)
            if any(v not in allowed for v in values):
                raise ValueError(f'Unknown {name} filter.')
            if values:
                conditions.append(f'e.{column}=ANY(%s)')
                params.append(values)
        categories=request.args.getlist('category')
        if categories:
            conditions.append('category_id=ANY(%s)')
            params.append(categories)
        schedules=request.args.getlist('schedule')
        if schedules:
            conditions.append("EXISTS(SELECT 1 FROM jsonb_array_elements(schedule_tags) tag WHERE tag->>'id'=ANY(%s))")
            params.append(schedules)
        search=request.args.get('q','').strip()
        if len(search)>200:
            raise ValueError('Search must be 200 characters or fewer.')
        if search:
            escaped=search.replace('\\','\\\\').replace('%',r'\%').replace('_',r'\_')
            conditions.append("(title ILIKE %s OR company ILIKE %s OR description ILIKE %s)")
            params.extend(['%'+escaped+'%']*3)
        pay_period=request.args.get('pay_period')
        if pay_period:
            if pay_period not in ('hour','year'):
                raise ValueError('Unknown pay period.')
            conditions.append('salary_period=%s')
            params.append(pay_period)
        if 'min_pay' in request.args:
            minimum=number_arg('min_pay',0,0,10000000)
            if pay_period not in ('hour','year'):
                raise ValueError('Select an hourly or annual pay period.')
            conditions.append('salary_min>=%s')
            params.append(minimum)
        sort=request.args.get('sort','newest')
        sorts={'nearest':'distance_miles ASC NULLS LAST,title,id','newest':"COALESCE(posting_date::timestamp AT TIME ZONE 'America/Phoenix',first_found_at) DESC,title,id",'title':'title,id','pay':'salary_min DESC NULLS LAST,title,id'}
        if sort not in sorts:
            raise ValueError('Unknown sort order.')
        if sort=='pay':
            if pay_period not in ('hour','year'):
                raise ValueError('Select hourly or annual pay before sorting by pay.')
        query=BASE_QUERY+'SELECT e.*,count(*) OVER() AS result_total FROM evaluated e WHERE '+' AND '.join(conditions)+' ORDER BY '+sorts[sort]+' LIMIT %s OFFSET %s'
        with connect() as conn:
            items=conn.execute(query,params+[limit,offset]).fetchall()
            if items:
                total=items[0]['result_total']
            elif offset:
                total=conn.execute(BASE_QUERY+'SELECT count(*) AS n FROM evaluated e WHERE '+' AND '.join(conditions),params).fetchone()['n']
            else:
                total=0
        features=[]
        for item in items:
            item.pop('overrides',None)
            item.pop('result_total',None)
            archive_reasons=[]
            if item['source_missing']:
                archive_reasons.append('The saved source page is no longer available')
            if item['status']=='closed':
                archive_reasons.append('The listing was marked closed')
            if item['deadline_passed']:
                archive_reasons.append('The recorded application deadline has passed')
            item['archived']=bool(archive_reasons)
            item['archive_reason']='. '.join(archive_reasons)
            if region=='phoenix':
                item['worksites']=[w for w in item['worksites'] if w['distance_miles']<=radius+1e-9]
            item['location_note']=item['location_resolution'].get('note') or ('Job worksite confirmed.' if item['worksites'] else 'Exact location is unclear. Listing remains available.')
            item['location_basis']=item['location_resolution'].get('status') or ('worksite' if item['worksites'] else 'unclear')
            # The former review field is retained for old clients but no longer gates publication.
            item['review_reasons']=[]
            for worksite in item['worksites']:
                features.append({'type':'Feature','geometry':{'type':'Point','coordinates':[worksite['longitude'],worksite['latitude']]},'properties':{'opportunity_id':item['id'],'location_id':worksite['id'],'type':item['opportunity_type'],'basis':worksite['basis']}})
        return jsonify(json_ready({'items':items,'total':total,'offset':offset,'limit':limit,'has_more':offset+len(items)<total,
            'features':{'type':'FeatureCollection','features':features},'view':view,'radius':radius}))

    @app.post('/api/refresh')
    def refresh():
        origin=request.headers.get('Origin')
        if request.headers.get('X-Hub-Request')!='1' or (origin and origin!=request.host_url.rstrip('/')):
            return jsonify(error='Refresh must be requested from this page.'),403
        if REFRESH_LOCK.locked():
            return jsonify(status='running'),202
        threading.Thread(target=refresh_all,daemon=True,name='manual-refresh').start()
        return jsonify(status='started'),202

    return app
