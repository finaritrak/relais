"""N'importe quel code qui utilise le SDK OpenAI fonctionne avec relais : changez juste base_url.

pip install openai
python examples/client_python.py
"""

from openai import OpenAI

client = OpenAI(base_url="http://localhost:8080/v1", api_key="ma-cle")

reponse = client.chat.completions.with_raw_response.create(
    model="defaut",  # = nom de la route dans relais.toml
    messages=[
        {"role": "system", "content": "Tu es l'assistant du service client."},
        {"role": "user", "content": "Le client rakoto@example.mg (034 12 345 67) n'a pas reçu sa facture."},
    ],
)

print("Fournisseur :", reponse.headers["x-relais-fournisseur"])  # local ou claude
print("Données masquées :", reponse.headers["x-relais-masques"])
print(reponse.parse().choices[0].message.content)
