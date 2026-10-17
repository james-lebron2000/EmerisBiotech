"""Explainable issuer discovery and observed ClinicalTrials.gov sponsor seeds.

This module never accesses the network or a database. Discovery is a screening
queue, not a verified census of pharmaceutical issuers. Seeded sponsor spellings
were observed in the linked registry records on 2026-10-17. Neither a sponsor
match nor a company pipeline page establishes ownership of every intervention.
"""
from copy import deepcopy
import re

EVIDENCE_DATE = '2026-10-17'
VERSION = 'issuer-discovery-1'

# market, ticker, display name, issuer group, official company source,
# [(observed exact lead sponsor, example NCT identifier)]. Multiple listings
# of the same issuer deliberately share issuer_group; do not add their trials.
_SEEDS = [
 ('US','PFE','Pfizer','pfizer','https://www.pfizer.com/science/drug-product-pipeline',[('Pfizer','NCT06760637')]),
 ('US','LLY','Eli Lilly and Company','lilly','https://www.lilly.com/science/research-development/pipeline',[('Eli Lilly and Company','NCT06465069')]),
 ('US','MRK','Merck & Co.','merck-us','https://www.merck.com/research/product-pipeline/',[('Merck Sharp & Dohme LLC','NCT06717347')]),
 ('US','BMY','Bristol Myers Squibb','bms','https://www.bms.com/research-and-development/pipeline.html',[('Bristol-Myers Squibb','NCT06937229')]),
 ('US','AMGN','Amgen','amgen','https://www.amgenpipeline.com/',[('Amgen','NCT07598825')]),
 ('US','GILD','Gilead Sciences','gilead','https://www.gilead.com/science/pipeline',[('Gilead Sciences','NCT04994509')]),
 ('US','REGN','Regeneron Pharmaceuticals','regeneron','https://www.regeneron.com/science/investigational-pipeline',[('Regeneron Pharmaceuticals','NCT07587658')]),
 ('US','VRTX','Vertex Pharmaceuticals','vertex','https://www.vrtx.com/our-science/pipeline/',[('Vertex Pharmaceuticals Incorporated','NCT06628908')]),
 ('US','BIIB','Biogen','biogen','https://www.biogen.com/science-and-innovation/pipeline.html',[('Biogen','NCT07219043')]),
 ('US','INCY','Incyte','incyte','https://incyte.com/what-we-do/pharmaceutical-portfolio',[('Incyte Corporation','NCT06388564')]),
 ('US','ALNY','Alnylam Pharmaceuticals','alnylam','https://www.alnylam.com/alnylam-rnai-pipeline',[('Alnylam Pharmaceuticals','NCT06360289')]),
 ('US','BMRN','BioMarin Pharmaceutical','biomarin','https://www.biomarin.com/science/research-pipeline/',[('BioMarin Pharmaceutical','NCT06780332')]),
 ('US','IONS','Ionis Pharmaceuticals','ionis','https://ionis.com/science-and-innovation/pipeline',[('Ionis Pharmaceuticals, Inc.','NCT06430385')]),
 ('US','ABBV','AbbVie','abbvie','https://www.abbvie.com/science/pipeline.html',[('AbbVie','NCT07071519')]),
 ('US','SRPT','Sarepta Therapeutics','sarepta','https://www.sarepta.com/products-pipeline/pipeline',[('Sarepta Therapeutics, Inc.','NCT07542314')]),
 ('US','JAZZ','Jazz Pharmaceuticals','jazz','https://www.jazzpharma.com/science/pipeline',[('Jazz Pharmaceuticals','NCT07625280')]),
 ('US','EXEL','Exelixis','exelixis','https://www.exelixis.com/clinical-trials-pipeline/',[('Exelixis','NCT06545331')]),
 ('US','JNJ','Johnson & Johnson','jnj','https://investor.jnj.com/pipeline/Innovative-Medicine-pipeline/default.aspx',[('Janssen Research & Development, LLC','NCT07577843')]),
 ('US','AZN','AstraZeneca','astrazeneca','https://www.astrazeneca.com/our-therapy-areas/pipeline.html',[('AstraZeneca','NCT06109779')]),
 ('US','NVS','Novartis','novartis','https://www.novartis.com/research-development/novartis-pipeline',[('Novartis Pharmaceuticals','NCT07456891')]),
 ('US','SNY','Sanofi','sanofi','https://www.sanofi.com/en/our-science/our-pipeline',[('Sanofi','NCT07225569')]),
 ('US','TAK','Takeda Pharmaceutical','takeda','https://www.takeda.com/science/pipeline/',[('Takeda','NCT07250815')]),
 ('US','BNTX','BioNTech','biontech','https://www.biontech.com/int/en/home/pipeline-and-products/pipeline.html',[('BioNTech SE','NCT07365995')]),
 ('US','MRNA','Moderna','moderna','https://www.modernatx.com/en-US/research/product-pipeline',[('ModernaTX, Inc.','NCT06880549')]),
 ('US','NVO','Novo Nordisk','novo-nordisk','https://www.novonordisk.com/science-and-technology/r-d-pipeline.html',[('Novo Nordisk A/S','NCT07868354')]),
 ('A','600276','恒瑞医药','hengrui','https://www.hengrui.com/en/',[('Jiangsu HengRui Medicine Co., Ltd.','NCT07286032')]),
 ('HK','01276','Hengrui Pharma','hengrui','https://www.hengrui.com/en/',[('Jiangsu HengRui Medicine Co., Ltd.','NCT07286032')]),
 ('A','688180','君实生物','junshi','https://www.junshipharma.com/en/',[('Shanghai Junshi Bioscience Co., Ltd.','NCT07876557')]),
 ('HK','01877','Junshi Biosciences','junshi','https://www.junshipharma.com/en/',[('Shanghai Junshi Bioscience Co., Ltd.','NCT07876557')]),
 ('A','688321','微芯生物','chipscreen','https://www.chipscreen.com/en/products',[('Chipscreen Biosciences, Ltd.','NCT07519265')]),
 ('A','300558','贝达药业','betta','https://www.bettapharma.com/en.php/Newdrug/projects',[('Betta Pharmaceuticals Co., Ltd.','NCT07435038')]),
 ('A','688428','诺诚健华','innocare','https://www.innocarepharma.com/en/development/pipeline',[('Beijing InnoCare Pharma Tech Co., Ltd.','NCT07236099'),('InnoCare Pharma Inc.','NCT05978739')]),
 ('HK','09969','InnoCare Pharma','innocare','https://www.innocarepharma.com/en/development/pipeline',[('Beijing InnoCare Pharma Tech Co., Ltd.','NCT07236099'),('InnoCare Pharma Inc.','NCT05978739')]),
 ('A','688192','迪哲医药','dizal','https://www.dizalpharma.com/',[('Dizal Pharmaceuticals','NCT07070440')]),
 ('A','688235','百济神州','beone','https://beonemedicines.com/science/pipeline/',[('BeiGene','NCT04883957')]),
 ('HK','06160','BeOne Medicines','beone','https://beonemedicines.com/science/pipeline/',[('BeiGene','NCT04883957')]),
 ('US','ONC','BeOne Medicines','beone','https://beonemedicines.com/science/pipeline/',[('BeiGene','NCT04883957')]),
 ('A','688266','泽璟制药','zelgen','https://www.zelgen.com/en/index.php/chanpinxian/',[('Suzhou Zelgen Biopharmaceuticals Co.,Ltd','NCT06233292')]),
 ('A','688336','三生国健','sunshine-guojian','https://www.3s-guojian.com/',[('Sunshine Guojian Pharmaceutical (Shanghai) Co., Ltd.','NCT07767253')]),
 ('A','300142','沃森生物','walvax','https://www.walvax.com/',[('Walvax Biotechnology Co., Ltd.','NCT05934890')]),
 ('A','688177','百奥泰','bio-thera','https://www.bio-thera.com/',[('Bio-Thera Solutions','NCT06139536')]),
 ('A','688185','康希诺','cansino','https://www.cansinotech.com/',[('CanSino Biologics Inc.','NCT07807033')]),
 ('HK','06185','CanSino Biologics','cansino','https://www.cansinotech.com/',[('CanSino Biologics Inc.','NCT07807033')]),
 ('A','688331','荣昌生物','remegen','https://www.remegen.com/',[('RemeGen Co., Ltd.','NCT07874282')]),
 ('HK','09995','RemeGen','remegen','https://www.remegen.com/',[('RemeGen Co., Ltd.','NCT07874282')]),
 ('HK','01801','Innovent Biologics','innovent','https://en.innoventbio.com/',[('Innovent Biologics (Suzhou) Co. Ltd.','NCT07842497')]),
 ('HK','09926','Akeso','akeso','https://www.akesobio.com/en/',[('Akeso','NCT06378697')]),
 ('HK','02162','Keymed Biosciences','keymed','https://en.keymedbio.com/en/pipeline.html',[('Keymed Biosciences Co.Ltd','NCT07823764'),('Beijing Keymed Biosciences Co., Ltd.','NCT07807826')]),
 ('HK','06990','Kelun-Biotech','kelun-biotech','https://en.kelun-biotech.com/',[('Sichuan Kelun-Biotech Biopharmaceutical Co., Ltd.','NCT06726369')]),
 ('HK','09688','Zai Lab','zai','https://ir.zailaboratory.com/',[('Zai Lab (Shanghai) Co., Ltd.','NCT06179069')]),
 ('US','ZLAB','Zai Lab','zai','https://ir.zailaboratory.com/',[('Zai Lab (Shanghai) Co., Ltd.','NCT06179069')]),
 ('HK','02256','Abbisko Therapeutics','abbisko','https://www.abbisko.com/',[('Abbisko Therapeutics Co, Ltd','NCT07718165')]),
 ('HK','02552','Hua Medicine','hua-medicine','https://www.huamedicine.com/En/investor',[('Hua Medicine Limited','NCT06263348')]),
 ('HK','02142','Harbour BioMed','harbour-biomed','https://www.harbourbiomed.com/',[('Harbour BioMed US, Inc.','NCT05824663'),('Harbour BioMed (Guangzhou) Co. Ltd.','NCT05790694')]),
 ('HK','02257','Sirnaomics','sirnaomics','https://www.sirnaomics.com/en/science-pipeline/clinical-studies/',[('Sirnaomics','NCT05421013')]),
 ('HK','02096','Simcere Pharmaceutical','simcere','https://www.simcere.com/en/kxcx/kxcx.aspx',[('Jiangsu Simcere Pharmaceutical Co., Ltd.','NCT04671849')]),
]

