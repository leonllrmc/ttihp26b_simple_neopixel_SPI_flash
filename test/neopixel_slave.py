"""
ws2812b_slave.py
=================

A cocotb model of a WS2812B addressable-LED chain acting as the *slave*
(receiver) on the single-wire data line, for testing an RTL *master*
(the DUT) that drives WS2812B pixels.

WS2812B is not a clocked bus -- each bit is self-clocked and encoded
purely as a pulse width on one wire:

                 T0H          T0L                  T1H          T1L
    bit 0:   ----+     +----------------+    bit 1:   ----+          +----+
                 |     |                |                 |          |
                 +-----+                +                 +----------+

    Nominal (WS2812B datasheet):
        T0H ~ 0.35 us   T0L ~ 0.80 us      (bit period ~1.25 us)
        T1H ~ 0.70 us   T1L ~ 0.60 us      (bit period ~1.25 us)
        RES  > 50 us (low)  -- latches the shifted-in colors and resets
                               the bit counter for the next frame.

    Each pixel is 24 bits, MSB-first, sent as three bytes in
    Green-Red-Blue order (WS2812B). Pixels are shifted through the
    chain back-to-back with no gap; a low period longer than the reset
    threshold marks the end of a frame.

This module only *observes* -- WS2812B has no return path, so the
slave never drives the line. Instantiate one `WS2812BSlave` per data
pin you want to monitor.

Typical usage inside a cocotb test
-----------------------------------

    from ws2812b_slave import WS2812BSlave

    slave = WS2812BSlave(dut.led_dout, num_leds=8)

    # ... reset / run the DUT so it shifts out a frame ...

    pixels = await slave.wait_for_frame(timeout_us=500)
    assert pixels == [(0, 255, 0), (0, 0, 255), ...]   # (R, G, B) tuples
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import cocotb
from cocotb.triggers import Event, FallingEdge, First, RisingEdge, Timer, with_timeout
from cocotb.utils import get_sim_time


class WS2812BProtocolError(Exception):
    """Raised when the observed waveform violates the WS2812B protocol
    and the monitor is configured to be strict about it."""


@dataclass
class WS2812BBit:
    """One decoded bit, kept around for debug/timing assertions."""

    value: int          # 0 or 1
    high_ns: float       # measured high (Txx H) time
    low_ns: float        # measured low (Txx L) time, or the reset gap


@dataclass
class WS2812BFrame:
    """One completed frame: the pixels shifted out between two resets."""

    pixels: List[Tuple[int, int, int]] = field(default_factory=list)
    bits: List[WS2812BBit] = field(default_factory=list)
    end_time_ns: float = 0.0


class WS2812BSlave:
    """Passive WS2812B chain model / protocol monitor.

    Parameters
    ----------
    data_signal:
        The DUT pin that drives the WS2812B data line (its "DOUT").
    num_leds:
        If given, a frame is also considered complete once this many
        pixels have been decoded (useful if your DUT doesn't emit a
        reset pulse between back-to-back test frames). If ``None``
        (default) a frame only ends on a reset/latch low pulse.
    color_order:
        Byte order of each pixel on the wire. WS2812B is ``"GRB"``.
        Some clones/variants use ``"RGB"``; SK6812-style parts with a
        white channel can be modeled with e.g. ``"GRBW"``.
    t0h_ns, t0l_ns, t1h_ns, t1l_ns:
        Nominal bit timings in nanoseconds (WS2812B datasheet
        defaults).
    reset_threshold_ns:
        Any low period longer than this is treated as a reset/latch
        rather than a ``T0L``/``T1L`` inter-bit gap. Must sit above
        the worst-case ``T0L``/``T1L`` and below a real minimum reset
        time. Default 9000 ns comfortably clears both.
    strict:
        If True, out-of-tolerance pulse widths and mid-byte/mid-pixel
        resets raise :class:`WS2812BProtocolError`. If False (default)
        they are logged as warnings and decoding does a best-effort
        classification (closest nominal value wins).
    tolerance_ns:
        Allowed +/- deviation from the nominal T0H/T0L/T1H/T1L used
        for the strict-mode timing check.
    auto_start:
        Start monitoring immediately (spawns a background cocotb task).
        Set False if you want to call :meth:`start` yourself later.
    """

    def __init__(
        self,
        data_signal,
        num_leds: Optional[int] = None,
        color_order: str = "GRB",
        t0h_ns: float = 350.0,
        t0l_ns: float = 800.0,
        t1h_ns: float = 700.0,
        t1l_ns: float = 600.0,
        reset_threshold_ns: float = 9_000.0,
        strict: bool = False,
        tolerance_ns: float = 150.0,
        auto_start: bool = True,
        log: Optional[logging.Logger] = None,
    ):
        self.signal = data_signal
        self.num_leds = num_leds
        self.color_order = color_order.upper()
        self.bytes_per_pixel = len(self.color_order)

        self.t0h_ns = t0h_ns
        self.t0l_ns = t0l_ns
        self.t1h_ns = t1h_ns
        self.t1l_ns = t1l_ns
        self.reset_threshold_ns = reset_threshold_ns
        self.strict = strict
        self.tolerance_ns = tolerance_ns
        # Decision boundary between a "0" pulse and a "1" pulse.
        self._high_decision_ns = (self.t0h_ns + self.t1h_ns) / 2.0

        self.log = log or logging.getLogger("cocotb.ws2812b_slave")

        self.frames: List[WS2812BFrame] = []
        self._frame_event = Event()

        self._cur_pixels: List[Tuple[int, int, int]] = []
        self._cur_bits: List[WS2812BBit] = []
        self._bitbuf: List[int] = []

        self._task = None
        if auto_start:
            self.start()

    # ------------------------------------------------------------------
    # control
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Spawn the background decode task (no-op if already running)."""
        if self._task is None:
            self._task = cocotb.start_soon(self._monitor())

    def stop(self) -> None:
        """Kill the background decode task."""
        if self._task is not None:
            self._task.kill()
            self._task = None

    def clear(self) -> None:
        """Discard any captured frames (does not affect in-flight decode)."""
        self.frames.clear()

    # ------------------------------------------------------------------
    # convenience accessors
    # ------------------------------------------------------------------
    @property
    def pixels(self) -> List[Tuple[int, int, int]]:
        """Pixels of the most recently completed frame (``[]`` if none yet)."""
        return self.frames[-1].pixels if self.frames else []

    async def wait_for_frame(self, timeout_us: Optional[float] = None) -> List[Tuple[int, int, int]]:
        """Block until the next frame completes and return its pixels.

        Parameters
        ----------
        timeout_us:
            Optional timeout in microseconds. Raises
            ``cocotb.triggers.SimTimeoutError`` if no frame completes
            in time.
        """
        self._frame_event.clear()
        if timeout_us is None:
            await self._frame_event.wait()
        else:
            await with_timeout(self._frame_event.wait(), timeout_us, "us")
        return self.frames[-1].pixels

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _classify(self, high_ns: float, low_ns: float, check_low: bool = True) -> int:
        bit = 0 if high_ns < self._high_decision_ns else 1
        nom_h = self.t0h_ns if bit == 0 else self.t1h_ns
        nom_l = self.t0l_ns if bit == 0 else self.t1l_ns

        bad_high = abs(high_ns - nom_h) > self.tolerance_ns
        bad_low = check_low and abs(low_ns - nom_l) > self.tolerance_ns

        if bad_high or bad_low:
            msg = (
                f"bit {bit}: high={high_ns:.1f}ns (nominal {nom_h:.0f}ns) "
                f"low={low_ns:.1f}ns (nominal {nom_l:.0f}ns), "
                f"tolerance +/-{self.tolerance_ns:.0f}ns"
            )
            if self.strict:
                raise WS2812BProtocolError(msg)
            self.log.warning("WS2812B timing out of spec: %s", msg)
        return bit

    def _push_bit(self, bit: int, high_ns: float, low_ns: float) -> None:
        self._cur_bits.append(WS2812BBit(bit, high_ns, low_ns))
        self._bitbuf.append(bit)

        bits_per_pixel = 8 * self.bytes_per_pixel
        if len(self._bitbuf) == bits_per_pixel:
            channels = []
            for i in range(self.bytes_per_pixel):
                byte_bits = self._bitbuf[i * 8:(i + 1) * 8]
                value = 0
                for b in byte_bits:
                    value = (value << 1) | b
                channels.append(value)
            self._bitbuf = []

            # map wire order (e.g. G,R,B) -> (R, G, B[, W...])
            ch_map = dict(zip(self.color_order, channels))
            r = ch_map.get("R", 0)
            g = ch_map.get("G", 0)
            b_ = ch_map.get("B", 0)
            if "W" in self.color_order:
                pixel = (r, g, b_, ch_map["W"])
            else:
                pixel = (r, g, b_)
            self._cur_pixels.append(pixel)

            if self.num_leds is not None and len(self._cur_pixels) >= self.num_leds:
                self._finish_frame()

    def _finish_frame(self) -> None:
        if not self._cur_pixels and not self._cur_bits:
            return  # nothing decoded yet -- a stray/idle reset, ignore

        if self._bitbuf:
            msg = (
                f"reset seen mid-pixel: {len(self._bitbuf)} stray bit(s) "
                f"buffered ({self._bitbuf})"
            )
            if self.strict:
                self._bitbuf = []
                raise WS2812BProtocolError(msg)
            self.log.warning(msg)
            self._bitbuf = []

        frame = WS2812BFrame(
            pixels=self._cur_pixels,
            bits=self._cur_bits,
            end_time_ns=get_sim_time("ns"),
        )
        self.frames.append(frame)
        self._cur_pixels = []
        self._cur_bits = []

        self._frame_event.set()

    async def _monitor(self) -> None:
        # Make sure we start out looking at a low/idle line so the very
        # first rising edge we see is a genuine bit start (not us
        # catching a pixel already in progress).
        if self.signal.value == 1:
            await FallingEdge(self.signal)

        # Wait for the very first bit of the very first frame.
        await RisingEdge(self.signal)
        rise_time = get_sim_time("ns")

        while True:
            # 1) This bit's high pulse (T0H / T1H).
            await FallingEdge(self.signal)
            fall_time = get_sim_time("ns")
            high_ns = fall_time - rise_time

            # 2) This same bit's low time (T0L / T1L). If the line is
            #    still low once reset_threshold_ns has elapsed, this low
            #    period is a reset/latch pulse rather than a normal
            #    inter-bit gap. Whichever trigger fires, `now` also marks
            #    the start of the *next* bit's high pulse (when it wasn't
            #    a reset) -- there is no separate rising edge to wait for.
            await First(RisingEdge(self.signal), Timer(self.reset_threshold_ns, "ns"))
            now = get_sim_time("ns")
            low_ns = now - fall_time
            is_reset = self.signal.value == 0  # still low => the Timer fired first

            # A reset-terminated low period isn't a real T0L/T1L, so don't
            # flag it as an out-of-spec inter-bit gap.
            bit = self._classify(high_ns, low_ns, check_low=not is_reset)
            self._push_bit(bit, high_ns, low_ns)

            if is_reset:
                self._finish_frame()
                # Consume the rest of the reset pulse and wait for the
                # next frame's first bit.
                await RisingEdge(self.signal)
                rise_time = get_sim_time("ns")
            else:
                rise_time = now