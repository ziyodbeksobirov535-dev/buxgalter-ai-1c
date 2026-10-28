import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from services_ehf import reconcile, ENTITIES, compare_items
from services_1c import ODataError

IN, OUT = ENTITIES['in'][0], ENTITIES['out'][0]
ORG = '11111111-1111-1111-1111-111111111111'


def invoice(flow='in', status='signed'):
    return {'id':'synthetic-ehf', 'number':'7', 'date':'2026-01-10', 'total':112,
            'flow':flow, 'ehf_status':status, 'ehf_status_label':status,
            'counterparty_inn':'000000001', 'counterparty_name':'Synthetic partner',
            'items':[{'name':'Synthetic product','ikpu':'00000000000000000','qty':2,'total':112,'vat_sum':12}]}


def document(entity=IN):
    return {'Ref_Key':'synthetic-doc','_entity':entity,'Организация_Key':ORG,
            'НомерВходящегоДокумента':'0007', 'Number':'SYS-123', 'ДатаВходящегоДокумента':'2026-01-10',
            'Date':'2026-01-10T12:00:00','СуммаДокумента':112,'Контрагент_Key':'synthetic-cp',
            'Posted':True,'DeletionMark':False,'СуммаВключаетНДС':True,
            'Товары':[{'Номенклатура_Key':'synthetic-product','Количество':2,'Сумма':112,'СуммаНДС':12}]}


class Transport:
    def __init__(self, docs, fail=None): self.docs=docs;self.calls=[];self.fail=fail
    async def query(self, entity, conds, top, skip, orderby):
        self.calls.append((entity, conds, top, skip, orderby))
        if entity==self.fail: raise ODataError('Synthetic API unavailable')
        rows=[d for d in self.docs if d['_entity']==entity]
        return rows[skip:skip+top]
    async def get(self, entity, key):
        if key=='synthetic-cp':return {'ИНН':'000000001','Description':'Synthetic partner'}
        return {'Description':'Synthetic product','КодИКПУ':'00000000000000000'}


class OneC:
    mode='live'
    def __init__(self, docs, fail=None):self.t=Transport(docs,fail)
    async def _metadata(self):return {'sets':{IN,OUT}}
    async def organization(self):return {'Ref_Key':ORG,'ИНН':'000000000'}
    async def entity_props(self,entity):return {'header':{'Организация_Key'}}


def run(inv, docs, **kw):
    return asyncio.run(reconcile(inv,OneC(docs,**kw),'000000000'))


@pytest.mark.parametrize('flow,status',[('in','signed'),('out','signed'),('in','sended'),('out','sended')])
def test_both_directions_and_pending_match(flow,status):
    rows, summary=run([invoice(flow,status)],[document(IN if flow=='in' else OUT)])
    assert rows[0]['reconcile']=='matched'
    assert rows[0]['direction']==('kirim' if flow=='in' else 'chiqim')
    assert summary['counts']['matched']==1 and not rows[0]['selectable']


@pytest.mark.parametrize('field,value,label',[
    ('СуммаДокумента',120,'summasi'),('ДатаВходящегоДокумента','2026-02-10','sanasi'),
    ('Контрагент_Key','different-cp','STIR'),('DeletionMark',True,'o‘chirishga'),('Posted',False,'qoralama')])
def test_header_differences_are_not_missed(field,value,label):
    doc=document();doc[field]=value
    rows,_=run([invoice()],[doc])
    assert rows[0]['reconcile']=='differences'
    assert any(label in d for d in rows[0]['diffs'])


@pytest.mark.parametrize('field,value,label',[('Количество',3,'Miqdor'),('Сумма',113,'Qator summasi'),('СуммаНДС',13,'QQS summasi')])
def test_product_differences(field,value,label):
    doc=document();doc['Товары'][0][field]=value
    rows,_=run([invoice()],[doc])
    assert any(label in d for d in rows[0]['diffs'])


