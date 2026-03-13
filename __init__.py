import re
import urllib.parse
import functools
import unicodedata
from PyQt5 import QtWidgets

from picard import log, config
from picard.webservice import ratecontrol
from picard.metadata import register_album_metadata_processor
from picard.ui.options import register_options_page, OptionsPage
from picard.album import Album
from picard.metadata import Metadata

from .constants import *

class DiscogsGenreOptionsPage(OptionsPage):
    NAME = "discogs_genre_and_style"
    TITLE = "Discogs Genre & Style"
    PARENT = "plugins"

    options = CONFIG_OPTIONS

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        options_group = QtWidgets.QGroupBox("Options", self)
        options_group.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Minimum)
        options_layout = QtWidgets.QVBoxLayout(options_group)

        self.token_input = QtWidgets.QLineEdit(self)
        self.token_input.setPlaceholderText("Account > Settings > Developers > Generate token")
        
        self.style_input = QtWidgets.QLineEdit(self)
        self.style_input.setPlaceholderText("grouping")
        self.style_input.setToolTip("Metadata tag to store Discogs style (default: grouping), set it blank to disable")
    
        min_overlap_layout = QtWidgets.QHBoxLayout()
        
        min_overlap_label = QtWidgets.QLabel("Minimum Token Overlap", self)
        min_overlap_label.setToolTip("Minimum percentage of token overlap required to consider a Discogs search result as a match (0-100%)")
        min_overlap_label.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.min_overlap_input = QtWidgets.QSpinBox(self)
        self.min_overlap_input.setRange(0, 100)
        self.min_overlap_input.setSuffix(" %")
        self.min_overlap_input.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Preferred)
        
        min_overlap_layout.addWidget(min_overlap_label)
        min_overlap_layout.addStretch()
        min_overlap_layout.addWidget(self.min_overlap_input)

        options_layout.addWidget(QtWidgets.QLabel("Personal Access Token (recommended for higher rate limits)", self))
        options_layout.addWidget(self.token_input)
        options_layout.addWidget(QtWidgets.QLabel("Style Tag", self))
        options_layout.addWidget(self.style_input)
        options_layout.addLayout(min_overlap_layout)

        layout.addWidget(options_group)
        layout.addStretch()

    def load(self):
        self.token_input.setText(config.setting["discogs_personal_access_token"] or "")
        self.style_input.setText(config.setting["discogs_style_tag"] or "grouping")
        self.min_overlap_input.setValue(config.setting["discogs_minimum_token_overlap"] or 80)

    def save(self):
        config.setting["discogs_personal_access_token"] = self.token_input.text().strip()
        config.setting["discogs_style_tag"] = self.style_input.text().strip()
        config.setting["discogs_minimum_token_overlap"] = self.min_overlap_input.value()

