import os
import json
import time
import tempfile
from typing import Optional, Tuple

from areyoulowbattery.models import BatteryStatus, DeviceProfile
from areyoulowbattery.transport import HidrawDevice, HidrawTransport, enumerate_hidraw
from areyoulowbattery.handlers.base import BaseProtocolHandler
from areyoulowbattery.logger import logger

CACHE_DIR = "/dev/shm" if os.path.exists("/dev/shm") else tempfile.gettempdir()
CACHE_TTL = 3600  # seconds a cached reading is still considered valid

# When the mouse is plugged into a USB cable it re-enumerates as a WIRED YJX-CHIP
# device (0xa8a4:0x2255 "YJX-CHIP USB MOUSE") and its 2.4GHz radio turns off. The
# receiver (0xa8a5:0x2255) then keeps replaying its last RF packet, which usually
# carries a stale "not charging" flag. Seeing this VID on the bus is ground truth.
WIRED_VENDOR_ID = 0xA8A4

REPORT_LENGTH = 65  # hidraw write buffer: 0x00 report ID prefix + 64-byte vendor frame
ACTIVE_RESPONSE_TIMEOUT = 0.5
PASSIVE_LISTEN_TIMEOUT = 4.5
READ_ONLY_FALLBACK_TIMEOUT = 0.5

# The ONLY frame this handler is allowed to write (confirmed by hardware capture).
# It is the battery query for YJX-CHIP 2.4GHz receivers; config/reset commands are never sent.
QUERY_PAYLOAD = (
    bytes([0x00, 0x55, 0x30, 0xA5, 0x0B, 0x2E, 0x01, 0x01, 0x01])
    + bytes(REPORT_LENGTH - 9)
)

QUERY_RESPONSE_PREFIX = b"\xAA\x30"
HEARTBEAT_PREFIX = b"\xAA\xFA\xAE"


