"""Psychiatric Literature Radar — live bibliographic search, no sample articles."""
import concurrent.futures
import datetime as dt
import hashlib
import html
import json
import os
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Psychiatric Literature Radar", page_icon="🧬", layout="wide")

OUTCOMES = {
    "Suicide / Suicidal behavior": ['suicid', 'self-harm', 'self harm', 'suicide attempt', 'suicidal ideation'],
    "Major depressive disorder (MDD)": ['major depressive disorder', 'major depression', 'depressive disorder', 'mdd'],
    "Treatment-resistant depression (TRD)": ['treatment-resistant depression', 'treatment resistant depression', 'treatment-refractory depression', 'treatment refractory depression', 'treatment resistant major depression', 'trd'],
    "Bipolar disorder": ['bipolar', 'manic depression'],
    "Schizophrenia": ['schizophren', 'psychosis', 'psychotic disorder'],
    "Other psychiatric disorders": ['psychiatric disorder', 'mental disorder', 'ptsd', 'posttraumatic stress', 'anxiety disorder', 'obsessive compulsive', 'ocd', 'adhd', 'autism spectrum disorder', 'eating disorder'],
}
METHODS = {
    "Multi-omics": ['multi-omics', 'multiomics', 'multi omics', 'integrative omics', 'proteogenomic'],
    "DNA-seq / WGS / WES": ['whole genome sequencing', 'whole-genome sequencing', 'whole exome sequencing', 'whole-exome sequencing', 'wgs', 'wes', 'dna sequencing', 'dna-seq', 'exome sequencing', 'genome sequencing'],
    "RNA-seq / Transcriptomics": ['rna-seq', 'rna seq', 'rna sequencing', 'transcriptom', 'single cell rna', 'scrna-seq', 'gene expression profiling'],
    "Rare-variant analysis": ['rare variant', 'rare genetic variant', 'ultra-rare', 'ultrarare', 'burden test', 'sequence kernel association', 'skat', 'loss-of-function variant', 'de novo mutation'],
    "Machine learning / Deep learning": ['machine learning', 'deep learning', 'neural network', 'random forest', 'support vector machine', 'xgboost', 'gradient boosting', 'artificial intelligence', 'predictive model'],
    "Biobank study": ['biobank', 'biobanking', 'uk biobank', 'all of us research program', 'finngen', 'biobank japan', 'population biobank', 'national biobank'],
    "Trio study / Family-based study": ['trio study', 'trio sequencing', 'parent-offspring trio', 'parent offspring trio', 'parent-child trio', 'parent child trio', 'parent-proband trio', 'parent proband trio', 'case-parent trio', 'case parent trio', 'family trio', 'family-based study', 'family based study', 'parent-offspring trios', 'de novo variant', 'de novo mutation'],
    "GWAS / PRS": ['genome-wide association', 'genome wide association', 'gwas', 'polygenic risk', 'polygenic score', 'prs'],
}
SOURCES = ['PubMed', 'Europe PMC', 'OpenAlex', 'arXiv', 'bioRxiv', 'medRxiv']
DB_PATH = Path(os.getenv('LITERATURE_DB_PATH', 'literature_favorites.sqlite3'))
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'PsychiatricLiteratureRadar/1.0 (academic literature search; contact: local-app)'})

def get_json(url, params=None, timeout=24):
    res = requests.get(url, params=params, timeout=timeout, headers={'User-Agent':'PsychiatricLiteratureRadar/1.0'})
    res.raise_for_status()
    return res.json()

def get_text(url, params=None, timeout=24):
    res = requests.get(url, params=params, timeout=timeout, headers={'User-Agent':'PsychiatricLiteratureRadar/1.0'})
    res.raise_for_status()
    return res.text

def clean(x):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', str(x or '')))).strip()

def canonical_doi(x):
    return re.sub(r'^(https?://(dx\.)?doi\.org/|doi:)', '', str(x or '').strip(), flags=re.I).lower().strip().rstrip('.')

def norm_title(x):
    return re.sub(r'[^a-z0-9]+', '', str(x or '').casefold())

