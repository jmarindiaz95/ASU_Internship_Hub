"""Persist public-page scans and schedule source refreshes."""
import hashlib
import logging
import re
import threading
import time

from psycopg.types.json import Jsonb

from .config import AUTO_REFRESH, REFRESH_POLL_SECONDS
from .db import connect
from .domain import canonical_url, stable_id
from .ingest import upsert_opportunity
from .locations import resolve_listing
from .pages import PageMissing, PublicClient, SourceUnavailable, discover, main_text, parse_listing, scan_source

LOG=logging.getLogger(__name__)
REFRESH_LOCK=threading.Lock()
COLLECTION_LOCK_KEY=662184019


def verify_single_office(conn,opportunity_id,company_id,body,contact_text,url):
    """Require a job-specific city statement and current matching office evidence."""
    locations=conn.execute('SELECT * FROM internship_hub.locations WHERE company_id=%s',(company_id,)).fetchall()
    if len(locations)!=1 or not contact_text:
        return False
    location=locations[0]
    compact=lambda value:re.sub(r'[^a-z0-9]','',value.lower())
    address=compact(location['address'] or '')
    city=location['city'] or ''
    if not address or address not in compact(contact_text) or not re.search(r'Location:\s*'+re.escape(city)+r'\b',body,re.I):
        return False
    conn.execute('''INSERT INTO internship_hub.opportunity_locations(opportunity_id,location_id,verified,evidence_url)
        VALUES(%s,%s,true,%s) ON CONFLICT(opportunity_id,location_id) DO UPDATE SET verified=true,evidence_url=EXCLUDED.evidence_url''',
        (opportunity_id,location['id'],url))
    return True


def save_listing(conn,source,url,record,contact_text):
    record=dict(record)
    metadata=record.pop('_metadata',{})
    known=conn.execute('''SELECT o.id,o.company_id FROM internship_hub.source_listings sl
        JOIN internship_hub.opportunities o ON o.id=sl.opportunity_id WHERE sl.source_id=%s AND sl.external_id=%s''',
        (source['id'],url)).fetchone()
    existing=conn.execute('SELECT id,company_id,title FROM internship_hub.opportunities WHERE source_url=%s',(url,)).fetchall()
    matching=[r for r in existing if r['title'].casefold()==record['title'].casefold()]
    prior=known or (existing[0] if len(existing)==1 else matching[0] if len(matching)==1 else None)
    if prior:
        opportunity_id,company_id=prior['id'],prior['company_id']
    else:
        company_id=source['config'].get('company_id')
        if not company_id:
            employer=metadata.get('employer_name') or source['config'].get('company_name')
            if not employer:
                employer='Employer not stated · '+record['title'][:120]+' · '+stable_id('',url)[-6:]
            existing_company=conn.execute('SELECT id FROM internship_hub.companies WHERE lower(name)=lower(%s)',(employer,)).fetchone()
            company_id=existing_company['id'] if existing_company else stable_id('C',employer.casefold())
            conn.execute('INSERT INTO internship_hub.companies(id,name) VALUES(%s,%s) ON CONFLICT DO NOTHING',(company_id,employer))
        opportunity_id=stable_id('J',url)
    exists=conn.execute('SELECT 1 FROM internship_hub.opportunities WHERE id=%s',(opportunity_id,)).fetchone()
    verified=verify_single_office(conn,opportunity_id,company_id,record['description'],contact_text,url) if exists else False
    has_verified=conn.execute('SELECT 1 FROM internship_hub.opportunity_locations WHERE opportunity_id=%s AND verified',(opportunity_id,)).fetchone()
    record.update(id=opportunity_id,company_id=company_id,review_state='verified' if (verified or has_verified) else 'needs_review',
        review_note='Source checked; location resolved automatically where possible.')
    upsert_opportunity(conn,record)
    if not has_verified and verify_single_office(conn,opportunity_id,company_id,record['description'],contact_text,url):
        conn.execute("UPDATE internship_hub.opportunities SET review_state='verified',review_note='Job city and employer office address confirmed.' WHERE id=%s",(opportunity_id,))
    resolve_listing(conn,opportunity_id,company_id,record['description'],metadata,url)
    digest=hashlib.sha256(record['description'].encode()).hexdigest()
    raw_fields={'title':record['title'],'application_url':record['application_url'],**metadata}
    conn.execute('''INSERT INTO internship_hub.source_listings(source_id,external_id,opportunity_id,url,fetched_at,content_hash,extracted_text,raw_fields)
        VALUES(%s,%s,%s,%s,now(),%s,%s,%s) ON CONFLICT(source_id,external_id) DO UPDATE SET last_seen_at=now(),fetched_at=now(),content_hash=EXCLUDED.content_hash,extracted_text=EXCLUDED.extracted_text,raw_fields=EXCLUDED.raw_fields''',
        (source['id'],url,opportunity_id,url,digest,record['description'],Jsonb(raw_fields)))
    conn.execute('''INSERT INTO internship_hub.listing_snapshots(source_id,external_id,content_hash,extracted_text)
        VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING''',(source['id'],url,digest,record['description']))
    return opportunity_id


