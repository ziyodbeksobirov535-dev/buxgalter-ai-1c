function businessApp() {
  return {
    me:null, firms:[], firmId:null, from:'', to:'', page:'overview', data:null, error:'', epoch:0,
    loading:false,pdfBusy:false, aiBusy:false,docBusy:false, detail:null, openIncome:false,profile:false,
    password:{old:'',new:''}, debtSide:'',debtSearch:'',debtOnlyAlerts:false,stockSearch:'',
    docs:{rows:[],count:0},docKind:'sotuv',docSearch:'',docPage:1,messages:[],question:'',aiNotice:'',timer:null,
    tabs:[{id:'overview',name:'Umumiy holat',icon:'◫'},{id:'debts',name:'Qarzlar',icon:'⇄'},
          {id:'stock',name:'Ombor',icon:'▦'},{id:'documents',name:'Hujjatlar',icon:'▤'},{id:'ai',name:'Gemini',icon:'✦'}],
    docTypes:{sotuv:'Sotuv',xarid:'Xarid',bank_kirim:'Bank kirim',bank_chiqim:'Bank chiqim',kirim_faktura:'Olingan hisob-faktura',chiqim_faktura:'Berilgan hisob-faktura',kassa_kirim:'Kassa kirim',kassa_chiqim:'Kassa chiqim'},
    async init(){
      const parts=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Tashkent',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());
      const part=t=>parts.find(x=>x.type===t).value;
      this.to=part('year')+'-'+part('month')+'-'+part('day');this.from=this.to.slice(0,8)+'01';
      try{
        this.me=await this.api('/api/auth/me'); if(!this.me)return;
        this.firms=this.me.firms;
        let saved;try{saved=Number(localStorage.getItem('bx_business_firm'));}catch(e){}
        this.firmId=(this.firms.find(f=>f.id===saved)||this.firms[0])?.id || null;
        const wanted=location.hash.slice(2);this.page=this.tabs.some(t=>t.id===wanted)?wanted:'overview';
        if(this.firmId){await this.refresh();await this.history();if(this.page==='documents')await this.loadDocs();}
      }catch(e){this.error=e.message;}
      this.timer=setInterval(()=>{if(this.firmId&&!document.hidden&&!this.loading)this.refresh(true);},300000);
      window.addEventListener('hashchange',()=>{const p=location.hash.slice(2);if(this.tabs.some(t=>t.id===p)&&this.page!==p)this.go(p);});
    },
    async api(path, opts={}){
      const rev=this.epoch, headers={...(opts.headers||{})};if(this.firmId)headers['X-Firm-Id']=String(this.firmId);
      const request={method:opts.method||'GET',headers};
      if(opts.json!==undefined){request.method=opts.method||'POST';headers['Content-Type']='application/json';request.body=JSON.stringify(opts.json);}
      const response=await fetch(path,request);
      if(response.status===401){location.replace('/login');return null;}
      if(!response.ok){const body=await response.json().catch(()=>({}));throw new Error(typeof body.detail==='string'?body.detail:'So‘rov bajarilmadi ('+response.status+')');}
      const value=opts.raw?await response.blob():await response.json();
      return rev===this.epoch?value:null;
    },
    params(){return new URLSearchParams({date_from:this.from,date_to:this.to});},
    validPeriod(){if(!this.from||!this.to||this.from>this.to){this.error='Davr sanalarini tekshiring.';return false;}return true;},
    resetView(){this.epoch++;this.data=null;this.error='';this.detail=null;this.openIncome=false;this.messages=[];this.aiNotice='';this.docs={rows:[],count:0};this.docPage=1;this.loading=false;this.docBusy=false;this.aiBusy=false;this.pdfBusy=false;},
    async changeFirm(){this.resetView();this.debtSearch='';this.stockSearch='';this.docSearch='';try{localStorage.setItem('bx_business_firm',String(this.firmId));}catch(e){}await this.refresh();await this.history();if(this.page==='documents')await this.loadDocs();},
    async periodChanged(){this.resetView();if(!this.validPeriod())return;await this.refresh();await this.history();if(this.page==='documents')await this.loadDocs();},
    async refresh(force=false){
      if(!this.firmId||this.loading||!this.validPeriod())return;
      const rev=this.epoch;this.loading=true;this.error='';
      try{const q=this.params();if(force)q.set('refresh','true');const d=await this.api('/api/business/dashboard?'+q);if(d)this.data=d;}
      catch(e){if(rev===this.epoch)this.error=e.message;}
      finally{if(rev===this.epoch)this.loading=false;}
    },
    async go(id){this.page=id;history.replaceState(null,'','#/'+id);if(id==='documents')await this.loadDocs();if(id==='ai')await this.history();},
    firmName(){return this.firms.find(f=>f.id===this.firmId)?.name || '';},
    number(v){return new Intl.NumberFormat('ru-RU',{maximumFractionDigits:4}).format(v);},
    money(v){return v==null?'Olinmagan':new Intl.NumberFormat('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2}).format(v)+' so‘m';},
    timestamp(v){return v?new Date(v).toLocaleString('ru-RU',{timeZone:'Asia/Tashkent'}):'To‘liq olinmagan';},
    sideLabel(s){return {receivable:'Bizga to‘lanadigan',payable:'Biz to‘laydigan',advance_paid:'Berilgan avans',advance_received:'Olingan avans',unclassified_debit:'Tasniflanmagan debet',unclassified_credit:'Tasniflanmagan kredit'}[s]||s;},
    totalDebt(side){return this.data?.balances && this.data.balances.debts_complete!==false?this.data.balances.debts.filter(r=>r.side===side).reduce((s,r)=>s+r.amount,0):null;},
    metrics(){const b=this.data?.balances,i=this.data?.income;return [
      {label:'Pul mablag‘lari',value:b?b.bank+b.cash:null,note:b?'Bank: '+this.money(b.bank)+' · Kassa: '+this.money(b.cash):'Bank va kassa',page:'documents',kind:'bank_kirim'},
      {label:'Savdo daromadi',value:i?.revenue,note:'Tanlangan davrda',page:'documents',kind:'sotuv'},
      {label:'Foyda / zarar',value:i?.profit,note:'Daromad − tannarx − xarajat'},
      {label:'Bizga to‘lanadigan qarz',value:this.totalDebt('receivable'),note:'Ochiq debitorlik',page:'debts'},
      {label:'Biz to‘laydigan qarz',value:this.totalDebt('payable'),note:'Ochiq kreditorlik',page:'debts'},
      {label:'Ombordagi qiymat',value:b?.stock_total,note:'Hisobdagi tovar va materiallar',page:'stock'}];},
    selectMetric(m){if(m.page){if(m.kind)this.docKind=m.kind;this.go(m.page);}else this.openIncome=true;},
    incomeRows(){const d=this.data?.income;return [{label:'Savdo daromadi',value:d?.revenue,kind:'income'},{label:'Boshqa daromad',value:d?.other_income,kind:'other'},{label:'Tannarx',value:d?.cost,kind:'cost'},{label:'Xarajatlar',value:d?.expenses,kind:'expense'}];},
    barWidth(v){const max=Math.max(...this.incomeRows().map(x=>Math.abs(x.value||0)),1);return Math.min(100,Math.abs(v||0)/max*100);},
    alerts(){return (this.data?.balances?.debts||[]).filter(r=>r.reasons.length);},
    filteredDebts(){const q=this.debtSearch.trim().toLocaleLowerCase();return (this.data?.balances?.debts||[]).filter(r=>(!this.debtSide||r.side===this.debtSide)&&(!this.debtOnlyAlerts||r.reasons.length)&&(!q||(r.name+' '+r.inn).toLocaleLowerCase().includes(q)));},
    filteredStock(){const q=this.stockSearch.trim().toLocaleLowerCase();return (this.data?.balances?.stock||[]).filter(r=>!q||(r.name+' '+r.ikpu).toLocaleLowerCase().includes(q));},
    openParty(r){this.detail={title:'Kontragent tafsiloti',party:r};},
    async loadDocs(){
      if(!this.firmId||!this.validPeriod())return;const rev=this.epoch, token=[this.docKind,this.docSearch,this.docPage].join('|');this.docBusy=true;
      try{const q=this.params();q.set('kind',this.docKind);q.set('page',String(this.docPage));q.set('search',this.docSearch);const r=await this.api('/api/business/documents?'+q);if(r && token===[this.docKind,this.docSearch,this.docPage].join('|'))this.docs=r;}
      catch(e){if(rev===this.epoch)this.error=e.message;}
      finally{if(rev===this.epoch)this.docBusy=false;}
    },
    async openDocument(d){const rev=this.epoch;try{const r=await this.api('/api/business/documents/'+encodeURIComponent(d.kind)+'/'+encodeURIComponent(d.id));if(r)this.detail={title:'Hujjat tafsiloti',document:r};}catch(e){if(rev===this.epoch)this.error=e.message;}},
    async history(){if(!this.firmId)return;const rev=this.epoch;try{const r=await this.api('/api/business/chat?'+this.params());if(r&&!this.aiBusy)this.messages=r.messages;}catch(e){if(rev===this.epoch)this.error=e.message;}},
    async ask(text){text=(text||'').trim();if(!text||this.aiBusy)return;const rev=this.epoch;this.aiBusy=true;this.aiNotice='';
      try{const r=await this.api('/api/business/chat',{json:{message:text,date_from:this.from,date_to:this.to}});if(r){this.messages=r.messages;this.aiNotice=r.notice;this.question='';this.$nextTick(()=>{const box=this.$refs.chat;if(box)box.scrollTop=box.scrollHeight;});}}
      catch(e){if(rev===this.epoch)this.aiNotice=e.message;}
      finally{if(rev===this.epoch)this.aiBusy=false;}
    },
    async downloadPdf(){if(this.pdfBusy||!this.validPeriod())return;const rev=this.epoch;this.pdfBusy=true;
      try{const blob=await this.api('/api/business/report.pdf?'+this.params(),{raw:true});if(!blob)return;const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='biznes-'+this.firmId+'-'+this.from+'-'+this.to+'.pdf';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
      catch(e){if(rev===this.epoch)this.error=e.message;}
      finally{if(rev===this.epoch)this.pdfBusy=false;}
    },
    async passwordChange(){try{const r=await this.api('/api/auth/password',{json:{old_password:this.password.old,new_password:this.password.new}});if(r){this.profile=false;this.password={old:'',new:''};}}catch(e){this.error=e.message;}},
    async logout(){await this.api('/api/auth/logout',{method:'POST'});location.replace('/login');}
  };
}
