"""Konstanten des Shelly Managers."""

DOMAIN = "shelly_manager"
SHELLY = "shelly"
STORAGE_KEY = "shelly_manager"
STORAGE_VERSION = 1
PANEL_URL = "shellys"
PANEL_ELEMENT = "shelly-manager-panel"
STATIC_URL = "/shelly_manager_static"
SIGNAL_EVENT = "shelly_manager_event"

CONF_SCAN_RANGES = "scan_ranges"
CONF_MDNS = "mdns"
CONF_CONCURRENCY = "concurrency"
CONF_FULL_INTERVAL = "full_interval"

DEFAULT_SCAN_RANGES = ""
DEFAULT_MDNS = True
DEFAULT_CONCURRENCY = 4
DEFAULT_FULL_INTERVAL = 6  # Stunden, 0 = aus

SCAN_CONCURRENCY = 32
SCAN_MAX_HOSTS = 4096
HISTORY_KEY = "shelly_manager.history"
SAMPLE_INTERVAL = 300  # s, liest nur den Live-Status aus HA (kein Netzwerkverkehr)
CONF_WIFI_SSID = "wifi_ssid"
CONF_WIFI_PASSWORD = "wifi_password"
CONF_AUTO_PROVISION = "auto_provision"
CONF_FIXED_RANGE = "fixed_ip_range"