_IDENTITY_EVIDENCE={
 'HK:06990':[{'kind':'exchange_identity_notice','url':'https://www.hkexnews.hk/listedco/listconews/sehk/2026/0409/2026040901548_c.pdf','observed':EVIDENCE_DATE,'summary':'Sichuan Kelun-Biotech, stock code 6990; English stock short name SKB BIO effective 2026-04-14.'}],
}


def initial_mappings():
 """Return independent seed copies, never assertions of complete coverage.

 Consumers must match a current listing, preserve existing mappings, and retain
 ``review_status=pending``. Subsidiaries and former names are intentionally not
 expanded beyond the exact spellings demonstrated by registry records.
 """
 result=[]
 for market,ticker,name,group,source,observations in _SEEDS:
  # Company URLs are useful references, but no archived capture or current
  # source-availability check is performed by this pure seed configuration.
  # Registry observations below have a separate, actual observation date.
  evidence=[{'kind':'company_primary_source','url':source,'availability':'not_checked_by_platform','verification':'reference_link_not_archived'}]
  evidence.extend({'kind':'registry_lead_sponsor','url':'https://clinicaltrials.gov/study/'+nct,'nct_id':nct,'sponsor':alias,'observed':EVIDENCE_DATE} for alias,nct in observations)
  evidence.extend(deepcopy(_IDENTITY_EVIDENCE.get(market+':'+ticker,[])))
  provenance=('Registry lead sponsor observed '+EVIDENCE_DATE+'; company reference link (not archived/availability not checked by platform) '+source+'; exact registry evidence '+', '.join(alias+' https://clinicaltrials.gov/study/'+nct for alias,nct in observations)+'. Pending independent mapping review. Sponsor association does not establish intervention ownership, licensing rights or complete pipeline coverage.')
  result.append({'id':market+':'+ticker,'market':market,'ticker':ticker,'name':name,'issuer_group':group,'aliases':[alias for alias,_ in observations],'provenance':provenance,'source':source,'observed':EVIDENCE_DATE,'evidence':evidence,'review_status':'pending'})
 return deepcopy(result)


