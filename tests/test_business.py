"""Firma egasi: ruxsat, tashkilot chegarasi, real hisob mantiqi, AI va PDF. Barcha ma'lumotlar sun'iy."""
import asyncio
from copy import deepcopy
from datetime import datetime,timedelta
import json
import sqlite3
from urllib.parse import parse_qs,urlsplit
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import auth,config,core,db,main
import services_business as b
import services_business_ai as ai
from services_1c import E_ACCOUNTS,E_COUNTERPARTY,E_NOMENCLATURE,E_PURCHASE,E_REGISTER,E_REGISTER_ROWS,ODataError

ORG='11111111-1111-1111-1111-111111111111'
TODAY=datetime.now(b.TZ).date()

@pytest.fixture(autouse=True)
def clean_business():
    b.CACHE.clear();b.LOCKS.clear();b.SUCCESS.clear()


def balance(code,value,cp=None,doc=None,product=None):
    row={'Организация_Key':ORG,'Account_Key':code,'СуммаBalance':value,'КоличествоBalance':4}
    if cp:row.update(ExtDimension1=cp,ExtDimension1_Type='StandardODATA.'+E_COUNTERPARTY)
    if product:row.update(ExtDimension1=product,ExtDimension1_Type='StandardODATA.'+E_NOMENCLATURE)
    if doc:row.update(ExtDimension3=doc,ExtDimension3_Type='StandardODATA.'+E_PURCHASE)
    return row


class Transport:
    mode='live'
    def __init__(self):
        self.calls=[];self.wrong=False;self.fail=False
        self.bal=[balance('5110',1000),balance('5010',20),balance('2910',500,product='product'),
                  balance('4010',110_000_000,cp='cp1',doc='old'),balance('4010',40_000_000,cp='cp1',doc='new'),
                  balance('6010',-20_000_000,cp='cp2',doc='new'),balance('4310',15_000_000,cp='cp1'),
                  balance('4010',150_000_000,cp='cp3')]
        self.ledger=[{'Организация_Key':ORG,'Active':True,'Period':TODAY.isoformat(),'LineNumber':i,'Recorder':'rec',
                      'AccountDr_Key':dt,'AccountCr_Key':kt,'Сумма':v} for i,(dt,kt,v) in enumerate([
                      ('4010','9010',1000),('9110','2910',600),('9420','6010',100),('9010','9910',1000),('9910','9110',600)])]
    async def _request(self,method,path):
        self.calls.append((method,path));assert method=='GET'
        if self.fail:raise ODataError('PRIVATE-CONNECTION-URL')
        params=parse_qs(urlsplit(path).query)
        assert ORG in params['$filter'][0] and 'Организация_Key' in params['$filter'][0]
        rows=deepcopy(self.bal)
        if self.wrong:rows[0]['Организация_Key']='other-org'
        start=int(params['$skip'][0]);return {'value':rows[start:start+b.PAGE]}
    async def get(self,entity,key):
        self.calls.append(('GET',entity,key))
        if entity==E_ACCOUNTS:return {'Code':key}
        if entity==E_COUNTERPARTY:return {'Description':'Synthetic '+key,'ИНН':'000000001'}
        if entity==E_NOMENCLATURE:return {'Description':'Synthetic product','КодИКПУ':'00000000000000000'}
        if key=='missing':return None
        return {'Ref_Key':key,'Организация_Key':ORG,'Date':(TODAY-timedelta(days=35 if key=='old' else 5)).isoformat(),
                'Number':key,'СуммаДокумента':200_000_000,'Posted':True,'DeletionMark':False,'Контрагент_Key':'cp1'}
    async def query(self,entity,conds,top,skip,orderby):
        self.calls.append(('GET',entity,conds))
        assert any(c.field=='Организация_Key' and c.value.value==ORG for c in conds)
        rows=deepcopy(self.ledger if entity==E_REGISTER_ROWS else [])
        return rows[skip:skip+top]


