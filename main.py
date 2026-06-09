"""
Copa do Mundo 2026 Tracker — Backend
FastAPI + cache em memória + proxy seguro pra Anthropic API
"""

import os
import re
import json
import time
import httpx
import logging
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Copa 2026 Tracker")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Cache ─────────────────────────────────────────────────────────────────────
CACHE: dict = {}

def cache_get(key, ttl=300):
    e = CACHE.get(key)
    return e["data"] if e and (time.time() - e["ts"]) < ttl else None

def cache_set(key, data):
    CACHE[key] = {"data": data, "ts": time.time()}

# ── Config ────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Modelos disponíveis publicamente (sem preview)
MODELS = ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"]

async def call_claude(prompt: str, max_tokens: int = 4000, plain_text: bool = False):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(500, "ANTHROPIC_API_KEY não configurada no Render")

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    last_err = None
    for model in MODELS:
        # Tenta primeiro COM web search, depois SEM se der 400
        for use_search in [True, False]:
            body = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if use_search:
                body["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]

            try:
                async with httpx.AsyncClient(timeout=90) as client:
                    resp = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers=headers, json=body,
                    )

                    if resp.status_code in (400, 404):
                        err_body = resp.json() if resp.headers.get("content-type","").startswith("application/json") else {}
                        last_err = f"{model} {'c/search' if use_search else 's/search'}: {resp.status_code} {err_body.get('error',{}).get('message','')}"
                        logger.warning(last_err)
                        break  # tenta sem search neste model

                    resp.raise_for_status()
                    data = resp.json()
                    logger.info(f"✅ {model} {'c/search' if use_search else 's/search'}")

                    text = "".join(
                        b.get("text","") for b in data.get("content",[])
                        if b.get("type") == "text"
                    )

                    if plain_text:
                        return text.strip()

                    m = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", text)
                    if not m:
                        raise HTTPException(502, f"IA não retornou JSON. Texto: {text[:300]}")
                    return json.loads(m.group(0))

            except httpx.HTTPStatusError as e:
                last_err = f"{model}: HTTP {e.response.status_code}"
                logger.error(last_err)
                if e.response.status_code not in (400, 404, 529):
                    raise HTTPException(502, last_err)

    raise HTTPException(502, f"Nenhum modelo funcionou. Último erro: {last_err}")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    key = ANTHROPIC_API_KEY
    return {
        "ok": True,
        "api_key_set": bool(key),
        "api_key_prefix": (key[:16] + "...") if key else "NÃO CONFIGURADA",
        "models_to_try": MODELS,
        "cache_keys": list(CACHE.keys()),
    }

@app.get("/api/copa")
async def get_copa():
    cached = cache_get("copa_main", ttl=300)
    if cached:
        return {"cached": True, **cached}

    prompt = """Você é um assistente especializado em futebol. Busque informações sobre a Copa do Mundo FIFA 2026.

A Copa do Mundo 2026 acontece nos EUA, México e Canadá, com 48 seleções em 16 grupos de 3 times cada.
Data de início: 11 de junho de 2026. Final: 19 de julho de 2026.

Com base no que você sabe e no que encontrar buscando, retorne um JSON com a estrutura abaixo.
IMPORTANTE: retorne SOMENTE o JSON, sem texto antes ou depois, sem blocos markdown.

{
  "status": "pre-tournament",
  "news": "Copa do Mundo 2026 começa em 11 de junho nos EUA, México e Canadá",
  "lastUpdated": "junho 2026",
  "dataSources": ["FIFA", "ESPN"],
  "artilheiros": [],
  "matches": [
    {
      "id": "A1",
      "group": "A",
      "team1": "Brasil",
      "team2": "México",
      "score1": null,
      "score2": null,
      "status": "upcoming",
      "date": "2026-06-13",
      "time": "18:00",
      "venue": "SoFi Stadium, Los Angeles",
      "minute": null,
      "statsSource": null,
      "stats": null,
      "events": []
    },
    {
      "id": "A2",
      "group": "A",
      "team1": "Brasil",
      "team2": "Polônia",
      "score1": null,
      "score2": null,
      "status": "upcoming",
      "date": "2026-06-17",
      "time": "15:00",
      "venue": "MetLife Stadium, Nova York",
      "minute": null,
      "statsSource": null,
      "stats": null,
      "events": []
    }
  ],
  "standings": {
    "A": [
      {"team": "Brasil", "pts": 0, "g": 0, "v": 0, "e": 0, "d": 0, "gp": 0, "gc": 0, "sg": 0},
      {"team": "México", "pts": 0, "g": 0, "v": 0, "e": 0, "d": 0, "gp": 0, "gc": 0, "sg": 0},
      {"team": "Polônia", "pts": 0, "g": 0, "v": 0, "e": 0, "d": 0, "gp": 0, "gc": 0, "sg": 0}
    ]
  }
}

Preencha com todos os grupos (A até P) e todos os jogos que souber.
Retorne SOMENTE o JSON."""

    result = await call_claude(prompt, max_tokens=4000)
    cache_set("copa_main", result)
    return {"cached": False, **result}


