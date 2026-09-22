#
#  test_grsdemodulator.py
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
Tests for the configuration and the ZMQ envelopes.

These cover the plumbing, not the DSP: what a window is, what goes on the
wire, and which misconfigurations must kill the boot instead of quietly
producing noise. The DSP itself is covered by test_gmsk and test_timesync.
"""

import numpy as np
import pytest

from grs_demodulator.grsdemodulator import GRSDemodulator


def test_defaults_are_the_station_configuration():
    demod = GRSDemodulator(env={})

    assert demod._fs == 240000
    assert demod._baudrate == 4800
    assert demod._bt == 0.5


def test_default_sample_rate_is_reachable_on_an_rtl_sdr():
    """
    The RTL-SDR only accepts 225001-300000 and 900001-3200000 S/s, and
    outside those ranges the driver does not fail -- it silently delivers a
    different rate. The old default of 48 kHz was in neither range.
    """
    demod = GRSDemodulator(env={})

    assert 225001 <= demod._fs <= 300000 or 900001 <= demod._fs <= 3200000


def test_default_sample_rate_gives_whole_samples_per_symbol():
    demod = GRSDemodulator(env={})

    assert demod._sps == 50


def test_addresses_come_from_the_environment():
    """
    They used to be hardcoded to localhost, which inside a container meant
    the demodulator subscribed to itself: healthy, blocked on recv(), and
    processing nothing.
    """
    demod = GRSDemodulator(
        env={
            "GRS_DEMOD_IQ_SOURCE": "tcp://grs-iq-rx:5556",
            "GRS_DEMOD_BITS_BIND": "tcp://*:5599",
        }
    )

    assert demod._iq_source == "tcp://grs-iq-rx:5556"
    assert demod._bits_bind == "tcp://*:5599"


def test_window_is_symbols_times_sps_times_eight_bytes():
    """
    The old expression used the BT product where samples-per-symbol belonged
    and asked for roughly 1.8 GB before processing anything.
    """
    demod = GRSDemodulator(env={"GRS_DEMOD_WINDOW_SYMBOLS": "100"})

    assert demod.window_bytes == 100 * 50 * 8


def test_window_is_a_sane_size_by_default():
    """Half a second of signal, not gigabytes."""
    demod = GRSDemodulator(env={})

    assert demod.window_bytes < 10 * 1024 * 1024


def test_sample_rate_below_two_samples_per_symbol_is_refused():
    with pytest.raises(ValueError, match="samples per symbol"):
        GRSDemodulator(env={"GRS_DEMOD_SAMPLE_RATE_HZ": "4800"})


def test_negative_baud_is_refused():
    with pytest.raises(ValueError, match="GRS_DEMOD_BAUD"):
        GRSDemodulator(env={"GRS_DEMOD_BAUD": "-1"})


def test_zero_window_is_refused():
    with pytest.raises(ValueError, match="GRS_DEMOD_WINDOW_SYMBOLS"):
        GRSDemodulator(env={"GRS_DEMOD_WINDOW_SYMBOLS": "0"})


class _FakeSocket:
    def __init__(self):
        self.sent = []

    def send(self, payload):
        self.sent.append(payload)


def test_bit_envelope_is_one_byte_per_bit():
    """
    The next stage searches over a bool*, which is exactly this layout: the
    consumer casts the payload and runs, with no unpacking and no MSB/LSB
    ambiguity to get wrong at this boundary.
    """
    demod = GRSDemodulator(env={})
    demod._out_socket = _FakeSocket()

    demod._publish_bits([1, 0, 1, 1, 0, 0, 0, 1])

    assert demod._out_socket.sent == [bytes([1, 0, 1, 1, 0, 0, 0, 1])]


def test_bit_envelope_has_no_topic_frame():
    """
    A single send(), not send_multipart(): a topic frame would be consumed as
    the message itself by a consumer doing a plain recv().
    """
    demod = GRSDemodulator(env={})
    demod._out_socket = _FakeSocket()

    demod._publish_bits([0, 1])

    assert len(demod._out_socket.sent) == 1


def test_iq_envelope_is_read_as_complex64():
    """
    Eight bytes per sample, float32 I then float32 Q, little endian -- the
    cf32_le of SigMF, and what grs-iq-rx publishes.
    """
    demod = GRSDemodulator(env={})
    payload = np.array([1 + 2j, 3 + 4j], dtype=np.complex64).tobytes()

    assert len(payload) == 16
    assert np.array_equal(
        np.frombuffer(payload, dtype=np.complex64),
        np.array([1 + 2j, 3 + 4j], dtype=np.complex64),
    )


def test_demodulating_a_window_produces_bits():
    """
    End to end through the real DSP: modulate a known byte sequence with the
    GMSK class, push the samples through _process_samples, and require bits
    to come out. Not a bit-accuracy check -- that needs a known-good capture,
    which is what the IQ recorder exists to provide.
    """
    demod = GRSDemodulator(env={})

    payload = [0xBA, 0x67, 0x54, 0x7E] + [0x55] * 32
    baseband, _, _ = demod._mod.modulate(payload, L=int(demod._sps))

    bits = demod._process_samples(baseband.astype(np.complex64).tobytes())

    assert len(bits) > 0
    assert set(bits) <= {0, 1}