class OneC:
    mode='live'
    def __init__(self):self.t=Transport()
    async def _metadata(self):return {'sets':{E_REGISTER,E_REGISTER_ROWS,*b.DOC_TYPES.values()}}
    async def organization(self):return {'Ref_Key':ORG,'ИНН':'000000000'}
    async def entity_props(self,entity):return {'header':{'Организация_Key'}}


@pytest.fixture
def live(monkeypatch):
    db.update_firm(1,{'inn':'000000000'})
    o=OneC();monkeypatch.setattr(core,'get_onec',lambda _:o)
    return o


def test_true_income_excludes_closing_entries_and_debts_use_outstanding_balance(live):
    data=asyncio.run(b.build(core.Session('synthetic',1)))
    assert data['income']=={'revenue':1000,'cost':600,'expenses':100,'other_income':0,'profit':300}
    assert data['balances']['bank']==1000 and data['balances']['cash']==20
    assert data['balances']['stock_total']==500 and data['balances']['stock'][0]['qty']==4
    rows=data['balances']['debts']
    cp=next(r for r in rows if r['id']=='cp1' and r['side']=='receivable')
    assert cp['amount']==150_000_000 and cp['old_amount']==110_000_000 and cp['oldest_days']==35 and len(cp['reasons'])==2
    unknown=next(r for r in rows if r['id']=='cp3')
    assert unknown['oldest_days'] is None and unknown['unknown_amount']==150_000_000 and len(unknown['reasons'])==1
    adv=next(r for r in rows if r['side']=='advance_paid')
    assert adv['amount']==15_000_000 and not adv['reasons']
    assert data['updated_at'] and not data['partial']


def test_missing_or_cross_organization_is_unknown_and_never_leaks(live):
    live.t.wrong=True
    data=asyncio.run(b.build(core.Session('synthetic',1)))
    assert data['balances'] is None and data['warnings'] and data['partial']
    live.t.fail=True
    data=asyncio.run(b.build(core.Session('synthetic',1),refresh=True))
    assert data['balances'] is None and 'PRIVATE-CONNECTION-URL' not in json.dumps(data)


def test_organization_mismatch_stops_before_financial_reads(live):
    live.organization=AsyncMock(return_value={'Ref_Key':ORG,'ИНН':'000000002'})
    data=asyncio.run(b.build(core.Session('synthetic',1)))
    assert data['income'] is None and data['balances'] is None and not live.t.calls


def test_limits_partial_payment_boundary_and_missing_age(live):
    live.t.bal=[balance('4010',100_000_000,cp='cp1',doc='missing'),balance('6010',-1,cp='cp2',doc='old')]
    data=asyncio.run(b.build(core.Session('synthetic',1)))
    cp1=next(r for r in data['balances']['debts'] if r['id']=='cp1')
    assert cp1['oldest_days'] is None and not cp1['reasons'] # Teng summa katta deb belgilanmaydi.
    cp2=next(r for r in data['balances']['debts'] if r['id']=='cp2')
    assert len(cp2['reasons'])==1 and cp2['old_amount']==1 # Faktura asl summasi emas, qolgan qarz.
    db.update_firm(1,{'debt_age_days':40,'debt_large_amount':90_000_000})
    data=asyncio.run(b.build(core.Session('synthetic',1)))
    assert next(r for r in data['balances']['debts'] if r['id']=='cp1')['reasons']
    assert not next(r for r in data['balances']['debts'] if r['id']=='cp2')['reasons']


def test_pagination_is_complete_and_unscoped_detail_rejected(live):
    live.t.bal=[dict(balance('5110',1),ExtDimension1='dimension-'+str(i)) for i in range(501)]
    data=asyncio.run(b.build(core.Session('synthetic',1)))
    assert data['balances']['bank']==501
    assert len([c for c in live.t.calls if 'Balance(' in c[1]])==2
    live.t.get=AsyncMock(return_value={'Ref_Key':'foreign','Организация_Key':'foreign'})
    with pytest.raises(ValueError,match='tashkilot'):
        asyncio.run(b.document(core.Session('synthetic',1),'xarid','foreign'))


