#!/usr/bin/env python3
"""
Automated Visual Audit & UI Regression Tool for AreYouLowBattery.

Renders all UI components offscreen, captures pixel-perfect PNGs of every
state, and allows visual and automated inspection before any release/deploy.

Usage:
    python3 tools/visual_audit.py
"""
import os
import sys

# Ensure offscreen rendering for headless/CI/sandbox execution
os.environ["QT_QPA_PLATFORM"] = "offscreen"

# Ensure package is discoverable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QLabel, QComboBox, QPushButton,
    QHBoxLayout, QLineEdit, QMenu
)
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt, QSize, QPoint
from areyoulowbattery.popup import BatteryPopup
from areyoulowbattery.discovery_dialog import DeviceDiscoveryDialog
from areyoulowbattery.discovery import DiscoveredDevice
from areyoulowbattery.models import DeviceProfile, BatteryStatus, DeviceCategory, DeviceMatch
from areyoulowbattery.tray import add_game_mode_actions
from areyoulowbattery.vector_icons import (
    render_category_icon,
    render_search_icon,
    render_refresh_icon,
    render_gear_icon,
    render_close_icon,
    get_app_icon
)

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "captures", "visual_audit")


def setup_app() -> QApplication:
    app = QApplication.instance()
    if not app:
        app = QApplication(["visual_audit", "-platform", "offscreen"])
    return app


def capture_widget(widget, filename: str) -> str:
    widget.show()
    QApplication.processEvents()
    widget.adjustSize()
    QApplication.processEvents()

    pix = widget.grab()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, filename)
    pix.save(out_path)
    print(f"Captured: {filename} ({pix.width()}x{pix.height()}) -> {out_path}")
    return out_path


def audit_tray_popup_normal():
    popup = BatteryPopup(on_refresh=lambda: None)
    devices = [
        (
            DeviceProfile("CompX 2.4G Mouse", DeviceCategory.MOUSE, "🖱️", DeviceMatch(0x25A7, 0xFA7B), protocol="compx_1k"),
            BatteryStatus(supported=True, is_connected=True, percentage=85, is_charging=False, details="2.4G Wireless")
        ),
        (
            DeviceProfile("Royalaxe R68 Keyboard", DeviceCategory.KEYBOARD, "⌨️", DeviceMatch(0x25A7, 0x2420), protocol="requires_bluetooth"),
            BatteryStatus(supported=True, is_connected=True, percentage=42, is_charging=True, details="Charging via USB")
        ),
        (
            DeviceProfile("Baseus GH02 Headset", DeviceCategory.HEADSET, "🎧", DeviceMatch(0x10D6, 0x4802), protocol="requires_bluetooth"),
            BatteryStatus(supported=True, is_connected=True, percentage=None, requires_bluetooth=True, details="Requires Bluetooth")
        ),
    ]
    popup._devices = devices
    popup._rebuild_cards()
    capture_widget(popup, "01_tray_popup_normal.png")


def audit_tray_popup_empty():
    popup = BatteryPopup(on_refresh=lambda: None)
    popup._devices = []
    popup._rebuild_cards()
    capture_widget(popup, "02_tray_popup_empty.png")


def audit_tray_popup_low_battery():
    popup = BatteryPopup(on_refresh=lambda: None)
    devices = [
        (
            DeviceProfile("CompX 2.4G Mouse", DeviceCategory.MOUSE, "🖱️", DeviceMatch(0x25A7, 0xFA7B), protocol="compx_1k"),
            BatteryStatus(supported=True, is_connected=True, percentage=8, is_charging=False, details="Battery Critical (8%)")
        ),
        (
            DeviceProfile("Royalaxe R68 Keyboard", DeviceCategory.KEYBOARD, "⌨️", DeviceMatch(0x25A7, 0x2420), protocol="compx_1k"),
            BatteryStatus(supported=True, is_connected=True, percentage=28, is_charging=False, details="Battery Low (28%)")
        ),
        (
            DeviceProfile("Xbox Wireless Controller", DeviceCategory.GAMEPAD, "🎮", DeviceMatch(0x045E, 0x028E), protocol="requires_bluetooth"),
            BatteryStatus(supported=True, is_connected=False, percentage=None, details="Offline / Sleeping")
        ),
    ]
    popup._devices = devices
    popup._rebuild_cards()
    capture_widget(popup, "03_tray_popup_low_battery.png")


