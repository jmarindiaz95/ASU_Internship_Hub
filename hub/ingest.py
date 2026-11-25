from datetime import datetime, time
from zoneinfo import ZoneInfo

import openpyxl
from psycopg import sql
from psycopg.types.json import Jsonb

from .db import connect
from .domain import as_date, canonical_url, category, classifications, schedule_evidence, stable_id, text
from .pages import load_page_config


def upsert_opportunity(conn, record):
    record = {k: Jsonb(v) if isinstance(v, (dict, list)) else v for k, v in record.items()}
    columns = list(record)
    assignments = sql.SQL(', ').join(sql.SQL('{}=EXCLUDED.{}').format(sql.Identifier(k), sql.Identifier(k)) for k in columns if k != 'id')
    conn.execute(sql.SQL('INSERT INTO internship_hub.opportunities ({}) VALUES ({}) ON CONFLICT(id) DO UPDATE SET {}, updated_at=now() WHERE internship_hub.opportunities.source_checked_at IS NULL OR EXCLUDED.source_checked_at > internship_hub.opportunities.source_checked_at').format(
        sql.SQL(',').join(map(sql.Identifier, columns)), sql.SQL(',').join(sql.Placeholder() for _ in columns), assignments), list(record.values()))


def add_location(conn, company_id, address, city, state, latitude, longitude, evidence_url):
    if latitude is None or longitude is None:
        return None
    lat, lon = float(latitude), float(longitude)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    location_id = stable_id('L', f'{company_id}:{lat:.6f}:{lon:.6f}')
    conn.execute('''INSERT INTO internship_hub.locations(id,company_id,address,city,state,geom,evidence_url)
        VALUES(%s,%s,%s,%s,%s,ST_SetSRID(ST_MakePoint(%s,%s),4326),%s) ON CONFLICT(id) DO NOTHING''',
        (location_id,company_id,address,city,state,lon,lat,evidence_url))
    return location_id


def rows(sheet):
    header = [c.value for c in sheet[5]]
    return [dict(zip(header,row)) for row in sheet.iter_rows(min_row=6,values_only=True) if row[0]]


