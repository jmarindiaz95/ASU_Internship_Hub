"""Automatic location enrichment with cached, attributed address evidence."""
import json
import re
from datetime import date, datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb

from .config import CENSUS_GEOCODER_URL, COMPANY_LOOKUP_URL, ROOT
from .domain import safe_url, stable_id
from .pages import METRO_RE, PublicClient, SourceUnavailable, PageMissing, USER_AGENT

# A city alone never becomes a street-level worksite. Address ranges from Census
# are approximate; the UI keeps that precision separate from source attribution.
ADDRESS_RE=re.compile(r'\b(\d{1,6}\s+(?:(?:[NSEW]\.?|North|South|East|West)\s+)?[A-Za-z0-9 .\-]{2,65}?\b(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Drive|Dr\.?|Boulevard|Blvd\.?|Parkway|Pkwy\.?|Lane|Ln\.?|Way|Place|Pl\.?|Circle|Cir\.?))\s*(?:[\s,.-]+(?:Suite|Ste\.?|Building|Bldg\.?|Floor|#)\s*#?\s*[\w-]+)?[\s,.-]+(Phoenix|Tempe|Scottsdale|Chandler|Mesa|Gilbert|Glendale|Peoria|Avondale|Goodyear|Surprise|Tolleson|Buckeye|Queen Creek|Sun City|Paradise Valley|Fountain Hills)\s*[,\n ]+\s*(AZ|Arizona)\b\s*(\d{5})?',re.I)


def addresses(value):
    return [dict(address=m[1].strip(),city=m[2].title(),state='AZ',zip=m[4] or '') for m in ADDRESS_RE.finditer(value or '')]


def location_hint(metadata):
    nodes=metadata.get('job_location') or []
    if not isinstance(nodes,list):
        nodes=[nodes]
    hints=[metadata.get('location_text') or '']
    explicit=[]
    for node in nodes:
        if not isinstance(node,dict):
            continue
        a=node.get('address') or {}
        if isinstance(a,str):
            hints.append(a)
        else:
            hints.append(', '.join(str(a.get(k) or '') for k in ('addressLocality','addressRegion','addressCountry')))
            if a.get('streetAddress') and a.get('addressLocality'):
                explicit.append(dict(address=a['streetAddress'],city=a['addressLocality'],state=a.get('addressRegion') or '',zip=a.get('postalCode') or ''))
    return ' / '.join(h for h in hints if h),explicit


def region_for(hint):
    if not hint:
        return 'unknown'
    if METRO_RE.search(hint):
        # Mesa, CA or Glendale, CA must not be mistaken for Arizona.
        if re.search(r'\b(CA|California|Colorado|CO|Texas|TX)\b',hint,re.I) and not re.search(r'\b(AZ|Arizona)\b',hint,re.I):
            return 'outside'
        return 'phoenix'
    if re.search(r'\b(AZ|Arizona)\b',hint,re.I):
        return 'unknown'
    if re.search(r'\b(?:AL|AK|AR|CA|CO|CT|DC|DE|FL|GA|HI|IA|ID|IL|IN|KS|KY|LA|MA|MD|ME|MI|MN|MO|MS|MT|NC|ND|NE|NH|NJ|NM|NV|NY|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VA|VT|WA|WI|WV|WY|Tucson|Flagstaff|Yuma|Prescott|Columbus|Des Moines|New York|California|Ohio|Iowa)\b',hint):
        return 'outside'
    if re.search(r'\b(Canada|Toronto|Ontario|London|United Kingdom|Germany|India|Australia|China|Mexico|Brazil|Seattle|Chicago|Boston|Denver|San Francisco|Los Angeles|New Jersey|Texas|Florida|New Mexico|Nevada|Washington|Colorado|Oregon|Utah)\b',hint,re.I):
        return 'outside'
    return 'unknown'


