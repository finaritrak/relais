"""Clients des fournisseurs. Tout est normalisé au format OpenAI Chat Completions."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from .config import ConfigFournisseur


class ErreurFournisseur(Exception):
    """Le fournisseur n'a pas pu répondre. ``reessayable`` = on peut basculer sur un autre."""

    def __init__(self, message: str, *, reessayable: bool = True, statut: Optional[int] = None) -> None:
        super().__init__(message)
        self.reessayable = reessayable
        self.statut = statut


@dataclass
class Reponse:
    texte: str
    fin: str
    jetons_entree: int
    jetons_sortie: int
    modele: str
    brut: Dict[str, Any]


def _verifier(r: httpx.Response, nom: str) -> Dict[str, Any]:
    if r.status_code == 429 or r.status_code >= 500 or r.status_code == 529:
        raise ErreurFournisseur(f"{nom}: HTTP {r.status_code}", statut=r.status_code)
    if r.status_code >= 400:
        # Erreur de la requête elle-même (400, 401…) : inutile d'essayer ailleurs avec 401,
        # mais une 400 peut venir d'un modèle qui refuse un paramètre → on bascule quand même.
        raise ErreurFournisseur(
            f"{nom}: HTTP {r.status_code} {r.text[:200]}",
            reessayable=r.status_code not in (401, 403),
            statut=r.status_code,
        )
    try:
        data: Dict[str, Any] = r.json()
    except ValueError as exc:
        raise ErreurFournisseur(f"{nom}: réponse non JSON") from exc
    return data


class Fournisseur:
    def __init__(self, cfg: ConfigFournisseur, client: httpx.AsyncClient) -> None:
        self.cfg = cfg
        self.client = client

    async def completer(self, messages: List[Dict[str, Any]], params: Dict[str, Any]) -> Reponse:
        raise NotImplementedError

    def cout(self, rep: Reponse) -> float:
        return (
            rep.jetons_entree * self.cfg.prix_entree_par_million
            + rep.jetons_sortie * self.cfg.prix_sortie_par_million
        ) / 1_000_000

    async def _post(self, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self.client.post(url, timeout=self.cfg.timeout, **kwargs)
        except httpx.TimeoutException as exc:
            raise ErreurFournisseur(f"{self.cfg.nom}: délai dépassé") from exc
        except httpx.HTTPError as exc:
            raise ErreurFournisseur(f"{self.cfg.nom}: {exc.__class__.__name__}") from exc


class OpenAICompatible(Fournisseur):
    """OpenAI, Ollama (``http://localhost:11434/v1``), vLLM, LM Studio, Mistral…"""

    PARAMS = ("temperature", "top_p", "max_tokens", "stop", "presence_penalty", "frequency_penalty", "seed")

    async def completer(self, messages: List[Dict[str, Any]], params: Dict[str, Any]) -> Reponse:
        corps: Dict[str, Any] = {"model": self.cfg.modele, "messages": messages}
        corps.update({k: v for k, v in params.items() if k in self.PARAMS and v is not None})
        entetes = {"Authorization": f"Bearer {self.cfg.cle_api}"} if self.cfg.cle_api else {}
        r = await self._post(f"{self.cfg.base_url}/chat/completions", json=corps, headers=entetes)
        data = _verifier(r, self.cfg.nom)
        try:
            choix = data["choices"][0]
            texte = choix["message"].get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ErreurFournisseur(f"{self.cfg.nom}: format de réponse inattendu") from exc
        usage = data.get("usage") or {}
        return Reponse(
            texte=texte,
            fin=choix.get("finish_reason") or "stop",
            jetons_entree=int(usage.get("prompt_tokens") or 0),
            jetons_sortie=int(usage.get("completion_tokens") or 0),
            modele=data.get("model") or self.cfg.modele,
            brut=data,
        )


class Anthropic(Fournisseur):
    """API Messages d'Anthropic (Claude)."""

    VERSION = "2023-06-01"
    FIN = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length", "tool_use": "tool_calls"}

    async def completer(self, messages: List[Dict[str, Any]], params: Dict[str, Any]) -> Reponse:
        system = "\n\n".join(_texte(m) for m in messages if m.get("role") == "system")
        conversation: List[Dict[str, Any]] = []
        for m in messages:
            role = m.get("role")
            if role not in ("user", "assistant"):
                continue
            if conversation and conversation[-1]["role"] == role:
                conversation[-1]["content"] += "\n\n" + _texte(m)
            else:
                conversation.append({"role": role, "content": _texte(m)})
        corps: Dict[str, Any] = {
            "model": self.cfg.modele,
            "max_tokens": params.get("max_tokens") or self.cfg.max_tokens_defaut,
            "messages": conversation,
        }
        if system:
            corps["system"] = system
        for k in ("temperature", "top_p"):
            if params.get(k) is not None:
                corps[k] = params[k]
        if params.get("stop"):
            stop = params["stop"]
            corps["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)
        entetes = {"x-api-key": self.cfg.cle_api or "", "anthropic-version": self.VERSION}
        r = await self._post(f"{self.cfg.base_url}/v1/messages", json=corps, headers=entetes)
        data = _verifier(r, self.cfg.nom)
        texte = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        usage = data.get("usage") or {}
        return Reponse(
            texte=texte,
            fin=self.FIN.get(data.get("stop_reason") or "", "stop"),
            jetons_entree=int(usage.get("input_tokens") or 0),
            jetons_sortie=int(usage.get("output_tokens") or 0),
            modele=data.get("model") or self.cfg.modele,
            brut=data,
        )


def _texte(message: Dict[str, Any]) -> str:
    contenu = message.get("content")
    if isinstance(contenu, str):
        return contenu
    if isinstance(contenu, list):  # format multi-parties OpenAI : on garde le texte
        return "".join(p.get("text", "") for p in contenu if isinstance(p, dict))
    return ""


def creer(cfg: ConfigFournisseur, client: httpx.AsyncClient) -> Fournisseur:
    return (Anthropic if cfg.type == "anthropic" else OpenAICompatible)(cfg, client)


def reponse_openai(rep: Reponse, route: str, texte: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": route,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": rep.texte if texte is None else texte},
                "finish_reason": rep.fin,
            }
        ],
        "usage": {
            "prompt_tokens": rep.jetons_entree,
            "completion_tokens": rep.jetons_sortie,
            "total_tokens": rep.jetons_entree + rep.jetons_sortie,
        },
    }
