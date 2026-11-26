# -*- coding: utf-8 -*-

#
#  gardner.py
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

import math

import numpy as np

# Correção por símbolo, em fração de símbolo, para erro normalizado unitário.
# Escolhido medindo (grs-station/tools/bancada_demod.py e as simulações da
# task da bancada RF): com 0,06 o laço sai de meio símbolo de erro em menos
# dos 32 símbolos do preâmbulo do FS-2 (4 bytes), e o jitter em
# rastreamento fica em ~5% do símbolo. 0,03 ainda perdia rajadas a 1200 baud;
# 0,12 já perdia mais com SNR baixo.
GARDNER_DEFAULT_GAIN = 0.06


class Gardner:
    """
    Sincronismo de símbolo de Gardner, para rajadas.

    Troca o Mueller & Müller (timing_sync/mm.py), que é CEGO no preâmbulo:
    o erro do M&M compara alturas de símbolos vizinhos ponderadas pelas
    decisões, e num `1010...` todos os picos têm a mesma altura em qualquer
    fase -- o erro dá exatamente zero. Ele só começava a corrigir no syncword,
    e uma rajada que chegava fora da fase da anterior perdia bits isolados do
    sync. Ao vivo na bancada RF: 5% dos quadros VHF e 9% dos UHF (o Gardner,
    na mesma bancada, 0 de 240 nas duas cadeias).

    O Gardner usa a amostra do MEIO entre dois símbolos:

        e = y(k - 1/2) * (y(k - 1) - y(k))

    Com a fase certa, o meio entre dois símbolos diferentes cai no cruzamento
    por zero e e = 0; adiantado ou atrasado, o sinal de y(k - 1/2) diz para
    que lado corrigir. Ele se alimenta de transições, e o preâmbulo alternado
    é o melhor caso dele, não o pior.

    Três escolhas, cada uma medida:

    - **Erro normalizado pelo próprio par** (dividido por y(k-1/2)^2 +
      (y(k-1) - y(k))^2 / 4). Fica entre -1 e 1 e não depende da amplitude:
      o preâmbulo sai do filtro gaussiano com bem menos amplitude que uma
      sequência de bits iguais, e com normalização fixa o laço ficava fraco
      justo ali. Normalizar pela amplitude média recente era pior: depois de
      um silêncio a média é a do ruído, e o laço levava centenas de símbolos
      para voltar a ter ganho.

    - **Interpolação linear** entre amostras. Sem ela, a amostragem cai na
      grade inteira, e meio símbolo (25 de 50 amostras) é um ponto da grade:
      ali o erro é zero exato, e a rajada que chegava nessa fase atravessava
      o preâmbulo inteiro parada (o equilíbrio instável de todo detector de
      fase simétrico). Com a fase contínua, qualquer desvio dele cresce.

    - **Estado exato entre blocos.** A posição é guardada como índice inteiro
      mais fração em [0, 1), como no M&M daqui: a conta de ponto flutuante é a
      mesma qualquer que seja o corte dos blocos, então dividir o fluxo em
      janelas não muda um bit da saída.
    """

    def __init__(self, samp_rate, baud, gain=GARDNER_DEFAULT_GAIN):
        """
        :param samp_rate: Taxa de amostragem do sinal de entrada, em S/s.
        :param baud: Taxa de símbolos.
        :param gain: Correção por símbolo, em fração de símbolo.
        """
        self._sps = samp_rate / baud
        if self._sps < 2:
            raise ValueError("Gardner precisa de pelo menos 2 amostras por símbolo")
        if not 0 < gain < 0.5:
            raise ValueError("ganho do Gardner fora de (0, 0,5): " + str(gain))

        self._half = int(round(self._sps / 2))
        # Amostras guardadas do bloco anterior: o y(k - 1/2) do primeiro
        # símbolo do bloco seguinte, e a vizinha dele para a interpolação.
        self._history_len = self._half + 1
        self._gain = gain * self._sps   # em amostras
        self.reset()

    def reset(self):
        """
        Volta ao estado inicial.

        :return: None
        """
        self._history = np.zeros(self._history_len)
        self._next = 0      # próximo símbolo, relativo ao início do bloco seguinte
        self._mu = 0.0      # fração de amostra, em [0, 1)
        self._prev = 0.0    # y(k - 1)

    def decode_stream(self, data):
        """
        Decide os símbolos de um bloco do fluxo, continuando do anterior.

        :param data: Amostras do filtro casado (reais).
        :type: list

        :return: Os bits decididos neste bloco.
        :rtype: list
        """
        x = np.concatenate((self._history, np.asarray(data, dtype=np.float64)))
        n = len(x)
        i = self._next + self._history_len
        mu = self._mu
        prev = self._prev
        sps = self._sps
        gain = self._gain
        half = self._half
        bits = []

        while i + 1 < n:
            y = x[i] + mu * (x[i + 1] - x[i])
            j = i - half
            mid = x[j] + mu * (x[j + 1] - x[j])
            diff = prev - y

            # 1e-12: no silêncio digital (zeros), 0/0.
            error = mid * diff / (mid * mid + 0.25 * diff * diff + 1e-12)

            bits.append(1 if y > 0 else 0)
            prev = y

            mu += sps + gain * error
            step = math.floor(mu)
            i += step
            mu -= step

        self._mu = mu
        self._prev = prev
        self._history = x[n - self._history_len:]
        self._next = i - n      # >= -1: o laço para com i + 1 >= n

        return bits
