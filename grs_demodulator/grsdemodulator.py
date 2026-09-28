#
#  grsdemodulator.py
#
#  Copyright The GRS Demodulator contributors.
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

import os

import numpy as np
import zmq

from scipy.signal import lfilter_zi, firwin, lfilter
from grs_demodulator.gmsk import GMSK
from grs_demodulator.timing_sync.mm import MM


class GRSDemodulator:
    """
    Demodulator application.

    Reads IQ samples from a ZMQ SUB socket, recovers the bitstream, and
    publishes the bits on a ZMQ PUB socket.

    ## The IQ envelope (input)

    One message per block, no topic frame, payload is interleaved
    complex64 little-endian (float32 I, float32 Q) -- `cf32_le` in SigMF
    terms. This is what the C/RTL-SDR implementation of grs-iq-rx publishes
    on port 5556.

    No topic frame is deliberate: a plain `recv()` is used here, so a topic
    frame in front would be consumed as the message itself.

    ## The bit envelope (output)

    One message per processing window, no topic frame, payload is ONE BYTE
    PER BIT, each byte either 0x00 or 0x01.

    One byte per bit rather than packed bits, because the next stage --
    grs-syncword-detector -- searches over a `bool*`, which is exactly this
    layout: the consumer casts the payload and runs, with no unpacking step
    and no MSB/LSB ambiguity to get wrong at this boundary. Bit packing
    belongs where byte framing is decided, which is after the sync word is
    found, not here.

    The cost is 8x the bandwidth of packed bits. At 4800 baud that is
    4.8 kB/s, which is nothing next to the 1.9 MB/s of IQ coming in.
    """

    DEMOD_DEFAULT_SAMPLE_RATE = 240000
    GMSK_DEFAULT_BAUD_RATE = 4800
    GMSK_DEFAULT_BT = 0.5

    # How many symbols to accumulate before running the DSP chain. The
    # timing recovery keeps state across windows, so this is a
    # latency-versus-overhead knob, not a correctness one. 2400 symbols is
    # half a second at 4800 baud.
    DEMOD_DEFAULT_WINDOW_SYMBOLS = 2400

    # With no IQ for this long, the buffered partial window is processed.
    DEMOD_IDLE_FLUSH_MS = 300

    # Rastreador do desvio de frequência residual (erro de sintonia, Doppler
    # que a sintonia não corrigiu). Constante de tempo longa, porque ele só
    # aprende com amostras de sinal (ver DC_GATE) e segura o valor no silêncio.
    DEMOD_DEFAULT_DC_TAU_S = 0.2

    # Uma amostra só entra no rastreador se o discriminador der menos que
    # isto, em unidades de desvio nominal (±1 = ±baud/4). Ruído puro dá valores
    # espalhados por ±100 (fase aleatória vezes o ganho); sinal dá ±1 mais o
    # desvio. Sem este portão, cada silêncio entre rajadas puxava a estimativa
    # de volta para zero, e a rajada seguinte chegava sem correção nenhuma.
    DC_GATE = 3.0

    DEMOD_DEFAULT_IQ_SOURCE = "tcp://localhost:5556"
    DEMOD_DEFAULT_BITS_BIND = "tcp://*:5555"

    def __init__(self, env=None):
        """
        Class constructor.

        :param env: Environment to read the configuration from. None reads
            os.environ; an explicit dict is what the tests pass.

        :return: None.
        """
        source = dict(os.environ) if env is None else env

        self._baudrate = int(source.get("GRS_DEMOD_BAUD", self.GMSK_DEFAULT_BAUD_RATE))
        self._fs = int(source.get("GRS_DEMOD_SAMPLE_RATE_HZ", self.DEMOD_DEFAULT_SAMPLE_RATE))
        self._bt = float(source.get("GRS_DEMOD_BT", self.GMSK_DEFAULT_BT))
        self._window_symbols = int(
            source.get("GRS_DEMOD_WINDOW_SYMBOLS", self.DEMOD_DEFAULT_WINDOW_SYMBOLS)
        )

        # The addresses are configuration, not constants. They used to be
        # hardcoded to localhost, which meant that inside a container the
        # demodulator subscribed to ITSELF: it came up healthy, blocked on
        # recv(), and never processed a single sample. A service that looks
        # alive and does nothing is worse than one that fails.
        self._iq_source = source.get("GRS_DEMOD_IQ_SOURCE", self.DEMOD_DEFAULT_IQ_SOURCE)
        self._bits_bind = source.get("GRS_DEMOD_BITS_BIND", self.DEMOD_DEFAULT_BITS_BIND)

        if self._baudrate <= 0:
            raise ValueError("GRS_DEMOD_BAUD must be positive, got " + str(self._baudrate))
        if self._fs <= 0:
            raise ValueError("GRS_DEMOD_SAMPLE_RATE_HZ must be positive, got " + str(self._fs))
        if self._window_symbols <= 0:
            raise ValueError(
                "GRS_DEMOD_WINDOW_SYMBOLS must be positive, got " + str(self._window_symbols)
            )

        self._sps = self._fs / self._baudrate
        if self._sps < 2:
            # Below two samples per symbol the timing recovery has nothing to
            # lock onto. Fail at boot rather than emit noise for a whole pass.
            raise ValueError(
                "sample rate " + str(self._fs) + " gives only " + str(self._sps)
                + " samples per symbol at " + str(self._baudrate) + " baud; need at least 2"
            )

        self._dc_tau_s = float(source.get("GRS_DEMOD_DC_TAU_S", self.DEMOD_DEFAULT_DC_TAU_S))
        if self._dc_tau_s <= 0:
            raise ValueError("GRS_DEMOD_DC_TAU_S must be positive, got " + str(self._dc_tau_s))

        self._mod = GMSK(self._bt, self._baudrate)
        self._mm = MM(self._fs, self._baudrate)

        self._build_lpf_taps(self._fs)
        self._build_streaming_state()

        self._zmq_ctx = None
        self._in_socket = None
        self._out_socket = None
        self._samples_buf = bytearray()

    @property
    def window_bytes(self):
        """
        Size of the processing window, in bytes of the incoming IQ stream.

        symbols -> samples -> bytes, at 8 bytes per complex64 sample.

        This used to read `300 * (fs / bt) * 8 * 8`, which mistook the BT
        product (0.5) for the samples-per-symbol figure. At 48 kHz that asked
        for about 1.8 GB of samples before processing anything -- roughly ten
        thousand times the intended window, so the demodulator accumulated
        forever and never produced a bit.
        """
        samples = self._window_symbols * self._sps

        return int(samples) * 8

    def start(self):
        """
        :return: None.
        """
        self._init_zmq()

    def run(self):
        """
        Main loop: IQ in, bits out.

        :return: The process exit code.
        """
        self._in_socket.connect(self._iq_source)
        self._in_socket.setsockopt_string(zmq.SUBSCRIBE, "")

        print("grs-demodulator: IQ from " + self._iq_source, flush=True)
        print("grs-demodulator: bits on " + self._bits_bind, flush=True)
        print(
            "grs-demodulator: " + str(self._fs) + " S/s, " + str(self._baudrate)
            + " baud, BT " + str(self._bt) + ", " + str(self._sps) + " samples/symbol",
            flush=True,
        )
        print(
            "grs-demodulator: window " + str(self._window_symbols) + " symbols ("
            + str(self.window_bytes) + " bytes)",
            flush=True,
        )

        total_bits = 0

        try:
            while True:
                total_bits += self._step()
        except KeyboardInterrupt:
            print("grs-demodulator: interrupted after " + str(total_bits) + " bits", flush=True)
            return 0
        finally:
            self.close()

    def _step(self):
        """
        One receive, and the processing it triggers.

        A window is processed when it fills. If the stream PAUSES instead
        (end of a replay, the source being switched), whatever is buffered is
        processed at the idle timeout -- otherwise the last partial window,
        and the frame inside it, would sit there until the next source
        started. Processing a short chunk is safe because every stage keeps
        its state across calls.

        :return: How many bits were published.
        """
        try:
            self._samples_buf.extend(self._in_socket.recv())
        except zmq.Again:
            return self._drain() if len(self._samples_buf) >= 8 else 0

        if len(self._samples_buf) < self.window_bytes:
            return 0

        return self._drain()

    def _drain(self):
        # Only whole samples: a window boundary must never split a complex64
        # in half, or every subsequent sample is shifted by four bytes and the
        # stream turns to noise silently.
        usable = (len(self._samples_buf) // 8) * 8
        chunk = bytes(self._samples_buf[:usable])
        del self._samples_buf[:usable]

        bits = self._process_samples(chunk)
        if bits:
            self._publish_bits(bits)

        return len(bits)

    def _publish_bits(self, bits):
        """
        Publishes a window of bits, one byte per bit.

        :param bits: Sequence of 0/1 integers.

        :return: None.
        """
        self._out_socket.send(bytes(bytearray(bits)))

    def _init_zmq(self):
        """
        :return: None.
        """
        self._zmq_ctx = zmq.Context()

        self._in_socket = self._zmq_ctx.socket(zmq.SUB)

        # The IQ stream is the fast side: 1.9 MB/s at 240 kS/s. A high water
        # mark and a big receive buffer keep a slow window from dropping
        # blocks mid-pass.
        self._in_socket.setsockopt(zmq.RCVHWM, 1000000)
        self._in_socket.setsockopt(zmq.RCVBUF, 2097152)
        # Idle timeout: see _step().
        self._in_socket.setsockopt(zmq.RCVTIMEO, self.DEMOD_IDLE_FLUSH_MS)

        self._out_socket = self._zmq_ctx.socket(zmq.PUB)
        self._out_socket.bind(self._bits_bind)

    def close(self):
        """
        :return: None.
        """
        for socket in (self._in_socket, self._out_socket):
            if socket is not None:
                socket.close()

        if self._zmq_ctx is not None:
            self._zmq_ctx.term()

        self._in_socket = None
        self._out_socket = None
        self._zmq_ctx = None

    def _process_samples(self, buf):
        """
        Process incoming samples.

        :param buf: Buffer with interleaved float32 IQ samples.
        :type: bytes

        :return: The demodulated bits.
        :rtype: list
        """
        samples = np.frombuffer(buf, dtype=np.complex64)

        # Cada etapa guarda o próprio estado entre janelas. A janela é só o
        # tamanho do lote em que o IQ chega; se dividir o fluxo em janelas
        # mudasse o resultado, todo pacote que atravessasse uma fronteira
        # sairia corrompido — e saía, um em cada quatro, antes disto.
        filtered_samples, self._zi = lfilter(self._taps, [1.0], samples, zi=self._zi)

        # Discriminador de frequência. A primeira diferença de fase da janela
        # usa a última amostra da janela anterior, e não zero.
        previous = np.concatenate(([self._last_sample], filtered_samples[:-1]))
        self._last_sample = filtered_samples[-1]
        frequency = self._disc_gain * np.angle(filtered_samples * np.conj(previous))

        # Desvio de frequência (erro de sintonia, Doppler residual) vira nível
        # DC aqui. Antes se subtraía a MÉDIA DA JANELA: num sinal em rajadas a
        # média mistura rajada e silêncio, fica longe do desvio real, e dá um
        # degrau a cada fronteira. Aqui é uma média exponencial PONDERADA pelo
        # portão: soma(peso·x) / soma(peso), as duas contínuas entre janelas.
        # Mede-se tolerância de ~500 Hz; acima de ~1 kHz, quem tem de corrigir
        # é a sintonia (Station Manager -> sintetizador), não o demodulador.
        gate = (np.abs(frequency) < self.DC_GATE).astype(np.float64)
        weighted, self._dc_num_zi = lfilter(
            self._dc_b, self._dc_a, frequency * gate, zi=self._dc_num_zi
        )
        weight, self._dc_den_zi = lfilter(self._dc_b, self._dc_a, gate, zi=self._dc_den_zi)
        frequency = frequency - weighted / np.maximum(weight, 1e-9)

        # Filtro casado com estado, como o passa-baixa acima. A convolução
        # "same" de antes truncava as bordas de cada janela.
        soft_symbols, self._mf_zi = lfilter(self._mf_taps, [1.0], frequency, zi=self._mf_zi)

        bits = self._mm.decode_stream(soft_symbols)

        return list(map(int, bits))

    def _build_streaming_state(self):
        """
        Taps and initial state of every stage that must survive a window
        boundary: discriminator, DC tracker, matched filter.
        """
        # Mesmo ganho de GMSK.demodulate: desvio de ±baud/4 -> ±1.
        self._disc_gain = self._fs / (0.5 * np.pi * self._baudrate)
        self._last_sample = np.complex64(0)

        alpha = 1.0 - np.exp(-1.0 / (self._dc_tau_s * self._fs))
        self._dc_b = np.array([alpha])
        self._dc_a = np.array([1.0, -(1.0 - alpha)])
        self._dc_num_zi = np.zeros(1)
        self._dc_den_zi = np.zeros(1)

        self._mf_taps = self._mod._gaussian_matched_filter(
            1 / self._baudrate, int(self._sps), 1
        )
        self._mf_zi = np.zeros(len(self._mf_taps) - 1)

    def _build_lpf_taps(self, fs, window="hamming", beta=6.76):
        """
        Calculate low pass FIR filter taps and initial state for continuous
        processing.
        """
        cutoff = 1.2 * self._baudrate
        transition = 2.5 * cutoff

        num_taps = int(np.ceil(4 * fs / transition))
        if num_taps % 2 == 0:
            num_taps += 1  # Make odd

        self._taps = firwin(
            num_taps,
            cutoff,
            window=(window, beta) if window == "kaiser" else window,
            fs=fs,
            pass_zero="lowpass",
        )

        self._zi = lfilter_zi(self._taps, [1.0]) * 0.0