def test_deleted_ehf_with_active_onec_is_alert():
    rows,_=run([invoice(status='deleted')],[document()])
    assert rows[0]['reconcile']=='deleted_in_1c' and rows[0]['status']=='discrepancy'
    rows,_=run([invoice(status='deleted')],[])
    assert rows[0]['reconcile']=='deleted_absent'


def test_drafts_and_rejected_are_not_normal_reconciliation():
    for status in ['drafts','rejected']:
        rows,_=run([invoice(status=status)],[document()])
        assert len(rows)==1 and rows[0]['reconcile']=='skipped'


def test_onec_only_rows_and_missing_ehf_rows():
    inv=invoice();inv['number']='99';inv['date']='2026-01-11'
    rows,_=asyncio.run(reconcile([inv],OneC([document()]),'000000000','2026-01-01','2026-01-31'))
    assert {r['reconcile'] for r in rows}=={'missing','onec_only'}


def test_failed_onec_is_unchecked_not_missing():
    rows,summary=run([invoice()],[document()],fail=IN)
    assert rows[0]['reconcile']=='unchecked' and not summary['coverage']['in']
    assert summary['warnings']


def test_multiple_exact_documents_are_not_hidden():
    second=document();second['Ref_Key']='synthetic-doc-2'
    rows,_=run([invoice()],[document(),second])
    assert rows[0]['reconcile']=='differences' and len(rows[0]['matches'])==2


def test_source_number_mismatch_is_warning_not_new():
    doc=document();doc['НомерВходящегоДокумента']='another-number'
    rows,_=run([invoice()],[doc])
    assert rows[0]['reconcile']=='differences' and any('raqami' in d for d in rows[0]['diffs'])


def test_organization_boundary_is_enforced():
    doc=document();doc['Организация_Key']='different-org'
    rows,summary=run([invoice()],[doc])
    assert rows[0]['reconcile']=='unchecked' and not summary['coverage']['in']


def test_more_than_twenty_candidates_and_same_number_duplicate():
    docs=[]
    for i in range(30):
        doc=document();doc['Ref_Key']=f'synthetic-{i}';doc['НомерВходящегоДокумента']=str(i)
        docs.append(doc)
    inv=invoice();inv['number']='29'
    rows,_=run([inv],docs)
    assert rows[0]['reconcile']=='matched' and rows[0]['match']['ref']=='synthetic-29'
    duplicate=document();duplicate['Ref_Key']='duplicate';duplicate['СуммаДокумента']=999
    rows,_=run([invoice()],[document(),duplicate])
    assert rows[0]['reconcile']=='differences' and len(rows[0]['matches'])==2


def test_invoice_entities_share_match_but_item_differences_are_kept():
    doc=document();tax=document(ENTITIES['in'][1]);tax['Ref_Key']='synthetic-tax';tax['Товары'][0]['Количество']=99
    onec=OneC([doc,tax]);onec._metadata=AsyncMock(return_value={'sets':{IN,OUT,ENTITIES['in'][1]}})
    rows,_=asyncio.run(reconcile([invoice()],onec,'000000000'))
    assert rows[0]['reconcile']=='differences' and any('Miqdor' in d for d in rows[0]['diffs'])


def test_roaming_identifier_finds_document_even_when_header_changed():
    inv=invoice();inv['roaming_id']='synthetic-roaming'
    doc=document();doc['ИдентификаторДокументаЭДО']='synthetic-roaming'
    doc['НомерВходящегоДокумента']='different';doc['СуммаДокумента']=999
    doc['ДатаВходящегоДокумента']='2026-04-01'
    rows,_=run([inv],[doc])
    assert rows[0]['reconcile']=='differences'
    assert len(rows[0]['diffs'])>=3 and rows[0]['match']['ref']=='synthetic-doc'


def test_roaming_id_does_not_hide_wrong_counterparty():
    inv=invoice();inv['roaming_id']='synthetic-roaming'
    doc=document();doc['ИдентификаторДокументаЭДО']='synthetic-roaming';doc['Контрагент_Key']='different-cp'
    rows,_=run([inv],[doc])
    assert rows[0]['reconcile']=='differences' and any('STIR' in d for d in rows[0]['diffs'])


