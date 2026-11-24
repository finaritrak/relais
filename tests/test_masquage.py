import pytest

from relais.redaction import Correspondances, Masqueur, luhn_valide


@pytest.mark.parametrize(
    "texte, categorie",
    [
        ("Écrivez à rakoto.jean@example.mg demain", "EMAIL"),
        ("Appelez le 034 12 345 67", "TEL"),
        ("Appelez le +261 32 98 765 43", "TEL"),
        ("Appelez le 0331234567", "TEL"),
        ("CIN 101 211 123 456", "CIN"),
        ("CIN 101211123456", "CIN"),
        ("Carte 4111 1111 1111 1111", "CARTE"),
        ("IBAN MG46 0000 5030 0712 8942 1016 045", "IBAN"),
        ("Serveur 192.168.1.20", "IP"),
    ],
)
def test_detecte(texte, categorie):
    masque, corr = Masqueur().masquer(texte)
    assert f"⟦{categorie}_1⟧" in masque
    assert Masqueur.demasquer(masque, corr) == texte


@pytest.mark.parametrize(
    "texte",
    [
        "Commande de 1500 Ar le 12/03/2026",
        "Carte 4111 1111 1111 1112",  # Luhn invalide
        "Version 1.2.3.4567",
        "Adresse 999.10.10.10",
        "Numéro 0201234567",  # fixe, pas mobile
    ],
)
def test_ne_masque_pas_le_reste(texte):
    masque, corr = Masqueur().masquer(texte)
    assert len(corr) == 0
    assert masque == texte


def test_meme_valeur_meme_jeton_et_plusieurs_messages():
    m = Masqueur()
    corr = Correspondances()
    a, _ = m.masquer("a@b.mg et c@d.mg", corr)
    b, _ = m.masquer("encore a@b.mg", corr)
    assert a == "⟦EMAIL_1⟧ et ⟦EMAIL_2⟧"
    assert b == "encore ⟦EMAIL_1⟧"


def test_categories_et_motifs_perso():
    m = Masqueur(categories=["email"], motifs_perso={"client": r"CLT-\d{6}"})
    masque, corr = m.masquer("CLT-123456 / a@b.mg / 034 12 345 67")
    assert masque == "⟦PERSO_CLIENT_1⟧ / ⟦EMAIL_1⟧ / 034 12 345 67"
    assert Masqueur.demasquer(masque, corr).startswith("CLT-123456")


def test_demasquer_jeton_inconnu_reste_tel_quel():
    _, corr = Masqueur().masquer("a@b.mg")
    assert Masqueur.demasquer("⟦EMAIL_9⟧ ⟦EMAIL_1⟧", corr) == "⟦EMAIL_9⟧ a@b.mg"
    assert Masqueur.demasquer("rien", Correspondances()) == "rien"


def test_luhn():
    assert luhn_valide("4111111111111111")
    assert not luhn_valide("4111111111111112")
    assert not luhn_valide("1234")
