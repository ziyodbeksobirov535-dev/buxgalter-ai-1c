"""Firma egasi API: har so'rovda joriy rol/firma, javobdan oldin ham qayta tekshirish."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from auth import FirmCtx, firm_ctx
import db
import services_business as service
import services_business_ai as ai

router=APIRouter(prefix='/api/business')


def access(ctx):
    fresh=db.get_user(ctx.user['id'])
    if not fresh or not fresh['active'] or fresh['role']!=ctx.user['role'] or not db.user_can_access(fresh,ctx.firm_id):
        raise HTTPException(403,'Firma yoki rol ruxsati yangilangan; sahifani qayta oching')


@router.get('/dashboard')
async def dashboard(date_from:str='',date_to:str='',refresh:bool=False,ctx:FirmCtx=Depends(firm_ctx)):
    try: data=await service.build(ctx.session,date_from,date_to,refresh)
    except ValueError as e: raise HTTPException(400,str(e)) from None
    access(ctx)
    return data


@router.get('/documents')
async def documents(kind:str='sotuv',date_from:str='',date_to:str='',page:int=1,search:str='',ctx:FirmCtx=Depends(firm_ctx)):
    if not 1<=page<=400 or len(search)>200: raise HTTPException(400,'Sahifa yoki qidiruv noto‘g‘ri')
    try: data=await service.documents(ctx.session,kind,date_from,date_to,page,search)
    except (ValueError,service.ODataError) as e: raise HTTPException(400,service.explain(e)) from None
    access(ctx)
    return data


@router.get('/documents/{kind}/{key}')
async def document(kind:str,key:str,ctx:FirmCtx=Depends(firm_ctx)):
    try: data=await service.document(ctx.session,kind,key)
    except KeyError: raise HTTPException(404,'Hujjat topilmadi') from None
    except (ValueError,service.ODataError) as e: raise HTTPException(400,service.explain(e)) from None
    access(ctx)
    return data


@router.get('/report.pdf')
async def pdf(date_from:str='',date_to:str='',ctx:FirmCtx=Depends(firm_ctx)):
    from services_business_pdf import report
    try: data=await service.build(ctx.session,date_from,date_to)
    except ValueError as e: raise HTTPException(400,str(e)) from None
    content=await asyncio.to_thread(report,data)
    access(ctx)
    return Response(content,media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="biznes-hisoboti.pdf"','Cache-Control':'no-store'})


class Question(BaseModel):
    message:str=Field(...,min_length=1,max_length=4000)
    date_from:str=''
    date_to:str=''


@router.post('/chat')
async def chat(body:Question,ctx:FirmCtx=Depends(firm_ctx)):
    try: data=await ai.chat(ctx.session,body.message,body.date_from,body.date_to,access_check=lambda:access(ctx))
    except (ValueError,service.ODataError) as e: raise HTTPException(400,service.explain(e)) from None
    access(ctx)
    return data


@router.get('/chat')
async def history(date_from:str='',date_to:str='',ctx:FirmCtx=Depends(firm_ctx)):
    import core
    try:a,b=service.period(date_from,date_to)
    except ValueError as e:raise HTTPException(400,str(e)) from None
    slot=ctx.session.business_chat
    tag=[core.onec_fingerprint(ctx.firm_id),a.isoformat(),b.isoformat()]
    return {'messages':slot.get('messages',[]) if slot.get('tag')==tag else []}
