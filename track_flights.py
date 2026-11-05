import os
import re
import time
import random
import itertools
from datetime import datetime, timedelta, timezone
import urllib.request
import json

from fast_flights import FlightQuery, Passengers, create_query, get_flights
import fast_flights.parser
import fast_flights.fetcher
from fast_flights.exceptions import FlightsNotFound
from fast_flights.model import (
    Airline,
    Airport,
    Alliance,
    CarbonEmission,
    Flights,
    JsMetadata,
    SimpleDatetime,
    SingleFlight,
)
from fast_flights.parser import ResultList, _parse_time
import primp
from selectolax.lexbor import LexborHTMLParser


# ---------------------------------------------------------------------------
# Patch de robustesse pour la bibliothèque fast-flights :
# 1. Évite 'IndexError: list index out of range' quand un vol n'a pas de prix direct.
# 2. Évite 'TypeError: object is not subscriptable' quand aucun vol n'existe (payload[3] is None).
# 3. Contourne le mur de cookies Google (SOCS) qui bloque les requêtes européennes.
# ---------------------------------------------------------------------------

def _safe_parse_js(js: str) -> ResultList:
    try:
        data = js.split("data:", 1)[1].rsplit(",", 1)[0]
    except Exception:
        return ResultList()

    if data.endswith("errorHasStatus: true"):
        raise FlightsNotFound("no flights found; received error")

    try:
        payload = json.loads(data)
    except Exception:
        return ResultList()

    alliances = []
    airlines = []
    try:
        if len(payload) > 7 and payload[7] and len(payload[7]) > 1 and payload[7][1]:
            for code, name in (payload[7][1][0] or []):
                alliances.append(Alliance(code=code, name=name))
            for code, name in (payload[7][1][1] or []):
                airlines.append(Airline(code=code, name=name))
    except Exception:
        pass

    meta = JsMetadata(alliances=alliances, airlines=airlines)
    flights = ResultList()

    if len(payload) <= 3 or payload[3] is None or not payload[3] or payload[3][0] is None:
        flights.metadata = meta
        return flights

    for k in payload[3][0]:
        try:
            flight = k[0]
            # Gestion robuste du tarif
            try:
                price = k[1][0][1]
            except (IndexError, TypeError):
                price = None

            typ = flight[0]
            flight_airlines = flight[1]
            sg_flights = []

            for single_flight in flight[2]:
                from_airport = Airport(code=single_flight[3], name=single_flight[4])
                to_airport = Airport(code=single_flight[6], name=single_flight[5])
                departure_time = _parse_time(single_flight[8])
                departure_date = tuple(single_flight[20])
                departure = SimpleDatetime(date=departure_date, time=departure_time)

                arrival_time = _parse_time(single_flight[10])
                arrival_date = tuple(single_flight[21])
                arrival = SimpleDatetime(date=arrival_date, time=arrival_time)

                plane_type = single_flight[17] if len(single_flight) > 17 else ""
                duration = single_flight[11] if len(single_flight) > 11 else ""

                sg_flights.append(
                    SingleFlight(
                        from_airport=from_airport,
                        to_airport=to_airport,
                        departure=departure,
                        arrival=arrival,
                        duration=duration,
                        plane_type=plane_type,
                    )
                )

            carbon_emission = None
            typical_carbon_emission = None
            if len(flight) > 22 and flight[22]:
                carbon_emission = flight[22][7] if len(flight[22]) > 7 else None
                typical_carbon_emission = flight[22][8] if len(flight[22]) > 8 else None

            flights.append(
                Flights(
                    type=typ,
                    price=price,
                    airlines=flight_airlines,
                    flights=sg_flights,
                    carbon=CarbonEmission(
                        typical_on_route=typical_carbon_emission,
                        emission=carbon_emission,
                    ),
                )
            )
        except Exception:
            continue

    flights.metadata = meta
    return flights


def _safe_parse(html: str) -> ResultList:
    parser = LexborHTMLParser(html)
    script = parser.css_first(r"script.ds\:1")
    if not script:
        return ResultList()
    return _safe_parse_js(script.text())