def article(title='', abstract='', date='', doi='', pmid='', url='', source='', authors='', journal=''):
    date = str(date or '')[:10]
    year_match = re.search(r'(19|20)\d{2}', date)
    return dict(title=clean(title), abstract=clean(abstract), date=date,
                year=int(year_match.group()) if year_match else None, doi=canonical_doi(doi),
                pmid=str(pmid or ''), url=url, sources=[source], authors=clean(authors), journal=clean(journal))

def phrase_query(mapping, chosen, field=None):
    # Search endpoints get manageable broad clinical/method terms, then deterministic local screening.
    terms = []
    for label in chosen:
        vals = mapping[label]
        terms.extend(vals[:3])
    terms = list(dict.fromkeys(terms))
    return '(' + ' OR '.join('"' + t + '"' + (field or '') for t in terms) + ')'

def search_pubmed(selected_outcomes, selected_methods, limit, since):
    q = phrase_query(OUTCOMES, selected_outcomes, '[Title/Abstract]') + ' AND ' + phrase_query(METHODS, selected_methods, '[Title/Abstract]')
    if since: q += f' AND ("{since}"[Date - Publication] : "3000"[Date - Publication])'
    base = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/'
    params = {'db':'pubmed','term':q,'retmax':limit,'retmode':'json','sort':'pub date','tool':'PsychiatricLiteratureRadar'}
    if os.getenv('NCBI_EMAIL'): params['email'] = os.getenv('NCBI_EMAIL')
    if os.getenv('NCBI_API_KEY'): params['api_key'] = os.getenv('NCBI_API_KEY')
    ids = get_json(base+'esearch.fcgi', params)['esearchresult']['idlist']
    if not ids: return []
    xml = get_text(base+'efetch.fcgi', {'db':'pubmed','id':','.join(ids),'retmode':'xml'}, timeout=35)
    root = ET.fromstring(xml)
    rows=[]
    for entry in root.findall('.//PubmedArticle'):
        med = entry.find('./MedlineCitation')
        if med is None: continue
        pub = med.find('./Article')
        if pub is None: continue
        title=''.join(pub.find('ArticleTitle').itertext()) if pub.find('ArticleTitle') is not None else ''
        abst=' '.join(''.join(e.itertext()) for e in pub.findall('.//Abstract/AbstractText'))
        pmid=med.findtext('PMID', '')
        doi=''
        for aid in entry.findall('.//ArticleId'):
            if aid.attrib.get('IdType')=='doi': doi=aid.text or ''
        y=pub.findtext('.//PubDate/Year') or pub.findtext('.//PubDate/MedlineDate') or med.findtext('.//DateCompleted/Year') or ''
        authors=', '.join((' '.join(filter(None, [a.findtext('ForeName'),a.findtext('LastName')])) or a.findtext('CollectiveName','')) for a in pub.findall('.//AuthorList/Author')[:8])
        rows.append(article(title,abst,y,doi,pmid,'https://pubmed.ncbi.nlm.nih.gov/'+pmid+'/', 'PubMed',authors,pub.findtext('.//Journal/Title','')))
    return rows

def search_europe(selected_outcomes, selected_methods, limit, since):
    q=phrase_query(OUTCOMES,selected_outcomes)+' AND '+phrase_query(METHODS,selected_methods)
    if since: q+=f' AND FIRST_PDATE:[{since} TO 3000-12-31]'
    data=get_json('https://www.ebi.ac.uk/europepmc/webservices/rest/search', {'query':q+' sort_date:y','format':'json','resultType':'core','pageSize':limit})
    rows=[]
    for a in data.get('resultList',{}).get('result',[]):
        pmid=a.get('pmid','')
        rows.append(article(a.get('title'),a.get('abstractText'),a.get('firstPublicationDate') or a.get('firstIndexDate') or a.get('pubYear'),a.get('doi'),pmid, 'https://pubmed.ncbi.nlm.nih.gov/'+pmid+'/' if pmid else 'https://europepmc.org/article/'+str(a.get('source','MED'))+'/'+str(a.get('id','')), 'Europe PMC',a.get('authorString'),a.get('journalTitle')))
    return rows

def rebuild_abstract(inverted):
    if not inverted: return ''
    words={}
    for word,positions in inverted.items():
        for pos in positions: words[int(pos)]=word
    return ' '.join(words[i] for i in sorted(words))

