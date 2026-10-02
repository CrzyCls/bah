"""
Test manuel : force une "fausse" détection de changement dans le top 3
pour vérifier que la capture et l'envoi Discord fonctionnent, sans attendre
qu'un vrai vote ait lieu.

Variables d'environnement attendues : PSEUDO, TOPIC, DISCORD_WEBHOOK
"""
from playwright.sync_api import sync_playwright

from continuous_check import (
    lire_timers,
    verifier_changements_top3,
)


def main():
    with sync_playwright() as p:
        navigateur = p.chromium.launch()
        page = navigateur.new_page()

        # Charge la page normalement (comme pour lire les timers).
        lire_timers(page)

        # On fabrique un "ancien" classement volontairement différent,
        # pour forcer verifier_changements_top3 à croire que ça a changé.
        faux_etat_precedent = {
            1: ("Quelqu'un d'autre", 0),
            2: ("Quelqu'un d'autre", 0),
            3: ("Quelqu'un d'autre", 0),
        }

        print("Déclenchement du test...")
        verifier_changements_top3(page, faux_etat_precedent)
        print("Test terminé, vérifie ton salon Discord.")

        navigateur.close()


if __name__ == "__main__":
    main()