_INSTRUMENT = re.compile(r'\b(?:etf|fund|reit|warrants?|preferred|rights|acquisition corp(?:oration)?|acquisition company)\b|基金|认股权|認股權|優先股|优先股',re.I)
_CRO = re.compile(r'\b(?:iqvia|medpace|charles river laboratories|icon plc|contract research|contract development|cdmo|cro)\b|药明康德|藥明康德|药明生物|藥明生物|康龙化成|康龍化成|泰格医药|泰格醫藥',re.I)
_DEVICE = re.compile(r'\b(?:medical devices?|surgical instruments?|diagnostic equipment|dental equipment)\b|医疗器械|醫療器械|医学影像|醫學影像|药房|藥房|医药商业|醫藥商業',re.I)
_DRUG = re.compile(r'\b(?:pharma(?:ceuticals?|ceutical)?|biopharma(?:ceuticals?|ceutical)?|therapeutics?|medicines?|vaccines?)\b|制药|製藥|药业|藥業|医药制造|醫藥製造|生物制品|生物製品|创新药|創新藥|疫苗',re.I)
_BROAD = re.compile(r'\b(?:biotech(?:nology)?|biosciences?|biologics?|healthcare|medical|life sciences?|genomics?)\b|生物|医药|醫藥|医疗|醫療|生命科学|生命科學',re.I)
_NAME_HINTS = {
 'pfizer':('pfizer',),'lilly':('lilly',),'merck-us':('merck',),
 'bms':('bristol',),'amgen':('amgen',),'gilead':('gilead',),
 'regeneron':('regeneron',),'vertex':('vertex',),'biogen':('biogen',),
 'incyte':('incyte',),'alnylam':('alnylam',),'biomarin':('biomarin',),
 'ionis':('ionis',),'abbvie':('abbvie',),'sarepta':('sarepta',),
 'jazz':('jazz',),'exelixis':('exelixis',),
 'jnj':('johnson',),'astrazeneca':('astrazeneca',),'novartis':('novartis',),
 'sanofi':('sanofi',),'takeda':('takeda',),'biontech':('biontech',),
 'moderna':('moderna',),'novo-nordisk':('novo nordisk',),
 'hengrui':('hengrui','恒瑞','恆瑞'),'junshi':('junshi','君实','君實'),
 'chipscreen':('chipscreen','微芯'),'betta':('betta','贝达','貝達'),
 'innocare':('innocare','诺诚','諾誠'),'dizal':('dizal','迪哲'),
 'beone':('beone','beigene','百济','百濟'),'zelgen':('zelgen','泽璟','澤璟'),
 'sunshine-guojian':('guojian','三生国健','三生國健'),
 'walvax':('walvax','沃森'),'bio-thera':('bio-thera','百奥泰','百奧泰'),
 'cansino':('cansino','康希诺','康希諾'),'remegen':('remegen','荣昌','榮昌'),
 'innovent':('innovent','信达','信達'),'akeso':('akeso','康方'),
 'keymed':('keymed','康诺亚','康諾亞'),'kelun-biotech':('kelun','科伦','科倫','skb bio'),
 'zai':('zai lab','再鼎'),'abbisko':('abbisko','和誉','和譽'),
 'hua-medicine':('hua medicine','华领','華領'),'harbour-biomed':('harbour','hbm','和铂','和鉑'),
 'sirnaomics':('sirnaomics','圣诺','聖諾'),'simcere':('simcere','先声','先聲'),
}


