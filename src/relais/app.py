"""Serveur HTTP compatible OpenAI : n'importe quel SDK OpenAI peut pointer sur relais."""

from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional, Union

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from . import __version__
from .config import Config
from .routeur import AucunFournisseur, RouteInconnue, Routeur


class Message(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: Union[str, List[Dict[str, Any]], None] = None


class RequeteChat(BaseModel):
    model_config = ConfigDict(extra="ignore")
    model: str = "defaut"
    messages: List[Message]
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    stop: Union[str, List[str], None] = None
    seed: Optional[int] = None
    stream: bool = False


def creer_app(config: Config, routeur: Optional[Routeur] = None) -> FastAPI:
    @asynccontextmanager
    async def cycle(app: FastAPI) -> AsyncIterator[None]:
        app.state.routeur = routeur or Routeur(config)
        yield
        await app.state.routeur.fermer()

    app = FastAPI(title="relais", version=__version__, lifespan=cycle)

    def auth(authorization: Optional[str] = Header(default=None)) -> None:
        if not config.cles_clients:
            return
        jeton = (authorization or "").removeprefix("Bearer ").strip()
        if not any(hmac.compare_digest(jeton, k) for k in config.cles_clients):
            raise HTTPException(status_code=401, detail="Clé API invalide")

    @app.get("/health")
    def sante() -> Dict[str, str]:
        return {"statut": "ok", "version": __version__}

    @app.get("/v1/models", dependencies=[Depends(auth)])
    def modeles() -> Dict[str, Any]:
        return {
            "object": "list",
            "data": [{"id": r, "object": "model", "owned_by": "relais"} for r in config.routes],
        }

    @app.get("/stats", dependencies=[Depends(auth)])
    def stats() -> Dict[str, Any]:
        r: Routeur = app.state.routeur
        return r.stats()

    @app.post("/v1/chat/completions", dependencies=[Depends(auth)])
    async def chat(req: RequeteChat) -> JSONResponse:
        if req.stream:
            raise HTTPException(status_code=400, detail="stream=true n'est pas encore pris en charge")
        r: Routeur = app.state.routeur
        params = req.model_dump(exclude={"model", "messages", "stream"}, exclude_none=True)
        messages = [m.model_dump(exclude_none=True) for m in req.messages]
        try:
            res = await r.completer(req.model, messages, params)
        except RouteInconnue:
            raise HTTPException(
                status_code=404, detail=f"Route inconnue : {req.model!r}. Routes : {list(config.routes)}"
            ) from None
        except AucunFournisseur as exc:
            return JSONResponse(
                status_code=503,
                content={"error": {"message": str(exc), "type": "relais_indisponible", "essais": exc.essais}},
            )
        return JSONResponse(
            content=res.corps,
            headers={
                "X-Relais-Fournisseur": res.fournisseur,
                "X-Relais-Masques": str(res.masques),
                "X-Relais-Cache": "hit" if res.depuis_cache else "miss",
            },
        )

    return app
