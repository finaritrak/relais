from datetime import date

import httpx
import pytest

from relais.routeur import AucunFournisseur, RouteInconnue, Routeur

from .conftest import CLAUDE, LOCAL, config_test, ok_anthropic, ok_openai

MSG = [{"role": "user", "content": "Bonjour"}]


async def test_local_d_abord(amont):
    amont.sur(LOCAL, ok_openai("Salama"))
    r = Routeur(config_test(), amont.client())
    res = await r.completer("defaut", MSG, {})
    assert res.fournisseur == "local"
    assert res.corps["choices"][0]["message"]["content"] == "Salama"
    assert res.corps["model"] == "defaut"
    assert amont.nb("anthropic") == 0
    assert res.cout_usd == 0


@pytest.mark.parametrize(
    "panne",
    [
        httpx.Response(500),
        httpx.Response(429),
        httpx.Response(200, text="pas du json"),
        httpx.Response(200, json={"oups": 1}),
    ],
)
async def test_bascule_vers_claude(amont, panne):
    amont.sur(LOCAL, panne)
    amont.sur(CLAUDE, ok_anthropic("Réponse de secours"))
    res = await Routeur(config_test(), amont.client()).completer("defaut", MSG, {})
    assert res.fournisseur == "claude"
    assert res.essais[0]["fournisseur"] == "local" and "erreur" in res.essais[0]["resultat"]
    assert res.corps["choices"][0]["message"]["content"] == "Réponse de secours"
    # coût : 1000 × 3 $/M + 500 × 15 $/M
    assert res.cout_usd == pytest.approx(0.0105)


async def test_bascule_sur_timeout(amont):
    def lent(req):
        raise httpx.ReadTimeout("trop lent", request=req)

    amont.sur(LOCAL, lent)
    amont.sur(CLAUDE, ok_anthropic())
    res = await Routeur(config_test(), amont.client()).completer("defaut", MSG, {})
    assert res.fournisseur == "claude"
    assert "délai" in res.essais[0]["resultat"]


async def test_masquage_uniquement_vers_externe(amont):
    amont.sur(LOCAL, httpx.Response(503))

    def echo(req):
        # Claude « répète » ce qu'il a reçu : on vérifie qu'il n'a vu que des jetons
        recu = req.read().decode()
        assert "rakoto@example.mg" not in recu and "034 12 345 67" not in recu
        return ok_anthropic("Je contacte ⟦EMAIL_1⟧ au ⟦TEL_1⟧.")

    amont.sur(CLAUDE, echo)
    msg = [
        {"role": "system", "content": "Tu es un assistant."},
        {"role": "user", "content": "Contacte rakoto@example.mg au 034 12 345 67"},
    ]
    res = await Routeur(config_test(), amont.client()).completer("defaut", msg, {})
    assert res.masques == 2
    assert res.corps["choices"][0]["message"]["content"] == "Je contacte rakoto@example.mg au 034 12 345 67."
    corps = amont.corps("anthropic")
    assert corps["system"] == "Tu es un assistant."
    assert corps["messages"][0]["content"] == "Contacte ⟦EMAIL_1⟧ au ⟦TEL_1⟧"


async def test_local_recoit_les_donnees_en_clair(amont):
    amont.sur(LOCAL, ok_openai())
    await Routeur(config_test(), amont.client()).completer(
        "defaut", [{"role": "user", "content": "a@b.mg"}], {}
    )
    assert amont.corps("ollama")["messages"][0]["content"] == "a@b.mg"


async def test_masquage_contenu_multiparties(amont):
    amont.sur(LOCAL, httpx.Response(500))
    amont.sur(CLAUDE, ok_anthropic())
    msg = [{"role": "user", "content": [{"type": "text", "text": "mail a@b.mg"}, {"type": "image_url"}]}]
    await Routeur(config_test(), amont.client()).completer("defaut", msg, {})
    assert amont.corps("anthropic")["messages"][0]["content"] == "mail ⟦EMAIL_1⟧"


