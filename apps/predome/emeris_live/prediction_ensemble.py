"""Deterministic descriptive ensemble of two fully bound provider outputs.

This is not a trained/calibrated model or an additional performance-series model.
The caller supplies the expected hashes of the actual requests sent to providers.
"""
import hashlib
import json
import math
from datetime import datetime, timezone

MEMBERS=('deepseek-flash','deepseek-v4-pro')
POLICY_VERSION='equal-weight-two-model.v1'
POLICY={'policy_version':POLICY_VERSION,'algorithm':'arithmetic_mean','members':list(MEMBERS),
        'weights':{model:0.5 for model in MEMBERS},'required_method':'llm',
        'required_member_status':'predicted','missing_member_policy':'unavailable_no_partial_average',
        'unexpected_member_policy':'unavailable',
        'binding':'exact_target_canonical_input_current_system_prompt_and_expected_request_hash',
        'calibration':'none','evaluation_scope':'descriptive_only_excluded_from_model_performance'}


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


POLICY_SHA256=digest(POLICY)


def valid_sha(value):
    return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)


def probability(value):
    return type(value) in (int,float) and math.isfinite(value) and 0<=value<=1


def build_ensemble(*,method,forecasts,packet,prompt_sha256,request_sha256_by_model,generated_at_utc):
    target=packet.get('target') if isinstance(packet,dict) else None
    try:input_hash=digest(packet);target_hash=digest(target)
    except (ValueError,TypeError):input_hash=target_hash=None
    result={'status':'unavailable','probability':None,'policy_version':POLICY_VERSION,'policy_sha256':POLICY_SHA256,
            'target_sha256':target_hash,'input_sha256':input_hash,'data_cutoff':packet.get('data_cutoff') if isinstance(packet,dict) else None,
            'members':[],'generated_at_utc':generated_at_utc,'reason':'','reason_codes':[],
            'clinical_prediction_validated':False,'evaluation_scope':'descriptive_only_excluded_from_model_performance',
            'uncertainty_kind':'model_range_not_confidence_interval','model_range':None}
    if method!='llm':
        return result|{'status':'not_applicable','reason':'当前方法不是双模型临床研究预测；条件统计计算与证据梳理不参与集成。','reason_codes':['method_not_llm']}
    errors=[]
    if isinstance(forecasts,dict) and set(forecasts)-set(MEMBERS):errors.append('unexpected_ensemble_member')
    if not isinstance(target,dict) or not all(isinstance(target.get(k),str) and target[k].strip() for k in ('endpoint','population','comparison','success_definition')):
        errors.append('invalid_target')
    if not valid_sha(input_hash):errors.append('invalid_canonical_packet')
    if not valid_sha(prompt_sha256):errors.append('invalid_expected_prompt_hash')
    try:
        generated=datetime.fromisoformat(generated_at_utc.replace('Z','+00:00'))
        if generated.tzinfo is None or generated.utcoffset()!=timezone.utc.utcoffset(generated):raise ValueError('UTC required')
    except (ValueError,TypeError,AttributeError):errors.append('invalid_generation_timestamp')
    values=[]
    for model in MEMBERS:
        component=forecasts.get(model) if isinstance(forecasts,dict) else None
        member={'model':model,'model_version':None,'returned_model':None,'status':'missing','probability':None,'weight':.5,
                'input_sha256':None,'request_sha256':None,'prompt_sha256':None,'component_sha256':None}
        if not isinstance(component,dict):
            errors.append(model+':missing_member');result['members'].append(member);continue
        p=component.get('probability')
        member.update(model_version=component.get('model_version') if isinstance(component.get('model_version'),str) else None,
                      returned_model=component.get('returned_model') if isinstance(component.get('returned_model'),str) else None,
                      status=component.get('status') if isinstance(component.get('status'),str) else 'invalid',
                      probability=p if probability(p) else None)
        for key in ('input_sha256','request_sha256','prompt_sha256'):
            member[key]=component.get(key) if valid_sha(component.get(key)) else None
        try:member['component_sha256']=digest(component)
        except (ValueError,TypeError):errors.append(model+':noncanonical_component')
        if component.get('status')!='predicted':errors.append(model+':member_not_predicted')
        if not probability(p):errors.append(model+':invalid_probability')
        if not member['model_version'] or not member['model_version'].strip():errors.append(model+':missing_model_version')
        if not isinstance(target,dict) or component.get('target_definition')!=target.get('success_definition'):
            errors.append(model+':target_mismatch')
        if member['input_sha256']!=input_hash or not valid_sha(member['input_sha256']):errors.append(model+':input_binding_mismatch')
        if member['prompt_sha256']!=prompt_sha256 or not valid_sha(member['prompt_sha256']):errors.append(model+':prompt_binding_mismatch')
        expected=request_sha256_by_model.get(model) if isinstance(request_sha256_by_model,dict) else None
        if not valid_sha(expected) or member['request_sha256']!=expected:errors.append(model+':request_binding_mismatch')
        result['members'].append(member)
        if probability(p):values.append(p)
    result['reason_codes']=errors
    if errors:
        labels={'unexpected_ensemble_member':'存在固定政策以外的模型成员','missing_member':'缺少指定成员','member_not_predicted':'成员未完成有效预测（拒答、失败或未启用）',
                'invalid_probability':'概率不是有限的零到一数值','missing_model_version':'缺少模型版本',
                'target_mismatch':'成功标准不一致','input_binding_mismatch':'输入哈希不匹配',
                'prompt_binding_mismatch':'提示词哈希不匹配','request_binding_mismatch':'请求哈希不匹配',
                'noncanonical_component':'成员输出无法生成规范摘要','invalid_target':'完整目标缺失',
                'invalid_canonical_packet':'输入无法生成规范摘要','invalid_expected_prompt_hash':'当前提示词摘要无效',
                'invalid_generation_timestamp':'生成时间不是有效 UTC 时间'}
        details=[]
        for code in errors:
            model,separator,key=code.partition(':')
            details.append((model+'：' if separator else '')+labels.get(key if separator else model,code))
        result['reason']='双模型集成不可用：'+'；'.join(details)+'。不会以剩余成员代替。'
        return result
    result.update(status='available',probability=math.fsum(values)/2,
                  model_range={'min':min(values),'max':max(values),'spread':max(values)-min(values)},
                  reason='两个指定模型通过目标与输入绑定校验，按预先固定的 50% / 50% 权重计算。该描述性集成未经临床校准；成员范围不是置信区间。')
    return result