def cached(conn,key,days,fetch):
    row=conn.execute('SELECT payload FROM internship_hub.enrichment_cache WHERE cache_key=%s AND expires_at>now()',(key,)).fetchone()
    if row:
        return row['payload']
    try:
        payload=fetch()
    except (requests.RequestException,ValueError,SourceUnavailable,PageMissing) as exc:
        payload={'error':str(exc)[:200]}
    ttl=days if payload and not payload.get('error') else min(days,1)
    conn.execute('''INSERT INTO internship_hub.enrichment_cache(cache_key,payload,expires_at) VALUES(%s,%s,now()+make_interval(days => %s))
        ON CONFLICT(cache_key) DO UPDATE SET payload=EXCLUDED.payload,checked_at=now(),expires_at=EXCLUDED.expires_at''',(key,Jsonb(payload),ttl))
    return payload


def geocode(conn,address):
    query=', '.join(str(address.get(k) or '') for k in ('address','city','state','zip'))
    def fetch():
        response=requests.get(CENSUS_GEOCODER_URL,params={'address':query,'benchmark':'Public_AR_Current','format':'json'},headers={'User-Agent':USER_AGENT},timeout=(5,20))
        response.raise_for_status()
        matches=response.json().get('result',{}).get('addressMatches',[])
        if len(matches)!=1:
            return {'error':'Address could not be uniquely geocoded.'}
        match=matches[0]
        return {'longitude':match['coordinates']['x'],'latitude':match['coordinates']['y'],
            'matched_address':match.get('matchedAddress'),'precision':'US Census address-range estimate'}
    return cached(conn,stable_id('census:',query.casefold()),90,fetch)


def normalize(value):
    return re.sub(r'[^a-z0-9]','',value.lower())


def official_offices(conn,company,metadata,client):
    directory=json.loads((ROOT/'config/company_addresses.json').read_text())
    entry=next((v for k,v in directory.items() if normalize(company['name']) in [normalize(n) for n in [k]+v.get('aliases',[])]),{})
    locations=[]
    for item in entry.get('locations',[]):
        # Seeded official-page evidence is timestamped, just like a cached fetch.
        # It expires after 30 days and then must be refreshed by the resolver.
        observed=item.get('observed_on')
        if observed and 0 <= (date.today()-date.fromisoformat(observed)).days < 30:
            locations.append(item)
            continue
        def evidence(item=item):
            body=client.get(item['evidence_url']).get_text(' ',strip=True)
            # Verify street number and city from the current official page.
            street_number=re.match(r'\d+',item['address'])[0]
            if not re.search(r'\b'+street_number+r'\b',body) or item['city'].casefold() not in body.casefold():
                return {'error':'Published office address changed or could not be confirmed.'}
            return {'confirmed':True}
        check=cached(conn,stable_id('office:',json.dumps(item,sort_keys=True)),30,evidence)
        if check.get('confirmed'):
            locations.append(item)
    if locations:
        return {'locations':locations}
    urls=list(entry.get('contact_urls',[]))
    site=metadata.get('company_website') or company.get('website_url')
    if isinstance(site,list):
        site=next((s for s in site if isinstance(s,str) and safe_url(s)),None)
    if isinstance(site,str) and safe_url(site) and urlparse(site).scheme=='https':
        urls.append(site)
    for url in list(dict.fromkeys(urls))[:3]:
        def read_contacts():
            soup=client.get(url)
            found=addresses(soup.get_text('\n',strip=True))
            if not found:
                contact=next((urljoin(url,a['href']) for a in soup.select('a[href]') if re.search(r'^contact( us)?$',a.get_text(' ',strip=True),re.I) and urlparse(urljoin(url,a['href'])).netloc==urlparse(url).netloc),None)
                if contact and contact!=url:
                    found=addresses(client.get(contact).get_text('\n',strip=True))
                else:
                    contact=url
            else:
                contact=url
            unique={normalize(a['address']+a['city']):a for a in found}
            return {'locations':[{**a,'basis':'company_office','evidence_url':contact,'note':'Published company office; job worksite is not confirmed.'} for a in unique.values()]}
        result=cached(conn,stable_id('contact:',url),30,read_contacts)
        if result.get('locations'):
            return result
    return {'locations':[]}


