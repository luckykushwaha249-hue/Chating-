"""
BLE Chat – Android Bluetooth Chat App (RFCOMM)
Requires: Android 6.0+ (API 23+), Bluetooth & Location permissions.
Devices must be paired manually in Android settings before using the app.
"""

import threading
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.logger import Logger

# ---------- PyJNIus (Android Bluetooth) ----------
from jnius import autoclass
from android.permissions import request_permissions, Permission

# Java classes
BluetoothAdapter = autoclass('android.bluetooth.BluetoothAdapter')
BluetoothDevice = autoclass('android.bluetooth.BluetoothDevice')
BluetoothSocket = autoclass('android.bluetooth.BluetoothSocket')
BluetoothServerSocket = autoclass('android.bluetooth.BluetoothServerSocket')
UUID = autoclass('java.util.UUID')
InputStream = autoclass('java.io.InputStream')
OutputStream = autoclass('java.io.OutputStream')

# Android context / UI helpers
PythonActivity = autoclass('org.kivy.android.PythonActivity')
Toast = autoclass('android.widget.Toast')


def toast(msg):
    """Show a short Android toast message."""
    Toast.makeText(PythonActivity.mActivity, msg, Toast.LENGTH_SHORT).show()


# ---------- Bluetooth Manager (handles all RFCOMM operations) ----------
class BluetoothManager:
    # Standard Serial Port Profile (SPP) UUID – works with most Bluetooth devices
    MY_UUID = UUID.fromString("00001101-0000-1000-8000-00805F9B34FB")

    def __init__(self):
        self.adapter = BluetoothAdapter.getDefaultAdapter()
        self.socket = None
        self.server_socket = None
        self.input_stream = None
        self.output_stream = None
        self.connected = False
        self.running = False
        self.receive_thread = None

    def is_bluetooth_ready(self):
        """Check if Bluetooth is supported and enabled."""
        if self.adapter is None:
            toast("Bluetooth not supported on this device")
            return False
        if not self.adapter.isEnabled():
            toast("Please enable Bluetooth in system settings")
            return False
        return True

    def start_server(self, on_connect_cb, on_message_cb):
        """Start listening for incoming connections (non‑blocking)."""
        if not self.is_bluetooth_ready():
            return False
        try:
            self.server_socket = self.adapter.listenUsingRfcommWithServiceRecord(
                "BLE Chat Server", self.MY_UUID
            )
            toast("Waiting for connection...")

            def accept_loop():
                self.running = True
                try:
                    self.socket = self.server_socket.accept()
                    if self.socket:
                        self.connected = True
                        self._setup_streams()
                        Clock.schedule_once(lambda dt: on_connect_cb(True))
                        self._start_receiver(on_message_cb)
                except Exception as e:
                    Logger.error(f"Server accept error: {e}")
                    Clock.schedule_once(lambda dt: on_connect_cb(False))

            threading.Thread(target=accept_loop, daemon=True).start()
            return True
        except Exception as e:
            Logger.error(f"Server start error: {e}")
            toast("Could not start server")
            return False

    def connect_to_device(self, mac_address, on_connect_cb, on_message_cb):
        """Connect to a remote device by MAC address (non‑blocking)."""
        if not self.is_bluetooth_ready():
            return False
        try:
            device = self.adapter.getRemoteDevice(mac_address)
            self.socket = device.createRfcommSocketToServiceRecord(self.MY_UUID)

            def connect_thread():
                self.running = True
                try:
                    self.socket.connect()
                    self.connected = True
                    self._setup_streams()
                    Clock.schedule_once(lambda dt: on_connect_cb(True))
                    self._start_receiver(on_message_cb)
                except Exception as e:
                    Logger.error(f"Connect error: {e}")
                    Clock.schedule_once(lambda dt: on_connect_cb(False))

            threading.Thread(target=connect_thread, daemon=True).start()
            return True
        except Exception as e:
            Logger.error(f"Connect init error: {e}")
            toast("Could not initiate connection")
            return False

    def _setup_streams(self):
        """Get input/output streams from the connected socket."""
        try:
            self.input_stream = self.socket.getInputStream()
            self.output_stream = self.socket.getOutputStream()
        except Exception as e:
            Logger.error(f"Stream setup error: {e}")

    def _start_receiver(self, on_message_cb):
        """Start a background thread that reads incoming messages."""
        def receiver():
            while self.running and self.connected:
                try:
                    # Read a single byte at a time until newline (10)
                    buffer = bytearray()
                    while True:
                        b = self.input_stream.read()
                        if b == -1:
                            raise Exception("Stream closed")
                        buffer.append(b)
                        if b == 10:  # newline
                            break
                    msg = buffer.decode('utf-8').strip()
                    if msg:
                        Clock.schedule_once(lambda dt, m=msg: on_message_cb(m))
                except Exception as e:
                    Logger.error(f"Receive error: {e}")
                    self.connected = False
                    break
        self.receive_thread = threading.Thread(target=receiver, daemon=True)
        self.receive_thread.start()

    def send_message(self, msg):
        """Send a text message (with newline). Returns True on success."""
        if self.connected and self.output_stream:
            try:
                self.output_stream.write((msg + "\n").encode('utf-8'))
                self.output_stream.flush()
                return True
            except Exception as e:
                Logger.error(f"Send error: {e}")
                self.connected = False
        return False

    def close(self):
        """Close all sockets and streams."""
        self.running = False
        self.connected = False
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None
        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass
            self.server_socket = None
        self.input_stream = None
        self.output_stream = None

    def get_paired_devices(self):
        """Return a list of (device_name, mac_address) for all paired devices."""
        if not self.is_bluetooth_ready():
            return []
        paired = self.adapter.getBondedDevices()
        devices = []
        for device in paired:
            devices.append((device.getName(), device.getAddress()))
        return devices


