"""Local web server for the Gold-layer chatbot - a business owner opens a
browser page instead of a terminal. Reuses app/chatbot.py's tool-calling
logic; no new AI logic here, just a thin HTTP wrapper + static page.

Usage: python api/server.py, then open http://localhost:8000
"""
import os
import sys

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


class ChatRequest(BaseModel):
    messages: list[dict]  # full history, [{role, content}, ...] - stateless server


@app.post("/api/chat")
def api_chat(req: ChatRequest):
    history = [{"role": "system", "content": chatbot.SYSTEM}] + req.messages
    answer = chatbot.chat(client, history)
    return {"answer": answer}


@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


if __name__ == "__main__":
    import uvicorn
    print("Open http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
