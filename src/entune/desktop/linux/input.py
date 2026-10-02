"""Linux: the keyboard as the kernel sees it, below X11 and Wayland alike.

Shortcuts are read from the keyboards in /dev/input, and the paste keystroke is sent
through a virtual keyboard made with /dev/uinput. Both work the same on X11 and on
Wayland, where an app cannot see or send keys for other apps. Both need the one-time
setup Entune shows (the `input` group, and a udev rule for /dev/uinput): Linux's
counterpart of the Mac's Input Monitoring and Accessibility.

Plain ioctls and struct packing; the `evdev` package would need a compiler to install.
"""

from __future__ import annotations

import sys

assert sys.platform != "win32"  # POSIX ioctls; type checkers skip the rest on Windows

import fcntl  # noqa: E402
import os  # noqa: E402
import select  # noqa: E402
import struct  # noqa: E402
import time  # noqa: E402
from collections.abc import Callable, Iterator  # noqa: E402
from pathlib import Path  # noqa: E402

INPUT_DIR = Path("/dev/input")
UINPUT = Path("/dev/uinput")
VIRTUAL_NAME = b"Entune virtual keyboard"  # our own keys; the listener skips this device

EV_SYN, EV_KEY = 0x00, 0x01
SYN_REPORT = 0
KEY_ESC, KEY_A, KEY_Z, KEY_SPACE = 1, 30, 44, 57
KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_LEFTALT, KEY_LEFTMETA = 29, 42, 56, 125
KEY_RIGHTCTRL, KEY_RIGHTSHIFT, KEY_RIGHTALT, KEY_RIGHTMETA = 97, 54, 100, 126
KEY_INSERT = 110
MODIFIERS = frozenset(
    {KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_LEFTALT, KEY_LEFTMETA}
    | {KEY_RIGHTCTRL, KEY_RIGHTSHIFT, KEY_RIGHTALT, KEY_RIGHTMETA}
)
KEY_MAX = 0x2FF

# struct input_event: a timeval, then type, code and value.
EVENT = struct.Struct("llHHi")


def _ioc(direction: int, kind: str, number: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(kind) << 8) | number


def _eviocgbit(event_type: int, size: int) -> int:
    return _ioc(2, "E", 0x20 + event_type, size)


def _eviocgname(size: int) -> int:
    return _ioc(2, "E", 0x06, size)


def _eviocgkey(size: int) -> int:
    return _ioc(2, "E", 0x18, size)