def test_cache_is_separate_by_firm_period_and_connection(live):
    first=asyncio.run(b.build(core.Session('synthetic',1)))
    live.t.bal[0]['СуммаBalance']=222
    cached=asyncio.run(b.build(core.Session('synthetic',1)))
    assert first==cached
    db.update_firm(1,{'onec_user':'synthetic-changed'})
    newer=asyncio.run(b.build(core.Session('synthetic',1)))
    assert newer['balances']['bank']==222
    bad=asyncio.run(b.build(core.Session('synthetic',1),refresh=True))
    assert bad['period']['to']==TODAY.isoformat()


def users():
    f2=db.create_firm({'name':'Synthetic second','inn':'000000002'})
    f3=db.create_firm({'name':'Private third','inn':'000000003'})
    admin=db.create_user('admin','synthetic-password','owner')
    owner=db.create_user('owner1','synthetic-password','business_owner',firm_ids=[1,f2['id']])
    other=db.create_user('owner2','synthetic-password','business_owner',firm_ids=[f3['id']])
    accountant=db.create_user('accountant','synthetic-password','accountant',firm_ids=[1])
    return owner,other,accountant,f2,f3


def login(name):
    c=TestClient(main.app,base_url='http://127.0.0.1:8000')
    assert c.post('/api/auth/login',json={'username':name,'password':'synthetic-password'}).status_code==200
    return c


def test_owner_sees_assigned_firms_only_and_legacy_write_and_read_routes_are_closed(live):
    owner,_,_,f2,f3=users();c=login('owner1')
    me=c.get('/api/auth/me').json()
    assert {f['id'] for f in me['firms']}=={1,f2['id']}
    assert all(set(f)=={'id','name','inn','onec_demo_mode'} for f in me['firms'])
    assert 'Private third' not in c.get('/api/firms').text and 'onec_user' not in c.get('/api/firms').text
    assert 'businessApp' in c.get('/').text and not me['can_post'] and not me['can_manage_firm']
    assert c.get('/api/business/dashboard',headers={'X-Firm-Id':str(f3['id'])}).status_code==403
    for path,method in [('/api/status','GET'),('/api/session?mode=didox','GET'),('/api/dashboard','GET'),
       ('/api/stock','GET'),('/api/didox/load','POST'),('/api/chat','POST'),('/api/chat/stream','POST'),
       ('/api/vipiska/export-1c','POST'),('/api/post','POST'),('/api/settings','GET'),('/api/users','GET')]:
        r=c.request(method,path,json={'message':'read','ids':[],'mode':'didox'} if method=='POST' else None)
        assert r.status_code==403,(path,r.text)
    assert c.put('/api/firms/1',json={'name':'changed'}).status_code==403
    assert c.post('/api/users',json={'username':'x','password':'synthetic-pass','role':'owner'}).status_code==403
    db.update_user(owner['id'],firm_ids=[])
    assert c.get('/api/business/dashboard',headers={'X-Firm-Id':'1'}).status_code==403


def test_accountant_manages_own_firm_but_cannot_change_system_or_assignments():
    _,_,accountant,_,f3=users();c=login('accountant')
    assert c.put('/api/firms/1',json={'name':'Synthetic changed','debt_age_days':45}).status_code==200
    assert db.get_firm(1)['debt_age_days']==45
    assert c.put('/api/firms/'+str(f3['id']),json={'name':'forbidden'}).status_code==403
    assert c.post('/api/settings',json={'gemini_api_key':'forbidden'}).status_code==403
    assert c.post('/api/firms',json={'name':'forbidden'}).status_code==403
    assert c.post('/api/firms/1/write-access',json={'enabled':False}).status_code==403
    assert c.put('/api/firms/1',json={'didox_drafts_enabled':True}).status_code==403
    db.update_firm(1,{'didox_drafts_enabled':True})
    assert c.put('/api/firms/1',json={'didox_drafts_enabled':'false'}).status_code==403
    assert c.post('/api/users',json={'username':'x','password':'synthetic-pass','role':'owner'}).status_code==403
    assert db.get_user(accountant['id'])['firm_ids']==[1]