def audit_tray_popup_stress():
    """Tests long peripheral names and multiple devices layout."""
    popup = BatteryPopup(on_refresh=lambda: None)
    devices = [
        (
            DeviceProfile("Royalaxe R68 Custom Triple-Mode Mechanical Keyboard RGB", DeviceCategory.KEYBOARD, "⌨️", DeviceMatch(0x25A7, 0x2420), protocol="compx_1k"),
            BatteryStatus(supported=True, is_connected=True, percentage=95, is_charging=False, details="Active • 2.4G High-Speed")
        ),
        (
            DeviceProfile("CompX Wireless PAW3395 Ultra-Lightweight Mouse 8000Hz", DeviceCategory.MOUSE, "🖱️", DeviceMatch(0x25A7, 0xFA7B), protocol="compx_8k"),
            BatteryStatus(supported=True, is_connected=True, percentage=60, is_charging=False, details="8000Hz Polling")
        ),
        (
            DeviceProfile("Baseus GH02 Dual-Wireless Headset Pro", DeviceCategory.HEADSET, "🎧", DeviceMatch(0x10D6, 0x4802), protocol="requires_bluetooth"),
            BatteryStatus(supported=True, is_connected=True, percentage=None, requires_bluetooth=True, details="Requires Bluetooth")
        ),
        (
            DeviceProfile("8BitDo Ultimate 2.4G Wireless Gamepad", DeviceCategory.GAMEPAD, "🎮", DeviceMatch(0x2DC8, 0x3106), protocol="requires_bluetooth"),
            BatteryStatus(supported=True, is_connected=True, percentage=72, is_charging=False, details="X-Input Mode")
        ),
    ]
    popup._devices = devices
    popup._rebuild_cards()
    capture_widget(popup, "04_tray_popup_stress.png")


def audit_discovery_dialog():
    from unittest.mock import patch

    unmapped = [
        DiscoveredDevice(
            name="Actions USB Audio & HID",
            vendor_id=0x10D6,
            product_id=0x4802,
            interface_number=0,
            dev_path="/dev/hidraw1",
            is_xpad=False,
            suggested_category=DeviceCategory.HEADSET,
            suggested_icon="🎧",
        ),
        DiscoveredDevice(
            name="2.4G Wireless Device",
            vendor_id=0x25A7,
            product_id=0x2420,
            interface_number=0,
            dev_path="/dev/hidraw2",
            is_xpad=False,
            suggested_category=DeviceCategory.KEYBOARD,
            suggested_icon="⌨️",
        ),
    ]

    with patch("areyoulowbattery.discovery_dialog.scan_unmapped_devices", return_value=unmapped):
        dlg = DeviceDiscoveryDialog(profiles=[], on_profile_created=lambda: None)
        dlg.resize(620, 560)
        capture_widget(dlg, "05_discovery_dialog_unmapped.png")

    with patch("areyoulowbattery.discovery_dialog.scan_unmapped_devices", return_value=[]):
        dlg_empty = DeviceDiscoveryDialog(profiles=[], on_profile_created=lambda: None)
        dlg_empty.resize(620, 560)
        capture_widget(dlg_empty, "06_discovery_dialog_all_configured.png")


