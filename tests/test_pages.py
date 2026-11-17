from xml.etree import ElementTree
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from hub import pages


def source(kind='html'):
    return {'id':'known','name':'Known public board','kind':kind,'index_url':'https://example.com/careers',
        'config':{'keywords':['accounting','finance','insurance'],'link_path':'/jobs/','filter_keywords':True}}


def page(title='Finance Intern'):
    return BeautifulSoup(f'''<main><h1>{title}</h1><p>Location: Phoenix, Arizona.</p>
        <p>This finance internship supports accounting research and public service.
        The team accepts students enrolled in an accounting degree. Flexible hours support coursework.</p></main>''','html.parser')


def test_keyword_matching_uses_words_and_records_context():
    assert pages.keyword_matches('Accountability is important; ACCOUNTING intern',['account','accounting'])==['accounting']
    evidence=pages.keyword_evidence('We are hiring an accounting intern for the finance team.',['accounting','finance'])
    assert {e['keyword'] for e in evidence}=={'accounting','finance'}
    assert all('intern' in e['text'] for e in evidence)


def test_rss_filters_keywords_and_rejects_other_hosts():
    root=ElementTree.fromstring('''<rss><channel>
        <item><title>Finance Intern</title><link>https://example.com/jobs/finance-intern</link></item>
        <item><title>Medical Intern</title><link>https://example.com/jobs/medical-intern</link></item>
        <item><title>Insurance Analyst</title><link>https://other.example/jobs/insurance</link></item>
        <item><title>Product Analyst</title><link>https://example.com/jobs/insurance-company-product-analyst</link></item>
        </channel></rss>''')
    assert pages.feed_links(source('rss'),root)==['https://example.com/jobs/finance-intern','https://example.com/jobs/insurance-company-product-analyst']


def test_empty_matching_feed_is_connected_not_error(monkeypatch):
    root=ElementTree.fromstring('<rss><channel><item><title>Medical Intern</title><link>https://example.com/jobs/medical</link></item></channel></rss>')
    monkeypatch.setattr(pages.PublicClient,'feed',lambda *args:root)
    report=pages.scan_source(source('rss'))
    assert report['connected'] and report['status']=='ok'
    assert report['matched']==0
    assert report['errors']==[]
    assert 'No current entries matched' in report['message']


def test_html_instead_of_feed_is_an_actual_failure(monkeypatch):
    monkeypatch.setattr(pages.PublicClient,'feed',lambda *args:ElementTree.fromstring('<html><body>Sign in</body></html>'))
    report=pages.scan_source(source('rss'))
    assert report['status']=='error'
    assert not report['connected']
    assert 'Expected a public RSS' in report['message']


def test_atom_feed_supported():
    root=ElementTree.fromstring('''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
        <title>Accounting Intern</title><link href="https://example.com/jobs/accounting-intern"/>
        </entry></feed>''')
    assert pages.feed_links(source('rss'),root)==['https://example.com/jobs/accounting-intern']


def test_pageup_selectors_avoid_agency_heading_and_extract_salary():
    item=source()
    item['config'].update(title_selector='#job-content > h2',body_selector='#job-details',location_selector='.location')
    soup=BeautifulSoup('''<div id="job-content"><h2>Accounting Compliance Intern</h2><span class="location">PHOENIX</span>
        <span class="work-type">Internship</span><div id="job-details"><h1>AUDITOR GENERAL</h1>
        <p>Review school district accounting audits and prepare entity guidance with a team of professional financial auditors.</p>
        <p>Salary: $20.00 per hour</p><p>Flexible hours, 20-40 hours per week, to meet your school schedule.</p></div></div>''','html.parser')
    record=pages.parse_listing(soup,item,'https://example.com/jobs/intern',[])
    assert record['title']=='Accounting Compliance Intern'
    assert record['opportunity_type']=='internship'
    assert record['salary_min']==record['salary_max']==20
    assert record['salary_period']=='hour'
    assert record['commitment']=='unknown'
    assert record['_metadata']['location_text']=='PHOENIX'


def test_single_page_needs_evidence_of_configured_title():
    item=source('single_page')
    item['config']['title']='Tax & Accounting Client Manager'
    with pytest.raises(pages.SourceUnavailable,match='no longer present'):
        pages.parse_listing(page(),item,item['index_url'],[])


def test_structured_employer_deadline_are_preserved():
    soup=page()
    script=soup.new_tag('script',type='application/ld+json')
    script.string='{"@context":"https://schema.org","@type":"JobPosting","title":"Finance Intern","hiringOrganization":{"name":"Example Finance"},"validThrough":"2027-02-01T23:59:00-07:00"}'
    soup.append(script)
    record=pages.parse_listing(soup,source(),'https://example.com/jobs/finance-intern',[])
    assert record['_metadata']['employer_name']=='Example Finance'
    assert str(record['deadline'])=='2027-02-01'


def test_old_missing_listing_does_not_mean_source_disconnected(monkeypatch):
    index=BeautifulSoup('<h1>Careers</h1><a href="/jobs/finance-intern">Finance Intern</a>','html.parser')
    def get(self,url):
        if url.endswith('/removed'):
            raise pages.PageMissing('Missing')
        return index if url.endswith('/careers') else page()
    monkeypatch.setattr(pages.PublicClient,'get',get)
    report=pages.scan_source(source(),known_urls=['https://example.com/jobs/removed'])
    assert report['status']=='ok' and report['connected']
    assert report['matched']==1
    assert report['missing_urls']==['https://example.com/jobs/removed']
    assert report['errors']==[]


