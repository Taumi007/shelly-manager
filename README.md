# Shelly Manager für Home Assistant

Custom Integration mit eigenem Panel (»Shellys«) zum lokalen Verwalten vieler Shelly-Geräte (Gen2+) in Home Assistant.

## Funktionen

- **Katalog** aller Shellys aus der Shelly-Integration, mDNS, IP-Scan, UniFi-Clientliste und Bluetooth-Funden – große Tabelle mit Spalten-Presets, Filtern, Suche und Symbolspalten (Sonstige / Firmware / WLAN)
- **Namen** zwischen Shelly und HA abgleichen (Shelly: Bereich + Name, HA: Name ohne Bereich + Bereich), Namensregeln, Problemstufen
- **Einstellungen im Batch** mit Vorschau, Rückgängig und gestaffeltem Neustart; Vorgaben für WLAN/MQTT/WebSocket (Werte optional aus `secrets.yaml`)
- **Firmware-Updates** gestaffelt, mit Schutz der Schaltzustände
- **Monitoring**: Signalstärke, Neustarts, Abstürze, Ausfälle
- **Bluetooth-Einrichtung** neuer Geräte (optional automatisch), Einrichtungs-Assistent, Sicherung und Wiederherstellung
- **Zurücksetzen und entfernen**: Werksreset, HA-Eintrag, UniFi-Client und IP-Reservierung, optional Verlauf/Statistiken
- **UniFi**: Client-Aliasse, feste IPs aus einem frei wählbaren Bereich, Aufräumen alter Reservierungen, Funkdiagnose
- **Fallback-Modus** für Taster (i4 & Co.): schaltet Hue, andere Shellys oder den eigenen Ausgang direkt, wenn Home Assistant nicht erreichbar ist (WebSocket- und Herzschlag-Überwachung, Szenen per Klick-Geste)
- **Verweise umschreiben** nach geänderten Entity-IDs (YAML, Dashboards, Energie, Helfer) – ohne KI, mit Vorschau, Sicherung und Rückgängig; Suche nach verwaisten Verweisen

## Installation

1. Dieses Repository nach `config/custom_components/shelly_manager/` kopieren (oder per HACS als benutzerdefiniertes Repository hinzufügen).
2. Home Assistant neu starten.
3. Einstellungen → Geräte & Dienste → Integration hinzufügen → »Shelly Manager«.

Voraussetzung: die offizielle Shelly-Integration. Optional: UniFi-Network-Integration, Philips Hue.

## Tests

```bash
python3 tests/test_model.py
```

## Lizenz

MIT – siehe [LICENSE](LICENSE).