def search_openalex(selected_outcomes, selected_methods, limit, since):
    # OpenAlex search uses compact keywords; strict local screening below.
    queries=[f'{OUTCOMES[o][0]} {METHODS[m][0]}' for o in selected_outcomes for m in selected_methods]
    result=[]; seen=set()
    for q in queries[:12]:
        params={'search':q,'per_page':min(25,max(5,limit//min(len(queries),12)+4)),'sort':'publication_date:desc'}
        if since: params['filter']='from_publication_date:'+since
        if os.getenv('OPENALEX_API_KEY'): params['api_key']=os.getenv('OPENALEX_API_KEY')
        data=get_json('https://api.openalex.org/works',params)
        for a in data.get('results',[]):
            if a['id'] in seen:continue
            seen.add(a['id'])
            pmid=str((a.get('ids') or {}).get('pmid') or '').rstrip('/').split('/')[-1] if (a.get('ids') or {}).get('pmid') else ''
            result.append(article(a.get('display_name'),rebuild_abstract(a.get('abstract_inverted_index')),a.get('publication_date'),a.get('doi'),pmid,a.get('doi') or a.get('id'),'OpenAlex',', '.join(x.get('author',{}).get('display_name','') for x in a.get('authorships',[])[:8]),((a.get('primary_location') or {}).get('source') or {}).get('display_name')))
    return result[:limit]

def search_arxiv(selected_outcomes,selected_methods,limit,since):
    # arXiv query uses a moderate number of phrases to avoid query-length limits.
    outcomes=[OUTCOMES[o][0] for o in selected_outcomes]
    methods=[METHODS[m][0] for m in selected_methods]
    q='('+' OR '.join('all:"'+x+'"' for x in outcomes)+') AND ('+' OR '.join('all:"'+x+'"' for x in methods)+')'
    text=get_text('https://export.arxiv.org/api/query',{'search_query':q,'start':0,'max_results':limit,'sortBy':'submittedDate','sortOrder':'descending'}, timeout=30)
    ns={'a':'http://www.w3.org/2005/Atom','arxiv':'http://arxiv.org/schemas/atom'}
    root=ET.fromstring(text); rows=[]
    for e in root.findall('a:entry',ns):
        url=e.findtext('a:id','',ns); day=e.findtext('a:published','',ns)[:10]
        if since and day < since: continue
        authors=', '.join(n.text or '' for n in e.findall('a:author/a:name',ns))
        doi=e.findtext('arxiv:doi','',ns)
        rows.append(article(e.findtext('a:title','',ns),e.findtext('a:summary','',ns),day,doi,'',url,'arXiv',authors,'arXiv preprint'))
    return rows

def search_rxiv(server, limit, since):
    # Official bioRxiv API is date/recent-post-based, not keyword search.
    # Search only recent pages then apply local outcome + method screening.
    interval=f'{since}/{dt.date.today().isoformat()}' if since else '1000'
    rows=[]
    for cursor in range(0,min(limit*3,300),100):
        data=get_json(f'https://api.biorxiv.org/details/{server}/{interval}/{cursor}',timeout=30)
        collection=data.get('collection',[])
        if not collection:break
        for a in collection:
            doi=a.get('doi','');rows.append(article(a.get('title'),a.get('abstract'),a.get('date'),doi,'',('https://www.medrxiv.org' if server=='medrxiv' else 'https://www.biorxiv.org')+'/content/'+doi+'v'+str(a.get('version','1')), 'medRxiv' if server=='medrxiv' else 'bioRxiv',a.get('authors'),a.get('category')))
        if len(collection)<100: break
    return rows

def matched(text, mapping, selected):
    lower=text.casefold()
    found=[]
    for label in selected:
        for term in mapping[label]:
            pattern = re.escape(term) if term in ('suicid', 'schizophren', 'transcriptom') else r'(?<![a-z])' + re.escape(term) + r'(?![a-z])'
            if re.search(pattern,lower):
                found.append(label);break
    return found

def enrich(rows, outcomes,methods):
    result=[]
    for r in rows:
        text=r['title']+' '+r['abstract']
        r['outcome_tags']=matched(text,OUTCOMES,outcomes)
        r['method_tags']=matched(text,METHODS,methods)
        if r['outcome_tags'] and r['method_tags'] and r['title']:result.append(r)
    return result

def deduplicate(rows):
    merged={}
    for r in rows:
        key='doi:'+r['doi'] if r['doi'] else ('pmid:'+r['pmid'] if r['pmid'] else 'title:'+norm_title(r['title']))
        titlekey='title:'+norm_title(r['title'])
        # Prefer DOI match, then PMID, then normalized title, merging source attributions.
        hit=next((k for k in [key,'pmid:'+r['pmid'] if r['pmid'] else None,titlekey] if k and k in merged),None)
        if not hit:
            merged[key]=r
            merged.setdefault(titlekey,r)
            if r['pmid']:merged.setdefault('pmid:'+r['pmid'],r)
        else:
            a=merged[hit]
            a['sources']=sorted(set(a['sources']+r['sources']))
            for field in ['abstract','doi','pmid','authors','journal','date']:
                if not a.get(field) or (field=='abstract' and len(r.get(field,''))>len(a.get(field,''))):a[field]=r.get(field)
            a['outcome_tags']=sorted(set(a['outcome_tags']+r['outcome_tags']))
            a['method_tags']=sorted(set(a['method_tags']+r['method_tags']))
            merged[key]=a;merged[titlekey]=a
    unique={id(v):v for v in merged.values()}
    return list(unique.values())

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS favorites (key TEXT PRIMARY KEY, payload TEXT NOT NULL, saved_at TEXT NOT NULL)')

def key_of(a):
    return a['doi'] or a['pmid'] or norm_title(a['title'])

def read_favs():
    with sqlite3.connect(DB_PATH) as conn:
        return {k:json.loads(payload) for k,payload in conn.execute('SELECT key,payload FROM favorites')}

def toggle_fav(a,add):
    with sqlite3.connect(DB_PATH) as conn:
        if add:conn.execute('INSERT OR REPLACE INTO favorites VALUES (?,?,?)',(key_of(a),json.dumps(a,ensure_ascii=False),dt.datetime.now(dt.timezone.utc).isoformat()))
        else:conn.execute('DELETE FROM favorites WHERE key=?',(key_of(a),))

init_db()
st.title('🧬 Psychiatric Literature Radar')
st.caption('Live literature discovery · Outcome AND Method · PubMed / Europe PMC / OpenAlex / preprints')
with st.sidebar:
    st.header('搜尋條件')
    outcomes=st.multiselect('Outcomes',list(OUTCOMES),default=list(OUTCOMES))
    methods=st.multiselect('Methods',list(METHODS),default=list(METHODS))
    sources=st.multiselect('資料來源',SOURCES,default=SOURCES)
    current=dt.date.today().year
    years=st.selectbox('出版年份範圍',['最近 1 年','最近 3 年','最近 5 年','不限年份'],index=1)
    min_date={'最近 1 年':dt.date.today()-dt.timedelta(days=365),'最近 3 年':dt.date.today()-dt.timedelta(days=1095),'最近 5 年':dt.date.today()-dt.timedelta(days=1826),'不限年份':None}[years]
    since=min_date.isoformat() if min_date else None
    limit=st.slider('各來源最多抓取篇數',10,100,30,10)
    sort=st.selectbox('排序',['最新優先','最舊優先','標題 A–Z'])
    search=st.button('🔎 即時搜尋 / 更新',type='primary',use_container_width=True)
    st.caption('搜尋結果會依標題與摘要再次比對。bioRxiv / medRxiv 官方 API 不支援全文關鍵字搜尋，因此僅掃描所選時間範圍內有限筆數的最新預印本。')

if 'articles' not in st.session_state:st.session_state.articles=[]
if 'errors' not in st.session_state:st.session_state.errors={}
if 'last_updated' not in st.session_state:st.session_state.last_updated=None
if search:
    if not outcomes or not methods or not sources:
        st.warning('請至少選擇一個 outcome、一個 method 和一個來源。')
    else:
        tasks={}
        with st.spinner('正在查詢真實文獻 API…'):
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                for source in sources:
                    if source=='PubMed':f=pool.submit(search_pubmed,outcomes,methods,limit,since)
                    elif source=='Europe PMC':f=pool.submit(search_europe,outcomes,methods,limit,since)
                    elif source=='OpenAlex':f=pool.submit(search_openalex,outcomes,methods,limit,since)
                    elif source=='arXiv':f=pool.submit(search_arxiv,outcomes,methods,limit,since)
                    else:f=pool.submit(search_rxiv,source.lower(),limit,since)
                    tasks[source]=f
                rows=[];errors={}
                for source,f in tasks.items():
                    try:rows.extend(f.result())
                    except Exception as e:errors[source]=str(e)[:250]
            st.session_state.articles=deduplicate(enrich(rows,outcomes,methods))
            st.session_state.errors=errors
            st.session_state.last_updated=dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

articles=st.session_state.articles
favs=read_favs()
tab1,tab2=st.tabs(['搜尋結果','⭐ 我的收藏'])
with tab1:
    st.write(f'**符合條件：{len(articles)} 篇**（本次取得並篩選的文獻，非全球符合條件總數）')
    if st.session_state.last_updated:st.caption('最後查詢：'+st.session_state.last_updated)
    if st.session_state.errors:
        st.warning('部分來源查詢失敗；其他來源仍正常顯示：'+'；'.join(f'{k}: {v}' for k,v in st.session_state.errors.items()))
    if not articles:st.info('按左側「即時搜尋 / 更新」開始查詢；若沒有結果，請擴大年份或增加單一來源筆數。')
    query=st.text_input('在本次結果內搜尋（標題 / 摘要 / DOI）',key='textfilter')
    visible=[a for a in articles if not query or query.casefold() in (a['title']+' '+a['abstract']+' '+a['doi']).casefold()]
    if sort=='最新優先':visible.sort(key=lambda a:(a.get('date') or '',a.get('year') or 0),reverse=True)
    elif sort=='最舊優先':visible.sort(key=lambda a:(a.get('date') or '',a.get('year') or 0))
    else:visible.sort(key=lambda a:a['title'].casefold())
    if visible:
        export=pd.DataFrame([{**a,'sources':'; '.join(a['sources']),'outcome_tags':'; '.join(a['outcome_tags']),'method_tags':'; '.join(a['method_tags'])} for a in visible])
        st.download_button('⬇️ 下載搜尋結果 CSV',export.to_csv(index=False).encode('utf-8-sig'),'literature_results.csv','text/csv')
    for i,a in enumerate(visible):
        st.markdown('---')
        st.subheader(a['title'])
        st.caption(f"{a.get('date') or a.get('year') or 'Date unknown'} · {a.get('journal') or 'Journal unknown'} · {' + '.join(a['sources'])}")
        st.markdown('**Outcome：** '+', '.join(a['outcome_tags'])+'  |  **Method：** '+', '.join(a['method_tags']))
        if a.get('authors'):st.caption(a['authors'][:300])
        with st.expander('摘要',expanded=False):st.write(a['abstract'] or '此來源未提供摘要。')
        links=[]
        if a.get('doi'):links.append(f"[DOI](https://doi.org/{quote(a['doi'],safe='/')})")
        if a.get('pmid'):links.append(f"[PubMed](https://pubmed.ncbi.nlm.nih.gov/{quote(a['pmid'])}/)")
        if a.get('url'):links.append(f"[原始來源]({a['url']})")
        st.markdown(' · '.join(links))
        is_saved=key_of(a) in favs
        if st.button('★ 取消收藏' if is_saved else '☆ 加入收藏',key=f'save_{i}_{key_of(a)}'):
            toggle_fav(a,not is_saved)
            st.rerun()
with tab2:
    st.write(f'**已收藏 {len(favs)} 篇**（儲存於部署環境的 SQLite 檔）')
    if favs:
        frame=pd.DataFrame([{**a,'sources':'; '.join(a['sources']),'outcome_tags':'; '.join(a.get('outcome_tags',[])),'method_tags':'; '.join(a.get('method_tags',[]))} for a in favs.values()])
        st.download_button('⬇️ 匯出收藏 CSV',frame.to_csv(index=False).encode('utf-8-sig'),'saved_articles.csv','text/csv')
        for k,a in list(favs.items()):
            with st.expander(a['title']):
                st.write(a.get('abstract') or '無摘要')
                st.write('DOI:',a.get('doi') or '無','| PMID:',a.get('pmid') or '無')
                if st.button('移除收藏',key='remove_'+k):
                    toggle_fav(a,False);st.rerun()
st.caption('資料來自所選的公開文獻 API；標籤是關鍵詞規則分類，不代表經人工審核。不同 API 的索引更新時間、收錄範圍與摘要可用性不同。')
