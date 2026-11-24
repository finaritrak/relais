import httpx
import pytest

from relais.config import ConfigFournisseur
from relais.fournisseurs import Anthropic, ErreurFournisseur, OpenAICompatible

from .conftest import CLAUDE, LOCAL, ok_anthropic


def cfg(**k):
    base = dict(
        nom="x", type="anthropic", base_url="https://anthropic.test", modele="m", externe=True, cle_api="k"
    )
    base.update(k)
    return ConfigFournisseur(**base)


async def test_anthropic_format(amont):
    amont.sur(CLAUDE, ok_anthropic())
    f = Anthropic(cfg(), amont.client())
    msgs = [
        {"role": "system", "content": "S1"},
        {"role": "system", "content": "S2"},
        {"role": "user", "content": "a"},
        {"role": "user", "content": "b"},
        {"role": "assistant", "content": "c"},
        {"role": "tool", "content": "ignoré"},
        {"role": "user", "content": None},
    ]
    rep = await f.completer(msgs, {"temperature": 0.2, "stop": "FIN", "max_tokens": 50, "seed": 3})
    c = amont.corps("anthropic")
    assert c["system"] == "S1\n\nS2"
    assert c["messages"][:2] == [{"role": "user", "content": "a\n\nb"}, {"role": "assistant", "content": "c"}]
    assert c["max_tokens"] == 50 and c["temperature"] == 0.2 and c["stop_sequences"] == ["FIN"]
    assert "seed" not in c
    req = amont.requetes[-1]
    assert req.headers["x-api-key"] == "k" and req.headers["anthropic-version"] == "2023-06-01"
    assert rep.fin == "stop" and rep.jetons_entree == 1000


async def test_anthropic_max_tokens_par_defaut_et_fin_longueur(amont):
    amont.sur(
        CLAUDE,
        httpx.Response(200, json={"content": [], "stop_reason": "max_tokens", "usage": {}}),
    )
    rep = await Anthropic(cfg(max_tokens_defaut=77), amont.client()).completer(
        [{"role": "user", "content": "x"}], {}
    )
    assert amont.corps("anthropic")["max_tokens"] == 77
    assert rep.fin == "length" and rep.texte == ""


async def test_openai_cle_et_params(amont):
    amont.sur(LOCAL, httpx.Response(200, json={"choices": [{"message": {"content": None}}]}))
    f = OpenAICompatible(cfg(type="openai", base_url="http://ollama.test/v1", cle_api="sk"), amont.client())
    rep = await f.completer([{"role": "user", "content": "x"}], {"temperature": 0, "inconnu": 1})
    c = amont.corps("ollama")
    assert c["temperature"] == 0 and "inconnu" not in c
    assert amont.requetes[-1].headers["Authorization"] == "Bearer sk"
    assert rep.texte == "" and rep.jetons_entree == 0


@pytest.mark.parametrize("statut, reessayable", [(400, True), (401, False), (403, False), (529, True)])
async def test_erreurs_http(amont, statut, reessayable):
    amont.sur(CLAUDE, httpx.Response(statut, text="err"))
    with pytest.raises(ErreurFournisseur) as exc:
        await Anthropic(cfg(), amont.client()).completer([{"role": "user", "content": "x"}], {})
    assert exc.value.reessayable is reessayable and exc.value.statut == statut


async def test_erreur_reseau(amont):
    def coupe(req):
        raise httpx.ConnectError("refusé", request=req)

    amont.sur(CLAUDE, coupe)
    with pytest.raises(ErreurFournisseur, match="ConnectError"):
        await Anthropic(cfg(), amont.client()).completer([{"role": "user", "content": "x"}], {})
