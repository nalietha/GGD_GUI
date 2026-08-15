"""End to end: domain models → render state → compositor → driver.

Every layer built so far, joined up, with no hardware and no network. This is
the test that would have caught V1's central failure — that its web app, its
LED process, and its preview grid each resolved node colour differently and
routinely disagreed about what the stand was showing.
"""

from __future__ import annotations

from dataclasses import dataclass

from livepulse.config.settings import LayoutSettings
from livepulse.core.clock import ManualClock
from livepulse.core.models import Channel, DisplayMode, LiveState, Node
from livepulse.leds.color import BLACK, RGB, IdentityGamma
from livepulse.leds.compositor import MASTER_FADE_SECONDS, Compositor
from livepulse.leds.driver.sim import SimulatedDriver
from livepulse.leds.layout import Layout
from livepulse.leds.renderer import Renderer
from livepulse.leds.state import build_render_state

IRONMOUSE_PURPLE = RGB(0xE1, 0x00, 0xFF)


@dataclass
class Settings:
    brightness: int = 255
    mode: DisplayMode = DisplayMode.STREAMER
    ambient_effect: str = "gradient"
    ambient_enabled: bool = True


class Pipeline:
    """The real stack, assembled the way the composition root will assemble it."""

    def __init__(self, node_count: int = 16, leds_per_node: int = 7) -> None:
        self.layout = Layout(LayoutSettings(nodes=node_count, leds_per_node=leds_per_node))
        self.driver = SimulatedDriver(self.layout.pixel_count)
        self.clock = ManualClock()
        self.renderer = Renderer(
            self.driver,
            Compositor(self.layout, IdentityGamma()),
            fps=60,
            clock=self.clock,
        )

    def draw(
        self,
        nodes: list[Node],
        channels: list[Channel],
        live: dict[str, bool],
        settings: Settings | None = None,
        *,
        settle: bool = True,
    ) -> tuple[RGB, ...]:
        by_id = {c.id: c for c in channels}
        by_handle = {c.handle: c.id for c in channels}
        states = {by_handle[handle]: LiveState(is_live=value) for handle, value in live.items()}

        self.renderer.submit(build_render_state(nodes, by_id, states, settings or Settings()))
        self.renderer.tick()
        if settle:
            # Past the start-up master fade.
            self.clock.advance(MASTER_FADE_SECONDS * 2)
            self.renderer.tick()
        return self.driver.frame

    def node_color(self, frame: tuple[RGB, ...], node_id: int) -> RGB:
        return frame[self.layout.pixels(node_id).start]


def channel(handle: str, color: str, alt: str | None = None) -> Channel:
    return Channel(
        provider="twitch", platform_ref=handle, handle=handle, color=color, alt_color=alt
    )