def _safe_fetch_flights_html(q, /, *, proxy=None, fetch_integration=None) -> str:
    if fetch_integration is not None:
        return fetch_integration.fetch_html(q)

    client = primp.Client(
        impersonate="chrome_145",
        impersonate_os="windows",
        referer=True,
        proxy=proxy,
        cookie_store=True,
    )
    from fast_flights.querying import Query
    params = q.params() if isinstance(q, Query) else {"q": q}
    res = client.get(
        "https://www.google.com/travel/flights",
        params=params,
        cookies={"SOCS": "CAESEwgDEgk2OTg5MjAyNTEaAmZyIAEaBgiA_LyaBg"},
    )
    return res.text


# Application des correctifs
fast_flights.parser.parse_js = _safe_parse_js
fast_flights.parser.parse = _safe_parse
fast_flights.fetcher.fetch_flights_html = _safe_fetch_flights_html


def parse_dates(base_date_str: str, delta: int) -> list[str]:
    """Génère la liste des dates [date - delta ... date + delta] au format YYYY-MM-DD."""
    base_date = datetime.strptime(base_date_str.strip(), "%Y-%m-%d")
    return [
        (base_date + timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(-delta, delta + 1)
    ]


def extract_numeric_price(flight_obj) -> float:
    """Extrait le prix numérique depuis l'objet Flight (compatible v2 et v3)."""
    raw_price = getattr(flight_obj, "price", None)
    if raw_price is None:
        return float("inf")
    if isinstance(raw_price, (int, float)):
        return float(raw_price)
    # Nettoyage si c'est une chaîne (ex: "542 €" ou "$620")
    cleaned = re.sub(r"[^\d.]", "", str(raw_price))
    return float(cleaned) if cleaned else float("inf")


def format_flight_details(flight_obj) -> str:
    """Formate les détails d'un vol de manière lisible."""
    name = getattr(flight_obj, "name", getattr(flight_obj, "airlines", "Compagnie inconnue"))
    departure = getattr(flight_obj, "departure", "")
    arrival = getattr(flight_obj, "arrival", "")
    duration = getattr(flight_obj, "duration", "")
    stops = getattr(flight_obj, "stops", "")

    details = f"{name}"
    if departure or arrival:
        details += f" ({departure} -> {arrival})"
    if duration:
        details += f" | Durée: {duration}"
    if stops != "":
        details += f" | Escales: {stops}"
    if details == "Compagnie inconnue":
        details = str(flight_obj)
    return details


def send_discord_embed(
    webhook_url: str,
    all_results: list[dict],
    alert_max_price: float | None,
    only_notify_under_threshold: bool = False,
):
    """Envoie un Embed Discord formaté avec le Top 5 et un lien direct vers Google Flights."""
    if not webhook_url or not all_results:
        return

    best = all_results[0]
    best_price = best["price"]
    currency = best["currency"]

    # Option : ne rien envoyer si le prix dépasse le seuil
    if only_notify_under_threshold and alert_max_price is not None and best_price > alert_max_price:
        print(
            f"🔕 Meilleur prix ({best_price:.0f} {currency}) supérieur au seuil "
            f"({alert_max_price:.0f} {currency}). Aucune notification Discord envoyée."
        )
        return

    is_good_deal = alert_max_price is not None and best_price <= alert_max_price
    color = 0x2ECC71 if is_good_deal else 0x3498DB  # Vert si sous le seuil, Bleu sinon
    content_ping = (
        f"🚨 **ALERTE PRIX BAS (< {alert_max_price:.0f} {currency}) !** @here"
        if is_good_deal
        else ""
    )

    fields = []
    for rank, item in enumerate(all_results[:5], 1):
        orig, dest = [x.strip() for x in item["route"].split("↔")]
        gf_url = (
            f"https://www.google.com/travel/flights?q=Flights%20to%20{dest}%20from%20{orig}"
            f"%20on%20{item['dep_date']}%20through%20{item['ret_date']}&curr={currency}&hl=fr"
        )
        medal = (
            "🥇" if rank == 1 else ("🥈" if rank == 2 else ("🥉" if rank == 3 else f"#{rank}"))
        )
        fields.append(
            {
                "name": f"{medal} {item['price']:.0f} {currency} — {item['route']} ({item['stay_days']} jours)",
                "value": (
                    f"📅 **Aller :** `{item['dep_date']}` | **Retour :** `{item['ret_date']}`\n"
                    f"✈️ {item['details']}\n"
                    f"🔗 [Voir sur Google Flights]({gf_url})"
                ),
                "inline": False,
            }
        )

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d à %H:%M UTC")
    payload = {
        "content": content_ping,
        "username": "✈️ Flight Price Tracker",
        "embeds": [
            {
                "title": f"🏆 Meilleur billet : {best_price:.0f} {currency} ({best['route']})",
                "description": (
                    f"Comparaison automatique des vols.\n"
                    f"Meilleure option : départ le **{best['dep_date']}** et retour le **{best['ret_date']}** "
                    f"({best['stay_days']} jours)."
                ),
                "color": color,
                "fields": fields,
                "footer": {"text": f"Exécuté le {now_utc}"},
            }
        ],
    }

    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "FlightTracker/1.0"},
    )
    try:
        urllib.request.urlopen(req)
        print("✅ Notification Discord envoyée avec succès !")
    except Exception as e:
        print(f"⚠️ Erreur lors de l'envoi du webhook Discord : {e}")


