"""Cross-engine paint-level regression coverage for the inline SVG map.

These tests intentionally inspect both computed SVG paint and screenshot
pixels. Chromium's automatic darkening can alter final paint without changing
getComputedStyle(), so computed-style assertions alone cannot catch the pale
surface / light text failure that motivated this suite.

Run with: make test-browser
"""

from __future__ import annotations

import io
import os
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
import requests
from PIL import Image
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from werkzeug.serving import make_server

pytestmark = pytest.mark.browser

EXPECTED_NODE_COUNT = 8
EXPECTED_EDGE_COUNT = 7


def _srgb_channel(value: int) -> float:
    channel = value / 255
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _luminance(rgb: tuple[int, int, int]) -> float:
    red, green, blue = (_srgb_channel(value) for value in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _parse_rgb(value: str) -> tuple[int, int, int]:
    body = value[value.index("(") + 1 : value.index(")")]
    channels = body.replace(",", " ").split()
    return tuple(int(float(channel)) for channel in channels[:3])


@pytest.fixture(scope="module")
def browser_panel(tmp_path_factory):
    import panel.app as panel_app

    database = tmp_path_factory.mktemp("browser-panel") / "ot-range.db"
    panel_app.app.config["TESTING"] = True  # nosemgrep -- isolated test server, not app config
    panel_app.app.config.update(DATABASE_PATH=str(database), SCRYPT_N=2**10)
    panel_app.app.extensions.pop("ot_range_storage", None)
    panel_app.app.extensions.pop("ot_range_auth", None)

    server = make_server("127.0.0.1", 0, panel_app.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield base_url
    finally:
        server.shutdown()
        thread.join(timeout=5)
        panel_app.app.extensions.pop("ot_range_storage", None)
        panel_app.app.extensions.pop("ot_range_auth", None)


@contextmanager
def _driver(engine: str):
    try:
        if engine == "chromium":
            binary = (
                shutil.which("chromium")
                or shutil.which("chromium-browser")
                or shutil.which("google-chrome")
                or shutil.which("google-chrome-stable")
            )
            if binary is None:
                pytest.skip("Chromium is not installed")
            options = webdriver.ChromeOptions()
            options.binary_location = binary
            options.add_argument("--headless=new")
            options.add_argument("--disable-extensions")
            options.add_argument("--window-size=1440,1000")
            driver = webdriver.Chrome(options=options)
        else:
            binary = shutil.which("firefox")
            if binary is None:
                pytest.skip("Firefox is not installed")
            options = webdriver.FirefoxOptions()
            options.binary_location = binary
            options.add_argument("-headless")
            driver = webdriver.Firefox(options=options)
    except WebDriverException as exc:
        pytest.skip(f"{engine} WebDriver is unavailable: {exc.msg}")

    try:
        driver.set_window_size(1440, 1000)
        yield driver
    finally:
        driver.quit()


def _open_student(driver, base_url: str) -> requests.Session:
    session = requests.Session()
    response = session.post(
        f"{base_url}/api/profiles",
        json={"display_name": f"{driver.capabilities['browserName']} Map Test"},
        timeout=10,
    )
    response.raise_for_status()

    driver.get(base_url)
    driver.add_cookie(
        {
            "name": "ot_range_student",
            "value": session.cookies["ot_range_student"],
            "path": "/",
            "sameSite": "Strict",
        }
    )
    driver.get(f"{base_url}/student")
    WebDriverWait(driver, 20).until(
        lambda current: (
            len(current.find_elements(By.CSS_SELECTOR, ".map-node")) == EXPECTED_NODE_COUNT
        )
    )
    driver.execute_script("document.querySelector('#network-map').scrollIntoView({block: 'start'})")
    return session


def _computed_paint(driver, selector: str) -> dict[str, str]:
    return driver.execute_script(
        """
        const style = getComputedStyle(document.querySelector(arguments[0]));
        return {fill: style.fill, stroke: style.stroke, opacity: style.opacity,
                filter: style.filter, blend: style.mixBlendMode,
                colorScheme: style.colorScheme};
        """,
        selector,
    )


def _paint_pixel(driver, selector: str, x_fraction: float, y_fraction: float):
    svg = driver.find_element(By.ID, "map-svg")
    geometry = driver.execute_script(
        """
        const svg = document.querySelector('#map-svg').getBoundingClientRect();
        const item = document.querySelector(arguments[0]).getBoundingClientRect();
        return {x: item.left - svg.left, y: item.top - svg.top,
                width: item.width, height: item.height,
                svgWidth: svg.width, svgHeight: svg.height};
        """,
        selector,
    )
    image = Image.open(io.BytesIO(svg.screenshot_as_png)).convert("RGB")
    x = round((geometry["x"] + geometry["width"] * x_fraction) * image.width / geometry["svgWidth"])
    y = round(
        (geometry["y"] + geometry["height"] * y_fraction) * image.height / geometry["svgHeight"]
    )
    return image.getpixel((x, y))


@pytest.mark.parametrize("engine", ["chromium", "firefox"])
def test_network_map_is_dark_readable_and_complete(browser_panel, engine):
    with _driver(engine) as driver:
        _open_student(driver, browser_panel)

        counts = driver.execute_script(
            """return {nodes: document.querySelectorAll('.map-node').length,
                       edges: document.querySelectorAll('.map-edge').length,
                       labels: document.querySelectorAll('.map-edge-label').length};"""
        )
        assert counts == {
            "nodes": EXPECTED_NODE_COUNT,
            "edges": EXPECTED_EDGE_COUNT,
            "labels": EXPECTED_EDGE_COUNT,
        }

        node = _computed_paint(driver, ".map-node .node-shape")
        primary = _computed_paint(driver, ".map-node .node-label")
        secondary = _computed_paint(driver, ".map-node .node-detail-text")
        protocol_surface = _computed_paint(driver, ".map-edge-label rect")
        protocol_text = _computed_paint(driver, ".map-edge-label text")
        badge_surface = _computed_paint(driver, ".node-kind-pill")
        badge_text = _computed_paint(driver, ".node-kind-text")
        zone_surface = _computed_paint(driver, ".map-zone-rect")

        assert node["fill"] == "rgb(16, 26, 34)"
        assert node["stroke"] == "rgb(113, 135, 151)"
        assert node["filter"] == "none"
        assert node["blend"] == "normal"
        assert node["opacity"] == "1"
        assert protocol_surface["fill"] == "rgb(7, 18, 26)"
        assert zone_surface["opacity"] == "1"
        assert _contrast(_parse_rgb(node["fill"]), _parse_rgb(primary["fill"])) >= 7
        assert _contrast(_parse_rgb(node["fill"]), _parse_rgb(secondary["fill"])) >= 7
        assert (
            _contrast(_parse_rgb(protocol_surface["fill"]), _parse_rgb(protocol_text["fill"])) >= 7
        )
        assert _contrast(_parse_rgb(badge_surface["fill"]), _parse_rgb(badge_text["fill"])) >= 7

        # Screenshot pixels catch post-computed-style transformations such as
        # Chromium's Auto Dark Mode and extensions that repaint inline SVG.
        node_pixel = _paint_pixel(driver, ".map-node .node-shape", 0.82, 0.78)
        protocol_pixel = _paint_pixel(driver, ".map-edge-label rect", 0.22, 0.25)
        assert _luminance(node_pixel) < 0.08, f"pale node paint detected: {node_pixel}"
        assert _luminance(protocol_pixel) < 0.08, f"pale protocol paint detected: {protocol_pixel}"

        artifact_dir = os.environ.get("OT_RANGE_BROWSER_ARTIFACT_DIR")
        if artifact_dir:
            output = Path(artifact_dir)
            output.mkdir(parents=True, exist_ok=True)
            driver.find_element(By.ID, "map-svg").screenshot(
                str(output / f"networkmap-{engine}.png")
            )


@pytest.mark.parametrize("engine", ["chromium", "firefox"])
def test_network_map_interactions_and_overlay_gating(browser_panel, engine):
    with _driver(engine) as driver:
        session = _open_student(driver, browser_panel)
        select = Select(driver.find_element(By.ID, "map-scenario-select"))

        select.select_by_value("S01")
        warning = driver.find_element(By.ID, "map-overlay-locked-note")
        assert warning.get_attribute("hidden") is None
        assert not driver.find_elements(By.CSS_SELECTOR, ".map-edge.attack-path")

        select.select_by_value("")
        assert warning.get_attribute("hidden") == "true"
        assert driver.find_element(By.ID, "map-overlay-summary").get_attribute("hidden") == "true"

        response = session.patch(
            f"{browser_panel}/api/training/S03",
            json={"mode": "guided", "start": True},
            timeout=10,
        )
        response.raise_for_status()
        driver.refresh()
        WebDriverWait(driver, 20).until(
            lambda current: (
                len(current.find_elements(By.CSS_SELECTOR, ".map-node")) == EXPECTED_NODE_COUNT
            )
        )
        Select(driver.find_element(By.ID, "map-scenario-select")).select_by_value("S03")
        WebDriverWait(driver, 5).until(
            lambda current: len(current.find_elements(By.CSS_SELECTOR, ".map-edge.attack-path")) > 0
        )
        assert driver.find_element(By.ID, "map-overlay-summary").get_attribute("hidden") is None
        attack = _computed_paint(driver, ".map-edge.attack-path")
        halo = _computed_paint(driver, ".map-edge-attack-halo")
        detection = _computed_paint(driver, ".map-node.detection .node-shape")
        affected = _computed_paint(driver, ".map-node.affected .node-shape")
        assert attack["stroke"] == "rgb(255, 107, 95)"
        assert attack["fill"] == "none"
        assert halo["fill"] == "none"
        assert detection["fill"] == "rgb(16, 38, 46)"
        assert detection["stroke"] == "rgb(83, 199, 240)"
        assert affected["fill"] == "rgb(41, 23, 25)"
        assert affected["stroke"] == "rgb(255, 107, 95)"

        driver.execute_script(
            "document.querySelector('.map-node[data-node=router]').dispatchEvent("
            "new MouseEvent('click', {bubbles: true}))"
        )
        assert driver.execute_script(
            "const node = document.querySelector('.map-node[data-node=router]');"
            "return node.classList.contains('selected')"
        )
        assert driver.find_element(By.ID, "map-node-detail").get_attribute("hidden") is None
        WebDriverWait(driver, 2).until(
            lambda current: (
                _computed_paint(current, ".map-node.selected .node-shape")["fill"]
                == "rgb(24, 39, 49)"
            )
        )
        selected = _computed_paint(driver, ".map-node.selected .node-shape")
        assert selected["fill"] == "rgb(24, 39, 49)"
        assert selected["stroke"] == "rgb(240, 160, 32)"

        original = driver.execute_script(
            "return document.querySelector('#map-svg').getAttribute('viewBox')"
        )
        driver.execute_script(
            "document.querySelector('#map-svg').dispatchEvent(new WheelEvent('wheel', "
            "{deltaY: -100, bubbles: true, cancelable: true}))"
        )
        assert (
            driver.execute_script(
                "return document.querySelector('#map-svg').getAttribute('viewBox')"
            )
            != original
        )
        driver.find_element(By.ID, "map-reset-view").click()
        assert (
            driver.execute_script(
                "return document.querySelector('#map-svg').getAttribute('viewBox')"
            )
            == "0 0 1100 560"
        )

        driver.execute_script(
            "const svg = document.querySelector('#map-svg');"
            "svg.dispatchEvent(new MouseEvent('mousedown', "
            "{clientX: 500, clientY: 300, bubbles: true}));"
            "window.dispatchEvent(new MouseEvent('mousemove', "
            "{clientX: 550, clientY: 325, bubbles: true}));"
            "window.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}))"
        )
        assert (
            driver.execute_script(
                "return document.querySelector('#map-svg').getAttribute('viewBox')"
            )
            != "0 0 1100 560"
        )
        driver.find_element(By.ID, "map-reset-view").click()

        driver.execute_script(
            "const node = document.querySelector('.map-node[data-node=attacker]');"
            "node.dispatchEvent(new KeyboardEvent('keydown', "
            "{key: 'Enter', bubbles: true, cancelable: true}))"
        )
        assert driver.execute_script(
            "const node = document.querySelector('.map-node[data-node=attacker]');"
            "const selected = node.classList.contains('selected');"
            "return selected && node.getAttribute('aria-pressed') === 'true'"
        )