def keys_down(fd: int) -> set[int]:
    """The keys the kernel says are down on this device now."""
    bits = bytearray((KEY_MAX + 8) // 8)
    fcntl.ioctl(fd, _eviocgkey(len(bits)), bits)
    return {code for code in range(KEY_MAX + 1) if bits[code // 8] >> (code % 8) & 1}


UI_SET_EVBIT = _ioc(1, "U", 100, 4)
UI_SET_KEYBIT = _ioc(1, "U", 101, 4)
UI_DEV_SETUP = _ioc(1, "U", 3, 92)  # struct uinput_setup: input_id, name[80], ff_effects_max
UI_DEV_CREATE = _ioc(0, "U", 1, 0)
UI_DEV_DESTROY = _ioc(0, "U", 2, 0)


def event_devices() -> list[Path]:
    return sorted(INPUT_DIR.glob("event*"), key=lambda p: int(p.name[5:] or 0))


def _keys(fd: int) -> set[int]:
    bits = bytearray((KEY_MAX + 8) // 8)
    fcntl.ioctl(fd, _eviocgbit(EV_KEY, len(bits)), bits)
    return {code for code in range(KEY_MAX + 1) if bits[code // 8] >> (code % 8) & 1}


def _name(fd: int) -> bytes:
    name = bytearray(256)
    fcntl.ioctl(fd, _eviocgname(len(name)), name)
    return bytes(name).split(b"\0", 1)[0]


def is_keyboard(keys: set[int], name: bytes) -> bool:
    """A device with letter keys or modifiers; not a mouse, not our virtual keyboard."""
    if name == VIRTUAL_NAME:
        return False
    return bool(keys & set(range(KEY_A, KEY_Z + 1)) or keys & MODIFIERS)


def open_keyboard(path: Path) -> int | None:
    """An open, non-blocking descriptor, or None for another kind of device.

    PermissionError propagates: it means the setup has not been done (or the session
    started before it, and needs a new login).
    """
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if is_keyboard(_keys(fd), _name(fd)):
            return fd
    except OSError:
        pass
    os.close(fd)
    return None


def can_read_keyboards() -> bool:
    """Whether at least one keyboard can be read now."""
    for path in event_devices():
        try:
            fd = open_keyboard(path)
        except OSError:
            continue
        if fd is not None:
            os.close(fd)
            return True
    return False


def can_write_uinput() -> bool:
    return os.access(UINPUT, os.W_OK)


def key_events(data: bytes) -> Iterator[tuple[int, int]]:
    """(code, value) for each key event in a read: value 1 down, 0 up, 2 auto-repeat."""
    for offset in range(0, len(data) - EVENT.size + 1, EVENT.size):
        _sec, _usec, kind, code, value = EVENT.unpack_from(data, offset)
        if kind == EV_KEY:
            yield code, value


class KeyboardReader:
    """Reads every keyboard on its own thread; new and unplugged keyboards are noticed."""

    RESCAN_SECONDS = 2.0

    def __init__(self, on_key: Callable[[int, bool], None]) -> None:
        self._on_key = on_key
        self._devices: dict[Path, int] = {}
        self._stopped = False
        self._wake_r, self._wake_w = os.pipe()

    def run(self) -> None:
        last_scan = 0.0
        try:
            while not self._stopped:
                if time.monotonic() - last_scan >= self.RESCAN_SECONDS:
                    self._scan()
                    last_scan = time.monotonic()
                ready, _, _ = select.select([self._wake_r, *self._devices.values()], [], [], 1.0)
                for path, fd in list(self._devices.items()):
                    if fd in ready:
                        self._read(path, fd)
        finally:
            for fd in self._devices.values():
                os.close(fd)
            self._devices.clear()
            os.close(self._wake_r)
            os.close(self._wake_w)

    def stop(self) -> None:
        self._stopped = True
        os.write(self._wake_w, b"x")

    def down(self) -> set[int]:
        """Every key down now on the keyboards being read, asked of the kernel: events
        can be dropped and a keyboard can go away mid-press, so no cache is trusted."""
        codes: set[int] = set()
        for fd in list(self._devices.values()):
            try:
                codes |= keys_down(fd)
            except OSError:  # unplugged, or closed by the reading thread meanwhile
                continue
        return codes

    def _scan(self) -> None:
        for path in event_devices():
            if path in self._devices:
                continue
            try:
                fd = open_keyboard(path)
            except OSError:
                continue
            if fd is not None:
                self._devices[path] = fd

    def _read(self, path: Path, fd: int) -> None:
        try:
            data = os.read(fd, EVENT.size * 64)
        except BlockingIOError:
            return
        except OSError:  # unplugged
            os.close(self._devices.pop(path))
            return
        for code, value in key_events(data):
            if value in (0, 1):
                self._on_key(code, value == 1)


def setup_bytes(name: bytes) -> bytes:
    """struct uinput_setup: a USB-like id, the name, no force feedback."""
    return struct.pack("HHHH80sI", 0x03, 0x1234, 0x5678, 1, name, 0)


def event_bytes(kind: int, code: int, value: int) -> bytes:
    return EVENT.pack(0, 0, kind, code, value)


class VirtualKeyboard:
    """A keyboard made through /dev/uinput, kept for the app's lifetime once created."""

    # Every ordinary key, as a real keyboard has: udev classes a device with only a few
    # keys as a generic key device, which libinput, and so the desktop, can ignore.
    KEYS = range(KEY_ESC, 249)  # up to KEY_MICMUTE

    def __init__(self) -> None:
        self._fd: int | None = None

    def _open(self) -> int:
        if self._fd is None:
            fd = os.open(UINPUT, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            try:
                fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
                for key in self.KEYS:
                    fcntl.ioctl(fd, UI_SET_KEYBIT, key)
                fcntl.ioctl(fd, UI_DEV_SETUP, setup_bytes(VIRTUAL_NAME))
                fcntl.ioctl(fd, UI_DEV_CREATE)
            except OSError:
                os.close(fd)
                raise
            self._fd = fd
            time.sleep(0.3)  # the desktop needs a moment to adopt a new keyboard
        return self._fd

    def press(self, *keys: int) -> None:
        """Press the keys in order, then release them in reverse: one chord."""
        fd = self._open()
        sync = event_bytes(EV_SYN, SYN_REPORT, 0)
        for key in keys:
            os.write(fd, event_bytes(EV_KEY, key, 1) + sync)
            time.sleep(0.01)  # one report per key, as a real keyboard sends them
        for key in reversed(keys):
            os.write(fd, event_bytes(EV_KEY, key, 0) + sync)
            time.sleep(0.01)

    def close(self) -> None:
        if self._fd is not None:
            try:
                fcntl.ioctl(self._fd, UI_DEV_DESTROY)
            finally:
                os.close(self._fd)
                self._fd = None
