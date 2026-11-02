"""Firma/davrga bog'langan Unicode PDF; fayl diskka saqlanmaydi."""
from io import BytesIO
from xml.sax.saxutils import escape

import config


def report(data):
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    if 'Business' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('Business',str(config.BASE_DIR/'static/fonts/DejaVuSans.ttf')))
    styles=getSampleStyleSheet()
    for style in styles.byName.values(): style.fontName='Business';style.fontSize=9;style.leading=13
    styles['Title'].fontSize=19;styles['Title'].leading=24
    def p(text,style='Normal'):return Paragraph(escape(str(text)),styles[style])
    def money(value):return 'Olinmagan' if value is None else f'{value:,.2f}'.replace(',',' ')+' so‘m'
    def table(rows,widths=None):
        t=Table([[p(c) for c in row] for row in rows],colWidths=widths,repeatRows=1,hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e7f3ee')),('VALIGN',(0,0),(-1,-1),'TOP'),
            ('BOTTOMPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),6),('LINEBELOW',(0,0),(-1,-1),.3,colors.HexColor('#dce4e0'))]))
        return t
    output=BytesIO()
    doc=SimpleDocTemplate(output,pagesize=A4,rightMargin=36,leftMargin=36,topMargin=36,bottomMargin=36,
                          title='Biznes hisoboti',author='Buxgalter AI')
    story=[p(data['firm']['name'],'Title'),p('Biznes hisoboti · '+data['period']['from']+' — '+data['period']['to']),
           p('Qoldiqlar sanasi: '+data['as_of']+' · Manba: '+data['source']),
           p('Muvaffaqiyatli yangilanish: '+(data.get('updated_at') or 'To‘liq olinmagan')),Spacer(1,12)]
    for w in data['warnings']:story += [p('Ogohlantirish: '+w),Spacer(1,4)]
    bal,inc=data['balances'] or {},data['income'] or {}
    story += [table([['Ko‘rsatkich','Qiymat']]+[[name,money(value)] for name,value in
        [('Bank',bal.get('bank')),('Kassa',bal.get('cash')),('Savdo daromadi',inc.get('revenue')),
         ('Boshqa daromad',inc.get('other_income')),('Tannarx',inc.get('cost')),('Xarajatlar',inc.get('expenses')),
         ('Foyda / zarar',inc.get('profit')),('Ombor',bal.get('stock_total'))]],[310,210]),Spacer(1,16)]
    debts=bal.get('debts',[])
    labels={'receivable':'Olinadigan','payable':'To‘lanadigan','advance_paid':'Berilgan avans','advance_received':'Olingan avans',
            'unclassified_debit':'Tasniflanmagan debet','unclassified_credit':'Tasniflanmagan kredit'}
    story += [p('Qarz va avanslar','Heading2'),p('Qarz yoshi to‘lov muddati emas; hujjatga bog‘lanmagan qoldiq yoshi aniqlanmagan.')]
    rows=[['Kontragent / tomoni','Qoldiq','Qarz yoshi / ogohlantirish']]
    for r in debts[:2000]:
        age=str(r['oldest_days'])+' kun' if r['oldest_days'] is not None else 'Aniqlanmadi'
        if r['unknown_amount']>0:age+='; yoshi noma’lum qoldiq: '+money(r['unknown_amount'])
        rows.append([r['name']+' · '+labels[r['side']],money(r['amount']),age+'; '+'; '.join(r['reasons'])])
    story += [table(rows,[220,125,175]),Spacer(1,16),p('Ombor qoldiqlari','Heading2')]
    rows=[['Tovar','Miqdor','Summa']]
    for r in bal.get('stock',[])[:2000]:rows.append([r['name'],str(r['qty'])+' '+r.get('unit','') if r['qty'] is not None else 'Olinmagan',money(r['amount'])])
    story.append(table(rows,[280,80,160]))
    if len(debts)>2000 or len(bal.get('stock',[]))>2000:story.append(p('Hisobot qisqartirilgan: har ro‘yxatdan birinchi 2000 qator. To‘liq tafsilot dashboardda.'))
    def footer(canvas,document):
        canvas.setFont('Business',8);canvas.drawRightString(A4[0]-36,20,'Buxgalter AI · '+str(document.page))
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()
