"""Read configured public pages or RSS feeds without a browser or an API account."""
import json
import html
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from .config import ROOT
from .domain import as_date, canonical_url, category, classifications, safe_url, schedule_evidence

USER_AGENT='ASUInternshipHub/0.2 (local academic job directory; public pages only)'
DEFAULT_KEYWORDS=['accounting','accountant','audit','finance','financial','tax','payroll','budget','banking','insurance','wealth','actuarial','treasury']


class SourceUnavailable(Exception):
    pass


class PageMissing(Exception):
    pass


def load_page_config(path=None):
    payload=json.loads(Path(path or ROOT/'config/known_pages.json').read_text())
    keywords=payload.get('keywords',DEFAULT_KEYWORDS)
    if not isinstance(keywords,list) or not keywords or any(not isinstance(k,str) or not k.strip() for k in keywords):
        raise ValueError('Configure a nonempty list of keyword strings.')
    seen=set()
    for source in payload.get('sources',[]):
        source_id=source.get('id')
        if not source_id or source_id in seen:
            raise ValueError('Source IDs must be present and unique.')
        if source.get('kind') not in ('rss','html','employer','single_page','asu','greenhouse'):
            raise ValueError(f'Unsupported source kind for {source_id}.')
        if not safe_url(source.get('index_url')) or urlparse(source['index_url']).scheme!='https':
            raise ValueError(f'{source_id} needs a public HTTPS index_url.')
        source.setdefault('config',{}).setdefault('keywords',list(keywords))
        source.setdefault('enabled',True)
        seen.add(source_id)
    if not seen:
        raise ValueError('Configure at least one known source.')
    return payload


def keyword_matches(value,keywords):
    return [keyword for keyword in keywords if re.search(r'(?<!\w)'+re.escape(keyword.strip())+r'(?!\w)',value,re.I)]


def keyword_evidence(body,keywords):
    evidence=[]
    for keyword in keywords:
        match=re.search(r'(?<!\w)'+re.escape(keyword)+r'(?!\w)',body,re.I)
        if match:
            evidence.append({'keyword':keyword,'text':body[max(0,match.start()-70):match.end()+160].strip()})
    return evidence


class PublicClient:
    def __init__(self):
        self.session=requests.Session()
        self.session.headers['User-Agent']=USER_AGENT
        self.robots={}
        self.last_requests={}

    def fetch(self,url):
        parsed=urlparse(url)
        if parsed.scheme!='https' or not parsed.netloc:
            raise SourceUnavailable('Only public HTTPS source pages are supported.')
        origin=f'https://{parsed.netloc}'
        if origin not in self.robots:
            response=self.session.get(origin+'/robots.txt',timeout=(5,20))
            if response.status_code>=400 and response.status_code not in (404,410):
                raise SourceUnavailable(f'robots.txt unavailable (HTTP {response.status_code}); check deferred.')
            robot=RobotFileParser()
            robot.parse(response.content.decode('utf-8-sig',errors='replace').splitlines() if response.ok else [])
            self.robots[origin]=robot
        robot=self.robots[origin]
        if not robot.can_fetch(USER_AGENT,url):
            raise SourceUnavailable('Automated reading is disallowed by this source.')
        delay=max(1,robot.crawl_delay(USER_AGENT) or 0)
        response=None
        target=url
        for _ in range(5):
            if urlparse(target).netloc!=parsed.netloc or urlparse(target).scheme!='https':
                raise SourceUnavailable('Source redirects to another host; configure that source separately.')
            if not robot.can_fetch(USER_AGENT,target):
                raise SourceUnavailable('The source disallows the redirect destination.')
            time.sleep(max(0,delay-(time.monotonic()-self.last_requests.get(origin,0))))
            response=self.session.get(target,timeout=(5,25),allow_redirects=False)
            self.last_requests[origin]=time.monotonic()
            if response.status_code not in (301,302,303,307,308):
                break
            target=urljoin(target,response.headers.get('Location',''))
        if response.status_code in (404,410):
            raise PageMissing(f'Listing page unavailable (HTTP {response.status_code}); hidden from current results.')
        if response.status_code!=200:
            raise SourceUnavailable(f'HTTP {response.status_code}; source was not refreshed.')
        if len(response.content)>5_000_000:
            raise SourceUnavailable('Source response is too large; narrow the configured page.')
        if re.search(r'[?&](jobnotfound|jobexpired)=(true|1)(?:&|$)',target,re.I):
            raise PageMissing('Listing page unavailable; the source reports that the job was not found.')
        return response

    def get(self,url):
        response=self.fetch(url)
        soup=BeautifulSoup(response.content,'html.parser')
        title=soup.title.get_text(' ',strip=True).lower() if soup.title else ''
        if any(phrase in title for phrase in ('just a moment','access denied','attention required')):
            raise SourceUnavailable('The source returned an access challenge; no attempt was made to bypass it.')
        if not soup.select_one('h1,h2,h3'):
            raise SourceUnavailable('The source returned an unreadable HTML page.')
        return soup

    def feed(self,url):
        response=self.fetch(url)
        try:
            return ElementTree.fromstring(response.content)
        except ElementTree.ParseError as exc:
            raise SourceUnavailable('The configured feed did not return valid RSS or Atom XML.') from exc

    def json(self,url):
        try:
            return self.fetch(url).json()
        except ValueError as exc:
            raise SourceUnavailable('The public board did not return valid JSON.') from exc