def audit_change_type_dialog():
    dlg = QDialog()
    dlg.setWindowTitle("Change Device Type / Icon")
    dlg.setWindowIcon(get_app_icon())
    dlg.setFixedWidth(360)
    dlg.setStyleSheet("""
        QDialog { background-color: #0f172a; color: #f8fafc; }
        QLabel { color: #f8fafc; font-size: 12px; }
        QComboBox { background-color: #1e293b; border: 1px solid #475569; border-radius: 6px; color: #f8fafc; padding: 6px 8px; font-size: 12px; }
        QComboBox QAbstractItemView { background-color: #1e293b; color: #f8fafc; selection-background-color: #334155; }
        QPushButton#saveBtn { background-color: #0284c7; color: #ffffff; border: none; border-radius: 6px; padding: 6px 16px; font-weight: 600; font-size: 12px; }
        QPushButton#cancelBtn { background-color: #1e293b; color: #94a3b8; border: 1px solid #334155; border-radius: 6px; padding: 6px 16px; font-size: 12px; }
    """)

    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(18, 18, 18, 18)
    lay.setSpacing(14)
    lay.addWidget(QLabel("Select category for <b>Royalaxe R68 Keyboard</b>:"))

    cmb = QComboBox()
    cmb.setIconSize(QSize(18, 18))
    for cat in (DeviceCategory.MOUSE, DeviceCategory.KEYBOARD, DeviceCategory.HEADSET, DeviceCategory.GAMEPAD, DeviceCategory.OTHER):
        cmb.addItem(render_category_icon(cat, size=18, color="#38bdf8"), cat.value.title(), cat)
    cmb.setCurrentIndex(1)
    lay.addWidget(cmb)

    btns = QHBoxLayout()
    btns.addStretch(1)
    b_c = QPushButton("Cancel")
    b_c.setObjectName("cancelBtn")
    btns.addWidget(b_c)
    b_s = QPushButton("Save")
    b_s.setObjectName("saveBtn")
    btns.addWidget(b_s)
    lay.addLayout(btns)

    capture_widget(dlg, "07_change_type_dialog.png")


def audit_rename_dialog():
    dlg = QDialog()
    dlg.setWindowTitle("Rename Device")
    dlg.setWindowIcon(get_app_icon())
    dlg.setFixedWidth(360)
    dlg.setStyleSheet("""
        QDialog { background-color: #0f172a; color: #f8fafc; }
        QLabel { color: #f8fafc; font-size: 12px; }
        QLineEdit { background-color: #1e293b; border: 1px solid #475569; border-radius: 6px; color: #f8fafc; padding: 6px 8px; font-size: 12px; }
        QPushButton#saveBtn { background-color: #0284c7; color: #ffffff; border: none; border-radius: 6px; padding: 6px 16px; font-weight: 600; font-size: 12px; }
        QPushButton#cancelBtn { background-color: #1e293b; color: #94a3b8; border: 1px solid #334155; border-radius: 6px; padding: 6px 16px; font-size: 12px; }
    """)

    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(18, 18, 18, 18)
    lay.setSpacing(14)
    lay.addWidget(QLabel("New name for <b>CompX 2.4G Mouse</b>:"))

    txt = QLineEdit("CompX Gaming Mouse Pro")
    lay.addWidget(txt)

    btns = QHBoxLayout()
    btns.addStretch(1)
    b_c = QPushButton("Cancel")
    b_c.setObjectName("cancelBtn")
    btns.addWidget(b_c)
    b_s = QPushButton("Save")
    b_s.setObjectName("saveBtn")
    btns.addWidget(b_s)
    lay.addLayout(btns)

    capture_widget(dlg, "08_rename_dialog.png")


