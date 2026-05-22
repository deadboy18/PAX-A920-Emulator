"""
GHL PAX A920 Terminal Emulator
==============================
Virtual ECR terminal for development and testing.
Emulates a GHL-configured PAX A920 payment terminal over RS232 serial.

Requirements:
    pip install pyserial

Setup:
    1. Install com0com (https://com0com.sourceforge.net/) to create virtual COM port pairs
       - e.g. COM5 <-> COM6
    2. Run this emulator on one port (e.g. COM6)
    3. Point your POS software at the other port (e.g. COM5)

Author: Deadboy / GHL POS Integration Toolkit
Protocol: GHL ECR Spec v1.0.17
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import serial
import serial.tools.list_ports
import threading
import time
import json
import os
import random
import string
from datetime import datetime

# ╔══════════════════════════════════════════════════════════════╗
# ║                    CONFIGURATION                             ║
# ╚══════════════════════════════════════════════════════════════╝

STX = 0x02
ETX = 0x03

CONFIG_FILE = "emulator_config.json"

# Terminal identity (configurable in GUI)
DEFAULT_TID = "20001234"
DEFAULT_MID = "000000012345678"

# ╔══════════════════════════════════════════════════════════════╗
# ║                    CARD DECK                                 ║
# ╚══════════════════════════════════════════════════════════════╝

CARD_DECK = [
    # --- APPROVAL CARDS ---
    {
        "name": "Visa Platinum",
        "bank": "Maybank",
        "pan": "4921810012345532",
        "pan_masked": "492181XXXXXX5532",
        "expiry": "2803",  # YYMM
        "type_code": "04",
        "brand": "VISA",
        "result": "00",
        "color": "#1A237E",
        "pin_required": True,
    },
    {
        "name": "Mastercard Gold",
        "bank": "CIMB Bank",
        "pan": "5274550088218821",
        "pan_masked": "527455XXXXXX8821",
        "expiry": "2705",
        "type_code": "05",
        "brand": "MASTERCARD",
        "result": "00",
        "color": "#E65100",
        "pin_required": True,
    },
    {
        "name": "MyDebit",
        "bank": "Public Bank",
        "pan": "4000123498769876",
        "pan_masked": "400012XXXXXX9876",
        "expiry": "3202",
        "type_code": "08",
        "brand": "MYDEBIT",
        "result": "00",
        "color": "#004D40",
        "pin_required": False,  # Tap-and-go for small amounts
    },
    {
        "name": "Visa Classic",
        "bank": "RHB Bank",
        "pan": "4539010012345678",
        "pan_masked": "453901XXXXXX5678",
        "expiry": "2910",
        "type_code": "04",
        "brand": "VISA",
        "result": "00",
        "color": "#0D47A1",
        "pin_required": True,
    },
    {
        "name": "Amex Blue",
        "bank": "Amex Direct",
        "pan": "374200001091003",
        "pan_masked": "3742XXXXXXX1003",
        "expiry": "2612",
        "type_code": "07",
        "brand": "AMEX",
        "result": "00",
        "color": "#006064",
        "pin_required": True,
    },
    {
        "name": "JCB Standard",
        "bank": "AmBank",
        "pan": "3530111333300000",
        "pan_masked": "353011XXXXXX0000",
        "expiry": "2909",
        "type_code": "09",
        "brand": "JCB",
        "result": "00",
        "color": "#1B5E20",
        "pin_required": True,
    },
    {
        "name": "UnionPay",
        "bank": "Bank of China (MY)",
        "pan": "6221260012340045",
        "pan_masked": "622126XXXXXX0045",
        "expiry": "2908",
        "type_code": "10",
        "brand": "UNIONPAY",
        "result": "00",
        "color": "#B71C1C",
        "pin_required": True,
    },
    {
        "name": "E-Wallet (TnG)",
        "bank": "Touch n Go",
        "pan": "9000000012345678",
        "pan_masked": "900000XXXXXX5678",
        "expiry": "9912",
        "type_code": "11",
        "brand": "E-WALLET",
        "result": "00",
        "color": "#4A148C",
        "pin_required": False,
    },
    # --- DECLINE CARDS ---
    {
        "name": "Visa (Expired)",
        "bank": "Hong Leong Bank",
        "pan": "4111111111111111",
        "pan_masked": "411111XXXXXX1111",
        "expiry": "2201",  # expired
        "type_code": "04",
        "brand": "VISA",
        "result": "54",  # Expired card
        "color": "#616161",
        "pin_required": False,
    },
    {
        "name": "MC (No Funds)",
        "bank": "OCBC Bank",
        "pan": "5500000000000004",
        "pan_masked": "550000XXXXXX0004",
        "expiry": "2712",
        "type_code": "05",
        "brand": "MASTERCARD",
        "result": "51",  # Insufficient funds
        "color": "#616161",
        "pin_required": False,
    },
    {
        "name": "Visa (Wrong PIN)",
        "bank": "Alliance Bank",
        "pan": "4000000000000002",
        "pan_masked": "400000XXXXXX0002",
        "expiry": "2806",
        "type_code": "04",
        "brand": "VISA",
        "result": "55",  # Incorrect PIN
        "color": "#616161",
        "pin_required": True,
    },
    {
        "name": "MC (Bank Down)",
        "bank": "Standard Chartered",
        "pan": "5100000000000008",
        "pan_masked": "510000XXXXXX0008",
        "expiry": "2901",
        "type_code": "05",
        "brand": "MASTERCARD",
        "result": "91",  # Issuer unavailable
        "color": "#616161",
        "pin_required": False,
    },
]

ERROR_DESCRIPTIONS = {
    "00": "Approved",
    "CT": "Cancelled / Timeout",
    "51": "Insufficient Funds",
    "54": "Expired Card",
    "55": "Incorrect PIN",
    "91": "Issuer Unavailable",
    "05": "Do Not Honour",
    "14": "Invalid Card Number",
}

CARD_TYPE_NAMES = {
    "04": "VISA", "05": "MASTERCARD", "06": "DINERS", "07": "AMEX",
    "08": "MYDEBIT", "09": "JCB", "10": "UNIONPAY", "11": "E-WALLET",
}

# ╔══════════════════════════════════════════════════════════════╗
# ║                ECR PROTOCOL ENGINE                           ║
# ╚══════════════════════════════════════════════════════════════╝

class ECRProtocol:
    """Handles ECR packet parsing and response building per GHL Spec v1.0.17"""

    RESPONSE_CODES = {"020": "021", "022": "023", "026": "027", "050": "051"}
    COMMAND_NAMES = {"020": "SALE", "022": "VOID", "026": "REFUND", "050": "SETTLEMENT"}

    @staticmethod
    def calculate_checksum(payload_bytes):
        """XOR checksum: pad to multiple of 8 with 0xFF, XOR all 8-byte blocks."""
        data = bytearray(payload_bytes)
        rem = len(data) % 8
        if rem != 0:
            data += b'\xFF' * (8 - rem)
        chk = bytearray(8)
        for i in range(0, len(data), 8):
            for j in range(8):
                chk[j] ^= data[i + j]
        return bytes(chk)

    @staticmethod
    def parse_tx_packet(raw_bytes):
        """Parse a TX packet from POS software. Returns dict or None on error."""
        if len(raw_bytes) < 11:
            return None
        if raw_bytes[0] != STX or raw_bytes[-1] != ETX:
            return None

        payload = raw_bytes[1:-9]  # Strip STX, checksum(8), ETX
        checksum = raw_bytes[-9:-1]

        # Verify checksum
        calc_chk = ECRProtocol.calculate_checksum(payload)
        checksum_valid = (calc_chk == checksum)

        try:
            payload_str = payload.decode('ascii')
        except:
            return None

        if len(payload_str) < 25:
            # Pad if short (some implementations send less)
            payload_str = payload_str.ljust(25)

        cmd = payload_str[0:3]
        amount_raw = payload_str[3:15]
        invoice_raw = payload_str[15:21]
        cashier_raw = payload_str[21:25]

        try:
            amount_cents = int(amount_raw)
        except:
            amount_cents = 0

        try:
            invoice = int(invoice_raw)
        except:
            invoice = 0

        return {
            "command": cmd,
            "command_name": ECRProtocol.COMMAND_NAMES.get(cmd, "UNKNOWN"),
            "amount_cents": amount_cents,
            "amount_display": "{:.2f}".format(amount_cents / 100),
            "invoice": invoice,
            "invoice_raw": invoice_raw,
            "cashier": cashier_raw.strip(),
            "checksum_valid": checksum_valid,
            "raw_hex": raw_bytes.hex().upper(),
        }

    @staticmethod
    def build_rx_packet(cmd, error_code, card, amount_cents, invoice,
                        cashier, auth_code, stan, tid, mid, batch,
                        firmware_new=True):
        """
        Build an RX response packet matching GHL Spec v1.0.17.
        
        Response payload layout:
          Offset 0-2:   Response code (3 bytes)
          Offset 3-4:   Error code (2 bytes)
          Offset 5-26:  Card number with length prefix (22 bytes)
          Offset 27-30: Card expiry YYMM (4 bytes)
          Offset 31-32: Card type code (2 bytes)
          Offset 33-40: Authorization code (8 bytes)
          Offset 41-52: Gross amount in cents (12 bytes)
          Offset 53-64: Net amount in cents (12 bytes)
          Offset 65-70: Trace number / STAN (6 bytes)
          Offset 71-76: Invoice number (6 bytes)
          Offset 77-80: Cashier ID (4 bytes)
          Offset 81-95: Card brand name (15 bytes)
          --- Firmware v1.0.17+ only ---
          Offset 96-103:  Terminal ID (8 bytes)
          Offset 104-118: Merchant ID (15 bytes)
          Offset 119-124: Batch number (6 bytes)
        """
        response_code = ECRProtocol.RESPONSE_CODES.get(cmd, "021")

        # Format card number field: 2-byte length prefix + PAN left-justified, zero-padded to 20
        if error_code == "00" and card:
            pan = card["pan_masked"]
            pan_len = len(pan)
            card_field = f"{pan_len:02d}{pan}".ljust(22, "0")[:22]
            expiry = card["expiry"]
            card_type = card["type_code"]
            brand_name = card.get("brand", "UNKNOWN").ljust(15)[:15]
        else:
            # Declined / cancelled: zero-fill card fields
            card_field = "0" * 22
            expiry = "0000"
            card_type = "00"
            brand_name = " " * 15

        # Build the payload string
        parts = [
            response_code,                              # 0-2:   Response code (3)
            error_code[:2].ljust(2),                    # 3-4:   Error code (2)
            card_field,                                 # 5-26:  Card number (22)
            expiry[:4].ljust(4, "0"),                   # 27-30: Expiry (4)
            card_type[:2].ljust(2, "0"),                # 31-32: Card type (2)
            (auth_code if error_code == "00"
             else "        ")[:8].ljust(8),             # 33-40: Auth code (8)
            f"{amount_cents:012d}",                     # 41-52: Gross amount (12)
            f"{0:012d}",                                # 53-64: Net amount (12)
            f"{stan:06d}",                              # 65-70: STAN (6)
            f"{invoice:06d}",                           # 71-76: Invoice (6)
            f"{cashier:>4}"[:4],                        # 77-80: Cashier (4)
            brand_name,                                 # 81-95: Brand name (15)
        ]

        if firmware_new:
            parts.append(tid[:8].ljust(8))              # 96-103:  TID (8)
            parts.append(mid[:15].ljust(15))            # 104-118: MID (15)
            parts.append(f"{batch:06d}")                # 119-124: Batch (6)

        payload_str = "".join(parts)
        payload_bytes = payload_str.encode('ascii')

        checksum = ECRProtocol.calculate_checksum(payload_bytes)

        packet = bytes([STX]) + payload_bytes + checksum + bytes([ETX])
        return packet


# ╔══════════════════════════════════════════════════════════════╗
# ║               SERIAL PORT LISTENER                           ║
# ╚══════════════════════════════════════════════════════════════╝

class SerialListener:
    """Listens on a COM port for incoming TX packets from POS software."""

    def __init__(self, on_packet_received, on_log):
        self.ser = None
        self.running = False
        self.thread = None
        self.on_packet_received = on_packet_received
        self.on_log = on_log

    def connect(self, port):
        self.disconnect()
        try:
            self.ser = serial.Serial(
                port=port, baudrate=9600, bytesize=8,
                parity='N', stopbits=1, timeout=0.1
            )
            self.running = True
            self.thread = threading.Thread(target=self._listen_loop, daemon=True)
            self.thread.start()
            return True, f"Listening on {port}"
        except Exception as e:
            return False, str(e)

    def disconnect(self):
        self.running = False
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except:
            pass
        self.ser = None

    def send_response(self, packet_bytes):
        """Send an RX response back to the POS software."""
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(packet_bytes)
                self.on_log(f"TX > {packet_bytes.hex().upper()}", "tx")
                return True
            except Exception as e:
                self.on_log(f"TX ERROR: {e}", "err")
                return False
        return False

    def _listen_loop(self):
        """Background thread: reads bytes looking for STX..ETX packets."""
        buffer = bytearray()
        in_packet = False

        while self.running:
            try:
                if not self.ser or not self.ser.is_open:
                    time.sleep(0.1)
                    continue

                data = self.ser.read(64)  # Read up to 64 bytes
                if not data:
                    continue

                for byte in data:
                    if byte == STX:
                        buffer = bytearray([byte])
                        in_packet = True
                    elif in_packet:
                        buffer.append(byte)
                        if byte == ETX:
                            # Complete packet received
                            self.on_log(f"RX < {buffer.hex().upper()}", "rx")
                            packet = bytes(buffer)
                            self.on_packet_received(packet)
                            buffer = bytearray()
                            in_packet = False
                        elif len(buffer) > 200:
                            # Safety: discard overly long buffer
                            self.on_log("RX WARN: Buffer overflow, discarding", "err")
                            buffer = bytearray()
                            in_packet = False

            except serial.SerialException:
                if self.running:
                    self.on_log("Serial connection lost", "err")
                    time.sleep(1)
            except Exception as e:
                if self.running:
                    self.on_log(f"Listener error: {e}", "err")
                    time.sleep(0.5)


# ╔══════════════════════════════════════════════════════════════╗
# ║                    THEME CONSTANTS                           ║
# ╚══════════════════════════════════════════════════════════════╝

BG_MAIN = "#1E1E2E"
BG_PANEL = "#2A2A3C"
BG_TERMINAL = "#0A0A0A"
BG_SCREEN = "#0A1628"
BG_SCREEN_HEADER = "#0D2847"
COL_TEXT = "#E0E0F0"
COL_TEXT_DIM = "#8888AA"
COL_ACCENT = "#4A90D9"
COL_GREEN = "#27AE60"
COL_RED = "#E74C3C"
COL_YELLOW = "#F1C40F"
COL_ORANGE = "#E67E22"

FONT_HEADER = ("Segoe UI", 14, "bold")
FONT_LABEL = ("Segoe UI", 9)
FONT_LABEL_B = ("Segoe UI", 9, "bold")
FONT_MONO = ("Consolas", 10)
FONT_MONO_SM = ("Consolas", 9)
FONT_SCREEN = ("Consolas", 14, "bold")
FONT_SCREEN_LG = ("Consolas", 22, "bold")
FONT_SCREEN_SM = ("Consolas", 10)
FONT_RECEIPT = ("Courier New", 9)
FONT_BTN = ("Segoe UI", 10, "bold")
FONT_PIN = ("Consolas", 18, "bold")


# ╔══════════════════════════════════════════════════════════════╗
# ║                     MAIN GUI                                 ║
# ╚══════════════════════════════════════════════════════════════╝

class TerminalEmulatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("GHL PAX A920 Terminal Emulator")
        self.root.geometry("1100x780")
        self.root.configure(bg=BG_MAIN)
        self.root.minsize(1000, 700)

        # State
        self.state = "idle"  # idle, sale_prompt, void_prompt, refund_prompt, settle_prompt
                             # pin_entry, processing, approved, declined, settling, settled
        self.selected_card = 0
        self.pin_digits = []
        self.current_tx = None
        self.stan = 50 + random.randint(0, 50)
        self.batch = 1
        self.tid = DEFAULT_TID
        self.mid = DEFAULT_MID
        self.firmware_new = True
        self.auto_respond = False
        self.response_delay_min = 2.0
        self.response_delay_max = 4.0
        self.admin_password = "0000"
        self.transaction_history = []

        # Protocol & Serial
        self.listener = SerialListener(
            on_packet_received=self._on_packet_received,
            on_log=self._log_from_thread
        )

        # Build UI
        self._build_ui()
        self._render_screen()
        self._render_cards()
        self._load_config()

        # Cleanup
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ─────────────────────────────────────────────────────────────
    #  UI CONSTRUCTION
    # ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Main container: 3 columns
        main = tk.Frame(self.root, bg=BG_MAIN)
        main.pack(fill="both", expand=True, padx=10, pady=10)
        main.columnconfigure(0, weight=1, minsize=220)
        main.columnconfigure(1, weight=0, minsize=300)
        main.columnconfigure(2, weight=1, minsize=260)
        main.rowconfigure(0, weight=1)

        # ── LEFT PANEL ──
        left = tk.Frame(main, bg=BG_MAIN)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.rowconfigure(1, weight=1)

        # Config section
        cfg_frame = tk.LabelFrame(left, text=" Configuration ", font=FONT_LABEL_B,
                                   bg=BG_PANEL, fg=COL_TEXT, bd=1, relief="groove",
                                   padx=10, pady=8)
        cfg_frame.grid(row=0, column=0, sticky="new", pady=(0, 8))

        # COM Port
        row = tk.Frame(cfg_frame, bg=BG_PANEL)
        row.pack(fill="x", pady=2)
        tk.Label(row, text="COM Port:", font=FONT_LABEL, bg=BG_PANEL, fg=COL_TEXT_DIM).pack(side="left")
        ports = [p.device for p in serial.tools.list_ports.comports()] or ["COM6"]
        self.port_var = tk.StringVar(value=ports[-1] if ports else "COM6")
        self.cb_port = ttk.Combobox(row, textvariable=self.port_var, values=ports, width=8)
        self.cb_port.pack(side="right")

        # Connect button
        btn_row = tk.Frame(cfg_frame, bg=BG_PANEL)
        btn_row.pack(fill="x", pady=4)
        self.btn_conn = tk.Button(btn_row, text="START LISTENING", font=FONT_BTN,
                                   bg="#2C3E50", fg="white", activebackground="#34495E",
                                   bd=0, padx=15, pady=5, command=self._toggle_connection)
        self.btn_conn.pack(fill="x")

        # Status
        self.lbl_status = tk.Label(cfg_frame, text="● Disconnected", font=FONT_LABEL,
                                    bg=BG_PANEL, fg=COL_RED)
        self.lbl_status.pack(anchor="w", pady=2)

        # Terminal ID
        row2 = tk.Frame(cfg_frame, bg=BG_PANEL)
        row2.pack(fill="x", pady=2)
        tk.Label(row2, text="TID:", font=FONT_MONO_SM, bg=BG_PANEL, fg=COL_TEXT_DIM).pack(side="left")
        self.ent_tid = tk.Entry(row2, font=FONT_MONO_SM, width=10, bg="#1E1E2E", fg=COL_TEXT,
                                 insertbackground=COL_TEXT, bd=1, relief="solid")
        self.ent_tid.insert(0, DEFAULT_TID)
        self.ent_tid.pack(side="right")

        row3 = tk.Frame(cfg_frame, bg=BG_PANEL)
        row3.pack(fill="x", pady=2)
        tk.Label(row3, text="MID:", font=FONT_MONO_SM, bg=BG_PANEL, fg=COL_TEXT_DIM).pack(side="left")
        self.ent_mid = tk.Entry(row3, font=FONT_MONO_SM, width=18, bg="#1E1E2E", fg=COL_TEXT,
                                 insertbackground=COL_TEXT, bd=1, relief="solid")
        self.ent_mid.insert(0, DEFAULT_MID)
        self.ent_mid.pack(side="right")

        # Firmware toggle
        self.var_firmware = tk.BooleanVar(value=True)
        tk.Checkbutton(cfg_frame, text="Firmware v1.0.17+ (send TID/MID/Batch)",
                        variable=self.var_firmware, font=FONT_LABEL,
                        bg=BG_PANEL, fg=COL_TEXT, selectcolor=BG_MAIN,
                        activebackground=BG_PANEL, activeforeground=COL_TEXT
                        ).pack(anchor="w", pady=2)

        # Auto-respond toggle
        self.var_auto = tk.BooleanVar(value=False)
        tk.Checkbutton(cfg_frame, text="Auto-respond (no manual interaction)",
                        variable=self.var_auto, font=FONT_LABEL,
                        bg=BG_PANEL, fg=COL_TEXT, selectcolor=BG_MAIN,
                        activebackground=BG_PANEL, activeforeground=COL_TEXT
                        ).pack(anchor="w", pady=2)

        # Delay
        delay_row = tk.Frame(cfg_frame, bg=BG_PANEL)
        delay_row.pack(fill="x", pady=2)
        tk.Label(delay_row, text="Response delay (s):", font=FONT_LABEL,
                  bg=BG_PANEL, fg=COL_TEXT_DIM).pack(side="left")
        self.ent_delay = tk.Entry(delay_row, font=FONT_MONO_SM, width=6, bg="#1E1E2E",
                                   fg=COL_TEXT, insertbackground=COL_TEXT, bd=1, relief="solid")
        self.ent_delay.insert(0, "2-4")
        self.ent_delay.pack(side="right")

        # Card deck
        card_frame = tk.LabelFrame(left, text=" Card Deck ", font=FONT_LABEL_B,
                                    bg=BG_PANEL, fg=COL_TEXT, bd=1, relief="groove",
                                    padx=6, pady=6)
        card_frame.grid(row=1, column=0, sticky="nsew")

        self.card_canvas = tk.Canvas(card_frame, bg=BG_PANEL, highlightthickness=0)
        card_scroll = tk.Scrollbar(card_frame, orient="vertical", command=self.card_canvas.yview)
        self.card_inner = tk.Frame(self.card_canvas, bg=BG_PANEL)

        self.card_inner.bind("<Configure>",
            lambda e: self.card_canvas.configure(scrollregion=self.card_canvas.bbox("all")))
        self.card_canvas.create_window((0, 0), window=self.card_inner, anchor="nw")
        self.card_canvas.configure(yscrollcommand=card_scroll.set)

        self.card_canvas.pack(side="left", fill="both", expand=True)
        card_scroll.pack(side="right", fill="y")

        # ── CENTER: TERMINAL ──
        center = tk.Frame(main, bg=BG_MAIN)
        center.grid(row=0, column=1, sticky="ns", padx=8)

        tk.Label(center, text="PAX A920 — GHL TERMINAL EMULATOR",
                  font=("Segoe UI", 8), bg=BG_MAIN, fg=COL_TEXT_DIM).pack(pady=(0, 6))

        # Terminal body
        term_body = tk.Frame(center, bg=BG_TERMINAL, padx=16, pady=14,
                              highlightbackground="#333", highlightthickness=2)
        term_body.pack()

        # Camera/speaker
        top_bar = tk.Frame(term_body, bg=BG_TERMINAL)
        top_bar.pack(pady=(0, 8))
        for _ in range(2):
            tk.Canvas(top_bar, width=8, height=8, bg="#222", highlightthickness=1,
                       highlightbackground="#333").pack(side="left", padx=4)
        tk.Canvas(top_bar, width=40, height=4, bg="#1A1A1A", highlightthickness=1,
                   highlightbackground="#333").pack(side="left", padx=6)

        # Screen
        screen_frame = tk.Frame(term_body, bg="#111", padx=2, pady=2)
        screen_frame.pack()

        self.screen = tk.Frame(screen_frame, bg=BG_SCREEN, width=260, height=380)
        self.screen.pack()
        self.screen.pack_propagate(False)

        # Screen header
        scr_hdr = tk.Frame(self.screen, bg=BG_SCREEN_HEADER, height=24)
        scr_hdr.pack(fill="x")
        scr_hdr.pack_propagate(False)
        tk.Label(scr_hdr, text="GHL", font=("Consolas", 9, "bold"),
                  bg=BG_SCREEN_HEADER, fg=COL_ACCENT).pack(side="left", padx=8)
        self.lbl_clock = tk.Label(scr_hdr, text="12:00", font=("Consolas", 9),
                                   bg=BG_SCREEN_HEADER, fg=COL_ACCENT)
        self.lbl_clock.pack(side="right", padx=8)
        self._update_clock()

        # Screen body (dynamic content)
        self.screen_body = tk.Frame(self.screen, bg=BG_SCREEN)
        self.screen_body.pack(fill="both", expand=True, padx=10, pady=10)

        # Hardware buttons
        hw_frame = tk.Frame(term_body, bg=BG_TERMINAL)
        hw_frame.pack(pady=(10, 0))

        btns = [
            ("✕", COL_RED, self._press_cancel),
            ("⌫", COL_YELLOW, self._press_back),
            ("☰", COL_ACCENT, self._press_menu),
            ("✓", COL_GREEN, self._press_enter),
        ]
        for text, color, cmd in btns:
            b = tk.Button(hw_frame, text=text, font=("Segoe UI", 12, "bold"),
                           bg=color, fg="white", activebackground=color,
                           width=3, height=1, bd=0, command=cmd)
            b.pack(side="left", padx=4)

        tk.Label(center, text="Red=Cancel  Yellow=Back  Blue=Menu  Green=Enter",
                  font=("Segoe UI", 7), bg=BG_MAIN, fg=COL_TEXT_DIM).pack(pady=(6, 0))

        # ── RIGHT PANEL ──
        right = tk.Frame(main, bg=BG_MAIN)
        right.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        right.rowconfigure(1, weight=1)

        # Receipt
        rcpt_frame = tk.LabelFrame(right, text=" Receipt ", font=FONT_LABEL_B,
                                    bg=BG_PANEL, fg=COL_TEXT, bd=1, relief="groove",
                                    padx=6, pady=6)
        rcpt_frame.grid(row=0, column=0, sticky="new", pady=(0, 8))

        self.txt_receipt = tk.Text(rcpt_frame, font=FONT_RECEIPT, bg="#FFFDE7", fg="#1A1A1A",
                                    width=38, height=16, bd=0, state="disabled")
        self.txt_receipt.pack(fill="x")

        # Protocol log
        log_frame = tk.LabelFrame(right, text=" Protocol Log ", font=FONT_LABEL_B,
                                   bg=BG_PANEL, fg=COL_TEXT, bd=1, relief="groove",
                                   padx=6, pady=6)
        log_frame.grid(row=1, column=0, sticky="nsew")

        self.txt_log = scrolledtext.ScrolledText(log_frame, font=FONT_MONO_SM,
                                                   bg="#0A0A1A", fg="#AAAACC",
                                                   height=10, bd=0, state="disabled")
        self.txt_log.pack(fill="both", expand=True)
        self.txt_log.tag_config("tx", foreground="#4FC3F7")
        self.txt_log.tag_config("rx", foreground="#69F0AE")
        self.txt_log.tag_config("err", foreground="#FF8A80")
        self.txt_log.tag_config("info", foreground="#FFD54F")

    # ─────────────────────────────────────────────────────────────
    #  CARD DECK RENDERING
    # ─────────────────────────────────────────────────────────────

    def _render_cards(self):
        for widget in self.card_inner.winfo_children():
            widget.destroy()

        for i, card in enumerate(CARD_DECK):
            is_sel = (i == self.selected_card)
            bg = "#3A3A5C" if is_sel else BG_PANEL
            border_color = COL_ACCENT if is_sel else "#444"

            frame = tk.Frame(self.card_inner, bg=bg, padx=6, pady=4,
                              highlightbackground=border_color, highlightthickness=1)
            frame.pack(fill="x", pady=2, padx=2)
            frame.bind("<Button-1>", lambda e, idx=i: self._select_card(idx))

            # Card type chip
            top = tk.Frame(frame, bg=bg)
            top.pack(fill="x")
            top.bind("<Button-1>", lambda e, idx=i: self._select_card(idx))

            chip = tk.Label(top, text=card["type_code"], font=("Consolas", 8, "bold"),
                             bg=card["color"], fg="white", padx=4, pady=1)
            chip.pack(side="left")
            chip.bind("<Button-1>", lambda e, idx=i: self._select_card(idx))

            result_text = "APPROVE" if card["result"] == "00" else card["result"]
            result_fg = COL_GREEN if card["result"] == "00" else COL_RED
            tk.Label(top, text=result_text, font=("Segoe UI", 7, "bold"),
                      bg=bg, fg=result_fg).pack(side="right")

            # Card name
            tk.Label(frame, text=card["name"], font=FONT_LABEL_B,
                      bg=bg, fg=COL_TEXT, anchor="w").pack(fill="x")

            # PAN + Bank
            tk.Label(frame, text=f"{card['pan_masked']}  •  {card['bank']}",
                      font=("Consolas", 7), bg=bg, fg=COL_TEXT_DIM, anchor="w").pack(fill="x")

            # Make all children clickable
            for child in frame.winfo_children():
                child.bind("<Button-1>", lambda e, idx=i: self._select_card(idx))

    def _select_card(self, idx):
        self.selected_card = idx
        self._render_cards()

    # ─────────────────────────────────────────────────────────────
    #  TERMINAL SCREEN RENDERING
    # ─────────────────────────────────────────────────────────────

    def _clear_screen(self):
        for w in self.screen_body.winfo_children():
            w.destroy()

    def _render_screen(self):
        self._clear_screen()
        s = self.screen_body

        if self.state == "idle":
            tk.Label(s, text="READY", font=FONT_SCREEN, bg=BG_SCREEN, fg=COL_ACCENT).pack(pady=(30, 10))
            tk.Label(s, text="💳", font=("Segoe UI", 36), bg=BG_SCREEN).pack(pady=10)
            tk.Label(s, text="Terminal ready\nWaiting for POS command...",
                      font=FONT_SCREEN_SM, bg=BG_SCREEN, fg="#6A8DB8", justify="center").pack(pady=10)
            tk.Label(s, text=f"TID: {self.ent_tid.get()}", font=FONT_MONO_SM,
                      bg=BG_SCREEN, fg="#3A6A9A").pack(pady=(20, 0))

        elif self.state in ("sale_prompt", "refund_prompt", "void_prompt"):
            type_names = {"sale_prompt": "SALE", "refund_prompt": "REFUND", "void_prompt": "VOID"}
            type_colors = {"sale_prompt": COL_GREEN, "refund_prompt": "#E05080", "void_prompt": COL_ORANGE}
            label = type_names[self.state]
            color = type_colors[self.state]

            tk.Label(s, text=label, font=FONT_SCREEN, bg=BG_SCREEN, fg=color).pack(pady=(20, 8))

            if self.current_tx and self.current_tx["amount_cents"] > 0:
                tk.Label(s, text=f"RM {self.current_tx['amount_display']}",
                          font=FONT_SCREEN_LG, bg=BG_SCREEN, fg="white").pack(pady=(0, 10))

            if self.state == "void_prompt" and self.current_tx:
                tk.Label(s, text=f"Invoice: {self.current_tx['invoice_raw']}",
                          font=FONT_SCREEN_SM, bg=BG_SCREEN, fg="#AABBCC").pack(pady=(0, 10))

            tk.Label(s, text="Please tap, insert or\nswipe your card",
                      font=FONT_SCREEN_SM, bg=BG_SCREEN, fg="#6A8DB8", justify="center").pack(pady=8)

            # Tap button
            tap_btn = tk.Button(s, text="📶  TAP CARD HERE", font=FONT_BTN,
                                 bg="#152A4A", fg=COL_ACCENT, activebackground="#1E3A5F",
                                 bd=1, relief="groove", padx=20, pady=10,
                                 command=self._tap_card)
            tap_btn.pack(pady=(15, 0))

        elif self.state == "pin_entry":
            tk.Label(s, text="ENTER PIN", font=FONT_SCREEN, bg=BG_SCREEN, fg=COL_ACCENT).pack(pady=(15, 10))

            # PIN dots
            dot_frame = tk.Frame(s, bg=BG_SCREEN)
            dot_frame.pack(pady=8)
            for i in range(4):
                filled = i < len(self.pin_digits)
                c = tk.Canvas(dot_frame, width=18, height=18, bg=BG_SCREEN, highlightthickness=0)
                c.pack(side="left", padx=4)
                if filled:
                    c.create_oval(2, 2, 16, 16, fill=COL_ACCENT, outline="")
                else:
                    c.create_oval(2, 2, 16, 16, outline=COL_ACCENT, width=2)

            # Keypad
            pad_frame = tk.Frame(s, bg=BG_SCREEN)
            pad_frame.pack(pady=8)
            keys = [
                ["1", "2", "3"],
                ["4", "5", "6"],
                ["7", "8", "9"],
                ["✕", "0", "✓"],
            ]
            for row in keys:
                rf = tk.Frame(pad_frame, bg=BG_SCREEN)
                rf.pack()
                for key in row:
                    if key == "✕":
                        bg, fg = "#4A1A1A", "#E64A4A"
                        cmd = self._press_cancel
                    elif key == "✓":
                        bg, fg = "#0A4A2A", "#4AE68A"
                        cmd = self._press_enter
                    else:
                        bg, fg = "#152A4A", "#C0D8F0"
                        digit = int(key)
                        cmd = lambda d=digit: self._press_pin(d)

                    b = tk.Button(rf, text=key, font=FONT_PIN, bg=bg, fg=fg,
                                   activebackground="#1E3A5F", width=3, height=1,
                                   bd=1, relief="flat", command=cmd)
                    b.pack(side="left", padx=2, pady=2)

        elif self.state == "processing":
            tk.Label(s, text="⏳", font=("Segoe UI", 32), bg=BG_SCREEN).pack(pady=(40, 10))
            tk.Label(s, text="PROCESSING", font=FONT_SCREEN, bg=BG_SCREEN, fg=COL_ACCENT).pack(pady=5)
            tk.Label(s, text="Connecting to bank...\nPlease wait",
                      font=FONT_SCREEN_SM, bg=BG_SCREEN, fg="#6A8DB8", justify="center").pack(pady=10)

        elif self.state == "approved":
            tk.Label(s, text="✓", font=("Segoe UI", 40, "bold"), bg=BG_SCREEN, fg=COL_GREEN).pack(pady=(30, 5))
            tk.Label(s, text="APPROVED", font=FONT_SCREEN, bg=BG_SCREEN, fg=COL_GREEN).pack(pady=5)
            if self.current_tx:
                card = CARD_DECK[self.selected_card]
                auth = self.current_tx.get("auth_code", "")
                tk.Label(s, text=f"Auth: {auth}", font=FONT_SCREEN_SM,
                          bg=BG_SCREEN, fg="#AABBCC").pack()
                tk.Label(s, text=f"{card['brand']} ●●●● {card['pan_masked'][-4:]}",
                          font=FONT_SCREEN_SM, bg=BG_SCREEN, fg="#6A8DB8").pack(pady=5)
            tk.Label(s, text="Printing receipt...", font=("Consolas", 9),
                      bg=BG_SCREEN, fg="#3A5A7A").pack(pady=(15, 0))

        elif self.state == "declined":
            tk.Label(s, text="✕", font=("Segoe UI", 40, "bold"), bg=BG_SCREEN, fg=COL_RED).pack(pady=(30, 5))
            tk.Label(s, text="DECLINED", font=FONT_SCREEN, bg=BG_SCREEN, fg=COL_RED).pack(pady=5)
            if self.current_tx:
                err = self.current_tx.get("error_code", "??")
                desc = ERROR_DESCRIPTIONS.get(err, "Unknown")
                tk.Label(s, text=f"Code: {err}", font=FONT_SCREEN_SM,
                          bg=BG_SCREEN, fg="#AABBCC").pack()
                tk.Label(s, text=desc, font=FONT_SCREEN_SM,
                          bg=BG_SCREEN, fg="#6A8DB8").pack(pady=5)

        elif self.state == "settling":
            tk.Label(s, text="⏳", font=("Segoe UI", 32), bg=BG_SCREEN).pack(pady=(40, 10))
            tk.Label(s, text="SETTLEMENT", font=FONT_SCREEN, bg=BG_SCREEN, fg=COL_ACCENT).pack(pady=5)
            tk.Label(s, text="Closing batch...\nPlease wait",
                      font=FONT_SCREEN_SM, bg=BG_SCREEN, fg="#6A8DB8", justify="center").pack(pady=10)

        elif self.state == "settled":
            tk.Label(s, text="✓", font=("Segoe UI", 40, "bold"), bg=BG_SCREEN, fg=COL_ACCENT).pack(pady=(30, 5))
            tk.Label(s, text="SETTLED", font=FONT_SCREEN, bg=BG_SCREEN, fg=COL_ACCENT).pack(pady=5)
            tk.Label(s, text="Batch closed successfully", font=FONT_SCREEN_SM,
                      bg=BG_SCREEN, fg="#AABBCC").pack(pady=5)

        elif self.state == "password":
            tk.Label(s, text="ADMIN ACCESS", font=FONT_SCREEN, bg=BG_SCREEN, fg=COL_ACCENT).pack(pady=(15, 5))
            tk.Label(s, text="Enter terminal password", font=FONT_SCREEN_SM,
                      bg=BG_SCREEN, fg="#6A8DB8").pack(pady=5)

            dot_frame = tk.Frame(s, bg=BG_SCREEN)
            dot_frame.pack(pady=8)
            for i in range(4):
                filled = i < len(self.pin_digits)
                c = tk.Canvas(dot_frame, width=18, height=18, bg=BG_SCREEN, highlightthickness=0)
                c.pack(side="left", padx=4)
                if filled:
                    c.create_oval(2, 2, 16, 16, fill=COL_ACCENT, outline="")
                else:
                    c.create_oval(2, 2, 16, 16, outline=COL_ACCENT, width=2)

            pad_frame = tk.Frame(s, bg=BG_SCREEN)
            pad_frame.pack(pady=8)
            keys = [["1","2","3"],["4","5","6"],["7","8","9"],["✕","0","✓"]]
            for row in keys:
                rf = tk.Frame(pad_frame, bg=BG_SCREEN)
                rf.pack()
                for key in row:
                    if key == "✕":
                        bg, fg = "#4A1A1A", "#E64A4A"
                        cmd = self._press_cancel
                    elif key == "✓":
                        bg, fg = "#0A4A2A", "#4AE68A"
                        cmd = self._submit_password
                    else:
                        bg, fg = "#152A4A", "#C0D8F0"
                        digit = int(key)
                        cmd = lambda d=digit: self._press_pin(d)
                    b = tk.Button(rf, text=key, font=FONT_PIN, bg=bg, fg=fg,
                                   activebackground="#1E3A5F", width=3, height=1,
                                   bd=1, relief="flat", command=cmd)
                    b.pack(side="left", padx=2, pady=2)

    # ─────────────────────────────────────────────────────────────
    #  USER INTERACTIONS (BUTTON PRESSES)
    # ─────────────────────────────────────────────────────────────

    def _press_pin(self, digit):
        if self.state not in ("pin_entry", "password"):
            return
        if len(self.pin_digits) < 4:
            self.pin_digits.append(digit)
            self._render_screen()

    def _press_back(self):
        if self.state in ("pin_entry", "password") and self.pin_digits:
            self.pin_digits.pop()
            self._render_screen()

    def _press_cancel(self):
        if self.state == "idle":
            return
        self._log("User cancelled on terminal", "info")
        self._send_decline("CT")

    def _press_menu(self):
        if self.state == "idle":
            self.state = "password"
            self.pin_digits = []
            self._render_screen()

    def _press_enter(self):
        if self.state == "pin_entry" and len(self.pin_digits) == 4:
            card = CARD_DECK[self.selected_card]

            # Check if this card triggers wrong PIN
            if card["result"] == "55":
                self._log("Wrong PIN (simulated decline card)", "err")
                self._send_decline("55")
                return

            # Process the transaction
            self._process_transaction()

    def _submit_password(self):
        if self.state != "password":
            return
        pw = "".join(str(d) for d in self.pin_digits)
        if pw == self.admin_password:
            self._log("Admin password accepted", "info")
            self.state = "idle"
            self.pin_digits = []
            self._render_screen()
        else:
            self._log("Wrong admin password", "err")
            self.pin_digits = []
            self._render_screen()

    def _tap_card(self):
        """User clicked 'TAP CARD' on screen."""
        if self.state not in ("sale_prompt", "void_prompt", "refund_prompt"):
            return

        card = CARD_DECK[self.selected_card]
        self._log(f"Card presented: {card['brand']} ●●●●{card['pan_masked'][-4:]} ({card['bank']})", "info")

        # Check if card auto-declines (expired, no funds, bank down)
        if card["result"] not in ("00", "55"):
            # These cards decline immediately without PIN
            self.state = "processing"
            self._render_screen()
            delay = self._get_delay()
            self.root.after(int(delay * 1000), lambda: self._send_decline(card["result"]))
            return

        # Card needs PIN?
        if card["pin_required"]:
            self.state = "pin_entry"
            self.pin_digits = []
            self._render_screen()
        else:
            # No PIN needed (e.g. MyDebit tap, e-wallet)
            self._process_transaction()

    def _process_transaction(self):
        """Simulate bank processing and send approved response."""
        self.state = "processing"
        self._render_screen()

        delay = self._get_delay()
        self.root.after(int(delay * 1000), self._complete_transaction)

    def _complete_transaction(self):
        """Transaction approved — build and send RX packet."""
        card = CARD_DECK[self.selected_card]
        self.stan += 1
        auth_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

        tx = self.current_tx
        if not tx:
            return

        self.current_tx["auth_code"] = auth_code
        self.current_tx["error_code"] = "00"

        # Build RX packet
        rx_packet = ECRProtocol.build_rx_packet(
            cmd=tx["command"],
            error_code="00",
            card=card,
            amount_cents=tx["amount_cents"],
            invoice=tx["invoice"],
            cashier=tx.get("cashier", "99"),
            auth_code=auth_code,
            stan=self.stan,
            tid=self.ent_tid.get(),
            mid=self.ent_mid.get(),
            batch=self.batch,
            firmware_new=self.var_firmware.get(),
        )

        # Send via serial
        self.listener.send_response(rx_packet)

        # Update screen
        self.state = "approved"
        self._render_screen()
        self._show_receipt(tx, card, auth_code)

        self._log(f"APPROVED — Auth: {auth_code}, STAN: {self.stan}", "info")

        # Return to idle after display
        self.root.after(4000, self._return_to_idle)

    def _send_decline(self, error_code):
        """Send a declined response."""
        card = CARD_DECK[self.selected_card] if error_code != "CT" else None
        tx = self.current_tx
        if not tx:
            self.state = "idle"
            self._render_screen()
            return

        self.current_tx["error_code"] = error_code
        self.stan += 1

        rx_packet = ECRProtocol.build_rx_packet(
            cmd=tx["command"],
            error_code=error_code,
            card=None,  # No card info on decline
            amount_cents=tx["amount_cents"],
            invoice=tx["invoice"],
            cashier=tx.get("cashier", "99"),
            auth_code="",
            stan=self.stan,
            tid=self.ent_tid.get(),
            mid=self.ent_mid.get(),
            batch=self.batch,
            firmware_new=self.var_firmware.get(),
        )

        self.listener.send_response(rx_packet)

        self.state = "declined"
        self._render_screen()
        desc = ERROR_DESCRIPTIONS.get(error_code, "Unknown")
        self._log(f"DECLINED — Code: {error_code} ({desc})", "err")

        self.root.after(3000, self._return_to_idle)

    def _return_to_idle(self):
        self.state = "idle"
        self.pin_digits = []
        self.current_tx = None
        self._render_screen()

    # ─────────────────────────────────────────────────────────────
    #  INCOMING PACKET HANDLER (FROM POS SOFTWARE)
    # ─────────────────────────────────────────────────────────────

    def _on_packet_received(self, raw_bytes):
        """Called from listener thread when a complete TX packet arrives."""
        # Parse on the main thread
        self.root.after(0, lambda: self._handle_packet(raw_bytes))

    def _handle_packet(self, raw_bytes):
        """Process a received TX packet from the POS."""
        parsed = ECRProtocol.parse_tx_packet(raw_bytes)
        if not parsed:
            self._log("Failed to parse incoming packet", "err")
            return

        cmd = parsed["command"]
        self._log(f"Received {parsed['command_name']}: RM {parsed['amount_display']} "
                  f"Inv={parsed['invoice_raw']} Cashier={parsed['cashier']} "
                  f"Checksum={'OK' if parsed['checksum_valid'] else 'FAIL'}", "info")

        if not parsed["checksum_valid"]:
            self._log("WARNING: Checksum mismatch (processing anyway)", "err")

        self.current_tx = parsed

        if cmd == "020":  # Sale
            self.state = "sale_prompt"
        elif cmd == "022":  # Void
            self.state = "void_prompt"
        elif cmd == "026":  # Refund
            self.state = "refund_prompt"
        elif cmd == "050":  # Settlement
            self.state = "settling"
            self._render_screen()
            delay = self._get_delay() * 2  # Settlement takes longer
            self.root.after(int(delay * 1000), self._complete_settlement)
            return
        else:
            self._log(f"Unknown command: {cmd}", "err")
            return

        self._render_screen()

        # Auto-respond mode
        if self.var_auto.get():
            self.root.after(500, self._auto_tap_card)

    def _auto_tap_card(self):
        """In auto-respond mode, automatically tap the selected card."""
        if self.state in ("sale_prompt", "void_prompt", "refund_prompt"):
            self._tap_card()
            # If PIN entry is required, auto-enter PIN
            if self.state == "pin_entry":
                self.pin_digits = [1, 2, 3, 4]
                self._render_screen()
                self.root.after(800, self._press_enter)

    def _complete_settlement(self):
        """Complete settlement process."""
        tx = self.current_tx
        if not tx:
            return

        self.current_tx["auth_code"] = ""
        self.current_tx["error_code"] = "00"

        rx_packet = ECRProtocol.build_rx_packet(
            cmd="050",
            error_code="00",
            card=None,
            amount_cents=0,
            invoice=0,
            cashier=tx.get("cashier", "99"),
            auth_code="      ",
            stan=self.stan,
            tid=self.ent_tid.get(),
            mid=self.ent_mid.get(),
            batch=self.batch,
            firmware_new=self.var_firmware.get(),
        )

        self.listener.send_response(rx_packet)
        self.batch += 1
        self.state = "settled"
        self._render_screen()
        self._log(f"Settlement complete — Batch {self.batch - 1} closed", "info")
        self.root.after(3000, self._return_to_idle)

    # ─────────────────────────────────────────────────────────────
    #  RECEIPT
    # ─────────────────────────────────────────────────────────────

    def _show_receipt(self, tx, card, auth_code):
        now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        type_label = tx.get("command_name", "SALE")
        amount = tx.get("amount_display", "0.00")

        receipt_text = (
            f"{'=' * 36}\n"
            f"{'MERCHANT COPY':^36}\n"
            f"{'=' * 36}\n"
            f"MID: {self.ent_mid.get()}\n"
            f"TID: {self.ent_tid.get()}\n"
            f"DATE: {now}\n"
            f"BATCH: {self.batch:06d}\n"
            f"{'-' * 36}\n"
            f"TYPE:    {type_label}\n"
            f"CARD:    {card['brand']}\n"
            f"PAN:     {card['pan_masked']}\n"
            f"EXPIRY:  {card['expiry'][2:]}/{card['expiry'][:2]}\n"
            f"AUTH:    {auth_code}\n"
            f"STAN:    {self.stan:06d}\n"
            f"INV:     {tx['invoice']:06d}\n"
            f"CASHIER: {tx.get('cashier', '99')}\n"
            f"{'-' * 36}\n"
            f"AMOUNT:       RM {amount}\n"
            f"{'-' * 36}\n"
            f"\n"
            f"{'*** APPROVED ***':^36}\n"
            f"\n"
            f"{'THANK YOU':^36}\n"
            f"{'=' * 36}\n"
        )

        self.txt_receipt.config(state="normal")
        self.txt_receipt.delete("1.0", "end")
        self.txt_receipt.insert("1.0", receipt_text)
        self.txt_receipt.config(state="disabled")

    # ─────────────────────────────────────────────────────────────
    #  LOGGING
    # ─────────────────────────────────────────────────────────────

    def _log(self, msg, tag=None):
        ts = datetime.now().strftime("[%H:%M:%S]")
        self.txt_log.config(state="normal")
        self.txt_log.insert("end", f"{ts} {msg}\n", tag)
        self.txt_log.see("end")
        self.txt_log.config(state="disabled")

    def _log_from_thread(self, msg, tag=None):
        """Thread-safe logging."""
        self.root.after(0, lambda: self._log(msg, tag))

    # ─────────────────────────────────────────────────────────────
    #  CONNECTION
    # ─────────────────────────────────────────────────────────────

    def _toggle_connection(self):
        if self.listener.ser and self.listener.ser.is_open:
            self.listener.disconnect()
            self.btn_conn.config(text="START LISTENING", bg="#2C3E50")
            self.lbl_status.config(text="● Disconnected", fg=COL_RED)
            self._log("Disconnected", "info")
        else:
            port = self.port_var.get()
            ok, msg = self.listener.connect(port)
            if ok:
                self.btn_conn.config(text="STOP LISTENING", bg=COL_RED)
                self.lbl_status.config(text=f"● Listening on {port}", fg=COL_GREEN)
                self._log(f"Listening on {port} (9600/8N1)", "info")
                self._save_config()
            else:
                messagebox.showerror("Connection Error", msg)
                self._log(f"Connection failed: {msg}", "err")

    # ─────────────────────────────────────────────────────────────
    #  UTILITIES
    # ─────────────────────────────────────────────────────────────

    def _update_clock(self):
        now = datetime.now().strftime("%H:%M")
        self.lbl_clock.config(text=now)
        self.root.after(30000, self._update_clock)

    def _get_delay(self):
        """Parse delay range from entry field."""
        try:
            text = self.ent_delay.get().strip()
            if "-" in text:
                parts = text.split("-")
                lo = float(parts[0])
                hi = float(parts[1])
                return lo + random.random() * (hi - lo)
            return float(text)
        except:
            return 2.5

    def _save_config(self):
        try:
            cfg = {
                "port": self.port_var.get(),
                "tid": self.ent_tid.get(),
                "mid": self.ent_mid.get(),
                "firmware_new": self.var_firmware.get(),
                "delay": self.ent_delay.get(),
                "auto_respond": self.var_auto.get(),
            }
            with open(CONFIG_FILE, "w") as f:
                json.dump(cfg, f, indent=2)
        except:
            pass

    def _load_config(self):
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
            if "port" in cfg:
                self.port_var.set(cfg["port"])
            if "tid" in cfg:
                self.ent_tid.delete(0, "end")
                self.ent_tid.insert(0, cfg["tid"])
            if "mid" in cfg:
                self.ent_mid.delete(0, "end")
                self.ent_mid.insert(0, cfg["mid"])
            if "firmware_new" in cfg:
                self.var_firmware.set(cfg["firmware_new"])
            if "delay" in cfg:
                self.ent_delay.delete(0, "end")
                self.ent_delay.insert(0, cfg["delay"])
            if "auto_respond" in cfg:
                self.var_auto.set(cfg["auto_respond"])
        except:
            pass

    def _on_close(self):
        self._save_config()
        self.listener.disconnect()
        self.root.destroy()


# ╔══════════════════════════════════════════════════════════════╗
# ║                       MAIN                                   ║
# ╚══════════════════════════════════════════════════════════════╝

if __name__ == "__main__":
    root = tk.Tk()
    app = TerminalEmulatorApp(root)
    root.mainloop()