def main_text(soup,selector=None):
    content=soup.select_one(selector) if selector else soup.select_one('.job-description, .job_description, #job-details, .entry-content') or soup.select_one('main, article')
    if content is None:
        raise SourceUnavailable('The job content area was not found; source format may have changed.')
    content=BeautifulSoup(str(content),'html.parser')
    for node in content.select('form,script,style,nav,footer,header,.sharedaddy,.share-buttons'):
        node.decompose()
    return content.get_text('\n',strip=True)


def discover(source,soup):
    config=source['config']
    found={}
    index_path=urlparse(source['index_url']).path.rstrip('/')
    for anchor in soup.select(config.get('link_selector','a[href]')):
        url=canonical_url(urljoin(source['index_url'],anchor.get('href','')))
        if urlparse(url).netloc!=urlparse(source['index_url']).netloc:
            continue
        if config.get('link_path','/job/') not in urlparse(url).path:
            continue
        if urlparse(url).path.rstrip('/')==index_path:
            continue
        title=anchor.get_text(' ',strip=True)
        if title.lower().startswith(('share ','view more')):
            continue
        if config.get('metro_only'):
            row=anchor.find_parent('tr') or anchor.parent
            if not metro_location(row.get_text(' ',strip=True)):
                continue
        if config.get('job_scope')!='all' and (config.get('filter_keywords') or source['kind']=='asu') and not keyword_matches(title+' '+url,config.get('keywords',DEFAULT_KEYWORDS)):
            continue
        found[url]=title
    return list(found)[:config.get('max_discovered',40)]


def feed_links(source,root):
    config=source['config']
    found=[]
    if root.tag.split('}')[-1]=='rss':
        entries=root.findall('./channel/item')
        candidates=[(item.findtext('title') or '',item.findtext('link') or '',item.findtext('description') or '') for item in entries]
    elif root.tag=='{http://www.w3.org/2005/Atom}feed':
        ns={'a':'http://www.w3.org/2005/Atom'}
        candidates=[]
        for item in root.findall('a:entry',ns):
            link=next((n.get('href','') for n in item.findall('a:link',ns) if n.get('rel','alternate')=='alternate'),'')
            candidates.append((item.findtext('a:title','',ns),link,item.findtext('a:summary','',ns)))
    else:
        raise SourceUnavailable('Expected a public RSS or Atom feed, not an HTML or API response.')
    for title,url,description in candidates:
        url=canonical_url(urljoin(source['index_url'],url))
        if urlparse(url).netloc!=urlparse(source['index_url']).netloc or not safe_url(url):
            continue
        if config.get('link_path','/') not in urlparse(url).path:
            continue
        haystack=title+' '+url+((' '+BeautifulSoup(description,'html.parser').get_text(' ',strip=True)) if config.get('match_scope')=='body' else '')
        if config.get('job_scope')=='all' or keyword_matches(haystack,config.get('keywords',DEFAULT_KEYWORDS)):
            found.append(url)
    return list(dict.fromkeys(found))[:config.get('max_discovered',40)]