def collect_source(source,include_records=False):
    with connect() as conn:
        run_id=conn.execute('INSERT INTO internship_hub.collection_runs(source_id) VALUES(%s) RETURNING id',(source['id'],)).fetchone()['id']
        conn.execute('UPDATE internship_hub.sources SET last_attempt_at=now() WHERE id=%s',(source['id'],))
        rules=conn.execute('SELECT * FROM internship_hub.schedule_rules WHERE enabled').fetchall()
        known=conn.execute('''SELECT DISTINCT sl.url FROM internship_hub.source_listings sl
            JOIN internship_hub.opportunities o ON o.id=sl.opportunity_id WHERE sl.source_id=%s
            AND COALESCE(o.review_note,'') NOT LIKE 'Listing page unavailable%%' ''',(source['id'],)).fetchall()
    def persist(url,record,contact_text):
        with connect() as conn:
            save_listing(conn,source,url,record,contact_text)
            conn.execute('UPDATE internship_hub.collection_runs SET updated=updated+1 WHERE id=%s',(run_id,))
    report=scan_source(source,rules=rules,known_urls=[r['url'] for r in known],on_record=persist)
    with connect() as conn:
        if source['kind']=='greenhouse' and report.get('index_complete'):
            conn.execute('''UPDATE internship_hub.opportunities o SET status='closed',updated_at=now()
                FROM internship_hub.source_listings sl WHERE sl.opportunity_id=o.id AND sl.source_id=%s
                AND NOT(sl.url=ANY(%s::text[]))''',(source['id'],report['all_live_urls']))
        for url in report['missing_urls']:
            conn.execute("UPDATE internship_hub.opportunities SET review_state='needs_review',review_note='Listing page unavailable; hidden from current results.',updated_at=now() WHERE source_url=%s",(canonical_url(url),))
        conn.execute('''UPDATE internship_hub.collection_runs SET finished_at=now(),status=%s,discovered=%s,updated=%s,message=%s WHERE id=%s''',
            (report['status'],report['discovered'],report['matched'],report['message'],run_id))
        conn.execute('''UPDATE internship_hub.sources SET last_status=%s,last_message=%s,
            last_success_at=CASE WHEN %s THEN now() ELSE last_success_at END WHERE id=%s''',
            (report['status'],report['message'],report['connected'],source['id']))
    report['updated']=report['matched']
    report.pop('contact_text',None)
    report.pop('all_live_urls',None)
    if not include_records:
        report.pop('records',None)
    return report


def refresh_all(due_only=False,source_ids=None,include_records=False,include_disabled=False):
    if not REFRESH_LOCK.acquire(blocking=False):
        return []
    try:
        with connect() as conn:
            if not conn.execute('SELECT pg_try_advisory_lock(%s) AS locked',(COLLECTION_LOCK_KEY,)).fetchone()['locked']:
                return []
            sources=conn.execute('''SELECT * FROM internship_hub.sources WHERE (enabled OR %s) AND
                (%s=false OR last_attempt_at IS NULL OR last_attempt_at < now()-make_interval(secs => interval_hours::double precision*3600))
                AND (%s::text[] IS NULL OR id=ANY(%s::text[])) ORDER BY id''',(include_disabled,due_only,source_ids,source_ids)).fetchall()
            results=[]
            for source in sources:
                try:
                    results.append(collect_source(source,include_records=include_records))
                except Exception:
                    LOG.exception('Collector failed: %s',source['id'])
                    with connect() as error_conn:
                        error_conn.execute("UPDATE internship_hub.sources SET last_status='error',last_message='Unexpected refresh error. See server log.',last_attempt_at=now() WHERE id=%s",(source['id'],))
                        error_conn.execute("UPDATE internship_hub.collection_runs SET status='error',finished_at=now(),message='Unexpected collector failure.' WHERE source_id=%s AND status='running'",(source['id'],))
                    results.append({'source':source['id'],'status':'error'})
            conn.execute('SELECT pg_advisory_unlock(%s)',(COLLECTION_LOCK_KEY,))
            return results
    finally:
        REFRESH_LOCK.release()


def start_scheduler():
    if not AUTO_REFRESH:
        return
    def loop():
        while True:
            try:
                refresh_all(due_only=True)
            except Exception:
                LOG.exception('Scheduled refresh failed')
            time.sleep(REFRESH_POLL_SECONDS)
    threading.Thread(target=loop,daemon=True,name='job-refresh').start()
