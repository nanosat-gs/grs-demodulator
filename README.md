<h1 align="center">
    GRS Demodulator
    <br>
</h1>

<h4 align="center">GMSK Demodulator of the SpaceLab's Ground Station.</h4>

<p align="center">
    <a href="https://github.com/spacelab-ufsc/grs-demodulator">
        <img src="https://img.shields.io/badge/status-development-green?style=for-the-badge">
    </a>
    <a href="https://github.com/spacelab-ufsc/grs-demodulator/releases">
        <img alt="GitHub commits since latest release (by date)" src="https://img.shields.io/github/commits-since/spacelab-ufsc/grs-demodulator/latest?style=for-the-badge">
    </a>
    <a href="https://github.com/spacelab-ufsc/grs-demodulator/blob/main/LICENSE">
        <img src="https://img.shields.io/badge/license-GPL3-yellow?style=for-the-badge">
    </a>
</p>

<p align="center">
    <a href="#overview">Overview</a> •
    <a href="#dependencies">Dependencies</a> •
    <a href="#installing">Installing</a> •
    <a href="#running-as-a-service-station-branch">Running</a> •
    <a href="#documentation">Documentation</a> •
    <a href="#license">License</a>
</p>

## Overview

The GRS Demodulator is a GMSK (Gaussian Minimum Shift Keying) demodulator for SpaceLab's Ground Station signal processing pipeline. It sits between the IQ Receiver and the downstream syncword detector and decoder, receiving a continuous stream of complex IQ samples and outputting a recovered bitstream.

The demodulation pipeline consists of a frequency discriminator, a Gaussian matched filter, symbol timing recovery, and hard-decision thresholding. Both modulation and demodulation are implemented, allowing the GMSK module to be used standalone for testing and simulation.

### Demodulation Pipeline

1. **Frequency Discriminator**: Extracts instantaneous frequency deviations from the phase derivative of the complex IQ samples.
   - **Residual offset tracking** (`station` branch): a slow tracker removes the leftover frequency offset (tuning error, Doppler the tuning did not correct). It only learns from signal samples and holds its value during the silence between bursts.
2. **Gaussian Matched Filter**: Applies a Gaussian filter matched to the BT product to reduce inter-symbol interference.
3. **Normalization**: Centers and scales the filtered signal for reliable symbol decisions.
4. **Timing Recovery**: Synchronizes the sampling instants to the symbol boundaries (Mueller & Müller).
5. **Hard Decision**: Thresholds the recovered soft symbols into a binary bitstream.

## Dependencies

* [numpy](https://pypi.org/project/numpy/) (>= 2.3.5)
* [scipy](https://pypi.org/project/scipy/) (>= 1.15.2)
* [pyzmq](https://pypi.org/project/pyzmq/) (>= 25.1.1)

### Installation on Ubuntu

```
sudo apt install python3 python3-numpy python3-scipy python3-zmq
```

### Installation on Fedora

```
sudo dnf install python3 python3-numpy python3-scipy python3-zmq
```

### Installation via pip

```
pip install -r requirements.txt
```

> **Note:** `matplotlib` is required only for running the GMSK test script (`test_gmsk.py`), which plots a visual comparison of transmitted and demodulated bits. It is not needed for normal operation.

## Installing

```
python setup.py install
```

## Running as a service (`station` branch)

This is the `nanosat-gs` fork. The `station` branch (based on upstream
`fix/demod`, the ref that actually runs) turns the demodulator into a
streaming ZMQ service used by the SpaceLab ground station
([nanosat-gs/grs-station](https://github.com/nanosat-gs/grs-station)):

```
python -m grs_demodulator
```

`SIGTERM` (`docker stop`) is handled like `Ctrl+C`. An invalid configuration
aborts the boot with the full message instead of demodulating noise.

### ZMQ interface

| Direction | Default | Envelope |
|---|---|---|
| IQ in (SUB) | `tcp://localhost:5556` | One message per block, **no topic frame**, interleaved `cf32_le` (float32 I, float32 Q) — what `grs-iq-rx` publishes |
| Bits out (PUB) | `tcp://*:5555` | One message per processing window, **no topic frame**, **one byte per bit** (`0x00`/`0x01`) — what `grs-syncword-detector` consumes |

One byte per bit, rather than packed bits, because the syncword detector
searches over a `bool*`: no unpacking step and no MSB/LSB ambiguity at this
boundary.

### Configuration (environment variables)

| Variable | Default | Description |
|---|---|---|
| `GRS_DEMOD_IQ_SOURCE` | `tcp://localhost:5556` | Where to subscribe to IQ |
| `GRS_DEMOD_BITS_BIND` | `tcp://*:5555` | Where to publish bits |
| `GRS_DEMOD_SAMPLE_RATE_HZ` | `240000` | Sample rate of the IQ stream. Must match the source **exactly**: 0.08% off already loses ~65% of the packets |
| `GRS_DEMOD_BAUD` | `4800` | Symbol rate (the ground station uses 1200 for the FS-2 VHF beacon and 4800 for UHF data) |
| `GRS_DEMOD_BT` | `0.5` | Gaussian filter BT product |
| `GRS_DEMOD_WINDOW_SYMBOLS` | `2400` | Symbols per processing window (latency vs. overhead; state is kept across windows) |
| `GRS_DEMOD_DC_TAU_S` | `0.2` | Time constant of the residual offset tracker, in seconds |

At least 2 samples per symbol are required. When the IQ stream stops for
300 ms, the buffered partial window is processed, so the last packet of a
burst is not held back.

### Changes in the `station` branch

- Publishes the demodulated bits, with the addresses as configuration (they
  were hardcoded to `localhost`, so inside a container the demodulator
  subscribed to itself and never processed a sample).
- Streaming state across windows: discriminator, offset tracker, matched
  filter and timing recovery no longer restart at each window — packets were
  corrupted at window boundaries, and the M&M gain was ~50x too weak to track.
- Partial window processed when the IQ stream stops.
- `np.float128` → `np.longdouble` (portability) and installation outside POSIX.
- Plotting tests skipped when `matplotlib` is not installed.

### Tests

```
pip install -r requirements.txt pytest
pytest
```

## Documentation

The documentation of the upstream project is generated using the Sphinx tool, and it is available [here](https://spacelab-ufsc.github.io/grs-demodulator/). It does not cover the changes in the `station` branch, and the Sphinx sources are not in this repository.

### Dependencies

* Sphinx
* sphinx-rtd-theme

### Building the Documentation

```
make html
```

## License

This project is licensed under GPLv3 license.