def company_lookup(conn,name):
    """Small, cached OpenStreetMap name lookup; ambiguous matches stay unplaced."""
    def fetch():
        outage=conn.execute("SELECT 1 FROM internship_hub.enrichment_cache WHERE cache_key='company-provider-unavailable' AND expires_at>now()").fetchone()
        if outage:
            return {'error':'Company-address lookup is temporarily unavailable.'}
        term=re.sub(r'\s+(?:LLC|Inc\.?|Co\.?)$','',name,flags=re.I).strip()
        escaped=re.escape(term).replace('\\','\\\\').replace('"','\\"')
        query=f'[out:json][timeout:12];nwr["name"~"^{escaped}( Headquarters)?$",i](33.1,-112.5,33.9,-111.4);out center tags;'
        try:
            response=requests.post(COMPANY_LOOKUP_URL,data={'data':query},headers={'User-Agent':USER_AGENT},timeout=(5,18))
            response.raise_for_status()
            data=response.json()
        except (requests.RequestException,ValueError):
            conn.execute("""INSERT INTO internship_hub.enrichment_cache(cache_key,payload,expires_at) VALUES('company-provider-unavailable','{}',now()+interval '1 hour')
                ON CONFLICT(cache_key) DO UPDATE SET expires_at=EXCLUDED.expires_at""")
            return {'error':'Company-address lookup is temporarily unavailable.'}
        matches=[]
        for item in data.get('elements',[]):
            tags=item.get('tags',{})
            point=item.get('center') or item
            if not all(tags.get(k) for k in ('addr:housenumber','addr:street','addr:city')):
                continue
            if not METRO_RE.search(tags['addr:city']):
                continue
            hq=tags.get('headquarters')=='yes' or tags.get('branch')=='headquarters' or bool(re.search(r'\bheadquarters\b',tags.get('name',''),re.I))
            matches.append(dict(address=tags['addr:housenumber']+' '+tags['addr:street'],city=tags['addr:city'],state='AZ',
                basis='company_hq' if hq else 'company_office',longitude=point.get('lon'),latitude=point.get('lat'),
                evidence_url=f"https://www.openstreetmap.org/{item['type']}/{item['id']}",
                note='Company headquarters identified in OpenStreetMap; job worksite is unconfirmed.' if hq else 'Company-name match in OpenStreetMap; neither headquarters nor job worksite is confirmed.'))
        unique={normalize(m['address']+m['city']):m for m in matches}
        headquarters=[m for m in unique.values() if m['basis']=='company_hq']
        if len(headquarters)==1:
            return {'locations':headquarters}
        if len(unique)!=1:
            return {'locations':[],'ambiguous':len(unique)>1}
        return {'locations':list(unique.values())}
    return cached(conn,stable_id('company-name:',normalize(name)),30,fetch)


