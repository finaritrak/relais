import httpx
import pytest
from fastapi.testclient import TestClient

from relais.app import creer_app
from relais.cli import main
from relais.config import ErreurConfig, charger, depuis_dict
from relais.routeur import Routeur

from .conftest import CLAUDE, LOCAL, config_test, ok_anthropic, ok_openai


def client(amont, **surcharges):
    cfg = config_test(**surcharges)
    return TestClient(creer_app(cfg, Routeur(cfg, amont.client())))


def test_chat_compatible_openai(amont):
    amont.sur(LOCAL, ok_openai("Salama"))
    with client(amont) as c:
        r = c.post(
            "/v1/chat/completions", json={"model": "defaut", "messages": [{"role": "user", "content": "hi"}]}
        )
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == "Salama"
        assert r.headers["X-Relais-Fournisseur"] == "local"
        assert r.headers["X-Relais-Cache"] == "miss"
        assert c.get("/health").json()["statut"] == "ok"
        assert [m["id"] for m in c.get("/v1/models").json()["data"]] == ["defaut", "local-seul"]
        assert c.get("/stats").json()["fournisseurs"]["local"]["requetes"] == 1


def test_auth(amont):
    amont.sur(LOCAL, ok_openai())
    with client(amont, serveur={"cles_clients": ["secret"]}) as c:
        corps = {"messages": [{"role": "user", "content": "hi"}]}
        assert c.post("/v1/chat/completions", json=corps).status_code == 401
        assert (
            c.post("/v1/chat/completions", json=corps, headers={"Authorization": "Bearer faux"}).status_code
            == 401
        )
        ok = c.post("/v1/chat/completions", json=corps, headers={"Authorization": "Bearer secret"})
        assert ok.status_code == 200
        assert c.get("/health").status_code == 200  # la santé reste publique


def test_erreurs_api(amont):
    amont.sur(LOCAL, httpx.Response(500))
    amont.sur(CLAUDE, httpx.Response(503))
    with client(amont) as c:
        msg = [{"role": "user", "content": "hi"}]
        assert c.post("/v1/chat/completions", json={"model": "x", "messages": msg}).status_code == 404
        assert c.post("/v1/chat/completions", json={"messages": msg, "stream": True}).status_code == 400
        r = c.post("/v1/chat/completions", json={"messages": msg})
        assert r.status_code == 503
        assert len(r.json()["error"]["essais"]) == 2


def test_masques_dans_les_entetes(amont):
    amont.sur(LOCAL, httpx.Response(500))
    amont.sur(CLAUDE, ok_anthropic("ok"))
    with client(amont) as c:
        r = c.post(
            "/v1/chat/completions", json={"messages": [{"role": "user", "content": "a@b.mg 034 12 345 67"}]}
        )
        assert r.headers["X-Relais-Masques"] == "2"


def test_config_erreurs(monkeypatch):
    base = {"nom": "a", "base_url": "http://x", "modele": "m"}
    with pytest.raises(ErreurConfig):
        depuis_dict({})
    with pytest.raises(ErreurConfig):
        depuis_dict({"fournisseurs": [{"base_url": "x", "modele": "m"}]})
    with pytest.raises(ErreurConfig):
        depuis_dict({"fournisseurs": [{**base, "type": "autre"}]})
    with pytest.raises(ErreurConfig):
        depuis_dict({"fournisseurs": [{"nom": "a", "modele": "m"}]})
    with pytest.raises(ErreurConfig):
        depuis_dict({"fournisseurs": [base], "routes": {"r": []}})
    with pytest.raises(ErreurConfig):
        depuis_dict({"fournisseurs": [base], "routes": {"r": ["zzz"]}})
    with pytest.raises(ErreurConfig, match="ABSENTE"):
        depuis_dict({"fournisseurs": [{**base, "cle_api_env": "ABSENTE"}]})
    monkeypatch.setenv("K", "v")
    monkeypatch.setenv("CLES", "a,b")
    cfg = depuis_dict(
        {"fournisseurs": [{**base, "cle_api_env": "K"}], "serveur": {"cles_clients_env": "CLES"}}
    )
    assert cfg.fournisseurs["a"].cle_api == "v"
    assert cfg.routes == {"defaut": ["a"]}
    assert cfg.cles_clients == ["a", "b"]
    assert cfg.fournisseurs["a"].externe is False


def test_exemple_de_config_valide(monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk")
    monkeypatch.setenv("RELAIS_CLES_CLIENTS", "")
    cfg = charger("relais.example.toml")
    assert cfg.routes["defaut"] == ["local", "claude"]
    assert cfg.fournisseurs["claude"].externe
    assert main(["check", "-c", "relais.example.toml"]) == 0
    out = capsys.readouterr().out
    assert "local (local) → claude (externe, masqué)" in out and "aucune clé client" in out


def test_cli_masquer_et_erreur(capsys, monkeypatch):
    assert main(["masquer", "Mail a@b.mg"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "Mail ⟦EMAIL_1⟧"
    assert "a@b.mg" in captured.err
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("034 12 345 67"))
    assert main(["masquer"]) == 0
    assert capsys.readouterr().out.strip() == "⟦TEL_1⟧"
    assert main(["check", "-c", "introuvable.toml"]) == 2
