"""Public-source connectors. No arbitrary URLs, credentials, or TLS bypass."""
import io,json,os,zipfile,xml.etree.ElementTree as ET
from urllib.parse import urlencode,urlsplit

URLS={
 'US':'https://www.sec.gov/files/company_tickers_exchange.json',
 'HK':'https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx',
 'SZ':'https://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1110&TABKEY=tab1',
 'USNASDAQ':'https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt',
 'USOTHER':'https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt',
 'SH':'https://query.sse.com.cn/sseQuery/commonQuery.do?sqlId=COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L&STOCK_TYPE=1&REG_PROVINCE=&CSRC_CODE=&STOCK_CODE=&COMPANY_STATUS=2%2C4%2C5%2C7%2C8&type=inParams&isPagination=true&pageHelp.pageSize=10000&pageHelp.pageNo=1',
}
URLS['STAR']=URLS['SH'].replace('STOCK_TYPE=1','STOCK_TYPE=8')
ALLOWED={urlsplit(u).hostname for u in URLS.values()}|{'clinicaltrials.gov'}
FIELDS='NCTId,BriefTitle,LeadSponsorName,Phase,OverallStatus,Condition,InterventionType,InterventionName,PrimaryCompletionDate,LastUpdatePostDate,StudyType'
def validate_url(url):
 p=urlsplit(url)
 if p.scheme!='https' or p.hostname not in ALLOWED or p.username or p.password or p.port not in (None,443):raise ValueError('Source URL rejected')
def fetch(url):
 validate_url(url)
 # System curl uses the system trust store; TLS verification is never disabled.
 import subprocess
 env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','SSL_CERT_FILE','CURL_CA_BUNDLE','HTTPS_PROXY','HTTP_PROXY','NO_PROXY')}
 args=['curl','--disable','--silent','--show-error','--fail','--max-time','30','--max-filesize','20000000','--retry','2','--retry-max-time','65','--proto','=https','--user-agent',os.environ.get('VBT_SOURCE_USER_AGENT','EmerisBiotechResearch/0.2')]
 if urlsplit(url).hostname=='query.sse.com.cn':args+=['--referer','https://www.sse.com.cn/']
 result=subprocess.run(args+[url],capture_output=True,timeout=100,env=env)
 if result.returncode:raise OSError('Source request failed: '+result.stderr.decode(errors='replace')[-300:])
 if len(result.stdout)>20_000_000:raise ValueError('Response exceeds 20 MB limit')
 return result.stdout

