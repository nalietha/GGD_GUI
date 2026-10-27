"""The HTML pages.

They only read — every write is the API's, tested there — so what matters
here is that each page renders what storage and the renderer actually hold,
that the markup keeps the contract ``livepulse.js`` relies on, and that
nothing a user typed can become markup.
"""

from __future__ import annotations

import re

import pytest

from livepulse.core.models import Channel, DisplayMode, LiveState, StateSource

from .conftest import WebEnv

PAGES = ["/", "/channels", "/settings"]


class TestEveryPage:
    @pytest.mark.parametrize("path", PAGES)
    async def test_renders(self, env: WebEnv, path: str) -> None:
        response = await env.client.get(path)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    @pytest.mark.parametrize("path", PAGES)
    async def test_carries_the_off_switch(self, env: WebEnv, path: str) -> None:
        """§16: the off button is on every page, not buried in settings."""
        await env.store.update(mode=DisplayMode.OFF)

        html = (await env.client.get(path)).text

        assert 'name="mode" value="off"' in html
        assert re.search(r'value="off"\s+aria-pressed="true"', html)

    @pytest.mark.parametrize("path", PAGES)
    async def test_every_live_region_has_an_id(self, env: WebEnv, path: str) -> None:
        """The script swaps regions by id; one without an id never updates."""
        html = (await env.client.get(path)).text

        tags = re.findall(r"<[^>]*\bdata-live\b[^>]*>", html)
        assert tags, "expected at least one live region"
        assert all(re.search(r'\bid="[^"]+"', tag) for tag in tags)

    async def test_static_assets_are_served(self, env: WebEnv) -> None:
        for asset in ("livepulse.css", "livepulse.js"):
            response = await env.client.get(f"/static/{asset}")
            assert response.status_code == 200, asset


class TestDashboard:
    async def test_lists_every_node(self, env: WebEnv) -> None:
        html = (await env.client.get("/")).text

        for node_id in range(4):
            assert f'data-patch="/api/v1/nodes/{node_id}"' in html

    async def test_swatch_mirrors_the_renderer(self, env: WebEnv) -> None:
        channel = await env.add_channel()
        await env.repos.channels.update(channel.model_copy(update={"color": "#00ff88"}))
        await env.repos.nodes.bind(2, channel.id)
        await env.render({channel.id: LiveState(is_live=True, source=StateSource.MANUAL)})

        html = (await env.client.get("/")).text

        assert "--swatch: #00ff88" in html
        assert "is-live" in html

    async def test_before_the_first_frame_swatches_are_blank(self, env: WebEnv) -> None:
        html = (await env.client.get("/")).text

        assert "--swatch:" not in html
        assert "not rendered yet" in html

    async def test_binding_is_preselected(self, env: WebEnv) -> None:
        channel = await env.add_channel()
        await env.repos.nodes.bind(1, channel.id)

        html = (await env.client.get("/")).text

        assert re.search(rf'value="{channel.id}"\s+selected', html)


class TestChannels:
    async def test_lists_channels_with_state(self, env: WebEnv) -> None:
        live = await env.add_channel("alice")
        idle = await env.add_channel("bob")
        await env.repos.nodes.bind(0, live.id)
        await env.repos.live_state.record(
            live.id, LiveState(is_live=True, title="Speedrun", source=StateSource.MANUAL)
        )

        html = (await env.client.get("/channels")).text

        assert "alice" in html and "bob" in html
        assert "Speedrun" in html
        assert html.count("not on a node") == 1, "only bob is unbound"
        assert f"/api/v1/channels/{idle.id}" in html

    async def test_empty_state_explains_what_to_do(self, env: WebEnv) -> None:
        html = (await env.client.get("/channels")).text

        assert "No channels yet" in html

    async def test_names_are_escaped(self, env: WebEnv) -> None:
        """A display name comes from a platform or a user; it is never markup."""
        hostile = Channel(
            provider="manual",
            platform_ref="x",
            handle="x",
            display_name="<script>alert(1)</script>",
        )
        await env.repos.channels.add(hostile)

        html = (await env.client.get("/channels")).text

        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html


class TestSettings:
    async def test_shows_current_values(self, env: WebEnv) -> None:
        await env.store.update(brightness=77, ambient_effect="rainbow")

        html = (await env.client.get("/settings")).text

        assert "<output>77</output>" in html
        assert re.search(r'value="rainbow"[^>]*selected', html)

    async def test_reports_provider_health(self, env: WebEnv) -> None:
        html = (await env.client.get("/settings")).text

        assert "manual: healthy" in html
