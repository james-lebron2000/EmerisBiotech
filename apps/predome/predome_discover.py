"""Read-only public-registry candidate discovery; PCD is never a readout guarantee."""
import argparse,urllib.request,urllib.parse,json
SPONSORS=['AstraZeneca','Novartis','Roche','Merck Sharp & Dohme','Pfizer','Eli Lilly','Sanofi','Takeda','Biogen','Amgen','GSK','Johnson & Johnson','AbbVie','Bristol Myers Squibb','Novo Nordisk']
def discover(sponsor,page_size=20):
 q=urllib.parse.urlencode({'query.spons':sponsor,'pageSize':min(page_size,100),'format':'json','countTotal':'true','filter.overallStatus':'RECRUITING,ACTIVE_NOT_RECRUITING,COMPLETED'})
 data=json.load(urllib.request.urlopen('https://clinicaltrials.gov/api/v2/studies?'+q,timeout=25));rows=[]
 for d in data.get('studies',[]):
  p=d['protocolSection'];id=p['identificationModule'];rows.append({'nct_id':id['nctId'],'title':id['briefTitle'],'has_results':d.get('hasResults'),'phases':p.get('designModule',{}).get('phases',[]),'primary_completion':p['statusModule'].get('primaryCompletionDateStruct'),'official_readout_window':None,'forecast_eligible':False,'next_action':'Match sponsor guidance and exclude already disclosed target results; registry PCD alone is not a readout window'})
 return {'sponsor':sponsor,'total_registry_matches':data.get('totalCount'),'truncated':bool(data.get('nextPageToken')),'next_page_token':data.get('nextPageToken'),'candidates':rows}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--sponsor',choices=SPONSORS,required=True);p.add_argument('--limit',type=int,default=20);a=p.parse_args();print(json.dumps(discover(a.sponsor,a.limit),ensure_ascii=False,indent=2))
