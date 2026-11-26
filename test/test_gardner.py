#
#  test_gardner.py
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
Regressão da perda de 7 a 9% dos quadros na bancada RF.

O Mueller & Müller dá erro zero num preâmbulo alternado, em qualquer fase:
só começava a corrigir no syncword. No silêncio ele segura a fase, então uma
rajada que chegava fora da fase da anterior perdia bits isolados do sync, e o
detector (até 1 erro) descartava o quadro. Os testes de antes não viam porque
as rajadas deles começavam todas na mesma fase, e o preâmbulo era de 32 bytes;
o do FS-2 é de 4 (32 símbolos).

Os testes daqui falham com o M&M (GRS_DEMOD_TIMING=mm) e passam com o Gardner.
"""

import numpy as np
import pytest

from grs_demodulator.grsdemodulator import GRSDemodulator
from grs_demodulator.timing_sync.gardner import Gardner

FS2_PREAMBLE = [0xAA] * 4   # o que o Si4463 do TTC 2.0 transmite
SYNCWORD = [0x5D, 0xE6, 0x2A, 0x7E]


def bits_of(data):
    return [(byte >> (7 - i)) & 1 for byte in data for i in range(8)]


def find(haystack, needle):
    """Posições onde `needle` aparece inteiro em `haystack`."""
    text = "".join(map(str, haystack))
    pattern = "".join(map(str, needle))
    found, start = [], text.find(pattern)
    while start >= 0:
        found.append(start)
        start = text.find(pattern, start + 1)
    return found


def frame(demod, payload):
    samples, _, _ = demod._mod.modulate(FS2_PREAMBLE + SYNCWORD + payload, L=int(demod._sps))
    return samples.astype(np.complex64)


def demodulate(demod, samples):
    """Em janelas do tamanho das de produção."""
    window = demod.window_bytes // 8
    bits = []
    for start in range(0, len(samples), window):
        bits.extend(demod._process_samples(samples[start : start + window].tobytes()))
    return bits


def with_noise(samples, snr_db, rng):
    sigma = np.sqrt(10 ** (-snr_db / 10) / 2)
    noise = rng.normal(0, sigma, len(samples)) + 1j * rng.normal(0, sigma, len(samples))
    return (samples + noise).astype(np.complex64)


@pytest.mark.parametrize("baud", [4800, 1200])
def test_fs2_preamble_is_enough_from_any_symbol_phase(baud):
    """
    Uma rajada com o preâmbulo de 4 bytes, chegando em cada fase de símbolo
    (inclusive meio símbolo, o equilíbrio instável do detector de fase).

    Antes da rajada, silêncio digital: o laço chega a ela com a fase de
    partida intacta, e a varredura passa por todas as distâncias entre essa
    fase e a dos símbolos. Os 64 bytes têm de sair inteiros, depois de um
    sync sem erro.
    """
    payload = list(range(64))
    expected = bits_of(SYNCWORD + payload)
    sps = 240000 // baud
    lost = []

    for offset in range(0, sps, max(1, sps // 25)):
        demod = GRSDemodulator(env={"GRS_DEMOD_BAUD": str(baud)})
        lead = np.zeros(20 * sps + offset, dtype=np.complex64)
        tail = np.zeros(20 * sps, dtype=np.complex64)
        bits = demodulate(demod, np.concatenate((lead, frame(demod, payload), tail)))
        if not find(bits, expected):
            lost.append(offset)

    assert lost == [], "rajadas perdidas nas fases (amostras): " + str(lost)


def test_bursts_out_of_phase_with_each_other():
    """
    O caso da bancada e do satélite: rajadas em sequência, cada uma chegando
    deslocada de uma fração qualquer de símbolo em relação à anterior (o
    intervalo entre elas não é um número inteiro de símbolos). Com ruído,
    como no ar.
    """
    rng = np.random.default_rng(11)
    demod = GRSDemodulator(env={})
    sps = int(demod._sps)
    payloads = [list(rng.integers(0, 256, 64)) for _ in range(20)]

    parts = [np.zeros(sps * 400, dtype=np.complex64)]
    for payload in payloads:
        parts.append(frame(demod, payload))
        # 0,25 s de silêncio mais uma fração de símbolo
        parts.append(np.zeros(sps * 1200 + int(rng.integers(0, sps)), dtype=np.complex64))
    samples = with_noise(np.concatenate(parts), snr_db=20, rng=rng)

    bits = demodulate(demod, samples)

    missing = [k for k, payload in enumerate(payloads)
               if not find(bits, bits_of(SYNCWORD + payload))]
    assert missing == []


def test_timing_recovery_is_independent_of_how_the_stream_is_split():
    """Mesmas amostras, um bloco só ou blocos irregulares: bits idênticos."""
    rng = np.random.default_rng(7)
    symbols = np.repeat(rng.choice([-1.0, 1.0], size=600), 50)
    soft = np.convolve(symbols, np.ones(20) / 20, mode="same") + 0.05 * rng.standard_normal(len(symbols))

    whole = Gardner(240000, 4800).decode_stream(soft)

    chunked_gardner = Gardner(240000, 4800)
    chunked = []
    start = 0
    for size in [997, 12345, 3, 50, 49, 1, 0, 4000, 7777] * 10:
        if start >= len(soft):
            break
        chunked.extend(chunked_gardner.decode_stream(soft[start : start + size]))
        start += size

    assert chunked == whole


def test_noise_cannot_push_the_loop_more_than_its_gain():
    """
    O erro normalizado fica entre -1 e 1, então nem o ruído do discriminador
    no silêncio (centenas de vezes a amplitude do sinal) desloca a amostragem
    mais que o ganho por símbolo: nada de saltos de vários símbolos, nem de
    símbolos decididos em dobro.
    """
    rng = np.random.default_rng(3)
    soft = 300.0 * rng.standard_normal(500_000)
    gardner = Gardner(240000, 4800)
    gain = gardner._gain

    bits = gardner.decode_stream(soft)

    assert len(soft) / (50 + gain) - 2 <= len(bits) <= len(soft) / (50 - gain) + 2


def test_gain_outside_the_stable_range_is_refused():
    with pytest.raises(ValueError):
        Gardner(240000, 4800, gain=0.0)
    with pytest.raises(ValueError):
        Gardner(240000, 4800, gain=0.5)


def test_needs_two_samples_per_symbol():
    with pytest.raises(ValueError):
        Gardner(4800, 4800)