class DiscogsGenreProcessor:
    def __init__(self):
        self.host = "api.discogs.com"

    def _clean_search_text(self, text: str) -> str:
        if not text:
            return ""

        text = unicodedata.normalize("NFKC", text).strip()
        text = re.sub(r"\s+", " ", text)
        return text

    def _normalize_text(self, text: str) -> str:
        if not text:
            return ""

        text = unicodedata.normalize("NFKC", text).casefold()

        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")

        text = "".join(ch if ch.isalnum() else " " for ch in text)
        return re.sub(r"\s+", " ", text).strip()

    def _collapse_search_tokens(self, text: str) -> str:
        if not text:
            return ""

        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
        text = "".join(ch if ch.isalnum() or ch.isspace() else "" for ch in text)
        return re.sub(r"\s+", " ", text).strip()

    def _artist_search_variants(self, artist: str) -> list[str]:
        variants: list[str] = []

        def add_variant(value: str) -> None:
            cleaned = self._clean_search_text(value)
            if cleaned and cleaned not in variants:
                variants.append(cleaned)

        add_variant(artist)
        add_variant(self._collapse_search_tokens(artist))

        normalized_tokens = [token for token in self._normalize_text(artist).split() if len(token) > 1]
        collapsed_tokens = [token for token in self._collapse_search_tokens(artist).split() if len(token) > 1]

        if collapsed_tokens:
            add_variant(" ".join(collapsed_tokens))

        if len(collapsed_tokens) > 1:
            add_variant(max(collapsed_tokens, key=len))

        if normalized_tokens:
            add_variant(" ".join(normalized_tokens))

        if len(normalized_tokens) > 1:
            add_variant(max(normalized_tokens, key=len))

        return variants

    def _build_search_attempts(self, artists: list[str], title: str, token: str) -> list[dict[str, str]]:
        artist_variants = self._artist_search_variants(artists[0]) if artists else []
        title_variants = [self._clean_search_text(title)]

        collapsed_title = self._collapse_search_tokens(title)
        if collapsed_title and collapsed_title not in title_variants:
            title_variants.append(collapsed_title)

        attempts: list[dict[str, str]] = []

        for artist_variant in artist_variants or [""]:
            for title_variant in title_variants:
                query_params = {
                    'artist': artist_variant,
                    'release_title': title_variant,
                    'type': 'master'
                }

                if token:
                    query_params['token'] = token

                if query_params not in attempts:
                    attempts.append(query_params)

        return attempts

    def _token_overlap(self, left: str, right: str) -> float:
        left_tokens = set(left.split())
        right_tokens = set(right.split())
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / len(left_tokens)

    def _apply_rate_limit(self, token):
        # 60 req/min with token (1000ms), 25 req/min without (2400ms)
        delay = 1000 if token else 2400
        ratecontrol.set_minimum_delay((self.host, 443), delay)

    def process_album(self, album: Album, metadata: Metadata, release: dict):
        token = (config.setting["discogs_personal_access_token"] or "").strip()
        self._apply_rate_limit(token)

        discogs_url = None

        for rel in release.get('relations', []):
            if rel.get('type') == 'discogs':
                discogs_url = rel.get('url', {}).get('resource')
                break

        if not discogs_url:
            for rel in release.get('release-group', {}).get('relations', []):
                if rel.get('type') == 'discogs':
                    discogs_url = rel.get('url', {}).get('resource')
                    break

        if discogs_url:
            match = re.search(r'/(release|master)/(\d+)', discogs_url)
            if match:
                entity_type = match.group(1)
                entity_id = match.group(2)

                album._requests += 1
                self.fetch_discogs_tags(album, metadata, entity_type, entity_id, token)
                return
        

        rg_credits = release.get('release-group', {}).get('artist-credit', [])
        credits = rg_credits or release.get('artist-credit', [])
        artists = [
            c.get('name') or c.get('artist', {}).get('name', '')
            for c in credits
            if isinstance(c, dict)
        ]
        title = metadata.get('album')

        if artists and title:
            album._requests += 1
            self.search_discogs(album, metadata, artists, title, token)

    def search_discogs(self, album: Album, metadata: Metadata, artists: list[str], title: str, token: str):
        attempts = self._build_search_attempts(artists, title, token)
        if not attempts:
            album._requests -= 1
            if album._requests == 0:
                album._finalize_loading(None)
            return

        self._perform_search_attempt(album, metadata, artists, title, attempts, 0)

    def _perform_search_attempt(
        self,
        album: Album,
        metadata: Metadata,
        artists: list[str],
        title: str,
        attempts: list[dict[str, str]],
        attempt_index: int,
    ) -> None:
        query_params = attempts[attempt_index]
        path = "/database/search?" + urllib.parse.urlencode(query_params)
        full_url = f"https://{self.host}{path}"

        album.tagger.webservice.get_url( # type: ignore
            url=full_url,
            parse_response_type="json",
            handler=functools.partial(
                self.handle_search_response,
                album,
                metadata,
                artists,
                title,
                attempts,
                attempt_index,
            )
        )

    def handle_search_response(
        self,
        album: Album,
        metadata: Metadata,
        artists: list[str],
        title: str,
        attempts: list[dict[str, str]],
        attempt_index: int,
        response,
        reply,
        error,
    ):
        try:
            if error or not response:
                log.error(f"Discogs Search API failed: {error}")
                return

            results = response.get('results', [])

            if not results and attempt_index + 1 < len(attempts):
                next_attempt = attempts[attempt_index + 1]
                log.debug(
                    "Discogs search returned no results for artist=%r title=%r, retrying with artist=%r title=%r",
                    attempts[attempt_index].get('artist', ''),
                    attempts[attempt_index].get('release_title', ''),
                    next_attempt.get('artist', ''),
                    next_attempt.get('release_title', ''),
                )
                album._requests += 1
                self._perform_search_attempt(album, metadata, artists, title, attempts, attempt_index + 1)
                return

            valid_result = self.validate_search_results(artists, title, results)

            if valid_result:
                genres = valid_result.get('genre', [])
                styles = valid_result.get('style', [])
                
                for genre in genres:
                    metadata.add('genre', genre)
                    
                if config.setting["discogs_style_tag"] is not None and config.setting["discogs_style_tag"] != "":
                    for style in styles:
                        metadata.add(config.setting["discogs_style_tag"] or "grouping", style)
        finally:
            album._requests -= 1
            if album._requests == 0:
                album._finalize_loading(None)

    def validate_search_results(self, mb_artists: list[str], mb_title: str, results: list):
        norm_mb_artists = [self._normalize_text(artist) for artist in mb_artists if artist]
        norm_mb_title = self._normalize_text(mb_title)

        for result in results:
            raw_title = result.get('title', '') or ''
            norm_full_title = self._normalize_text(raw_title)

            # "Artist(s) - Release" in search results
            parts = raw_title.split(' - ', 1)
            norm_dc_artist = self._normalize_text(parts[0]) if len(parts) > 1 else norm_full_title
            norm_dc_release = self._normalize_text(parts[1]) if len(parts) > 1 else norm_full_title

            title_match = (
                norm_mb_title == norm_dc_release
                or self._token_overlap(norm_mb_title, norm_dc_release) >= (config.setting["discogs_minimum_token_overlap"] or 80) / 100
                or self._token_overlap(norm_mb_title, norm_full_title) >= (config.setting["discogs_minimum_token_overlap"] or 80) / 100
            )

            artists_match = all(
                (artist in norm_dc_artist)
                or (artist in norm_full_title)
                or (self._token_overlap(artist, norm_dc_artist) >= (config.setting["discogs_minimum_token_overlap"] or 80) / 100)
                for artist in norm_mb_artists
            )

            if title_match and artists_match:
                return result

        return None

    def fetch_discogs_tags(self, album: Album, metadata: Metadata, entity_type: str, entity_id: str, token: str):
        path = f"/{entity_type}s/{entity_id}"
        if token:
            path += f"?token={token}"

        full_url = f"https://{self.host}{path}"

        album.tagger.webservice.get_url( # type: ignore
            url=full_url,
            parse_response_type="json",
            priority=True,
            handler=functools.partial(self.handle_tags_response, album, metadata)
        )

    def handle_tags_response(self, album: Album, metadata: Metadata, response, reply, error):
        try:
            if error or not response:
                log.error(f"Discogs Tags API failed: {error}")
                return

            genres = response.get('genres', [])
            styles = response.get('styles', [])

            for genre in genres:
                metadata.add('genre', genre)
            
            if config.setting["discogs_style_tag"] is not None and config.setting["discogs_style_tag"] != "":
                for style in styles:
                    metadata.add(config.setting["discogs_style_tag"] or "grouping", style)

        finally:
            album._requests -= 1
            if album._requests == 0:
                album._finalize_loading(None)

register_options_page(DiscogsGenreOptionsPage)
register_album_metadata_processor(DiscogsGenreProcessor().process_album)