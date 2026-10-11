import re
import urllib.parse
import functools
import unicodedata
from typing import Callable

from PyQt6 import QtWidgets, QtCore

from picard.plugin3.api import (
    Album,
    BaseAction,
    Metadata,
    OptionsPage,
    PluginApi,
)
from picard.webservice import PendingRequest, ratecontrol

from .constants import *

class DiscogsGenreOptionsPage(OptionsPage):
    NAME = "discogs_genre_and_style"
    TITLE = "Discogs Genre & Style"
    PARENT = "plugins"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        options_group = QtWidgets.QGroupBox("Options", self)
        options_group.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Minimum)
        options_layout = QtWidgets.QVBoxLayout(options_group)

        self.token_input = QtWidgets.QLineEdit(self)
        self.token_input.setPlaceholderText("Account > Settings > Developers > Generate token")

        self.style_input = QtWidgets.QLineEdit(self)
        self.style_input.setPlaceholderText(DEFAULT_STYLE_TAG)
        self.style_input.setToolTip(f"Metadata tag to store Discogs style (default: {DEFAULT_STYLE_TAG}), set it blank to disable")

        min_overlap_layout = QtWidgets.QHBoxLayout()

        min_overlap_label = QtWidgets.QLabel("Minimum Token Overlap", self)
        min_overlap_label.setToolTip("Minimum percentage of token overlap required to consider a Discogs search result as a match (0-100%)")
        min_overlap_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self.min_overlap_input = QtWidgets.QSpinBox(self)
        self.apply_option_bounds(self.min_overlap_input, OPTION_MIN_OVERLAP)
        self.min_overlap_input.setSuffix(" %")
        self.min_overlap_input.setSizePolicy(QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Preferred)

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
        config = self.api.plugin_config
        self.token_input.setText(config[OPTION_TOKEN])
        self.style_input.setText(config[OPTION_STYLE_TAG])
        self.min_overlap_input.setValue(config[OPTION_MIN_OVERLAP])

    def save(self):
        config = self.api.plugin_config
        config[OPTION_TOKEN] = self.token_input.text().strip()
        config[OPTION_STYLE_TAG] = self.style_input.text().strip()
        config[OPTION_MIN_OVERLAP] = self.min_overlap_input.value()

class DiscogsGenreProcessor:
    def __init__(self, api: PluginApi):
        self.api = api
        self.host = DISCOGS_HOST

    def _token(self) -> str:
        return (self.api.plugin_config[OPTION_TOKEN] or "").strip()

    def _min_overlap(self) -> float:
        return self.api.plugin_config[OPTION_MIN_OVERLAP] / 100

    def _start_task(
        self,
        album: Album,
        task_id: str,
        description: str,
        request_factory: Callable[[], PendingRequest],
        blocking: bool,
    ) -> None:
        # Blocking tasks hold back album finalization, so the tags written to the
        # album metadata get copied to its tracks like MusicBrainz data does
        self.api.add_album_task(
            album,
            task_id,
            description,
            request_factory=request_factory,
            blocking=blocking,
        )

    def _finish_task(self, album: Album, task_id: str, blocking: bool) -> None:
        self.api.complete_album_task(album, task_id)
        if blocking and not album.loaded and not album.has_critical_tasks():
            album._finalize_loading(None)

    def _add_discogs_tags(self, metadata: Metadata, genres: list[str], styles: list[str]) -> None:
        metadata.delete('genre')
        for genre in genres:
            metadata.add('genre', genre)

        style_tag = self.api.plugin_config[OPTION_STYLE_TAG]
        if style_tag:
            metadata.delete(style_tag)
            for style in styles:
                metadata.add(style_tag, style)

    def _propagate_tags_to_album_tracks(self, album: Album, genres: list[str], styles: list[str]) -> None:
        for track in album.tracks:
            self._add_discogs_tags(track.metadata, genres, styles)
            for file in track.files:
                self._add_discogs_tags(file.metadata, genres, styles)
                file.update()
            track.update()

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
        token = self._token()
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
            self.search_discogs(album, metadata, artists, title, token)

    def search_discogs(self, album: Album, metadata: Metadata, artists: list[str], title: str, token: str):
        attempts = self._build_search_attempts(artists, title, token)
        if not attempts:
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
        task_id = f"search_{attempt_index}"

        self._start_task(
            album,
            task_id,
            f"Searching Discogs ({attempt_index + 1}/{len(attempts)})",
            request_factory=lambda: self.api.web_service.get_url(
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
                    task_id,
                ),
            ),
            blocking=True,
        )

    def handle_search_response(
        self,
        album: Album,
        metadata: Metadata,
        artists: list[str],
        title: str,
        attempts: list[dict[str, str]],
        attempt_index: int,
        task_id: str,
        response,
        _reply,
        error,
    ):
        try:
            if error or not response:
                self.api.logger.error(f"Discogs Search API failed: {error}")
                return

            results = response.get('results', [])

            if not results and attempt_index + 1 < len(attempts):
                next_attempt = attempts[attempt_index + 1]
                self.api.logger.debug(
                    "Discogs search returned no results for artist=%r title=%r, retrying with artist=%r title=%r",
                    attempts[attempt_index].get('artist', ''),
                    attempts[attempt_index].get('release_title', ''),
                    next_attempt.get('artist', ''),
                    next_attempt.get('release_title', ''),
                )
                # Queued before this task completes so the album keeps waiting
                self._perform_search_attempt(album, metadata, artists, title, attempts, attempt_index + 1)
                return

            valid_result = self.validate_search_results(artists, title, results)

            if valid_result:
                genres = valid_result.get('genre', [])
                styles = valid_result.get('style', [])

                self._add_discogs_tags(metadata, genres, styles)
        finally:
            self._finish_task(album, task_id, blocking=True)

    def validate_search_results(self, mb_artists: list[str], mb_title: str, results: list):
        norm_mb_artists = [self._normalize_text(artist) for artist in mb_artists if artist]
        norm_mb_title = self._normalize_text(mb_title)
        min_overlap = self._min_overlap()

        for result in results:
            raw_title = result.get('title', '') or ''
            norm_full_title = self._normalize_text(raw_title)

            # "Artist(s) - Release" in search results
            parts = raw_title.split(' - ', 1)
            norm_dc_artist = self._normalize_text(parts[0]) if len(parts) > 1 else norm_full_title
            norm_dc_release = self._normalize_text(parts[1]) if len(parts) > 1 else norm_full_title

            title_match = (
                norm_mb_title == norm_dc_release
                or self._token_overlap(norm_mb_title, norm_dc_release) >= min_overlap
                or self._token_overlap(norm_mb_title, norm_full_title) >= min_overlap
            )

            artists_match = all(
                (artist in norm_dc_artist)
                or (artist in norm_full_title)
                or (self._token_overlap(artist, norm_dc_artist) >= min_overlap)
                for artist in norm_mb_artists
            )

            if title_match and artists_match:
                return result

        return None

    def fetch_discogs_tags(
        self,
        album: Album,
        metadata: Metadata,
        entity_type: str,
        entity_id: str,
        token: str,
        during_album_load: bool = True,
    ):
        path = f"/{entity_type}s/{entity_id}"
        if token:
            path += f"?token={token}"

        full_url = f"https://{self.host}{path}"
        task_id = f"tags_{entity_type}_{entity_id}"

        self._start_task(
            album,
            task_id,
            f"Fetching Discogs {entity_type} {entity_id}",
            request_factory=lambda: self.api.web_service.get_url(
                url=full_url,
                parse_response_type="json",
                priority=True,
                handler=functools.partial(self.handle_tags_response, album, metadata, during_album_load, task_id),
            ),
            blocking=during_album_load,
        )

    def handle_tags_response(self, album: Album, metadata: Metadata, during_album_load: bool, task_id: str, response, reply, error):
        try:
            if error or not response:
                self.api.logger.error(f"Discogs Tags API failed: {error}")
                return

            genres = response.get('genres', [])
            styles = response.get('styles', [])

            self._add_discogs_tags(metadata, genres, styles)

            if not during_album_load:
                self._propagate_tags_to_album_tracks(album, genres, styles)

        finally:
            self._finish_task(album, task_id, blocking=during_album_load)
            if not during_album_load:
                album.update()

