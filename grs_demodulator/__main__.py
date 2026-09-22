#!/usr/bin/env python

#
#  __main__.py
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

import os
import pathlib
import signal
import sys

sys.path.append(str(pathlib.Path(os.path.realpath(__file__)).parents[1]))

from grs_demodulator.grsdemodulator import GRSDemodulator


def _raise_keyboard_interrupt(signum, frame):
    """
    Turns SIGTERM into the same exception SIGINT already raises.

    Without this the demodulator ignores SIGTERM, which is what `docker stop`
    and `docker compose down` send: the container would sit there for the
    full ten second grace period and then be killed, leaving the PUB socket
    to be torn down by the kernel instead of closed.
    """
    del signum, frame

    raise KeyboardInterrupt


def main(args):
    """
    Main function.

    :param args:

    :return: The code uppon termination.
    """
    del args

    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)

    try:
        app = GRSDemodulator()
    except ValueError as error:
        # Bad configuration kills the boot, with the whole message. The
        # failure mode this avoids is coming up on the default sample rate
        # because of a typo in the .env, and demodulating noise for a whole
        # pass without anyone noticing.
        print("grs-demodulator: invalid configuration: " + str(error), file=sys.stderr, flush=True)

        return 1

    app.start()

    return app.run()


if __name__ == '__main__':
    sys.exit(main(sys.argv))
