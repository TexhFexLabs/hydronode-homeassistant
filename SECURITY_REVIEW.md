# Prüfung der HydroNode-Integration — 10. September 2026

## Umfang und Grenzen

Geprüft wurden alle Python-Module der Integration, Konfiguration, Übersetzungen,
Tests und CI sowie die zugehörigen Authentifizierungs-, REST- und WebSocket-Pfade
im benachbarten Backend. Vorhandene Änderungen für Analyse-Entitäten wurden
beibehalten und mitgeprüft. Backend, Infrastruktur und bestehende Installationen
wurden nicht geändert. Dies ist eine Quellcodeprüfung mit Regressionstests, kein
Penetrationstest und keine Zusicherung vollständiger Sicherheit.

## Token und Domain

`ApiTokenService.create()` erzeugt `hat_` plus 40 Zeichen aus
`RandomStringGenerator.generate(40)`. Dieser nutzt `SecureRandom.nextInt(62)`
für Zeichen aus A–Z, a–z und 0–9: rund 238 Bit Zufallsentropie.
`sha256Hex(token)` hasht ausschließlich die UTF-8-Bytes des Tokens. Weder Domain
noch URL, Benutzername oder JWT-Issuer gehen in diesen Hash ein.
`resolveToken()` sucht über denselben Hash im Tokenbestand.

Bewertung: 9/10 für Zufallserzeugung und Hash-Speicherung; 7/10 für die derzeitige
Gesamtnutzung einschließlich der unten aufgeführten Backend-Befunde. Die Zahlen
sind eine qualitative Einschätzung, keine standardisierte Sicherheitsmetrik.

Ein vorhandener `hat_…`-Token kann auf beiden Domains verwendet werden, wenn sie
denselben Benutzer-/Tokenbestand bedienen. Das gilt nicht automatisch für andere
Tokenarten wie OAuth/JWT. `wht_…`-LoRaWAN-Webhooks sind ein separater Mechanismus
und werden von dieser Integration nicht verwendet.

## Umgesetzt in der Integration

- `https://hydronode.tech` als Standard und in den öffentlichen Dokumentationslinks.
- Umkonfiguration der gespeicherten URL mit vorhandenem Token und Prüfung derselben
  Benutzer-ID; IDs und Verlauf bestehender Entitäten bleiben dabei erhalten.
- Auch Reauth, initialer Bootstrap und WS-Authentifizierung prüfen das Konto.
- Cloud-Domains erfordern HTTPS; URL-Zugangsdaten, Queries, Fragmente und ungültige
  Schemes werden abgewiesen. HTTP bleibt für explizit konfigurierte eigene
  Deployments möglich und sollte ausschließlich in vertrauenswürdigen lokalen
  Netzen verwendet werden.
- REST- und WebSocket-Weiterleitungen werden blockiert. Insbesondere wird ein
  WS-Token nicht nach einem Redirect an das neue Ziel gesendet.
- Begrenzte Request-/Verbindungszeiten, begrenzte REST-/WS-Nachrichtengrößen;
  Fehlertexte enthalten keine Remote-Antwortkörper.
- Strukturelle Prüfung des gesamten Bootstraps vor der Gerätebereinigung;
  ungültiges JSON oder falsche Antworttypen gelten als Fehler, nicht als leere Daten.
- Messwerte müssen endlich und Zeitstempel zeitzonenbezogen sein. Ungültige
  WS-Nachrichten und einzelne Callback-Fehler beenden nicht die Verbindung.
- Handshake-Zeitüberschreitungen werden erneut versucht statt als Tokenwiderruf
  behandelt; Backoff mit Zufallsstreuung und Rücksetzung nach stabiler Verbindung.
- REST-Polling bleibt bei kontinuierlichen Pushes und deaktivierten Messwert-Entitäten
  aktiv. Ein erkanntes Authentifizierungsproblem blockiert weitere Pushes und stoppt
  den Socket. Änderungen an Freigaben werden weiterhin beim Bootstrap sichtbar.