def xlsx_rows(raw):
 ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  if sum(i.file_size for i in z.infolist())>100_000_000:raise ValueError('Expanded workbook too large')
  strings=[]
  if 'xl/sharedStrings.xml' in z.namelist():
   strings=[''.join(x.itertext()) for x in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si',ns)]
  rows=[]
  for row in ET.fromstring(z.read('xl/worksheets/sheet1.xml')).findall('.//s:row',ns):
   values={}
   for c in row.findall('s:c',ns):
    col=''.join(x for x in c.attrib.get('r','') if x.isalpha());v=c.find('s:v',ns);value=v.text if v is not None else ''.join(c.find('s:is',ns).itertext()) if c.find('s:is',ns) is not None else ''
    values[col]=strings[int(value)] if c.attrib.get('t')=='s' and value else value
   rows.append(values)
  return rows

def parse_universe(source,raw):
 rows=[];total=None
 if source=='US':
  d=json.loads(raw);keys=d['fields']
  for vals in d['data']:
   r=dict(zip(keys,vals))
   if r.get('exchange') in ('Nasdaq','NYSE','Cboe','AMEX'):
    rows.append({'market':'US','exchange':r['exchange'],'ticker':r['ticker'],'name':r['name'],'industry':''})
 elif source=='HK':
  sheet=xlsx_rows(raw);header=next((i for i,r in enumerate(sheet) if 'Stock Code' in r.values()),None)
  if header is None:raise ValueError('HKEX header changed')
  mapping={v:k for k,v in sheet[header].items()}
  for r in sheet[header+1:]:
   category=r.get(mapping.get('Category',''),'')
   if 'Equit' not in category:continue
   code=r.get(mapping['Stock Code'],'')
   if code.isdigit():rows.append({'market':'HK','exchange':'HKEX','ticker':code.zfill(5),'name':r.get(mapping.get('Name of Securities',''),''),'industry':''})
 elif source in ('USNASDAQ','USOTHER'):
  import csv
  records=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig')),delimiter='|'))
  for r in records:
   code=r.get('Symbol',r.get('ACT Symbol',''))
   if not code or code.startswith('File Creation') or r.get('Test Issue')!='N' or r.get('ETF')!='N':continue
   exchange='Nasdaq' if source=='USNASDAQ' else {'N':'NYSE','A':'NYSE American','P':'NYSE Arca','Z':'Cboe','V':'IEX'}.get(r.get('Exchange'),'Other')
   rows.append({'market':'US','exchange':exchange,'ticker':code,'name':r['Security Name'],'industry':''})
 elif source=='SZ':
  sheet=xlsx_rows(raw);header=next((i for i,r in enumerate(sheet) if 'A股代码' in r.values()),None)
  if header is None:raise ValueError('SZSE header changed')
  mapping={v:k for k,v in sheet[header].items()}
  for r in sheet[header+1:]:
   code=r.get(mapping['A股代码'],'')
   if code.isdigit():rows.append({'market':'A','exchange':'SZSE','ticker':code.zfill(6),'name':r.get(mapping.get('A股简称',''),''),'industry':r.get(mapping.get('所属行业',''),'')})
 elif source in ('SH','STAR'):
  d=json.loads(raw);rs=d.get('result',d.get('pageHelp',{}).get('data',[]));total=int(d.get('pageHelp',{}).get('total',len(rs)))
  for r in rs:rows.append({'market':'A','exchange':'SSE','ticker':str(r['A_STOCK_CODE']).zfill(6),'name':r.get('COMPANY_ABBR',r.get('SEC_NAME_CN','')),'industry':r.get('CSRC_CODE_DESC','')})
 else:raise ValueError('Unknown source')
 if not rows or any(not r['name'] for r in rows):raise ValueError('Empty or changed universe schema; old snapshot retained')
 return rows, total

def trial_url(alias,token=None):
 params={'query.spons':alias,'format':'json','pageSize':1000,'countTotal':'true','fields':FIELDS,'sort':'LastUpdatePostDate:desc'}
 if token:params['pageToken']=token
 return 'https://clinicaltrials.gov/api/v2/studies?'+urlencode(params)

def parse_trials(raw,aliases):
 d=json.loads(raw)
 if not isinstance(d.get('studies'),list):raise ValueError('ClinicalTrials.gov schema changed')
 rows=[]
 for study in d['studies']:
  p=study['protocolSection'];sponsor=p.get('sponsorCollaboratorsModule',{}).get('leadSponsor',{}).get('name','')
  # API sponsor query also matches collaborators: exact lead-name matching avoids false attribution.
  if sponsor.casefold().strip() not in {s.casefold().strip() for s in aliases}:continue
  interventions=[{'name':x['name'],'type':x['type']} for x in p.get('armsInterventionsModule',{}).get('interventions',[]) if x.get('type') in ('DRUG','BIOLOGICAL') and x.get('name')]
  if not interventions:continue
  status=p.get('statusModule',{});ident=p['identificationModule']
  rows.append({'nct_id':ident['nctId'],'title':ident.get('briefTitle',''),'sponsor':sponsor,'interventions':interventions,'conditions':p.get('conditionsModule',{}).get('conditions',[]),'phases':p.get('designModule',{}).get('phases',[]),'status':status.get('overallStatus','UNKNOWN'),'primary_completion':status.get('primaryCompletionDateStruct',{}),'source_updated':status.get('lastUpdatePostDateStruct',{}).get('date'),'source_url':'https://clinicaltrials.gov/study/'+ident['nctId'],'attribution':'exact lead sponsor; drug ownership and arm role not established'})
 return rows,d.get('nextPageToken'),d.get('totalCount')