async def test_budget_ecarte_les_payants(amont):
    amont.sur(LOCAL, httpx.Response(500))
    amont.sur(CLAUDE, ok_anthropic())
    jour = [date(2026, 11, 24)]
    r = Routeur(config_test(budget={"journalier_usd": 0.01}), amont.client(), aujourd_hui=lambda: jour[0])
    await r.completer("defaut", MSG, {})  # 0,0105 $ dépensés
    with pytest.raises(AucunFournisseur) as exc:
        await r.completer("defaut", MSG, {})
    assert "budget" in exc.value.essais[-1]["resultat"]
    jour[0] = date(2026, 11, 25)  # nouveau jour : compteur remis à zéro
    assert (await r.completer("defaut", MSG, {})).fournisseur == "claude"


async def test_disjoncteur(amont):
    amont.sur(LOCAL, httpx.Response(500))
    amont.sur(CLAUDE, ok_anthropic())
    t = [0.0]
    r = Routeur(config_test(), amont.client(), horloge=lambda: t[0])
    for _ in range(2):
        await r.completer("defaut", MSG, {})
    assert amont.nb("ollama") == 2
    res = await r.completer("defaut", MSG, {})
    assert "disjoncteur" in res.essais[0]["resultat"]
    assert amont.nb("ollama") == 2  # plus sollicité
    assert r.stats()["fournisseurs"]["local"]["disjoncteur_ouvert"]
    t[0] = 31.0
    amont.sur(LOCAL, ok_openai())
    assert (await r.completer("defaut", MSG, {})).fournisseur == "local"


async def test_saturation_bascule(amont):
    """Modèle local occupé (toutes ses places prises) → la requête part vers Claude."""
    amont.sur(LOCAL, ok_openai("local"))
    amont.sur(CLAUDE, ok_anthropic("rapide"))
    cfg = config_test()
    r = Routeur(cfg, amont.client())
    sem = r.etats["local"].semaphore
    for _ in range(cfg.fournisseurs["local"].max_concurrence):
        await sem.acquire()
    res = await r.completer("defaut", MSG, {})
    assert res.fournisseur == "claude"
    assert "saturé" in res.essais[0]["resultat"]
    sem.release()
    assert (await r.completer("defaut", MSG, {})).fournisseur == "local"


async def test_dernier_de_la_chaine_attend_meme_sature(amont):
    amont.sur(LOCAL, ok_openai())
    r = Routeur(config_test(), amont.client())
    assert r._ecarter("local", dernier=True) is None


async def test_cache_temperature_zero(amont):
    amont.sur(LOCAL, ok_openai("déterministe"))
    r = Routeur(config_test(), amont.client())
    a = await r.completer("defaut", MSG, {"temperature": 0})
    b = await r.completer("defaut", MSG, {"temperature": 0})
    assert not a.depuis_cache and b.depuis_cache
    assert b.corps["choices"][0]["message"]["content"] == "déterministe"
    assert amont.nb("ollama") == 1
    await r.completer("defaut", MSG, {"temperature": 0.7})
    await r.completer("defaut", MSG, {"temperature": 0.7})
    assert amont.nb("ollama") == 3
    assert r.stats()["cache"]["hits"] == 1


async def test_cache_expire_et_taille(amont):
    from relais.routeur import Cache

    t = [0.0]
    c = Cache(taille=2, ttl=10, horloge=lambda: t[0])
    c.ecrire("a", {"x": 1}, "local")
    c.ecrire("b", {"x": 2}, "local")
    c.ecrire("c", {"x": 3}, "local")
    assert c.lire("a") is None  # évincé (LRU)
    t[0] = 11
    assert c.lire("b") is None  # expiré


async def test_erreur_non_reessayable_arrete(amont):
    amont.sur(LOCAL, httpx.Response(401, json={"error": "clé"}))
    amont.sur(CLAUDE, ok_anthropic())
    with pytest.raises(AucunFournisseur):
        await Routeur(config_test(), amont.client()).completer("defaut", MSG, {})
    assert amont.nb("anthropic") == 0


async def test_route_inconnue_et_tout_en_panne(amont):
    r = Routeur(config_test(), amont.client())
    with pytest.raises(RouteInconnue):
        await r.completer("nope", MSG, {})
    amont.sur(LOCAL, httpx.Response(500))
    with pytest.raises(AucunFournisseur) as exc:
        await r.completer("local-seul", MSG, {})
    assert exc.value.route == "local-seul"
    await r.fermer()
