"""
Copa do Mundo 2026 Tracker — Backend
FastAPI + cache simples em memória
"""

import os
import time
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="Copa 2026 Tracker")

# ── Cache em memória ──────────────────────────────────────────────────────────
CACHE: dict = {}
CACHE_TTL = 300  # 5 minutos


def cache_get(key: str):
    entry = CACHE.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    return None


def cache_set(key: str, data):
    CACHE[key] = {"data": data, "ts": time.time()}


# ── Chave da API (variável de ambiente no Render) ─────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


async def call_claude(prompt: str, max_tokens: int = 4000) -> dict:
    """Chama a API do Claude com web search e retorna o JSON parseado."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY não configurada")

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": max_tokens,
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
        "messages": [{"role": "user", "content": prompt}],
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

    text = "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
    import re, json
    m = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", text)
    if not m:
        raise HTTPException(status_code=502, detail="Resposta da IA não continha JSON válido")
    return json.loads(m.group(0))


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/api/copa")
async def get_copa():
    """Dados principais da copa — cacheados por 5 min."""
    cached = cache_get("copa_main")
    if cached:
        return {"cached": True, **cached}

    prompt = """Busque informações ATUALIZADAS sobre a Copa do Mundo FIFA 2026 (começa 11 junho 2026, EUA/México/Canadá, 48 times, 16 grupos de 3).

Priorize: sofascore.com, footystats.org, ogol.com.br, fifa.com, espn.com.br

Retorne SOMENTE JSON:
{
  "status": "pre-tournament",
  "news": "frase curta",
  "lastUpdated": "horário",
  "dataSources": ["SofaScore","FootyStats","oGol"],
  "artilheiros": [
    {"pos":1,"player":"Nome","team":"País","gols":3,"assistencias":1,"source":"SofaScore"}
  ],
  "matches": [
    {
      "id":"m1","group":"A","team1":"Brasil","team2":"México",
      "score1":null,"score2":null,"status":"upcoming",
      "date":"2026-06-12","time":"15:00","venue":"SoFi Stadium, Los Angeles",
      "minute":null,"statsSource":"SofaScore","stats":null,"events":[]
    }
  ],
  "standings": {
    "A": [{"team":"Brasil","pts":0,"g":0,"v":0,"e":0,"d":0,"gp":0,"gc":0,"sg":0}]
  }
}

Para jogos encerrados/ao vivo, inclua stats e events. Retorne SOMENTE o JSON."""

    result = await call_claude(prompt, max_tokens=4000)
    cache_set("copa_main", result)
    return {"cached": False, **result}


@app.get("/api/h2h")
async def get_h2h(team1: str, team2: str):
    """Histórico H2H entre dois times — cacheado por 1 hora."""
    key = f"h2h_{team1}_{team2}"
    cached = cache_get(key)
    if cached:
        return {"cached": True, **cached}

    prompt = f"""Busque histórico de confrontos H2H entre {team1} e {team2} seleções nacionais.
Consulte ogol.com.br e sofascore.com.
Retorne SOMENTE JSON:
{{"source":"oGol","totalJogos":10,"vitorias1":4,"empates":3,"vitorias2":3,
"golsMarcados1":14,"golsMarcados2":10,
"ultimosJogos":[{{"date":"2022-12-09","competition":"Copa 2022","score1":2,"score2":1,"winner":1,"venue":"Qatar"}}],
"curiosidade":"frase histórica",
"footyStats":{{"mediaGols":2.4,"ambosMarcam":45,"over25":60,"mediaEscanteios":9.2}}}}"""

    result = await call_claude(prompt, max_tokens=1200)
    # H2H não muda — cache de 1 hora
    CACHE[key] = {"data": result, "ts": time.time() - CACHE_TTL + 3600}
    return {"cached": False, **result}


@app.get("/api/summary")
async def get_summary(team1: str, team2: str, score1: int, score2: int, date: str, venue: str = ""):
    """Resumo narrativo de uma partida — cacheado."""
    key = f"summary_{team1}_{team2}_{score1}_{score2}"
    cached = cache_get(key)
    if cached:
        return {"cached": True, "text": cached}

    prompt = f"""Escreva resumo narrativo em pt-BR estilo crônica esportiva do jogo {team1} {score1}x{score2} {team2} Copa 2026 em {date} {venue}.
Consulte sofascore.com e ogol.com.br. Máx 250 palavras. SOMENTE o texto."""

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 600,
                "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()

    text = "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text").strip()
    cache_set(key, text)
    return {"cached": False, "text": text}


@app.get("/api/raiox")
async def get_raiox(team: str):
    """Raio-X tático de um time — cacheado por 1 hora."""
    key = f"raiox_{team}"
    cached = cache_get(key)
    if cached:
        return {"cached": True, **cached}

    prompt = f"""Busque informações táticas de {team} na Copa 2026 via sofascore.com e transfermarkt.com.
Retorne SOMENTE JSON:
{{"team":"{team}","treinador":"Nome","formacao":"4-3-3","estilo":"descrição","valorElenco":"1.2B €","source":"Transfermarkt",
"jogadoresChave":[{{"nome":"Jogador","posicao":"Posição","valor":"100M€","clube":"Clube"}}],
"forcas":["força1"],"fraquezas":["fraqueza1"],
"esquemaCampo":{{"goleiro":"Nome","zagueiros":["N1","N2"],"laterais":["N1","N2"],"meios":["N1","N2","N3"],"atacantes":["N1","N2","N3"]}}}}"""

    result = await call_claude(prompt, max_tokens=1800)
    CACHE[key] = {"data": result, "ts": time.time() - CACHE_TTL + 3600}
    return {"cached": False, **result}


@app.get("/api/simulacao")
async def get_simulacao(grupos: str = ""):
    """Simula o mata-mata com base nos grupos atuais."""
    prompt = f"""Simule o mata-mata da Copa 2026 com base nas classificações atuais dos grupos.
{f"Dados dos grupos: {grupos}" if grupos else "Use os times classificados que você encontrar."}
Retorne SOMENTE JSON:
{{"source":"Simulação IA",
"oitavas":[{{"time1":"Brasil","time2":"Portugal","prob1":62,"prob2":38}}],
"quartas":[{{"time1":"Brasil","time2":"França","prob1":55,"prob2":45}}],
"semis":[{{"time1":"Brasil","time2":"Espanha","prob1":58,"prob2":42}}],
"final":{{"time1":"Brasil","time2":"Argentina","prob1":52,"prob2":48}},
"favorito":"Brasil",
"probCampeao":[{{"team":"Brasil","prob":18}},{{"team":"França","prob":15}}]}}"""

    return await call_claude(prompt, max_tokens=1500)


@app.get("/api/cache/status")
async def cache_status():
    """Mostra status do cache (útil para debug)."""
    now = time.time()
    return {
        key: {
            "age_seconds": round(now - entry["ts"]),
            "expires_in": max(0, round(CACHE_TTL - (now - entry["ts"]))),
        }
        for key, entry in CACHE.items()
    }


@app.delete("/api/cache")
async def clear_cache():
    """Limpa o cache forçando nova busca."""
    CACHE.clear()
    return {"ok": True, "message": "Cache limpo"}


# ── Serve o frontend estático ─────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/{full_path:path}")
async def catch_all(full_path: str):
    return FileResponse("static/index.html")
