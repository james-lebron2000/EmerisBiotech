"""Minimal, owner-previewed metadata for explicitly consented public evaluation.

Values are declarations copied from saved inputs, not medically validated classes.
Unknown assets stay unknown; trial IDs must never become synthetic asset identities.
"""
from copy import deepcopy

SCHEMA_VERSION='predome.public_evaluation_metadata.v1'
CLASSIFICATION_OPTIONS={
    'therapeutic_area':['Oncology','Immunology','Neurology','Metabolic Diseases','Cardiovascular','Infectious Diseases','Rare Diseases','Other','Unknown'],
    'sponsor_category':['large_pharma','biotech','academic','government','other','unknown']}
DEFAULT_CLASSIFICATION={'therapeutic_area':'Unknown','sponsor_category':'unknown'}
SAFE_KEYS=('schema_version','asset','phase','modality','design','randomized','therapeutic_area','sponsor_category',
           'field_sources','classification_source','clinical_classification_validated')


def classification(value=None):
    if value is None:return dict(DEFAULT_CLASSIFICATION)
    if not isinstance(value,dict) or set(value)-set(CLASSIFICATION_OPTIONS):raise ValueError('Only therapeutic_area and sponsor_category classifications are accepted')
    result=dict(DEFAULT_CLASSIFICATION)
    for key,item in value.items():
        if item not in CLASSIFICATION_OPTIONS[key]:raise ValueError('Invalid evaluation classification: '+key)
        result[key]=item
    return result


def _text(value,maximum):
    transformation='literal_value'
    if isinstance(value,list) and len(value)<=8 and all(isinstance(x,str) for x in value):
        value='; '.join(value);transformation='joined_source_values'
    if not isinstance(value,str):return None,'missing_or_unsupported_value'
    value=value.strip()
    if not value or value.casefold() in ('unknown','n/a','not provided','none','null'):return None,'unknown_value'
    if len(value)>maximum:return None,'oversized_value_withheld'
    return value,transformation


def preview_metadata(input_snapshot,evaluation_classification=None):
    """Exact whitelist projection; never include free-form protocol or source IDs."""
    selected=classification(evaluation_classification)
    profile=input_snapshot.get('profile') if isinstance(input_snapshot,dict) else None
    fields=profile.get('fields') if isinstance(profile,dict) else None
    structured=isinstance(fields,dict)
    fields=fields if structured else input_snapshot
    if not isinstance(fields,dict):fields={}
    output={};sources={}
    for key,aliases,maximum in (('asset',('drug','asset'),200),('phase',('phase',),120),
                                 ('modality',('modality',),120),('design',('design','trial_design'),300)):
        name=next((name for name in aliases if name in fields),aliases[0])
        supplied=fields.get(name)
        if structured:
            supplied=supplied if isinstance(supplied,dict) else {}
            raw=supplied.get('value');origin=supplied.get('origin')
            origin=origin if origin in ('public','user','inferred','missing') else 'unknown'
            confirmed=supplied.get('confirmed') is True
        else:raw=supplied;origin='legacy_flat_saved_input';confirmed=False
        transform=None
        if key=='design' and isinstance(raw,dict):
            design=raw.get('designInfo',raw)
            if isinstance(design,dict):
                raw='; '.join(design[k] for k in ('allocation','interventionModel','primaryPurpose') if isinstance(design.get(k),str))
                transform='joined_whitelisted_design_fields'
        value,normalization=_text(raw,maximum)
        output[key]=value
        sources[key]={'input_path':('input_snapshot.profile.fields.'+name+'.value') if structured else 'input_snapshot.'+name,
                      'origin':origin,'confirmed':confirmed,'transformation':transform or normalization}
    tokens={part.strip().upper() for part in (output['design'] or '').split(';')}
    randomized=True if 'RANDOMIZED' in tokens else False if 'NON_RANDOMIZED' in tokens else None
    sources['randomized']={'input_path':sources['design']['input_path'],'origin':'literal_allocation_token' if randomized is not None else 'unknown',
                           'confirmed':sources['design']['confirmed'],'transformation':'exact_allocation_token_only'}
    for key in CLASSIFICATION_OPTIONS:
        sources[key]={'input_path':'lock.evaluation_classification.'+key,'origin':'user_classification',
                      'confirmed':False,'transformation':'enumerated_user_selection_not_medical_validation'}
    return {'schema_version':SCHEMA_VERSION,**output,'randomized':randomized,**selected,
            'field_sources':sources,'classification_source':'user_selected_not_medically_validated',
            'clinical_classification_validated':False}


def published_metadata(snapshot):
    if snapshot.get('published') is not True or snapshot.get('evaluation_metadata_published') is not True:return None
    metadata=snapshot.get('evaluation_metadata')
    if not isinstance(metadata,dict) or metadata.get('schema_version')!=SCHEMA_VERSION:return None
    # The frozen object is server-generated. Whitelist again at the public boundary.
    return {key:deepcopy(metadata.get(key)) for key in SAFE_KEYS}


def evaluation_fields(snapshot):
    """Public scoring metadata must never fall back to a private saved profile."""
    metadata=published_metadata(snapshot)
    if metadata is not None:provenance='explicit_owner_consent_frozen_metadata'
    elif snapshot.get('published') is True and snapshot.get('provenance')=='archived_record_import':
        archived=snapshot.get('input_snapshot') or {}
        metadata=preview_metadata(archived)
        for key in ('therapeutic_area','sponsor_category'):
            value,transformation=_text(archived.get(key),120)
            metadata[key]=value
            metadata['field_sources'][key]={'input_path':'input_snapshot.'+key,'origin':'previously_public_archive',
                                            'confirmed':False,'transformation':transformation}
        if metadata.get('randomized') is None and type(archived.get('randomized')) is bool:
            metadata['randomized']=archived['randomized']
        metadata['classification_source']='existing_public_archive_metadata_only'
        provenance='archived_public_input_fields'
    else:
        metadata={'asset':None,'phase':None,'modality':None,'design':None,'randomized':None,
                  'therapeutic_area':None,'sponsor_category':None}
        provenance='not_authorized_or_not_available'
    return {'asset':metadata.get('asset') or 'unknown',
            **{key:metadata.get(key) for key in ('phase','modality','design','randomized','therapeutic_area','sponsor_category')},
            'evaluation_metadata_provenance':provenance,
            'clinical_classification_validated':False}