- Ältere Push-/REST-Messwerte überschreiben keine neueren Werte. Pro Push entfällt
  die Kopie des kompletten Zustandsverzeichnisses; unveränderte Sensoren werden nicht
  erneut geschrieben und Zeitstempel nicht ständig neu geparst.
- Analysewerte ignorieren ungültige Scores, kaputte und veraltete Analysen;
  entfernte Stationen verlieren ihre im RAM gespeicherten Analysen.
- Listener werden beim Entladen abgemeldet; fehlgeschlagenes Entladen lässt den
  Socket bestehen. Hintergrundverbindung startet erst nach den Plattformen.
- Diagnosen schwärzen Token, URL, Namen, IDs, Kanalnamen und Koordinaten.
- Regressionstests werden zusätzlich zu Hassfest/HACS in CI ausgeführt.

## Backend-Befunde: weiterhin offen

Diese Punkte erfordern Änderungen im Backend; ein HA-Client kann sie nicht für
andere oder manipulierte Clients erzwingen:

1. **Tokenwiderruf und offene WebSockets:** `HaWebSocket` authentifiziert einmalig.
   `HaSessionRegistry` speichert Sitzungen nur nach Benutzer-ID, ohne Tokenbezug oder
   erneute Prüfung. Ein bereits verbundener anderer Client kann nach Tokenwiderruf
   weiterhin Nachrichten erhalten. Sitzungen müssen serverseitig dem Token zugeordnet
   und bei Widerruf geschlossen beziehungsweise vor weiterer Nutzung geprüft werden.
2. **Cache bei mehreren Backend-Instanzen:** `ApiTokenService.revoke()` entfernt den
   Cacheeintrag nur im lokalen Prozess. Andere Instanzen können den Token bis zum
   Ablauf ihrer 15-minütigen Cachezeit weiter akzeptieren. Es fehlt eine übergreifende
   Invalidierung beziehungsweise verbindliche Prüfung des Widerrufs.
3. **Freigaben:** `HaAudienceResolver` cached Empfänger fünf Minuten ohne Invalidierung.
   Entzug einer Freigabe oder Veröffentlichung kann daher verzögert wirken. Der Client
   filtert anhand seines letzten Bootstraps; das ersetzt keine serverseitige Kontrolle.
4. **Berechtigungen/Laufzeit:** HA verwendet den weitreichenden `hat_`-Scope. `hrt_`
   ist im vorhandenen Backend für die HA-Endpunkte und den WS-Pfad nicht zugelassen.
   Ein eigener minimaler HA-Scope und eine Token-Laufzeit wären separate Backend-Änderungen.

## Domainbetrieb und Verifikation

Bestehende Einträge werden nicht automatisch umgeschrieben. Beim Wechsel über
**⋮ → Neu konfigurieren** wird der Token gegen die neue URL geprüft, bevor diese
gespeichert wird. Fehler lassen die bisherige URL unverändert.

Für den angekündigten Übergangszeitraum müssen beide Domains gültige Zertifikate
und direkte Proxy-Routen für `/api/ha/v1/*` sowie `/ws/ha/v1` besitzen. Eine reine
Website-Weiterleitung oder ausschließlich weiterbetriebene LoRaWAN-Webhooks reichen
nicht. DNS, TLS, Proxy und gemeinsamer Datenbestand sind Betreiberaufgaben.

Beide öffentlichen Bootstrap-URLs antworteten im unauthentifizierten Erreichbarkeitstest
mit HTTP 401 ohne Weiterleitung. Ein produktiver Token wurde nicht verwendet; ein
vollständiger authentifizierter Produktions- oder Lasttest steht daher nicht hinter
diesen Aussagen. Lokale Regressionstests verwenden Home Assistant 2026.2.3,
Python 3.13 und echte lokale HTTP-Server für den WS-Redirect-Test.
