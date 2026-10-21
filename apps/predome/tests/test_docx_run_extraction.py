"""Real DOCX XML formatting must not create artificial field boundaries."""
from io import BytesIO
import zipfile
import pytest
from emeris_live.prediction_service import parse_document

NS='http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def document(body):
    out=BytesIO()
    with zipfile.ZipFile(out,'w') as archive:
        archive.writestr('word/document.xml',f'<w:document xmlns:w="{NS}"><w:body>{body}</w:body></w:document>')
    return out.getvalue()


def run(text):return '<w:r><w:t xml:space="preserve">'+text+'</w:t></w:r>'

def paragraph(*runs):return '<w:p>'+''.join(runs)+'</w:p>'


def test_formatting_split_labels_and_endpoint_runs_remain_one_field():
    raw=document(paragraph(run('Primary '),'<w:r><w:rPr><w:b/></w:rPr><w:t>End</w:t></w:r>',run('point: Progres'),run('sion-free '),run('survival'))+paragraph(run('Comparator: Standard care')))
    result=parse_document(raw,'formatted.docx')
    field=result['fields']['primary_endpoint']
    assert field['value']=='Progression-free survival'
    assert field['origin']=='inferred' and field['confirmed'] is False
    assert result['fields']['comparator']['value']=='Standard care'
    assert 'Primary Endpoint: Progression-free survival\nComparator:' in result['text_preview']


def test_chinese_split_label_and_hyperlink_runs_are_preserved():
    raw=document(paragraph(run('主要'),run('终点：'),'<w:hyperlink>'+run('总生')+'</w:hyperlink>',run('存期')))
    result=parse_document(raw,'formatted.docx')
    assert result['fields']['primary_endpoint']['value']=='总生存期'


def test_table_cells_and_paragraphs_do_not_merge_adjacent_fields():
    raw=document('<w:tbl><w:tr><w:tc>'+paragraph(run('Primary End'),run('point: Overall survival'))+'</w:tc><w:tc>'+paragraph(run('Comparator: Placebo'))+'</w:tc></w:tr><w:tr><w:tc>'+paragraph(run('Study Drug: Example drug'))+'</w:tc></w:tr></w:tbl>')
    result=parse_document(raw,'table.docx')
    assert result['fields']['primary_endpoint']['value']=='Overall survival'
    assert result['fields']['comparator']['value']=='Placebo'
    assert result['fields']['drug']['value']=='Example drug'
    assert 'Overall survival\n\nComparator' in result['text_preview']


@pytest.mark.parametrize('boundary',['</w:p><w:p>','</w:p></w:tc><w:tc><w:p>'])
def test_label_without_value_never_consumes_next_paragraph_or_cell(boundary):
    raw=document('<w:tbl><w:tr><w:tc><w:p>'+run('Primary Endpoint:')+boundary+run('Comparator: Placebo')+'</w:p></w:tc></w:tr></w:tbl>')
    result=parse_document(raw,'missing-value.docx')
    assert result['fields']['primary_endpoint']['value'] is None
    assert result['fields']['comparator']['value']=='Placebo'


def test_explicit_line_breaks_carriage_returns_and_tabs_are_not_lost():
    raw=document(paragraph(run('Primary Endpoint:'),'<w:r><w:tab/></w:r>',run('PFS'),'<w:r><w:br/></w:r>',run('Comparator: Placebo'),'<w:r><w:cr/></w:r>',run('Study Drug: Example drug')))
    result=parse_document(raw,'controls.docx')
    assert 'Primary Endpoint:\tPFS\nComparator: Placebo\nStudy Drug: Example drug' in result['text_preview']
    assert result['fields']['primary_endpoint']['value']=='PFS'
    assert result['fields']['comparator']['value']=='Placebo'
    assert result['fields']['drug']['value']=='Example drug'


def test_explicit_line_break_after_empty_label_does_not_invent_value():
    raw=document(paragraph(run('Primary Endpoint:'),'<w:r><w:br/></w:r>',run('Comparator: Placebo')))
    result=parse_document(raw,'controls.docx')
    assert result['fields']['primary_endpoint']['value'] is None
    assert result['fields']['comparator']['value']=='Placebo'


def test_deleted_word_runs_are_not_visible_field_content():
    raw=document(paragraph(run('Primary Endpoint: '),'<w:del>'+run('obsolete endpoint')+'</w:del>',run('Overall survival')))
    assert parse_document(raw,'tracked.docx')['fields']['primary_endpoint']['value']=='Overall survival'


def test_tab_separated_fields_in_one_paragraph_do_not_mix_values():
    raw=document(paragraph(run('Primary Endpoint: PFS'),'<w:r><w:tab/></w:r>',run('Comparator: Placebo')))
    result=parse_document(raw,'tab-separated.docx')
    assert result['fields']['primary_endpoint']['value']=='PFS'
    assert result['fields']['comparator']['value']=='Placebo'
    assert 'PFS\tComparator:' in result['text_preview']
