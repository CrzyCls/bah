"""
Envoie un résumé quotidien du top 3 du classement PixelSMP sur Discord.
Conçu pour être lancé une fois par jour via un workflow planifié séparé.

Variables d'environnement attendues : PSEUDO, TOPIC, DISCORD_WEBHOOK
"""
from playwright.sync_api import sync_playwright

from continuous_check import envoyer_resume_quotidien, lire_timers


def main():
    with sync_playwright() as p:
        navigateur = p.chromium.launch()
        page = navigateur.new_page()

        lire_timers(page)  # charge la page et remplit le pseudo
        envoyer_resume_quotidien(page)

        navigateur.close()


if __name__ == "__main__":
    main()