# ---------- Kivy GUI ----------
class ChatScreen(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation='vertical', **kwargs)

        self.bt = BluetoothManager()
        self.connected = False
        self.is_host = False
        self.chat_history = []  # list of messages for display

        # ---- Build UI ----
        # Status label
        self.status_label = Label(text="Not connected", size_hint_y=0.08)
        self.add_widget(self.status_label)

        # Chat display area (scrollable)
        scroll = ScrollView(size_hint_y=0.6)
        self.chat_label = Label(text="", markup=True, halign='left', valign='top')
        self.chat_label.bind(size=self.chat_label.setter('text_size'))
        scroll.add_widget(self.chat_label)
        self.add_widget(scroll)

        # Message input row
        input_box = BoxLayout(size_hint_y=0.1)
        self.msg_input = TextInput(text='', multiline=False)
        self.send_btn = Button(text='Send', disabled=True)
        self.send_btn.bind(on_press=self.send_message)
        input_box.add_widget(self.msg_input)
        input_box.add_widget(self.send_btn)
        self.add_widget(input_box)

        # Control row (Host / Join / Refresh / Disconnect)
        control_box = BoxLayout(size_hint_y=0.12)
        self.host_btn = Button(text='Host')
        self.host_btn.bind(on_press=self.host_chat)
        self.join_btn = Button(text='Join')
        self.join_btn.bind(on_press=self.join_chat)
        self.refresh_btn = Button(text='Refresh')
        self.refresh_btn.bind(on_press=self.refresh_devices)
        self.disconnect_btn = Button(text='Disconnect', disabled=True)
        self.disconnect_btn.bind(on_press=self.disconnect)
        control_box.add_widget(self.host_btn)
        control_box.add_widget(self.join_btn)
        control_box.add_widget(self.refresh_btn)
        control_box.add_widget(self.disconnect_btn)
        self.add_widget(control_box)

        # Device selection spinner (below controls)
        spinner_box = BoxLayout(size_hint_y=0.1)
        spinner_box.add_widget(Label(text='Device:', size_hint_x=0.2))
        self.device_spinner = Spinner(text='Select device', values=['No devices'])
        self.device_spinner.size_hint_x = 0.8
        spinner_box.add_widget(self.device_spinner)
        self.add_widget(spinner_box)

        # Request permissions and refresh device list
        self.request_permissions()
        self.refresh_devices()

    # ---------- Permission handling ----------
    def request_permissions(self):
        perms = [
            Permission.BLUETOOTH,
            Permission.BLUETOOTH_ADMIN,
            Permission.ACCESS_FINE_LOCATION,
            Permission.ACCESS_COARSE_LOCATION,
        ]
        # Android 12+ extra permissions
        try:
            perms.append(Permission.BLUETOOTH_SCAN)
            perms.append(Permission.BLUETOOTH_CONNECT)
            perms.append(Permission.BLUETOOTH_ADVERTISE)
        except:
            pass
        request_permissions(perms, self.on_permissions_result)

    def on_permissions_result(self, permissions, grants):
        if all(g == 0 for g in grants):
            toast("Permissions granted")
        else:
            toast("Some permissions denied – app may not work")

    # ---------- Device list ----------
    def refresh_devices(self, *args):
        """Populate spinner with paired devices."""
        devices = self.bt.get_paired_devices()
        if not devices:
            self.device_spinner.values = ['No devices']
            self.device_spinner.text = 'No devices'
        else:
            # Format: "DeviceName (MAC)"
            values = [f"{name} ({addr})" for name, addr in devices]
            self.device_spinner.values = values
            self.device_spinner.text = values[0]

    # ---------- Actions ----------
    def host_chat(self, *args):
        if self.connected:
            toast("Already connected")
            return
        if self.bt.start_server(self.on_connect, self.on_message_received):
            self.is_host = True
            self.status_label.text = "Host: Waiting for connection..."
            self.host_btn.disabled = True
            self.join_btn.disabled = True
            self.refresh_btn.disabled = True

    def join_chat(self, *args):
        if self.connected:
            toast("Already connected")
            return
        selection = self.device_spinner.text
        if not selection or selection == 'No devices' or selection == 'Select device':
            toast("Please select a paired device")
            return
        # Extract MAC address from "DeviceName (AA:BB:CC:DD:EE:FF)"
        try:
            mac = selection.split('(')[1].split(')')[0]
        except:
            toast("Invalid device entry")
            return
        if self.bt.connect_to_device(mac, self.on_connect, self.on_message_received):
            self.status_label.text = f"Connecting to {selection}..."
            self.host_btn.disabled = True
            self.join_btn.disabled = True
            self.refresh_btn.disabled = True

    def on_connect(self, success):
        """Callback when connection attempt finishes."""
        if success:
            self.connected = True
            self.status_label.text = "Connected!"
            self.send_btn.disabled = False
            self.msg_input.disabled = False
            self.disconnect_btn.disabled = False
            self.host_btn.disabled = True
            self.join_btn.disabled = True
            self.refresh_btn.disabled = True
            toast("Connected successfully")
        else:
            self.connected = False
            self.status_label.text = "Connection failed"
            self.host_btn.disabled = False
            self.join_btn.disabled = False
            self.refresh_btn.disabled = False
            self.send_btn.disabled = True
            self.msg_input.disabled = True
            self.disconnect_btn.disabled = True

    def on_message_received(self, msg):
        """Called from receiver thread (scheduled to main thread)."""
        self.add_message(f"Other: {msg}")

    def send_message(self, *args):
        msg = self.msg_input.text.strip()
        if not msg:
            return
        if not self.connected:
            toast("Not connected")
            return
        if self.bt.send_message(msg):
            self.add_message(f"Me: {msg}")
            self.msg_input.text = ""
        else:
            toast("Send failed – connection lost")
            self.disconnect()

    def add_message(self, msg):
        """Append a message to the chat display."""
        self.chat_history.append(msg)
        self.chat_label.text = "\n".join(self.chat_history)
        # Scroll to bottom (simulate by moving cursor to end – not perfect but works)
        # For better scrolling, we would need a RecycleView, but this is simpler.

    def disconnect(self, *args):
        """Close connection and reset UI."""
        self.bt.close()
        self.connected = False
        self.is_host = False
        self.status_label.text = "Disconnected"
        self.host_btn.disabled = False
        self.join_btn.disabled = False
        self.refresh_btn.disabled = False
        self.send_btn.disabled = True
        self.msg_input.disabled = True
        self.disconnect_btn.disabled = True
        toast("Disconnected")

    def on_stop(self):
        """Called when the app is closed."""
        self.bt.close()


# ---------- App class ----------
class BLE_ChatApp(App):
    def build(self):
        Window.size = (400, 600)  # for desktop preview; ignored on Android
        return ChatScreen()

    def on_stop(self):
        # Ensure Bluetooth is closed when app exits
        if hasattr(self.root, 'on_stop'):
            self.root.on_stop()


if __name__ == '__main__':
    BLE_ChatApp().run()