def main():
    # 1. Lecture des variables d'environnement
    origins = [
        x.strip().upper()
        for x in os.getenv("ORIGIN_AIRPORTS", "CDG,ORY,BRU").split(",")
        if x.strip()
    ]
    destinations = [
        x.strip().upper()
        for x in os.getenv("DEST_AIRPORTS", "PVG,SHA").split(",")
        if x.strip()
    ]
    dep_date_base = os.getenv("DEPARTURE_DATE", "2026-10-15")
    ret_date_base = os.getenv("RETURN_DATE", "2026-10-29")
    delta_days = int(os.getenv("DELTA_DAYS", "1"))
    currency = os.getenv("CURRENCY", "EUR")
    adults = int(os.getenv("ADULTS", "1"))

    max_stops_env = os.getenv("MAX_STOPS", "")
    max_stops = int(max_stops_env) if max_stops_env.strip().isdigit() else None

    webhook_url = os.getenv("WEBHOOK_URL", "").strip()
    alert_price_env = os.getenv("ALERT_MAX_PRICE", "").strip()
    alert_max_price = (
        float(alert_price_env)
        if alert_price_env.replace(".", "", 1).isdigit()
        else None
    )
    only_under_threshold = (
        os.getenv("ONLY_NOTIFY_UNDER_THRESHOLD", "false").strip().lower() == "true"
    )

    dep_dates = parse_dates(dep_date_base, delta_days)
    ret_dates = parse_dates(ret_date_base, delta_days)

    print(f"🔎 Départs : {origins} | Arrivées : {destinations}")
    print(f"📅 Fenêtre Aller : {dep_dates[0]} au {dep_dates[-1]}")
    print(f"📅 Fenêtre Retour : {ret_dates[0]} au {ret_dates[-1]}")
    if alert_max_price:
        print(f"🎯 Seuil d'alerte Discord : {alert_max_price:.0f} {currency}")
    print("-" * 60)

    all_results = []

    # 2. Boucle sur tous les couples d'aéroports et de dates
    airport_pairs = list(itertools.product(origins, destinations))
    date_pairs = [
        (d_dep, d_ret)
        for d_dep, d_ret in itertools.product(dep_dates, ret_dates)
        if d_dep < d_ret  # Garantit que la date de retour est postérieure à l'aller
    ]

    total_queries = len(airport_pairs) * len(date_pairs)
    print(f"🚀 Lancement de {total_queries} recherches...\n")

    for idx, ((orig, dest), (d_dep, d_ret)) in enumerate(
        itertools.product(airport_pairs, date_pairs), 1
    ):
        print(
            f"[{idx}/{total_queries}] Recherche {orig} ➔ {dest} | Aller: {d_dep} | Retour: {d_ret}..."
        )
        try:
            flight_leg_out = FlightQuery(
                date=d_dep,
                from_airport=orig,
                to_airport=dest,
                max_stops=max_stops,
            )
            flight_leg_ret = FlightQuery(
                date=d_ret,
                from_airport=dest,
                to_airport=orig,
                max_stops=max_stops,
            )

            query = create_query(
                flights=[flight_leg_out, flight_leg_ret],
                seat="economy",
                trip="round-trip",
                passengers=Passengers(adults=adults),
                currency=currency,
                language="fr-FR",
            )

            res = get_flights(query)
            flights_list = (
                res.flights
                if hasattr(res, "flights")
                else (res if isinstance(res, list) else [])
            )

            if flights_list:
                valid_flights = [
                    f for f in flights_list if extract_numeric_price(f) < float("inf")
                ]
                if valid_flights:
                    best_flight = min(valid_flights, key=extract_numeric_price)
                    price_val = extract_numeric_price(best_flight)
                    stay_days = (
                        datetime.strptime(d_ret, "%Y-%m-%d")
                        - datetime.strptime(d_dep, "%Y-%m-%d")
                    ).days

                    all_results.append(
                        {
                            "route": f"{orig} ↔ {dest}",
                            "dep_date": d_dep,
                            "ret_date": d_ret,
                            "stay_days": stay_days,
                            "price": price_val,
                            "currency": currency,
                            "details": format_flight_details(best_flight),
                        }
                    )
                    print(
                        f"   ✅ Meilleur prix trouvé : {price_val:.0f} {currency} ({format_flight_details(best_flight)})"
                    )
                else:
                    print("   ⚠️ Vols trouvés mais prix non lisible.")
            else:
                print("   ❌ Aucun vol trouvé.")

        except FlightsNotFound:
            print("   ℹ️ Aucun vol trouvé pour cette combinaison.")
        except Exception as e:
            print(f"   ⚠️ Erreur sur cette recherche : {e}")

        # Pause aléatoire entre 1.5s et 3.5s pour éviter le blocage par Google Flights
        if idx < total_queries:
            time.sleep(random.uniform(1.5, 3.5))

    # 3. Tri global par prix croissant
    all_results.sort(key=lambda x: x["price"])

    print("\n" + "=" * 60)
    if not all_results:
        print("😢 Aucun billet trouvé sur l'ensemble des critères.")
        return

    best = all_results[0]
    print(f"🏆 MEILLEUR TARIF GLOBAL : {best['price']:.0f} {best['currency']}")
    print(
        f"   Trajet : {best['route']} | Du {best['dep_date']} au {best['ret_date']} ({best['stay_days']} jours)"
    )
    print(f"   Détails : {best['details']}")
    print("=" * 60)

    # 4. Génération d'un rapport Markdown dans GitHub Actions (GITHUB_STEP_SUMMARY)
    summary_md = [
        "## ✈️ Résultats du comparateur de vols",
        f"**Meilleur prix trouvé :** `{best['price']:.0f} {best['currency']}` sur **{best['route']}** ({best['dep_date']} au {best['ret_date']})",
        "",
        "| Rang | Prix | Trajet | Date Aller | Date Retour | Durée séjour | Détails du vol |",
        "|---|---|---|---|---|---|---|",
    ]
    for rank, item in enumerate(all_results[:15], 1):
        summary_md.append(
            f"| #{rank} | **{item['price']:.0f} {item['currency']}** | `{item['route']}` | {item['dep_date']} | {item['ret_date']} | {item['stay_days']}j | {item['details']} |"
        )

    summary_text = "\n".join(summary_md)
    step_summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if step_summary_path:
        with open(step_summary_path, "a", encoding="utf-8") as f:
            f.write(summary_text + "\n")

    # 5. Envoi de l'alerte sur Discord
    send_discord_embed(
        webhook_url=webhook_url,
        all_results=all_results,
        alert_max_price=alert_max_price,
        only_notify_under_threshold=only_under_threshold,
    )


if __name__ == "__main__":
    main()
