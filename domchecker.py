#!/usr/bin/env python3
import argparse
import asyncio
import aiohttp
import time
import socket
from typing import Tuple, Optional

async def get_with_ttfb(session: aiohttp.ClientSession, url: str,
                        ttfb_timeout: float, read_min_bytes: int,
                        total_timeout: float) -> Tuple[int, float, float]:
    timeout = aiohttp.ClientTimeout(
        total=total_timeout,
        sock_connect=ttfb_timeout,
        sock_read=ttfb_timeout
    )
    start = time.perf_counter()
    async with session.get(url, timeout=timeout, allow_redirects=True, max_redirects=5) as resp:
        elapsed_headers = time.perf_counter() - start

        if resp.status == 204:
            return resp.status, round(elapsed_headers, 3), 0.0

        want = read_min_bytes
        got = 0
        first_byte_elapsed: Optional[float] = None

        while got < want:
            try:
                chunk = await asyncio.wait_for(
                    resp.content.read(min(8192, want - got)),
                    timeout=ttfb_timeout
                )
            except asyncio.TimeoutError:
                raise asyncio.TimeoutError("TTFB/body timeout")
            if not chunk:
                break
            if first_byte_elapsed is None:
                first_byte_elapsed = time.perf_counter() - start
            got += len(chunk)

        if first_byte_elapsed is None and want > 0:
            raise asyncio.TimeoutError("No se recibió ningún byte de cuerpo a tiempo")

        return resp.status, round(elapsed_headers, 3), round(first_byte_elapsed or 0.0, 3)

async def probe(domain: str, session: aiohttp.ClientSession,
                ttfb_timeout: float, read_min_bytes: int, total_timeout: float):
    schemes = ['https://', 'http://']
    for scheme in schemes:
        url = scheme + domain
        try:
            status, _, _ = await get_with_ttfb(session, url, ttfb_timeout, read_min_bytes, total_timeout)
            alive = 200 <= status < 300 
            return domain, alive
        except Exception:
            continue
    return domain, False

async def worker(name: int, queue: asyncio.Queue, session: aiohttp.ClientSession,
                 ttfb_timeout: float, read_min_bytes: int, total_timeout: float,
                 per_request_delay: float,
                 counters: dict, activos_list: list, total: int):
    while True:
        try:
            domain = queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        try:
            dom, alive = await probe(domain, session, ttfb_timeout, read_min_bytes, total_timeout)
            counters['done'] += 1
            if alive:
                counters['alive'] += 1
                activos_list.append(dom)
            print(f"\rCompletados {counters['done']}/{total} - Activos: {counters['alive']}", end='', flush=True)
        finally:
            queue.task_done()
            if per_request_delay > 0:
                await asyncio.sleep(per_request_delay)

async def main_async(domains_file: str, concurrency: int, rps: float,
                     ttfb_timeout: float, read_min_bytes: int, total_timeout: float,
                     ipv4_only: bool):
    # Leer dominios y deduplicar manteniendo orden
    with open(domains_file, 'r', encoding='utf-8') as f:
        raw = [line.strip() for line in f]
    dominios = [d for d in raw if d]
    seen = set()
    dominios = [d for d in dominios if not (d in seen or seen.add(d))]
    total = len(dominios)
    if total == 0:
        return
     
    per_request_delay = 0.0
    if rps > 0:
        per_request_delay = max(0.0, concurrency / rps)

    connector_kwargs = {
        "limit": max(concurrency, 20)
        "limit_per_host": max(5, min(10, concurrency)), 
        "ssl": True,
        "use_dns_cache": True
    }
    if ipv4_only:
        connector_kwargs["family"] = socket.AF_INET  

    connector = aiohttp.TCPConnector(**connector_kwargs)
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; DomainChecker/strict/1.1)'}
    counters = {"done": 0, "alive": 0}
    activos_list: list[str] = []

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        queue: asyncio.Queue[str] = asyncio.Queue()
        for d in dominios:
            queue.put_nowait(d)

        # Lanzar workers (no miles de tareas: sólo 'concurrency' corriendo)
        tasks = [
            asyncio.create_task(
                worker(i, queue, session, ttfb_timeout, read_min_bytes, total_timeout,
                       per_request_delay, counters, activos_list, total)
            )
            for i in range(concurrency)
        ]

        await queue.join()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


    print()
    for d in activos_list:
        print(d)

def main():
    parser = argparse.ArgumentParser(
        description="Checker estricto con control de concurrencia y rate-limit (salida minimalista)."
    )
    parser.add_argument('domains_file', type=str, help='Archivo con subdominios (uno por línea).')
    parser.add_argument('--concurrency', type=int, default=50,
                        help='Workers simultáneos (baja si tu red sufre).')
    parser.add_argument('--rps', type=float, default=20.0,
                        help='Peticiones totales por segundo (aprox). Usa 0 para desactivar rate-limit.')
    parser.add_argument('--ttfb-timeout', type=float, default=3.0,
                        help='Timeout para conexión/primeros bytes (s).')
    parser.add_argument('--read-min-bytes', type=int, default=64,
                        help='Bytes mínimos del cuerpo para considerar activo.')
    parser.add_argument('--total-timeout', type=float, default=8.0,
                        help='Timeout total duro por petición (s).')
    parser.add_argument('--ipv4-only', action='store_true',
                        help='Forzar IPv4 (evita picos por resolución/IPv6).')
    args = parser.parse_args()

    asyncio.run(main_async(
        args.domains_file, args.concurrency, args.rps,
        args.ttfb_timeout, args.read_min_bytes, args.total_timeout,
        args.ipv4_only
    ))

if __name__ == '__main__':
    main()