class YjxHandler(BaseProtocolHandler):
    """
    Protocol handler for YJX-CHIP 2.4GHz receivers (VID: 0xA8A5).

    Used by Smailwolf RS7, EWEADN G5 and MCHOSE G7 mice. The vendor collection is
    interface 2 (usage page 0xFF01, usage 0x10) and reports carry no report ID.

    Protocol (validated by hardware capture + live probe):
      - Active query (single 65-byte write): [0x00, 0x55, 0x30, 0xA5, 0x0B, 0x2E, 0x01, 0x01, 0x01]
        + zero padding.
      - Response frame starts with AA 30; byte 8 = battery level (%), byte 9 = charging flag.
        Levels above 100 are rejected instead of being clamped or invented.
      - The receiver also PUSHES a heartbeat about every 67s starting with AA FA AE; byte 9
        carries the battery level. It is used as a secondary, read-only source
        (config `enable_passive_listen`, disabled by default).
      - A sleeping mouse answers nothing: the last cached reading is returned with a
        "Cached (...)" detail, and no percentage is ever fabricated.
      - Wired charging: plugged into a USB cable the mouse re-enumerates as
        0xa8a4:0x2255 and its 2.4GHz radio goes silent. Whenever that wired node is
        on the bus, is_charging is forced to True regardless of the stale RF frame.
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
        return os.path.join(CACHE_DIR, f"areyoulowbattery_yjx_{safe}_{vid:04x}_{pid:04x}.json")

    def _read_cache(self, profile: DeviceProfile, dev: Optional[HidrawDevice] = None) -> Optional[dict]:
        try:
            with open(self._cache_path(profile, dev), "r") as f:
                data = json.load(f)
            if time.time() - data.get("timestamp", 0) < CACHE_TTL:
                return data
        except Exception as e:
            logger.debug("YJX cache read miss for %s: %s", profile.name, e)
        return None

    def _write_cache(self, profile: DeviceProfile, data: dict, dev: Optional[HidrawDevice] = None):
        try:
            data["timestamp"] = time.time()
            with open(self._cache_path(profile, dev), "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.debug("YJX cache write failed for %s: %s", profile.name, e)

    @staticmethod
    def _parse_query_response(pkt: bytes) -> Optional[Tuple[int, bool]]:
        """Parses an AA 30 battery response: byte 8 = level, byte 9 = charging flag."""
        if len(pkt) < 10 or not pkt.startswith(QUERY_RESPONSE_PREFIX):
            return None
        level = pkt[8]
        if level > 100:
            return None
        return level, bool(pkt[9])

    @staticmethod
    def _parse_heartbeat(pkt: bytes) -> Optional[Tuple[int, bool]]:
        """Parses an AA FA AE heartbeat: byte 9 = level (charging state is not reported)."""
        if len(pkt) < 10 or not pkt.startswith(HEARTBEAT_PREFIX):
            return None
        level = pkt[9]
        if level > 100:
            return None
        return level, False

    def _parse_frame(self, pkt: bytes) -> Optional[Tuple[int, bool]]:
        if pkt.startswith(QUERY_RESPONSE_PREFIX):
            return self._parse_query_response(pkt)
        if pkt.startswith(HEARTBEAT_PREFIX):
            return self._parse_heartbeat(pkt)
        return None

    def _drain_input(self, transport: HidrawTransport, budget: float = 0.05):
        """Discards stale input reports left over from previous queries."""
        deadline = time.time() + budget
        while time.time() < deadline:
            if not transport.read_input_report(length=REPORT_LENGTH, timeout_sec=0):
                break

    def _listen_for_frame(self, transport: HidrawTransport, timeout: float) -> Tuple[Optional[bytes], Optional[bytes]]:
        """
        Polls the node for up to `timeout` seconds looking for a valid telemetry frame.
        Returns (valid_frame, last_seen_invalid_frame).
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
            if self._parse_frame(pkt) is not None:
                return pkt, last_seen
            if pkt.startswith(QUERY_RESPONSE_PREFIX) or pkt.startswith(HEARTBEAT_PREFIX):
                last_seen = pkt
        return None, last_seen

    def _find_usb_cable_device(self, dev: HidrawDevice, profile: DeviceProfile) -> Optional[HidrawDevice]:
        """
        Returns the hidraw node of the mouse's USB-cable enumeration (0xa8a4), if any.

        While charging over the cable the 2.4GHz radio is off and the mouse answers
        through this node only; it is therefore both the "charging" ground truth and
        the only source of a live, rising percentage during the charge.
        """
        if not dev.dev_path or not os.path.exists(dev.dev_path):
            return None
        target_pids = set(profile.match.product_ids or [])
        if profile.match.product_id is not None:
            target_pids.add(profile.match.product_id)
        if dev.product_id:
            target_pids.add(dev.product_id)

        candidates = [
            hdev for hdev in enumerate_hidraw()
            if hdev.vendor_id == WIRED_VENDOR_ID and (not target_pids or hdev.product_id in target_pids)
        ]
        if not candidates:
            return None
        preferred_iface = (
            profile.match.interface_number if profile.match.interface_number is not None else 2
        )
        for hdev in candidates:
            if hdev.interface_number == preferred_iface:
                return hdev
        return candidates[0]

    def _is_usb_cable_connected(self, dev: HidrawDevice, profile: DeviceProfile) -> bool:
        """
        True when the mouse is physically attached through its USB cable.

        While charging, the mouse enumerates as WIRED_VENDOR_ID (0xa8a4) and stops
        transmitting over 2.4GHz, so the receiver's last RF frame is stale. Detecting
        the wired node is therefore authoritative for "charging".
        """
        return self._find_usb_cable_device(dev, profile) is not None

    def _query_live_frame(self, dev_path: str, timeout: float) -> Optional[bytes]:
        """
        Opens `dev_path` read/write, drains stale input, sends the documented battery
        query and returns the first valid telemetry frame (or None).

        Any permission/OS error is swallowed so the caller can fall back to another
        node: the wired node has no udev rule on many systems.
        """
        try:
            transport = HidrawTransport(dev_path, read_only=False)
            transport.open()
        except (PermissionError, OSError) as e:
            logger.debug("YJX query open failed for %s: %s", dev_path, e)
            return None

        with transport:
            self._drain_input(transport)
            try:
                transport.write_output_report(QUERY_PAYLOAD)
            except OSError as e:
                logger.debug("YJX query write failed for %s: %s", dev_path, e)
                return None
            pkt, _ = self._listen_for_frame(transport, timeout)

        if pkt is None or self._parse_frame(pkt) is None:
            return None
        return pkt

    def _cached_or_none(
        self,
        cached: Optional[dict],
        reason: str,
        cable_connected: bool = False,
    ) -> Optional[BatteryStatus]:
        if cached and cached.get("percentage") is not None:
            age = int(time.time() - cached["timestamp"])
            return BatteryStatus(
                cached["percentage"],
                is_charging=True if cable_connected else cached.get("is_charging", False),
                is_connected=True,
                details=f"Cached ({age}s ago, {reason})",
            )
        return None

    def _read_passive(
        self,
        dev: HidrawDevice,
        profile: DeviceProfile,
        timeout: float,
        details_prefix: str = "",
    ) -> Optional[BatteryStatus]:
        """
        Opens the node read-only and listens for a pushed frame (AA 30 or AA FA AE).
        Never writes anything. Returns None when no valid frame showed up.
        """
        try:
            transport = HidrawTransport(dev.dev_path, read_only=True)
            transport.open()
        except (PermissionError, OSError) as e:
            logger.debug("YJX read-only open failed for %s: %s", dev.dev_path, e)
            return None

        with transport:
            self._drain_input(transport)
            pkt, _ = self._listen_for_frame(transport, timeout)

        if pkt is None:
            return None
        parsed = self._parse_frame(pkt)
        if parsed is None:
            return None

        percentage, is_charging = parsed
        cable_connected = self._is_usb_cable_connected(dev, profile)
        is_charging = is_charging or cable_connected
        raw_hex = pkt.hex()
        source = "Passive heartbeat" if pkt.startswith(HEARTBEAT_PREFIX) else "Passive report"
        self._write_cache(profile, {
            "percentage": percentage,
            "is_charging": is_charging,
            "raw": raw_hex,
        }, dev)
        return BatteryStatus(
            percentage,
            is_charging=is_charging,
            is_connected=True,
            details=f"{details_prefix}{source}: {raw_hex}",
        )

    def read_battery(self, dev: HidrawDevice, profile: DeviceProfile) -> Optional[BatteryStatus]:
        cfg = profile.config
        cached = self._read_cache(profile, dev)
        wired_dev = self._find_usb_cable_device(dev, profile)
        cable_connected = wired_dev is not None

        # On the cable the 2.4GHz radio is off and the receiver keeps replaying its last
        # RF frame (frozen percentage). The wired node answers the same query with the
        # live level, so it is queried first and the receiver only serves as a fallback.
        if wired_dev is not None and wired_dev.interface_number in (
            None,
            2,
            profile.match.interface_number,
        ):
            timeout = float(cfg.get("response_timeout", ACTIVE_RESPONSE_TIMEOUT))
            wired_pkt = self._query_live_frame(wired_dev.dev_path, timeout)
            if wired_pkt is not None:
                percentage, _ = self._parse_frame(wired_pkt)
                raw_hex = wired_pkt.hex()
                self._write_cache(profile, {
                    "percentage": percentage,
                    "is_charging": True,
                    "raw": raw_hex,
                }, dev)
                return BatteryStatus(
                    percentage,
                    is_charging=True,
                    is_connected=True,
                    details=f"Live report (USB cable): {raw_hex}",
                )

        # Optional passive mode: listen for the receiver heartbeat / pushed reports first.
        if cfg.get("enable_passive_listen"):
            status = self._read_passive(
                dev, profile, float(cfg.get("listen_timeout", PASSIVE_LISTEN_TIMEOUT))
            )
            if status is not None:
                return status

        try:
            transport = HidrawTransport(dev.dev_path, read_only=False)
            transport.open()
        except PermissionError:
            # Without udev access we can still listen passively, but never query.
            status = self._read_passive(
                dev,
                profile,
                READ_ONLY_FALLBACK_TIMEOUT,
                details_prefix="Read-only (no udev permission): ",
            )
            if status is not None:
                return status
            return self._cached_or_none(
                cached, "permission denied, install udev rules", cable_connected=cable_connected
            )
        except OSError as e:
            return self._cached_or_none(
                cached, f"failed to open device: {e}", cable_connected=cable_connected
            )

        timeout = float(cfg.get("response_timeout", ACTIVE_RESPONSE_TIMEOUT))
        with transport:
            self._drain_input(transport)
            try:
                transport.write_output_report(QUERY_PAYLOAD)
            except OSError as e:
                return self._cached_or_none(
                    cached, f"query write failed: {e}", cable_connected=cable_connected
                )
            pkt, last_seen = self._listen_for_frame(transport, timeout)

        if pkt is None:
            if last_seen is not None:
                return self._cached_or_none(
                    cached,
                    f"invalid battery frame ignored ({last_seen.hex()})",
                    cable_connected=cable_connected,
                )
            return self._cached_or_none(
                cached, "no response from receiver", cable_connected=cable_connected
            )

        parsed = self._parse_frame(pkt)
        if parsed is None:
            return self._cached_or_none(
                cached,
                f"invalid battery frame ignored ({pkt.hex()})",
                cable_connected=cable_connected,
            )

        percentage, is_charging = parsed
        is_charging = is_charging or cable_connected
        raw_hex = pkt.hex()
        source = "Passive heartbeat" if pkt.startswith(HEARTBEAT_PREFIX) else "Live report"
        self._write_cache(profile, {
            "percentage": percentage,
            "is_charging": is_charging,
            "raw": raw_hex,
        }, dev)
        return BatteryStatus(
            percentage,
            is_charging=is_charging,
            is_connected=True,
            details=f"{source}: {raw_hex}",
        )