def resolve_listing(conn,opportunity_id,company_id,body,metadata,url):
    company=conn.execute('SELECT * FROM internship_hub.companies WHERE id=%s',(company_id,)).fetchone()
    hint,explicit=location_hint(metadata)
    region=region_for(hint)
    result={'status':'unclear','region':region,'source_location':hint or None,'checked_at':datetime.now(timezone.utc).isoformat(),
        'note':'Exact job location and a clear Phoenix-area company address were not found. Listing remains available.'}
    candidates=[]
    if region=='outside':
        result.update(status='outside_area',note='Source states a location outside the Phoenix area. No Phoenix company address has been substituted.')
    else:
        # Job-specific address evidence takes priority over the employer reference.
        block=re.search(r'Job Location\s*:?(.*?)(?:Posting Details\s*:|Job Summary\s*:|$)',body,re.I|re.S)
        location_body=block[1][:1500] if block else body
        found=explicit or addresses(location_body)
        unique={normalize(a['address']+a['city']):a for a in found if METRO_RE.search(a['city']) and a.get('state','').upper() in ('AZ','ARIZONA')}
        if len(unique)==1:
            candidates=[{**next(iter(unique.values())),'basis':'worksite' if explicit or block else 'company_office','evidence_url':url,
                'note':'Street address stated in the job location section; map coordinates are approximate.' if explicit or block else 'Address published in the job description; company reference only, exact worksite unconfirmed.'}]
        existing=conn.execute("SELECT 1 FROM internship_hub.opportunity_locations WHERE opportunity_id=%s AND verified AND active AND basis='worksite'",(opportunity_id,)).fetchone()
        if not candidates and existing and not unique:
            result.update(status='worksite',region='phoenix',note='Job city and published employer address confirmed. Coordinates are approximate.')
        elif not candidates:
            offices=official_offices(conn,company,metadata,PublicClient())
            if not offices.get('locations'):
                offices=company_lookup(conn,company['name'])
            candidates=offices.get('locations',[])
            if offices.get('ambiguous'):
                result['note']='Several Phoenix-area company addresses matched; no clear headquarters or job worksite could be selected. Listing remains available.'
            elif offices.get('error'):
                result['note']='Job street address is not stated. '+offices['error']+' Listing remains available.'
            if hint and not candidates and result['status']=='unclear':
                result['status']='remote' if re.search(r'remote',hint,re.I) else 'city_only'
    mapped=[]
    for a in candidates:
        point=a if a.get('longitude') is not None else geocode(conn,a)
        if point.get('longitude') is None or point.get('latitude') is None:
            continue
        # Only Phoenix-region results can supply the fallback marker.
        if not (33.1<=float(point['latitude'])<=33.95 and -112.7<=float(point['longitude'])<=-111.3):
            continue
        location_id=stable_id('L',f"{company_id}:{float(point['latitude']):.6f}:{float(point['longitude']):.6f}")
        conn.execute('''INSERT INTO internship_hub.locations(id,company_id,address,city,state,geom,evidence_url)
            VALUES(%s,%s,%s,%s,%s,ST_SetSRID(ST_MakePoint(%s,%s),4326),%s) ON CONFLICT(id) DO UPDATE SET evidence_url=EXCLUDED.evidence_url''',
            (location_id,company_id,a['address'],a['city'],'AZ',point['longitude'],point['latitude'],a['evidence_url']))
        mapped.append((location_id,a))
    if mapped:
        conn.execute('UPDATE internship_hub.opportunity_locations SET active=false WHERE opportunity_id=%s',(opportunity_id,))
        for location_id,a in mapped:
            conn.execute('''INSERT INTO internship_hub.opportunity_locations(opportunity_id,location_id,verified,evidence_url,basis,active)
                VALUES(%s,%s,true,%s,%s,true) ON CONFLICT(opportunity_id,location_id) DO UPDATE SET verified=true,evidence_url=EXCLUDED.evidence_url,basis=EXCLUDED.basis,active=true''',
                (opportunity_id,location_id,a['evidence_url'],a['basis']))
        result.update(status=mapped[0][1]['basis'],region='phoenix',note=' '.join(dict.fromkeys(a['note'] for _,a in mapped))+' Coordinates are approximate.',precision='Approximate coordinates; Census address-range estimate or OpenStreetMap reference')
    elif region=='outside':
        conn.execute('UPDATE internship_hub.opportunity_locations SET active=false WHERE opportunity_id=%s',(opportunity_id,))
    elif candidates:
        conn.execute('UPDATE internship_hub.opportunity_locations SET active=false WHERE opportunity_id=%s',(opportunity_id,))
        result['note']='An address was found but could not be uniquely placed on the map. Listing remains available.'
    elif result['status']!='worksite':
        # An expired or failed company reference must not remain on the map just
        # because it was successfully resolved during an older refresh.
        conn.execute("UPDATE internship_hub.opportunity_locations SET active=false WHERE opportunity_id=%s AND basis<>'worksite'",(opportunity_id,))
    conn.execute('UPDATE internship_hub.opportunities SET location_resolution=%s WHERE id=%s',(Jsonb(result),opportunity_id))
    return result


def enrich_existing():
    from .db import connect
    with connect() as conn:
        rows=conn.execute('''SELECT DISTINCT ON(o.id) o.id,o.company_id,o.description,o.source_url,sl.raw_fields
            FROM internship_hub.opportunities o JOIN internship_hub.source_listings sl ON sl.opportunity_id=o.id
            WHERE sl.fetched_at IS NOT NULL AND o.status='open' ORDER BY o.id,sl.fetched_at DESC''').fetchall()
    counts={}
    for row in rows:
        with connect() as conn:
            result=resolve_listing(conn,row['id'],row['company_id'],row['description'],row['raw_fields'],row['source_url'])
        counts[result['status']]=counts.get(result['status'],0)+1
    return counts
