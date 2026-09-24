"""Local web server for the Gold-layer chatbot - a business owner opens a
browser page instead of a terminal. Reuses app/chatbot.py's tool-calling
logic; no new AI logic here, just a thin HTTP wrapper + static page.

Usage: python api/server.py, then open http://localhost:8000
"""
import os
import sys

import duckdb
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
import chatbot  # noqa: E402
from groq import Groq  # noqa: E402

app = FastAPI()
client = Groq()

ACCOUNTS = chatbot.ACCOUNTS


class ChatRequest(BaseModel):
    messages: list[dict]  # full history, [{role, content}, ...] - stateless server


@app.post("/api/chat")
def api_chat(req: ChatRequest):
    history = [{"role": "system", "content": chatbot.SYSTEM}] + req.messages
    answer = chatbot.chat(client, history)
    return {"answer": answer}


@app.get("/api/stats")
def api_stats():
    con = duckdb.connect()
    total = con.execute(f"SELECT count(*) FROM read_parquet('{ACCOUNTS}')").fetchone()[0]
    bands = con.execute(f"""
        SELECT band, count(*) FROM read_parquet('{ACCOUNTS}') GROUP BY 1 ORDER BY 2 DESC
    """).fetchall()
    countries = con.execute(f"""
        SELECT primary_country_code, count(*) AS n FROM read_parquet('{ACCOUNTS}')
        WHERE primary_country_code IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC LIMIT 8
    """).fetchall()
    top = con.execute(f"""
        SELECT company_key, primary_country_code, contact_score, band, fit_score, urgency_score
        FROM read_parquet('{ACCOUNTS}') ORDER BY contact_score DESC LIMIT 20
    """).fetchall()
    tiers = con.execute(f"""
        SELECT resolution_tier, count(*) FROM read_parquet('{ACCOUNTS}') GROUP BY 1 ORDER BY 2 DESC
    """).fetchall()
    return {
        "total": total,
        "bands": [{"band": b, "count": n} for b, n in bands],
        "countries": [{"country": c, "count": n} for c, n in countries],
        "top": [
            {"company_key": r[0], "country": r[1], "score": r[2], "band": r[3], "fit": r[4], "urgency": r[5]}
            for r in top
        ],
        "tiers": [{"tier": t, "count": n} for t, n in tiers],
    }


@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


if __name__ == "__main__":
    import uvicorn
    print("Open http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
