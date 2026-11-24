from __future__ import annotations

import json
from typing import Any, Callable, Dict, List

import httpx
import pytest

from relais.config import depuis_dict

LOCAL = "http://ollama.test/v1/chat/completions"
CLAUDE = "https://anthropic.test/v1/messages"


def ok_openai(texte: str = "Bonjour", entree: int = 10, sortie: int = 5) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "qwen",
            "choices": [{"message": {"role": "assistant", "content": texte}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": entree, "completion_tokens": sortie},
        },
    )


def ok_anthropic(texte: str = "Salut", entree: int = 1000, sortie: int = 500) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "claude-test",
            "content": [{"type": "text", "text": texte}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": entree, "output_tokens": sortie},
        },
    )


class Amont:
    """Faux fournisseurs : on programme la réponse de chaque URL."""

    def __init__(self) -> None:
        self.reponses: Dict[str, Callable[[httpx.Request], httpx.Response]] = {}
        self.requetes: List[httpx.Request] = []

    def sur(self, url: str, rep: Any) -> None:
        self.reponses[url] = rep if callable(rep) else (lambda req, rep=rep: rep)

    def gerer(self, req: httpx.Request) -> httpx.Response:
        self.requetes.append(req)
        f = self.reponses.get(str(req.url))
        if f is None:
            return httpx.Response(404)
        return f(req)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.gerer))

    def corps(self, url_part: str) -> Dict[str, Any]:
        for r in reversed(self.requetes):
            if url_part in str(r.url):
                return json.loads(r.content)
        raise AssertionError(url_part)

    def nb(self, url_part: str) -> int:
        return sum(url_part in str(r.url) for r in self.requetes)


def config_test(**surcharges: Any):
    data: Dict[str, Any] = {
        "fournisseurs": [
            {
                "nom": "local",
                "type": "openai",
                "base_url": "http://ollama.test/v1",
                "modele": "qwen",
                "externe": False,
            },
            {
                "nom": "claude",
                "type": "anthropic",
                "base_url": "https://anthropic.test",
                "modele": "claude-test",
                "cle_api": "sk-test",
                "prix_entree_par_million": 3.0,
                "prix_sortie_par_million": 15.0,
            },
        ],
        "routes": {"defaut": ["local", "claude"], "local-seul": ["local"]},
        "disjoncteur": {"echecs": 2, "pause_secondes": 30},
    }
    for k, v in surcharges.items():
        data[k] = v
    return depuis_dict(data)


@pytest.fixture
def amont() -> Amont:
    return Amont()
