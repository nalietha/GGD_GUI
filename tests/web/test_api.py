"""The REST API, driven through the ASGI interface.

No sockets, no uvicorn — httpx talks to the FastAPI app directly, over the
same in-memory environment the coordinator tests use. What matters here is
the contract: every write lands in storage AND on the bus, and no response
ever carries a secret.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from livepulse.core.events import ChannelConfigChanged, NodeConfigChanged
from livepulse.core.models import DisplayMode, LiveState, StateSource
from livepulse.leds.state import build_render_state

from .conftest import WebEnv


class TestNodes:
    async def test_lists_the_configured_nodes(self, env: WebEnv) -> None:
        response = await env.client.get("/api/v1/nodes")

        assert response.status_code == 200
        assert [node["id"] for node in response.json()] == [0, 1, 2, 3]

    async def test_binding_persists_and_announces(self, env: WebEnv) -> None:
        channel = await env.add_channel()

        with env.bus.subscribe(NodeConfigChanged) as events:
            response = await env.client.patch(
                "/api/v1/nodes/1", json={"channel_id": str(channel.id), "color": "#123456"}
            )
            announced = await asyncio.wait_for(anext(aiter(events)), timeout=1.0)

        assert response.status_code == 200
        assert announced.node_id == 1
        stored = await env.repos.nodes.get(1)
        assert stored is not None
        assert stored.channel_id == channel.id
        assert stored.color == "#123456"

    async def test_explicit_null_unbinds(self, env: WebEnv) -> None:
        channel = await env.add_channel()
        await env.repos.nodes.bind(1, channel.id)

        response = await env.client.patch("/api/v1/nodes/1", json={"channel_id": None})

        assert response.status_code == 200
        stored = await env.repos.nodes.get(1)
        assert stored is not None and stored.channel_id is None

    async def test_unknown_channel_and_effect_are_rejected(self, env: WebEnv) -> None:
        missing = await env.client.patch("/api/v1/nodes/1", json={"channel_id": str(uuid4())})
        assert missing.status_code == 404

        bad_effect = await env.client.patch("/api/v1/nodes/1", json={"effect": "disco"})
        assert bad_effect.status_code == 422
        assert "disco" in bad_effect.json()["detail"]

        # And neither rejected request changed the node.
        stored = await env.repos.nodes.get(1)
        assert stored is not None and stored.channel_id is None and stored.effect == "static"

    async def test_missing_node_is_404(self, env: WebEnv) -> None:
        response = await env.client.patch("/api/v1/nodes/99", json={"color": "#000000"})
        assert response.status_code == 404


class TestChannels:
    async def test_add_resolves_and_announces(self, env: WebEnv) -> None:
        with env.bus.subscribe(ChannelConfigChanged) as events:
            response = await env.client.post(
                "/api/v1/channels", json={"input": "alice", "provider": "manual"}
            )
            announced = await asyncio.wait_for(anext(aiter(events)), timeout=1.0)

        assert response.status_code == 201
        body = response.json()
        assert body["provider"] == "manual"
        assert body["handle"] == "alice"
        assert str(announced.channel_id) == body["id"]
        assert await env.repos.channels.find_by_ref("manual", "alice") is not None

    async def test_duplicate_add_is_a_conflict(self, env: WebEnv) -> None:
        await env.add_channel("alice")

        response = await env.client.post(
            "/api/v1/channels", json={"input": "alice", "provider": "manual"}
        )

        assert response.status_code == 409

    async def test_unresolvable_input_is_rejected(self, env: WebEnv) -> None:
        response = await env.client.post(
            "/api/v1/channels", json={"input": "   ", "provider": "manual"}
        )
        assert response.status_code in (404, 422)

    async def test_list_includes_latest_observation(self, env: WebEnv) -> None:
        channel = await env.add_channel()
        await env.repos.live_state.record(
            channel.id, LiveState(is_live=True, source=StateSource.MANUAL)
        )

        response = await env.client.get("/api/v1/channels")

        assert response.status_code == 200
        (entry,) = response.json()
        assert entry["channel"]["id"] == str(channel.id)
        assert entry["state"]["is_live"] is True

    async def test_edit_and_delete(self, env: WebEnv) -> None:
        channel = await env.add_channel()

        patched = await env.client.patch(
            f"/api/v1/channels/{channel.id}", json={"color": "#00ff00", "enabled": False}
        )
        assert patched.status_code == 200
        assert patched.json()["color"] == "#00ff00"
        assert patched.json()["enabled"] is False

        deleted = await env.client.delete(f"/api/v1/channels/{channel.id}")
        assert deleted.status_code == 204
        assert await env.client.delete(f"/api/v1/channels/{channel.id}") != 204  # gone
        assert (await env.client.get(f"/api/v1/channels/{channel.id}")).status_code == 404


class TestSettings:
    async def test_round_trip(self, env: WebEnv) -> None:
        before = await env.client.get("/api/v1/settings")
        assert before.json()["mode"] == "streamer"

        response = await env.client.patch("/api/v1/settings", json={"brightness": 42})

        assert response.status_code == 200
        assert response.json()["brightness"] == 42
        assert env.store.current.brightness == 42

    async def test_the_off_button(self, env: WebEnv) -> None:
        """PATCH {"mode": "off"} is the one-touch off from the operating model."""
        response = await env.client.patch("/api/v1/settings", json={"mode": "off"})

        assert response.status_code == 200
        assert env.store.current.mode is DisplayMode.OFF

    async def test_invalid_values_are_rejected(self, env: WebEnv) -> None:
        assert (
            await env.client.patch("/api/v1/settings", json={"brightness": 999})
        ).status_code == 422
        assert (await env.client.patch("/api/v1/settings", json={"nonsense": 1})).status_code == 422
        assert env.store.current.brightness == 64  # untouched


class TestProvidersEffectsPreview:
    async def test_providers_report_status_but_never_secrets(self, env: WebEnv) -> None:
        response = await env.client.get("/api/v1/providers")

        assert response.status_code == 200
        by_id = {entry["id"]: entry for entry in response.json()}
        assert by_id["manual"]["credentials_configured"] is True
        assert by_id["manual"]["running"] is True
        assert by_id["twitch"]["running"] is False
        for entry in by_id.values():
            assert "client_secret" not in str(entry)

    async def test_effects_list_names_the_builtins(self, env: WebEnv) -> None:
        response = await env.client.get("/api/v1/effects")

        assert response.status_code == 200
        assert "static" in response.json()

    async def test_preview_mirrors_the_renderer(self, env: WebEnv) -> None:
        # Before anything is submitted there is nothing to mirror.
        assert (await env.client.get("/api/v1/preview")).status_code == 503

        channel = await env.add_channel()
        await env.repos.nodes.bind(0, channel.id)
        nodes = await env.repos.nodes.list()
        env.renderer.submit(
            build_render_state(
                nodes,
                {channel.id: channel},
                {channel.id: LiveState(is_live=True, source=StateSource.MANUAL)},
                env.store.current,
            )
        )

        response = await env.client.get("/api/v1/preview")

        assert response.status_code == 200
        body = response.json()
        assert body["mode"] == "streamer"
        assert body["nodes"][0]["live"] is True
        assert body["nodes"][0]["color"] == channel.color.lower()

    async def test_healthz_summarises_the_system(self, env: WebEnv) -> None:
        response = await env.client.get("/healthz")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["renderer"]["running"] is False  # not started in these tests