def import_workbook(path):
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    companies, opportunities = rows(workbook['Companies']), rows(workbook['Opportunities'])
    with connect() as conn:
        conn.execute("INSERT INTO internship_hub.sources(id,name,kind,last_status,last_message) VALUES('workbook','Original research workbook','manual','imported','Historical research. Importing does not refresh source-check dates.') ON CONFLICT(id) DO NOTHING")
        rules = conn.execute('SELECT * FROM internship_hub.schedule_rules WHERE enabled').fetchall()
        for row in companies:
            conn.execute('''INSERT INTO internship_hub.companies(id,name,industry,website_url,careers_url)
                VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING''',
                tuple(row.get(k) for k in ('Company_ID','Company_Name','Industry','Website_URL','Careers_URL')))
            add_location(conn,row['Company_ID'],row.get('Street_Address'),row.get('City'),row.get('State'),row.get('Latitude'),row.get('Longitude'),row.get('Address_Source_URL'))
        for row in opportunities:
            description = '\n'.join(text(row.get(k)) for k in ('Experience_Description','Program_Term','Notes') if text(row.get(k)))
            requirements = '\n'.join(label+': '+text(row.get(key)) for label,key in (
                ('Experience','Experience_Description'),('Degree requirement','Degree_Required'),
                ('Degree field','Degree_Field'),('CPA requirement','CPA_Required'),
                ('Other certifications','Other_Certification'),('Visa sponsorship','Visa_Sponsorship')) if text(row.get(key)))
            checked = as_date(row.get('Date_Checked'))
            record = {
                'id':row['Opportunity_ID'], 'company_id':row['Company_ID'], 'title':row['Job_Title'],
                'category_id':category(row['Job_Category']), 'job_category':row['Job_Category'],
                **classifications(row['Job_Title'],text(row.get('Program_Term')),text(row.get('Employment_Type')),text(row.get('Work_Arrangement')),text(row.get('Career_Stage'))),
                'description':description, 'requirements':requirements,
                'schedule_tags':schedule_evidence(description,rules), 'hours_per_week':row.get('Hours_Per_Week'),
                'program_term':text(row.get('Program_Term')),
                'paid_status':{'Yes':'paid','No':'unpaid'}.get(row.get('Paid_Status'),'unknown'),
                'salary_min':row.get('Salary_Min'), 'salary_max':row.get('Salary_Max'),
                'salary_period':{'Hourly':'hour','Annual':'year'}.get(row.get('Salary_Period')),
                'application_url':row['Application_URL'], 'application_method':row.get('Application_Method'),
                'application_email':row.get('Application_Email'), 'source_url':canonical_url(row['Source_URL']),
                'posting_date':as_date(row.get('Posting_Date')), 'deadline':as_date(row.get('Application_Deadline')),
                'status':'open' if row['Posting_Status'].startswith('Open on') else 'closed' if row['Posting_Status']=='Closed' else 'program' if row['Posting_Status'].startswith('Employer program') else 'unknown',
                'review_state':'verified' if row['Map_Eligibility']=='Map verified worksite' else 'needs_review',
                'review_note':row['Map_Eligibility'],
                'source_checked_at':datetime.combine(checked,time(),ZoneInfo('America/Phoenix')) if checked else None,
            }
            upsert_opportunity(conn,record)
            # Enrich original research requirements without undoing a later missing-page review.
            conn.execute('''UPDATE internship_hub.opportunities o SET requirements=%s WHERE id=%s AND source_checked_at=%s
                AND NOT EXISTS(SELECT 1 FROM internship_hub.source_listings sl WHERE sl.opportunity_id=o.id AND sl.fetched_at IS NOT NULL)''',
                (requirements,row['Opportunity_ID'],record['source_checked_at']))
            location_id = add_location(conn,row['Company_ID'],row.get('Street_Address'),row.get('Office_City'),row.get('State'),row.get('Latitude'),row.get('Longitude'),row['Source_URL'])
            if location_id:
                conn.execute('''INSERT INTO internship_hub.opportunity_locations(opportunity_id,location_id,verified,evidence_url)
                    VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                    (row['Opportunity_ID'],location_id,record['review_state']=='verified',row['Source_URL']))
            conn.execute('''INSERT INTO internship_hub.source_listings(source_id,external_id,opportunity_id,url,raw_fields)
                VALUES('workbook',%s,%s,%s,%s) ON CONFLICT(source_id,external_id) DO NOTHING''',
                (row['Opportunity_ID'],row['Opportunity_ID'],row['Source_URL'],Jsonb({k:str(v) if v is not None else None for k,v in row.items()})))
    workbook.close()
    return {'companies':len(companies),'opportunities':len(opportunities)}


def register_sources(config_path=None):
    sources=load_page_config(config_path)['sources']
    with connect() as conn:
        for source in sources:
            config=source['config']
            if config.get('company_id') and config.get('company_name'):
                company=conn.execute('''SELECT id FROM internship_hub.companies WHERE id=%s OR lower(name)=lower(%s)
                    ORDER BY (id=%s) DESC LIMIT 1''',(config['company_id'],config['company_name'],config['company_id'])).fetchone()
                if company:
                    config['company_id']=company['id']
                else:
                    conn.execute('''INSERT INTO internship_hub.companies(id,name,website_url,careers_url) VALUES(%s,%s,%s,%s)''',
                        (config['company_id'],config['company_name'],config.get('company_website'),source['index_url']))
            conn.execute('''INSERT INTO internship_hub.sources(id,name,kind,index_url,enabled,config,interval_hours)
                VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET name=EXCLUDED.name,kind=EXCLUDED.kind,
                index_url=EXCLUDED.index_url,enabled=EXCLUDED.enabled,config=EXCLUDED.config,interval_hours=EXCLUDED.interval_hours''',
                (source['id'],source['name'],source['kind'],source['index_url'],source['enabled'],Jsonb(source['config']),source.get('interval_hours',24)))
    return len(sources)
