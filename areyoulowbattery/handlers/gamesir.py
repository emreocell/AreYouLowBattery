import os
import json
import time
import tempfile
from typing import Optional, Tuple

from areyoulowbattery.models import BatteryStatus, DeviceProfile
from areyoulowbattery.transport import HidrawDevice, HidrawTransport
from areyoulowbattery.handlers.base import BaseProtocolHandler
from areyoulowbattery.logger import logger

CACHE_DIR = "/dev/shm" if os.path.exists("/dev/shm") else tempfile.gettempdir()
CACHE_TTL = 3600  # seconds a cached reading is still considered valid

TARGET_PRODUCT_ID = 0x100B  # GameSir Cyclone 2 XInput dongle (VID: 0x3537)

REPORT_LENGTH = 64  # vendor output frame and input reports are 64 bytes (including report ID)
RESPONSE_TIMEOUT = 1.0
READ_ONLY_FALLBACK_TIMEOUT = 1.0
QUERY_INTERVAL = 60.0  # seconds between active (wake + listen) battery reads

# Vendor INPUT reports streamed after the wake frame:
#   0x12 = controller state, byte 35 = cable/charging flag, byte 36 = battery level (%)
#   0x10 = event report, carries no battery data
BATTERY_REPORT_ID = 0x12
EVENT_REPORT_ID = 0x10
CHARGING_OFFSET = 35
LEVEL_OFFSET = 36
BATTERY_FRAME_MIN_LENGTH = 37

# The ONLY frame this handler is allowed to write (confirmed by hardware capture):
# report ID 0x0F, opcode 0x03, subsystem 0x00 (battery query), zero padding.
# LED/RGB (0x0F 0x03 0x20 ...), register and firmware-loader frames are never sent.
WAKE_PAYLOAD = bytes([0x0F, 0x03]) + bytes(62)