def audit_change_protocol_dialog():
    dlg = QDialog()
    dlg.setWindowTitle("Change Device Protocol")
    dlg.setWindowIcon(get_app_icon())
    dlg.setFixedWidth(440)
    dlg.setStyleSheet("""
        QDialog { background-color: #0f172a; color: #f8fafc; }
        QLabel { color: #f8fafc; font-size: 12px; }
        QComboBox { background-color: #1e293b; border: 1px solid #475569; border-radius: 6px; color: #f8fafc; padding: 6px 8px; font-size: 12px; }
        QComboBox QAbstractItemView { background-color: #1e293b; color: #f8fafc; selection-background-color: #334155; }
        QPushButton#saveBtn { background-color: #0284c7; color: #ffffff; border: none; border-radius: 6px; padding: 6px 16px; font-weight: 600; font-size: 12px; }
        QPushButton#cancelBtn { background-color: #1e293b; color: #94a3b8; border: 1px solid #334155; border-radius: 6px; padding: 6px 16px; font-size: 12px; }
    """)

    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(18, 18, 18, 18)
    lay.setSpacing(14)
    lay.addWidget(QLabel("Select communication protocol for <b>CompX 2.4G Mouse</b>:"))

    cmb = QComboBox()
    protocols = [
        "2.4G CompX / Beken (Ajazz, Delux, Pulsar, Keychron)",
        "2.4G Nordic 4K/8K RF (VXE, ATK, Scyrox, Lamzu)",
        "2.4G Logitech Lightspeed & Bolt (G Pro, G502, G915)",
        "2.4G Logitech HID++ 1.0 (Legacy Nano Receiver M185/M220)",
        "2.4G Razer HyperSpeed (Viper, DeathAdder, Basilisk)",
        "2.4G SteelSeries (Aerox, Rival, Arctis, Nova)",
        "2.4G Corsair Slipstream (Dark Core, Virtuoso, HS80)",
        "2.4G HyperX Wireless (Cloud Flight/II/Alpha, Pulsefire)",
        "2.4G Wireless Headset (Actions Semi / Baseus)",
        "2.4G Standard USB HID Battery (Generic 0x85)",
        "2.4G Dongle (Requires Bluetooth for battery)",
        "Wired USB (Continuous power, no battery alerts)",
    ]
    for p in protocols:
        cmb.addItem(p)
    cmb.setCurrentIndex(0)
    lay.addWidget(cmb)

    btns = QHBoxLayout()
    btns.addStretch(1)
    b_c = QPushButton("Cancel")
    b_c.setObjectName("cancelBtn")
    btns.addWidget(b_c)
    b_s = QPushButton("Save")
    b_s.setObjectName("saveBtn")
    btns.addWidget(b_s)
    lay.addLayout(btns)

    capture_widget(dlg, "09_change_protocol_dialog.png")


def audit_tray_context_menu():
    menu = QMenu()
    menu.setStyleSheet("""
        QMenu {
            background-color: #0f172a;
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 6px;
            color: #f8fafc;
        }
        QMenu::item {
            background-color: transparent;
            padding: 6px 14px;
            border-radius: 4px;
            color: #cbd5e1;
            font-size: 12px;
        }
        QMenu::item:selected {
            background-color: #1e293b;
            color: #38bdf8;
        }
        QMenu::item:disabled {
            color: #94a3b8;
        }
        QMenu::separator {
            height: 1px;
            background-color: #334155;
            margin: 4px 6px;
        }
    """)

    act_status1 = menu.addAction("🖱️ CompX 2.4G Mouse: 85%")
    act_status1.setEnabled(False)
    act_status2 = menu.addAction("⌨️ Royalaxe R68: 42% ⚡")
    act_status2.setEnabled(False)
    menu.addSeparator()

    menu.addAction("Find devices...")
    menu.addAction("Rename device...")
    menu.addAction("Change device type...")
    menu.addAction("Change device protocol...")
    menu.addAction("Remove device...")
    refresh_action = menu.addAction("Refresh now")
    game_mode_status, pause_action = add_game_mode_actions(menu)
    menu.addSeparator()

    act_auto = menu.addAction("Start with system")
    act_auto.setCheckable(True)
    act_auto.setChecked(True)

    menu.addSeparator()
    menu.addAction("Export diagnostics (support bundle)...")
    menu.addAction("Quit")

    capture_widget(menu, "10_tray_context_menu.png")
    game_mode_status.setVisible(True)
    pause_action.setChecked(True)
    refresh_action.setEnabled(False)
    capture_widget(menu, "11_tray_context_menu_game_mode.png")


def main():
    setup_app()
    print("=" * 60)
    print("Starting Comprehensive Automated Visual Audit for AreYouLowBattery...")
    print("=" * 60)

    audit_tray_popup_normal()
    audit_tray_popup_empty()
    audit_tray_popup_low_battery()
    audit_tray_popup_stress()
    audit_discovery_dialog()
    audit_change_type_dialog()
    audit_rename_dialog()
    audit_change_protocol_dialog()
    audit_tray_context_menu()

    print("=" * 60)
    print(f"Comprehensive visual audit completed! Captures stored in: {OUTPUT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
