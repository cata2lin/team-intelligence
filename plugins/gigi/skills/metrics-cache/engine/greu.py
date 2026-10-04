"""Rute GRELE (dashboard-uri de profitabilitate/analitica) in acelasi proces cu scanerul din depozit.

`async def` cu SQLite/calcule sincrone in corp bloca event loop-ul uvicorn: 30-sep si 1-oct, deschiderea
dashboard-urilor (15-76 s) a tinut pe loc TOATE scanarile (15,7 s, 18 s, 20 s -> 499 in aplicatie) si
`POST /api/oh-ingest` al Order Hub (15-74 s). `@greu` muta corpul in threadpool, deci loop-ul ramane liber.

UNA odata: corpul e Python care tine GIL-ul (calcule, json), iar trei deodata urcau o ruta rapida de la
40 ms la p50 830 ms; cu una, ~300 ms (recenzia V90, `gil_ab.py`). Nu se pierde nimic: inainte loop-ul le
rula oricum una dupa alta. Corpul NU are voie sa contina `await`: un apel async din el se face cu
`asyncio.run(f(...))` (bucla proprie a firului), nu pe bucla principala."""
import asyncio
import functools

from starlette.concurrency import run_in_threadpool

_LOCURI = asyncio.Semaphore(1)


def greu(fn):
    @functools.wraps(fn)
    async def w(*a, **k):
        async with _LOCURI:
            return await run_in_threadpool(fn, *a, **k)
    return w