def test_header_difference_in_second_document_type_is_kept():
    tax=document(ENTITIES['in'][1]);tax['Ref_Key']='synthetic-tax';tax['СуммаДокумента']=999
    onec=OneC([document(),tax]);onec._metadata=AsyncMock(return_value={'sets':{IN,OUT,ENTITIES['in'][1]}})
    rows,_=asyncio.run(reconcile([invoice()],onec,'000000000'))
    assert rows[0]['reconcile']=='differences' and any('umumiy summasi' in d for d in rows[0]['diffs'])


def test_deleted_absence_is_not_claimed_with_incomplete_onec_fields():
    doc=document();doc['НомерВходящегоДокумента']='another';doc['СуммаДокумента']=None
    rows,summary=run([invoice(status='deleted')],[doc])
    assert rows[0]['reconcile']=='unchecked' and summary['warnings']


def test_rejected_number_does_not_hide_unrelated_onec_invoice():
    inv=invoice(status='rejected');inv['counterparty_inn']='000000002'
    rows,_=run([inv],[document()])
    assert {r['reconcile'] for r in rows}=={'skipped','onec_only'}


def test_unmatched_product_is_warning_not_confirmed_amount_or_extra_product_difference():
    inv=invoice();inv['items'][0].update(name='Synthetic differently named product', ikpu='different')
    rows,_=run([inv],[document()])
    assert rows[0]['reconcile']=='partial' and not rows[0]['diffs']
    assert any('aniq moslik' in w for w in rows[0]['comparison_warnings'])
    assert any('hali tasdiqlanmagan' in w for w in rows[0]['comparison_warnings'])


def test_missing_vat_detail_is_unknown_and_real_extra_line_is_difference():
    inv=invoice();inv['items'][0]['vat_sum']=None
    rows,_=run([inv],[document()])
    assert rows[0]['reconcile']=='partial'
    assert any('QQS summasi' in w for w in rows[0]['comparison_warnings'])
    doc=document();doc['Товары'].append(dict(doc['Товары'][0],Номенклатура_Key='synthetic-extra'))
    # Qo'shimcha qatorning MXIKi bir xil, lekin nomi boshqa.
    onec=OneC([doc])
    original=onec.t.get
    async def get(entity,key):
        if key=='synthetic-extra':return {'Description':'Synthetic extra product'}
        return await original(entity,key)
    onec.t.get=get
    rows,_=asyncio.run(reconcile([invoice()],onec,'000000000'))
    assert rows[0]['reconcile']=='differences' and any('qo‘shimcha' in d for d in rows[0]['diffs'])


def test_item_names_with_1c_suffix_and_unicode_x_match():
    # 1C nomida «-145» qo'shimchasi, EHFda «×» va nuqta; hamma qatorda bir xil MXIK
    code = '00000000000000001'
    ehf = [{'name': 'Kabel A 4x2x0,52', 'ikpu': code, 'qty': 10, 'total': 100, 'vat_sum': 10},
           {'name': 'Kabel B 4×2×0.57', 'ikpu': code, 'qty': 5, 'total': 50, 'vat_sum': 5},
           {'name': 'Kabel C', 'ikpu': code, 'qty': 3, 'total': 30, 'vat_sum': 3}]
    onec = [{'name': 'Kabel B 4x2x0,57-145', 'ikpu': code, 'qty': 5, 'total': 50, 'vat_sum': 5},
            {'name': 'Kabel A 4x2x0,52-145', 'ikpu': code, 'qty': 10, 'total': 100, 'vat_sum': 10},
            {'name': 'Boshqa nom', 'ikpu': code, 'qty': 3, 'total': 30, 'vat_sum': 3}]
    diffs, warnings = compare_items({'items': ehf}, {'items': onec})
    assert diffs == [] and warnings == []
    onec[1]['qty'] = 11
    diffs, _ = compare_items({'items': ehf}, {'items': onec})
    assert diffs == ['Miqdor farqli: Kabel A 4x2x0,52']
