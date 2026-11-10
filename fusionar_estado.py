#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fusiona observaciones concurrentes de BuscaLibre sin perder fechas o alertas.

Uso:
    python fusionar_estado.py --incoming /ruta/estado-local.json --target estado_monitor_v7.json
    python fusionar_estado.py --self-test
"""
from __future__ import annotations
import argparse
import copy
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

def amount(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None

def newer(one, two, field):
    return copy.deepcopy(one if str(one.get(field, '')) >= str(two.get(field, '')) else two)

def merge(remote: dict, incoming: dict) -> dict:
    """Preserva los extremos diarios de precios y la alerta más reciente."""
    result = copy.deepcopy(remote)
    for key, value in incoming.items():
        result.setdefault(key, copy.deepcopy(value))

    result.setdefault('history', {})
    for url, observation in incoming.get('history', {}).items():
        if url not in result['history']:
            result['history'][url] = copy.deepcopy(observation)
            continue
        target = result['history'][url]
        target.setdefault('days', {})
        for day, range_in in observation.get('days', {}).items():
            if day not in target['days']:
                target['days'][day] = copy.deepcopy(range_in)
                continue
            range_old = target['days'][day]
            mins = [v for v in (amount(range_old.get('min')), amount(range_in.get('min'))) if v is not None]
            maxes = [v for v in (amount(range_old.get('max')), amount(range_in.get('max'))) if v is not None]
            if mins:
                range_old['min'] = f'{min(mins):.2f}'
            if maxes:
                range_old['max'] = f'{max(maxes):.2f}'
        target['last_seen_utc'] = max(str(target.get('last_seen_utc', '')), str(observation.get('last_seen_utc', '')))
        if not target['last_seen_utc']:
            target.pop('last_seen_utc', None)

    # Conservar el límite del historial también después de la fusión.
    cutoff = (date.today() - timedelta(days=180)).isoformat()
    for observation in result['history'].values():
        for day in list(observation.get('days', {})):
            if day < cutoff:
                del observation['days'][day]

    result.setdefault('alerted', {})
    for url, value in incoming.get('alerted', {}).items():
        if url not in result['alerted']:
            result['alerted'][url] = copy.deepcopy(value)
        else:
            result['alerted'][url] = newer(result['alerted'][url], value, 'at')

    result.setdefault('direct_status', {})
    for env, until in incoming.get('direct_status', {}).items():
        result['direct_status'][env] = max(str(result['direct_status'].get(env, '')), str(until))

    if incoming.get('imported_v6'):
        result['imported_v6'] = True
    if incoming.get('last_scan'):
        if not result.get('last_scan') or incoming['last_scan'].get('utc', '') >= result['last_scan'].get('utc', ''):
            result['last_scan'] = copy.deepcopy(incoming['last_scan'])
    # La última instantánea COMPLETA se conserva aunque llegue una revisión
    # parcial o dos ejecuciones actualicen main casi al mismo tiempo.
    snapshot_at=incoming.get('last_complete_catalog_utc','')
    if (snapshot_at and incoming.get('last_complete_catalog_urls')
            and snapshot_at>=result.get('last_complete_catalog_utc','')):
        result['last_complete_catalog_utc']=snapshot_at
        result['last_complete_catalog_urls']=copy.deepcopy(incoming['last_complete_catalog_urls'])
    return result

def self_test():
    remote = {'history': {'book': {'days': {'2026-11-07': {'min': '399.00', 'max': '450.00'}}}},
              'alerted': {'a': {'at': '2026-11-07T10:00:00+00:00','price':'180.00'}},
              'last_scan': {'utc': '2026-11-07T10:00:00+00:00','complete': True}}
    incoming = {'history': {'book': {'days': {'2026-11-07': {'min': '190.00', 'max': '410.00'}}},
                            'new': {'days': {'2026-11-07': {'min': '500.00', 'max': '500.00'}}}},
                'alerted': {'a': {'at': '2026-11-07T11:00:00+00:00','price':'170.00'}},
                'last_scan': {'utc': '2026-11-07T11:00:00+00:00','complete': False}}
    out = merge(remote, incoming)
    assert out['history']['book']['days']['2026-11-07']=={'min':'190.00','max':'450.00'}
    assert out['history']['new']['days']['2026-11-07']['min']=='500.00'
    assert out['alerted']['a']['price']=='170.00'
    assert out['last_scan']['complete'] is False
    assert remote['history']['book']['days']['2026-11-07']['min']=='399.00'
    print('OK: fusión de mínimos/máximos, alertas y escaneo sin pérdida de datos.')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--incoming')
    parser.add_argument('--target')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.incoming or not args.target:
        parser.error('Se requieren --incoming y --target')
    source = json.loads(Path(args.incoming).read_text(encoding='utf-8'))
    path = Path(args.target)
    old = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(old, dict) or not isinstance(source, dict):
        raise ValueError('Los estados deben ser objetos JSON')
    merged = merge(old, source)
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print('Historial fusionado: ', len(merged.get('history', {})), 'libros.')

if __name__ == '__main__':
    main()