def seed_matches_listing(mapping, listing):
 """Fail closed on changed ticker identity before automatic seed installation."""
 if _key(mapping)!=_key(listing):return False
 name=str(listing.get('name','')).casefold()
 return any(hint.casefold() in name for hint in _NAME_HINTS.get(mapping.get('issuer_group'),()))


def _key(row):
 market=str(row.get('market','')).upper().strip();ticker=str(row.get('ticker','')).upper().strip()
 if market not in ('US','A','HK') or not re.fullmatch(r'[A-Z0-9.\-]{1,16}',ticker):
  raise ValueError('Invalid listing market or ticker')
 if market in ('A','HK'):
  if not ticker.isdigit() or len(ticker)>(6 if market=='A' else 5):raise ValueError('Invalid numeric ticker')
  ticker=ticker.zfill(6 if market=='A' else 5)
 return market,ticker


def unsupported_listings(listings):
 """Return quarantined source rows without inventing normalized identities.

 Exchange directories contain preferred-series symbols with ``$``, ``/`` and
 other formats unsupported by the platform's company contract. These rows
 remain source listings and a reported identity gap, never a second company.
 """
 result=[]
 for row in listings:
  try:
   _key(row)
   if not isinstance(row.get('name'),str) or not row['name'].strip():raise ValueError('Listing name required')
  except ValueError as ex:
   result.append({'market':row.get('market'),'ticker':row.get('ticker'),'name':row.get('name'),'source':row.get('source',''),'reason':str(ex),'discovery_version':VERSION})
 return result