@app.get("/api/h2h")
async def get_h2h(team1: str, team2: str):
    key = f"h2h_{team1}_{team2}"
    cached = cache_get(key, ttl=3600)
    if cached:
        return {"cached": True, **cached}

    prompt = f"""Histórico de confrontos diretos entre as seleções de {team1} e {team2}.
Retorne SOMENTE JSON (sem markdown):
{{"source":"oGol","totalJogos":10,"vitorias1":4,"empates":3,"vitorias2":3,"golsMarcados1":14,"golsMarcados2":10,
"ultimosJogos":[{{"date":"2022-12-09","competition":"Copa do Mundo 2022","score1":2,"score2":1,"winner":1,"venue":"Qatar"}}],
"curiosidade":"Curiosidade sobre esse confronto histórico",
"footyStats":{{"mediaGols":2.4,"ambosMarcam":45,"over25":60,"mediaEscanteios":9.2}}}}"""

    result = await call_claude(prompt, max_tokens=1200)
    cache_set(key, result)
    return {"cached": False, **result}


@app.get("/api/summary")
async def get_summary(team1: str, team2: str, score1: int, score2: int, date: str, venue: str = ""):
    key = f"sum_{team1}_{team2}_{score1}_{score2}"
    cached = cache_get(key, ttl=86400)
    if cached:
        return {"cached": True, "text": cached}

    prompt = f"""Escreva um resumo narrativo em português brasileiro, estilo crônica esportiva, sobre:
{team1} {score1} x {score2} {team2} — Copa do Mundo 2026, {date}, {venue}
Inclua: ritmo do jogo, momentos decisivos, destaques e impacto na classificação.
Máximo 200 palavras. Apenas o texto, sem título."""

    text = await call_claude(prompt, max_tokens=500, plain_text=True)
    cache_set(key, text)
    return {"cached": False, "text": text}


@app.get("/api/raiox")
async def get_raiox(team: str):
    key = f"raiox_{team}"
    cached = cache_get(key, ttl=3600)
    if cached:
        return {"cached": True, **cached}

    prompt = f"""Informações táticas da seleção de {team} para a Copa do Mundo 2026.
Retorne SOMENTE JSON (sem markdown):
{{"team":"{team}","treinador":"Nome","formacao":"4-3-3","estilo":"Descrição do estilo","valorElenco":"1.2 bi €","source":"Transfermarkt",
"jogadoresChave":[{{"nome":"Jogador","posicao":"Posição","valor":"100M€","clube":"Clube"}}],
"forcas":["Força 1"],"fraquezas":["Fraqueza 1"],
"esquemaCampo":{{"goleiro":"Nome","zagueiros":["N1","N2"],"laterais":["N1","N2"],"meios":["N1","N2","N3"],"atacantes":["N1","N2","N3"]}}}}"""

    result = await call_claude(prompt, max_tokens=1500)
    cache_set(key, result)
    return {"cached": False, **result}


@app.get("/api/simulacao")
async def get_simulacao():
    prompt = """Simule o mata-mata da Copa do Mundo 2026 com probabilidades de título para cada seleção favorita.
Retorne SOMENTE JSON (sem markdown):
{"source":"Simulação IA",
"oitavas":[{"time1":"Brasil","time2":"Portugal","prob1":62,"prob2":38}],
"quartas":[{"time1":"Brasil","time2":"França","prob1":55,"prob2":45}],
"semis":[{"time1":"Brasil","time2":"Espanha","prob1":58,"prob2":42}],
"final":{"time1":"Brasil","time2":"Argentina","prob1":52,"prob2":48},
"favorito":"Brasil",
"probCampeao":[
  {"team":"Brasil","prob":18},{"team":"França","prob":15},{"team":"Argentina","prob":13},
  {"team":"Espanha","prob":12},{"team":"Inglaterra","prob":10},{"team":"Alemanha","prob":9},
  {"team":"Portugal","prob":8},{"team":"Países Baixos","prob":7}
]}"""
    return await call_claude(prompt, max_tokens=1200)


@app.delete("/api/cache")
async def clear_cache():
    CACHE.clear()
    return {"ok": True}


# ── Frontend ──────────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")

@app.get("/{full_path:path}")
async def catch_all(full_path: str):
    return FileResponse("static/index.html")