def test_existing_database_role_migration_preserves_identity_password_firms_and_sessions():
    u=db.create_user('legacy-admin','synthetic-password','owner',firm_ids=[1]);token=db.create_web_session(u['id'])
    with sqlite3.connect(db.DB_FILE) as c:
        c.execute('PRAGMA foreign_keys=OFF')
        sql=c.execute("SELECT sql FROM sqlite_master WHERE name='users'").fetchone()[0]
        c.execute(sql.replace('users','users_old',1).replace(", 'business_owner'",''))
        c.execute('INSERT INTO users_old SELECT * FROM users')
        c.execute('DROP TABLE users');c.execute('ALTER TABLE users_old RENAME TO users')
    db.init()
    assert db.authenticate('legacy-admin','synthetic-password')['id']==u['id']
    assert db.user_from_web_session(token)['firm_ids']==[1]
    assert db.create_user('new-firm-owner','synthetic-password','business_owner',firm_ids=[1])['role']=='business_owner'
    with db.tx() as c:assert not c.execute('PRAGMA foreign_key_check').fetchall()
    db.init() # Ikkinchi ishga tushirish ham xavfsiz.


def test_pdf_has_unicode_font_and_cannot_access_other_company(live):
    *_,f3=users();c=login('owner1')
    r=c.get('/api/business/report.pdf')
    assert r.status_code==200 and r.content.startswith(b'%PDF-') and r.headers['content-type']=='application/pdf'
    assert b'/FontFile2' in r.content and r.headers['cache-control']=='no-store'
    assert c.get('/api/business/report.pdf',headers={'X-Firm-Id':str(f3['id'])}).status_code==403


def test_gemini_tools_cannot_override_firm_or_write(live,monkeypatch):
    owner,*_=users();c=login('owner1')
    config.save_settings({'gemini_api_key':'synthetic-key'})
    seen=[]
    async def generate(s,m,snapshot,history,settings,**kwargs):
        seen.append((snapshot,deepcopy(history)))
        return 'Synthetic selected firm answer','Gemini synthetic'
    monkeypatch.setattr(ai,'generate',generate)
    r=c.post('/api/business/chat',json={'message':'What about debt?'}).json()
    assert r['engine_label']=='Gemini synthetic' and seen[0][0]['firm']['id']==1
    c.post('/api/business/chat',json={'message':'And expenses?'})
    assert len(seen[1][1])==2
    assert c.get('/api/business/chat',headers={'X-Firm-Id':'2'}).json()['messages']==[]
    s=core.get_session(core.session_id(owner['id'],1),1)
    for name,args in [('business_overview',{'firm_id':3}),('post_confirmed_to_1c',{}),('onec_read',{'entity':'x'})]:
        with pytest.raises(ValueError):asyncio.run(ai.tool(s,name,args,TODAY.replace(day=1).isoformat(),TODAY.isoformat()))
    original=deepcopy(s.business_chat)
    restored=core._load_session(s.sid,1)
    assert restored.business_chat==original


def test_gemini_failures_and_no_key_are_not_reported_as_real_ai(live,monkeypatch,caplog):
    users();c=login('owner1')
    r=c.post('/api/business/chat',json={'message':'Explain'}).json()
    assert r['engine_label']=='Mahalliy izoh' and 'Gemini ulanmagan' in r['notice']
    config.save_settings({'gemini_api_key':'synthetic-key'})
    monkeypatch.setattr(ai,'generate',AsyncMock(side_effect=RuntimeError('PRIVATE-AI-SECRET')))
    r=c.post('/api/business/chat',json={'message':'Explain'}).json()
    assert 'PRIVATE-AI-SECRET' not in json.dumps(r)+caplog.text and 'javob bermadi' in r['notice']


