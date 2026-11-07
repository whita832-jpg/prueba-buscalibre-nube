#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Monitoreo de cuatro catálogos de BuscaLibre MX, con filtro estricto:
novela + tapa dura + actual < 200 MXN + referencia histórica observada >= 400.
No realiza compras. No guarda credenciales. Solo usa solicitudes GET.
"""
from __future__ import annotations

import argparse
import csv
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime, timezone, date, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import statistics
import sys
import threading
import time
import unicodedata
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import requests
from bs4 import BeautifulSoup

DIR = Path(__file__).resolve().parent
API = 'https://api.scrapingant.com/v2/general'
CATALOGS = [
    ('Alba', 'https://www.buscalibre.com.mx/libros/editorial/alba'),
    ('Alba Editorial', 'https://www.buscalibre.com.mx/libros/editorial/alba-editorial'),
    ('Acantilado', 'https://www.buscalibre.com.mx/libros/editorial/acantilado'),
    ('El Acantilado', 'https://www.buscalibre.com.mx/libros/editorial/el-acantilado'),
]
LIMIT = Decimal('200.00')  # estrictamente menor
REFERENCE = Decimal('400.00')  # previamente observado, >=400
MIN_REFERENCE_DAYS = 3
HISTORY_DAYS = 180
MAX_PAGES = 1000
API_GAP = 0.3  # segundos entre inicios de solicitudes; 1 sola en plan gratis
RETRY_COUNT = 3
FIELDS = ['catalogo','pagina','titulo','autor','edicion','formato_catalogo','precio_mxn','url','candidato_menos_200']
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
API_LAST = 0.0
API_LOCK = threading.Lock()


def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def today(): return datetime.now(ZoneInfo('America/Mexico_City')).date().isoformat()
def clean(s): return re.sub(r'\s+', ' ', s or '').strip()
def txt(el): return clean(el.get_text(' ', strip=True)) if el else ''
def norm(s): return ''.join(x for x in unicodedata.normalize('NFKD', (s or '').casefold()) if not unicodedata.combining(x))


def money(s):
    m = re.search(r'\$\s*([\d.,]+)', s or '')
    if not m: return None
    x = m.group(1).strip('.,')
    if '.' in x and ',' in x:
        x = x.replace(',','') if x.rfind('.') > x.rfind(',') else x.replace('.','').replace(',','.')
    elif ',' in x:
        x = x.replace(',','.') if len(x.split(',')[-1])<=2 else x.replace(',','')
    try: return Decimal(x)
    except InvalidOperation: return None


def decimal_or_none(x):
    try: return Decimal(str(x))
    except (InvalidOperation,ValueError,TypeError): return None


def hardcover(s):
    t = norm(s)
    if re.search(r'tapa blanda|pasta blanda|rustica|bolsillo|paperback|softcover', t): return False
    if re.search(r'tapa dura|pasta dura|cartone|hardcover|hardback|cubierta dura', t): return True
    return None  # desconocido: no alertar antes de verificar ficha


def clipboard_windows():
    if sys.platform != 'win32': raise RuntimeError('Portapapeles disponible solo en Windows')
    user32=ctypes.WinDLL('user32',use_last_error=True)
    kernel32=ctypes.WinDLL('kernel32',use_last_error=True)
    user32.OpenClipboard.argtypes=[wintypes.HWND];user32.OpenClipboard.restype=wintypes.BOOL
    user32.GetClipboardData.argtypes=[wintypes.UINT];user32.GetClipboardData.restype=wintypes.HANDLE
    user32.CloseClipboard.restype=wintypes.BOOL
    kernel32.GlobalLock.argtypes=[wintypes.HGLOBAL];kernel32.GlobalLock.restype=wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes=[wintypes.HGLOBAL];kernel32.GlobalUnlock.restype=wintypes.BOOL
    for _ in range(10):
        if user32.OpenClipboard(None):break
        time.sleep(.2)
    else: raise RuntimeError('Portapapeles ocupado')
    try:
        h=user32.GetClipboardData(13)
        if not h:raise RuntimeError('No hay texto en el portapapeles')
        ptr=kernel32.GlobalLock(h)
        if not ptr:raise RuntimeError('No se pudo bloquear el portapapeles')
        try:return ctypes.wstring_at(ptr)
        finally:kernel32.GlobalUnlock(h)
    finally:user32.CloseClipboard()


def get_token(required):
    token=os.environ.get('SCRAPINGANT_API_TOKEN','').strip()
    if not token and required:
        print('Copia API Token desde https://app.scrapingant.com/profile')
        input('Presiona ENTER cuando esté copiado (no pegues aquí): ')
        token=clipboard_windows().strip().strip('"').strip("'")
    if required and (not token or any(ord(c)<32 for c in token)):
        raise RuntimeError('No se recibió un API Token válido. Nunca lo compartas.')
    return token


class FetchError(Exception):
    def __init__(self,status,message):
        self.status=status; super().__init__(message)


@dataclass
class Receipt:
    html:str
    api_calls:int=0
    api_credits:int=0
    direct_calls:int=0
    retries:int=0


class Transport:
    def __init__(self,token,source,stored):
        self.token=token;self.source=source
        self.lock=threading.Lock()
        self.env='github' if os.environ.get('GITHUB_ACTIONS')=='true' else 'local'
        self.direct_status=stored.setdefault('direct_status',{})
        until=self.direct_status.get(self.env,'')
        self.direct_enabled=(source in ('auto','direct')) and (source=='direct' or until<=today())
        self.direct_calls=0;self.api_calls=0;self.api_credits=0

    def _direct(self,url):
        try:
            r=requests.get(url,timeout=12,headers={'User-Agent':USER_AGENT,'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8','Accept-Language':'es-MX,es;q=0.9'})
        except requests.RequestException as e:
            raise FetchError('NETWORK',type(e).__name__) from e
        if r.status_code!=200:raise FetchError(r.status_code,'Acceso directo rechazado')
        if not ('buscalibre' in r.url.lower()):raise FetchError('REDIRECT','Redirección a otro dominio')
        h=r.text
        if len(h)<1800 or any(s in norm(h[:9000]) for s in ('captcha', 'access denied', 'acceso denegado')):
            raise FetchError('HTML','Página incompleta o bloqueo')
        return h

    def _scrapingant(self,url):
        if not self.token:raise FetchError('SIN TOKEN','No hay clave ScrapingAnt; use --source direct o configure el token')
        global API_LAST
        for n in range(RETRY_COUNT+1):
            with API_LOCK:
                diff=API_GAP-(time.monotonic()-API_LAST)
                if diff>0:time.sleep(diff)
                API_LAST=time.monotonic()
            try:
                r=requests.get(API,params={'x-api-key':self.token,'url':url,'timeout':'45',
                          'proxy_type':'datacenter','browser':'false'},timeout=65)
            except requests.RequestException as e:
                if n==RETRY_COUNT:raise FetchError('NETWORK',type(e).__name__) from e
                time.sleep(min(2**n*2,15));continue
            with self.lock:
                self.api_calls+=1
                try:self.api_credits+=int(r.headers.get('Ant-credits-cost','0'))
                except ValueError:pass
            status=r.headers.get('Ant-page-status-code','')
            if r.status_code==200 and (not status or status=='200'):
                return r.text
            code=r.status_code if r.status_code!=200 else status
            if str(code) not in ('409','423','429','500','502','503','504','520','522','524'):
                raise FetchError(code,'La API o la página respondió con error')
            if n==RETRY_COUNT:raise FetchError(code,'Fallo persistente tras reintentos')
            time.sleep(min(2**n*2,15))
        raise FetchError('UNKNOWN','No hubo respuesta')

    def fetch(self,url,kind='catalog',page=1):
        d0,a0=self.direct_calls,self.api_calls
        if self.source in ('auto','direct') and self.direct_enabled:
            self.direct_calls+=1
            try:
                html=self._direct(url)
                soup=BeautifulSoup(html,'html.parser')
                valid=bool(soup.select_one('.box-producto')) if kind=='catalog' and page==1 else (
                    bool(soup.select_one('#data-info-libro')) if kind=='detail' else True)
                if not valid:raise FetchError('HTML','No se encontraron fichas del primer catálogo')
                return Receipt(html,0,0,1)
            except FetchError:
                if self.source=='direct':raise
                # Una comprobación diaria por entorno; no perder tiempo con 53 fallos directos.
                self.direct_enabled=False
                self.direct_status[self.env]=(date.today()+timedelta(days=1)).isoformat()
        b0=self.api_credits
        html=self._scrapingant(url)
        return Receipt(html,self.api_calls-a0,self.api_credits-b0,self.direct_calls-d0)


def catalog_url(base,n):
    if n==1:return base
    u=urlparse(base)
    return urlunparse((u.scheme,u.netloc,u.path,'',urlencode({'page':n}),''))


def extract_page(html,base,catalog,n):
    soup=BeautifulSoup(html,'html.parser')
    cards=soup.select('.box-producto')
    rows=[];issues=[];notes=[]
    for i,c in enumerate(cards,1):
        title=txt(c.select_one('h3.nombre'))
        author=txt(c.select_one('.autor:not(.metas)'))
        edition=txt(c.select_one('.autor.metas'))
        current=money(txt(c.select_one('.box-precios strong'))) # nunca <del>
        a=c.select_one('a[href*="/libro-"]')
        link=a.get('href','').strip() if a else ''
        if link.startswith('/'):link='https://www.buscalibre.com.mx'+link
        if not any([title,author,edition,current is not None,link]) and not c.get('data-id_producto'):
            continue
        if not edition or not title or current is None:
            if not edition and not title and current is None:
                notes.append(f'Página {n} tarjeta {i}: ficha de origen sin título, editorial ni precio, id={c.get("data-id_producto","")}')
            elif not edition or (edition and norm(edition.split(',',1)[0].strip())==norm(catalog)):
                issues.append(f'Página {n} tarjeta {i}: datos esenciales ausentes: {title or "sin título"}')
            continue
        publisher=edition.split(',',1)[0].strip()
        if norm(publisher)!=norm(catalog):continue
        if not link:
            issues.append(f'Página {n} tarjeta {i}: falta enlace de {title}')
            continue
        f=hardcover(edition)
        rows.append(dict(catalogo=catalog,pagina=n,titulo=title,autor=author,edicion=edition,
                         formato_catalogo='DURA' if f is True else 'NO DURA' if f is False else 'DESCONOCIDO',
                         precio_mxn=f'{current:.2f}',url=link,
                         candidato_menos_200='SI' if current<LIMIT else 'NO'))
    origin=urlparse(base)
    links={1}
    for a in soup.select('a[href]'):
        u=urlparse(a.get('href',''))
        if u.netloc and u.netloc.lower()!=origin.netloc.lower():continue
        if u.path.rstrip('/')!=origin.path.rstrip('/'):continue
        links.update(int(p) for p in parse_qs(u.query).get('page',[]) if p.isdigit())
    return dict(rows=rows,issues=issues,notes=notes,cards=len(cards),pages=links,
                title=txt(soup.title))


@dataclass
class Catalog:
    name:str
    base:str
    page:int=1
    finished:bool=False
    closed:bool=False
    last_count:int=0
    last_urls:set=field(default_factory=set)
    full:list=field(default_factory=list)
    partial:list=field(default_factory=list)
    failed:list=field(default_factory=list)
    terminals:list=field(default_factory=list)
    issues:list=field(default_factory=list)
    notes:list=field(default_factory=list)
    records:list=field(default_factory=list)
    why:str='Sin cierre verificado'


def one_page(s,trans):
    n=s.page
    if n>MAX_PAGES:
        s.failed.append(n);s.finished=True; s.issues.append('Límite de seguridad de paginación alcanzado');return
    print(f'[{s.name}] Página {n}',flush=True)
    try:
        raw=trans.fetch(catalog_url(s.base,n),'catalog',n)
        p=extract_page(raw.html,s.base,s.name,n)
    except FetchError as e:
        s.failed.append(n);s.finished=True;s.issues.append(f'Página {n}: {e.status} {e}');return
    if p['issues']:
        # Un segundo intento solo si falta precio/título; no repetir por autores faltantes.
        try:
            again=extract_page(trans.fetch(catalog_url(s.base,n),'catalog',n).html,s.base,s.name,n)
            if len(again['issues'])<len(p['issues']):p=again
        except FetchError:pass
    s.notes.extend(p['notes'])
    if p['cards']==0:
        if 'buscalibre' not in norm(p['title']):
            s.failed.append(n);s.issues.append(f'Página {n}: HTML sin fichas y título no reconocido, posible bloqueo ({p["title"][:60]})');s.finished=True;return
        s.terminals.append(n)
        if n>1 and s.last_count<50:
            s.closed=True;s.why=f'Página terminal {n} vacía, después de página con {s.last_count} fichas'
        else:
            s.failed.append(n);s.issues.append(f'Página {n} vacía, fin no demostrado ({p["title"][:60]})')
        s.finished=True;return
    urls={r['url'] for r in p['rows']}
    if n>1 and urls and urls==s.last_urls:
        s.failed.append(n);s.issues.append(f'Página {n} repite exactamente los libros de la anterior');s.finished=True;return
    s.records.extend(p['rows'])
    if p['issues']:
        s.partial.append(n);s.issues.extend(p['issues'])
    else:s.full.append(n)
    s.last_count=p['cards'];s.last_urls=urls
    if n+1 not in p['pages'] and p['cards']<50:
        s.closed=True;s.finished=True;s.why=f'Página {n} con {p["cards"]} fichas sin enlace a {n+1}'
    else:s.page=n+1


def detail_info(html):
    soup=BeautifulSoup(html,'html.parser')
    return dict(title=txt(soup.select_one('#data-info-libro p.tituloProducto')),
                author=txt(soup.select_one('#data-info-libro p.font-size-h1')),
                publisher=txt(soup.select_one('#metadata-editorial')),
                binding=txt(soup.select_one('[id="metadata-encuadernación"]')),
                category=txt(soup.select_one('[id="metadata-categorías"]')),
                price=money(txt(soup.select_one('#detallePrecio strong.precio'))),
                stock=txt(soup.select_one('#detallePrecio p.stock')),
                can_buy=bool(soup.select_one('#detallePrecio #addToCart:not([disabled])')),
                synopsis=txt(soup.select_one('#sinopsis') or soup.select_one('.sinopsis') or soup.select_one('#descripcion')))


def is_novel(category,synopsis):
    cat=norm(category)
    bad=r'\b(ensayo|ensayos|cocina|recetas|gastronomia|biografia|biografias|poesia|poesias|arqueologia|historia real|historias reales|cuento|cuentos|comic|manga|teatro)\b'
    if re.search(bad,cat):return False
    if re.search(r'\b(novela|novelas)\b',cat):return True
    desc=norm(synopsis)
    if re.search(r'\b(no es una novela|novela grafica|novela en verso)\b',desc):return False
    return bool(re.search(r'\b(esta novela|la novela narra|su primera novela|su segunda novela|novela historica)\b',desc))


def load_state():
    path=DIR/'estado_monitor_v7.json'
    try:st=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    except (OSError,ValueError):st={}
    if not isinstance(st,dict):st={}
    st.setdefault('history',{});st.setdefault('alerted',{});st.setdefault('direct_status',{})
    # Migrar una observación real de V6; no convertirla en tres fechas artificiales.
    if not st.get('imported_v6'):
        old=DIR/'estado_monitor_v6_base.json'
        if old.exists():
            try:
                data=json.loads(old.read_text(encoding='utf-8'))
                for link,val in data.get('prices',{}).items():
                    p=decimal_or_none(val.get('precio_mxn'))
                    when=val.get('when','')[:10]
                    if p is not None and re.fullmatch(r'\d{4}-\d{2}-\d{2}',when):
                        st['history'].setdefault(link,{'days':{}})['days'][when]={'min':f'{p:.2f}','max':f'{p:.2f}'}
                st['imported_v6']=True
            except (OSError,ValueError):pass
    return st


def update_history(st,rows):
    d=today()
    oldest=(date.fromisoformat(d)-timedelta(days=HISTORY_DAYS)).isoformat()
    for r in rows:
        link=r['url'];amount=decimal_or_none(r['precio_mxn'])
        if amount is None:continue
        ent=st['history'].setdefault(link,{'days':{}})
        ent.setdefault('days',{})
        item=ent['days'].get(d)
        if item:
            lo=decimal_or_none(item.get('min'));hi=decimal_or_none(item.get('max'))
            lo=min(lo,amount) if lo is not None else amount
            hi=max(hi,amount) if hi is not None else amount
        else:lo=hi=amount
        ent['days'][d]={'min':f'{lo:.2f}','max':f'{hi:.2f}'}
    for ent in st['history'].values():
        for day in list(ent.get('days',{})):
            if day<oldest:del ent['days'][day]


def historical_reference(st,link):
    """Mediana de precios altos observados en AL MENOS 3 días DISTINTOS previos.
    Un precio tachado, el precio actual y 3 consultas el mismo día NO prueban 'habitual'.
    """
    ent=st['history'].get(link,{})
    high=[]
    for day,price_range in ent.get('days',{}).items():
        if day>=today():continue
        v=decimal_or_none(price_range.get('min'))
        # Día entero observado >=400 (mínimo diario), no un solo pico puntual.
        if v is not None and v>=REFERENCE:high.append(v)
    if len(high)<MIN_REFERENCE_DAYS:return None,len(high)
    return Decimal(str(statistics.median(high))),len(high)


def check_offers(trans,records,st):
    checked=[];offers=[]
    low=[x for x in records if x['candidato_menos_200']=='SI']
    for row in low:
        f=hardcover(row['edicion'])
        if f is False:
            checked.append({**row,'resultado':'DESCARTADO: no es tapa dura'});continue
        reference,days=historical_reference(st,row['url'])
        if reference is None:
            checked.append({**row,'resultado':f'PENDIENTE: referencia >=$400 acreditada en {days}/{MIN_REFERENCE_DAYS} días anteriores'});continue
        before=st['alerted'].get(row['url'],{})
        if before.get('price')==row['precio_mxn']:
            checked.append({**row,'resultado':'ALERTA ANTERIOR SIN CAMBIOS'});continue
        try:
            info=detail_info(trans.fetch(row['url'],'detail').html)
        except FetchError as e:
            checked.append({**row,'resultado':f'NO VERIFICABLE: error ficha {e.status}'});continue
        if norm(info['publisher'])!=norm(row['catalogo']):
            reason='RECHAZADO: editorial distinta'
        elif info['price'] is None or info['price']>=LIMIT:
            reason='RECHAZADO: precio actual de ficha no es <200'
        elif not info['title'] or norm(info['title'])!=norm(row['titulo']):
            reason='RECHAZADO: título distinto'
        elif hardcover(info['binding']) is not True:
            reason='RECHAZADO: encuadernación no confirma tapa dura'
        elif not is_novel(info['category'],info['synopsis']):
            reason='RECHAZADO: categoría/sinopsis no confirman novela'
        elif not info['can_buy'] or re.search(r'agotad|sin stock',norm(info['stock'])):
            reason='RECHAZADO: existencia/compra no confirmadas'
        else:reason=None
        if reason:
            checked.append({**row,'resultado':reason,'categoria':info['category'],'encuadernacion_ficha':info['binding']});continue
        offer={**row,'precio_mxn':f'{info["price"]:.2f}',
               'autor':row['autor'] or info['author'], 'precio_referencia':f'{reference:.2f}',
               'dias_referencia':days,'categoria':info['category'],
               'encuadernacion_ficha':info['binding'], 'existencias':info['stock'] or 'Sin número indicado',
               'confirmado_utc':now()}
        checked.append({**offer,'resultado':'OFERTA VERIFICADA: novela, tapa dura, precio e histórico'})
        offers.append(offer)
    return low,checked,offers


def send_telegram_text(message):
    token=os.environ.get('TELEGRAM_BOT_TOKEN','').strip()
    dest=os.environ.get('TELEGRAM_CHAT_ID','').strip()
    if not token or not dest:
        return False,'Telegram pendiente de configurar'
    try:
        response=requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
            data={'chat_id':dest,'text':message,'disable_web_page_preview':'true'},timeout=20)
        if response.status_code==200 and response.json().get('ok'):
            return True,'Enviado'
        return False,f'HTTP {response.status_code}'
    except (requests.RequestException,ValueError) as e:
        return False,type(e).__name__


def send_telegram(offer):
    tok=os.environ.get('TELEGRAM_BOT_TOKEN','').strip();dest=os.environ.get('TELEGRAM_CHAT_ID','').strip()
    if not tok or not dest:return False,'Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID'
    msg=(f'📚 NOVELA EN TAPA DURA: OFERTA VERIFICADA\n'
         f'{offer["titulo"]}\nAutor: {offer["autor"] or "Sin identificar"}\n'
         f'Editorial: {offer["catalogo"]}\n'
         f'Precio actual: ${offer["precio_mxn"]} MXN\n'
         f'Precio observado anteriormente: ${offer["precio_referencia"]} MXN '
         f'(>= $400 en {offer["dias_referencia"]} días distintos; no precio tachado)\n'
         f'Existencias: {offer["existencias"]}\n{offer["url"]}')
    return send_telegram_text(msg)


def write_csv(file,rows,columns):
    with (DIR/file).open('w',newline='',encoding='utf-8-sig') as fp:
        writer=csv.DictWriter(fp,fieldnames=columns,extrasaction='ignore')
        writer.writeheader();writer.writerows(rows)


def scan(token,source,mode,parallel=1):
    started=time.monotonic();start_at=now()
    st=load_state();trans=Transport(token,source,st)
    catalogs=[Catalog(*t) for t in CATALOGS]
    # Rondas equilibradas por editorial. En plan gratis, una petición simultánea.
    # En plan de pago, --parallel 2/3/4 acelera sin saltarse ninguna página.
    with ThreadPoolExecutor(max_workers=parallel) as executor:
        while any(not x.finished for x in catalogs):
            pending=[c for c in catalogs if not c.finished]
            list(executor.map(lambda c: one_page(c,trans), pending))
    rows=[x for c in catalogs for x in c.records]
    full=all(c.closed and not c.partial and not c.failed for c in catalogs)
    # Nunca generar avisos de ofertas ni incorporar precios históricos de un
    # recorrido incompleto; podría faltar justo el libro con una gran rebaja.
    if full:
        low,checked,offers=check_offers(trans,rows,st)
        update_history(st,rows)
    else:
        low=[x for x in rows if x['candidato_menos_200']=='SI']
        checked=[]
        offers=[]
    st['last_scan']={
        'utc':now(), 'complete':full, 'catalogs':[
            {'name':c.name,'pages_complete':len(c.full),
             'partial':c.partial,'failed':c.failed,'closed':c.closed}
            for c in catalogs],
        'products':len(rows), 'candidates':len(low),
    }
    messages=[]
    if mode=='monitor' and full:
        for offer in offers:
            ok,reason=send_telegram(offer)
            messages.append(f'{offer["titulo"]}: {reason}')
            if ok:st['alerted'][offer['url']]={'price':offer['precio_mxn'],
                    'reference':offer['precio_referencia'],'at':now()}
    if mode=='monitor' and not full and st.get('last_failure_notice_day')!=today():
        problem=', '.join(
            f'{c.name}: parciales {c.partial}, errores {c.failed}, cierre {c.closed}'
            for c in catalogs if not c.closed or c.partial or c.failed)
        ok,reason=send_telegram_text(
            '⚠️ MONITOR BUSCALIBRE: revisión incompleta. '
            'No se generan alertas de ofertas ni se actualiza el histórico.\n'
            + problem + '\nRevisa GitHub Actions.')
        messages.append('Cobertura incompleta: '+reason)
        if ok:st['last_failure_notice_day']=today()
    (DIR/'estado_monitor_v7.json').write_text(json.dumps(st,ensure_ascii=False,indent=1)+'\n',encoding='utf-8')
    write_csv('todos_los_libros_v7.csv',rows,FIELDS)
    write_csv('candidatos_menos_200_v7.csv',low,FIELDS)
    write_csv('verificacion_candidatos_v7.csv',checked,FIELDS+['resultado','categoria','encuadernacion_ficha','precio_referencia','dias_referencia','existencias'])
    write_csv('ofertas_confirmadas_v7.csv',offers,FIELDS+['precio_referencia','dias_referencia','categoria','encuadernacion_ficha','existencias','confirmado_utc'])
    lines=['MONITOR BUSCALIBRE V7 (criterios estrictos)',f'Inicio UTC: {start_at}',f'Fin UTC: {now()}',
           f'Duración: {time.monotonic()-started:.1f} segundos',f'Modo: {mode}; fuente: {source}; concurrencia: {parallel}',
           'Requisitos: NOVELA + TAPA DURA + PRECIO ACTUAL <200 + >=400 OBSERVADO EN 3 DÍAS DISTINTOS ANTERIORES.',
           'Un precio tachado no cuenta como precio habitual. No se admiten géneros desconocidos.', '']
    for c in catalogs:
        lines.extend([f'EDITORIAL: {c.name}',f'  Páginas completas: {c.full}',
                      f'  Páginas parciales: {c.partial}',f'  Páginas fallidas: {c.failed}',
                      f'  Páginas vacías terminales: {c.terminals}',
                      f'  Paginación cerrada: {"SÍ" if c.closed else "NO"}. {c.why}',
                      f'  Fichas con datos esenciales: {len(c.records)}'])
        lines.extend('  ERROR: '+e for e in c.issues)
        lines.extend('  AVISO: '+e for e in c.notes)
        lines.append('')
    lines.extend(['RESUMEN',f'  Cobertura de páginas de los 4 catálogos: {"COMPLETA" if full else "INCOMPLETA"}',
                  f'  Páginas con libros: {sum(len(c.full)+len(c.partial) for c in catalogs)}',
                  f'  Productos recuperados: {len(rows)}',f'  Libros <200 sin clasificar: {len(low)}',
                  f'  Ofertas que cumplen TODOS los criterios: {len(offers)}',
                  f'  Solicitudes directas HTTP: {trans.direct_calls}',
                  f'  Solicitudes a ScrapingAnt: {trans.api_calls}',
                  f'  Créditos API reportados por cabeceras: {trans.api_credits} (0 puede significar cabecera ausente)',
                  '  Nota: precios históricos de un solo día NO prueban que un libro usualmente valga >=400.',
                  '  Un cambio de página o bloqueo puede impedir futuras revisiones.',
                  '  Libro sin título/precio en la tienda: advertencia, no considerado producto verificable.'])
    lines.extend('  ALERTA: '+x for x in messages)
    (DIR/'reporte_integral_v7.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('\n'.join(lines[-14:]),flush=True)
    return 0 if full else 1


def self_test():
    assert money('$ 199.99')==Decimal('199.99')
    assert money('$ 1,400.00')==Decimal('1400')
    assert hardcover('Alba, 2023, Tapa Dura, Nuevo') is True
    assert hardcover('Alba, 2023, Tapa Blanda, Nuevo') is False
    assert hardcover('Acantilado, 2022, Libro') is None
    assert is_novel('Novelas contemporáneas','')
    assert not is_novel('Ensayos literarios','Esta novela')
    demo='<div class="box-producto" data-id_producto="12"><a href="https://www.buscalibre.com.mx/libro-test/123/p/12"></a><h3 class="nombre">Prueba</h3><div class="autor metas">Alba, Tapa Dura, Nuevo</div><div class="box-precios"><del>$ 500.00</del><strong>$ 199.00</strong></div></div>'
    e=extract_page(demo,CATALOGS[0][1],'Alba',1)
    assert len(e['rows'])==1 and e['rows'][0]['precio_mxn']=='199.00'
    st={'history':{'book':{'days':{
        '2026-11-01':{'min':'420.00'},'2026-11-02':{'min':'430.00'},
        '2026-11-03':{'min':'450.00'},'2026-11-04':{'min':'130.00'}}}}}
    ref,n=historical_reference(st,'book')
    assert ref==Decimal('430.00') and n==3
    st['history']['book']['days']['2026-11-02']['min']='199.00'
    ref,n=historical_reference(st,'book')
    assert ref is None and n==2
    print('TEST CORRECTO: formato, precio actual, editorial, autor opcional, novela e histórico de tres días.')


def probe_direct():
    url=CATALOGS[0][1]
    lines=['PRUEBA DE ACCESO DIRECTO SIN SCRAPINGANT',f'Fecha UTC: {now()}','No consume créditos ni solicita la clave.']
    try:
        t=Transport('','direct',{'direct_status':{}})
        h=t.fetch(url,'catalog',1).html
        p=extract_page(h,url,'Alba',1)
        ok=p['cards']>=10 and len(p['rows'])>=10
        lines.extend([f'Acceso directo: {"FUNCIONA" if ok else "NO CONFIRMADO"}',
                      f'Tarjetas: {p["cards"]}; con precio y datos: {len(p["rows"])}',
                      f'Anomalías: {len(p["issues"])}'])
    except FetchError as e:
        ok=False;lines.extend(['Acceso directo: NO FUNCIONA',f'Error: {e.status} {e}'])
    (DIR/'prueba_acceso_directo_v7.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('\n'.join(lines),flush=True)
    return 0 if ok else 1


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--mode',choices=['test','monitor'],default='test')
    p.add_argument('--source',choices=['auto','direct','scrapingant'],default='auto')
    p.add_argument('--self-test',action='store_true')
    p.add_argument('--probe-direct',action='store_true')
    p.add_argument('--parallel',type=int,choices=[1,2,3,4],default=1,help='1 si ScrapingAnt gratis; >1 solo si plan de pago')
    args=p.parse_args()
    if args.self_test:self_test();return 0
    if args.probe_direct:return probe_direct()
    try:token=get_token(args.source=='scrapingant' or (args.source=='auto' and os.environ.get('GITHUB_ACTIONS')!='true'))
    except (RuntimeError,EOFError) as e:
        print('ERROR:',e);return 2
    return scan(token,args.source,args.mode,args.parallel)

if __name__=='__main__':
    try:sys.exit(main())
    except KeyboardInterrupt:print('Interrumpido por el usuario');sys.exit(130)