class TestFullPipeline:
    def test_a_live_streamer_lights_its_node_in_the_channel_colour(self) -> None:
        pipeline = Pipeline()
        ironmouse = channel("IronMouse", "#e100ff")
        nodes = [Node(id=n) for n in range(16)]
        nodes[9] = Node(id=9, channel_id=ironmouse.id, effect="static", color="#808000")

        frame = pipeline.draw(nodes, [ironmouse], {"IronMouse": True})

        assert pipeline.node_color(frame, 9) == IRONMOUSE_PURPLE

    def test_the_colour_lands_on_the_physically_correct_pixels(self) -> None:
        """Node 9 sits at physical position 1 on this stand — pixels 7..13.

        The layout is honoured all the way through. V1 recomputed this
        transform in three files and a Razor page, and they disagreed.
        """
        pipeline = Pipeline()
        ironmouse = channel("IronMouse", "#e100ff")
        nodes = [Node(id=n) for n in range(16)]
        nodes[9] = Node(id=9, channel_id=ironmouse.id, effect="static")

        frame = pipeline.draw(nodes, [ironmouse], {"IronMouse": True})

        assert list(frame[7:14]) == [IRONMOUSE_PURPLE] * 7
        assert frame[0:7] == (BLACK,) * 7

    def test_offline_streamers_stay_dark_while_one_is_live(self) -> None:
        pipeline = Pipeline()
        live = channel("IronMouse", "#e100ff")
        dark = channel("Nyanners", "#ffd500")
        nodes = [Node(id=n) for n in range(16)]
        nodes[0] = Node(id=0, channel_id=dark.id, effect="static")
        nodes[9] = Node(id=9, channel_id=live.id, effect="static")

        frame = pipeline.draw(nodes, [live, dark], {"IronMouse": True, "Nyanners": False})

        assert pipeline.node_color(frame, 9) == IRONMOUSE_PURPLE
        assert pipeline.node_color(frame, 0) is BLACK

    def test_nobody_live_gives_the_ambient_gradient(self) -> None:
        """V1's good idea: an idle stand shows a gradient, not darkness."""
        pipeline = Pipeline()
        dark = channel("Nyanners", "#ffd500")
        nodes = [Node(id=n) for n in range(16)]
        nodes[0] = Node(id=0, channel_id=dark.id)

        frame = pipeline.draw(nodes, [dark], {"Nyanners": False})

        colours = {pipeline.node_color(frame, n) for n in range(16)}
        assert len(colours) == 16, "the gradient should give every node a distinct hue"
        assert BLACK not in colours

    def test_two_nodes_can_share_one_channel(self) -> None:
        """The real save.json binds nodes 1 and 3 to JuniperActias."""
        pipeline = Pipeline()
        juniper = channel("JuniperActias", "#a5dfa3")
        nodes = [Node(id=n) for n in range(16)]
        nodes[1] = Node(id=1, channel_id=juniper.id, effect="static")
        nodes[3] = Node(id=3, channel_id=juniper.id, effect="static")

        frame = pipeline.draw(nodes, [juniper], {"JuniperActias": True})

        expected = RGB(0xA5, 0xDF, 0xA3)
        assert pipeline.node_color(frame, 1) == expected
        assert pipeline.node_color(frame, 3) == expected

    def test_off_mode_fades_the_whole_stand_down(self) -> None:
        pipeline = Pipeline()
        live = channel("IronMouse", "#e100ff")
        nodes = [Node(id=n) for n in range(16)]
        nodes[9] = Node(id=9, channel_id=live.id, effect="static")

        pipeline.draw(nodes, [live], {"IronMouse": True})
        pipeline.clock.advance(10.0)
        frame = pipeline.draw(
            nodes,
            [live],
            {"IronMouse": True},
            Settings(mode=DisplayMode.OFF),
            settle=False,
        )
        pipeline.clock.advance(10.0)
        pipeline.renderer.tick()

        assert set(pipeline.driver.frame) == {BLACK}
        assert frame is not None

    def test_brightness_scales_the_whole_stand(self) -> None:
        pipeline = Pipeline()
        live = channel("IronMouse", "#ffffff")
        nodes = [Node(id=n) for n in range(16)]
        nodes[0] = Node(id=0, channel_id=live.id, effect="static")

        frame = pipeline.draw(nodes, [live], {"IronMouse": True}, Settings(brightness=64))

        assert pipeline.node_color(frame, 0).r < 70


class TestGoLiveAnimation:
    def test_a_transient_overlays_then_settles(self) -> None:
        """The V1 behaviour, without the three-second block per node.

        V1's flash_node slept inside the poll loop; sixteen streamers going
        live together froze ingestion for the better part of a minute.
        """
        pipeline = Pipeline()
        live = channel("IronMouse", "#e100ff", alt="#00ff00")
        nodes = [Node(id=n) for n in range(16)]
        nodes[9] = Node(id=9, channel_id=live.id, effect="static")

        pipeline.draw(nodes, [live], {"IronMouse": True})

        now = pipeline.clock.monotonic()
        pipeline.renderer.compositor.add_transient({9}, "flash", now=now, duration=1.0)
        pipeline.renderer.tick()
        during = pipeline.node_color(pipeline.driver.frame, 9)

        pipeline.clock.advance(2.0)
        pipeline.renderer.tick()
        after = pipeline.node_color(pipeline.driver.frame, 9)

        assert during != IRONMOUSE_PURPLE, "the flash should be visible"
        assert after == IRONMOUSE_PURPLE, "and should settle back afterwards"

    def test_sixteen_at_once_still_produce_one_frame(self) -> None:
        pipeline = Pipeline()
        channels = [channel(f"s{n}", "#ffffff") for n in range(16)]
        nodes = [Node(id=n, channel_id=channels[n].id, effect="static") for n in range(16)]

        pipeline.draw(nodes, channels, {f"s{n}": True for n in range(16)})

        now = pipeline.clock.monotonic()
        for node_id in range(16):
            pipeline.renderer.compositor.add_transient({node_id}, "flash", now=now)

        assert pipeline.renderer.compositor.active_transients == 16
        pipeline.renderer.tick()
        assert len(pipeline.driver.frame) == 112