def job_schema(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data=json.loads(script.string or script.get_text())
        except (ValueError,TypeError):
            continue
        pending=data if isinstance(data,list) else [data]
        while pending:
            node=pending.pop()
            if not isinstance(node,dict):
                continue
            kinds=node.get('@type',[])
            if 'JobPosting' in (kinds if isinstance(kinds,list) else [kinds]):
                return node
            pending.extend(node.get('@graph',[]))
    return {}


def source_date(value):
    if not value:
        return None
    parsed=as_date(value)
    if parsed:
        return parsed
    for fmt in ('%B %d, %Y','%b %d %Y','%d %b %Y','%d %B %Y','%m/%d/%Y'):
        try:
            return datetime.strptime(str(value).strip(),fmt).date()
        except ValueError:
            pass
    try:
        return parsedate_to_datetime(value).date()
    except (ValueError,TypeError):
        return None


def parse_listing(soup,source,url,rules):
    config=source['config']
    structured=job_schema(soup)
    heading=soup.select_one(config.get('title_selector','h1'))
    title=config.get('title') or structured.get('title') or (heading.get_text(' ',strip=True) if heading else '')
    body=main_text(soup,config.get('body_selector'))
    if config.get('title') and config['title'].casefold() not in body.casefold():
        raise SourceUnavailable('The known position title is no longer present on its career page.')
    if not title or len(body)<80 or re.search(r'^(not found|page not found|access denied)$',title,re.I):
        raise SourceUnavailable('No complete job description was found; no opening was invented.')
    keywords=config.get('keywords',DEFAULT_KEYWORDS)
    matches=keyword_matches(title+' '+url+' '+body,keywords)
    if config.get('job_scope')!='all' and (config.get('filter_keywords') or source['kind'] in ('rss','asu','html')) and not matches:
        return None
    hint=title+' '+url
    category_id=category(hint)
    if category_id=='accounting' and re.search(r'finance|financial|budget|treasury',title,re.I) and not re.search(r'account|tax|audit',title,re.I):
        category_id='corporate_finance'
    result={'title':title,'description':body[:50000],'requirements':None,'category_id':category_id,'job_category':None,
        **classifications(title,body),'schedule_tags':schedule_evidence(body,rules),'source_url':url,'application_url':url,
        'status':'open','source_checked_at':datetime.now(timezone.utc)}
    employment=soup.select_one('#job-content .work-type')
    if employment:
        result.update(classifications(title,body,employment.get_text(' ',strip=True)))
    if re.search(r'(this (job|position|listing).{0,30}(closed|expired)|no longer accepting applications)',body,re.I):
        result['status']='closed'
    for key,field in [('datePosted','posting_date'),('validThrough','deadline')]:
        value=source_date(structured.get(key))
        if value:
            result[field]=value
    full_text=soup.get_text(' ',strip=True)
    for label,field in [('Expires on','deadline'),('Recruitment began on','posting_date')]:
        match=re.search(label+r'\s+([A-Za-z]+ \d{1,2}, \d{4})',full_text)
        if match and source_date(match[1]):
            result[field]=source_date(match[1])
    close=re.search(r'Applications close:\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})',full_text,re.I)
    if close:
        result['deadline']=source_date(close[1])
    salary=re.search(r'(?:salary|pay)(?:\s+range)?\s*:\s*\$([\d,.]+)(?:\s*[-–]\s*\$?([\d,.]+))?\s*(per\s+hour|hourly|/hr|per\s+year|annually|annual)',body,re.I)
    if salary:
        result.update(salary_min=float(salary[1].replace(',','')),salary_max=float((salary[2] or salary[1]).replace(',','')),
            salary_period='hour' if re.search(r'hour|/hr',salary[3],re.I) else 'year',paid_status='paid')
    for anchor in soup.select('a[href]'):
        if anchor.get_text(' ',strip=True).lower()=='apply now' and safe_url(urljoin(url,anchor['href'])):
            result['application_url']=urljoin(url,anchor['href'])
            break
    employer=config.get('company_name')
    organization=structured.get('hiringOrganization')
    if isinstance(organization,dict) and organization.get('name'):
        employer=organization['name']
    location=soup.select_one(config['location_selector']) if config.get('location_selector') else None
    result['_metadata']={'matched_keywords':matches,'keyword_evidence':keyword_evidence(body,matches),
        'employer_name':employer,'location_text':location.get_text(' ',strip=True) if location else None,
        'job_location':structured.get('jobLocation'),
        'company_website':organization.get('sameAs') if isinstance(organization,dict) else None}
    return result


METRO_RE=re.compile(r'\b(Phoenix|Tempe|Scottsdale|Chandler|Mesa|Gilbert|Glendale|Peoria|Avondale|Goodyear|Surprise|Tolleson|Buckeye|Queen Creek|Sun City|Paradise Valley|Litchfield Park|Fountain Hills|El Mirage)\b',re.I)


def metro_location(value):
    return bool(METRO_RE.search(value or '') or re.search(r'remote.*(Arizona|\bAZ\b)|(Arizona|\bAZ\b).*remote|REMOTE OPTIONS|^\s*(Arizona|AZ)(\s*[,\-]\s*(United States( of America)?|USA|US))?\s*$',value or '',re.I))


def greenhouse_record(job,source,rules):
    body=BeautifulSoup(html.unescape(html.unescape(job.get('content') or '')),'html.parser').get_text('\n',strip=True)
    title=job.get('title','')
    url=canonical_url(job['absolute_url'])
    if not title or len(body)<80:
        raise SourceUnavailable('The board returned an incomplete job description.')
    location=(job.get('location') or {}).get('name','')
    employment=' '.join(str(m.get('value') or '') for m in job.get('metadata') or [] if re.search(r'time|employment',m.get('name',''),re.I))
    record={'title':title,'description':body[:50000],'requirements':None,'category_id':category(title),
        'job_category':', '.join(d['name'] for d in job.get('departments',[])),
        **classifications(title,body,employment,location),'schedule_tags':schedule_evidence(body,rules),
        'source_url':url,'application_url':url,'status':'open','source_checked_at':datetime.now(timezone.utc),
        'posting_date':source_date(job.get('first_published')),'deadline':source_date(job.get('application_deadline')),
        '_metadata':{'provider_id':job['id'],'employer_name':source['config']['company_name'],'location_text':location,
            'job_location':None,'matched_keywords':keyword_matches(title,source['config'].get('keywords',DEFAULT_KEYWORDS))}}
    return record


def scan_greenhouse(source,rules,on_record):
    client=PublicClient()
    config=source['config']
    report={'source':source['id'],'name':source['name'],'connected':False,'discovered':0,'checked':0,'matched':0,
        'records':[],'missing_urls':[],'errors':[],'contact_text':'','all_live_urls':[],'index_complete':False}
    try:
        payload=client.json(source['index_url'])
        if not isinstance(payload.get('jobs'),list):
            raise SourceUnavailable('Public board jobs array is missing.')
        report.update(connected=True,index_complete=True,all_live_urls=[canonical_url(j['absolute_url']) for j in payload['jobs']])
        jobs=[j for j in payload['jobs'] if metro_location((j.get('location') or {}).get('name','')) and
            (re.search(r'\b(Arizona|AZ)\b',(j.get('location') or {}).get('name',''),re.I))]
        if config.get('job_scope')!='all':
            jobs=[j for j in jobs if keyword_matches(j.get('title',''),config.get('keywords',DEFAULT_KEYWORDS))]
        report['discovered']=len(jobs)
        # Newest postings first, with enough capacity for the entire local board.
        jobs.sort(key=lambda j:j.get('first_published') or j.get('updated_at') or '',reverse=True)
        for job in jobs[:config.get('max_checked',500)]:
            try:
                detail=client.json(source['index_url'].rstrip('/')+'/'+str(job['id']))
                report['checked']+=1
                record=greenhouse_record(detail,source,rules)
                if on_record:
                    on_record(record['source_url'],record,'')
                report['records'].append(record)
                report['matched']+=1
            except PageMissing:
                report['missing_urls'].append(canonical_url(job['absolute_url']))
            except (requests.RequestException,SourceUnavailable,ValueError) as exc:
                report['errors'].append(str(exc)[:200])
    except (requests.RequestException,SourceUnavailable,PageMissing,ValueError) as exc:
        report['errors'].append(str(exc)[:300])
    report['status']='ok' if report['connected'] and not report['errors'] else 'partial' if report['connected'] else 'error'
    report['message']=f"{report['discovered']} Phoenix-area roles found; {report['matched']} listings read across all career areas."
    if report['errors']:
        report['message']+=' '+report['errors'][0]
    return report


def scan_source(source,rules=(),known_urls=(),on_record=None):
    """Return an auditable report; an empty matching feed is a successful connection."""
    if source['kind']=='greenhouse':
        return scan_greenhouse(source,rules,on_record)
    report={'source':source['id'],'name':source.get('name',source['id']),'connected':False,'discovered':0,
        'checked':0,'matched':0,'records':[],'missing_urls':[],'errors':[],'contact_text':''}
    client=PublicClient()
    config=source['config']
    try:
        index=None
        if source['kind']=='rss':
            urls=feed_links(source,client.feed(source['index_url']))
        elif source['kind']=='single_page':
            index=client.get(source['index_url'])
            urls=[canonical_url(source['index_url'])]
        else:
            index=client.get(source['index_url'])
            urls=discover(source,index)
            if not urls and not re.search(r'no (current |open |matching )?(jobs|positions|opportunities)|no results',index.get_text(' ',strip=True),re.I):
                # Distinguish a correctly parsed board with no keyword matches from a changed layout.
                all_jobs=discover({**source,'kind':'employer','config':{**config,'filter_keywords':False}},index)
                if not all_jobs:
                    raise SourceUnavailable('No job links or explicit empty-results message found; check the configured selector.')
            visited={canonical_url(source['index_url'])}
            current_url=source['index_url']
            for page_number in range(2,config.get('max_pages',1)+1):
                anchor=index.select_one(config.get('next_selector','a[rel="next"], a.more-link, a.next'))
                link=urljoin(current_url,anchor['href']) if anchor and anchor.get('href') else next((urljoin(current_url,a['href']) for a in index.select('a[href]') if re.search(r'/page/'+str(page_number)+r'/?$',urlparse(a['href']).path)),None)
                if not link or canonical_url(link) in visited or urlparse(link).netloc!=urlparse(source['index_url']).netloc:
                    break
                visited.add(canonical_url(link))
                index=client.get(link)
                current_url=link
                urls.extend(discover(source,index))
        report['connected']=True
        urls=list(dict.fromkeys(urls))
        report['discovered']=len(urls)
        if config.get('recheck_known',True):
            urls=list(dict.fromkeys(urls+[canonical_url(url) for url in known_urls]))
        if config.get('contact_url'):
            try:
                report['contact_text']=main_text(client.get(config['contact_url']))
            except (requests.RequestException,SourceUnavailable,PageMissing) as exc:
                report['errors'].append(f'Office evidence unavailable: {exc}')
        for url in urls[:config.get('max_checked',60)]:
            try:
                soup=index if source['kind']=='single_page' and url==canonical_url(source['index_url']) else client.get(url)
                report['checked']+=1
                record=parse_listing(soup,source,url,rules)
                if record:
                    if on_record:
                        on_record(url,record,report['contact_text'])
                    report['records'].append(record)
                    report['matched']+=1
            except PageMissing:
                report['missing_urls'].append(url)
            except (requests.RequestException,SourceUnavailable,ValueError) as exc:
                report['errors'].append(f'{url}: {str(exc)[:220]}')
    except (requests.RequestException,SourceUnavailable,PageMissing,ValueError) as exc:
        report['errors'].append(str(exc)[:300])
    report['status']='ok' if report['connected'] and not report['errors'] else 'partial' if report['connected'] else 'error'
    report['message']=f"Connected. {report['discovered']} matching links found; {report['matched']} listings read."
    if not report['connected']:
        report['message']='Could not connect to the configured source.'
    elif not report['discovered']:
        report['message']='Connected. No current entries matched the configured keywords.'
    if report['missing_urls']:
        report['message']+=f" {len(report['missing_urls'])} old listing page(s) unavailable and hidden from current results."
    if report['errors']:
        report['message']+=' '+report['errors'][0]
    return report