def test_real_gemini_adapter_tool_loop_is_read_only_and_uses_selected_snapshot(live,monkeypatch):
    import google.genai
    from google.genai import types,errors
    from types import SimpleNamespace
    users();c=login('owner1')
    config.save_settings({'gemini_api_key':'synthetic-key','gemini_model':'synthetic-retired'})
    calls=[]
    mock_docs=AsyncMock(return_value={'rows':[{'number':'ONLY-SELECTED-FIRM'}],'count':1,'page':1})
    monkeypatch.setattr(b,'documents',mock_docs)
    class FakeAio:
        def __init__(self):self.models=self
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def generate_content(self,*,model,contents,config):
            calls.append((model,deepcopy(contents)))
            assert {d.name for d in config.tools[0].function_declarations}=={'business_overview','business_documents','business_document'}
            if model=='synthetic-retired':raise errors.ClientError(404,{'error':{'code':404,'message':'not found'}})
            if len(calls)==2:
                fc=types.FunctionCall(name='business_documents',args={'kind':'xarid','search':'Synthetic'})
                return SimpleNamespace(function_calls=[fc],candidates=[SimpleNamespace(content=types.Content(role='model',parts=[types.Part(function_call=fc)]))])
            assert 'ONLY-SELECTED-FIRM' in str(contents[-1])
            return SimpleNamespace(function_calls=None,text='Synthetic final answer')
    class FakeClient:
        def __init__(self,**kwargs):self.aio=FakeAio()
    monkeypatch.setattr(google.genai,'Client',FakeClient)
    r=c.post('/api/business/chat',json={'message':'Explain purchase documents'})
    assert r.status_code==200 and r.json()['reply']=='Synthetic final answer'
    assert 'Private third' not in str(calls)
    assert mock_docs.await_args.args[0].firm_id==1 and len(calls)==3


def test_revocation_during_ai_request_is_checked_before_response(live,monkeypatch):
    owner,*_=users();c=login('owner1')
    config.save_settings({'gemini_api_key':'synthetic-key'})
    async def revoke(*args,**kwargs):
        db.update_user(owner['id'],firm_ids=[])
        return 'ANSWER-MUST-NOT-RETURN','Gemini synthetic'
    monkeypatch.setattr(ai,'generate',revoke)
    r=c.post('/api/business/chat',json={'message':'Explain'})
    assert r.status_code==403 and 'ANSWER-MUST-NOT-RETURN' not in r.text


def test_age_at_thirty_days_and_subthreshold_debt_is_flagged(live):
    original=live.t.get
    async def get(entity,key):
        rec=await original(entity,key)
        if key=='boundary':rec['Date']=(TODAY-timedelta(days=30)).isoformat()
        return rec
    live.t.get=get
    live.t.bal=[balance('4010',1,cp='cp1',doc='boundary')]
    data=asyncio.run(b.build(core.Session('synthetic',1)))
    assert data['balances']['debts'][0]['old_amount']==1
    assert len(data['balances']['debts'][0]['reasons'])==1


def test_unconfigured_real_firm_is_not_replaced_with_demo():
    db.update_firm(1,{'onec_demo_mode':False})
    data=asyncio.run(b.build(core.Session('synthetic',1)))
    assert data['source']=='offline' and data['balances'] is None and data['income'] is None
    assert data['warnings'] and data['updated_at'] is None


def test_reverse_settlement_balances_are_visible_and_not_netted_with_regular_debt(live):
    live.t.bal=[balance('4010',100,cp='cp1'),balance('4010',-25,cp='cp1'),balance('6010',20,cp='cp1')]
    data=asyncio.run(b.build(core.Session('synthetic',1)))
    rows=data['balances']['debts']
    assert len(rows)==3
    assert next(r for r in rows if r['side']=='receivable')['amount']==100
    assert next(r for r in rows if r['side']=='unclassified_credit')['amount']==25
    assert next(r for r in rows if r['side']=='unclassified_debit')['amount']==20


def test_admin_can_create_business_owner_through_existing_user_form_api():
    *_,second,private=users();c=login('admin')
    r=c.post('/api/users',json={'username':'new-business-owner','password':'synthetic-password','role':'business_owner','firm_ids':[1,second['id']]})
    assert r.status_code==200 and r.json()['role_label']=='Firma egasi'
    c=login('new-business-owner')
    assert {f['id'] for f in c.get('/api/auth/me').json()['firms']}=={1,second['id']}
    assert c.get('/api/business/dashboard',headers={'X-Firm-Id':str(private['id'])}).status_code==403