class DiscogsManualSearchAction(BaseAction):
    TITLE = "[Discogs] Manual Search"

    def callback(self, objs):
        albums = [a for a in objs if isinstance(a, Album)]

        if not albums:
            return

        processor = DiscogsGenreProcessor(self.api)

        for album in albums:
            title = album.metadata.get('album', '') if getattr(album, 'metadata', None) else ''

            prompt = "Enter Discogs master/release code"
            if title:
                prompt = f"{prompt} for \"{title}\""
            prompt = f"{prompt}\nFound in the top right corner of the page, example: [m123456] or [r123456]"

            parent = QtWidgets.QApplication.activeWindow()
            input_dialog = QtWidgets.QInputDialog(parent)
            input_dialog.setWindowTitle("Discogs Manual Search")
            input_dialog.setLabelText(prompt)
            input_dialog.setInputMode(QtWidgets.QInputDialog.InputMode.TextInput)
            input_dialog.setTextValue("")

            def focus_input() -> None:
                line_edit = input_dialog.findChild(QtWidgets.QLineEdit)
                if line_edit:
                    line_edit.setFocus()

            QtCore.QTimer.singleShot(0, focus_input)

            ok = input_dialog.exec()
            value = input_dialog.textValue()

            if not ok or not value.strip():
                continue

            value = value.strip()
            match = re.match(r'^\[[mMrR](\d+)\]$', value)
            if match:
                value = value[1:-1]

            if not match:
                QtWidgets.QMessageBox.warning(
                    parent,
                    "Invalid code",
                    "Please enter a valid Discogs master/release code, example: [m123456] or [r123456]",
                )
                continue

            entity_id = match.group(1)
            entity_type = 'master' if value.strip().lower().startswith('m') else 'release'

            token = processor._token()
            processor._apply_rate_limit(token)

            processor.fetch_discogs_tags(
                album,
                album.metadata,
                entity_type,
                entity_id,
                token,
                during_album_load=False,
            )

def _migrate_legacy_options(api: PluginApi) -> None:
    global_setting = api.global_config.setting
    for name, legacy_name in LEGACY_OPTION_NAMES.items():
        if name in api.plugin_config or legacy_name not in global_setting:
            continue
        qtype = int if name == OPTION_MIN_OVERLAP else str
        value = global_setting.raw_value(legacy_name, qtype=qtype)
        if value is not None:
            api.plugin_config[name] = value
            api.logger.info("Migrated setting %s from plugin API v2", legacy_name)

def process_album(api: PluginApi, album: Album, metadata: Metadata, release: dict):
    DiscogsGenreProcessor(api).process_album(album, metadata, release)

def enable(api: PluginApi):
    api.plugin_config.register_option(OPTION_TOKEN, DEFAULT_TOKEN)
    api.plugin_config.register_option(OPTION_STYLE_TAG, DEFAULT_STYLE_TAG)
    api.plugin_config.register_option(OPTION_MIN_OVERLAP, DEFAULT_MIN_OVERLAP, bounds=(0, 100))
    _migrate_legacy_options(api)

    api.register_options_page(DiscogsGenreOptionsPage)
    api.register_album_action(DiscogsManualSearchAction)
    api.register_album_metadata_processor(process_album)