class GamesirHandler(BaseProtocolHandler):
    """
    Protocol handler for the GameSir Cyclone 2 XInput dongle (VID: 0x3537, PID: 0x100B).

    The dongle exposes a vendor HID collection on interface 1 (interface 0 is the
    xpad-owned controller interface, with no hidraw node). It stays silent while idle.

    Protocol (validated by hardware capture + documented by the cyclone2-linux and
    gamesir-linux-tools projects):
      - Wake with a single 64-byte output report: [0x0F, 0x03, 0x00 * 62].
      - The controller then streams INPUT report 0x12 (~200 Hz) plus occasional 0x10
        event reports. Byte 35 = charging/cable flag, byte 36 = battery percentage.
      - Levels above 100 are rejected instead of being clamped or invented.
      - A sleeping controller answers nothing: the last cached reading is returned with
        a "Cached (...)" detail, and no percentage is ever fabricated.
      - Battery is refreshed at most once per `query_interval` (default 60s); cached
        answers in between never touch the device.
    """

    def _cache_path(self, profile: DeviceProfile, dev: Optional[HidrawDevice] = None) -> str:
        safe = "".join(c if c.isalnum() else "_" for c in profile.name.lower())
        vid = profile.match.vendor_id or (dev.vendor_id if dev else 0)
        if profile.match.product_ids:
            pid = profile.match.product_ids[0]
        elif profile.match.product_id is not None:
            pid = profile.match.product_id
        else:
            pid = dev.product_id if dev else 0
        return os.path.join(CACHE_DIR, f"areyoulowbattery_gamesir_{safe}_{vid:04x}_{pid:04x}.json")

    def _read_cache(self, profile: DeviceProfile, dev: Optional[HidrawDevice] = None) -> Optional[dict]:
        try:
            with open(self._cache_path(profile, dev), "r") as f:
                data = json.load(f)
            if time.time() - data.get("timestamp", 0) < CACHE_TTL:
                return data
        except Exception as e:
            logger.debug("GameSir cache read miss for %s: %s", profile.name, e)
        return None

    def _write_cache(self, profile: DeviceProfile, data: dict, dev: Optional[HidrawDevice] = None):
        try:
            data["timestamp"] = time.time()
            with open(self._cache_path(profile, dev), "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.debug("GameSir cache write failed for %s: %s", profile.name, e)

    @staticmethod
    def _parse_battery_report(pkt: bytes) -> Optional[Tuple[int, bool]]:
        """Parses report 0x12: byte 35 = charging/cable flag, byte 36 = level (%)."""
        if len(pkt) < BATTERY_FRAME_MIN_LENGTH or pkt[0] != BATTERY_REPORT_ID:
            return None
        level = pkt[LEVEL_OFFSET]
        if level > 100:
            return None
        return level, bool(pkt[CHARGING_OFFSET])

    def _drain_input(self, transport: HidrawTransport, budget: float = 0.05):
        """Discards stale input reports left over from previous queries."""
        deadline = time.time() + budget
        while time.time() < deadline:
            if not transport.read_input_report(length=REPORT_LENGTH, timeout_sec=0):
                break

    def _listen_for_battery(self, transport: HidrawTransport, timeout: float) -> Tuple[Optional[bytes], Optional[bytes]]:
        """
        Polls the node for up to `timeout` seconds looking for a valid 0x12 battery frame.
        Event reports (0x10) are ignored. Stops at the first valid frame to minimize RF.
        Returns (valid_frame, last_seen_invalid_battery_frame).
        """
        last_seen = None
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            pkt = transport.read_input_report(
                length=REPORT_LENGTH,
                timeout_sec=min(0.02, max(0.01, remaining)),
            )
            if not pkt:
                time.sleep(0.02)
                continue
            if pkt[0] != BATTERY_REPORT_ID:
                # 0x10 event reports (and anything else) carry no battery data.
                if pkt[0] != EVENT_REPORT_ID:
                    last_seen = pkt
                continue
            if self._parse_battery_report(pkt) is not None:
                return pkt, last_seen
            last_seen = pkt
        return None, last_seen

    def _cached_status(self, cached: dict, reason: str) -> BatteryStatus:
        age = int(time.time() - cached["timestamp"])
        return BatteryStatus(
            cached["percentage"],
            is_charging=cached.get("is_charging", False),
            is_connected=True,
            details=f"Cached ({age}s ago, {reason})",
        )

    def _cached_or_none(self, cached: Optional[dict], reason: str) -> Optional[BatteryStatus]:
        if cached and cached.get("percentage") is not None:
            return self._cached_status(cached, reason)
        return None

    def _read_passive(
        self,
        dev: HidrawDevice,
        profile: DeviceProfile,
        timeout: float,
        details_prefix: str = "",
    ) -> Optional[BatteryStatus]:
        """
        Opens the node read-only and listens for an already-streaming 0x12 frame.
        Never writes anything. Returns None when no valid frame showed up.
        """
        try:
            transport = HidrawTransport(dev.dev_path, read_only=True)
            transport.open()
        except (PermissionError, OSError) as e:
            logger.debug("GameSir read-only open failed for %s: %s", dev.dev_path, e)
            return None

        with transport:
            self._drain_input(transport)
            pkt, _ = self._listen_for_battery(transport, timeout)

        if pkt is None:
            return None
        parsed = self._parse_battery_report(pkt)
        if parsed is None:
            return None

        percentage, is_charging = parsed
        raw_hex = pkt.hex()
        self._write_cache(profile, {
            "percentage": percentage,
            "is_charging": is_charging,
            "raw": raw_hex,
        }, dev)
        return BatteryStatus(
            percentage,
            is_charging=is_charging,
            is_connected=True,
            details=f"{details_prefix}Passive report: {raw_hex}",
        )

    def read_battery(self, dev: HidrawDevice, profile: DeviceProfile) -> Optional[BatteryStatus]:
        # PID gate: other PIDs under this vendor (0x0575 HID mode, 0x1053 post-flash)
        # have no battery telemetry. Never write to them.
        if dev.product_id != TARGET_PRODUCT_ID:
            logger.debug(
                "GameSir handler skipped %s (%04x:%04x): not the Cyclone 2 XInput dongle",
                dev.dev_path, dev.vendor_id, dev.product_id,
            )
            return None

        cfg = profile.config
        cached = self._read_cache(profile, dev)

        # Internal throttle: at most one fresh read per `query_interval` (default 60s).
        interval = float(cfg.get("query_interval", QUERY_INTERVAL))
        if cached and cached.get("percentage") is not None:
            age = time.time() - cached["timestamp"]
            if age < interval:
                return self._cached_status(cached, f"throttled, {int(interval)}s interval")

        try:
            transport = HidrawTransport(dev.dev_path, read_only=False)
            transport.open()
        except PermissionError:
            # Without udev access we can still listen passively, but never wake the controller.
            status = self._read_passive(
                dev,
                profile,
                READ_ONLY_FALLBACK_TIMEOUT,
                details_prefix="Read-only (no udev permission): ",
            )
            if status is not None:
                return status
            return self._cached_or_none(cached, "permission denied, install udev rules")
        except OSError as e:
            return self._cached_or_none(cached, f"failed to open device: {e}")

        timeout = float(cfg.get("response_timeout", RESPONSE_TIMEOUT))
        with transport:
            self._drain_input(transport)
            try:
                transport.write_output_report(WAKE_PAYLOAD)
            except OSError as e:
                return self._cached_or_none(cached, f"wake write failed: {e}")
            pkt, last_seen = self._listen_for_battery(transport, timeout)

        if pkt is None:
            if last_seen is not None:
                return self._cached_or_none(
                    cached, f"invalid battery frame ignored ({last_seen.hex()})"
                )
            return self._cached_or_none(cached, "no response from controller (asleep)")

        parsed = self._parse_battery_report(pkt)
        if parsed is None:
            return self._cached_or_none(cached, f"invalid battery frame ignored ({pkt.hex()})")

        percentage, is_charging = parsed
        raw_hex = pkt.hex()
        self._write_cache(profile, {
            "percentage": percentage,
            "is_charging": is_charging,
            "raw": raw_hex,
        }, dev)
        return BatteryStatus(
            percentage,
            is_charging=is_charging,
            is_connected=True,
            details=f"Live report: {raw_hex}",
        )
