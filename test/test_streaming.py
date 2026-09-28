#
#  test_streaming.py
#
#  Copyright The GRS Demodulator Contributors.
#
#  This file is part of GRS Demodulator.
#
#  GRS Demodulator is free software; you can redistribute it
#  and/or modify it under the terms of the GNU General Public License as
#  published by the Free Software Foundation, either version 3 of the
#  License, or (at your option) any later version.
#
#  GRS Demodulator is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public
#  License along with GRS Demodulator; if not, see <http://www.gnu.org/licenses/>.
#
#

"""
Regression tests for the two defects that corrupted one packet in four.

1. Every DSP stage restarted at each processing window. The timing recovery
   discarded where the last symbol step landed past the end of the window, so
   the next window resumed up to a whole symbol off -- a repeated or dropped
   bit in any frame that crossed a boundary.

2. The Mueller & Muller loop gain was written per SYMBOL but applied to `mu`
   counted in SAMPLES, 50x too weak at 50 samples per symbol. The loop did not
   track at all; it only worked when the symbols happened to start on the
   sample grid, as they do in a simulator and never do on air.

Both were invisible to the existing tests, which demodulate one window with
the symbols aligned on sample zero.
"""

import numpy as np
import pytest

from grs_demodulator.grsdemodulator import GRSDemodulator
from grs_demodulator.timing_sync.mm import MM

PREAMBLE = [0xAA] * 32
SYNCWORD = [0x5D, 0xE6, 0x2A, 0x7E]
PAYLOAD = list(range(64))


def bits_of(data):
    return [(byte >> (7 - i)) & 1 for byte in data for i in range(8)]


def contains(haystack, needle):
    text = "".join(map(str, haystack))
    return "".join(map(str, needle)) in text


def burst(demod, symbol_offset):
    """Lead-in silence, `symbol_offset` extra samples, one frame, tail silence."""
    frame, _, _ = demod._mod.modulate(PREAMBLE + SYNCWORD + PAYLOAD, L=int(demod._sps))
    lead = np.zeros(5000 + symbol_offset, dtype=np.complex64)
    tail = np.zeros(5000, dtype=np.complex64)

    return np.concatenate((lead, frame.astype(np.complex64), tail))


def demodulate_in_windows(demod, samples, window):
    bits = []
    for start in range(0, len(samples), window):
        bits.extend(demod._process_samples(samples[start : start + window].tobytes()))

    return bits


def test_timing_recovery_is_independent_of_how_the_stream_is_split():
    """Same soft symbols, one call versus irregular chunks: identical bits."""
    rng = np.random.default_rng(7)
    symbols = np.repeat(rng.choice([-1.0, 1.0], size=600), 50)
    soft = symbols + 0.05 * rng.standard_normal(len(symbols))

    whole = MM(240000, 4800).decode_stream(soft)

    chunked_mm = MM(240000, 4800)
    chunked = []
    start = 0
    for size in [997, 12345, 3, 50, 49, 4000, 7777] * 10:
        if start >= len(soft):
            break
        chunked.extend(chunked_mm.decode_stream(soft[start : start + size]))
        start += size

    assert chunked == whole


@pytest.mark.parametrize("symbol_offset", [0, 13, 25, 37])
def test_frame_survives_any_symbol_phase_and_window_boundaries(symbol_offset):
    """
    Windows of 7000 samples against a 40000-sample frame: the frame crosses
    five window boundaries. Before the fix, offsets that put the sampling
    instant near a symbol edge lost the frame outright.
    """
    demod = GRSDemodulator(env={})
    samples = burst(demod, symbol_offset)

    bits = demodulate_in_windows(demod, samples, window=7000)

    assert contains(bits, bits_of(SYNCWORD + PAYLOAD))


def test_loop_gain_is_scaled_to_samples_per_symbol():
    """0.001 per symbol, expressed in samples: 0.05 at 50 samples/symbol."""
    assert MM(240000, 4800)._gain == pytest.approx(0.05)
    assert MM(48000, 1200)._gain == pytest.approx(0.04)
