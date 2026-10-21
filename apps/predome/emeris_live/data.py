import csv
import io
import re
from collections import Counter

FIELDS = {'subject_id': '研究编号', 'age': '年龄（年）', 'sex': '性别', 'stage': '分期原值', 'os_months': '总生存随访（月）', 'os_status': '总生存状态原值'}
BASE = {'PATIENT_ID': 'subject_id', 'SUBJECT_ID': 'subject_id', 'AGE': 'age', 'SEX': 'sex', 'AJCC_PATHOLOGIC_TUMOR_STAGE': 'stage', 'OS_MONTHS': 'os_months', 'OS_STATUS': 'os_status'}
MISSING = {'', 'NA', 'N/A', 'NAN', '[NOT AVAILABLE]', '[NOT APPLICABLE]', 'NOT_REPORTED', '[UNKNOWN]', 'UNKNOWN'}
FORBIDDEN = re.compile(r'(^|[_\s])(name|phone|email|address|dob|birth_date|mrn|id_card)($|[_\s])|姓名|电话|身份证|住院号|门诊号|出生日期|地址|邮箱', re.I)

def parse(raw):
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError('请提供 UTF-8 编码的 CSV 或 TSV。')
    lines = [x for x in text.splitlines() if not x.startswith('#') and x.strip()]
    if len(lines) < 2:
        raise ValueError('数据必须包含表头和记录。')
    delim = '\t' if '\t' in lines[0] else ','
    reader = csv.reader(io.StringIO('\n'.join(lines)), delimiter=delim)
    headers = next(reader)
    if len(headers) > 50 or len(set(headers)) != len(headers) or any(not h or len(h)>100 for h in headers):
        raise ValueError('表头重复、为空或超过限制。')
    if any(FORBIDDEN.search(h) for h in headers):
        raise ValueError('发现直接身份字段。请先在机构内去标识化，仅保留研究编号。')
    rows=[]
    for row in reader:
        if len(row)!=len(headers) or any(len(v)>500 for v in row):
            raise ValueError('记录列数或单元格长度不符合要求。')
        rows.append(dict(zip(headers,row)))
        if len(rows)*len(headers)>100000:
            raise ValueError('单份数据最多 100,000 个单元格；请按研究批次拆分。')
        if len(rows)>10000:
            raise ValueError('首版每份文件最多 10,000 条记录。')
    return headers, rows

def suggested(headers, aliases):
    mapping={h: aliases[h.upper()] for h in headers if h.upper() in aliases}
    duplicates={v for v in mapping.values() if list(mapping.values()).count(v)>1}
    return {h:v for h,v in mapping.items() if v not in duplicates}

def summarize(headers, rows, mapping):
    if not mapping or any(k not in headers or v not in FIELDS for k,v in mapping.items()):
        raise ValueError('字段映射不正确。')
    if len(set(mapping.values()))!=len(mapping.values()) or 'subject_id' not in mapping.values():
        raise ValueError('必须有且仅有一个研究编号字段；同一目标字段不能重复。')
    counts=Counter(); missing=Counter(); invalid=Counter(); identities=[]; ages=[]
    stages=Counter(); sexes=Counter(); events=Counter()
    for row in rows:
        for source,target in mapping.items():
            value=row[source].strip()
            if value.upper() in MISSING:
                missing[target]+=1
                continue
            counts[target]+=1
            if target=='subject_id':
                identities.append(value)
                if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,64}',value): invalid[target]+=1
            elif target in ('age','os_months'):
                try:
                    number=float(value)
                    if not 0<=number<=(120 if target=='age' else 1500): raise ValueError()
                    if target=='age': ages.append(number)
                except ValueError:
                    invalid[target]+=1
            elif target=='stage': stages[value]+=1
            elif target=='sex': sexes[value]+=1
            elif target=='os_status': events[value]+=1
    duplicate_count=len(identities)-len(set(identities))
    issues=[]
    if duplicate_count: issues.append(f'研究编号重复 {duplicate_count} 条；不要按行数当作独立患者数。')
    if missing['subject_id']: issues.append(f'缺失研究编号 {missing["subject_id"]} 条。')
    if invalid: issues.append('存在类型或范围异常，请核对原始资料。')
    return {'rows':len(rows),'unique_subjects':len(set(identities)), 'duplicate_subject_rows':duplicate_count,
            'missing':dict(missing),'invalid':dict(invalid),'nonmissing':dict(counts), 'issues':issues,
            'age_mean':sum(ages)/len(ages) if ages else None,'stage_counts':dict(stages), 'sex_counts':dict(sexes),
            'status_counts':dict(events), 'scope':'descriptive_research_only',
            'note':'分期和生存状态保留来源原值；未估计生存概率、药物收益或个人治疗建议。'}
