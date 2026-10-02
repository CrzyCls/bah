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
from PIL import Image
from playwright.sync_api import sync_playwright

PSEUDO = os.environ["PSEUDO"]
TOPIC = os.environ["TOPIC"]
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")  # optionnel
URL = "https://pixelsmp.fr/vote"

LABELS_TOP3 = ["Meilleur votant du mois", "Top 2", "Top 3"]

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


def rendre_plus_carre(chemin_image):
    """Repère les blocs de contenu réel (avatar+nom, votes...) dans une ligne
    longue et plate, et les empile verticalement en ignorant les grandes
    zones vides entre eux, pour un format plus compact et plus carré."""
    img = Image.open(chemin_image).convert("RGB")
    largeur, hauteur = img.size
    if largeur == 0 or hauteur == 0:
        return chemin_image

    gris = img.convert("L")
    seuil = 235  # en dessous de ce niveau de gris : considéré comme "contenu"
    pixels = gris.load()
    pas = max(1, hauteur // 40)  # échantillonnage pour rester rapide

    colonnes_actives = []
    for x in range(largeur):
        actif = any(pixels[x, y] < seuil for y in range(0, hauteur, pas))
        colonnes_actives.append(actif)

    # Regroupe les colonnes actives en segments (blocs de contenu).
    segments = []
    debut = None
    for x, actif in enumerate(colonnes_actives):
        if actif and debut is None:
            debut = x
        elif not actif and debut is not None:
            segments.append((debut, x))
            debut = None
    if debut is not None:
        segments.append((debut, largeur))

    # Fusionne les segments séparés par un petit espace (bruit, pas un vrai vide).
    ecart_min = max(15, largeur // 25)
    fusionnes = []
    for seg in segments:
        if fusionnes and seg[0] - fusionnes[-1][1] < ecart_min:
            fusionnes[-1] = (fusionnes[-1][0], seg[1])
        else:
            fusionnes.append(list(seg))

    if len(fusionnes) < 2:
        return chemin_image  # rien de notable à réorganiser

    marge = 8
    morceaux = []
    largeur_max = 0
    for x0, x1 in fusionnes:
        x0 = max(0, x0 - marge)
        x1 = min(largeur, x1 + marge)
        morceau = img.crop((x0, 0, x1, hauteur))
        morceaux.append(morceau)
        largeur_max = max(largeur_max, morceau.width)

    nouvelle_img = Image.new("RGB", (largeur_max, hauteur * len(morceaux)), "white")
    for i, morceau in enumerate(morceaux):
        decalage_x = (largeur_max - morceau.width) // 2
        nouvelle_img.paste(morceau, (decalage_x, i * hauteur))

    nouvelle_img.save(chemin_image)
    return chemin_image


def envoyer_discord(message, chemin_image):
    if not DISCORD_WEBHOOK:
        return
    try:
        with open(chemin_image, "rb") as f:
            requests.post(
                DISCORD_WEBHOOK,
                data={"content": message},
                files={"file": ("top3.png", f, "image/png")},
                timeout=15,
            )
    except Exception as e:
        print("Erreur envoi Discord :", e, flush=True)


def ligne_depuis_label(page, label):
    """Remonte depuis le sous-titre (ex: 'Top 2') jusqu'au bloc qui contient
    aussi le nombre de votes, pour avoir toute la ligne (nom + votes)."""
    sous_titre = page.get_by_text(label, exact=True).first
    if sous_titre.count() == 0:
        return None
    for niveau in range(1, 7):
        candidat = sous_titre.locator(f"xpath=ancestor::*[{niveau}]")
        try:
            texte = candidat.inner_text()
        except Exception:
            continue
        if "vote" in texte.lower():
            return candidat
    return None


def lire_top3(page):
    """Renvoie {1: (nom, votes, locator_ligne), 2: ..., 3: ...}."""
    resultat = {}
    for position, label in enumerate(LABELS_TOP3, start=1):
        try:
            ligne = ligne_depuis_label(page, label)
            if ligne is None:
                continue
            texte = ligne.inner_text()
            nom = texte.strip().split("\n")[0].strip()
            votes_match = re.search(r"(\d+)\s*votes?", texte, re.IGNORECASE)
            votes = int(votes_match.group(1)) if votes_match else None
            resultat[position] = (nom, votes, ligne)
        except Exception as e:
            print(f"Erreur lecture top3 position {position} :", e, flush=True)
    return resultat


def verifier_changements_top3(page, top3_precedent):
    """Compare le top3 actuel au précédent, regroupe les changements détectés
    (hors PSEUDO) en un seul message Discord avec l'évolution des votes,
    et renvoie le nouvel état à retenir."""
    top3_actuel = lire_top3(page)
    nouvel_etat = {}
    changements = []  # (position, nom, votes, evolution_texte, locator_ligne)

    for position, (nom, votes, ligne) in top3_actuel.items():
        nouvel_etat[position] = (nom, votes)

        if nom.lower() == PSEUDO.lower():
            continue  # on ignore ses propres votes

        avant = top3_precedent.get(position)

        if not top3_precedent:
            continue  # pas d'alerte au tout premier passage

        if avant is None:
            continue  # position pas encore vue, rien à comparer

        nom_avant, votes_avant = avant

        if nom != nom_avant:
            # Nouvelle personne à cette position (elle entre dans le top 3).
            changements.append((position, nom, votes, "🆕 nouvelle entrée", ligne))
        elif votes != votes_avant and votes is not None and votes_avant is not None:
            diff = votes - votes_avant
            evolution = f"+{diff}" if diff > 0 else str(diff)
            changements.append(
                (position, nom, votes, f"{votes_avant} → {votes} ({evolution})", ligne)
            )

    if changements:
        lignes_message = [
            f"#{position} **{nom}** — {evolution}"
            for position, nom, votes, evolution, _ in changements
        ]
        message = "📊 Changement(s) dans le top 3 :\n" + "\n".join(lignes_message)

        # On illustre avec la capture de la première ligne concernée.
        try:
            _, _, _, _, premiere_ligne = changements[0]
            premiere_ligne.screenshot(path="top3_change.png")
            rendre_plus_carre("top3_change.png")
            envoyer_discord(message, "top3_change.png")
        except Exception as e:
            print("Erreur capture top3 :", e, flush=True)

    return nouvel_etat


def envoyer_resume_quotidien(page):
    top3_actuel = lire_top3(page)
    if not top3_actuel:
        return

    lignes = [
        f"#{position} **{nom}** — {votes} votes"
        for position, (nom, votes, _ligne) in sorted(top3_actuel.items())
    ]
    message = "📅 Résumé du jour — Top 3 du classement :\n" + "\n".join(lignes)

    try:
        conteneur = page.get_by_text("Top votes").first.locator(
            "xpath=ancestor::*[3]"
        )
        conteneur.screenshot(path="resume_quotidien.png")
        rendre_plus_carre("resume_quotidien.png")
        envoyer_discord(message, "resume_quotidien.png")
    except Exception as e:
        print("Erreur capture résumé quotidien, envoi en texte seul :", e, flush=True)
        if DISCORD_WEBHOOK:
            requests.post(DISCORD_WEBHOOK, data={"content": message}, timeout=15)


def main():
    debut = time.monotonic()
    deja_notifie = set()
    top3_precedent = {}

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

            try:
                top3_precedent = verifier_changements_top3(page, top3_precedent)
            except Exception as e:
                print("Erreur vérification top3 :", e, flush=True)

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

            # On ne dépasse jamais la marge de sécurité du job, et on plafonne
            # à 5 min pour garder une vérification régulière du classement.
            temps_restant_job = DUREE_MAX - (time.monotonic() - debut)
            attente = max(30, min(attente, temps_restant_job, 300))
            time.sleep(attente)

        navigateur.close()

    print("Fin de ce run (limite de temps atteinte), le workflow va en relancer un autre.", flush=True)


if __name__ == "__main__":
    main()
