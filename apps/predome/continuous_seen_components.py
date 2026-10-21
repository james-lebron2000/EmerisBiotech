"""Curate all69seen identity targets into molecule cores; no historical X/labels."""
from pathlib import Path
from datetime import datetime,timezone
import json,hashlib,re,os
ROOT=Path(__file__).resolve().parent
DATA=ROOT/'predome_data/continuous_v4';IDENTITY=DATA/'seen_identity'
def sha(b):return hashlib.sha256(b).hexdigest()
def encode(j):return json.dumps(j,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()+b'\n'
def norm(s):return re.sub('[^a-z0-9]+','',str(s or '').lower())
def atomic(p,j):p.parent.mkdir(parents=True,exist_ok=True);t=p.with_name(p.name+'.part');t.write_bytes(encode(j));os.replace(t,p)
def desc(p):return {'path':str(p.relative_to(ROOT)),'sha256':sha(p.read_bytes())}
# Named target products, curated from each current registry title/summary/arms, not outcomes.
GROUPS={
'fulvestrant':'01602380','zolbetuximab':'01630083 03504397 03653507','palbociclib':'01740427','risankizumab':'02031276 03104413 03105128','abemaciclib':'02107703','atezolizumab':'02302807 02367794 02450331 02657434 02788279','pembrolizumab':'02370498 02494583 02574598 03221426 03515837 03675737 03834493','ozanimod':'02435992','alpelisib':'02437318','anifrolumab':'02446899 02446912','etrasimod':'02447302 03945188 03996369','durvalumab':'02542293 03164616','tremelimumab':'02542293 03164616','avelumab':'02625610 02625623','baricitinib':'02708095 03616912 03616964','cobimetinib':'02788279','litifilimab':'02847598 04895241 04961567','vericiguat':'02861534','pegilodecakin':'02923921','omecamtiv mecarbil':'02929329','evobrutinib':'02975349 04338022 04338061','gantenerumab':'03443973 03444870','canakinumab':'03447769 03626545 03631199','ustekinumab':'03517722','deucravacitinib':'03611751 03624127','oleclumab':'03822351 05221840','monalizumab':'03822351 05221840','tolebrutinib':'03889639 04410978 04410991 04411641','capivasertib':'03997123 04305496','inavolisib':'04191499','camizestrant':'04214288 04964934','xevinapant':'04459715','amcenestrant':'04478266','zasocitinib':'06233461 06254950','arcotatug tavatecan':'06238843','sonesitatug vedotin':'06346392'}
SEEDS={'fulvestrant':['Faslodex'],'palbociclib':['PD-0332991'],'ozanimod':['RPC1063'],'deucravacitinib':['BMS-986165'],'camizestrant':['AZD9833'],'tolebrutinib':['SAR442168'],'litifilimab':['BIIB059'],'zasocitinib':['TAK-279'],'arcotatug tavatecan':['IBI343'],'sonesitatug vedotin':['AZD0901']}
# These identity equivalences are primary manufacturer excerpts, not archived full documents.
EXTERNAL={
'fulvestrant':{'url':'https://medicalinformation.astrazeneca-us.com/prescribing-information/faslodex.html','identity_excerpt':'FASLODEX / Fulvestrant','supports_aliases':['Faslodex'],'source_kind':'official_manufacturer_prescribing_information_search_index_excerpt'},
'ozanimod':{'url':'https://packageinserts.bms.com/sds/sds_zeposia.pdf','identity_excerpt':'For ozanimod hydrochloride: RPC1063','supports_aliases':['RPC1063'],'source_kind':'official_manufacturer_safety_data_sheet_search_index_excerpt'},
'deucravacitinib':{'url':'https://news.bms.com/news/corporate-financial/2020/Bristol-Myers-Squibb-Announces-Deucravacitinib-BMS-986165-Demonstrated-Superiority-to-Placebo-and-Otezla-apremilast-in-Pivotal-Phase-3-Psoriasis-Study/default.aspx','identity_excerpt':'Deucravacitinib (BMS-986165)','supports_aliases':['BMS-986165'],'source_kind':'official_manufacturer_publication_identity_only_search_index_excerpt'},
'camizestrant':{'url':'https://www.astrazeneca.com/content/dam/az/PDF/2021/q1-2021/Q1_2021_results_clinical_trials_appendix.pdf','identity_excerpt':'Camizestrant (AZD9833, oral SERD)','supports_aliases':['AZD9833'],'source_kind':'official_manufacturer_trials_appendix_search_index_excerpt'}}
# Temporal/registry-role exceptions backed by original identities; this is named-product exposure, not causal-effect attribution.
NOTES={
'NCT01602380':'Double dummy: active Faslodex500mg is fulvestrant; arimidex dummy and faslodex dummy are placebo, active arimidex is comparator.',
'NCT01740427':'Repeated separate letrozole intervention entries occur in both arms; aggregate identical identities before declaring an experimental-only core.',
'NCT02031276':'Placebo arm crosses to risankizumab in later periods. Initial randomized target is risankizumab; shared later exposure is not baseline SOC.',
'NCT02367794':'Named target is atezolizumab; paclitaxel/nab-paclitaxel are chemotherapy backbone variants. This does not claim the armA-versusC contrast isolates atezolizumab or makes the backbones equivalent.',
'NCT02574598':'Docetaxel control crosses to pembrolizumab after progression. Principal contrast targets adding MK3475; later crossover must not erase target identity.',
'NCT02625610':'Both arms typed experimental, but official title and summary explicitly compare maintenance avelumab against continuing first-line chemotherapy.',
'NCT02625623':'Registry labels avelumab active comparator and physician chemotherapy experimental. Official summary explicitly names avelumab superiority comparison; role override is documented.',
'NCT03517722':'All assigned groups include placebo crossover; named target remains ustekinumab, and placebo is never a core.',
'NCT03631199':'Safety run-in and randomized parts coexist; randomized placebo+pembro+chemotherapy is typedOTHER. Principal named target is added canakinumab; pembrolizumab/chemotherapy are shared backbone.',
'NCT03822351':'Durvalumab monotherapy arm explicitly labeled Control Arm despite experimental armType. Novel combination cores are oleclumab and monalizumab; durvalumab is comparator backbone here.',
'NCT03889639':'SAR442168 is dose-response target; placebo and MRI contrast agent are not therapeutic cores.',
'NCT06254950':'Placebo participants receive TAK279 after induction. Primary induction contrast targets TAK279; subsequent active treatment is not shared baseline SOC.'}
BROAD={'antipdl1','antipd1','pdl1','pd1','antiinterleukin23','btkinhibitor','tyk2inhibitor','monoclonalantibody'}
def placebo(s):return bool(re.search(r'placebo|dummy|normal saline|saline solution',s,re.I))
def fit_alias(s,component):
 return any(norm(a) and norm(a) in norm(s) for a in [component]+SEEDS.get(component,[]))
def main():
 seenp=DATA/'seen_inventory.json';seen=json.loads(seenp.read_text());idxp=IDENTITY/'identity_source_index.json';idx=json.loads(idxp.read_text());assert sha(seenp.read_bytes())==idx['seen_inventory_source']['sha256'];index={r['nct_id']:r for r in idx['identity_records']};targets={}
 for component,ids in GROUPS.items():
  for number in ids.split():targets.setdefault('NCT'+number,[]).append(component)
 assert set(targets)==set(seen['nct_ids'])==set(index) and len(targets)==69
 ext_sources={}
 for component,e in EXTERNAL.items():
  p=IDENTITY/'identity_alias_sources'/(norm(component)+'.json');record=e|{'captured_at_utc':datetime.now(timezone.utc).isoformat(),'capture_method':'Official primary source identity excerpt returned by web search; not full text download','full_document_archived':False,'source_role':'Drug identity equivalence only, not historical features or efficacy outcomes'};atomic(p,record);ext_sources[component]=desc(p)
 records=[];alias_evidence={c:{} for c in GROUPS};quarantine_edges=[];unknown=[]
 for n in seen['nct_ids']:
  ir=index[n];p=ROOT/ir['projection_path'];assert sha(p.read_bytes())==ir['projection_sha256'];j=json.loads(p.read_text());rawsource=j.get('source');
  if rawsource:assert sha((ROOT/rawsource['path']).read_bytes())==rawsource['sha256']
  target=targets[n];arms=j.get('arms_interventions',{}).get('armGroups',[]);interventions=j.get('arms_interventions',{}).get('interventions',[]);types={a['label']:a.get('type') for a in arms};role_by_name={}
  for iv in interventions:role_by_name.setdefault(norm(iv['name']),set()).update(types.get(a) for a in iv.get('armGroupLabels',[]))
  found={c:[] for c in target};excluded=[];ambiguous=[];alias_rejections=[]
  for i,iv in enumerate(interventions):
   name=iv['name'];base='/arms_interventions/interventions/'+str(i);strings=[name]+iv.get('otherNames',[])
   if placebo(name):excluded.append({'index':i,'name':name,'role':'placebo_or_double_dummy','source_pointer':base});continue
   hits=[c for c in target if any(fit_alias(s,c) for s in strings)]
   if hits:
    for c in hits:
     found[c].append({'intervention_index':i,'name':name,'source_pointer':base,'raw_arm_roles':sorted(t for t in role_by_name[norm(name)] if t)})
   else:
    roles=role_by_name[norm(name)];control=bool(roles&{'ACTIVE_COMPARATOR','PLACEBO_COMPARATOR','SHAM_COMPARATOR','NO_INTERVENTION'});experimental='EXPERIMENTAL' in roles
    if re.search('contrast medium|magnetic resonance|radiation|best supportive|standard of care',name,re.I) or iv.get('type') not in ['DRUG','BIOLOGICAL']:
     role='supportive_diagnostic_or_non_drug'
    elif control:role='shared_background' if experimental else 'active_comparator'
    elif n in NOTES:role='curated_background_or_control_in_named_target_contrast'
    else:role='unresolved_noncore_role';ambiguous.append({'index':i,'name':name,'raw_roles':sorted(t for t in roles if t)})
    excluded.append({'index':i,'name':name,'role':role,'source_pointer':base,'role_evidence_pointer':'/identification/officialTitle' if n in NOTES else base+'/armGroupLabels'})
  missing=[c for c,v in found.items() if not v];resolved=not missing and not ambiguous and not j.get('status')=='identity_retrieval_failed'
  if not resolved:unknown.append(n)
  status='resolved_curated_role_exception' if resolved and n in NOTES else 'resolved_named_target_identity' if resolved else 'unknown'
  record={'nct_id':n,'status':status,'component_ids':target if resolved else [],'possible_component_ids':target if not resolved else [],'identity_projection':{'path':ir['projection_path'],'sha256':ir['projection_sha256']},'identity_source':rawsource,'in_union':ir['in_current_union'],'component_evidence':[{'component_id':c,'matched_interventions':found[c],'target_role_pointers':['/identification/briefTitle','/identification/officialTitle','/brief_summary'],'external_identity_source':ext_sources.get(c)} for c in target],'excluded_interventions':excluded,'ambiguous_interventions':ambiguous,'role_note':NOTES.get(n),'missing_target_matches':missing,'current_identity_only':True,'used_for_historical_X':False,'outcome_used':False,'agent_curated':True,'human_review_complete':False,'independent_review_required':True,'generic_alias_rejections':alias_rejections,'quarantine_exact_nct':True}
  records.append(record)
  if resolved:
   quarantine_edges.extend({'nct_id':n,'component_id':c,'role':'named_investigational_core_for_exposure_quarantine','projection_sha256':ir['projection_sha256'],'resolution_status':status} for c in target)
 # Build molecule aliases from all69identity records, including background mentions.
 # Alias identity evidence never creates a target-exposure edge by itself.
 alias_rejections_global=[]
 for n in seen['nct_ids']:
  ir=index[n];j=json.loads((ROOT/ir['projection_path']).read_text())
  for i,iv in enumerate(j.get('arms_interventions',{}).get('interventions',[])):
   if placebo(iv['name']):continue
   texts=[(iv['name'],'/arms_interventions/interventions/'+str(i)+'/name')]+[(a,'/arms_interventions/interventions/'+str(i)+'/otherNames/'+str(k)) for k,a in enumerate(iv.get('otherNames',[]))]
   owners={c for c in GROUPS if any(fit_alias(text,c) for text,pointer in texts)}
   for raw,pointer in texts:
    hits=[c for c in GROUPS if fit_alias(raw,c)]
    owner=next(iter(owners)) if len(owners)==1 else hits[0] if len(hits)==1 else None
    if owner is None:continue
    candidates=[raw]+([part.strip() for part in raw.split(';')] if ';' in raw else [])
    for part in re.findall(r'\(([^()]*)\)',raw):
     if re.fullmatch(r'[A-Za-z]{2,8}[- ]?\d{3,10}[A-Za-z]?',part.strip()):candidates.append(part.strip())
    for alias in candidates:
     if not alias or placebo(alias) or norm(alias) in BROAD:
      alias_rejections_global.append({'nct_id':n,'alias':alias,'pointer':pointer,'reason':'placebo/dummy or non-specific pharmacologic target/class'});continue
     evidence={'nct_id':n,'projection_path':ir['projection_path'],'projection_sha256':ir['projection_sha256'],'pointer':pointer,'derivation':'Single explicitly identity-linked molecule in this intervention/name-or-otherNames; parenthetical molecule codes split; combination labels never assigned wholesale','alias_identity_only_not_target_edge':True}
     alias_evidence[owner].setdefault(alias,[]).append(evidence)
     # The canonical generic token occurs directly in this name, or is linked by the external identity source.
     if norm(owner) in norm(raw):
      alias_evidence[owner].setdefault(owner,[]).append(evidence|{'derivation':'Canonical molecule token in explicit registry name/otherNames; dose/route decorations omitted'})
     elif owner in ext_sources:
      alias_evidence[owner].setdefault(owner,[]).append(evidence|{'derivation':'Manufacturer generic-code/brand identity equivalence linked to explicit current registry code/brand','external_identity_source':ext_sources[owner]})
 for c in GROUPS:
  assert c in alias_evidence[c], 'Unsupported canonical identity '+c
 components=[]
 for c,evidence in sorted(alias_evidence.items()):
  components.append({'component_id':c,'canonical_name':c,'normalization_key':norm(c),'aliases':sorted(evidence),'normalized_aliases':sorted({norm(a) for a in evidence}),'alias_evidence':[{'alias':a,'evidence':v} for a,v in sorted(evidence.items())],'seen_ncts':sorted(n for n,cs in targets.items() if c in cs),'external_identity_source':ext_sources.get(c)})
 aliasowners={}
 for c in components:
  for a in c['normalized_aliases']:aliasowners.setdefault(a,set()).add(c['component_id'])
 collisions=[{'normalized_alias':a,'components':sorted(cs)} for a,cs in aliasowners.items() if len(cs)>1]
 legacy={a:[part.strip() for part in a.split('+') if part.strip() in GROUPS] for a in seen['asset_groups']}
 seed_links=[]
 for component,aliases in SEEDS.items():
  for alias in aliases:
   proofs=[]
   for n in seen['nct_ids']:
    ir=index[n];j=json.loads((ROOT/ir['projection_path']).read_text())
    for i,iv in enumerate(j.get('arms_interventions',{}).get('interventions',[])):
     if placebo(iv['name']):continue
     texts=[iv['name']]+iv.get('otherNames',[])
     if any(norm(component) in norm(t) for t in texts) and any(norm(alias) in norm(t) for t in texts):proofs.append({'nct_id':n,'projection_path':ir['projection_path'],'projection_sha256':ir['projection_sha256'],'pointer':'/arms_interventions/interventions/'+str(i),'method':'Generic and code/brand explicitly coexist in same registered intervention'})
    for field,value in j.get('identification',{}).items():
     if isinstance(value,str) and norm(component) in norm(value) and norm(alias) in norm(value):proofs.append({'nct_id':n,'projection_path':ir['projection_path'],'projection_sha256':ir['projection_sha256'],'pointer':'/identification/'+field,'method':'Generic and code/brand explicitly coexist in registered title'})
   if component in ext_sources and norm(alias) in {norm(a) for a in EXTERNAL[component]['supports_aliases']}:proofs.append({'external_identity_source':ext_sources[component],'method':'Primary manufacturer explicitly identifies generic-code/brand equivalence; excerpt capture is not full-document archive'})
   assert proofs,'Seed synonym lacks primary identity evidence: '+component+' '+alias
   seed_links.append({'component_id':component,'alias':alias,'evidence':proofs})
 out={'schema_version':'derived-seen-component-manifest1','created_at_utc':datetime.now(timezone.utc).isoformat(),'status':'agent_curated_identity_manifest_requires_independent_review','source_seen_inventory':desc(seenp),'identity_source_index':desc(idxp),'membership_reference':desc(DATA/'union_universe.json'),'curation_script':desc(Path(__file__)),'scope':{'expected_ncts':69,'records':len(records),'identity_available':sum(bool(r['identity_source']) for r in records),'seen_in_union':sum(r['in_union'] for r in records),'outside_union':sum(not r['in_union'] for r in records),'resolved_ncts':69-len(unknown),'unknown_ncts':len(unknown),'molecule_cores':len(components),'nct_component_edges':len(quarantine_edges)},'records':records,'components':components,'nct_component_edges':quarantine_edges,'unresolved_seen_ncts':unknown,'alias_collisions':collisions,'rejected_non_specific_aliases':alias_rejections_global,'legacy_asset_group_to_components':legacy,'canonical_seed_alias_links':seed_links,'quarantine_policy':{'exact_seen_nct_always_quarantined':True,'component_overlap_quarantine':'Any shared named investigational core through a source-backed canonical alias. Combinations contribute each core, never one synthetic independent combination asset.','never_union_on':'Placebo/dummy, generic target/class aliases, SOC/background/active comparators in that trial','unknown_rule':'Unknown identity/target role never becomes a unique independent asset; retain unresolved audit and block unsupported independence claim','current_registry_role':'Identity and exposure guard only; no historical design covariates or labels','role_warning':'Raw armType can describe crossover/dose/registry naming rather than the principal target contrast; curated exceptions are explicit and require independent review','not_claimed':'No clinical efficacy, causal contrast isolation, historical availability or human double adjudication; identity sources may also report results but those labels/effects are not derivation inputs'},'clinical_prediction_validated':False,'outcome_labels_or_metrics_used_in_derivation':False,'human_review_count':0}
 path=DATA/'derived_seen_component_manifest.json';atomic(path,out)
 files=[idxp,IDENTITY/"continuous_seen_identity.py"]+[ROOT/r['projection_path'] for r in idx['identity_records']]+[ROOT/r['source']['path'] for r in idx['identity_records'] if r.get('source')]+[ROOT/s['path'] for s in ext_sources.values()]+[Path(__file__),path]
 unique=sorted(set(files));atomic(DATA/'derived_seen_component_source_manifest.json',{'source_seen_inventory':desc(seenp),'artifacts':[desc(p) for p in unique]})
 print(json.dumps({'manifest_sha256':sha(path.read_bytes()),'scope':out['scope'],'unknown':unknown,'alias_collisions':collisions,'rejected_non_specific_aliases':alias_rejections_global,'source_inventory_unchanged':sha(seenp.read_bytes())==idx['seen_inventory_source']['sha256']}),flush=True)
if __name__=='__main__':main()