def discover(listings,*,strict=False):
 """Screen all supplied listings, retaining uncertainty and negative evidence.

 Exactly one result is emitted per market/ticker. Duplicate exchange snapshots
 contribute listing evidence rather than duplicate issuers. No current clock,
 network, registry mutation or source-string inference is used. Unclassified
 securities remain visible so absence of a name keyword cannot hide a gap.
 Unsupported identities are quarantined by ``unsupported_listings`` and are
 skipped here. They must be reported as gaps; punctuation is never removed to
 make an unsupported preferred/warrant symbol match an ordinary-share seed.
 """
 seeds={r['id']:r for r in initial_mappings()};groups={}
 for raw in listings:
  try:
   market,ticker=_key(raw)
   if not isinstance(raw.get('name'),str) or not raw['name'].strip():raise ValueError('Listing name required')
  except ValueError:
   if strict:raise
   continue
  groups.setdefault(market+':'+ticker,[]).append(dict(raw,market=market,ticker=ticker))
 result=[]
 for ident,rows in sorted(groups.items()):
  # Latest observation is representative; deterministic tie-break avoids order
  # dependent classifications when two connectors observe the same security.
  rows.sort(key=lambda r:(str(r.get('observed','')),str(r.get('source','')),r['name']))
  row=rows[-1];name=row['name'].strip();industry=str(row.get('industry','') or '')
  text=name+' '+industry;seed=seeds.get(ident)
  evidence=[{'kind':'listing_metadata','source':r.get('source',''),'name':r['name'],'industry':r.get('industry',''),'observed':r.get('observed','')} for r in rows]
  if _INSTRUMENT.search(text):
   classification='excluded';reason='证券名称提示基金、权证、优先股或收购载体；不纳入药物开发公司候选。'
  elif _CRO.search(text):
   classification='excluded';reason='名称或行业提示 CRO/CDMO 服务业务；独立药物资产需另外提供证据。'
  elif _DEVICE.search(text):
   classification='excluded';reason='名称或行业明确提示器械、设备、零售药房或流通业务；独立药物资产需另核。'
  elif seed and seed_matches_listing(seed,row):
   classification='drug_developer';reason='证券键命中有公司主来源和登记申办方观察的种子；主体映射仍待独立审查。'
   evidence.extend(deepcopy(seed['evidence']))
  elif seed:
   classification='uncertain';reason='证券键命中种子但公司名称不一致；可能更名或证券代码重用，必须核对主体。'
   evidence.append({'kind':'seed_identity_conflict','expected_name':seed['name'],'company_source':seed['source']})
  elif _DRUG.search(text) or re.search(r'(^|\s)C27(?:\s|$)',industry,re.I):
   classification='candidate';reason='证券名称或交易所行业提示药物/疫苗业务；须核对公司研发管线及申办方主体。'
  elif _BROAD.search(text):
   classification='uncertain';reason='生物或医疗名称涵盖多个业务类型；现有字段不足以认定药物开发公司。'
  else:
   classification='unclassified';reason='证券名称及行业信息不足；尚未核对年报、公司业务和申办方。'
  item={'id':ident,'market':row['market'],'ticker':row['ticker'],'name':name,'industry':industry,'classification':classification,'reason':reason,'rationale':reason,'evidence':evidence,'source':str(row.get('source','')),'observed':str(row.get('observed','')),'review_status':'pending','discovery_version':VERSION}
  if seed and seed_matches_listing(seed,row):
   item['issuer_group']=seed['issuer_group']
   item['suggested_aliases']=deepcopy(seed['aliases'])
  result.append(item)
 return result
