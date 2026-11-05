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
