"""
Surveille en continu les timers de vote PixelSMP et envoie une notif ntfy
dès qu'un site est disponible. Conçu pour tourner sur GitHub Actions
(repo public = minutes illimitées), avec un arrêt propre avant la limite
de 6h imposée par GitHub (le workflow le relance automatiquement ensuite).

Variables d'environnement attendues : PSEUDO, TOPIC
"""
import os
import re
import time

import requests
from playwright.sync_api import sync_playwright

PSEUDO = os.environ["PSEUDO"]
TOPIC = os.environ["TOPIC"]
URL = "https://pixelsmp.fr/vote"

# Marge de sécurité : on s'arrête bien avant les 6h max de GitHub (21600 s),
# pour laisser le temps au prochain run de démarrer proprement.
DUREE_MAX = 5 * 3600 + 45 * 60  # 5h45

TIMER = re.compile(r"(\d{2}):(\d{2}):(\d{2})")
SITE = re.compile(r"Site #(\d+)")


def notifier(message):
    requests.post(f"https://ntfy.sh/{TOPIC}", data=message.encode("utf-8"))
    print(message, flush=True)


def fermer_bandeau_cookies(page):
    textes_possibles = [
        "Tout accepter", "J'accepte", "Accepter", "Accepter tout",
        "Accept all", "Accept",
    ]
    for texte in textes_possibles:
        bouton = page.get_by_role("button", name=texte)
        try:
            if bouton.first.is_visible(timeout=1500):
                bouton.first.click()
                page.wait_for_timeout(500)
                return
        except Exception:
            continue


def lire_timers(page):
    page.goto(URL)
    page.wait_for_load_state("networkidle")
    fermer_bandeau_cookies(page)

    champ_pseudo = page.get_by_placeholder("Nom")
    champ_pseudo.fill(PSEUDO)
    champ_pseudo.press("Enter")

    try:
        page.wait_for_selector("text=/\\d{2}:\\d{2}:\\d{2}/", timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(2000)

    resultats = {}
    for carte in page.locator("a", has_text="Site #").all():
        texte = carte.inner_text()
        site = SITE.search(texte)
        if not site:
            continue
        t = TIMER.search(texte)
        secondes = int(t[1]) * 3600 + int(t[2]) * 60 + int(t[3]) if t else 0
        resultats[f"Site #{site[1]}"] = secondes

    if not resultats or all(v == 0 for v in resultats.values()):
        page.screenshot(path="debug.png", full_page=True)

    return resultats


def main():
    debut = time.monotonic()
    deja_notifie = set()

    with sync_playwright() as p:
        navigateur = p.chromium.launch()
        page = navigateur.new_page()

        while time.monotonic() - debut < DUREE_MAX:
            try:
                timers = lire_timers(page)
            except Exception as e:
                print("Erreur de lecture :", e, flush=True)
                time.sleep(120)
                continue

            print("Timers :", timers, flush=True)

            if timers and all(v == 0 for v in timers.values()):
                print("⚠️ Lecture suspecte (tout à 0 en même temps), on réessaie.", flush=True)
                time.sleep(120)
                continue

            SEUIL_ANTICIPATION = 60  # secondes avant la vraie disponibilité

            for site, secondes in timers.items():
                if secondes <= SEUIL_ANTICIPATION and site not in deja_notifie:
                    notifier(f"🗝️ Tu peux bientôt voter sur {site} (dans ~1 min) !")
                    deja_notifie.add(site)
                elif secondes > SEUIL_ANTICIPATION:
                    deja_notifie.discard(site)

            # On réveille le script juste avant le seuil d'anticipation, pour ne pas
            # attendre inutilement jusqu'à la disponibilité réelle (0).
            restants = [
                max(1, s - SEUIL_ANTICIPATION) for s in timers.values()
                if s > SEUIL_ANTICIPATION
            ]
            attente = min(restants) if restants else 600

            # On ne dépasse jamais la marge de sécurité du job.
            temps_restant_job = DUREE_MAX - (time.monotonic() - debut)
            attente = max(30, min(attente, temps_restant_job))
            time.sleep(attente)

        navigateur.close()

    print("Fin de ce run (limite de temps atteinte), le workflow va en relancer un autre.", flush=True)


if __name__ == "__main__":
    main()
