import os
import sys
import time
from typing import List, Callable, Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QLineEdit, QComboBox, QScrollArea, QWidget, QMessageBox,
    QSizePolicy, QApplication
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QCursor

from areyoulowbattery.models import DeviceCategory, CATEGORY_ICONS, DeviceProfile
from areyoulowbattery.vector_icons import render_category_pixmap, render_category_icon, render_zap_icon, get_app_icon
from areyoulowbattery.discovery import (
    DiscoveredDevice, scan_unmapped_devices, probe_device_protocol, save_custom_profile
)


class DeviceConfigCard(QFrame):
    """Card widget representing an unmapped peripheral, with quick configuration fields."""

    def __init__(self, dev: DiscoveredDevice, on_saved_callback: Callable[[], None], parent=None):
        super().__init__(parent)
        self.dev = dev
        self.on_saved_callback = on_saved_callback

        self.setObjectName("configCard")
        self.setStyleSheet("""
            QFrame#configCard {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 10px;
                padding: 12px;
            }
            QLabel {
                color: #f8fafc;
                background: transparent;
                border: none;
            }
            QLineEdit, QComboBox {
                background-color: #0f172a;
                border: 1px solid #475569;
                border-radius: 6px;
                color: #f8fafc;
                padding: 6px 8px;
                font-size: 11px;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #38bdf8;
            }
            QComboBox QAbstractItemView {
                background-color: #0f172a;
                color: #f8fafc;
                selection-background-color: #334155;
                border: 1px solid #475569;
            }
            QPushButton#actionBtn {
                background-color: #0284c7;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton#actionBtn:hover {
                background-color: #0369a1;
            }
            QPushButton#testBtn {
                background-color: #334155;
                color: #cbd5e1;
                border: 1px solid #475569;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton#testBtn:hover {
                background-color: #475569;
                color: #ffffff;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Header: Device Name & Hardware ID
        header = QHBoxLayout()
        header.setSpacing(8)

        clean_name = dev.name
        if clean_name.lower().startswith("8bitdo"):
            clean_name = "8BitDo Ultimate Wireless Controller"
        elif clean_name.lower() in ("idle", "unknown", ""):
            clean_name = f"Wireless Peripheral (0x{dev.vendor_id:04x}:0x{dev.product_id:04x})"

        icon_lbl = QLabel()
        icon_lbl.setPixmap(render_category_pixmap(dev.suggested_category, size=18, color="#38bdf8"))
        icon_lbl.setFixedSize(18, 18)
        header.addWidget(icon_lbl)

        title_lbl = QLabel(clean_name)
        title_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #f8fafc;")
        title_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        header.addWidget(title_lbl, 1)

        id_badge = QLabel(f"0x{dev.vendor_id:04x}:0x{dev.product_id:04x}")
        id_badge.setStyleSheet(
            "font-size: 11px; color: #94a3b8; background-color: #0f172a; "
            "padding: 3px 8px; border-radius: 4px; border: 1px solid #334155;"
        )
        header.addWidget(id_badge)
        layout.addLayout(header)

        # Form: Name, Category, Mode
        form_layout = QVBoxLayout()
        form_layout.setSpacing(8)

        # Name row
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        lbl_name = QLabel("Name:")
        lbl_name.setFixedWidth(65)
        lbl_name.setStyleSheet("color: #94a3b8; font-size: 12px;")
        self.txt_name = QLineEdit(clean_name)
        name_row.addWidget(lbl_name)
        name_row.addWidget(self.txt_name, 1)
        form_layout.addLayout(name_row)

        # Category row
        cat_row = QHBoxLayout()
        cat_row.setSpacing(8)
        lbl_cat = QLabel("Category:")
        lbl_cat.setFixedWidth(65)
        lbl_cat.setStyleSheet("color: #94a3b8; font-size: 12px;")
        self.cmb_cat = QComboBox()
        self.cmb_cat.setIconSize(QSize(16, 16))
        cats = [
            ("Other", DeviceCategory.OTHER, "🔋"),
            ("Mouse", DeviceCategory.MOUSE, "🖱️"),
            ("Keyboard", DeviceCategory.KEYBOARD, "⌨️"),
            ("Headset", DeviceCategory.HEADSET, "🎧"),
            ("Gamepad", DeviceCategory.GAMEPAD, "🎮"),
        ]
        default_cat_idx = 0
        for idx, (label, cat_enum, ico) in enumerate(cats):
            self.cmb_cat.addItem(render_category_icon(cat_enum, size=16, color="#38bdf8"), label, (cat_enum, ico))
            if cat_enum == dev.suggested_category:
                default_cat_idx = idx
        self.cmb_cat.setCurrentIndex(default_cat_idx)
        self.cmb_cat.currentIndexChanged.connect(
            lambda: icon_lbl.setPixmap(render_category_pixmap(self.cmb_cat.currentData()[0], size=18, color="#38bdf8"))
        )
        cat_row.addWidget(lbl_cat)
        cat_row.addWidget(self.cmb_cat, 1)
        form_layout.addLayout(cat_row)

        # Mode row (Expanded 2.4G protocols & profiles)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        lbl_mode = QLabel("Protocol:")
        lbl_mode.setFixedWidth(65)
        lbl_mode.setStyleSheet("color: #94a3b8; font-size: 12px;")
        self.cmb_mode = QComboBox()
        self.cmb_mode.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_mode.setMinimumContentsLength(18)

        modes = [
            ("2.4G Dongle (Requires Bluetooth for battery)", "requires_bluetooth"),
            ("2.4G CompX / Beken (Ajazz, Delux, Pulsar, Keychron)", "compx_v1"),
            ("2.4G YJX-Chip (Smailwolf RS7, EWEADN G5, MCHOSE G7)", "yjx_v1"),
            ("2.4G GameSir XInput (Cyclone 2)", "gamesir_v1"),
            ("2.4G Nordic 4K/8K RF (VXE, ATK, Scyrox, Lamzu)", "nordic_v1"),
            ("2.4G Logitech Lightspeed & Bolt (G Pro, G502, G915)", "hidpp_v2"),
            ("2.4G Logitech HID++ 1.0 (Legacy Nano Receiver M185/M220)", "hidpp_v1"),
            ("2.4G Razer HyperSpeed (Viper, DeathAdder, Basilisk)", "razer_v1"),
            ("2.4G SteelSeries (Aerox, Rival, Arctis, Nova)", "steelseries_v1"),
            ("2.4G Corsair Slipstream (Dark Core, Virtuoso, HS80)", "corsair_v1"),
            ("2.4G HyperX Wireless (Cloud Flight/II/Alpha, Pulsefire)", "hyperx_v1"),
            ("2.4G Wireless Headset (Actions Semi / Baseus)", "actions_v1"),
            ("2.4G Standard USB HID Battery (Generic 0x85)", "generic_hid"),
            ("Wired USB (Continuous power, no battery alerts)", "is_wired"),
        ]
        # Auto-recommend "requires_bluetooth" for controllers / xpad or headsets without HID battery
        default_mode_idx = 0 if (dev.is_xpad or dev.suggested_category in (DeviceCategory.GAMEPAD, DeviceCategory.HEADSET)) else 1
        for label, mode_id in modes:
            self.cmb_mode.addItem(label, mode_id)
        self.cmb_mode.setCurrentIndex(default_mode_idx)

        mode_row.addWidget(lbl_mode)
        mode_row.addWidget(self.cmb_mode, 1)
        form_layout.addLayout(mode_row)

        layout.addLayout(form_layout)

        # Action Buttons row: Test button & Save button
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.btn_test = QPushButton("Test Protocol")
        self.btn_test.setIcon(render_zap_icon(14, "#cbd5e1"))
        self.btn_test.setIconSize(QSize(14, 14))
        self.btn_test.setObjectName("testBtn")
        self.btn_test.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_test.clicked.connect(self._handle_test)
        btn_row.addWidget(self.btn_test)

        btn_row.addStretch(1)

        self.btn_save = QPushButton("Save Device Profile")
        self.btn_save.setObjectName("actionBtn")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.clicked.connect(self._handle_save)
        btn_row.addWidget(self.btn_save)

        layout.addLayout(btn_row)

        # Dedicated full-width status / feedback row (Never forces horizontal scrollbar)
        self.test_status_lbl = QLabel("")
        self.test_status_lbl.setWordWrap(True)
        self.test_status_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.test_status_lbl.setStyleSheet("font-size: 11px; color: #94a3b8; padding-top: 2px;")
        layout.addWidget(self.test_status_lbl)

        from areyoulowbattery.engine import load_ignored_devices
        ignored = load_ignored_devices()
        self.is_ignored = (
            self.dev.name in ignored
            or f"{self.dev.vendor_id:04x}:{self.dev.product_id:04x}" in ignored
            or f"{self.dev.vendor_id:04x}" in ignored
        )
        if self.is_ignored:
            self.btn_save.setText("Restore Device")
            self.test_status_lbl.setText("ℹ️ Device was previously removed/hidden. Click 'Restore Device' to unhide it.")
            self.test_status_lbl.setStyleSheet("font-size: 11px; color: #38bdf8;")

    def _handle_test(self):
        self.btn_test.setEnabled(False)
        self.btn_test.setText("Testing...")
        self.test_status_lbl.setText("Probing 2.4G telemetry channels (CompX, Nordic, Logitech, Razer, SteelSeries, Corsair, HyperX)...")
        self.test_status_lbl.setStyleSheet("font-size: 11px; color: #38bdf8;")
        QApplication.processEvents()

        # Smooth feedback pause so user clearly sees the probe progress
        time.sleep(0.35)
        QApplication.processEvents()

        try:
            res = probe_device_protocol(self.dev)
            if res:
                proto, pct = res
                proto_names = {
                    "compx_v1": "CompX / Beken",
                    "yjx_v1": "YJX-Chip 2.4G",
                    "gamesir_v1": "GameSir XInput",
                    "nordic_v1": "Nordic 4K/8K RF",
                    "hidpp_v2": "Logitech Lightspeed",
                    "razer_v1": "Razer HyperSpeed",
                    "steelseries_v1": "SteelSeries Wireless",
                    "corsair_v1": "Corsair Slipstream",
                    "hyperx_v1": "HyperX Wireless",
                    "actions_v1": "Wireless Headset",
                    "generic_hid": "Standard HID Battery",
                }
                display_proto = proto_names.get(proto, proto)
                self.test_status_lbl.setText(f"✓ Detected via {display_proto}! Battery: {pct}%")
                self.test_status_lbl.setStyleSheet("font-size: 11px; color: #4ade80; font-weight: 600;")
                for i in range(self.cmb_mode.count()):
                    if self.cmb_mode.itemData(i) == proto:
                        self.cmb_mode.setCurrentIndex(i)
                        break
            else:
                if self.dev.is_xpad or self.dev.suggested_category == DeviceCategory.GAMEPAD:
                    self.test_status_lbl.setText("Dongle uses standard Xbox/xpad protocol (no 2.4G battery channel). Keep 'Requires Bluetooth'.")
                    self.test_status_lbl.setStyleSheet("font-size: 11px; color: #38bdf8;")
                else:
                    self.test_status_lbl.setText("No response from 2.4G protocols. If your peripheral also supports Bluetooth, select 'Requires Bluetooth'.")
                    self.test_status_lbl.setStyleSheet("font-size: 11px; color: #facc15;")
        finally:
            self.btn_test.setEnabled(True)
            self.btn_test.setText("Test Protocol")

    def _handle_save(self):
        name = self.txt_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Invalid Name", "Please enter a valid peripheral name.")
            return

        from areyoulowbattery.engine import restore_device
        restore_device(name, self.dev.vendor_id, self.dev.product_id)
        if self.dev.name and self.dev.name != name:
            restore_device(self.dev.name, self.dev.vendor_id, self.dev.product_id)

        cat_enum, icon = self.cmb_cat.currentData()
        mode_id = self.cmb_mode.currentData()

        requires_bt = mode_id == "requires_bluetooth"
        is_wired = mode_id == "is_wired"
        protocol = "compx_v1" if (requires_bt or is_wired) else mode_id

        # For 8BitDo Ultimate controllers, include both idle PID and active PID
        pids = None
        if self.dev.vendor_id == 0x2dc8 and self.dev.product_id in (0x3106, 0x3109):
            pids = [0x3106, 0x3109]

        try:
            path = save_custom_profile(
                name=name,
                category=cat_enum,
                icon=icon,
                vendor_id=self.dev.vendor_id,
                product_id=self.dev.product_id,
                protocol=protocol,
                requires_bluetooth=requires_bt,
                is_wired=is_wired,
            )
            # If 8BitDo dual PID, ensure both are in YAML
            if pids:
                import yaml
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                data["match"]["product_ids"] = [f"0x{p:04x}" for p in pids]
                with open(path, "w", encoding="utf-8") as f:
                    yaml.dump(data, f, default_flow_style=False, sort_keys=False)

            self.btn_save.setEnabled(False)
            self.btn_save.setText("✓ Device Restored" if getattr(self, "is_ignored", False) else "✓ Profile Saved")
            self.test_status_lbl.setText("Active! Device restored to dashboard." if getattr(self, "is_ignored", False) else "Active! Profile saved to user directory.")
            self.test_status_lbl.setStyleSheet("font-size: 11px; color: #4ade80; font-weight: 600;")
            self.on_saved_callback()
        except Exception as e:
            QMessageBox.critical(self, "Error Saving Profile", f"Could not write profile YAML:\n{e}")


class DeviceDiscoveryDialog(QDialog):
    """Modern dark-themed dialog allowing users to scan for and configure unmapped peripherals."""

    def __init__(self, profiles: List[DeviceProfile], on_profile_created: Callable[[], None], parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.profiles = profiles
        self.on_profile_created = on_profile_created

        self.setWindowTitle("Find & Configure Devices — AreYouLowBattery")
        self.setObjectName("areyoulowbattery")
        self.setWindowIcon(get_app_icon())
        self.setFixedSize(620, 540)
        self.setStyleSheet("""
            QDialog {
                background-color: #0f172a;
                color: #f8fafc;
            }
            QScrollArea {
                background-color: transparent;
                border: none;
            }
            QPushButton#rescanBtn {
                background-color: #1e293b;
                color: #cbd5e1;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton#rescanBtn:hover {
                background-color: #334155;
                color: #f8fafc;
            }
            QPushButton#closeBtn {
                background-color: #1e293b;
                color: #94a3b8;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton#closeBtn:hover {
                background-color: #334155;
                color: #f8fafc;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(12)

        # Header
        header = QHBoxLayout()
        header.setSpacing(10)

        header_text = QVBoxLayout()
        header_text.setSpacing(2)
        title = QLabel("Find & Configure Devices")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #f8fafc;")
        subtitle = QLabel("Scan USB ports to configure new wireless peripherals")
        subtitle.setStyleSheet("font-size: 11px; color: #94a3b8;")
        header_text.addWidget(title)
        header_text.addWidget(subtitle)
        header.addLayout(header_text, 1)

        self.btn_rescan = QPushButton("↻ Rescan")
        self.btn_rescan.setObjectName("rescanBtn")
        self.btn_rescan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_rescan.clicked.connect(self._run_scan)
        header.addWidget(self.btn_rescan)

        layout.addLayout(header)

        # Linux-only udev rules banner (100% omitted on Windows)
        if sys.platform.startswith("linux"):
            self._init_udev_banner(layout)

        # Scrollable area for device cards (Strictly vertical only)
        self.scroll = QScrollArea(self)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: #0f172a;
                width: 6px;
                margin: 0px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #334155;
                min-height: 24px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: #475569;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
                background: none;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
        """)
        self.scroll.viewport().setStyleSheet("background: transparent; border: none;")
        self.scroll.viewport().setAutoFillBackground(False)

        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background: transparent; border: none;")
        self.cards_layout = QVBoxLayout(self.scroll_content)
        self.cards_layout.setContentsMargins(0, 4, 8, 14)
        self.cards_layout.setSpacing(10)
        self.cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.scroll_content)
        layout.addWidget(self.scroll, 1)

        # Footer
        footer = QHBoxLayout()
        footer.addStretch(1)
        btn_close = QPushButton("Done")
        btn_close.setObjectName("closeBtn")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.clicked.connect(self.accept)
        footer.addWidget(btn_close)
        layout.addLayout(footer)

        self._run_scan()

    def _init_udev_banner(self, layout: QVBoxLayout):
        from areyoulowbattery.udev_installer import are_udev_rules_installed, install_udev_rules

        is_installed = are_udev_rules_installed()
        banner = QFrame()
        banner.setObjectName("udevBanner")
        banner.setStyleSheet("""
            QFrame#udevBanner {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 6px 12px;
            }
        """)
        b_layout = QHBoxLayout(banner)
        b_layout.setContentsMargins(8, 6, 8, 6)
        b_layout.setSpacing(10)

        icon_lbl = QLabel("🛡️")
        icon_lbl.setStyleSheet("font-size: 15px;")
        b_layout.addWidget(icon_lbl)

        msg_layout = QVBoxLayout()
        msg_layout.setSpacing(1)
        msg_title = QLabel("2.4G Device Permissions (udev)" if not is_installed else "✓ 2.4G Device Permissions Active")
        msg_title.setStyleSheet(
            "font-size: 12px; font-weight: 600; color: #f8fafc;" if not is_installed else "font-size: 12px; font-weight: 600; color: #4ade80;"
        )
        msg_desc = QLabel(
            "Install udev rules to access 2.4G mice & keyboards without root." if not is_installed
            else "Hardened rules active. Click 'Update' if a newly connected device needs access."
        )
        msg_desc.setStyleSheet("font-size: 11px; color: #94a3b8;")
        msg_layout.addWidget(msg_title)
        msg_layout.addWidget(msg_desc)
        b_layout.addLayout(msg_layout, 1)

        btn_install = QPushButton("Install Rules" if not is_installed else "Update")
        btn_install.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_install.setStyleSheet("""
            QPushButton {
                background-color: #0284c7;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #0369a1;
            }
        """)

        def _handle_install():
            if install_udev_rules(self):
                msg_title.setText("✓ 2.4G Device Permissions Active")
                msg_title.setStyleSheet("font-size: 12px; font-weight: 600; color: #4ade80;")
                msg_desc.setText("Hardened rules active. Permissions updated.")
                btn_install.setText("Update")
                self._run_scan()

        btn_install.clicked.connect(_handle_install)
        b_layout.addWidget(btn_install)
        layout.addWidget(banner)

    def _run_scan(self):
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        unmapped = scan_unmapped_devices(self.profiles)
        if not unmapped:
            empty_lbl = QLabel(
                "✓ All connected wireless peripherals are already configured!\n\n"
                "To configure a new device, plug its 2.4G dongle into USB and click Rescan."
            )
            empty_lbl.setStyleSheet(
                "font-size: 12px; color: #94a3b8; background-color: #1e293b; "
                "border: 1px solid #334155; border-radius: 10px; padding: 24px;"
            )
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.cards_layout.addWidget(empty_lbl)
            return

        for dev in unmapped:
            card = DeviceConfigCard(dev, on_saved_callback=self._handle_profile_saved, parent=self.scroll_content)
            self.cards_layout.addWidget(card)

    def _handle_profile_saved(self):
        from areyoulowbattery.profile_loader import load_all_profiles
        self.profiles = load_all_profiles()
        self.on_profile_created()
