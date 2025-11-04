#!/usr/bin/env python3
import argparse
import asyncio
import aiohttp
import time
import socket
import signal
import sys
import os
from typing import Tuple, Optional

BANNER = r"""\
┌───────────────────────────────────────────────┐
│   Subdomain Live Checker (estricto, async)   │
└───────────────────────────────────────────────┘
"""

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

class UI:
    def __init__(self, total: int, use_ansi: bool):
        self.total = total
        self.use_ansi = use_ansi
        self.alive = 0
        self.done = 0
        self.printed_domains = 0
        self._print_initial()

    def _progress_str(self) -> str:
        return f"Completados {self.done}/{self.total} - Activos: {self.alive}"

    def _print_initial(self):
        sys.stdout.write(self._progress_str() + "\n\n")
        sys.stdout.flush()

    def update_progress(self):
        if self.use_ansi:
            up = self.printed_domains + 1
            sys.stdout.write(f"\x1b[{up}A\r{self._progress_str()}\x1b[K")
            sys.stdout.write(f"\x1b[{up}B")
            sys.stdout.flush()
        else:
            sys.stdout.write("\r" + self._progress_str())
            sys.stdout.flush()

    def print_active(self, domain: str):
        sys.stdout.write(f"{domain.strip()}\n")
        sys.stdout.flush()
        self.printed_domains += 1

async def worker(name: int, queue: asyncio.Queue, session: aiohttp.ClientSession,
                 ttfb_timeout: float, read_min_bytes: int, total_timeout: float,
                 per_request_delay: float,
                 counters: dict, activos_list: list, ui: UI,
                 stop_event: asyncio.Event):
    while True:
        if stop_event.is_set():
            return
        try:
            domain = await asyncio.wait_for(queue.get(), timeout=0.2)
        except asyncio.TimeoutError:
            if queue.empty():
                return
            continue
        try:
            if stop_event.is_set():
                queue.put_nowait(domain)
                return
            dom, alive = await probe(domain, session, ttfb_timeout, read_min_bytes, total_timeout)
            counters['done'] += 1
            ui.done = counters['done']
            if alive:
                counters['alive'] += 1
                ui.alive = counters['alive']
                activos_list.append(dom)
                ui.print_active(dom)
            ui.update_progress()
        finally:
            queue.task_done()
            if per_request_delay > 0:
                await asyncio.sleep(per_request_delay)

def drain_queue(q: asyncio.Queue):
    try:
        while True:
            q.get_nowait()
            q.task_done()
    except asyncio.QueueEmpty:
        pass

async def main_async(domains_file: str, concurrency: int, rps: float,
                     ttfb_timeout: float, read_min_bytes: int, total_timeout: float,
                     ipv4_only: bool, force_no_ansi: bool) -> int:
    print(BANNER)
    with open(domains_file, 'r', encoding='utf-8') as f:
        raw = [line.strip() for line in f]
    dominios = [d for d in raw if d]
    seen = set()
    dominios = [d for d in dominios if not (d in seen or seen.add(d))]
    total = len(dominios)
    if total == 0:
        return 0
    per_request_delay = 0.0
    if rps > 0:
        per_request_delay = max(0.0, concurrency / rps)
    connector_kwargs = {
        "limit": max(concurrency, 20),
        "limit_per_host": max(5, min(10, concurrency)),
        "ssl": True,
        "use_dns_cache": True
    }
    if ipv4_only:
        connector_kwargs["family"] = socket.AF_INET
    connector = aiohttp.TCPConnector(**connector_kwargs)
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; DomainChecker/strict/1.4)'}
    counters = {"done": 0, "alive": 0}
    activos_list: list[str] = []
    stop_event = asyncio.Event()
    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, stop_event.set)
        loop.add_signal_handler(signal.SIGTERM, stop_event.set)
    except (NotImplementedError, RuntimeError):
        pass
    use_ansi = (not force_no_ansi) and sys.stdout.isatty()
    ui = UI(total=total, use_ansi=use_ansi)
    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        queue: asyncio.Queue[str] = asyncio.Queue()
        for d in dominios:
            queue.put_nowait(d)
        tasks = [
            asyncio.create_task(
                worker(i, queue, session, ttfb_timeout, read_min_bytes,
                       total_timeout, per_request_delay,
                       counters, activos_list, ui, stop_event),
                name=f"worker-{i}"
            )
            for i in range(concurrency)
        ]
        try:
            while True:
                try:
                    await asyncio.wait_for(queue.join(), timeout=0.2)
                    break
                except asyncio.TimeoutError:
                    if stop_event.is_set():
                        drain_queue(queue)
                        break
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    return 0 if not stop_event.is_set() else 130

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('domains_file', type=str)
    parser.add_argument('--concurrency', type=int, default=50)
    parser.add_argument('--rps', type=float, default=20.0)
    parser.add_argument('--ttfb-timeout', type=float, default=3.0)
    parser.add_argument('--read-min-bytes', type=int, default=64)
    parser.add_argument('--total-timeout', type=float, default=8.0)
    parser.add_argument('--ipv4-only', action='store_true')
    parser.add_argument('--no-ansi', action='store_true')
    args = parser.parse_args()
    exit_code = 0
    try:
        exit_code = asyncio.run(main_async(
            args.domains_file, args.concurrency, args.rps,
            args.ttfb_timeout, args.read_min_bytes, args.total_timeout,
            args.ipv4_only, args.no_ansi
        ))
    except KeyboardInterrupt:
        print("\nCancelado por el usuario (Ctrl+C).")
        exit_code = 130
    finally:
        try:
            sys.stdout.flush()
        except Exception:
            pass
    sys.exit(exit_code)

if __name__ == '__main__':
    main()
