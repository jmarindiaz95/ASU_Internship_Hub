"""Integration tests run inside rolled-back transactions; collected records stay intact."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import json
import os
from pathlib import Path
import shutil
import subprocess

import psycopg
import pytest
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb

from hub import api, collectors, ingest, locations
from hub.db import connect
from hub.domain import classifications, schedule_evidence
from hub.ingest import upsert_opportunity


@pytest.fixture
def database(monkeypatch):
    conn=connect()
    with conn.transaction(force_rollback=True):
        @contextmanager
        def borrowed():
            yield conn
        monkeypatch.setattr(api,'connect',borrowed)
        monkeypatch.setattr(collectors,'connect',borrowed)
        monkeypatch.setattr(ingest,'connect',borrowed)
        monkeypatch.setattr(collectors,'COLLECTION_LOCK_KEY',662184020)
        monkeypatch.setattr(locations,'official_offices',lambda *args:{'locations':[]})
        monkeypatch.setattr(locations,'company_lookup',lambda *args:{'locations':[]})
        yield conn
    conn.close()


@pytest.fixture
def sample(database):
    token='TEST_ASU_'+uuid4().hex[:12]
    company=token+'_company'
    location=token+'_location'
    database.execute('INSERT INTO internship_hub.companies(id,name) VALUES(%s,%s)',(company,token))
    database.execute("INSERT INTO internship_hub.locations(id,company_id,address,city,geom) VALUES(%s,%s,'1 Test Way','Tempe',ST_SetSRID(ST_MakePoint(-111.9343818,33.4177504),4326))",(location,company))
    web_source=token+'_web'
    database.execute("INSERT INTO internship_hub.sources(id,name,kind,enabled) VALUES(%s,'Test web board','html',true)",(web_source,))
    ids=[]
    for suffix in ('one','two','closed','expired','unverified'):
        opportunity_id=token+'_'+suffix
        record={'id':opportunity_id,'company_id':company,'title':token+' '+suffix,'category_id':'accounting',
            'source_url':'https://example.com/'+suffix,'application_url':'https://example.com/'+suffix,
            'status':'closed' if suffix=='closed' else 'open',
            'review_state':'needs_review' if suffix=='unverified' else 'verified',
            'deadline':datetime.now(timezone.utc).date()+timedelta(days=-2 if suffix=='expired' else 3),
            'source_checked_at':datetime.now(timezone.utc),'opportunity_type':'internship',
            'commitment':'part_time' if suffix=='one' else 'unknown'}
        upsert_opportunity(database,record)
        database.execute('INSERT INTO internship_hub.opportunity_locations(opportunity_id,location_id,verified) VALUES(%s,%s,%s)',(opportunity_id,location,suffix!='unverified'))
        database.execute('INSERT INTO internship_hub.source_listings(source_id,external_id,opportunity_id,url,fetched_at) VALUES(%s,%s,%s,%s,now())',
            (web_source,opportunity_id,opportunity_id,record['source_url']))
        ids.append(opportunity_id)
    return {'token':token,'company':company,'location':location,'ids':ids,'web_source':web_source}


@pytest.fixture
def client(database):
    app=api.create_app()
    app.testing=True
    return app.test_client()


def test_schedule_rules_handle_negation_and_keep_evidence(database):
    rules=database.execute('SELECT * FROM internship_hub.schedule_rules WHERE enabled').fetchall()
    for phrase in ('No flexible hours.','We do not offer flexible scheduling.','Flexible spending account.','Must be flexible to work assigned shifts.','Hybrid and part-time.'):
        assert schedule_evidence(phrase,rules)==[]
    positive=schedule_evidence('No experience needed. Flexible schedule. Part-time around ASU coursework.',rules)
    assert {tag['id'] for tag in positive}=={'flexible_hours','coursework'}
    assert all(tag['evidence'] for tag in positive)


def test_internship_does_not_imply_hours_or_flexibility():
    result=classifications('Finance Intern','Hybrid internship. No hours stated.')
    assert result['opportunity_type']=='internship'
    assert result['commitment']=='unknown'
    assert result['arrangement']=='hybrid'


def test_current_map_list_agree_at_shared_worksite(client,sample):
    data=client.get('/api/opportunities',query_string={'q':sample['token'],'view':'mapped'}).get_json()
    expected=set(sample['ids'][:2])
    assert {row['id'] for row in data['items']}==expected
    assert {f['properties']['opportunity_id'] for f in data['features']['features']}==expected
    assert data['total']==2
    assert len({tuple(f['geometry']['coordinates']) for f in data['features']['features']})==1


def test_unresolved_jobs_appear_without_review_and_survive_radius_filter(client,sample):
    data=client.get('/api/opportunities',query_string={'q':sample['token'],'radius':0}).get_json()
    assert data['total']==3
    job=next(j for j in data['items'] if j['id']==sample['ids'][4])
    assert job['location_basis']=='unclear'
    assert job['location_note'] and job['review_reasons']==[]
    assert job['application_url']
    assert len(data['features']['features'])==2


def test_company_reference_is_labelled_in_both_cards_and_map(client,sample,database):
    database.execute("UPDATE internship_hub.opportunity_locations SET basis='company_hq' WHERE opportunity_id=%s",(sample['ids'][0],))
    database.execute('UPDATE internship_hub.opportunities SET location_resolution=%s WHERE id=%s',
        (Jsonb({'status':'company_hq','region':'phoenix','note':'Headquarters reference; worksite unconfirmed.'}),sample['ids'][0]))
    data=client.get('/api/opportunities',query_string={'q':sample['token']}).get_json()
    job=next(j for j in data['items'] if j['id']==sample['ids'][0])
    feature=next(f for f in data['features']['features'] if f['properties']['opportunity_id']==job['id'])
    assert job['location_basis']==feature['properties']['basis']=='company_hq'
    assert 'unconfirmed' in job['location_note']


def test_explicit_outside_location_never_receives_phoenix_headquarters(database,sample,monkeypatch):
    def unexpected(*args):
        raise AssertionError('A stated outside worksite must not trigger a Phoenix office lookup')
    monkeypatch.setattr(locations,'official_offices',unexpected)
    result=locations.resolve_listing(database,sample['ids'][0],sample['company'],'Finance job in Ohio',
        {'location_text':'Columbus, OH'},'https://example.com/one')
    assert result['status']=='outside_area'
    assert not database.execute('SELECT 1 FROM internship_hub.opportunity_locations WHERE opportunity_id=%s AND active',(sample['ids'][0],)).fetchone()


def test_address_range_lookup_is_cached_and_attached_as_job_worksite(database,sample,monkeypatch):
    calls=[]
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {'result':{'addressMatches':[{'coordinates':{'x':-111.987,'y':33.480},'matchedAddress':'2910 N 44TH ST, PHOENIX, AZ'}]}}
    monkeypatch.setattr(locations.requests,'get',lambda *args,**kwargs:(calls.append(kwargs) or Response()))
    body='Job Location: Address: 8877 N 44th Street\nPhoenix, Arizona 85018'
    for _ in range(2):
        result=locations.resolve_listing(database,sample['ids'][4],sample['company'],body,{'location_text':'PHOENIX'},'https://example.com/unverified')
    assert len(calls)==1
    assert result['status']=='worksite'
    assert 'approximate' in result['note']
    link=database.execute('SELECT basis,verified FROM internship_hub.opportunity_locations WHERE opportunity_id=%s AND active',(sample['ids'][4],)).fetchone()
    assert link=={'basis':'worksite','verified':True}


def test_ambiguous_company_lookup_keeps_job_visible_without_coordinates(database,sample,monkeypatch,client):
    monkeypatch.setattr(locations,'company_lookup',lambda *args:{'locations':[],'ambiguous':True})
    result=locations.resolve_listing(database,sample['ids'][4],sample['company'],'No street location supplied.',{},'https://example.com/unverified')
    assert result['status']=='unclear' and 'Several' in result['note']
    data=client.get('/api/opportunities',query_string={'q':sample['token'],'view':'unlocated'}).get_json()
    assert data['total']==1 and data['items'][0]['id']==sample['ids'][4]


def test_location_region_rejects_other_mesa_and_glendale():
    assert locations.region_for('Costa Mesa, California')=='outside'
    assert locations.region_for('Glendale, CA')=='outside'
    assert locations.region_for('Mesa, Arizona')=='phoenix'
    assert locations.region_for('Remote - Ohio')=='outside'
    assert locations.region_for('Remote - Arizona')=='unknown'


def test_sample_only_records_hidden_until_a_real_web_check(client,sample,database):
    record_id=sample['ids'][0]
    database.execute('UPDATE internship_hub.source_listings SET fetched_at=NULL WHERE opportunity_id=%s',(record_id,))
    manual_id=sample['token']+'_manual'
    database.execute("INSERT INTO internship_hub.sources(id,name,kind) VALUES(%s,'Proxy spreadsheet','manual')",(manual_id,))
    database.execute('INSERT INTO internship_hub.source_listings(source_id,external_id,opportunity_id,url,fetched_at) VALUES(%s,%s,%s,%s,now())',
        (manual_id,record_id,record_id,'https://example.com/one'))
    data=client.get('/api/opportunities',query_string={'q':sample['token']}).get_json()
    assert {r['id'] for r in data['items']}=={sample['ids'][1],sample['ids'][4]}
    assert all(s['kind']!='manual' for s in client.get('/api/meta').get_json()['sources'])
    database.execute('UPDATE internship_hub.source_listings SET fetched_at=now() WHERE opportunity_id=%s AND source_id=%s',(record_id,sample['web_source']))
    assert client.get('/api/opportunities',query_string={'q':sample['token']}).get_json()['total']==3


def test_stale_web_records_are_not_displayed(client,sample,database):
    database.execute('UPDATE internship_hub.opportunities SET source_checked_at=%s WHERE id=%s',
        (datetime.now(timezone.utc)-timedelta(days=api.MAX_AGE+1),sample['ids'][0]))
    for view in ('current','review'):
        records=client.get('/api/opportunities',query_string={'q':sample['token'],'view':view}).get_json()['items']
        assert sample['ids'][0] not in {r['id'] for r in records}


def test_source_schedule_reload_applies_interval_and_keeps_history(database,sample,tmp_path):
    config=tmp_path/'schedule.json'
    source_id=sample['token']+'_hourly'
    settings={'keywords':['finance'],'sources':[{'id':source_id,'name':'Hourly finance feed','kind':'rss',
        'index_url':'https://example.com/feed','interval_hours':24}]}
    config.write_text(json.dumps(settings))
    ingest.register_sources(config)
    checked=database.execute('UPDATE internship_hub.sources SET last_success_at=now() WHERE id=%s RETURNING last_success_at',(source_id,)).fetchone()['last_success_at']
    settings['sources'][0]['interval_hours']=1
    config.write_text(json.dumps(settings))
    ingest.register_sources(config)
    saved=database.execute('SELECT interval_hours,last_success_at FROM internship_hub.sources WHERE id=%s',(source_id,)).fetchone()
    assert saved['interval_hours']==1
    assert saved['last_success_at']==checked


def test_fresh_source_setup_creates_employer_without_workbook(database,sample,tmp_path):
    company_id=sample['token']+'_fresh_employer'
    source_id=sample['token']+'_fresh_source'
    config=tmp_path/'fresh.json'
    settings={'keywords':['finance'],'sources':[{'id':source_id,'name':'Fresh employer careers','kind':'employer',
        'index_url':'https://example.com/careers','config':{'company_id':company_id,'company_name':company_id,
            'company_website':'https://example.com'}}]}
    config.write_text(json.dumps(settings))
    ingest.register_sources(config)
    ingest.register_sources(config)
    company=database.execute('SELECT id,website_url FROM internship_hub.companies WHERE id=%s',(company_id,)).fetchone()
    assert company=={'id':company_id,'website_url':'https://example.com'}
    source=database.execute('SELECT * FROM internship_hub.sources WHERE id=%s',(source_id,)).fetchone()
    record={'title':'Finance intern','category_id':'corporate_finance','description':'An available finance internship with no street address.',
        'source_url':'https://example.com/jobs/'+source_id,'application_url':'https://example.com/jobs/'+source_id,
        'source_checked_at':datetime.now(timezone.utc),'status':'open'}
    job_id=collectors.save_listing(database,source,record['source_url'],record,'')
    assert database.execute('SELECT company_id FROM internship_hub.opportunities WHERE id=%s',(job_id,)).fetchone()['company_id']==company_id


def test_source_setup_reuses_existing_employer_name(database,sample,tmp_path):
    source_id=sample['token']+'_same_employer'
    config=tmp_path/'employer.json'
    config.write_text(json.dumps({'keywords':['finance'],'sources':[{'id':source_id,'name':'Existing employer','kind':'employer',
        'index_url':'https://example.com/careers','config':{'company_id':sample['token']+'_unused','company_name':sample['token']}}]}))
    ingest.register_sources(config)
    saved=database.execute('SELECT config FROM internship_hub.sources WHERE id=%s',(source_id,)).fetchone()['config']
    assert saved['company_id']==sample['company']


def test_first_found_date_comes_from_web_discovery(client,sample,database):
    database.execute("UPDATE internship_hub.opportunities SET imported_at=now()-interval '1 year' WHERE id=%s",(sample['ids'][0],))
    record=next(r for r in client.get('/api/opportunities',query_string={'q':sample['token']}).get_json()['items'] if r['id']==sample['ids'][0])
    assert datetime.fromisoformat(record['first_found_at'])>datetime.now(timezone.utc)-timedelta(minutes=5)


def test_page_includes_listings_without_javascript(client,sample):
    response=client.get('/',query_string={'q':sample['token']})
    assert response.status_code==200
    html=BeautifulSoup(response.data,'html.parser')
    assert len(html.select('#results > .job-card'))==3
    assert len(html.select('#category-options input'))>=5
    assert html.select_one('#initial-data')
    assert 'no-store' in response.headers['Cache-Control']
    assert '?v=' in html.select_one('script[src$="js?v=1.9.4"]').get('src')


def test_index_html_url_opens_the_app_and_preserves_search(client):
    response=client.get('/index.html?q=finance')
    assert response.status_code==302
    assert response.headers['Location']=='/?q=finance'


def test_page_survives_unavailable_database(client,monkeypatch):
    def unavailable():
        raise psycopg.OperationalError('unavailable')
    monkeypatch.setattr(api,'connect',unavailable)
    response=client.get('/')
    assert response.status_code==200
    assert b'Listings are temporarily unavailable' in response.data


def test_initial_html_escapes_source_text(client,sample,database):
    database.execute('UPDATE internship_hub.opportunities SET title=%s WHERE id=%s',('<img src=x onerror=alert(1)>',sample['ids'][0]))
    html=BeautifulSoup(client.get('/',query_string={'q':sample['token']}).data,'html.parser')
    assert not html.select('#results img')
    assert '<img src=x onerror=alert(1)>' in html.select_one('#results').get_text()


def test_offline_frontend_survives_failed_map(client,sample,tmp_path):
    node=os.environ.get('NODE_BINARY') or shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for the offline frontend startup test.')
    root=Path(__file__).resolve().parent.parent
    fixture=tmp_path/'startup.json'
    fixture.write_text(json.dumps({'html':client.get('/').get_data(as_text=True),
        'source':(root/'static/js/app.js').read_text(), 'meta':client.get('/api/meta').get_json(),
        'data':client.get('/api/opportunities',query_string={'q':sample['token']}).get_json()}))
    result=subprocess.run([node,str(root/'tests/frontend_startup.cjs'),str(fixture)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr


def test_review_records_are_not_mapped(client,sample):
    data=client.get('/api/opportunities',query_string={'q':sample['token'],'view':'review'}).get_json()
    assert {row['id'] for row in data['items']}=={sample['ids'][4]}
    assert data['features']['features']==[]
    assert all(row['location_note'] and not row['review_reasons'] for row in data['items'])


def test_expired_and_closed_records_are_hidden_from_public_views(client,sample):
    records={r['id']:r for r in client.get('/api/opportunities',query_string={'q':sample['token'],'view':'review'}).get_json()['items']}
    assert sample['ids'][2] not in records
    assert sample['ids'][3] not in records
    assert records[sample['ids'][4]]['archived'] is False
    html=BeautifulSoup(client.get('/',query_string={'q':sample['token'],'view':'review'}).data,'html.parser')
    assert {card['data-id'] for card in html.select('#results > .job-card')}=={sample['ids'][4]}


def test_missing_source_is_hidden_even_without_deadline(client,sample,database):
    database.execute("UPDATE internship_hub.opportunities SET review_note='Listing unavailable (HTTP 404); needs review.',deadline=NULL WHERE id=%s",(sample['ids'][4],))
    records=client.get('/api/opportunities',query_string={'q':sample['token'],'view':'review'}).get_json()['items']
    assert sample['ids'][4] not in {r['id'] for r in records}
    assert database.execute('SELECT 1 FROM internship_hub.opportunities WHERE id=%s',(sample['ids'][4],)).fetchone()


def test_missing_source_cannot_appear_current_with_verified_worksite(client,sample,database):
    database.execute("UPDATE internship_hub.opportunities SET review_note='Listing page unavailable; retained for review.' WHERE id=%s",(sample['ids'][0],))
    data=client.get('/api/opportunities',query_string={'q':sample['token']}).get_json()
    assert {r['id'] for r in data['items']}=={sample['ids'][1],sample['ids'][4]}


def test_expiration_is_checked_without_import(client,sample,database):
    database.execute("UPDATE internship_hub.opportunities SET deadline=current_date-2 WHERE id=%s",(sample['ids'][0],))
    data=client.get('/api/opportunities',query_string={'q':sample['token']}).get_json()
    assert {row['id'] for row in data['items']}=={sample['ids'][1],sample['ids'][4]}


def test_radius_uses_worksite_and_meters(client,sample,database):
    assert client.get('/api/opportunities',query_string={'q':sample['token'],'radius':0,'view':'mapped'}).get_json()['total']==2
    database.execute('UPDATE internship_hub.locations SET geom=ST_SetSRID(ST_MakePoint(-111.9343818,34.4177504),4326) WHERE id=%s',(sample['location'],))
    assert client.get('/api/opportunities',query_string={'q':sample['token'],'radius':30,'view':'mapped'}).get_json()['total']==0


def test_filters_combine_and_reset(client,sample):
    data=client.get('/api/opportunities',query_string={'q':sample['token'],'type':'internship','commitment':'part_time','category':'accounting'}).get_json()
    assert [r['id'] for r in data['items']]==[sample['ids'][0]]
    assert client.get('/api/opportunities',query_string={'q':sample['token'],'arrangement':'hybrid'}).get_json()['total']==0
    assert client.get('/api/opportunities',query_string={'q':sample['token']}).get_json()['total']==3


def test_pay_periods_are_not_mixed(client,sample,database):
    database.execute("UPDATE internship_hub.opportunities SET salary_min=25,salary_period='hour' WHERE id=%s",(sample['ids'][0],))
    database.execute("UPDATE internship_hub.opportunities SET salary_min=50000,salary_period='year' WHERE id=%s",(sample['ids'][1],))
    hourly=client.get('/api/opportunities',query_string={'q':sample['token'],'pay_period':'hour'}).get_json()
    assert [r['id'] for r in hourly['items']]==[sample['ids'][0]]
    assert client.get('/api/opportunities',query_string={'q':sample['token'],'min_pay':30,'pay_period':'hour'}).get_json()['total']==0


def test_pagination_keeps_total_beyond_last_page(client,sample):
    data=client.get('/api/opportunities',query_string={'q':sample['token'],'limit':1,'offset':20}).get_json()
    assert data['items']==[]
    assert data['total']==3


@pytest.mark.parametrize('query',['radius=nan','radius=inf','radius=-1','radius=31','limit=1.5','type=invalid','sort=invalid','min_pay=20','sort=pay','view=invalid'])
def test_invalid_filters_are_rejected(client,query):
    assert client.get('/api/opportunities?'+query).status_code==400


def test_literal_search_is_parameterized(client,sample):
    for text in ("'; DROP TABLE internship_hub.companies; --",'%','_'):
        response=client.get('/api/opportunities',query_string={'q':sample['token']+text})
        assert response.status_code==200
        if text=='%':
            assert response.get_json()['total']==0


def test_refresh_preserves_reviewer_overrides_and_snapshots(database,sample):
    source={'id':sample['token']+'_source','kind':'employer','config':{'company_id':sample['company']}}
    database.execute("INSERT INTO internship_hub.sources(id,name,kind) VALUES(%s,'Test source','employer')",(source['id'],))
    database.execute('UPDATE internship_hub.opportunities SET overrides=%s WHERE id=%s',(Jsonb({'title':'Reviewer correction','commitment':'part_time'}),sample['ids'][0]))
    record={'title':'Updated collected title','category_id':'accounting','description':'A complete current job description.','application_url':'https://example.com/one','source_url':'https://example.com/one','source_checked_at':datetime.now(timezone.utc),'status':'open'}
    for _ in range(2):
        collectors.save_listing(database,source,'https://example.com/one',dict(record),'')
    effective=database.execute('SELECT title,commitment FROM internship_hub.effective_opportunities WHERE id=%s',(sample['ids'][0],)).fetchone()
    assert effective=={'title':'Reviewer correction','commitment':'part_time'}
    assert database.execute('SELECT count(*) AS n FROM internship_hub.listing_snapshots WHERE source_id=%s',(source['id'],)).fetchone()['n']==1


def test_older_import_cannot_replace_current_source_data(database,sample):
    upsert_opportunity(database,{'id':sample['ids'][0],'company_id':sample['company'],'title':'Old imported title','category_id':'accounting','source_url':'https://example.com/one','application_url':'https://example.com/one','source_checked_at':datetime.now(timezone.utc)-timedelta(days=30)})
    assert database.execute('SELECT title FROM internship_hub.opportunities WHERE id=%s',(sample['ids'][0],)).fetchone()['title'].startswith(sample['token'])


def test_same_check_date_cannot_undo_a_missing_page_review(database,sample):
    original=database.execute('SELECT * FROM internship_hub.opportunities WHERE id=%s',(sample['ids'][0],)).fetchone()
    database.execute("UPDATE internship_hub.opportunities SET review_state='needs_review',review_note='Listing page missing' WHERE id=%s",(sample['ids'][0],))
    upsert_opportunity(database,{key:original[key] for key in ('id','company_id','title','category_id','source_url','application_url','source_checked_at','review_state')})
    assert database.execute('SELECT review_state FROM internship_hub.opportunities WHERE id=%s',(sample['ids'][0],)).fetchone()['review_state']=='needs_review'


def test_failed_source_never_closes_existing_jobs(monkeypatch,database,sample):
    source={'id':sample['token']+'_source','kind':'employer','index_url':'https://example.com/careers','config':{'company_id':sample['company'],'link_path':'/job/'}}
    database.execute("INSERT INTO internship_hub.sources(id,name,kind) VALUES(%s,'Test blocked source','employer')",(source['id'],))
    def unavailable(*args):
        raise collectors.SourceUnavailable('HTTP 403; source was not refreshed.')
    monkeypatch.setattr(collectors.PublicClient,'get',unavailable)
    result=collectors.collect_source(source)
    assert result['status']=='error'
    row=database.execute('SELECT status,review_state FROM internship_hub.opportunities WHERE id=%s',(sample['ids'][0],)).fetchone()
    assert row=={'status':'open','review_state':'verified'}


def test_empty_feed_records_successful_connection(monkeypatch,database,sample):
    from xml.etree import ElementTree
    source={'id':sample['token']+'_feed','name':'Empty public feed','kind':'rss','index_url':'https://example.com/feed',
        'config':{'keywords':['finance'],'link_path':'/jobs/'}}
    database.execute("INSERT INTO internship_hub.sources(id,name,kind) VALUES(%s,'Empty public feed','rss')",(source['id'],))
    monkeypatch.setattr(collectors.PublicClient,'feed',lambda *args:ElementTree.fromstring('<rss><channel/></rss>'))
    report=collectors.collect_source(source)
    saved=database.execute('SELECT last_status,last_success_at FROM internship_hub.sources WHERE id=%s',(source['id'],)).fetchone()
    assert report['status']=='ok' and report['updated']==0
    assert saved['last_status']=='ok' and saved['last_success_at'] is not None


def test_cli_collection_is_visible_in_source_status(client,database,sample):
    source_id=sample['token']+'_running'
    database.execute("INSERT INTO internship_hub.sources(id,name,kind) VALUES(%s,'Running source','rss')",(source_id,))
    database.execute('INSERT INTO internship_hub.collection_runs(source_id) VALUES(%s)',(source_id,))
    assert client.get('/api/meta').get_json()['refreshing'] is True


def test_explicit_manual_scan_can_check_disabled_source(monkeypatch,database,sample):
    source_id=sample['token']+'_disabled'
    database.execute("INSERT INTO internship_hub.sources(id,name,kind,enabled) VALUES(%s,'Manual-only source','rss',false)",(source_id,))
    monkeypatch.setattr(collectors,'collect_source',lambda source,**kwargs:{'source':source['id'],'status':'ok'})
    assert collectors.refresh_all(source_ids=[source_id])==[]
    assert collectors.refresh_all(source_ids=[source_id],include_disabled=True)==[{'source':source_id,'status':'ok'}]


def test_discovery_finds_new_jobs_and_ignores_other_hosts():
    soup=BeautifulSoup('<h1>Careers</h1><a href="/careers/job-new">New role</a><a href="https://other.example/careers/job-bad">Bad</a>','html.parser')
    source={'kind':'employer','index_url':'https://example.com/careers/','config':{'link_path':'/careers/job-'}}
    assert collectors.discover(source,soup)==['https://example.com/careers/job-new']


def test_database_error_is_recoverable(client,monkeypatch):
    def unavailable():
        raise psycopg.OperationalError('unavailable')
    monkeypatch.setattr(api,'connect',unavailable)
    response=client.get('/api/opportunities')
    assert response.status_code==503
    assert 'temporarily unavailable' in response.get_json()['error']


def test_private_files_and_cross_origin_refresh_are_blocked(client):
    for path in ('/.env','/.git/config','/server.py','/hub/config.py'):
        assert client.get(path).status_code==404
    assert client.post('/api/refresh').status_code==403
    assert client.post('/api/refresh',headers={'Origin':'https://example.com','X-Hub-Request':'1'}).status_code==403


@pytest.mark.parametrize('legacy,current',[
    ('/app.js','/static/js/app.js'),('/style.css','/static/css/style.css'),
    ('/favicon.svg','/static/images/favicon.svg'),('/open-hub.js','/static/js/open-hub.js')])
def test_organized_assets_and_legacy_links_remain_available(client,legacy,current):
    old=client.get(legacy)
    new=client.get(current)
    assert old.status_code==new.status_code==200
    assert old.data==new.data
