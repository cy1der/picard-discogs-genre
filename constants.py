from typing import List
from picard.config import TextOption, IntOption, Option

PLUGIN_NAME = "Discogs Genre & Style"
PLUGIN_AUTHOR = "cy1der"
PLUGIN_DESCRIPTION = "Fetches genres and styles from Discogs"
PLUGIN_VERSION = "1.0.1"
PLUGIN_API_VERSIONS = ["2.0", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9", "2.10", "2.11", "2.12", "2.13"]
PLUGIN_LICENSE = "GPL-2.0-or-later"
PLUGIN_LICENSE_URL = "https://www.gnu.org/licenses/gpl-2.0.html"
PLUGIN_USER_GUIDE_URL = "https://github.com/cy1der/picard-discogs-genre"

CONFIG_OPTIONS: List[Option] = [
    TextOption("setting", "discogs_personal_access_token", ""),
    IntOption("setting", "discogs_minimum_token_overlap", 80)
]