import httpx
import respx

from am2sp.spotify import API, Spotify


def _client():
    return Spotify(http=httpx.Client(base_url=API), token={"access_token": "t"})


@respx.mock
def test_retries_on_429(monkeypatch):
    monkeypatch.setattr("am2sp.spotify.time.sleep", lambda s: None)
    route = respx.get(f"{API}/me").mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(200, json={"id": "me"})]
    )
    assert _client().me() == {"id": "me"}
    assert route.call_count == 2


@respx.mock
def test_save_to_library_batches_40_in_query_string():
    route = respx.put(f"{API}/me/library").mock(return_value=httpx.Response(200))
    uris = [f"spotify:track:{i}" for i in range(85)]
    _client().save_to_library(uris)
    assert route.call_count == 3
    first = route.calls[0].request.url.params["uris"].split(",")
    assert len(first) == 40 and first[0] == "spotify:track:0"


@respx.mock
def test_add_to_playlist_uses_items_endpoint_in_batches_of_100():
    route = respx.post(f"{API}/playlists/abc/items").mock(return_value=httpx.Response(201, json={"snapshot_id": "s"}))
    _client().add_to_playlist("abc", [f"spotify:track:{i}" for i in range(150)])
    assert route.call_count == 2


@respx.mock
def test_search_uses_limit_10():
    route = respx.get(f"{API}/search").mock(return_value=httpx.Response(200, json={"tracks": {"items": [None]}}))
    assert _client().search_tracks("x") == []
    assert route.calls[0].request.url.params["limit"] == "10"


@respx.mock
def test_saved_albums_follows_next_pages():
    page2 = f"{API}/me/albums?offset=50&limit=50"
    route = respx.get(f"{API}/me/albums").mock(
        side_effect=[
            httpx.Response(200, json={"items": [{"album": {"uri": "spotify:album:1"}}], "next": page2}),
            httpx.Response(200, json={"items": [{"album": {"uri": "spotify:album:2"}}, {"album": None}], "next": None}),
        ]
    )
    assert [a["uri"] for a in _client().saved_albums()] == ["spotify:album:1", "spotify:album:2"]
    assert route.calls[1].request.url.params["offset"] == "50"


@respx.mock
def test_search_albums_and_album_tracks():
    search = respx.get(f"{API}/search").mock(
        return_value=httpx.Response(200, json={"albums": {"items": [{"uri": "spotify:album:a", "id": "a"}, None]}})
    )
    assert [a["id"] for a in _client().search_albums("x")] == ["a"]
    assert search.calls[0].request.url.params["type"] == "album"
    respx.get(f"{API}/albums/a/tracks").mock(
        return_value=httpx.Response(200, json={"items": [{"name": "t1"}, {"name": "t2"}], "next": None})
    )
    assert [t["name"] for t in _client().album_tracks("a")] == ["t1", "t2"]


@respx.mock
def test_my_playlists_keeps_only_owned():
    respx.get(f"{API}/me").mock(return_value=httpx.Response(200, json={"id": "me"}))
    respx.get(f"{API}/me/playlists").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"id": "a", "owner": {"id": "me"}}, {"id": "b", "owner": {"id": "other"}}], "next": None},
        )
    )
    assert [p["id"] for p in _client().my_playlists()] == ["a"]


@respx.mock
def test_playlist_items_reads_item_field_and_drops_episodes():
    items = [
        {"item": {"type": "track", "uri": "spotify:track:1"}},
        {"track": {"type": "track", "uri": "spotify:track:2"}},  # pre-Feb-2026 shape
        {"item": {"type": "episode", "uri": "spotify:episode:3"}},
        {"item": {"type": "track", "uri": "spotify:local:x:y:z:1"}},
        {"item": None},
    ]
    respx.get(f"{API}/playlists/p/items").mock(return_value=httpx.Response(200, json={"items": items, "next": None}))
    assert [t["uri"] for t in _client().playlist_items("p")] == ["spotify:track:1", "spotify:track:2"]


@respx.mock
def test_long_retry_after_raises_instead_of_sleeping(monkeypatch):
    import pytest

    from am2sp.spotify import RateLimitError

    slept = []
    monkeypatch.setattr("am2sp.spotify.time.sleep", slept.append)
    respx.get(f"{API}/search").mock(return_value=httpx.Response(429, headers={"Retry-After": "84104"}))
    with pytest.raises(RateLimitError) as e:
        _client().search_tracks("x")
    assert e.value.retry_after == 84104 and slept == []
    assert "23h21m" in str(e.value)