def test_contact_failure_does_not_discard_job_text(monkeypatch):
    item=source()
    item['config']['contact_url']='https://example.com/contact'
    index=BeautifulSoup('<h1>Careers</h1><a href="/jobs/finance-intern">Finance Intern</a>','html.parser')
    def get(self,url):
        if url.endswith('/contact'):
            raise pages.SourceUnavailable('HTTP 503')
        return index if url.endswith('/careers') else page()
    monkeypatch.setattr(pages.PublicClient,'get',get)
    report=pages.scan_source(item)
    assert report['status']=='partial' and report['connected']
    assert report['matched']==1
    assert report['contact_text']==''


def test_changed_html_is_not_mislabeled_empty_results(monkeypatch):
    monkeypatch.setattr(pages.PublicClient,'get',lambda *args:BeautifulSoup('<h1>Welcome to our new website</h1>','html.parser'))
    report=pages.scan_source(source())
    assert report['status']=='error'
    assert 'configured selector' in report['message']


def test_nonmatching_but_readable_board_is_connected(monkeypatch):
    monkeypatch.setattr(pages.PublicClient,'get',lambda *args:BeautifulSoup('<h1>Careers</h1><a href="/jobs/nurse">Nurse</a>','html.parser'))
    report=pages.scan_source(source())
    assert report['status']=='ok'
    assert report['discovered']==0


def test_config_is_editable_and_validated(tmp_path):
    path=tmp_path/'known.json'
    path.write_text('{"keywords":["finance"],"sources":[{"id":"one","name":"Example","kind":"rss","index_url":"https://example.com/jobs/feed/"}]}')
    data=pages.load_page_config(path)
    assert data['sources'][0]['config']['keywords']==['finance']
    assert data['sources'][0]['enabled'] is True
    path.write_text('{"keywords":[],"sources":[]}')
    with pytest.raises(ValueError):
        pages.load_page_config(path)


def test_all_careers_feed_does_not_require_finance_keywords():
    item=source('rss')
    item['config']['job_scope']='all'
    root=ElementTree.fromstring('<rss><channel><item><title>Nurse</title><link>https://example.com/jobs/nurse</link></item></channel></rss>')
    assert pages.feed_links(item,root)==['https://example.com/jobs/nurse']
    assert pages.parse_listing(page('Nurse'),item,'https://example.com/jobs/nurse',[])['category_id']=='healthcare'


def test_query_pagination_follows_each_next_page_and_stops_cycles(monkeypatch):
    item=source('employer')
    item['config'].update(job_scope='all',max_pages=20)
    visited=[]
    def get(self,url):
        visited.append(url)
        if '/jobs/' in url:
            return page('Software Engineer')
        if '?page=2' in url:
            return BeautifulSoup('<h1>Careers</h1><a href="/jobs/two">Software Engineer</a><a class="more-link" href="?page=2">More Jobs</a>','html.parser')
        return BeautifulSoup('<h1>Careers</h1><a href="/jobs/one">Nurse</a><a class="more-link" href="?page=2">More Jobs</a>','html.parser')
    monkeypatch.setattr(pages.PublicClient,'get',get)
    report=pages.scan_source(item)
    assert report['status']=='ok'
    assert report['matched']==report['discovered']==2
    assert visited.count('https://example.com/careers?page=2')==1


def test_greenhouse_filters_local_jobs_and_uses_stable_human_url(monkeypatch):
    item=source('greenhouse')
    item.update(index_url='https://boards-api.greenhouse.io/v1/boards/example/jobs')
    item['config'].update(company_name='Example',job_scope='all')
    jobs=[{'id':1,'title':'Software Engineer','location':{'name':'Tempe, Arizona'},'absolute_url':'https://example.com/jobs/one'},
        {'id':2,'title':'Nurse','location':{'name':'Costa Mesa, California'},'absolute_url':'https://example.com/jobs/two'}]
    detail={**jobs[0],'content':'&lt;p&gt;'+('Build useful software with our team. '*5)+'&lt;/p&gt;',
        'first_published':'2026-10-01','departments':[{'name':'Engineering'}]}
    monkeypatch.setattr(pages.PublicClient,'json',lambda self,url:detail if url.endswith('/1') else {'jobs':jobs})
    saved=[]
    report=pages.scan_source(item,on_record=lambda url,record,contact:saved.append((url,record)))
    assert report['index_complete'] and report['discovered']==report['matched']==1
    assert saved[0][0]=='https://example.com/jobs/one'
    assert saved[0][1]['category_id']=='technology'
    assert saved[0][1]['posting_date'].isoformat()=='2026-10-01'
    assert '<p>' not in saved[0][1]['description']


def test_removed_job_redirect_is_missing_even_when_final_page_returns_200(monkeypatch):
    client=pages.PublicClient()
    client.robots['https://example.com']=SimpleNamespace(can_fetch=lambda *args:True,crawl_delay=lambda *args:None)
    replies=iter([SimpleNamespace(status_code=302,headers={'Location':'/careers?jobnotfound=true'}),
        SimpleNamespace(status_code=200,content=b'<h1>Current Opportunities</h1>')])
    monkeypatch.setattr(client.session,'get',lambda *args,**kwargs:next(replies))
    monkeypatch.setattr(pages.time,'sleep',lambda *args:None)
    with pytest.raises(pages.PageMissing,match='job was not found'):
        client.get('https://example.com/jobs/removed')
